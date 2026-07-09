"""
AI drafting of an "Appel d'Offres" from raw source material.

Takes the raw text of an AO (pasted email + extracted attachment text) and asks
the LLM (OpenRouter / Claude, same client as the assistant & matching engine) to
return a clean, structured set of AO fields that the admin then reviews and edits
before saving. The model is instructed to extract only — never to invent missing
data.
"""
import json
import re
from typing import Optional

from openai import AsyncOpenAI

from config import settings
from mip_rum_ai import record_ai_call
from services import ai_ledger
from services.scoring import STAR_CRITERIA

_client: Optional[AsyncOpenAI] = (
    AsyncOpenAI(api_key=settings.openrouter_key, base_url="https://openrouter.ai/api/v1", timeout=60, max_retries=1)
    if settings.openrouter_key
    else None
)
# Repli Mistral (même schéma que le scoring matching) : si OpenRouter est
# indisponible — clé révoquée/expirée (401 « User not found »), quota, panne —
# la génération d'AO continue de fonctionner au lieu d'échouer sèchement.
_mistral_client: Optional[AsyncOpenAI] = (
    AsyncOpenAI(api_key=settings.mistral_key, base_url="https://api.mistral.ai/v1", timeout=60, max_retries=1)
    if settings.mistral_key
    else None
)
# Génération de fiche AO : qualité rédactionnelle → Sonnet.
# Résumé en une phrase : trivial → Haiku (économique). Tous deux via .env.
DRAFT_MODEL = settings.draft_model
SUMMARY_MODEL = settings.summary_model
MISTRAL_MODEL = settings.mistral_model
MAX_SOURCE_CHARS = 24000


def is_available() -> bool:
    return _client is not None or _mistral_client is not None


def _draft_candidates() -> list[tuple[AsyncOpenAI, str, str]]:
    """Providers à essayer dans l'ordre pour la génération : OpenRouter puis Mistral."""
    out: list[tuple[AsyncOpenAI, str, str]] = []
    if _client:
        out.append((_client, DRAFT_MODEL, "OpenRouter"))
    if _mistral_client:
        out.append((_mistral_client, MISTRAL_MODEL, "Mistral"))
    return out


def _summary_candidates() -> list[tuple[AsyncOpenAI, str, str]]:
    out: list[tuple[AsyncOpenAI, str, str]] = []
    if _client:
        out.append((_client, SUMMARY_MODEL, "OpenRouter"))
    if _mistral_client:
        out.append((_mistral_client, MISTRAL_MODEL, "Mistral"))
    return out


def _extract_json(raw: str) -> Optional[dict]:
    """Best-effort parse of a JSON object from the model output."""
    if not raw:
        return None
    raw = raw.strip()
    # Strip ```json ... ``` fences if present
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _sanitize(d: dict, ao_types: list[str]) -> dict:
    """Coerce the model output into safe, form-ready values."""
    def text(key: str) -> str:
        v = d.get(key)
        if v is None:
            return ""
        return v.strip() if isinstance(v, str) else str(v).strip()

    budget = d.get("budget_max")
    try:
        budget = int(budget) if budget not in (None, "", "null") else None
    except (ValueError, TypeError):
        budget = None

    ao_type = text("ao_type")
    if ao_type not in ao_types:
        ao_type = ""

    work_mode = text("work_mode").lower()
    if work_mode not in ("onsite", "hybrid", "remote"):
        work_mode = ""

    deadline = text("deadline")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", deadline):
        deadline = ""

    return {
        "title": text("title")[:300],
        "description": text("description"),
        "skills_required": text("skills_required"),
        "reference": text("reference")[:200],
        "ao_type": ao_type,
        "budget_max": budget,
        "location": text("location"),
        "work_mode": work_mode,
        "duration": text("duration"),
        "deadline": deadline,
        "context": text("context"),
        "langue_requise": text("langue_requise")[:120],
        # Nom du client détecté (ex. "CMA CGM"). Simple SUGGESTION : le front la
        # rapproche d'un client existant pour pré-sélectionner la liste ; jamais
        # imposée (l'admin valide).
        "client_hint": text("client")[:120],
        # Suggestion (jamais imposée) des priorités de matching : l'admin garde
        # la main et peut tout ajuster avant d'enregistrer l'AO.
        "scoring_stars": _stars(d.get("importance")),
    }


def _stars(imp) -> Optional[dict]:
    """Normalise la suggestion d'importance (0-5 par critère) renvoyée par le LLM."""
    if not isinstance(imp, dict):
        return None
    out = {}
    for c in STAR_CRITERIA:
        v = imp.get(c)
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        out[c] = max(0, min(5, v))
    return out or None


async def draft_ao_fields(source: str, ao_types: list[str]) -> Optional[dict]:
    """
    Generate structured AO fields from raw source text.
    Returns a dict of form fields, or None if the model output was unusable.
    Essaie OpenRouter puis Mistral en repli (dégradation maîtrisée).
    """
    candidates = _draft_candidates()
    if not candidates:
        return None

    source = source[:MAX_SOURCE_CHARS]
    system = (
        "Tu es un assistant staffing qui transforme un appel d'offres brut (email "
        "et/ou pièces jointes) en une fiche d'AO structurée, claire et "
        "professionnelle, en français. Tu extrais les informations présentes et tu "
        "les reformules proprement. N'invente jamais de donnée absente : laisse le "
        "champ vide.\n"
        "La source peut être un modèle de marché Excel (type AGIRC-ARRCO : "
        "feuilles CCTP / CRT / AF). Dans ce cas les vraies données sont dans les "
        "cellules renseignées par le prescripteur (Références de la consultation, "
        "Objet de la consultation, Lieu/Site de la prestation, Durée du marché, "
        "Valeur estimée, dates d'envoi / de remise des offres, Contexte du besoin, "
        "Missions, Matrice de compétences). IGNORE les listes de référence et "
        "valeurs de listes déroulantes (catalogues de catégories, d'UO, de sites) : "
        "ce sont des options génériques, pas les données de CET AO. Ne retiens que "
        "la catégorie / l'UO / le site EFFECTIVEMENT sélectionnés pour cette "
        "consultation.\n"
        "Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour ni balises "
        "markdown."
    )
    user = (
        "Génère la fiche AO à partir du contenu source ci-dessous.\n\n"
        "Champs JSON attendus (tous présents, vide si l'info est absente) :\n"
        '- "title": titre TRÈS court (3 à 8 mots) au format "Type de profil — précision". '
        'Il doit nommer le PROFIL recherché (ex. "Tech Lead Data", "Architecte Cloud", '
        '"Chef de projet MOA", "Ingénieur DevOps", "AI Tech Lead"), suivi si utile d\'UNE '
        'précision clé : secteur ou client, longue durée, télétravail. JAMAIS une phrase '
        'complète, JAMAIS l\'objet de la prestation recopié. '
        'Exemples : "Tech Lead Big Data — Assurance, longue mission" · '
        '"Architecte Cloud — secteur bancaire" · "Chef de projet MOA — télétravail partiel"\n'
        '- "client": nom du CLIENT / donneur d\'ordre mentionné dans la source '
        '(ex. "CMA CGM", "AGIRC-ARRCO", "SAFRAN"), tel qu\'il apparaît, sinon "". '
        "C'est le client pour lequel l'AO est ouvert, pas un partenaire/prestataire.\n"
        '- "reference": référence client / de la consultation si présente (ex. "Marché Spécifique n°23915SA230MS"), sinon ""\n'
        '- "description": description claire et professionnelle (3 à 6 phrases), reformulée\n'
        '- "skills_required": compétences techniques clés, séparées par des virgules. '
        "Sur un modèle de marché, déduis-les de la « Matrice de compétences » "
        "(compétences techniques ET fonctionnelles listées, ex. Clarity/OpenWorkBench, "
        "Excel avancé, Power BI, Access, méthode ABC, gestion de projet)\n"
        f'- "ao_type": exactement l\'une de ces valeurs si pertinent, sinon "" : {", ".join(ao_types)}\n'
        '- "budget_max": UNIQUEMENT un taux journalier max en €/jour (TJM), sinon null. '
        '⚠️ Sur un modèle de marché, les montants « Valeur estimée du marché », '
        '« Montant maximum pour la durée du contrat » (ex. 320 000€ HT) sont des '
        'TOTAUX, PAS un TJM : ne les mets JAMAIS dans budget_max (laisse null) et '
        'reporte plutôt cette information dans "context".\n'
        '- "location": localisation / lieu de la prestation (ville, site) — ex. "Marseille". '
        "N'y mets PAS le télétravail (c'est \"work_mode\").\n"
        '- "work_mode": mode de travail, exactement l\'une de ces valeurs sinon "" : '
        '"onsite" (100% sur site), "hybrid" (présentiel + télétravail partiel, ex. '
        '"télétravail possible 2j/semaine", "hybride"), "remote" (100% télétravail). '
        "Déduis-le du texte : « Télétravail possible X j/semaine » ⇒ \"hybrid\".\n"
        '- "duration": durée de la mission (durée du marché ET volumétrie en jours si indiquée)\n'
        '- "deadline": date limite de réponse au format "YYYY-MM-DD" (sur un modèle '
        'de marché : la « Date de limite de remise des offres »), sinon ""\n'
        '- "context": éléments de contexte utiles (secteur, contraintes, urgence, environnement technique)\n'
        '- "langue_requise": si une langue est exigée (ex. "Langue impérative : Anglais courant"), '
        'renvoie-la de façon concise (ex. "Anglais courant"), sinon "".\n'
        '- "importance": objet notant de 0 (exclu) à 5 (critique) l\'importance '
        'RELATIVE de chaque critère DÉDUITE du texte, avec les clés exactes '
        '"competences", "seniorite", "contexte", "points_forts_cv", '
        '"elements_differenciants", "tjm". Ex. : un AO qui insiste sur un '
        '"profil senior expert" met "seniorite" à 5 ; si le TJM est déjà borné '
        'par un TJM max, "tjm" peut être 0. En l\'absence de signal clair, mets 3.\n\n'
        f'Contenu source :\n"""\n{source}\n"""'
    )

    last_err: Optional[Exception] = None
    for c, model, provider in candidates:
        try:
            with record_ai_call(provider=provider.lower(), model=model, route="ao/draft") as _call:
                resp = await c.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.2,
                    max_tokens=1200,
                    extra_body=(ai_ledger.OR_USAGE if provider == "OpenRouter" else {}),
                )
                _u = getattr(resp, "usage", None)
                if _u:
                    _call.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                                output_tokens=getattr(_u, "completion_tokens", None),
                                cost=getattr(_u, "cost", None))
            ai_ledger.record(provider=provider.lower(), model=model, operation="draft",
                             resp=resp, entity_type="ao")
            data = _extract_json(resp.choices[0].message.content or "")
            if data is None:
                continue
            result = _sanitize(data, ao_types)
            # Traçabilité : quel fournisseur/modèle a réellement généré la fiche
            # (utile pour repérer un repli sur le modèle faible).
            result["_ai_provider"] = provider
            result["_ai_model"] = model
            result["_ai_fallback"] = (provider != "OpenRouter")
            return result
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[AO_DRAFTER] {provider} échec ({model}): {e}")

    if last_err is not None:
        raise last_err
    return None


async def summarize_ao(ao: dict) -> Optional[str]:
    """
    Résume un AO en UNE phrase courte (petit modèle, peu coûteux). Pensé pour
    servir de sous-titre/accroche sur la fiche AO. Best-effort : None si le
    client LLM n'est pas configuré ou si la source est vide.
    """
    candidates = _summary_candidates()
    if not candidates:
        return None
    fields = ("title", "ao_type", "skills_required", "description",
              "context", "location", "duration", "budget_max")
    bits = [f"{k}: {ao.get(k)}" for k in fields if ao.get(k)]
    source = "\n".join(bits)[:4000]
    if not source.strip():
        return None
    for c, model, provider in candidates:
        try:
            with record_ai_call(provider=provider.lower(), model=model, route="ao/summary") as _call:
                resp = await c.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": (
                            "Tu résumes un appel d'offres en UNE seule phrase courte (20 mots "
                            "maximum), en français, claire et parlante. Réponds uniquement par "
                            "la phrase, sans préfixe, sans guillemets."
                        )},
                        {"role": "user", "content": source},
                    ],
                    temperature=0.3,
                    max_tokens=80,
                    extra_body=(ai_ledger.OR_USAGE if provider == "OpenRouter" else {}),
                )
                _u = getattr(resp, "usage", None)
                if _u:
                    _call.usage(input_tokens=getattr(_u, "prompt_tokens", None),
                                output_tokens=getattr(_u, "completion_tokens", None),
                                cost=getattr(_u, "cost", None))
            ai_ledger.record(provider=provider.lower(), model=model, operation="summary",
                             resp=resp, entity_type="ao")
            txt = (resp.choices[0].message.content or "").strip().strip('"').strip()
            return txt[:240] or None
        except Exception as e:  # noqa: BLE001
            print(f"[AO_DRAFTER] résumé {provider} échec ({model}): {e}")
    return None
