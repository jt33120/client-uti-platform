"""
Motif de refus d'un candidat (« Non retenu ») — pré-rempli par l'IA, choisissable
dans une liste, et affiché au partenaire pour lever l'effet « boîte noire ».

  • REASONS : liste canonique (code + libellé) partagée comme référentiel ;
  • suggest_refusal_reason(ao, match) : propose 1 phrase courte + un code, à partir
    du profil scoré (anonymisé) vs l'AO. Dégradation maîtrisée : sans clé LLM ou en
    cas d'échec, repli DÉTERMINISTE dérivé du plus faible critère du score. Ne lève
    jamais (Art. 15 — jamais d'erreur bloquante sur une aide à la décision).

Aucune PII n'est transmise au LLM : le profil est décrit par ses % de critères et
son avis de matching, jamais par son nom.
"""
from openai import AsyncOpenAI
from config import settings
from mip_rum_ai import record_ai_call
from services import ai_ledger
from services.ai_matching import _LLM_TIMEOUT
from services.error_log import record as _record_err
from services.cv_harmonizer import _extract_json

# Référentiel des motifs (code → libellé court). Le front propose la même liste ;
# le code sert de source de vérité pour la suggestion IA et l'analytique.
REASONS = [
    {"code": "tjm_eleve", "label": "TJM au-dessus de la fourchette cible"},
    {"code": "seniorite", "label": "Séniorité insuffisante pour la mission"},
    {"code": "competences", "label": "Compétences techniques clés non couvertes"},
    {"code": "domaine", "label": "Manque d'expérience dans le secteur / domaine"},
    {"code": "disponibilite", "label": "Disponibilité incompatible avec le besoin"},
    {"code": "localisation", "label": "Localisation / mobilité incompatible"},
    {"code": "deja_pourvu", "label": "Mission déjà pourvue / short-list bouclée"},
    {"code": "autre", "label": "Autre (précisé ci-dessous)"},
]
_CODES = {r["code"] for r in REASONS}

# Critère de score (clé breakdown, clé de poids) → code de motif le plus proche.
_CRIT_TO_CODE = [
    ("competences_techniques", "w_competences", "competences"),
    ("seniorite", "w_seniorite", "seniorite"),
    ("contexte_domaine", "w_contexte", "domaine"),
    ("compatibilite_tjm", "w_tjm", "tjm_eleve"),
]
_CODE_LABEL = {r["code"]: r["label"] for r in REASONS}

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

_SYSTEM = """Tu es un consultant en recrutement IT (ESN). Un candidat n'a PAS été
retenu pour une mission. Rédige le motif de refus qui sera communiqué au partenaire
qui a proposé le profil : franc, factuel, respectueux, ACTIONNABLE (le partenaire
doit comprendre quoi améliorer la prochaine fois). Une seule phrase, ≤ 160 caractères.

On te donne l'appel d'offres et le profil (ANONYMISÉ : % par critère + avis). Ne
mentionne jamais de nom. Ne donne pas de score chiffré. Reste concret (compétence
manquante, séniorité, TJM, domaine…), jamais vague.

Retourne UNIQUEMENT un JSON valide (sans markdown) au format EXACT :
{"code": "<un code parmi la liste>", "reason": "<la phrase de motif>"}

Codes autorisés : tjm_eleve, seniorite, competences, domaine, disponibilite,
localisation, deja_pourvu, autre.
"""


def _match_brief(ao: dict, match: dict) -> str:
    weights = match.get("weights") or {}
    hb = match.get("hybrid_breakdown") or match.get("breakdown") or {}
    parts = []
    for k, wk, _code in _CRIT_TO_CODE + [("points_forts_cv", "w_points_forts_cv", "autre"),
                                          ("elements_differenciants", "w_elements_differenciants", "autre")]:
        mx = int(weights.get(wk, 0) or 0)
        if mx <= 0 or hb.get(k) is None:
            continue
        parts.append(f"{k} {round((hb[k] / mx) * 100)}%")
    avis = (match.get("llm_global") or "").strip()
    return "\n".join([
        f"AO — titre : {ao.get('title') or '—'}",
        f"AO — compétences attendues : {ao.get('skills_required') or '—'}",
        f"AO — séniorité / budget : {ao.get('seniority') or '—'} / {ao.get('budget_max') or '—'} €/j",
        f"AO — contexte : {(ao.get('context') or '—')[:1500]}",
        "Profil (anonymisé) : " + (" · ".join(parts) if parts else "détail indisponible"),
        (f'Avis de matching : "{avis[:400]}"' if avis else ""),
    ])


def _deterministic(match: dict) -> dict:
    """Repli sans LLM : motif dérivé du critère le plus faible (en % du barème)."""
    weights = match.get("weights") or {}
    hb = match.get("hybrid_breakdown") or match.get("breakdown") or {}
    worst = None  # (pct, code)
    for k, wk, code in _CRIT_TO_CODE:
        mx = int(weights.get(wk, 0) or 0)
        if mx <= 0 or hb.get(k) is None:
            continue
        pct = round((hb[k] / mx) * 100)
        if worst is None or pct < worst[0]:
            worst = (pct, code)
    code = worst[1] if worst else "autre"
    return {"code": code, "reason": _CODE_LABEL.get(code, _CODE_LABEL["autre"]), "source": "deterministic"}


async def _call(c: AsyncOpenAI, model: str, user: str) -> dict:
    _prov = "mistral" if "mistral" in str(getattr(c, "base_url", "")) else "openrouter"
    with record_ai_call(provider=_prov, model=model, operation="chat", route="matching/refusal") as _c:
        resp = await c.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=200,
            extra_body=(ai_ledger.OR_USAGE if _prov == "openrouter" else {}),
        )
        _u = resp.usage
        _c.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                 output_tokens=getattr(_u, "completion_tokens", None),
                 cost=getattr(_u, "cost", None))
    ai_ledger.record(provider=_prov, model=model, operation="refusal", resp=resp)
    content = (resp.choices[0].message.content or "").strip()
    data = _extract_json(content) if content else None
    if not data:
        raise ValueError("JSON de motif illisible")
    return data


async def suggest_refusal_reason(ao: dict, match: dict) -> dict:
    """Propose {code, reason, source}. Ne lève jamais : repli déterministe garanti."""
    fallback = _deterministic(match or {})
    user = _match_brief(ao or {}, match or {}) + "\n\nRédige le motif de refus au format JSON demandé."

    candidates = []
    if _client:
        candidates.append((_client, settings.scoring_model, "OpenRouter"))
    if _mistral_client:
        candidates.append((_mistral_client, settings.mistral_model, "Mistral"))

    for c, model, provider in candidates:
        try:
            data = await _call(c, model, user)
            code = str(data.get("code") or "").strip().lower()
            if code not in _CODES:
                code = fallback["code"]
            reason = str(data.get("reason") or "").strip()[:200] or _CODE_LABEL.get(code, "")
            return {"code": code, "reason": reason, "source": "llm"}
        except Exception as e:  # noqa: BLE001
            print(f"[REFUSAL] {provider} échec ({model}): {e}")
            _record_err("matching.refusal", f"{provider} ({model}) en échec", exc=e, level="warning")

    return fallback
