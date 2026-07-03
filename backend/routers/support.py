from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Literal, Optional
from routers.auth import get_current_user
from services.supabase_client import supabase
from services.email import send_email, render_email_html
from services.ratelimit import rate_limit, rate_limit_public
from config import settings
import html as _html

router = APIRouter(prefix="/support", tags=["support"])

# Destination des demandes de contact publiques (futurs partenaires).
PARTNER_CONTACT_EMAIL = "contact@groupement-it.com"

_TYPES = Literal["bug", "question", "suggestion", "other"]
_TYPE_LABELS = {
    "bug": "Problème technique",
    "question": "Question",
    "suggestion": "Suggestion",
    "other": "Autre",
}


class ContactRequest(BaseModel):
    type: _TYPES
    subject: str
    message: str


def _send_support_email(
    from_name: str,
    from_email: str,
    type_label: str,
    subject: str,
    message: str,
) -> tuple[bool, Optional[str]]:
    """Forward contact message to the admin via SMTP. Never raises."""
    if not settings.admin_email:
        return False, "ADMIN_EMAIL non configurée"

    # Contenu fourni par l'utilisateur → échappé (même règle que partner_request) :
    # sans cela, un compte authentifié injecte du HTML dans la boîte de l'admin.
    import html as _html
    e_name, e_subject, e_message = _html.escape(from_name), _html.escape(subject), _html.escape(message)
    body_html = (
        '<table cellpadding="0" cellspacing="0" style="width:100%;font-size:13px;color:#1d1d1f;margin-bottom:12px;">'
        f'<tr><td style="padding:4px 0;color:#6e6e73;width:90px;">De</td><td>{e_name} &lt;{_html.escape(from_email)}&gt;</td></tr>'
        f'<tr><td style="padding:4px 0;color:#6e6e73;">Sujet</td><td>{e_subject}</td></tr>'
        f'<tr><td style="padding:4px 0;color:#6e6e73;">Type</td><td>{type_label}</td></tr>'
        '</table>'
        f'<div style="background:#f5f5f7;border-radius:8px;padding:16px;font-size:14px;line-height:1.6;color:#1d1d1f;white-space:pre-wrap;">{e_message}</div>'
    )
    html = render_email_html(
        title=f"Nouveau message : {type_label}",
        body_html=body_html,
        footer_note=f"Vous pouvez répondre directement à {from_email}",
    )

    text = (
        f"Nouveau message de support : {type_label}\n\n"
        f"De : {from_name} <{from_email}>\n"
        f"Sujet : {subject}\n"
        f"Type : {type_label}\n\n"
        f"{message}\n\n"
        f"Vous pouvez répondre directement à {from_email}"
    )
    return send_email(
        settings.admin_email,
        f"[Support] {type_label} : {subject}",
        html,
        text=text,
        reply_to=from_email,
    )


@router.post("/contact", dependencies=[Depends(rate_limit(5, 300))])
async def contact(body: ContactRequest, user: dict = Depends(get_current_user)):
    if not body.subject.strip():
        raise HTTPException(status_code=422, detail="Le sujet est requis.")
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="Le message est requis.")
    if len(body.message.strip()) < 10:
        raise HTTPException(status_code=422, detail="Le message est trop court (minimum 10 caractères).")

    user_id = user["sub"]
    user_email = user["email"]

    # Fetch name from profiles
    try:
        profile = supabase.table("profiles").select("name").eq("id", user_id).single().execute()
        from_name = profile.data.get("name", user_email)
    except Exception:
        from_name = user_email

    # Persist in DB (service role bypasses RLS)
    try:
        supabase.table("support_messages").insert({
            "user_id": user_id,
            "from_name": from_name,
            "from_email": user_email,
            "type": body.type,
            "subject": body.subject.strip(),
            "message": body.message.strip(),
        }).execute()
    except Exception as e:
        print(f"[SUPPORT] DB insert failed: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de l'enregistrement du message.")

    # Send email notification to admin
    type_label = _TYPE_LABELS.get(body.type, body.type)
    ok, err = _send_support_email(from_name, user_email, type_label, body.subject.strip(), body.message.strip())
    if not ok:
        print(f"[SUPPORT] Email non envoyé : {err}")
    else:
        print(f"[SUPPORT] Email transmis à {settings.admin_email}")

    return {"message": "Message envoyé avec succès."}


class PartnerRequest(BaseModel):
    nom: str
    prenom: str
    email: str
    phone: Optional[str] = None
    company: str
    siret: Optional[str] = None
    message: Optional[str] = None


@router.post("/partner-request", dependencies=[Depends(rate_limit_public(3, 300))])
async def partner_request(body: PartnerRequest):
    """Formulaire de contact PUBLIC (futurs partenaires) → email à l'équipe.
    Aucune authentification ; limité par IP."""
    nom = (body.nom or "").strip()
    prenom = (body.prenom or "").strip()
    email = (body.email or "").strip()
    company = (body.company or "").strip()
    if not nom or not prenom:
        raise HTTPException(status_code=422, detail="Nom et prénom sont requis.")
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    if not company:
        raise HTTPException(status_code=422, detail="Le nom de la société est requis.")

    def row(label, value):
        return (f'<tr><td style="padding:4px 0;color:#6e6e73;width:130px;">{_html.escape(label)}</td>'
                f'<td style="color:#1d1d1f;">{_html.escape(value or "—")}</td></tr>')

    body_html = (
        '<table cellpadding="0" cellspacing="0" style="width:100%;font-size:13px;margin-bottom:12px;">'
        + row("Nom", nom) + row("Prénom", prenom) + row("Email", email)
        + row("Téléphone", body.phone) + row("Société", company) + row("SIRET", body.siret)
        + '</table>'
    )
    if (body.message or "").strip():
        body_html += (f'<div style="background:#f5f5f7;border-radius:8px;padding:16px;font-size:14px;'
                      f'line-height:1.6;color:#1d1d1f;white-space:pre-wrap;">{_html.escape(body.message.strip())}</div>')

    html = render_email_html(
        title="Nouvelle demande de partenariat",
        body_html=body_html,
        footer_note=f"Répondez directement à {email}",
    )
    text = (
        "Nouvelle demande de partenariat\n\n"
        f"Nom : {nom}\nPrénom : {prenom}\nEmail : {email}\n"
        f"Téléphone : {body.phone or '—'}\nSociété : {company}\nSIRET : {body.siret or '—'}\n\n"
        f"{(body.message or '').strip()}\n"
    )
    ok, err = send_email(
        PARTNER_CONTACT_EMAIL,
        f"[Contact partenaire] {company} — {prenom} {nom}",
        html, text=text, reply_to=email,
    )
    if not ok:
        raise HTTPException(status_code=502, detail=f"Échec de l'envoi : {err}")
    return {"message": "Votre demande a bien été envoyée. Nous vous recontacterons rapidement."}
