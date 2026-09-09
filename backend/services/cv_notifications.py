"""Notifications e-mail du cycle de vie « Validation CV » (demande Sullyvan).

Émet les notifications aux partenaires porteurs (retenu / non retenu / envoyé
client / échange commercial / affaire gagnée-perdue) et l'envoi réel du CV au
client. Best-effort : un échec d'email ne casse jamais la mise à jour d'état.
"""
from typing import Optional
from services.supabase_client import supabase
from services import email_optout, email_templates, storage
from services.email import send_email
from config import settings


def _ao_context(ao_id: str) -> dict:
    try:
        ao = supabase.table("appels_offres").select(
            "id, title, reference, client_id, clients(name)"
        ).eq("id", ao_id).single().execute().data or {}
    except Exception:
        ao = {}
    client = ao.get("clients") if isinstance(ao.get("clients"), dict) else {}
    return {
        "title": ao.get("title") or "Appel d'offres",
        "reference": ao.get("reference") or "",
        "client": (client or {}).get("name") or "",
        "client_id": ao.get("client_id"),
        "link": f"{settings.frontend_url.rstrip('/')}/aos/{ao_id}",
    }


def _latest_submission(ao_id: str, consultant_id: str) -> Optional[dict]:
    try:
        rows = supabase.table("submissions").select(
            "id, cv_url, submitted_by, consultants(name), "
            "submitter:profiles!submitted_by(id, name, email)"
        ).eq("ao_id", ao_id).eq("consultant_id", consultant_id).order(
            "submitted_at", desc=True
        ).limit(1).execute().data or []
        return rows[0] if rows else None
    except Exception:
        return None


def _log_email(ao_id, recipient_email, recipient_id, kind, status, error, sent_by):
    """Trace un envoi dans partner_email_log (best-effort — même journal que les
    notifications d'AO, pour que les emails « Validation CV » soient visibles)."""
    try:
        supabase.table("partner_email_log").insert({
            "ao_id": ao_id,
            "recipient_id": recipient_id,
            "recipient_email": recipient_email,
            "kind": kind,
            "status": status,
            "error": (error or None),
            "sent_by": sent_by,
        }).execute()
    except Exception as e:  # noqa: BLE001
        print(f"[CV_NOTIF] log non écrit (AO {ao_id}): {e}")


def notify_partner(ao_id: str, consultant_id: str, key: str,
                   sent_by: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Envoie une notification au partenaire porteur du CV pour cet AO, et la
    journalise dans partner_email_log."""
    sub = _latest_submission(ao_id, consultant_id)
    if not sub:
        return False, "Soumission introuvable"
    partner = sub.get("submitter") or {}
    to = partner.get("email")
    if not to:
        return False, "Email du partenaire introuvable"
    ctx = _ao_context(ao_id)
    consultant = (sub.get("consultants") or {}).get("name") or "le consultant"
    context = {**ctx, "consultant": consultant, "partner": partner.get("name") or ""}
    # Désabonné du suivi de ses CV : on n'envoie pas, et ce n'est pas un échec.
    # Ce point d'envoi est direct (pas via la file), donc le filtre posé dans
    # email_outbox.enqueue ne le couvre pas — il est répété ici.
    # Le saut est journalisé : `partner_email_log` sert à répondre à « pourquoi
    # ce partenaire n'a-t-il rien reçu ? », et un silence non tracé est
    # exactement la question qu'on ne saura pas trancher trois semaines plus tard.
    if email_optout.is_blocked(to, key):
        _log_email(ao_id, to, partner.get("id"), key, "skipped",
                   "destinataire désabonné de ce type de notification", sent_by)
        return True, None
    subject, html, text = email_templates.build_email(key, context, recipient=to)
    ok, err = send_email(to, subject, html, text=text,
                         unsubscribe_url=email_optout.unsubscribe_url(to, key))
    _log_email(ao_id, to, partner.get("id"), key,
               "sent" if ok else "failed", err, sent_by)
    return ok, err


# Événement (transition d'état) → clé de template partenaire.
EVENT_TEMPLATE = {
    "retenu": "cv_retenu",
    "non_retenu": "cv_non_retenu",
    "envoye_client": "cv_envoye_client",
    "echange_commercial": "echange_commercial",
    "gagnee": "affaire_gagnee",
    "perdue": "affaire_perdue",
}


def notify_event(ao_id: str, consultant_id: str, event: str,
                 sent_by: Optional[str] = None) -> tuple[bool, Optional[str]]:
    key = EVENT_TEMPLATE.get(event)
    if not key:
        return False, f"Événement inconnu: {event}"
    try:
        return notify_partner(ao_id, consultant_id, key, sent_by=sent_by)
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def send_cv_to_client(ao_id: str, consultant_id: str, to_email: str,
                      message: Optional[str] = None,
                      sent_by: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Envoi RÉEL du CV au client (lien sécurisé) + notification du partenaire."""
    sub = _latest_submission(ao_id, consultant_id)
    if not sub:
        return False, "Soumission introuvable"
    cv_link = None
    if sub.get("cv_url"):
        try:
            cv_link = storage.signed_cv_url(sub["cv_url"], expires_in=7 * 24 * 3600)
        except Exception:
            cv_link = None
    if not cv_link:
        return False, "CV introuvable pour ce consultant"

    ctx = _ao_context(ao_id)
    context = {**ctx, "link": cv_link, "message": (message or "").strip()}
    subject, html, text = email_templates.build_email("cv_client", context,
                                                      recipient=to_email)
    ok, err = send_email(to_email, subject, html, text=text)
    _log_email(ao_id, to_email, None, "cv_client",
               "sent" if ok else "failed", err, sent_by)
    if ok:
        # Informe le partenaire que son CV a été transmis au client (best-effort).
        try:
            notify_partner(ao_id, consultant_id, "cv_envoye_client", sent_by=sent_by)
        except Exception:
            pass
    return ok, err
