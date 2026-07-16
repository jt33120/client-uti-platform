"""
Journal des emails de notification envoyés aux partenaires (admin / staff).
Lecture seule — la table partner_email_log est alimentée par services.notifications.
"""
from datetime import datetime, date, timezone, timedelta
from fastapi import APIRouter, Depends
from services.supabase_client import supabase
from routers.auth import require_staff, get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Fenêtre d'urgence : un AO à échéance dans ≤ 3 jours remonte comme alerte.
_URGENT_DAYS = 3
# Fenêtre des « bonnes nouvelles » candidat (retenu / présenté / gagné) : 14 jours,
# pour ne pas re-notifier indéfiniment une décision ancienne.
_STATUS_WINDOW_DAYS = 14
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


def _urgent_ao_items(today, horizon):
    """AO ouverts à échéance ≤ 3 j (staff). Best-effort."""
    out = []
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
            out.append({
                "id": f"ao-{a['id']}", "kind": "ao_urgent",
                "severity": "urgent" if days <= 1 else "warning",
                "title": a.get("title") or "Appel d'offres",
                "subtitle": (f"{cl} · " if cl else "") + f"Échéance {when}",
                "link": f"/aos/{a['id']}", "date": a.get("deadline"),
            })
    except Exception:
        pass
    return out


def _email_items():
    """Miroir des derniers e-mails partenaires (staff). Best-effort."""
    out = []
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
            out.append({
                "id": f"email-{r['id']}", "kind": "email",
                "severity": "info" if ok else "warning",
                "title": label + ("" if ok else " · échec d'envoi"),
                "subtitle": (f"{t} · " if t else "") + (r.get("recipient_email") or ""),
                "link": f"/aos/{r['ao_id']}" if r.get("ao_id") else "/emails",
                "date": r.get("created_at"),
            })
    except Exception:
        pass
    return out


def _partner_eligible_client_ids(uid):
    """client_ids visibles par ce partenaire (accès list_1 / list_2, non suspendu)."""
    try:
        rows = supabase.table("partner_clients").select("client_id").eq(
            "partner_id", uid).in_("tier", ["list_1", "list_2"]).execute().data or []
        return [r["client_id"] for r in rows if r.get("client_id")]
    except Exception:
        return []


def _partner_responded_ao_ids(uid):
    """ao_id auxquels ce partenaire a déjà soumis au moins un CV."""
    try:
        rows = supabase.table("submissions").select("ao_id").eq(
            "submitted_by", uid).execute().data or []
        return {r["ao_id"] for r in rows if r.get("ao_id")}
    except Exception:
        return set()


def _partner_urgent_ao_items(uid, today, horizon):
    """AO éligibles à échéance ≤ 3 j auxquels le partenaire n'a PAS encore répondu.
    « Il te reste X jours pour répondre. » Best-effort, cloisonné à ses clients."""
    out = []
    try:
        client_ids = _partner_eligible_client_ids(uid)
        if not client_ids:
            return out
        answered = _partner_responded_ao_ids(uid)
        base = supabase.table("appels_offres").select(
            "id, title, deadline, client_id, clients(name)"
        ).eq("status", "open").in_("client_id", client_ids).gte(
            "deadline", today.isoformat()).lte("deadline", horizon.isoformat())
        try:
            rows = base.eq("archived", False).eq("is_draft", False).order("deadline").execute().data or []
        except Exception:
            rows = base.order("deadline").execute().data or []
        for a in rows:
            if a["id"] in answered:
                continue
            dl = _parse_date(a.get("deadline"))
            if not dl:
                continue
            days = (dl - today).days
            cl = (a.get("clients") or {}).get("name")
            when = "aujourd'hui" if days <= 0 else ("demain" if days == 1 else f"dans {days} jours")
            out.append({
                "id": f"ao-{a['id']}", "kind": "ao_urgent",
                "severity": "urgent" if days <= 1 else "warning",
                "title": a.get("title") or "Appel d'offres",
                "subtitle": (f"{cl} · " if cl else "") + f"Échéance {when} — pas encore de réponse",
                "link": f"/aos/{a['id']}", "date": a.get("deadline"),
            })
    except Exception:
        pass
    return out


def _partner_status_items(uid, since):
    """Bonnes nouvelles récentes sur les candidats du partenaire : retenu, présenté
    au client, affaire gagnée (≤ 14 j). Motivant, non-spam. Cloisonné à SES paires
    (ao, consultant). Best-effort (colonnes récentes → rien plutôt qu'une erreur)."""
    out = []
    try:
        subs = supabase.table("submissions").select(
            "ao_id, consultant_id, consultants(name)"
        ).eq("submitted_by", uid).execute().data or []
        if not subs:
            return out
        ao_ids = list({s["ao_id"] for s in subs if s.get("ao_id")})
        cids = list({s["consultant_id"] for s in subs if s.get("consultant_id")})
        name_by = {s.get("consultant_id"): (s.get("consultants") or {}).get("name") for s in subs}
        # Paires RÉELLEMENT soumises par ce partenaire. Le double .in_() ci-dessous
        # renverrait le produit cartésien (ao ∈ ao_ids × consultant ∈ cids) ; on
        # re-filtre donc sur les vraies paires pour ne jamais notifier une décision
        # portant sur une paire (ao, consultant) que le partenaire n'a pas soumise.
        pairs = {(s.get("ao_id"), s.get("consultant_id")) for s in subs}
        if not ao_ids or not cids:
            return out
        rows = supabase.table("ao_consultant_state").select(
            "ao_id, consultant_id, validation, sent_to_client_at, deal_status, updated_at"
        ).in_("ao_id", ao_ids).in_("consultant_id", cids).execute().data or []
        rows = [r for r in rows if (r.get("ao_id"), r.get("consultant_id")) in pairs]
        titles = {}
        try:
            for a in supabase.table("appels_offres").select("id, title").in_("id", ao_ids).execute().data or []:
                titles[a["id"]] = a.get("title")
        except Exception:
            pass
        for r in rows:
            upd = _parse_date(r.get("updated_at"))
            if upd and upd < since:
                continue  # trop ancien : on ne re-notifie pas indéfiniment
            nm = name_by.get(r.get("consultant_id")) or "Votre candidat"
            t = titles.get(r.get("ao_id"))
            ctx = f" · {t}" if t else ""
            if r.get("deal_status") == "gagnee":
                label, sev = "Affaire gagnée 🎉", "info"
                sub = f"{nm} — mission remportée{ctx}"
            elif r.get("sent_to_client_at"):
                label, sev = "Profil présenté au client", "info"
                sub = f"{nm} a été présenté au client{ctx}"
            elif r.get("validation") == "retenu":
                label, sev = "Profil retenu", "info"
                sub = f"{nm} a été retenu par GRP-IT{ctx}"
            else:
                continue
            out.append({
                "id": f"status-{r.get('ao_id')}-{r.get('consultant_id')}", "kind": "status",
                "severity": sev, "title": label, "subtitle": sub,
                "link": f"/aos/{r.get('ao_id')}", "date": r.get("updated_at"),
            })
    except Exception:
        pass
    return out


def _missing_info_items(role, uid):
    """Invitation à compléter les infos VRAIMENT importantes des consultants : ici,
    le statut de disponibilité manquant. Agrégé (un seul item), non-spam. Partenaire :
    ses consultants ; staff : tout le vivier. Best-effort (colonne non migrée → rien)."""
    out = []
    try:
        q = supabase.table("consultants").select("id, name, availability_status")
        if role == "ao":
            q = q.eq("created_by", uid)
        cons = q.execute().data or []
        missing = [c for c in cons if not c.get("availability_status")]
        if missing:
            n = len(missing)
            out.append({
                "id": "missing-availability", "kind": "missing_info", "severity": "info",
                "title": f"{n} consultant{'s' if n > 1 else ''} sans statut de disponibilité",
                "subtitle": "Renseignez « disponible / en mission… » pour un vivier à jour.",
                "link": f"/consultants/{missing[0]['id']}" if n == 1 else "/consultants",
            })
    except Exception:
        pass
    return out


@router.get("/feed")
async def notifications_feed(user: dict = Depends(get_current_user)):
    """Fil d'alertes de la cloche du header, selon le rôle :
      • staff      : AO urgents (échéance ≤ 3 j) + miroir e-mail + infos manquantes ;
      • partenaire : invitations à compléter les infos importantes de SES consultants.
    Dégrade proprement (jamais 500) ; chaque bloc est isolé."""
    role = user.get("role")
    uid = user.get("sub")
    is_staff = role in ("admin", "commerce")
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=_URGENT_DAYS)

    items = []
    if is_staff:
        items += _urgent_ao_items(today, horizon)
        items += _email_items()
    elif role == "ao":
        # Partenaire : AO éligibles urgents non répondus + progression de SES candidats.
        items += _partner_urgent_ao_items(uid, today, horizon)
        items += _partner_status_items(uid, today - timedelta(days=_STATUS_WINDOW_DAYS))
    items += _missing_info_items(role, uid)

    urgent_count = sum(1 for i in items if i["kind"] == "ao_urgent")
    action_count = sum(1 for i in items if i["kind"] in ("ao_urgent", "missing_info"))
    return {"items": items, "urgent_count": urgent_count, "action_count": action_count, "count": len(items)}


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
