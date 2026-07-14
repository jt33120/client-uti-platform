from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from services.supabase_client import supabase
from services import cv_harmonizer
from routers.auth import require_staff

router = APIRouter(prefix="/cv", tags=["cv"])


class HarmonizeRequest(BaseModel):
    submission_id: Optional[str] = None
    consultant_id: Optional[str] = None
    lang: str = "fr"  # 'fr' | 'en'


def _cv_text_for(body: HarmonizeRequest) -> Optional[str]:
    if body.submission_id:
        try:
            s = supabase.table("submissions").select("cv_text").eq(
                "id", body.submission_id
            ).single().execute().data
            if s and s.get("cv_text"):
                return s["cv_text"]
        except Exception:
            pass
    if body.consultant_id:
        try:
            rows = supabase.table("submissions").select("cv_text, submitted_at").eq(
                "consultant_id", body.consultant_id
            ).order("submitted_at", desc=True).limit(5).execute().data or []
            return next((r["cv_text"] for r in rows if r.get("cv_text")), None)
        except Exception:
            return None
    return None


@router.post("/harmonize")
async def harmonize(body: HarmonizeRequest, user: dict = Depends(require_staff)):
    """Régénère un CV au format standard Groupement-IT (JSON structuré, FR ou EN)."""
    if body.lang not in ("fr", "en"):
        raise HTTPException(status_code=422, detail="lang doit être 'fr' ou 'en'.")
    # Cohérence : un CV structuré déjà stocké (potentiellement enrichi par la vision —
    # niveaux d'étoiles, etc.) est la source de vérité en FR. On le renvoie tel quel
    # plutôt que d'en régénérer un « texte seul » divergent de la vue « CV analysé ».
    if body.submission_id and body.lang == "fr":
        from services.cv_structured import get_structured
        got = get_structured(body.submission_id)
        if got:
            return {"cv": got, "lang": "fr"}
    if not cv_harmonizer.is_available():
        raise HTTPException(status_code=503, detail="Service IA indisponible (clé LLM manquante).")

    cv_text = _cv_text_for(body)
    if not cv_text or len(cv_text.strip()) < 50:
        raise HTTPException(status_code=422, detail="Aucun texte de CV exploitable trouvé.")

    try:
        cv = await cv_harmonizer.harmonize_cv(cv_text, body.lang)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "User not found" in msg or "invalid api key" in msg.lower():
            raise HTTPException(status_code=502, detail="Le fournisseur d'IA a refusé la requête (clé API invalide).")
        raise HTTPException(status_code=502, detail=f"Échec de la génération : {msg}")
    if not cv:
        raise HTTPException(status_code=502, detail="La régénération n'a produit aucun résultat exploitable.")

    return {"cv": cv, "lang": body.lang}
