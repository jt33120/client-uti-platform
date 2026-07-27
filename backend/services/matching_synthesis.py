"""
Synthèse transverse du vivier — un LLM lit TOUS les profils scorés d'un AO et
produit la lecture d'ensemble que le radar par candidat ne contient pas :

  • qualité globale du vivier pour CETTE mission ;
  • resserrement (course serrée vs un profil qui se détache nettement) ;
  • profils qui sortent du lot et POURQUOI (différenciateurs concrets) ;
  • angles morts : exigence / compétence qu'aucun candidat ne couvre bien ;
  • lecture par critère (qui mène, dispersion) ;
  • recommandation d'action (combien de profils shortlister).

Entrée PSEUDONYMISÉE : les profils sont étiquetés « Profil #<rang> » (aucun nom,
aucune PII transmise au LLM — cohérent avec `llm_scoring`). Dégradation maîtrisée
(AI Act Art. 15) : sans clé ou en cas d'échec de tous les providers, on renvoie
une synthèse DÉTERMINISTE calculée à partir des scores — jamais d'erreur bloquante.
"""
import json
from typing import Optional

from openai import AsyncOpenAI
from config import settings
from mip_rum_ai import record_ai_call, flag_refusal
from services import ai_ledger
from services.ai_matching import calculate_cost, _LLM_TIMEOUT
from services.error_log import record as _record_err
from services.cv_harmonizer import _extract_json

_client = AsyncOpenAI(
    api_key=settings.openrouter_key,
    base_url="https://openrouter.ai/api/v1",
    timeout=_LLM_TIMEOUT,
    max_retries=1,
) if settings.openrouter_key else None

_mistral_client = AsyncOpenAI(
    api_key=settings.mistral_key,
    base_url="https://api.mistral.ai/v1",
    timeout=_LLM_TIMEOUT,
    max_retries=1,
) if settings.mistral_key else None

SYNTHESIS_MODEL = settings.scoring_model
_MISTRAL_MODEL = settings.mistral_model

# Critères : (clé déterministe = clé du breakdown, clé de poids, libellé court).
# Même ordre / mêmes clés que `llm_scoring._CATS` et le front (SCORE_CATS).
_CATS = [
    ("competences_techniques", "w_competences", "Compétences"),
    ("seniorite", "w_seniorite", "Séniorité"),
    ("contexte_domaine", "w_contexte", "Contexte"),
    ("points_forts_cv", "w_points_forts_cv", "Atouts"),
    ("elements_differenciants", "w_elements_differenciants", "Différenciants"),
    ("compatibilite_tjm", "w_tjm", "TJM"),
]

_SYSTEM = """Tu es un consultant en recrutement IT (ESN) qui prépare une short-list.

On te donne un appel d'offres puis PLUSIEURS profils déjà notés (score /100 et
détail par critère en % de leur barème). Les profils sont ANONYMISÉS et étiquetés
« Profil #<rang> ». Ta mission : produire la LECTURE D'ENSEMBLE du vivier — ce que
le détail par candidat ne dit pas. Compare les profils ENTRE EUX.

Ne répète PAS les scores : apporte de la valeur AU-DELÀ des chiffres (dynamique
du vivier, écarts, angles morts, arbitrages).

Retourne UNIQUEMENT un JSON valide (sans markdown), au format EXACT :
{
  "pool_quality": "fort" | "correct" | "faible",
  "pool_verdict": "<2 à 3 phrases : le vivier est-il à la hauteur de la mission ?>",
  "tightness": "<1 à 2 phrases : est-ce serré entre les premiers, ou un profil se détache-t-il nettement ? pourquoi>",
  "standouts": [
    {"rank": <entier>, "point": "<1 phrase : ce qui distingue CE profil des autres, au-delà du score>"}
  ],
  "gaps": ["<1 phrase : une exigence de l'AO qu'AUCUN profil (ou presque) ne couvre bien>"],
  "criterion_insights": {
__CRITERION_INSIGHTS__
  },
  "recommendation": "<1 à 2 phrases : combien de profils présenter au client et lesquels (par rang), pourquoi>"
}

Règles :
- Désigne les profils par leur RANG (« le profil #1 », « les profils #2 et #3 »). N'invente jamais de nom.
- 1 à 3 standouts maximum ; 0 à 3 gaps. Si le vivier est homogène, dis-le plutôt que d'inventer un écart.
- Les critères listés ci-dessus sont les SEULS retenus pour cette mission. N'en
  invoque aucun autre — ni dans "criterion_insights", ni dans "recommendation",
  ni dans les "standouts". Un critère absent de la liste a été délibérément exclu
  du barème : le citer comme argument de sélection serait une erreur.
- Reste factuel : appuie-toi sur les scores et les avis fournis, n'invente aucune compétence.
- Français, concis, orienté décision. Pas de name-dropping, pas de PII.
"""


def _profiles_block(results: list[dict], weights: dict) -> tuple[str, list[str]]:
    """Bloc texte anonymisé (un paragraphe par profil, par rang) + liste des
    clés de critères ACTIFS (barème > 0) pour cadrer la sortie du LLM."""
    active = [(k, wk, lbl) for k, wk, lbl in _CATS if int(weights.get(wk, 0) or 0) > 0]
    lines: list[str] = []
    for i, r in enumerate(results, start=1):
        hb = r.get("hybrid_breakdown") or r.get("breakdown") or {}
        w = r.get("weights") or weights or {}
        score = r.get("score_hybride")
        if score is None:
            score = r.get("score_total")
        parts = []
        for k, wk, lbl in active:
            mx = int(w.get(wk, 0) or 0) or 1
            val = hb.get(k)
            if val is None:
                continue
            parts.append(f"{lbl} {round((val / mx) * 100)}%")
        crit = " · ".join(parts) if parts else "détail indisponible"
        block = [f"Profil #{i} — score {round(score or 0)}/100", f"  {crit}"]
        avis = (r.get("llm_global") or "").strip()
        if avis:
            block.append(f'  Avis : "{avis[:400]}"')
        lines.append("\n".join(block))
    return "\n\n".join(lines), [k for k, _wk, _lbl in active]


def _ao_brief(ao: dict) -> str:
    return "\n".join([
        f"Titre : {ao.get('title') or '—'}",
        f"Type / secteur : {ao.get('ao_type') or '—'}",
        f"Compétences attendues : {ao.get('skills_required') or '—'}",
        f"Contexte : {(ao.get('context') or '—')[:6000]}",
        f"Séniorité / durée : {ao.get('seniority') or '—'} / {ao.get('duration') or '—'}",
        f"Budget max : {ao.get('budget_max') or '—'} €/j",
    ])


def _deterministic(results: list[dict], weights: dict) -> dict:
    """Synthèse de repli, sans LLM : verdict + resserrement dérivés des scores.
    Toujours cohérente, jamais bloquante (Art. 15)."""
    def _sc(r):
        s = r.get("score_hybride")
        return int(round(s if s is not None else (r.get("score_total") or 0)))

    scores = sorted((_sc(r) for r in results), reverse=True)
    n = len(scores)
    best = scores[0] if scores else 0
    avg = round(sum(scores) / n) if n else 0
    gap = (scores[0] - scores[1]) if n >= 2 else 0
    quality = "fort" if avg >= 65 else "correct" if avg >= 45 else "faible"
    tight = (
        f"Course serrée : {gap} pt d'écart entre le 1ᵉʳ et le 2ᵉ." if gap < 8
        else f"Le profil #1 se détache ({gap} pts d'avance sur le 2ᵉ)."
    ) if n >= 2 else "Un seul profil évalué."

    # Meneur par critère (en % du barème).
    insights: dict = {}
    for k, wk, lbl in _CATS:
        mx = int(weights.get(wk, 0) or 0)
        if mx <= 0:
            insights[k] = ""
            continue
        ranked = []
        for i, r in enumerate(results, start=1):
            hb = r.get("hybrid_breakdown") or r.get("breakdown") or {}
            if hb.get(k) is not None:
                ranked.append((i, round((hb[k] / mx) * 100)))
        if not ranked:
            insights[k] = ""
            continue
        ranked.sort(key=lambda t: t[1], reverse=True)
        top_i, top_v = ranked[0]
        spread = top_v - ranked[-1][1]
        insights[k] = (
            f"Profil #{top_i} en tête ({top_v}%)"
            + (f", vivier dispersé (écart {spread} pts)." if spread >= 25 else ", vivier homogène.")
        )
    return {
        "pool_quality": quality,
        "pool_verdict": f"Vivier {quality} : moyenne {avg}/100 sur {n} profil{'s' if n > 1 else ''}, meilleur score {best}/100.",
        "tightness": tight,
        "standouts": [{"rank": 1, "point": "Meilleur score global du vivier."}] if n else [],
        "gaps": [],
        "criterion_insights": insights,
        "recommendation": (
            "Un profil se détache : présentez-le en priorité." if gap >= 8
            else "Profils serrés : présentez les 2–3 premiers au client pour arbitrage."
        ) if n >= 2 else "Un seul profil : à confirmer avant présentation.",
        "source": "deterministic",
    }


# Amorce de la phrase attendue par critère (le 1er porte la consigne complète).
_INSIGHT_HINT = {
    "competences_techniques": "<1 phrase : qui mène sur ce critère, est-ce dispersé ?>",
}


def _system(active_keys: list[str]) -> str:
    """
    Système paramétré par les critères RÉELLEMENT actifs sur cette mission.

    Un critère à 0★ ne doit pas apparaître du tout : la seule présence de sa clé
    dans le schéma le gardait saillant pour le modèle, qui le ressortait ensuite
    en argument libre dans `recommendation` (« ... avec un TJM compatible »)
    alors même qu'aucune donnée TJM ne lui était transmise.
    """
    lines = ",\n".join(
        f'    "{k}": "{_INSIGHT_HINT.get(k, "<1 phrase>")}"' for k in active_keys
    )
    return _SYSTEM.replace("__CRITERION_INSIGHTS__", lines)


async def _call(c: AsyncOpenAI, model: str, user: str, active_keys: list[str]) -> dict:
    _prov = "mistral" if "mistral" in str(getattr(c, "base_url", "")) else "openrouter"
    with record_ai_call(provider=_prov, model=model, operation="synthesis", route="matching/synthesis") as _call:
        resp = await c.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _system(active_keys)}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=1400,
            extra_body=(ai_ledger.OR_USAGE if _prov == "openrouter" else {}),
        )
        _u = resp.usage
        _call.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                    output_tokens=getattr(_u, "completion_tokens", None),
                    cost=getattr(_u, "cost", None))
        flag_refusal(_call, resp)  # refus modèle → refusal_rate (MIP)
    ai_ledger.record(provider=_prov, model=model, operation="synthesis", resp=resp)
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise ValueError("synthèse IA tronquée (max_tokens atteint)")
    content = (choice.message.content or "").strip()
    if not content:
        raise ValueError("réponse LLM vide")
    data = _extract_json(content)
    if not data:
        raise ValueError("JSON de synthèse illisible")
    return data


def _sanitize(data: dict, active_keys: list[str], n: int) -> dict:
    """Borne et normalise la sortie LLM (types, longueurs, rangs valides)."""
    quality = str(data.get("pool_quality") or "").lower()
    if quality not in ("fort", "correct", "faible"):
        quality = "correct"

    def _s(v, cap=600):
        return str(v or "").strip()[:cap]

    standouts = []
    for it in (data.get("standouts") or [])[:3]:
        if not isinstance(it, dict):
            continue
        try:
            rk = int(it.get("rank"))
        except (TypeError, ValueError):
            continue
        if 1 <= rk <= n and it.get("point"):
            standouts.append({"rank": rk, "point": _s(it.get("point"), 300)})

    gaps = [_s(g, 300) for g in (data.get("gaps") or [])[:3] if str(g or "").strip()]

    ci_in = data.get("criterion_insights") or {}
    insights = {k: _s(ci_in.get(k), 300) for k in active_keys}

    return {
        "pool_quality": quality,
        "pool_verdict": _s(data.get("pool_verdict"), 700),
        "tightness": _s(data.get("tightness"), 400),
        "standouts": standouts,
        "gaps": gaps,
        "criterion_insights": insights,
        "recommendation": _s(data.get("recommendation"), 500),
        "source": "llm",
    }


async def synthesize_pool(ao: dict, results: list[dict], weights: dict) -> dict:
    """
    Produit la synthèse transverse du vivier. `results` doit être trié par rang
    (le meilleur en premier). Retombe sur une synthèse déterministe si le LLM est
    indisponible. Ne lève jamais (dégradation maîtrisée).
    """
    if not results:
        return {**_deterministic(results, weights), "profiles": 0}

    block, active_keys = _profiles_block(results, weights)
    user = (
        "APPEL D'OFFRES :\n" + _ao_brief(ao)
        + "\n\nPROFILS DU VIVIER (anonymisés, triés par rang) :\n" + block
        + "\n\nProduis la synthèse d'ensemble au format JSON demandé."
    )

    candidates = []
    if _client:
        candidates.append((_client, SYNTHESIS_MODEL, "OpenRouter"))
    if _mistral_client:
        candidates.append((_mistral_client, _MISTRAL_MODEL, "Mistral"))

    for c, model, provider in candidates:
        try:
            data = await _call(c, model, user, active_keys)
            out = _sanitize(data, active_keys, len(results))
            out["profiles"] = len(results)
            return out
        except Exception as e:  # noqa: BLE001
            print(f"[SYNTHESIS] {provider} échec ({model}): {e}")
            _record_err("matching.synthesis", f"{provider} ({model}) en échec", exc=e, level="warning")

    _record_err("matching.synthesis", "Synthèse IA indisponible (tous providers) — repli déterministe", level="warning")
    out = _deterministic(results, weights)
    out["profiles"] = len(results)
    return out
