"""
Admin supervision console (admin only):
  * accounts  — every profile with role + last connection, delete account
  * tickets   — support messages with an open/resolved workflow
  * overview  — high-level KPIs (accounts by role, activity over 30 days)
"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Literal, Optional
import httpx

from services.supabase_client import supabase
from services.app_settings import get_notification_settings, set_notification_settings
from routers.auth import require_admin
from config import settings

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview")
async def overview(user: dict = Depends(require_admin)):
    """KPIs for the supervision page. Each block is best-effort — mais un bloc
    en échec est désormais listé dans `degraded` : un None doit se lire
    « indisponible », jamais « zéro »."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    degraded: list[str] = []

    def _count(table, since_col=None, **filters):
        """COUNT côté base (count=exact) : aucune ligne rapatriée, tient la volumétrie."""
        try:
            q = supabase.table(table).select("id", count="exact").limit(1)
            for k, v in filters.items():
                q = q.eq(k, v)
            if since_col:
                q = q.gte(since_col, since)
            return q.execute().count
        except Exception:
            degraded.append(table)
            return None

    profiles = []
    try:
        profiles = supabase.table("profiles").select("id, role, last_login_at").execute().data or []
    except Exception:
        try:
            profiles = supabase.table("profiles").select("id, role").execute().data or []
        except Exception:
            degraded.append("profiles")

    by_role = {}
    active_30d = 0
    for p in profiles:
        by_role[p["role"]] = by_role.get(p["role"], 0) + 1
        if p.get("last_login_at") and p["last_login_at"] >= since:
            active_30d += 1

    tickets_open = None
    try:
        tickets_open = supabase.table("support_messages").select(
            "id", count="exact"
        ).neq("status", "resolved").limit(1).execute().count
    except Exception:
        try:
            tickets_open = supabase.table("support_messages").select(
                "id", count="exact"
            ).limit(1).execute().count
        except Exception:
            degraded.append("support_messages")

    # Coût IA cumulé — métrique sensible réservée aux admins (cet endpoint est
    # require_admin). Elle n'apparaît volontairement pas sur le dashboard staff.
    matchings_total = _count("matchings")
    matching_cost_usd = None
    try:
        rows = supabase.table("matchings").select("cost_usd").execute().data or []
        matching_cost_usd = round(sum(float(r.get("cost_usd") or 0) for r in rows), 2)
    except Exception:
        degraded.append("matchings.cost")

    return {
        "accounts_total": len(profiles) if "profiles" not in degraded else None,
        "accounts_by_role": by_role,
        "active_accounts_30d": active_30d,
        "aos_total": _count("appels_offres"),
        "aos_open": _count("appels_offres", status="open"),
        "aos_30d": _count("appels_offres", since_col="created_at"),
        "submissions_30d": _count("submissions", since_col="submitted_at"),
        "matchings_30d": _count("matchings", since_col="created_at"),
        "matchings_total": matchings_total,
        "matching_cost_usd": matching_cost_usd,
        "tickets_open": tickets_open,
        # Blocs dont la lecture a échoué — l'UI doit dire « indisponible », pas 0.
        "degraded": sorted(set(degraded)) or None,
    }


@router.get("/ai-usage")
async def ai_usage(user: dict = Depends(require_admin)):
    """Usage & coûts IA pour la supervision : coût cumulé, moyenne par run,
    série journalière (30 j) et modèles configurés. Le coût est porté par la
    ligne de rang 1 de chaque run (voir matching_runner._persist)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=30)
    degraded: list[str] = []

    total_cost, total_runs, series = None, None, []
    try:
        rows = supabase.table("matchings").select(
            "cost_usd, created_at"
        ).gt("cost_usd", 0).execute().data or []
        total_runs = len(rows)  # 1 ligne cost>0 par run (rang 1)
        total_cost = round(sum(float(r.get("cost_usd") or 0) for r in rows), 4)
        by_day: dict = {}
        for r in rows:
            ts = r.get("created_at")
            if not ts:
                continue
            try:
                d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except Exception:
                continue
            if d < since:
                continue
            day = str(ts)[:10]
            e = by_day.setdefault(day, {"date": day, "cost": 0.0, "runs": 0})
            e["cost"] += float(r.get("cost_usd") or 0)
            e["runs"] += 1
        series = [
            {"date": k, "cost": round(v["cost"], 4), "runs": v["runs"]}
            for k, v in sorted(by_day.items())
        ]
    except Exception:
        degraded.append("matchings")

    # Nombre total de matchings scorés (lignes), pour information.
    scored = None
    try:
        scored = supabase.table("matchings").select("id", count="exact").limit(1).execute().count
    except Exception:
        pass

    return {
        "total_cost_usd": total_cost,
        "total_runs": total_runs,
        "scored_profiles": scored,
        "avg_cost_per_run": round(total_cost / total_runs, 4) if (total_cost and total_runs) else 0,
        "series_30d": series,
        "models": {
            "extraction": settings.extraction_model,
            "scoring": settings.scoring_model,
            "draft": settings.draft_model,
            "assistant": settings.assistant_model,
        },
        "degraded": sorted(set(degraded)) or None,
    }


# Cache court (par fenêtre) de la réponse MIP RUM : l'API MIP limite à 60 req/min
# par token → on ne tape jamais à chaque rafraîchissement navigateur.
_RUM_WINDOWS = ("24h", "7d", "30d")
_RUM_CACHE: dict[str, tuple[float, dict]] = {}
_RUM_TTL = 60.0


@router.get("/rum")
async def rum_metrics(window: str = "30d", user: dict = Depends(require_admin)):
    """Proxy vers l'API de LECTURE MIP RUM (token gardé côté serveur, jamais au
    navigateur). Cache 60 s par fenêtre (respect du rate-limit MIP 60 req/min).

    Renvoie {"configured": false} tant que MIP_RUM_READ_URL/TOKEN ne sont pas
    définis — l'onglet RUM affiche alors « en attente »."""
    import time
    if window not in _RUM_WINDOWS:
        window = "30d"
    base = (settings.mip_rum_read_url or "").rstrip("/")
    token = settings.mip_rum_read_token
    if not base or not token:
        return {"configured": False,
                "message": "API MIP RUM non configurée (MIP_RUM_READ_URL / MIP_RUM_READ_TOKEN)."}

    now = time.time()
    hit = _RUM_CACHE.get(window)
    if hit and now - hit[0] < _RUM_TTL:
        return hit[1]

    app_id = settings.mip_rum_app_id or "gip-plateforme"
    try:
        with httpx.Client(timeout=12) as client:
            resp = client.get(
                f"{base}/rum/summary",
                params={"app": app_id, "window": window},
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 429:
            return {"configured": True, "ok": False, "window": window,
                    "message": "API MIP RUM: quota atteint (429), réessayez dans un instant."}
        if resp.status_code >= 400:
            return {"configured": True, "ok": False, "window": window,
                    "message": f"API MIP RUM: HTTP {resp.status_code}."}
        payload = {"configured": True, "ok": True, "window": window, "data": resp.json()}
        _RUM_CACHE[window] = (now, payload)
        return payload
    except Exception as e:  # noqa: BLE001
        print(f"[RUM] lecture MIP RUM échouée: {e}")
        return {"configured": True, "ok": False, "window": window,
                "message": "API MIP RUM injoignable."}


@router.get("/accounts")
async def list_accounts(user: dict = Depends(require_admin)):
    """All accounts (admin, commerce, partners) + pending invitations."""
    try:
        accounts = supabase.table("profiles").select(
            "id, email, name, role, org, status, created_at, last_login_at, last_login_ip, avatar_url, mfa_enabled, mfa_required"
        ).order("created_at", desc=True).execute().data or []
    except Exception:
        # colonnes (org/status/last_login_*/mfa_*) pas encore migrées — dégrade proprement
        try:
            accounts = supabase.table("profiles").select(
                "id, email, name, role, org, status, created_at, last_login_at, avatar_url"
            ).order("created_at", desc=True).execute().data or []
        except Exception:
            accounts = supabase.table("profiles").select(
                "id, email, name, role, created_at, avatar_url"
            ).order("created_at", desc=True).execute().data or []

    pending = []
    try:
        now = datetime.now(timezone.utc).isoformat()
        try:
            pending = supabase.table("invitations").select(
                "id, email, name, role, org, expires_at, created_at"
            ).is_("used_at", "null").gte("expires_at", now).order(
                "created_at", desc=True
            ).execute().data or []
        except Exception:
            # 'org' column not migrated yet — degrade gracefully.
            pending = supabase.table("invitations").select(
                "id, email, name, role, expires_at, created_at"
            ).is_("used_at", "null").gte("expires_at", now).order(
                "created_at", desc=True
            ).execute().data or []
    except Exception:
        pass

    return {"accounts": accounts, "pending_invitations": pending}


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[Literal["admin", "commerce", "ao"]] = None
    org: Optional[Literal["uti", "groupement-it"]] = None
    status: Optional[Literal["active", "suspended", "disabled"]] = None


@router.patch("/accounts/{account_id}")
async def update_account(account_id: str, body: AccountUpdate, user: dict = Depends(require_admin)):
    """
    Admin edit of any account: display name, email, role, commercial entity and
    status (active / suspended / disabled). Email changes are propagated to the
    Supabase Auth user. An admin cannot change their own role or status (anti
    self-lockout) — name/email are still allowed.
    """
    is_self = account_id == user["sub"]
    if is_self and (body.role is not None or (body.status is not None and body.status != "active")):
        raise HTTPException(
            status_code=400,
            detail="Vous ne pouvez pas modifier votre propre rôle ni suspendre votre propre compte.",
        )

    profile_update: dict = {}
    if body.name is not None:
        name = body.name.strip()
        if len(name) < 2:
            raise HTTPException(status_code=422, detail="Le nom doit contenir au moins 2 caractères.")
        profile_update["name"] = name
    if body.email is not None:
        profile_update["email"] = body.email
    if body.role is not None:
        profile_update["role"] = body.role
    if body.status is not None:
        profile_update["status"] = body.status
    # org only meaningful for sales accounts; normalise UTI → NULL.
    if body.org is not None or body.role is not None:
        effective_role = body.role if body.role is not None else None
        if body.org is not None:
            profile_update["org"] = body.org if body.org == "groupement-it" else None
        elif effective_role in ("admin", "ao"):
            profile_update["org"] = None

    if not profile_update:
        raise HTTPException(status_code=422, detail="Aucune modification fournie.")

    # Propagate an email change to the Supabase Auth user first.
    if body.email is not None:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.put(
                    f"{settings.supabase_url}/auth/v1/admin/users/{account_id}",
                    headers={
                        "apikey": settings.supabase_service_key,
                        "Authorization": f"Bearer {settings.supabase_service_key}",
                        "Content-Type": "application/json",
                    },
                    json={"email": body.email, "email_confirm": True},
                )
            if resp.status_code >= 400:
                raise HTTPException(status_code=400, detail="Impossible de mettre à jour l'email (déjà utilisé ?).")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=502, detail="Service d'authentification indisponible.")

    try:
        updated = supabase.table("profiles").update(profile_update).eq("id", account_id).execute()
    except Exception as e:
        # 'org'/'status' columns missing — retry without them so the rest applies.
        profile_update.pop("org", None)
        profile_update.pop("status", None)
        if not profile_update:
            raise HTTPException(status_code=500, detail="Colonnes org/status absentes : migration requise.")
        updated = supabase.table("profiles").update(profile_update).eq("id", account_id).execute()

    if not updated.data:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    return updated.data[0]


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, user: dict = Depends(require_admin)):
    """Permanently delete any account (profile + Supabase Auth user)."""
    if account_id == user["sub"]:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas supprimer votre propre compte.")
    try:
        supabase.table("profiles").delete().eq("id", account_id).execute()
        with httpx.Client(timeout=10) as client:
            client.delete(
                f"{settings.supabase_url}/auth/v1/admin/users/{account_id}",
                headers={
                    "apikey": settings.supabase_service_key,
                    "Authorization": f"Bearer {settings.supabase_service_key}",
                },
            )
        return {"message": "Compte supprimé"}
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


class NotificationSettings(BaseModel):
    enabled: Optional[bool] = None
    list2_delay_days: Optional[int] = None
    relance_auto_enabled: Optional[bool] = None
    relance_interval_days: Optional[int] = None
    relance_max: Optional[int] = None


@router.get("/settings")
async def get_settings(user: dict = Depends(require_admin)):
    """Réglages globaux pilotés par l'admin (notifications + relances)."""
    return {"notifications": get_notification_settings()}


@router.put("/settings/notifications")
async def update_notif_settings(body: NotificationSettings, user: dict = Depends(require_admin)):
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=422, detail="Aucun réglage fourni.")
    return {"notifications": set_notification_settings(patch)}


@router.get("/errors")
async def list_errors(limit: int = 100, level: Optional[str] = None, user: dict = Depends(require_admin)):
    """Journal des erreurs/dégradations récentes du backend (ring buffer en
    mémoire — voir services/error_log.py). `level` : error | warning.
    Se vide au redémarrage du service ; l'historique complet vit dans journald
    (RUNBOOK §3)."""
    from services.error_log import recent
    return {"events": recent(limit=limit, level=level)}


@router.get("/tickets")
async def list_tickets(user: dict = Depends(require_admin)):
    try:
        return supabase.table("support_messages").select("*").order(
            "created_at", desc=True
        ).execute().data or []
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise


class TicketUpdate(BaseModel):
    status: Literal["open", "resolved"]


@router.patch("/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, body: TicketUpdate, user: dict = Depends(require_admin)):
    try:
        response = supabase.table("support_messages").update(
            {"status": body.status}
        ).eq("id", ticket_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Ticket introuvable")
        return response.data[0]
    except HTTPException:
        raise
    except Exception:
        # Détail loggé côté serveur ; réponse 500 générique (handler global).
        raise
