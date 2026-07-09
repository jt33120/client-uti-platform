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


_AI_WINDOWS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}


def _ai_usage_from_ledger(since_iso: str) -> Optional[dict]:
    """Agrège le registre ``ai_usage`` (source de vérité). Renvoie None si la
    table est absente pour que l'appelant se rabatte sur les matchings."""
    try:
        rows = supabase.table("ai_usage").select(
            "created_at, provider, model, operation, cost_usd, cost_source, "
            "input_tokens, output_tokens, cached_tokens, entity_type, entity_id, user_id, user_email"
        ).gte("created_at", since_iso).order("created_at", desc=True).limit(50000).execute().data
    except Exception:
        return None  # table pas encore migrée → fallback matchings
    rows = rows or []

    def _c(r):
        return float(r.get("cost_usd") or 0)

    total_cost = round(sum(_c(r) for r in rows), 4)
    total_calls = len(rows)
    total_in = sum(int(r.get("input_tokens") or 0) for r in rows)
    total_out = sum(int(r.get("output_tokens") or 0) for r in rows)
    total_cached = sum(int(r.get("cached_tokens") or 0) for r in rows)

    by_op: dict = {}
    by_model: dict = {}
    by_source: dict = {}
    by_day: dict = {}
    by_ao: dict = {}
    by_user: dict = {}
    for r in rows:
        cost = _c(r)
        toks = int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)
        op = r.get("operation") or "—"
        md = r.get("model") or "—"
        src = r.get("cost_source") or "none"
        e = by_op.setdefault(op, {"key": op, "cost": 0.0, "calls": 0, "tokens": 0})
        e["cost"] += cost; e["calls"] += 1; e["tokens"] += toks
        m = by_model.setdefault(md, {"key": md, "cost": 0.0, "calls": 0, "tokens": 0})
        m["cost"] += cost; m["calls"] += 1; m["tokens"] += toks
        by_source[src] = round(by_source.get(src, 0.0) + cost, 4)
        day = str(r.get("created_at") or "")[:10]
        if day:
            d = by_day.setdefault(day, {"date": day, "cost": 0.0, "calls": 0})
            d["cost"] += cost; d["calls"] += 1
        if r.get("entity_type") == "ao" and r.get("entity_id"):
            a = by_ao.setdefault(r["entity_id"], {"ao_id": r["entity_id"], "cost": 0.0, "calls": 0})
            a["cost"] += cost; a["calls"] += 1
        uid = r.get("user_id") or r.get("user_email")
        if uid:
            u = by_user.setdefault(uid, {"user_id": r.get("user_id"), "email": r.get("user_email"),
                                          "cost": 0.0, "calls": 0})
            u["cost"] += cost; u["calls"] += 1

    def _top(d, n=8):
        out = sorted(d.values(), key=lambda x: x["cost"], reverse=True)[:n]
        for x in out:
            x["cost"] = round(x["cost"], 4)
        return out

    # Résolution des titres d'AO (best-effort, une seule requête).
    top_aos = _top(by_ao)
    if top_aos:
        try:
            ids = [a["ao_id"] for a in top_aos]
            aos = supabase.table("appels_offres").select("id, title").in_("id", ids).execute().data or []
            titles = {a["id"]: a.get("title") for a in aos}
            for a in top_aos:
                a["title"] = titles.get(a["ao_id"]) or "AO supprimé"
        except Exception:
            for a in top_aos:
                a["title"] = a["ao_id"][:8]

    # Résolution des noms de comptes (best-effort).
    top_users = _top(by_user)
    if top_users:
        try:
            ids = [u["user_id"] for u in top_users if u.get("user_id")]
            if ids:
                profs = supabase.table("profiles").select("id, name, email").in_("id", ids).execute().data or []
                names = {p["id"]: p for p in profs}
                for u in top_users:
                    p = names.get(u.get("user_id")) or {}
                    u["name"] = p.get("name") or u.get("email") or "—"
                    u["email"] = u.get("email") or p.get("email")
        except Exception:
            pass

    for e in by_op.values():
        e["cost"] = round(e["cost"], 4)
    for m in by_model.values():
        m["cost"] = round(m["cost"], 4)

    return {
        "source": "ledger",
        "total_cost_usd": total_cost,
        "total_calls": total_calls,
        "tokens": {"input": total_in, "output": total_out, "cached": total_cached,
                   "total": total_in + total_out},
        "avg_cost_per_call": round(total_cost / total_calls, 6) if total_calls else 0,
        "by_operation": sorted(by_op.values(), key=lambda x: x["cost"], reverse=True),
        "by_model": sorted(by_model.values(), key=lambda x: x["cost"], reverse=True),
        "by_cost_source": by_source,
        "series": [{"date": k, "cost": round(by_day[k]["cost"], 4),
                    "calls": by_day[k]["calls"]} for k in sorted(by_day)],
        "top_aos": top_aos,
        "top_users": top_users,
    }


@router.get("/ai-usage")
async def ai_usage(window: str = "30d", user: dict = Depends(require_admin)):
    """Usage & coûts IA — lit le registre ``ai_usage`` (coût réel OpenRouter par
    appel, attribué au compte / système IA / AO). Se rabat sur les matchings tant
    que la table n'existe pas. `window` ∈ 24h|7d|30d|90d."""
    days = _AI_WINDOWS.get(window, 30)
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    since_iso = since.isoformat()
    degraded: list[str] = []

    models = {
        "extraction": settings.extraction_model,
        "scoring": settings.scoring_model,
        "summary": settings.summary_model,
        "draft": settings.draft_model,
        "assistant": settings.assistant_model,
    }

    ledger = _ai_usage_from_ledger(since_iso)
    if ledger is not None:
        ledger["window"] = window
        ledger["models"] = models
        ledger["degraded"] = None
        return ledger

    # ── Fallback : ancienne vue basée sur matchings.cost_usd ──────────────
    total_cost, total_runs, series = None, None, []
    try:
        rows = supabase.table("matchings").select(
            "cost_usd, created_at"
        ).gt("cost_usd", 0).gte("created_at", since_iso).execute().data or []
        total_runs = len(rows)
        total_cost = round(sum(float(r.get("cost_usd") or 0) for r in rows), 4)
        by_day: dict = {}
        for r in rows:
            day = str(r.get("created_at") or "")[:10]
            if not day:
                continue
            e = by_day.setdefault(day, {"date": day, "cost": 0.0, "calls": 0})
            e["cost"] += float(r.get("cost_usd") or 0)
            e["calls"] += 1
        series = [{"date": k, "cost": round(v["cost"], 4), "calls": v["calls"]}
                  for k, v in sorted(by_day.items())]
    except Exception:
        degraded.append("matchings")

    return {
        "source": "matchings",
        "window": window,
        "total_cost_usd": total_cost,
        "total_calls": total_runs,
        "avg_cost_per_call": round(total_cost / total_runs, 6) if (total_cost and total_runs) else 0,
        "series": series,
        "by_operation": [], "by_model": [], "by_cost_source": {},
        "top_aos": [], "top_users": [],
        "models": models,
        "degraded": sorted(set(degraded)) or None,
    }


# Cache court (par fenêtre) du miroir OpenRouter (l'API compte n'aime pas être martelée).
_OR_CACHE: dict[str, tuple[float, dict]] = {}
_OR_TTL = 60.0
_OR_WINDOWS = {"24h": 1, "7d": 7, "30d": 30}


def _or_activity_date(s: str):
    """Parse une date d'activité OpenRouter ('2026-07-08 00:00:00' ou ISO)."""
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T")).date()
    except Exception:
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
        except Exception:
            return None


@router.get("/ai-openrouter")
async def ai_openrouter(window: str = "30d", user: dict = Depends(require_admin)):
    """Miroir du compte OpenRouter UTI (source de vérité facturation, non
    hallucinée) : solde, usage cumulé, et — avec la clé de *provisioning* —
    l'activité agrégée par modèle / par jour + l'usage par clé (comme le
    dashboard OpenRouter). Token gardé côté serveur, cache 60 s par fenêtre."""
    import time
    days = _OR_WINDOWS.get(window, 30)
    prov = settings.openrouter_provisioning_key
    runtime = settings.openrouter_key
    if not (prov or runtime):
        return {"configured": False,
                "message": "Aucune clé OpenRouter configurée (OPENROUTER_KEY)."}

    ck = f"or:{window}"
    now = time.time()
    hit = _OR_CACHE.get(ck)
    if hit and now - hit[0] < _OR_TTL:
        return hit[1]

    out: dict = {"configured": True, "has_provisioning": bool(prov), "ok": True,
                 "window": window, "balance": None, "usage": None,
                 "total_credits": None, "key_label": None,
                 "totals": {"cost": 0.0, "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "tokens": 0},
                 "by_model": [], "series": [], "keys": []}
    base = "https://openrouter.ai/api/v1"
    since = (datetime.now(timezone.utc).date() - timedelta(days=days - 1))
    try:
        with httpx.Client(timeout=12) as client:
            # Solde & usage cumulé du compte (facturé).
            try:
                r = client.get(f"{base}/credits", headers={"Authorization": f"Bearer {runtime or prov}"})
                if r.status_code < 400:
                    d = (r.json() or {}).get("data") or {}
                    tc = float(d.get("total_credits") or 0)
                    tu = float(d.get("total_usage") or 0)
                    out["total_credits"] = round(tc, 4)
                    out["usage"] = round(tu, 4)
                    out["balance"] = round(tc - tu, 4)
            except Exception:
                pass
            # Activité agrégée par modèle / par jour — nécessite la provisioning.
            if prov:
                try:
                    r = client.get(f"{base}/activity", headers={"Authorization": f"Bearer {prov}"})
                    if r.status_code < 400:
                        rows = (r.json() or {}).get("data") or []
                        by_model: dict = {}
                        by_day: dict = {}
                        for a in rows:
                            dt = _or_activity_date(a.get("date"))
                            if dt is None or dt < since:
                                continue
                            cost = float(a.get("usage") or 0)
                            reqs = int(a.get("requests") or 0)
                            pin = int(a.get("prompt_tokens") or 0)
                            pout = int(a.get("completion_tokens") or 0)
                            md = a.get("model") or a.get("model_permaslug") or "—"
                            m = by_model.setdefault(md, {"model": md, "provider": a.get("provider_name"),
                                                          "cost": 0.0, "requests": 0,
                                                          "prompt_tokens": 0, "completion_tokens": 0})
                            m["cost"] += cost; m["requests"] += reqs
                            m["prompt_tokens"] += pin; m["completion_tokens"] += pout
                            day = dt.isoformat()
                            e = by_day.setdefault(day, {"date": day, "cost": 0.0, "requests": 0, "tokens": 0})
                            e["cost"] += cost; e["requests"] += reqs; e["tokens"] += pin + pout
                            t = out["totals"]
                            t["cost"] += cost; t["requests"] += reqs
                            t["prompt_tokens"] += pin; t["completion_tokens"] += pout
                        t = out["totals"]
                        t["cost"] = round(t["cost"], 4)
                        t["tokens"] = t["prompt_tokens"] + t["completion_tokens"]
                        for m in by_model.values():
                            m["cost"] = round(m["cost"], 4)
                            m["tokens"] = m["prompt_tokens"] + m["completion_tokens"]
                        out["by_model"] = sorted(by_model.values(), key=lambda x: x["cost"], reverse=True)
                        out["series"] = [{"date": k, "cost": round(v["cost"], 4), "requests": v["requests"],
                                          "tokens": v["tokens"]} for k, v in sorted(by_day.items())]
                except Exception:
                    pass
                # Usage par clé (Top API Keys).
                try:
                    r = client.get(f"{base}/keys", headers={"Authorization": f"Bearer {prov}"})
                    if r.status_code < 400:
                        klist = (r.json() or {}).get("data") or []
                        keys = [{"name": k.get("name"), "label": k.get("label"),
                                 "usage": round(float(k.get("usage") or 0), 4),
                                 "usage_daily": round(float(k.get("usage_daily") or 0), 4),
                                 "usage_weekly": round(float(k.get("usage_weekly") or 0), 4),
                                 "usage_monthly": round(float(k.get("usage_monthly") or 0), 4),
                                 "disabled": bool(k.get("disabled"))} for k in klist]
                        out["keys"] = sorted(keys, key=lambda x: x["usage"], reverse=True)
                except Exception:
                    pass
            else:
                # Sans provisioning : au moins le label/plafond de la clé runtime.
                try:
                    r = client.get(f"{base}/key", headers={"Authorization": f"Bearer {runtime}"})
                    if r.status_code < 400:
                        d = (r.json() or {}).get("data") or {}
                        out["key_label"] = d.get("label")
                except Exception:
                    pass
    except Exception as e:  # noqa: BLE001
        print(f"[AI-OR] miroir OpenRouter échec: {e}")
        out["ok"] = False
        return out

    _OR_CACHE[ck] = (now, out)
    return out


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
