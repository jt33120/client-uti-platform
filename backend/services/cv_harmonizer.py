"""Harmonisation / régénération d'un CV au format standard Groupement-IT.

Le LLM (OpenRouter → Mistral en repli) restructure le CV brut en un JSON strict
calqué sur le modèle GRP-IT, en français ou en anglais. Le format GRP-IT est
ANONYMISÉ : on ne conserve jamais l'identité (nom, contacts) — on démarre par
l'intitulé de poste. Cela respecte l'anonymisation (AI Act / trigrammes).
"""
import json
import re
from typing import Optional
from openai import AsyncOpenAI
from config import settings
from mip_rum_ai import record_ai_call

_client: Optional[AsyncOpenAI] = (
    AsyncOpenAI(api_key=settings.openrouter_key, base_url="https://openrouter.ai/api/v1")
    if settings.openrouter_key else None
)
_mistral: Optional[AsyncOpenAI] = (
    AsyncOpenAI(api_key=settings.mistral_key, base_url="https://api.mistral.ai/v1")
    if settings.mistral_key else None
)
HARMONIZE_MODEL = settings.draft_model
MISTRAL_MODEL = settings.mistral_model
MAX_CV_CHARS = 24000


def is_available() -> bool:
    return _client is not None or _mistral is not None


def _candidates() -> list[tuple[AsyncOpenAI, str, str]]:
    out: list[tuple[AsyncOpenAI, str, str]] = []
    if _client:
        out.append((_client, HARMONIZE_MODEL, "OpenRouter"))
    if _mistral:
        out.append((_mistral, MISTRAL_MODEL, "Mistral"))
    return out


def _extract_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


_SCHEMA = """{
  "title": "intitulé de poste (ex. « Ingénieur de développement senior ») — JAMAIS le nom de la personne",
  "synthese": ["point de synthèse 1", "point de synthèse 2"],
  "experiences": [
    {"company": "Société", "role": "Poste", "period": "2022 - Présent",
     "context": "Contexte de la mission",
     "missions": ["mission 1", "mission 2"],
     "environment": "technos séparées par des virgules"}
  ],
  "competences": {
    "metier": ["secteur/domaine"],
    "fonctionnelles": ["compétence fonctionnelle"],
    "soft_skills": ["soft skill"],
    "techniques": ["Langages: ...", "Frameworks: ...", "Outils: ..."]
  },
  "langues": ["Français (maternelle)", "Anglais (C1)"],
  "formation": ["2014 - Diplôme - École"]
}"""


def _as_list(v) -> list:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if v in (None, ""):
        return []
    return [str(v).strip()]


def _sanitize(d: dict) -> dict:
    comp = d.get("competences") or {}
    if not isinstance(comp, dict):
        comp = {}
    experiences = []
    for e in (d.get("experiences") or []):
        if not isinstance(e, dict):
            continue
        experiences.append({
            "company": str(e.get("company") or "").strip(),
            "role": str(e.get("role") or "").strip(),
            "period": str(e.get("period") or "").strip(),
            "context": str(e.get("context") or "").strip(),
            "missions": _as_list(e.get("missions")),
            "environment": str(e.get("environment") or "").strip(),
        })
    return {
        "title": str(d.get("title") or "").strip(),
        "synthese": _as_list(d.get("synthese")),
        "experiences": experiences,
        "competences": {
            "metier": _as_list(comp.get("metier")),
            "fonctionnelles": _as_list(comp.get("fonctionnelles")),
            "soft_skills": _as_list(comp.get("soft_skills")),
            "techniques": _as_list(comp.get("techniques")),
        },
        "langues": _as_list(d.get("langues")),
        "formation": _as_list(d.get("formation")),
    }


async def harmonize_cv(cv_text: str, lang: str = "fr") -> Optional[dict]:
    """Restructure un CV brut au format GRP-IT (JSON). lang = 'fr' | 'en'."""
    candidates = _candidates()
    if not candidates:
        return None
    cv_text = (cv_text or "")[:MAX_CV_CHARS]
    langue = "en anglais" if lang == "en" else "en français"

    system = (
        f"Tu mets en forme un CV brut au format standard du Groupement-IT, {langue}. "
        "Tu réorganises et reformules proprement le contenu EXISTANT, sans jamais "
        "inventer d'expérience ou de compétence absente.\n"
        "RÈGLE ABSOLUE — anonymisation : n'inclus JAMAIS le nom, prénom, email, "
        "téléphone, adresse ou photo de la personne. Le document commence par "
        "l'intitulé de poste.\n"
        f"Traduis TOUT le contenu {langue}. "
        "Réponds STRICTEMENT en JSON valide conforme à ce schéma (sans texte autour) :\n"
        + _SCHEMA
    )

    last_err = None
    for client, model, provider in candidates:
        try:
            with record_ai_call(provider=provider.lower(), model=model, route="cv/harmonize") as _call:
                resp = await client.chat.completions.create(
                    model=model,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": cv_text},
                    ],
                )
                _u = getattr(resp, "usage", None)
                if _u:
                    _call.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                                output_tokens=getattr(_u, "completion_tokens", None),
                                cost=getattr(_u, "cost", None))
            data = _extract_json(resp.choices[0].message.content or "")
            if data:
                return _sanitize(data)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[CV_HARMONIZER] {provider} échec: {e}")
    if last_err is not None:
        raise last_err
    return None
