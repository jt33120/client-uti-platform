"""
Notifications partenaires d'un appel d'offres + relances.

Envoi MANUEL déclenché par le commercial : liste 1 immédiatement, liste 2 après
un délai configurable. Un planificateur (services.scheduler) envoie la liste 2 à
échéance et gère les relances automatiques. Tout est best-effort : un échec
d'email n'interrompt jamais le flux.
"""
from typing import Optional
from datetime import datetime, timezone

from services.supabase_client import supabase
from services import email_templates, email_outbox
from config import settings


def _client_name(ao: dict) -> str:
    c = ao.get("clients")
    if isinstance(c, dict) and c.get("name"):
        return c["name"]
    try:
        row = supabase.table("clients").select("name").eq("id", ao["client_id"]).single().execute().data
        return (row or {}).get("name") or "—"
    except Exception:
        return "—"


def _emails_for_tiers(client_id: str, tiers: list[str]) -> list[dict]:
    """Partenaires (id, email, name) ayant accès au client pour les tiers donnés."""
    try:
        access = supabase.table("partner_clients").select("partner_id").eq(
            "client_id", client_id
        ).in_("tier", tiers).execute().data or []
    except Exception:
        return []
    ids = list({a["partner_id"] for a in access if a.get("partner_id")})
    if not ids:
        return []
    try:
        profiles = supabase.table("profiles").select("id, email, name").in_("id", ids).execute().data or []
    except Exception:
        return []
    return [p for p in profiles if p.get("email")]


def _partner_ids_with_submission(ao_id: str) -> set:
    """Partenaires ayant déjà soumis un CV sur cet AO (pour ne pas les relancer)."""
    try:
        subs = supabase.table("submissions").select("submitted_by").eq("ao_id", ao_id).execute().data or []
        return {s["submitted_by"] for s in subs if s.get("submitted_by")}
    except Exception:
        return set()


def _render(ao: dict, client_name: str, kind: str, recipient: dict | None = None) -> tuple[str, str, str]:
    """Construit (subject, html, text) de l'email AO. kind = 'new' | 'relance'.

    Sujet et corps proviennent des templates éditables (Administration →
    Templates Mails), avec repli sur les valeurs par défaut.
    """
    url = f"{settings.frontend_url.rstrip('/')}/aos/{ao['id']}"
    key = "ao_relance" if kind == "relance" else "ao_new"
    # Prénom seul : « Bonjour Marc, » sonne juste, « Bonjour Marc Dupont, »
    # sonne comme un publipostage — ce que c'est, mais autant ne pas l'annoncer.
    first = ((recipient or {}).get("name") or "").strip().split(" ")[0]
    context = {
        "title": ao.get("title") or "Appel d'offres",
        "client": client_name,
        "reference": ao.get("reference") or "",
        "location": ao.get("location") or "",
        "deadline": ao.get("deadline") or "",
        "link": url,
        "partner_name": (recipient or {}).get("name") or "",
        "greeting": f"Bonjour {first}," if first else "Bonjour,",
    }
    # Source unique de rendu (identique à l'aperçu admin).
    return email_templates.build_email(key, context)


# `_log_send` a disparu : la file (`email_outbox`) EST désormais le journal.
# Écrire dans `partner_email_log` en plus produirait deux vérités divergentes —
# le journal disait « envoyé » au moment de l'appel SMTP, alors que la file
# connaît le vrai état final (envoyé, en attente de réessai, ou abandonné).
# Les lignes historiques de `partner_email_log` restent lisibles : l'endpoint
# de consultation fusionne les deux sources.


def _send_to(recipients: list[dict], ao: dict, client_name: str, kind: str, actor_id=None) -> int:
    """Dépose la campagne en file d'envoi. Retourne le nombre d'emails mis en file.

    Deux changements par rapport à l'envoi direct d'avant :

    Le rendu est fait DANS la boucle, donc par destinataire. Auparavant il était
    calculé une seule fois avant la boucle et tout le monde recevait le même
    corps au mot près — impossible de nommer le partenaire.

    Et on dépose au lieu d'envoyer : plus rien ne se perd sur un hoquet SMTP, et
    l'appelant (souvent une requête HTTP) rend la main immédiatement.
    """
    if not recipients:
        return 0
    key_base = "ao_relance" if kind == "relance" else "ao_new"
    # Le n° de relance fait partie de la clé : la 2e relance sur le même AO doit
    # bien partir, alors que le REJEU de la 1re ne doit pas.
    round_no = ao.get("relance_count") or 0
    queued = 0
    for r in recipients:
        subject, html, text = _render(ao, client_name, kind, recipient=r)
        row = email_outbox.enqueue(
            to_email=r["email"],
            to_name=r.get("name"),
            subject=subject, html=html, text=text,
            category=f"ao_{kind}",
            template_key=key_base,
            ao_id=ao.get("id"),
            recipient_id=r.get("id"),
            created_by=actor_id,
            idempotency_key=f"ao:{ao.get('id')}:{kind}:{r.get('id')}:{round_no}",
        )
        if row:
            queued += 1
    return queued


def notify_tier(ao: dict, tier: str, actor_id=None) -> int:
    """Envoie la notification d'ouverture aux partenaires d'un tier (list_1/list_2)."""
    client_name = _client_name(ao)
    recipients = _emails_for_tiers(ao["client_id"], [tier])
    return _send_to(recipients, ao, client_name, tier, actor_id)


def relance(ao: dict, only_pending: bool = True, actor_id=None) -> int:
    """
    Relance les partenaires (liste 1 + liste 2). Par défaut, uniquement ceux qui
    n'ont pas encore soumis de CV.
    """
    client_name = _client_name(ao)
    recipients = _emails_for_tiers(ao["client_id"], ["list_1", "list_2"])
    if only_pending:
        done = _partner_ids_with_submission(ao["id"])
        recipients = [r for r in recipients if r["id"] not in done]
    return _send_to(recipients, ao, client_name, "relance", actor_id)


def eligible_partners(ao: dict) -> list[dict]:
    """
    Partenaires (liste 1 / liste 2) du client de l'AO, avec leur tier, s'ils ont
    déjà soumis un CV, et si leur compte est bloqué. Sert au renvoi ciblé.
    """
    try:
        access = supabase.table("partner_clients").select("partner_id, tier").eq(
            "client_id", ao["client_id"]
        ).in_("tier", ["list_1", "list_2"]).execute().data or []
    except Exception:
        return []
    tiers = {a["partner_id"]: a["tier"] for a in access if a.get("partner_id")}
    ids = list(tiers.keys())
    if not ids:
        return []
    try:
        profiles = supabase.table("profiles").select("id, name, email, status").in_("id", ids).execute().data or []
    except Exception:
        return []
    submitted = _partner_ids_with_submission(ao["id"])
    out = []
    for p in profiles:
        if not p.get("email"):
            continue
        out.append({
            "id": p["id"],
            "name": p.get("name"),
            "email": p["email"],
            "tier": tiers.get(p["id"]),
            "has_submitted": p["id"] in submitted,
            "blocked": bool(p.get("status") and p["status"] != "active"),
        })
    return out


def notify_selected(ao: dict, partner_ids: list[str], actor_id=None) -> int:
    """Renvoi MANUEL ciblé : envoie l'email d'AO aux seuls partenaires sélectionnés (validés éligibles)."""
    eligible = {p["id"]: p for p in eligible_partners(ao)}
    recipients = [eligible[pid] for pid in partner_ids if pid in eligible]
    client_name = _client_name(ao)
    return _send_to(recipients, ao, client_name, "manual", actor_id)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
