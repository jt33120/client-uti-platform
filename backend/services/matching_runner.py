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
from services import audit

# Keep vivier runs bounded — most recent consultants first
VIVIER_MAX_CONSULTANTS = 20


async def _features_for(item: dict) -> tuple[dict, float]:
    """Extraction pseudonymisée des features d'un candidat (best-effort)."""
    clean = strip_pii(item.get("cv_text"), item.get("name"))
    return await extract_features(clean)


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
)


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
        "rank": rank,
        "cost_usd": cost_usd,
        "ran_by": ran_by,
    } for rank, r in enumerate(results, start=1)]

    # ids du classement courant : on ne les supprime qu'après un insert réussi.
    old_ids = [
        o["id"] for o in
        (supabase.table("matchings").select("id").eq("ao_id", ao_id).execute().data or [])
    ]

    try:
        supabase.table("matchings").insert(rows).execute()
    except Exception as e:
        print(f"[MATCHING] insert complet échoué ({e}); repli sans colonnes hybrides")
        trimmed = [{k: v for k, v in row.items() if k not in _HYBRID_COLS} for row in rows]
        supabase.table("matchings").insert(trimmed).execute()

    if old_ids:
        supabase.table("matchings").delete().in_("id", old_ids).execute()


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
    extracted = await asyncio.gather(*[_features_for(it) for it in items])

    # Étape déterministe (synchrone) puis 2e avis IA (concurrent sur tous les CV).
    base = []
    for it, (features, ex_cost) in zip(items, extracted):
        score = score_consultant(features, it, ao, config)
        base.append((it, features, score, ex_cost))
    llm_outs = await asyncio.gather(*[
        llm_score(features, it, ao, weights) for (it, features, _s, _c) in base
    ])

    total_cost = 0.0
    results: list[dict] = []
    for (it, features, score, ex_cost), (llm_res, llm_cost) in zip(base, llm_outs):
        total_cost += ex_cost + llm_cost
        score.update(combine_hybrid(score, llm_res, weights))  # score_hybride, score_llm, agreement…
        score["weights"] = weights  # barèmes effectifs par axe (pour le radar)
        score["submission_id"] = it.get("submission_id")
        score["consultant_id"] = it.get("consultant_id")
        score["consultant_name"] = it.get("name")
        score["submitter_name"] = it.get("submitter_name")
        score["consultant_tjm"] = it.get("tjm")
        score["consultant_skills"] = it.get("skills")
        results.append(score)
        audit.log_event(
            "score", run_id,
            ao_id=ao.get("id"), actor_id=ran_by,
            model_version=EXTRACTION_MODEL, grid_version=GRID_VERSION,
            input_hash=audit.features_hash(features),
            payload={
                "submission_id": it.get("submission_id"),
                "consultant_id": it.get("consultant_id"),
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


async def run_submission_matching(ao_id: str, ran_by: Optional[str], top_n: int = 3) -> dict:
    """
    Score every submitted CV for this AO and persist the top N.
    Raises LookupError (no AO / no submissions) or ValueError (no readable CV).
    """
    run_id = audit.new_run_id()
    try:
        ao = supabase.table("appels_offres").select("*").eq("id", ao_id).single().execute().data
    except Exception:
        raise LookupError("AO introuvable")

    submissions = supabase.table("submissions").select(
        "id, cv_text, consultant_id, submitted_by, submitter:profiles!submitted_by(id, name), "
        "consultants(id, name, tjm, skills, experience_years, employment_type)"
    ).eq("ao_id", ao_id).execute().data

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
    } for r in results]

    return {
        "ao_id": ao_id,
        "ao_title": ao["title"],
        "total_consultants_evaluated": len(items),
        "top_n": top_n,
        "results": top_results,
        "all_scores": all_scores,
    }


async def run_vivier_matching(ao_id: str, ran_by: Optional[str], top_n: int = 3) -> Optional[dict]:
    """
    Recommend consultants straight from the vivier for a freshly created AO.
    Only consultants owned by partners with active access to the AO's client
    (or by UTI staff) are eligible. Never raises — background-task friendly.
    """
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
        return None


async def auto_rescore_ao(ao_id: str, ran_by: Optional[str]):
    """Background task: re-score an AO after a new CV lands. Never raises."""
    try:
        await run_submission_matching(ao_id, ran_by)
        print(f"[MATCHING] auto re-score done for AO {ao_id}")
    except Exception as e:
        print(f"[MATCHING] auto re-score skipped for AO {ao_id}: {e}")
