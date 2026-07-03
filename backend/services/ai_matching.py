"""
Étape 1 du pipeline de matching — EXTRACTION par LLM (uniquement).

Conformité AI Act : le génératif est cantonné à *lire et normaliser* le CV
(compétences, années d'expérience, secteurs). Il ne décide PAS du score — c'est
le rôle du moteur déterministe `services.scoring`. L'entrée est pseudonymisée en
amont (`services.pseudonymize`) et la température est fixée à 0 (reproductibilité,
Art. 15).
"""
import json
from typing import Optional
from openai import AsyncOpenAI
from config import settings
from mip_rum_ai import record_ai_call
from services.error_log import record as _record_err

# timeout : sans lui, un provider qui « hang » bloque la requête indéfiniment
# (le repli inter-providers ne se déclenche que sur exception, pas sur lenteur).
_LLM_TIMEOUT = 45  # secondes par appel — l'extraction Haiku prend < 15 s en pratique

client = AsyncOpenAI(
    api_key=settings.openrouter_key,
    base_url="https://openrouter.ai/api/v1",
    timeout=_LLM_TIMEOUT,
    max_retries=1,
) if settings.openrouter_key else None

# Fallback : Mistral La Plateforme (OpenAI-compatible, modèle gratuit)
_mistral_client = AsyncOpenAI(
    api_key=settings.mistral_key,
    base_url="https://api.mistral.ai/v1",
    timeout=_LLM_TIMEOUT,
    max_retries=1,
) if settings.mistral_key else None

# Modèle d'extraction figé et versionné (Art. 12 — traçabilité ; Art. 17 — gestion
# des modifications : tout changement déclenche tests + MAJ doc technique).
EXTRACTION_MODEL = settings.extraction_model
_MISTRAL_EXTRACTION_MODEL = settings.mistral_model

# Claude Haiku 4.5 pricing via OpenRouter
HAIKU_INPUT_COST_PER_MILLION = 1.00   # $1.00 / 1M input tokens
HAIKU_OUTPUT_COST_PER_MILLION = 5.00  # $5.00 / 1M output tokens


def calculate_cost(input_tokens, output_tokens) -> float:
    """Coût estimé en USD (tarif Haiku). Tolérant aux tokens manquants : certains
    providers (Mistral gratuit) ne renvoient pas d'usage — un None ne doit pas
    faire échouer une extraction par ailleurs réussie."""
    return ((input_tokens or 0) / 1_000_000) * HAIKU_INPUT_COST_PER_MILLION + (
        (output_tokens or 0) / 1_000_000
    ) * HAIKU_OUTPUT_COST_PER_MILLION


EXTRACTION_SYSTEM_PROMPT = """Tu es un assistant d'extraction d'informations de CV.

Ta SEULE tâche : lire un CV (déjà anonymisé) et en extraire des informations
structurées factuelles. Tu ne notes RIEN, tu ne juges RIEN, tu n'inventes RIEN.

Retourne UNIQUEMENT un JSON valide, sans markdown, au format exact :
{
  "skills": ["compétence 1", "compétence 2", ...],   // technologies/outils/méthodes explicitement mentionnés
  "experience_years": 8,                               // nombre d'années d'expérience pro (entier) ou null
  "sectors": ["banque", "assurance", ...],            // secteurs/domaines métier rencontrés
  "languages": [                                       // langues parlées + niveau, [] si non mentionné
    {"langue": "anglais", "niveau": "courant"}         // niveau : natif | courant | professionnel | intermédiaire | notions
  ],
  "summary": "résumé factuel en 1-2 phrases, sans donnée personnelle"
}

Règles :
- N'inclus jamais de nom, e-mail, téléphone, adresse, âge, genre, nationalité.
- Si une information est absente, mets une liste vide ou null. N'invente pas.
- "languages" : normalise le nom de langue en minuscules (anglais, espagnol…) et le
  niveau parmi natif/courant/professionnel/intermédiaire/notions. Si le niveau
  n'est pas précisé, mets "niveau": null. N'ajoute pas une langue non mentionnée.
"""


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_int(value) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


_LANG_LEVELS = {"natif", "courant", "professionnel", "intermédiaire", "intermediaire", "notions", "bilingue"}


def _as_languages(value) -> list[dict]:
    """Normalise la liste des langues extraites en [{"langue","niveau"}]."""
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in value:
        langue = niveau = None
        if isinstance(item, dict):
            langue = item.get("langue") or item.get("language") or item.get("nom")
            niveau = item.get("niveau") or item.get("level")
        elif isinstance(item, str):
            langue = item
        langue = str(langue or "").strip().lower()
        if not langue or langue in seen:
            continue
        seen.add(langue)
        niveau = str(niveau or "").strip().lower() or None
        if niveau and niveau not in _LANG_LEVELS:
            niveau = niveau[:40]  # niveau libre inattendu : on garde tel quel, borné
        out.append({"langue": langue[:40], "niveau": niveau})
    return out[:12]


_EMPTY_FEATURES = {"skills": [], "experience_years": None, "sectors": [], "languages": [], "summary": ""}


async def _call_extraction(c: AsyncOpenAI, model: str, cv_text: str) -> tuple[dict, float]:
    """Appel d'extraction sur un client/modèle donné. Lève en cas d'erreur."""
    _prov = "mistral" if "mistral" in str(getattr(c, "base_url", "")) else "openrouter"
    with record_ai_call(provider=_prov, model=model, operation="chat", route="matching/extract") as _call:
        response = await c.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": cv_text[:6000]},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=1500,
        )
        _u = response.usage
        _call.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                    output_tokens=getattr(_u, "completion_tokens", None),
                    cost=getattr(_u, "cost", None))
    choice = response.choices[0]
    # Sortie coupée par max_tokens = JSON invalide ou incomplet : on lève pour
    # que la chaîne de repli essaie le provider suivant, plutôt que de renvoyer
    # silencieusement une extraction partielle.
    if getattr(choice, "finish_reason", None) == "length":
        raise ValueError("extraction tronquée (max_tokens atteint)")
    data = json.loads(choice.message.content)
    features = {
        "skills": _as_list(data.get("skills")),
        "experience_years": _as_int(data.get("experience_years")),
        "sectors": _as_list(data.get("sectors")),
        "languages": _as_languages(data.get("languages")),
        "summary": str(data.get("summary") or "")[:500],
    }
    # Le coût est calculé APRÈS la construction des features : un usage manquant
    # ne doit pas transformer une extraction réussie en échec total.
    usage = response.usage
    cost = calculate_cost(getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))
    return features, cost


async def extract_features(cv_text: str) -> tuple[dict, float]:
    """
    Extrait des features structurées d'un texte de CV **déjà pseudonymisé**.
    Essaie OpenRouter en premier, puis Mistral en fallback.
    Retourne (features, cost_usd). Ne lève jamais : en cas d'erreur totale,
    renvoie des features vides (dégradation maîtrisée, Art. 15).
    """
    if not cv_text or len(cv_text.strip()) < 20:
        return dict(_EMPTY_FEATURES), 0.0

    candidates = []
    if client:
        candidates.append((client, EXTRACTION_MODEL, "OpenRouter"))
    if _mistral_client:
        candidates.append((_mistral_client, _MISTRAL_EXTRACTION_MODEL, "Mistral"))

    for c, model, provider in candidates:
        try:
            return await _call_extraction(c, model, cv_text)
        except Exception as e:  # noqa: BLE001
            print(f"[EXTRACTION] {provider} échec ({model}): {e}")
            _record_err("llm.extraction", f"{provider} ({model}) en échec", exc=e, level="warning")

    # Échec TOTAL : le flag extraction_failed doit suivre les features pour que
    # l'aval (matching_runner, UI) sache que ce "profil vide" n'est PAS un vrai
    # profil vide — sinon on note un consultant sur du néant sans le dire.
    _record_err("llm.extraction", "Tous les providers LLM en échec — extraction CV indisponible")
    out = dict(_EMPTY_FEATURES)
    out["extraction_failed"] = True
    return out, 0.0
