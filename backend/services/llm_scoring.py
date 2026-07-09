"""
Étape 2bis du pipeline — SECOND AVIS par LLM + score HYBRIDE.

Le score déterministe (`services.scoring`) reste l'ancre auditable (AI Act
Art. 13/15). Ici, un LLM produit un *avis indépendant* : une note par catégorie
sur la MÊME échelle que la grille (compétences /w, séniorité /w, contexte /w,
TJM /w), une justification par catégorie et une justification globale.

Le score hybride combine les deux par catégorie avec un « repli sur le
déterministe » : plus l'IA et la grille divergent, plus on refait confiance à la
grille — mais un plancher (A_FLOOR) empêche d'écraser totalement un avis IA
confiant (sinon les bons profils restaient bridés vers l'ancre neutre).
    proximité a = max(A_FLOOR, 1 − |D−L| / w)
    H = a · (IA_WEIGHT·L + (1−IA_WEIGHT)·D) + (1−a) · D

L'entrée du LLM est déjà pseudonymisée (features issues de `ai_matching`, aucune
PII). En cas d'absence de clé ou d'erreur, on retombe proprement sur le seul
score déterministe (dégradation maîtrisée, Art. 15).
"""
import json
from typing import Optional

from openai import AsyncOpenAI
from config import settings
from mip_rum_ai import record_ai_call
from services import ai_ledger
from services.ai_matching import calculate_cost, _LLM_TIMEOUT
from services.error_log import record as _record_err

_client = AsyncOpenAI(
    api_key=settings.openrouter_key,
    base_url="https://openrouter.ai/api/v1",
    timeout=_LLM_TIMEOUT,
    max_retries=1,
) if settings.openrouter_key else None

# Fallback : Mistral La Plateforme
_mistral_client = AsyncOpenAI(
    api_key=settings.mistral_key,
    base_url="https://api.mistral.ai/v1",
    timeout=_LLM_TIMEOUT,
    max_retries=1,
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
  "competences":             {"score": <entier 0..MAX_COMPETENCES>, "justification": "<1 phrase ancrée dans le CV>"},
  "seniorite":               {"score": <entier 0..MAX_SENIORITE>,   "justification": "<1 phrase ancrée dans le CV>"},
  "contexte":                {"score": <entier 0..MAX_CONTEXTE>,    "justification": "<1 phrase ancrée dans le CV>"},
  "points_forts_cv":         {"score": <entier 0..MAX_POINTS_FORTS_CV>,         "justification": "<1 phrase : le point fort majeur, cité du CV>"},
  "elements_differenciants": {"score": <entier 0..MAX_ELEMENTS_DIFFERENCIANTS>, "justification": "<1 phrase : ce qui différencie, cité du CV>"},
  "tjm":                     {"score": <entier 0..MAX_TJM>,          "justification": "<1 phrase>"},
  "global": "<2 à 3 phrases : qui contacter et pourquoi, points forts et réserves>"
}

Règles :
- CITE LE CV. Quand un EXTRAIT DU CV est fourni, chaque justification (sauf tjm)
  doit s'appuyer sur un élément CONCRET repris du CV : intitulé de poste, mission,
  technologie, certification, chiffre, secteur. Mets l'élément repris entre
  guillemets « … ». Une justification vague, sans ancrage dans le CV, est proscrite.
- Reste factuel, fondé sur les données fournies. N'invente JAMAIS un élément absent
  du CV (ni expérience, ni compétence, ni chiffre) : cite uniquement ce qui y figure.
- UTILISE TOUTE L'ÉTENDUE DU BARÈME — ne te réfugie pas systématiquement au milieu.
  Repères (en proportion du MAX de chaque critère) :
    • ~80-100 % du MAX : adéquation forte / évidente, éléments concrets probants.
    • ~55-75 % : correct, adéquation réelle mais partielle ou avec réserves.
    • ~25-45 % : faible, peu d'éléments pertinents.
    • 0-15 % : hors-sujet, ou aucune information exploitable dans le CV.
  Un excellent profil DOIT obtenir des scores proches du MAX ; un profil hors-sujet,
  proche de 0. N'écrase pas les bons profils par excès de prudence.
- Ne note « au milieu » que si l'information est RÉELLEMENT absente et que tu ne peux
  pas trancher — et dis-le alors dans la justification. Ce doit être l'exception.
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
            max_tokens=1200,
            extra_body=(ai_ledger.OR_USAGE if _prov == "openrouter" else {}),
        )
        _u = resp.usage
        _call.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                    output_tokens=getattr(_u, "completion_tokens", None),
                    cost=getattr(_u, "cost", None))
    ai_ledger.record(provider=_prov, model=model, operation="scoring", resp=resp)
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        # JSON coupé par max_tokens : provider suivant plutôt qu'un avis partiel.
        raise ValueError("avis IA tronqué (max_tokens atteint)")
    content = (choice.message.content or "").strip()
    if not content:
        raise ValueError("réponse LLM vide (modèle indisponible ou format non supporté)")
    data = json.loads(content)
    usage = resp.usage
    # Après le parsing : un usage manquant ne doit pas invalider un avis réussi.
    cost = calculate_cost(getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))
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


async def llm_score(
    features: dict, consultant: dict, ao: dict, weights: dict,
    cv_excerpt: Optional[str] = None, human_feedback: Optional[str] = None,
) -> tuple[Optional[dict], float]:
    """
    Second avis IA. Essaie OpenRouter en premier, puis Mistral en fallback.
    Retourne (resultat, cost_usd) ou (None, 0.0) si tous les providers échouent
    (→ fallback déterministe, dégradation maîtrisée Art. 15).

    `cv_excerpt`     : texte pseudonymisé du CV → l'IA cite des éléments concrets.
    `human_feedback` : désaccord signalé par un opérateur → réinjecté au ré-scoring.
    """
    maxes = {llm_k: int(weights.get(w_k, 0)) for llm_k, _det_k, w_k in _CATS}
    bareme = "\n".join(f"- MAX_{llm_k.upper()} = {maxes[llm_k]}" for llm_k, _d, _w in _CATS)
    parts = [
        "APPEL D'OFFRES :\n" + _ao_brief(ao),
        "PROFIL CONSULTANT (anonymisé) :\n" + _candidate_brief(features, consultant),
    ]
    # Extrait du CV (déjà pseudonymisé) : permet à l'IA de CITER des éléments
    # concrets (intitulés de poste, missions, technos, chiffres) au lieu de rester
    # générique. Borné pour tenir dans le contexte.
    if cv_excerpt and cv_excerpt.strip():
        parts.append("EXTRAIT DU CV (anonymisé — cite-le dans tes justifications) :\n"
                     + cv_excerpt.strip()[:3500])
    # Retour humain : un opérateur a signalé un désaccord avec une évaluation
    # précédente ; l'IA doit en tenir compte (supervision effective, Art. 14).
    if human_feedback and human_feedback.strip():
        parts.append(
            "⚠️ RETOUR D'UN OPÉRATEUR sur une évaluation précédente de ce profil "
            "(prends-le en compte, réévalue en conséquence et dis dans le \"global\" "
            "comment tu l'as intégré) :\n« " + human_feedback.strip()[:600] + " »"
        )
    parts.append("Barèmes (scores entiers, maximum par critère) :\n" + bareme)
    user = "\n\n".join(parts) + "\n"

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
            _record_err("llm.scoring", f"{provider} ({model}) en échec", exc=e, level="warning")

    # Pas bloquant (le déterministe reste l'ancre) mais l'admin doit le voir :
    # sans 2e avis IA, le score affiché n'est plus « hybride ».
    _record_err("llm.scoring", "Second avis IA indisponible (tous providers en échec) — score déterministe seul", level="warning")
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
    IA_WEIGHT = 0.75  # 75 % IA / 25 % grille quand accord ; repli déterministe si divergence
    # Plancher de proximité (v2.1) : sans lui, une divergence égale au poids du
    # critère annulait totalement l'avis IA (a→0, hybride = déterministe pur), ce
    # qui bridait les bons profils vers l'ancre neutre. Avec le plancher, un avis
    # IA confiant conserve toujours ≥ A_FLOOR·IA_WEIGHT (~30 %) du poids, même en
    # forte divergence — la grille reste l'ancre, sans écraser l'IA.
    A_FLOOR = 0.4
    for llm_k, det_k, w_k in _CATS:
        w = int(weights.get(w_k, 0)) or 1
        d = int(det_bd.get(det_k) or 0)
        l = int((llm_bd.get(llm_k) or {}).get("score") or 0)
        diff_sum += abs(d - l)
        a = max(A_FLOOR, 1 - abs(d - l) / w)   # proximité (bornée) sur ce critère
        hybrid_bd[det_k] = round(a * (IA_WEIGHT * l + (1 - IA_WEIGHT) * d) + (1 - a) * d)

    score_hybride = sum(hybrid_bd.values())
    # Normalise par la somme RÉELLE des poids (aujourd'hui 100 par construction,
    # mais on ne code pas cette hypothèse en dur).
    total_w = sum(int(weights.get(w_k, 0)) for _l, _d, w_k in _CATS) or 100
    agreement = round(100 * (1 - diff_sum / total_w))
    return {
        "score_llm": int(llm.get("score_llm") or 0),
        "score_hybride": int(score_hybride),
        "agreement": max(0, min(100, agreement)),
        "llm_breakdown": llm_bd,
        "llm_global": llm.get("llm_global"),
        "hybrid_breakdown": hybrid_bd,
    }
