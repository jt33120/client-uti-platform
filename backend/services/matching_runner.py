"""
Moteur de matching — orchestration hybride (AI Act Phase 3).

Pipeline : CV → [extraction LLM, pseudonymisée] → features → [scoring déterministe]
→ persistance + journal d'audit. Trois points d'entrée :
  * POST /matching/run             (manuel, staff)
  * new CV submitted               (auto re-score, background task)
  * AO created                     (vivier recommendations, background task)

Vivier mode scores consultants from the talent pool BEFORE any partner has
submitted a CV, so staff see recommendations immediately. Those rows are
stored with submission_id=NULL and are naturally replaced by the first
submission-based run (which clears previous matchings for the AO).
"""
import asyncio
from typing import Optional
from services.supabase_client import supabase
from services.ai_matching import extract_features, EXTRACTION_MODEL
from services.scoring import score_consultant, GRID_VERSION, DEFAULTS, stars_to_weights, STAR_CRITERIA
from services.llm_scoring import llm_score, combine_hybrid
from services.scoring_settings import get_config
from services.pseudonymize import strip_pii
from services.cv_structured import flatten_structured
from services import audit
from services import ai_ledger
from services.error_log import record as _record_err

# Keep vivier runs bounded — most recent consultants first
VIVIER_MAX_CONSULTANTS = 20

# Bride la concurrence des appels LLM d'un run (extraction + 2e avis) : sans
# borne, un vivier de 20 candidats tire 40 requêtes simultanées — rate-limits
# provider et pics mémoire assurés.
_LLM_MAX_CONCURRENCY = 4


async def _gather_bounded(coros):
    """asyncio.gather avec au plus _LLM_MAX_CONCURRENCY coroutines actives.
    Le sémaphore est créé ici (et pas au niveau module) pour rester lié à la
    boucle d'événements courante."""
    sem = asyncio.Semaphore(_LLM_MAX_CONCURRENCY)

    async def _run(coro):
        async with sem:
            return await coro

    return await asyncio.gather(*[_run(c) for c in coros])


async def _features_for(item: dict) -> tuple[dict, float, str]:
    """Extraction pseudonymisée des features d'un candidat (best-effort).
    Retourne aussi le TEXTE pseudonymisé du CV, réutilisé par le 2e avis IA pour
    citer des éléments concrets dans sa justification (sans PII)."""
    clean = strip_pii(item.get("cv_text"), item.get("name"))
    features, cost = await extract_features(clean)
    return features, cost, clean


def _human_feedback_map(ao_id: str) -> dict:
    """Derniers désaccords humains signalés pour cet AO, par consultant_id.
    Ce texte est réinjecté dans le prompt du 2e avis IA au ré-scoring : l'humain
    corrige, l'IA en tient compte (AI Act Art. 14 — supervision effective)."""
    try:
        rows = supabase.table("human_decision").select(
            "consultant_id, justification, decided_at"
        ).eq("ao_id", ao_id).eq("decision", "overridden").order(
            "decided_at", desc=True
        ).execute().data or []
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for r in rows:  # trié du plus récent au plus ancien → on garde le 1er par consultant
        cid = r.get("consultant_id")
        just = (r.get("justification") or "").strip()
        if cid and just and cid not in out:
            out[cid] = just
    return out


def _weights_from_config(config: dict) -> dict:
    """Poids effectifs (w_competences…) dérivés de la config, comme score_consultant."""
    cfg = {**DEFAULTS, **{k: v for k, v in (config or {}).items() if v is not None}}
    stars = (config or {}).get("stars")
    if stars:
        cfg.update(stars_to_weights(stars))
    return {f"w_{c}": cfg[f"w_{c}"] for c in STAR_CRITERIA}


# Colonnes ajoutées par supabase_migration_hybrid_scoring.sql. Si la migration
# n'a pas été appliquée en prod, l'insert les concernant échoue : on retombe sur
# le sous-ensemble auditable (score déterministe) plutôt que de tout perdre.
_HYBRID_COLS = (
    "score_llm", "score_hybride", "agreement",
    "llm_breakdown", "llm_global", "hybrid_breakdown", "weights",
    # Colonne langues (supabase_migration_languages.sql) : stripée elle aussi
    # si la migration n'est pas encore appliquée.
    "langues",
)
# Colonnes non essentielles : leur absence (schéma non migré OU cache PostgREST
# périmé, erreur PGRST204) ne doit jamais faire perdre le score déterministe.
_OPTIONAL_COLS = _HYBRID_COLS + ("cost_usd",)
# Colonnes sans lesquelles une ligne de matching n'a aucun sens.
_ESSENTIAL_COLS = frozenset({"ao_id", "consultant_id", "score_total", "rank"})


def _insert_matchings(rows: list[dict]) -> set:
    """Insert résilient. PostgREST renvoie PGRST204 « Could not find the 'X'
    column » quand une colonne manque du schéma OU que son cache est périmé
    (cas vu en prod sur `cost_usd`). On retire alors la colonne nommée et on
    réessaie — jamais on ne perd le score déterministe. Retourne les colonnes
    retirées."""
    import re
    dropped: set = set()

    def _pruned():
        return [{k: v for k, v in r.items() if k not in dropped} for r in rows]

    while True:
        try:
            supabase.table("matchings").insert(_pruned()).execute()
            return dropped
        except Exception as e:  # noqa: BLE001
            m = re.search(r"'([a-z0-9_]+)' column", str(e))
            col = m.group(1) if m else None
            if col and col not in _ESSENTIAL_COLS and col not in dropped:
                dropped.add(col)
                _record_err(
                    "matching.persist",
                    f"Colonne '{col}' absente du schéma/cache PostgREST — insert sans elle "
                    f"(rafraîchir le cache : NOTIFY pgrst, 'reload schema')",
                    level="warning",
                )
                continue
            # Erreur non liée à une colonne, ou colonne essentielle : dernier
            # recours = retirer d'un coup toutes les colonnes optionnelles.
            if not dropped.issuperset(_OPTIONAL_COLS):
                dropped.update(_OPTIONAL_COLS)
                continue
            raise


def _persist(ao_id: str, results: list[dict], cost_usd: float, ran_by: Optional[str]):
    """
    Remplace les matchings de cet AO par les nouveaux résultats.

    IMPORTANT : on INSÈRE d'abord, on supprime l'ancien classement ENSUITE. Ainsi
    un insert qui échoue (ex. colonne hybride absente car migration non appliquée)
    ne détruit jamais les résultats déjà affichés. En dernier recours, on réessaie
    sans les colonnes hybrides (dégradation maîtrisée : score déterministe seul).
    """
    rows = [{
        "ao_id": ao_id,
        "submission_id": r.get("submission_id"),
        "consultant_id": r.get("consultant_id"),
        "score_total": r["score_total"],
        "breakdown": r.get("breakdown"),
        "points_forts": r.get("points_forts"),
        "points_faibles": r.get("points_faibles"),
        "resume_matching": r.get("resume_matching"),
        "recommandation": r.get("recommandation"),
        # Architecture hybride : 2e avis IA + score combiné (repli déterministe)
        "score_llm": r.get("score_llm"),
        "score_hybride": r.get("score_hybride"),
        "agreement": r.get("agreement"),
        "llm_breakdown": r.get("llm_breakdown"),
        "llm_global": r.get("llm_global"),
        "hybrid_breakdown": r.get("hybrid_breakdown"),
        "weights": r.get("weights"),
        "langues": r.get("langues"),  # langues détectées dans le CV (affichage)
        "rank": rank,
        # Coût du RUN porté par la SEULE ligne de rang 1 (0 sur les autres) :
        # sinon sommer matchings.cost_usd multiplie le coût par le nombre de
        # profils persistés (Top N). Permet un « Coût IA » juste côté supervision.
        "cost_usd": cost_usd if rank == 1 else 0,
        "ran_by": ran_by,
    } for rank, r in enumerate(results, start=1)]

    # ids du classement courant : on ne les supprime qu'après un insert réussi.
    old_ids = [
        o["id"] for o in
        (supabase.table("matchings").select("id").eq("ao_id", ao_id).execute().data or [])
    ]

    # Insert résilient : retire à la volée toute colonne absente du schéma/cache
    # (jamais de perte du score déterministe).
    _insert_matchings(rows)

    if old_ids:
        # Si ce delete échoue, l'ancien ET le nouveau classement coexistent dans
        # la table (doublons à l'écran) : on retente une fois puis on alerte.
        try:
            supabase.table("matchings").delete().in_("id", old_ids).execute()
        except Exception as e:
            try:
                supabase.table("matchings").delete().in_("id", old_ids).execute()
            except Exception:
                _record_err(
                    "matching.persist",
                    f"Ancien classement non purgé pour l'AO {ao_id} — doublons possibles à l'écran",
                    exc=e,
                )
                raise


def _effective_config(ao: dict) -> dict:
    """
    Config de scoring effective pour un AO : la grille globale (pilotée par
    l'admin), surchargée par les priorités propres à l'AO (`scoring_overrides`).
    Les étoiles sont fusionnées critère par critère pour qu'un override partiel
    n'efface pas les autres axes.
    """
    config = get_config()  # best-effort : {} si la table n'existe pas
    overrides = ao.get("scoring_overrides") or {}
    if not isinstance(overrides, dict) or not overrides:
        return config
    merged = {**config, **{k: v for k, v in overrides.items() if v is not None and k != "stars"}}
    g_stars = config.get("stars") or {}
    a_stars = overrides.get("stars") or {}
    if g_stars or a_stars:
        merged["stars"] = {**g_stars, **a_stars}
    return merged


async def _score_all(
    ao: dict, items: list[dict], run_id: str, ran_by: Optional[str]
) -> tuple[list[dict], float]:
    """Extrait (concurremment) puis score chaque candidat ; journalise chaque score."""
    config = _effective_config(ao)  # grille globale + priorités propres à l'AO
    weights = _weights_from_config(config)
    feedback = _human_feedback_map(ao.get("id"))  # désaccords humains à réinjecter
    extracted = await _gather_bounded([_features_for(it) for it in items])

    # Étape déterministe (synchrone) puis 2e avis IA (concurrent sur tous les CV).
    base = []
    for it, (features, ex_cost, clean_cv) in zip(items, extracted):
        score = score_consultant(features, it, ao, config)
        if features.get("extraction_failed"):
            # La lecture IA du CV a totalement échoué : le score repose sur la
            # seule fiche déclarée. On le DIT (UI + audit) au lieu de présenter
            # un score plausible comme un matching complet.
            score["extraction_failed"] = True
            warn = "⚠️ Lecture IA du CV indisponible — score fondé sur la fiche déclarée uniquement"
            score["points_faibles"] = [warn] + list(score.get("points_faibles") or [])
        base.append((it, features, score, ex_cost, clean_cv))
    # Extrait cité par l'IA : le CV structuré aplati (déjà anonymisé) quand il
    # existe → chaque « … » cité se retrouve tel quel dans un champ affiché de la
    # vue « CV analysé » (surlignage exact). Repli sur le CV brut pseudonymisé.
    llm_outs = await _gather_bounded([
        llm_score(features, it, ao, weights,
                  cv_excerpt=(flatten_structured(it.get("cv_structured")) or clean_cv),
                  human_feedback=feedback.get(it.get("consultant_id")))
        for (it, features, _s, _c, clean_cv) in base
    ])

    total_cost = 0.0
    results: list[dict] = []
    for (it, features, score, ex_cost, _clean), (llm_res, llm_cost) in zip(base, llm_outs):
        total_cost += ex_cost + llm_cost
        score.update(combine_hybrid(score, llm_res, weights))  # score_hybride, score_llm, agreement…
        score["weights"] = weights  # barèmes effectifs par axe (pour le radar)
        score["submission_id"] = it.get("submission_id")
        score["consultant_id"] = it.get("consultant_id")
        score["consultant_name"] = it.get("name")
        score["submitter_name"] = it.get("submitter_name")
        score["consultant_tjm"] = it.get("tjm")
        score["consultant_skills"] = it.get("skills")
        score["langues"] = features.get("languages")  # affichées dans le détail
        results.append(score)
        audit.log_event(
            "score", run_id,
            ao_id=ao.get("id"), actor_id=ran_by,
            model_version=EXTRACTION_MODEL, grid_version=GRID_VERSION,
            input_hash=audit.features_hash(features),
            payload={
                "submission_id": it.get("submission_id"),
                "consultant_id": it.get("consultant_id"),
                "extraction_failed": bool(features.get("extraction_failed")),
                "score_total": score["score_total"],
                "score_llm": score.get("score_llm"),
                "score_hybride": score.get("score_hybride"),
                "agreement": score.get("agreement"),
                "breakdown": score["breakdown"],
                "recommandation": score["recommandation"],
            },
        )
    # Classement par score hybride (repli sur le déterministe si l'IA est absente).
    results.sort(key=lambda r: (r.get("score_hybride") if r.get("score_hybride") is not None else r["score_total"]), reverse=True)
    return results, total_cost


def _fetch_submissions(ao_id: str) -> list:
    """Soumissions d'un AO pour le scoring. Tente d'inclure `cv_structured` (CV
    canonique GRP-IT → citations IA exactes) ; retombe sans cette colonne si la
    migration 0002 n'est pas appliquée, pour ne jamais casser le matching."""
    base_cols = (
        "id, cv_text, consultant_id, submitted_by, submitter:profiles!submitted_by(id, name), "
        "consultants(id, name, tjm, skills, experience_years, employment_type)"
    )
    try:
        return supabase.table("submissions").select(
            base_cols + ", cv_structured"
        ).eq("ao_id", ao_id).execute().data
    except Exception:
        return supabase.table("submissions").select(base_cols).eq("ao_id", ao_id).execute().data


async def run_submission_matching(ao_id: str, ran_by: Optional[str], top_n: int = 5) -> dict:
    """
    Score every submitted CV for this AO and persist the top N.
    Raises LookupError (no AO / no submissions) or ValueError (no readable CV).
    """
    ai_ledger.set_context(user_id=ran_by, entity_type="ao", entity_id=ao_id)
    run_id = audit.new_run_id()
    try:
        ao = supabase.table("appels_offres").select("*").eq("id", ao_id).single().execute().data
    except Exception:
        raise LookupError("AO introuvable")

    submissions = _fetch_submissions(ao_id)

    if not submissions:
        raise LookupError("Aucun CV soumis pour cet AO")

    items = []
    for s in submissions:
        c = s.get("consultants") or {}
        if not s.get("cv_text"):
            continue
        items.append({
            "submission_id": s["id"],
            "consultant_id": s["consultant_id"],
            "submitter_name": (s.get("submitter") or {}).get("name"),
            "name": c.get("name", "Inconnu"),
            "tjm": c.get("tjm"),
            "skills": c.get("skills", ""),
            "experience_years": c.get("experience_years"),
            "cv_text": s["cv_text"],
            # CV structuré (anonymisé) : si présent, l'IA cite depuis LUI (surlignage exact).
            "cv_structured": s.get("cv_structured"),
        })

    if not items:
        raise ValueError("Aucun CV lisible pour cet AO")

    audit.log_event(
        "run_start", run_id, ao_id=ao_id, actor_id=ran_by,
        model_version=EXTRACTION_MODEL, grid_version=GRID_VERSION,
        payload={"trigger": "submission", "candidates": len(items)},
    )

    results, cost_usd = await _score_all(ao, items, run_id, ran_by)
    top_results = results[:top_n]

    try:
        _persist(ao_id, top_results, cost_usd, ran_by)
    except Exception as e:
        audit.log_event(
            "error", run_id, ao_id=ao_id, severity="error",
            payload={"stage": "persist", "error": str(e)},
        )
        print(f"[MATCHING] Warning: could not save results for AO {ao_id}: {e}")

    # Tous les scores (léger) pour les analyses côté UI : distribution, classement
    # complet, écart entre profils. On ne persiste que le Top N, mais on renvoie
    # l'ensemble dans la réponse du run.
    all_scores = [{
        "consultant_id": r.get("consultant_id"),
        "consultant_name": r.get("consultant_name"),
        "submitter_name": r.get("submitter_name"),
        "score": r.get("score_hybride") if r.get("score_hybride") is not None else r.get("score_total"),
        "score_total": r.get("score_total"),
        "tjm": r.get("consultant_tjm"),
        "extraction_failed": bool(r.get("extraction_failed")),
    } for r in results]

    return {
        "ao_id": ao_id,
        "ao_title": ao["title"],
        "total_consultants_evaluated": len(items),
        "top_n": top_n,
        "results": top_results,
        "all_scores": all_scores,
    }


async def run_vivier_matching(ao_id: str, ran_by: Optional[str], top_n: int = 5) -> Optional[dict]:
    """
    Recommend consultants straight from the vivier for a freshly created AO.
    Only consultants owned by partners with active access to the AO's client
    (or by UTI staff) are eligible. Never raises — background-task friendly.
    """
    ai_ledger.set_context(user_id=ran_by, entity_type="ao", entity_id=ao_id)
    run_id = audit.new_run_id()
    try:
        ao = supabase.table("appels_offres").select("*").eq("id", ao_id).single().execute().data
        if not ao:
            return None

        # Don't overwrite real submission-based results
        existing = supabase.table("submissions").select("id").eq("ao_id", ao_id).limit(1).execute().data
        if existing:
            return None

        # Eligible owners: partners with list_1/list_2 on the client + UTI staff
        eligible_ids = set()
        if ao.get("client_id"):
            rows = supabase.table("partner_clients").select("partner_id").eq(
                "client_id", ao["client_id"]
            ).in_("tier", ["list_1", "list_2"]).execute().data or []
            eligible_ids = {r["partner_id"] for r in rows}
        staff = supabase.table("profiles").select("id").in_(
            "role", ["admin", "commerce"]
        ).execute().data or []
        eligible_ids |= {r["id"] for r in staff}

        consultants = supabase.table("consultants").select("*").order(
            "created_at", desc=True
        ).limit(200).execute().data or []
        pool = [c for c in consultants if c.get("created_by") in eligible_ids][:VIVIER_MAX_CONSULTANTS]
        if not pool:
            return None

        # CVs are anonymised / often absent in the vivier — fall back to a
        # profile sheet so the extractor always has something to read.
        items = []
        for c in pool:
            cv = c.get("cv_text") or (
                f"Profil consultant (fiche vivier, CV non fourni)\n"
                f"Compétences : {c.get('skills') or 'N/A'}\n"
                f"Expérience : {c.get('experience_years') or 'N/A'} ans\n"
                f"TJM : {c.get('tjm') or 'N/A'} €/j\n"
                f"Disponibilité : {c.get('availability') or 'N/A'}\n"
                f"Statut : {c.get('employment_type') or 'N/A'}"
            )
            items.append({
                "submission_id": None,  # vivier recommendation — no CV submitted yet
                "consultant_id": c["id"],
                "name": c.get("name", "Inconnu"),
                "tjm": c.get("tjm"),
                "skills": c.get("skills", ""),
                "experience_years": c.get("experience_years"),
                "cv_text": cv,
            })

        audit.log_event(
            "run_start", run_id, ao_id=ao_id, actor_id=ran_by,
            model_version=EXTRACTION_MODEL, grid_version=GRID_VERSION,
            payload={"trigger": "vivier", "candidates": len(items)},
        )

        results, cost_usd = await _score_all(ao, items, run_id, ran_by)
        top_results = results[:top_n]
        _persist(ao_id, top_results, cost_usd, ran_by)
        return {"ao_id": ao_id, "results": top_results}
    except Exception as e:
        print(f"[MATCHING] vivier matching failed for AO {ao_id}: {e}")
        _record_err("matching.vivier", f"Recommandations vivier en échec pour l'AO {ao_id}", exc=e, level="warning")
        return None


async def auto_rescore_ao(ao_id: str, ran_by: Optional[str]):
    """Background task: re-score an AO after a new CV lands. Never raises."""
    try:
        await run_submission_matching(ao_id, ran_by)
        print(f"[MATCHING] auto re-score done for AO {ao_id}")
    except (LookupError, ValueError) as e:
        # Cas fonctionnels attendus (pas de CV lisible…) — pas une panne.
        print(f"[MATCHING] auto re-score skipped for AO {ao_id}: {e}")
    except Exception as e:
        print(f"[MATCHING] auto re-score skipped for AO {ao_id}: {e}")
        _record_err("matching.rescore", f"Re-score automatique en échec pour l'AO {ao_id}", exc=e)
