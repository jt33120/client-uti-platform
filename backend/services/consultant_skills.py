"""Extraction des compétences d'un consultant depuis son CV.

Le CV vit sur les soumissions (cv_text). On réutilise l'extracteur du matching
(`extract_features`, OpenRouter → Mistral) pour en déduire une liste de
compétences, puis on la **fusionne** dans `consultant.skills` sans jamais
écraser une saisie manuelle.
"""
from typing import Optional
from services.supabase_client import supabase
from services.ai_matching import extract_features


def _merge_skills(existing: Optional[str], new: list) -> str:
    """Fusionne existant + nouvelles compétences, dédoublonné (insensible à la
    casse), en gardant l'ordre : existant d'abord, puis les nouveautés du CV."""
    out, seen = [], set()
    for s in [p.strip() for p in (existing or "").split(",")] + list(new or []):
        s = (s or "").strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return ", ".join(out)


async def extract_and_store_skills(consultant_id: str, *, only_if_empty: bool = False) -> str:
    """Déduit les compétences du CV le plus récent du consultant et les fusionne
    dans `consultant.skills`. Renvoie la chaîne finale.

    - only_if_empty : ne fait rien si des compétences sont déjà présentes
      (utilisé par le hook automatique à la soumission).
    """
    c = supabase.table("consultants").select("id, skills").eq(
        "id", consultant_id
    ).single().execute().data
    if not c:
        raise ValueError("Consultant introuvable")
    if only_if_empty and (c.get("skills") or "").strip():
        return c.get("skills") or ""

    subs = supabase.table("submissions").select("cv_text, submitted_at").eq(
        "consultant_id", consultant_id
    ).order("submitted_at", desc=True).limit(5).execute().data or []
    cv_text = next((s["cv_text"] for s in subs if s.get("cv_text")), None)
    if not cv_text:
        raise ValueError("Aucun CV exploitable pour ce consultant.")

    features, _cost = await extract_features(cv_text)
    merged = _merge_skills(c.get("skills"), features.get("skills") or [])
    supabase.table("consultants").update({"skills": merged}).eq("id", consultant_id).execute()
    return merged


async def auto_extract_skills(consultant_id: str) -> None:
    """Version best-effort pour BackgroundTasks : n'extrait que si le champ est
    vide et n'échoue jamais bruyamment (ne doit pas casser la soumission)."""
    try:
        await extract_and_store_skills(consultant_id, only_if_empty=True)
    except Exception as e:  # noqa: BLE001
        print(f"[SKILLS] auto-extraction ignorée (consultant {consultant_id}): {e}")
