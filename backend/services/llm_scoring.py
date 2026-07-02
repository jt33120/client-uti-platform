"""
Étape 2bis du pipeline — SECOND AVIS par LLM + score HYBRIDE.

Le score déterministe (`services.scoring`) reste l'ancre auditable (AI Act
Art. 13/15). Ici, un LLM produit un *avis indépendant* : une note par catégorie
sur la MÊME échelle que la grille (compétences /w, séniorité /w, contexte /w,
TJM /w), une justification par catégorie et une justification globale.

Le score hybride combine les deux par catégorie avec un « repli sur le
déterministe » : plus l'IA et la grille divergent, plus on fait confiance à la
grille.
    proximité a = 1 − |D−L| / w
    H = a · (D+L)/2 + (1−a) · D

L'entrée du LLM est déjà pseudonymisée (features issues de `ai_matching`, aucune
PII). En cas d'absence de clé ou d'erreur, on retombe proprement sur le seul
score déterministe (dégradation maîtrisée, Art. 15).
"""
import json
from typing import Optional

from openai import AsyncOpenAI
from config import settings
from mip_rum_ai import record_ai_call
from services.ai_matching import calculate_cost

_client = AsyncOpenAI(
    api_key=settings.openrouter_key,
    base_url="https://openrouter.ai/api/v1",
) if settings.openrouter_key else None

# Fallback : Mistral La Plateforme
_mistral_client = AsyncOpenAI(
    api_key=settings.mistral_key,
    base_url="https://api.mistral.ai/v1",
) if settings.mistral_key else None

SCORING_MODEL = settings.scoring_model
_MISTRAL_SCORING_MODEL = settings.mistral_model

# Correspondance clés LLM ↔ clés du breakdown déterministe (`services.scoring`).
# Depuis v2 : deux axes qualitatifs supplémentaires (points forts / différenciation)
# que SEUL le LLM sait noter (la grille déterministe les pose en neutre).
_CATS = [
    ("competences", "competences_techniques", "w_competences"),
    ("seniorite", "seniorite", "w_seniorite"),
    ("contexte", "contexte_domaine", "w_contexte"),
    ("points_forts_cv", "points_forts_cv", "w_points_forts_cv"),
    ("elements_differenciants", "elements_differenciants", "w_elements_differenciants"),
    ("tjm", "compatibilite_tjm", "w_tjm"),
]

_SYSTEM = """Tu es un évaluateur de candidatures pour des missions IT (ESN).

On te donne un appel d'offres et le profil ANONYMISÉ d'un consultant. Tu dois
noter l'adéquation du profil sur 6 critères, CHACUN sur son barème propre fourni
dans la requête (0 = nul … MAX = parfait), puis justifier brièvement.

Critères :
- competences : adéquation des compétences techniques aux compétences attendues.
- seniorite : niveau d'expérience au regard de la séniorité attendue.
- contexte : proximité du secteur / contexte de la mission avec le vécu du profil.
- points_forts_cv : force et pertinence des points forts du CV pour CETTE mission
  (réalisations concrètes, certifications, expertises marquantes).
- elements_differenciants : ce qui distingue ce profil d'un candidat générique pour
  cette mission (spécialisations rares, combinaisons de compétences, expériences peu communes).
- tjm : compatibilité du TJM avec le budget de l'AO.

Retourne UNIQUEMENT un JSON valide (sans markdown) au format EXACT :
{
  "competences":             {"score": <entier 0..MAX_COMPETENCES>, "justification": "<1 phrase concrète>"},
  "seniorite":               {"score": <entier 0..MAX_SENIORITE>,   "justification": "<1 phrase>"},
  "contexte":                {"score": <entier 0..MAX_CONTEXTE>,    "justification": "<1 phrase>"},
  "points_forts_cv":         {"score": <entier 0..MAX_POINTS_FORTS_CV>,         "justification": "<1 phrase : le point fort majeur>"},
  "elements_differenciants": {"score": <entier 0..MAX_ELEMENTS_DIFFERENCIANTS>, "justification": "<1 phrase : ce qui différencie>"},
  "tjm":                     {"score": <entier 0..MAX_TJM>,          "justification": "<1 phrase>"},
  "global": "<2 à 3 phrases : qui contacter et pourquoi, points forts et réserves>"
}

Règles :
- Reste factuel, fondé sur les données fournies. N'invente pas d'expérience absente.
- Si une information manque, note prudemment au milieu du barème et dis-le dans la justification.
- Si un critère a un MAX de 0, il est désactivé : renvoie score 0 et justification vide.
- Ne mentionne jamais de nom, contact ou donnée personnelle.
"""


def _clampi(v, lo: int, hi: int) -> int:
    try:
        v = int(round(float(v)))
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def _ao_brief(ao: dict) -> str:
    bits = [
        f"Titre : {ao.get('title') or '—'}",
        f"Type / secteur : {ao.get('ao_type') or '—'}",
        f"Compétences attendues : {ao.get('skills_required') or '—'}",
        f"Contexte : {(ao.get('context') or '—')[:600]}",
        f"Durée : {ao.get('duration') or '—'}",
        f"Localisation : {ao.get('location') or '—'}",
        f"Budget max : {ao.get('budget_max') or '—'} €/j",
    ]
    return "\n".join(bits)


def _candidate_brief(features: dict, consultant: dict) -> str:
    skills = ", ".join(features.get("skills") or []) or (consultant.get("skills") or "—")
    sectors = ", ".join(features.get("sectors") or []) or "—"
    years = features.get("experience_years")
    if years is None:
        years = consultant.get("experience_years")
    bits = [
        f"Compétences : {skills}",
        f"Secteurs rencontrés : {sectors}",
        f"Expérience : {years if years is not None else '—'} ans",
        f"TJM : {consultant.get('tjm') if consultant.get('tjm') is not None else '—'} €/j",
        f"Résumé : {(features.get('summary') or '—')[:500]}",
    ]
    return "\n".join(bits)


async def _call_scoring(c: AsyncOpenAI, model: str, user: str, maxes: dict) -> tuple[Optional[dict], float]:
    """Appel de scoring sur un client/modèle donné. Lève en cas d'erreur."""
    _prov = "mistral" if "mistral" in str(getattr(c, "base_url", "")) else "openrouter"
    with record_ai_call(provider=_prov, model=model, operation="chat", route="matching/score") as _call:
        resp = await c.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=700,
        )
        _u = resp.usage
        _call.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                    output_tokens=getattr(_u, "completion_tokens", None),
                    cost=getattr(_u, "cost", None))
    data = json.loads(resp.choices[0].message.content)
    usage = resp.usage
    cost = calculate_cost(usage.prompt_tokens, usage.completion_tokens)
    breakdown = {}
    score_llm = 0
    for llm_k, _det_k, _w_k in _CATS:
        cell = data.get(llm_k) or {}
        s = _clampi(cell.get("score"), 0, maxes[llm_k])
        breakdown[llm_k] = {"score": s, "justification": str(cell.get("justification") or "")[:300]}
        score_llm += s
    return {
        "score_llm": score_llm,
        "llm_breakdown": breakdown,
        "llm_global": str(data.get("global") or "")[:800],
    }, cost


async def llm_score(features: dict, consultant: dict, ao: dict, weights: dict) -> tuple[Optional[dict], float]:
    """
    Second avis IA. Essaie OpenRouter en premier, puis Mistral en fallback.
    Retourne (resultat, cost_usd) ou (None, 0.0) si tous les providers échouent
    (→ fallback déterministe, dégradation maîtrisée Art. 15).
    """
    maxes = {llm_k: int(weights.get(w_k, 0)) for llm_k, _det_k, w_k in _CATS}
    bareme = "\n".join(f"- MAX_{llm_k.upper()} = {maxes[llm_k]}" for llm_k, _d, _w in _CATS)
    user = (
        "APPEL D'OFFRES :\n" + _ao_brief(ao) + "\n\n"
        "PROFIL CONSULTANT (anonymisé) :\n" + _candidate_brief(features, consultant) + "\n\n"
        "Barèmes (scores entiers, maximum par critère) :\n" + bareme + "\n"
    )

    candidates = []
    if _client:
        candidates.append((_client, SCORING_MODEL, "OpenRouter"))
    if _mistral_client:
        candidates.append((_mistral_client, _MISTRAL_SCORING_MODEL, "Mistral"))

    for c, model, provider in candidates:
        try:
            return await _call_scoring(c, model, user, maxes)
        except Exception as e:  # noqa: BLE001
            print(f"[LLM_SCORING] {provider} échec ({model}): {e}")

    return None, 0.0


def combine_hybrid(deterministic: dict, llm: Optional[dict], weights: dict) -> dict:
    """
    Fusionne le score déterministe et l'avis IA, critère par critère, avec repli
    sur le déterministe en cas de divergence. Retourne un dict des champs hybrides
    (toujours sûr : si `llm` est None, le hybride = déterministe).
    """
    det_bd = deterministic.get("breakdown") or {}
    det_total = int(deterministic.get("score_total") or 0)

    if not llm:
        return {
            "score_llm": None,
            "score_hybride": det_total,
            "agreement": None,
            "llm_breakdown": None,
            "llm_global": None,
            "hybrid_breakdown": None,
        }

    llm_bd = llm.get("llm_breakdown") or {}
    hybrid_bd: dict = {}
    diff_sum = 0
    IA_WEIGHT = 0.65  # 65 % IA / 35 % grille quand accord parfait ; repli déterministe si divergence
    for llm_k, det_k, w_k in _CATS:
        w = int(weights.get(w_k, 0)) or 1
        d = int(det_bd.get(det_k) or 0)
        l = int((llm_bd.get(llm_k) or {}).get("score") or 0)
        diff_sum += abs(d - l)
        a = 1 - abs(d - l) / w            # proximité sur ce critère
        hybrid_bd[det_k] = round(a * (IA_WEIGHT * l + (1 - IA_WEIGHT) * d) + (1 - a) * d)

    score_hybride = sum(hybrid_bd.values())
    agreement = round(100 * (1 - diff_sum / 100))  # somme des poids = 100
    return {
        "score_llm": int(llm.get("score_llm") or 0),
        "score_hybride": int(score_hybride),
        "agreement": max(0, min(100, agreement)),
        "llm_breakdown": llm_bd,
        "llm_global": llm.get("llm_global"),
        "hybrid_breakdown": hybrid_bd,
    }
