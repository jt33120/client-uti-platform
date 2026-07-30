import re
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Optional, Literal
from services.supabase_client import supabase
from services.email import send_email, render_email_html
from services import email_templates
from routers.auth import require_admin
from config import settings

router = APIRouter(prefix="/invitations", tags=["invitations"])


class CreateInviteRequest(BaseModel):
    email: EmailStr
    name: str  # Display name set by admin
    role: Literal["ao", "commerce"] = "ao"  # partner (default) or sales
    # Commercial entity (only meaningful when role == 'commerce').
    # None / 'uti' → Commercial UTI ; 'groupement-it' → Commercial Groupement-IT.
    org: Optional[Literal["uti", "groupement-it"]] = None


def _is_expired(expires_at: str) -> bool:
    dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    return dt < datetime.now(timezone.utc)


_GENERIC_MAILBOX = {
    "contact", "info", "hello", "bonjour", "admin", "rh", "hr",
    "team", "support", "sales", "commercial", "noreply", "no-reply",
}


def _greeting_name(name: str) -> str:
    """
    Confident first name for the greeting / register prefill. If the admin typed
    an email as the display name (e.g. 'christelle.pelouin@grp-it.com'), derive a
    capitalised first name ('Christelle'). Returns '' when we CAN'T be confident
    (generic mailbox like contact@, non-alphabetic or too-short local part) —
    callers then fall back to a plain 'Bonjour,' rather than greeting garbage.
    """
    n = (name or "").strip()
    if not n:
        return ""
    if "@" in n:
        candidate = re.split(r"[._\-+]", n.split("@", 1)[0])[0]
        if len(candidate) < 2 or not candidate.isalpha() or candidate.lower() in _GENERIC_MAILBOX:
            return ""
        return candidate.capitalize()
    return n


def _send_invite_email(to_email: str, partner_name: str, invite_url: str, role: str = "ao") -> tuple[bool, Optional[str]]:
    """
    Send the invitation email via SMTP.
    Returns (success, error_message). Never raises — caller decides what to do.
    """
    first = _greeting_name(partner_name)
    role_label = "l'équipe commerciale Groupement-IT" if role == "commerce" else "la plateforme partenaires Groupement-IT"
    # Sujet + corps + coquille via la source unique (= aperçu admin fidèle).
    context = {"name": first, "role": role_label, "link": invite_url}
    subject, html, text = email_templates.build_email("invite", context)
    # Via la file : une invitation perdue sur un hoquet SMTP, c'est un partenaire
    # qui n'arrive jamais et personne qui s'en aperçoit. La file réessaie et
    # garde la trace, ce que l'envoi direct ne faisait ni l'un ni l'autre.
    from services import email_outbox
    row = email_outbox.enqueue(
        to_email=to_email, to_name=partner_name or None,
        subject=subject, html=html, text=text,
        category="invitation", template_key="invite",
    )
    return (row is not None), None if row else "Dépôt en file impossible"


@router.post("")
async def create_invitation(body: CreateInviteRequest, user: dict = Depends(require_admin)):
    """
    Create a single-use, time-limited invitation link for a partner (role='ao')
    or a UTI sales account (role='commerce'). The admin sets both the email and
    the display name. A pending unused invite for this email is replaced.
    """
    name = body.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Le nom doit contenir au moins 2 caractères.")
    # Normalise an email accidentally typed as the display name into a clean
    # first name, so it shows correctly everywhere (email greeting, the register
    # prefill, and the admin "pending invitations" chip). Keep the original input
    # when we can't confidently extract a name (helper returns '').
    name = _greeting_name(name) or name

    # Revoke any existing unused invites for this email
    supabase.table("invitations").delete() \
        .eq("email", body.email).is_("used_at", "null").execute()

    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    # Only carry an org for sales accounts; normalise UTI → None.
    org = body.org if (body.role == "commerce" and body.org == "groupement-it") else None
    record = {
        "token": token,
        "email": body.email,
        "name": name,
        "role": body.role,
        "invited_by": user["sub"],
        "expires_at": expires_at,
        "org": org,
    }
    try:
        supabase.table("invitations").insert(record).execute()
    except Exception:
        # 'org' column not migrated yet — degrade gracefully.
        record.pop("org", None)
        supabase.table("invitations").insert(record).execute()

    invite_url = f"{settings.frontend_url}/register?invite={token}"

    # Best-effort email send. Failure is non-blocking — admin can still copy the link.
    email_sent, email_error = _send_invite_email(body.email, name, invite_url, body.role)
    if not email_sent:
        print(f"[INVITATIONS] Email not sent to {body.email}: {email_error}")

    return {
        "url": invite_url,
        "email": body.email,
        "name": name,
        "expires_in_days": 7,
        "email_sent": email_sent,
        "email_error": email_error,
    }


class ResendInviteRequest(BaseModel):
    token: str


@router.post("/resend")
async def resend_invitation(body: ResendInviteRequest, user: dict = Depends(require_admin)):
    """Re-send an existing invite email (e.g. partner lost it). Does not regenerate the token."""
    try:
        result = supabase.table("invitations").select("*").eq("token", body.token).single().execute()
    except Exception:
        raise HTTPException(status_code=404, detail="Invitation introuvable")

    inv = result.data
    if inv.get("used_at"):
        raise HTTPException(status_code=410, detail="Cette invitation a déjà été utilisée")
    if _is_expired(inv["expires_at"]):
        raise HTTPException(status_code=410, detail="Cette invitation a expiré")

    invite_url = f"{settings.frontend_url}/register?invite={body.token}"
    email_sent, email_error = _send_invite_email(inv["email"], inv.get("name", ""), invite_url, inv.get("role", "ao"))

    if not email_sent:
        raise HTTPException(status_code=502, detail=f"Échec d'envoi: {email_error}")

    return {"email_sent": True, "email": inv["email"]}


@router.get("/validate/{token}")
async def validate_invitation(token: str):
    """
    Public endpoint: validate an invite token.
    Returns invite metadata (email, name, role) if valid; 400/410 otherwise.
    """
    try:
        result = supabase.table("invitations").select("*").eq("token", token).single().execute()
    except Exception:
        raise HTTPException(status_code=400, detail="Lien d'invitation invalide.")

    inv = result.data
    if not inv:
        raise HTTPException(status_code=400, detail="Lien d'invitation invalide.")
    if inv.get("used_at"):
        raise HTTPException(status_code=410, detail="Ce lien d'invitation a déjà été utilisé.")
    if _is_expired(inv["expires_at"]):
        raise HTTPException(status_code=410, detail="Ce lien d'invitation a expiré.")

    return {"email": inv["email"], "name": inv.get("name", ""), "role": inv["role"], "valid": True}
