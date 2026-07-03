"""
Décisions humaines sur les résultats de matching (AI Act Art. 14 — supervision).

Matérialise l'override : un opérateur habilité retient/écarte un profil
indépendamment du rang IA, avec justification OBLIGATOIRE en cas de divergence.
Chaque décision est aussi journalisée (audit_log). Voir
compliance/ai-act/phase-3-technique/04-spec-supervision-humaine.md.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from services.supabase_client import supabase
from services import audit
from routers.auth import require_staff

router = APIRouter(prefix="/decisions", tags=["decisions"])

VALID_DECISIONS = ("retained", "rejected", "overridden")


class DecisionRequest(BaseModel):
    ao_id: str
    submission_id: Optional[str] = None
    consultant_id: Optional[str] = None
    ai_rank: Optional[int] = None
    ai_score: Optional[int] = None
    decision: str
    justification: Optional[str] = None


@router.post("")
async def record_decision(body: DecisionRequest, user: dict = Depends(require_staff)):
    """Enregistre une décision humaine. Justification requise si override."""
    if body.decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"decision doit être l'une de {VALID_DECISIONS}",
        )
    if body.decision == "overridden" and not (body.justification or "").strip():
        raise HTTPException(
            status_code=422,
            detail="Une justification est obligatoire lorsque l'opérateur s'écarte "
                   "de la recommandation (override).",
        )

    try:
        row = supabase.table("human_decision").insert({
            "ao_id": body.ao_id,
            "submission_id": body.submission_id,
            "consultant_id": body.consultant_id,
            "ai_rank": body.ai_rank,
            "ai_score": body.ai_score,
            "decision": body.decision,
            "justification": body.justification,
            "decided_by": user["sub"],
        }).execute().data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur enregistrement décision: {e}")

    audit.log_event(
        "override", audit.new_run_id(),
        ao_id=body.ao_id, actor_id=user["sub"],
        payload={
            "submission_id": body.submission_id,
            "consultant_id": body.consultant_id,
            "decision": body.decision,
            "ai_rank": body.ai_rank,
            "ai_score": body.ai_score,
            "has_justification": bool((body.justification or "").strip()),
        },
    )
    return row


@router.get("/ao/{ao_id}")
async def list_decisions(ao_id: str, user: dict = Depends(require_staff)):
    """Liste les décisions humaines enregistrées pour un AO (staff UTI)."""
    try:
        return supabase.table("human_decision").select("*").eq(
            "ao_id", ao_id
        ).order("decided_at", desc=True).execute().data or []
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise
