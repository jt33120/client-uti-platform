"""
Templates d'emails éditables (Administration → Templates Mails).

Lecture : staff UTI. Écriture/réinitialisation : admin uniquement.
Toute modification est journalisée (audit_log).
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel, Field, EmailStr

from services import email_templates, audit, storage
from services.email import send_email
from services.supabase_client import supabase
from routers.auth import require_staff, require_admin

router = APIRouter(prefix="/email-templates", tags=["email-templates"])

# Images insérées dans les templates → bucket public dédié.
_IMG_BUCKET = "email-assets"
_MAX_IMG_BYTES = 5 * 1024 * 1024  # 5 Mo
_IMG_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    # SVG retiré : il peut embarquer du JavaScript → XSS stocké dans un
    # bucket public. Les formats raster suffisent pour un logo d'e-mail.
    "image/gif": "gif", "image/webp": "webp",
}


# Valeurs d'exemple pour l'aperçu (rendu via la MÊME coquille que l'envoi réel).
_PREVIEW_SAMPLE = {
    "title": "Tech Lead Big Data",
    "client": "AGIRC SAD",
    "reference": "AO-2026-014",
    "location": "Paris / hybride",
    "deadline": "2026-07-15",
    "link": "https://plateforme.groupement-it.com/aos/exemple",
    "name": "Marie",
    "role": "la plateforme partenaires Groupement-IT",
}


@router.get("")
async def list_templates(user: dict = Depends(require_staff)):
    """Liste des templates (valeur effective + défaut + variables disponibles)."""
    return {"templates": email_templates.get_all()}


class PreviewRequest(BaseModel):
    key: str
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=50000)


@router.post("/preview")
async def preview_template(req: PreviewRequest, user: dict = Depends(require_staff)):
    """Rend l'email complet (coquille de marque) avec des valeurs d'exemple.

    Utilise build_email — la même fonction que l'envoi réel — donc l'aperçu est
    fidèle au mail effectivement reçu.
    """
    if req.key not in email_templates.DEFAULTS:
        raise HTTPException(status_code=404, detail="Template inconnu")
    subject, html, _ = email_templates.build_email(
        req.key, _PREVIEW_SAMPLE, subject=req.subject, body=req.body
    )
    return {"subject": subject, "html": html}


class TestSendRequest(BaseModel):
    key: str
    to: EmailStr
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=50000)


@router.post("/send-test")
async def send_test(req: TestSendRequest, user: dict = Depends(require_admin)):
    """Envoie un email de test (contenu en cours d'édition, valeurs d'exemple).

    Rendu via build_email — donc strictement identique à l'envoi réel.
    """
    if req.key not in email_templates.DEFAULTS:
        raise HTTPException(status_code=404, detail="Template inconnu")
    subject, html, text = email_templates.build_email(
        req.key, _PREVIEW_SAMPLE, subject=req.subject, body=req.body
    )
    subject = f"[TEST] {subject}"
    ok, err = send_email(req.to, subject, html, text=text)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Échec de l'envoi : {err}")
    audit.log_event(
        "email_template_test", audit.new_run_id(),
        actor_id=user["sub"], payload={"key": req.key, "to": req.to},
    )
    return {"message": f"Email de test envoyé à {req.to}."}


class TemplateUpdate(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=50000)
    format: str = Field(default="html")


@router.put("/{key}")
async def update_template(key: str, body: TemplateUpdate, user: dict = Depends(require_admin)):
    """Enregistre le sujet + corps personnalisés d'un template (admin)."""
    if key not in email_templates.DEFAULTS:
        raise HTTPException(status_code=404, detail="Template inconnu")
    fmt = body.format if body.format in ("html", "text") else "html"
    base = {
        "key": key,
        "subject": body.subject,
        "body": body.body,
        "updated_by": user["sub"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        # La colonne `format` peut ne pas exister (migration non appliquée) :
        # on tente avec, puis on retombe sans pour rester fonctionnel.
        try:
            supabase.table("email_templates").upsert(
                {**base, "format": fmt}, on_conflict="key"
            ).execute()
        except Exception:
            supabase.table("email_templates").upsert(base, on_conflict="key").execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur enregistrement: {e}")
    audit.log_event(
        "email_template_change", audit.new_run_id(),
        actor_id=user["sub"], payload={"key": key},
    )
    return {"message": "Template enregistré.", "key": key}


@router.delete("/{key}")
async def reset_template(key: str, user: dict = Depends(require_admin)):
    """Réinitialise un template à sa valeur par défaut (supprime la ligne stockée)."""
    if key not in email_templates.DEFAULTS:
        raise HTTPException(status_code=404, detail="Template inconnu")
    try:
        supabase.table("email_templates").delete().eq("key", key).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur réinitialisation: {e}")
    audit.log_event(
        "email_template_reset", audit.new_run_id(),
        actor_id=user["sub"], payload={"key": key},
    )
    return {"message": "Template réinitialisé.", "key": key}


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...), user: dict = Depends(require_admin)):
    """Héberge une image utilisée dans un template et renvoie son URL publique."""
    ext = _IMG_EXT.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(status_code=422, detail="Format image non supporté (png, jpg, gif, webp).")
    content = await file.read()
    if len(content) > _MAX_IMG_BYTES:
        raise HTTPException(status_code=413, detail="Image trop lourde (max 5 Mo).")
    try:
        storage.ensure_bucket(_IMG_BUCKET, public=True)
        path = f"{datetime.now(timezone.utc):%Y/%m}/{uuid.uuid4().hex}.{ext}"
        url = storage.upload(_IMG_BUCKET, path, content, file.content_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Échec de l'envoi de l'image: {e}")
    return {"url": url}
