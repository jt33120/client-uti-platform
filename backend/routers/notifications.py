"""
Journal des emails de notification envoyés aux partenaires (admin / staff).
Lecture seule — la table partner_email_log est alimentée par services.notifications.
"""
from datetime import datetime, date, timezone, timedelta
from fastapi import APIRouter, Depends
from services.supabase_client import supabase
from routers.auth import require_staff

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Fenêtre d'urgence : un AO à échéance dans ≤ 3 jours remonte comme alerte.
_URGENT_DAYS = 3
_EMAIL_KIND_LABEL = {
    "list_1": "Diffusion liste 1",
    "list_2": "Diffusion liste 2",
    "relance": "Relance partenaires",
    "target": "Envoi ciblé",
}


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(str(s)[:10])
        except Exception:
            return None


@router.get("/feed")
async def notifications_feed(user: dict = Depends(require_staff)):
    """Fil d'alertes (staff) pour la cloche du header : AO urgents (échéance ≤ 3 j)
    + miroir des dernières communications e-mail. Dégrade proprement (jamais 500)."""
    items = []
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=_URGENT_DAYS)

    # 1) AO urgents : ouverts, échéance entre aujourd'hui et J+3, non archivés/brouillons.
    try:
        base = supabase.table("appels_offres").select(
            "id, title, deadline, clients(name)"
        ).eq("status", "open").gte("deadline", today.isoformat()).lte("deadline", horizon.isoformat())
        try:
            rows = base.eq("archived", False).eq("is_draft", False).order("deadline").execute().data or []
        except Exception:
            rows = base.order("deadline").execute().data or []
        for a in rows:
            dl = _parse_date(a.get("deadline"))
            if not dl:
                continue
            days = (dl - today).days
            cl = (a.get("clients") or {}).get("name")
            when = "aujourd'hui" if days <= 0 else ("demain" if days == 1 else f"dans {days} jours")
            items.append({
                "id": f"ao-{a['id']}",
                "kind": "ao_urgent",
                "severity": "urgent" if days <= 1 else "warning",
                "title": a.get("title") or "Appel d'offres",
                "subtitle": (f"{cl} · " if cl else "") + f"Échéance {when}",
                "link": f"/aos/{a['id']}",
                "date": a.get("deadline"),
            })
    except Exception:
        pass

    # 2) Miroir des e-mails partenaires récents.
    try:
        logs = supabase.table("partner_email_log").select(
            "id, ao_id, kind, status, recipient_email, created_at"
        ).order("created_at", desc=True).limit(8).execute().data or []
        ao_ids = list({r["ao_id"] for r in logs if r.get("ao_id")})
        titles = {}
        if ao_ids:
            try:
                for a in supabase.table("appels_offres").select("id, title").in_("id", ao_ids).execute().data or []:
                    titles[a["id"]] = a.get("title")
            except Exception:
                pass
        for r in logs:
            ok = (r.get("status") or "").lower() in ("sent", "ok", "success")
            label = _EMAIL_KIND_LABEL.get(r.get("kind"), "E-mail partenaire")
            t = titles.get(r.get("ao_id"))
            items.append({
                "id": f"email-{r['id']}",
                "kind": "email",
                "severity": "info" if ok else "warning",
                "title": label + ("" if ok else " · échec d'envoi"),
                "subtitle": (f"{t} · " if t else "") + (r.get("recipient_email") or ""),
                "link": f"/aos/{r['ao_id']}" if r.get("ao_id") else "/emails",
                "date": r.get("created_at"),
            })
    except Exception:
        pass

    urgent_count = sum(1 for i in items if i["kind"] == "ao_urgent")
    return {"items": items, "urgent_count": urgent_count, "count": len(items)}


@router.get("/log")
async def email_log(user: dict = Depends(require_staff), limit: int = 200):
    """Derniers envois d'emails aux partenaires, enrichis du titre d'AO et des noms."""
    try:
        rows = supabase.table("partner_email_log").select("*").order(
            "created_at", desc=True
        ).limit(min(max(limit, 1), 500)).execute().data or []
    except Exception:
        return {"logs": []}

    ao_ids = list({r["ao_id"] for r in rows if r.get("ao_id")})
    person_ids = list({
        *(r["recipient_id"] for r in rows if r.get("recipient_id")),
        *(r["sent_by"] for r in rows if r.get("sent_by")),
    })

    ao_titles: dict = {}
    if ao_ids:
        try:
            for a in supabase.table("appels_offres").select("id, title").in_("id", ao_ids).execute().data or []:
                ao_titles[a["id"]] = a.get("title")
        except Exception:
            pass

    names: dict = {}
    if person_ids:
        try:
            for p in supabase.table("profiles").select("id, name").in_("id", person_ids).execute().data or []:
                names[p["id"]] = p.get("name")
        except Exception:
            pass

    for r in rows:
        r["ao_title"] = ao_titles.get(r.get("ao_id"))
        r["recipient_name"] = names.get(r.get("recipient_id"))
        r["sent_by_name"] = names.get(r.get("sent_by"))
    return {"logs": rows}
