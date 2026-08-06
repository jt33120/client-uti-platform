"""
Admin supervision console (admin only):
  * accounts  — every profile with role + last connection, delete account
  * tickets   — support messages with an open/resolved workflow
  * overview  — high-level KPIs (accounts by role, activity over 30 days)
"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Literal, Optional
import httpx

from services.supabase_client import supabase
from services import credentials
from services.app_settings import (
    get_notification_settings, set_notification_settings,
    get_ai_budget_settings, set_ai_budget_settings,
    get_retention_settings, set_retention_settings,
)
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


def _acct_key(r: dict) -> Optional[str]:
    """Clé d'attribution d'un compte : user_id si présent, sinon email."""
    return r.get("user_id") or r.get("user_email")


def _ai_usage_from_ledger(
    since_iso: str,
    *,
    f_op: Optional[str] = None,
    f_model: Optional[str] = None,
    f_account: Optional[str] = None,
) -> Optional[dict]:
    """Agrège le registre ``ai_usage`` (source de vérité). Renvoie None si la
    table est absente pour que l'appelant se rabatte sur les matchings.

    Filtres optionnels (croisables) : ``f_op`` (fonction/opération IA),
    ``f_model`` (modèle), ``f_account`` (compte consommateur). Les *facettes*
    (valeurs disponibles pour les filtres) sont TOUJOURS calculées sur le jeu
    complet, avant filtrage, pour que les menus déroulants restent stables quel
    que soit le filtre actif."""
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

    # ── Facettes (avant filtrage) : valeurs sélectionnables + volume associé ──
    facet_ops: dict = {}
    facet_models: dict = {}
    facet_accounts: dict = {}
    for r in rows:
        op = r.get("operation") or "—"
        md = r.get("model") or "—"
        facet_ops[op] = facet_ops.get(op, 0) + 1
        facet_models[md] = facet_models.get(md, 0) + 1
        acct = _acct_key(r)
        if acct:
            fa = facet_accounts.setdefault(acct, {"key": acct, "id": r.get("user_id"),
                                                  "email": r.get("user_email"), "calls": 0})
            fa["calls"] += 1

    # ── Filtrage (croisé) ────────────────────────────────────────────────────
    def _match(r) -> bool:
        if f_op and (r.get("operation") or "—") != f_op:
            return False
        if f_model and (r.get("model") or "—") != f_model:
            return False
        if f_account and _acct_key(r) != f_account:
            return False
        return True

    frows = [r for r in rows if _match(r)] if (f_op or f_model or f_account) else rows

    total_cost = round(sum(_c(r) for r in frows), 4)
    total_calls = len(frows)
    total_in = sum(int(r.get("input_tokens") or 0) for r in frows)
    total_out = sum(int(r.get("output_tokens") or 0) for r in frows)
    total_cached = sum(int(r.get("cached_tokens") or 0) for r in frows)

    by_op: dict = {}
    by_model: dict = {}
    by_source: dict = {}
    by_day: dict = {}
    by_ao: dict = {}
    by_user: dict = {}
    for r in frows:
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
            d = by_day.setdefault(day, {"date": day, "cost": 0.0, "calls": 0, "ops": {}})
            d["cost"] += cost; d["calls"] += 1
            # Ventilation par fonction DANS la journée → graphe empilé par fonction.
            d["ops"][op] = d["ops"].get(op, 0) + 1
        if r.get("entity_type") == "ao" and r.get("entity_id"):
            a = by_ao.setdefault(r["entity_id"], {"ao_id": r["entity_id"], "cost": 0.0, "calls": 0})
            a["cost"] += cost; a["calls"] += 1
        uid = _acct_key(r)
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

    # Noms des comptes de la facette (best-effort, batch) — pour le menu déroulant.
    try:
        fa_ids = [v["id"] for v in facet_accounts.values() if v.get("id")]
        if fa_ids:
            profs = supabase.table("profiles").select("id, name, email").in_("id", fa_ids).execute().data or []
            names = {p["id"]: p for p in profs}
            for v in facet_accounts.values():
                p = names.get(v.get("id")) or {}
                v["name"] = p.get("name") or v.get("email") or "—"
                v["email"] = v.get("email") or p.get("email")
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
                    "calls": by_day[k]["calls"], "ops": by_day[k]["ops"]} for k in sorted(by_day)],
        "top_aos": top_aos,
        "top_users": top_users,
        "facets": {
            "operations": sorted(
                ({"key": k, "calls": v} for k, v in facet_ops.items()),
                key=lambda x: x["calls"], reverse=True),
            "models": sorted(
                ({"key": k, "calls": v} for k, v in facet_models.items()),
                key=lambda x: x["calls"], reverse=True),
            "accounts": sorted(facet_accounts.values(), key=lambda x: x["calls"], reverse=True),
        },
        "filters": {"operation": f_op, "model": f_model, "account": f_account},
    }


@router.get("/ai-usage")
async def ai_usage(
    window: str = "30d",
    operation: Optional[str] = None,
    model: Optional[str] = None,
    account: Optional[str] = None,
    user: dict = Depends(require_admin),
):
    """Usage & coûts IA — lit le registre ``ai_usage`` (coût réel OpenRouter par
    appel, attribué au compte / système IA / AO). Se rabat sur les matchings tant
    que la table n'existe pas. `window` ∈ 24h|7d|30d|90d.

    Filtres optionnels (croisables) : `operation` (fonction IA : extraction,
    scoring, draft, summary, assistant…), `model` (modèle exact), `account`
    (compte consommateur = user_id ou email). Les facettes disponibles sont
    renvoyées dans la réponse (`facets`) pour alimenter les menus de filtre."""
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

    ledger = _ai_usage_from_ledger(since_iso, f_op=operation, f_model=model, f_account=account)
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


def _supervised_fragments() -> list[str]:
    """Fragments de nom identifiant les clés « plateforme » (cf. config).
    Vide → défaut « plateforme » (convention de nommage des clés UTI)."""
    raw = settings.openrouter_supervised_keys or "plateforme"
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return parts or ["plateforme"]


def _is_platform_key(name: Optional[str], fragments: list[str]) -> bool:
    n = (name or "").lower()
    return any(f in n for f in fragments)


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
                 "platform_cost": None, "keys_filtered": False,
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
                            e = by_day.setdefault(day, {"date": day, "cost": 0.0, "requests": 0, "tokens": 0, "models": {}})
                            e["cost"] += cost; e["requests"] += reqs; e["tokens"] += pin + pout
                            # Ventilation par modèle DANS la journée → graphe empilé (façon OpenRouter).
                            e["models"][md] = e["models"].get(md, 0.0) + cost
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
                                          "tokens": v["tokens"],
                                          "models": {m: round(c, 4) for m, c in v.get("models", {}).items()}}
                                         for k, v in sorted(by_day.items())]
                except Exception:
                    pass
                # Usage par clé — restreint aux clés DE LA PLATEFORME (les autres
                # apps du compte OpenRouter, ex. CV MANAGER / Achatinfo, sont
                # masquées : la supervision UTI ne montre que sa propre conso).
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
                        frags = _supervised_fragments()
                        plat = [k for k in keys if _is_platform_key(k["name"], frags)]
                        # Garde-fou : si le nommage ne matche rien, on montre tout
                        # plutôt qu'un tableau vide (et on le signale).
                        out["keys_filtered"] = bool(plat)
                        shown = plat or keys
                        out["keys"] = sorted(shown, key=lambda x: x["usage"], reverse=True)
                        # Coût agrégé des SEULES clés plateforme (source facturation
                        # fiable par clé — les totaux /activity sont au niveau compte).
                        out["platform_cost"] = {
                            "daily": round(sum(k["usage_daily"] for k in shown), 4),
                            "weekly": round(sum(k["usage_weekly"] for k in shown), 4),
                            "monthly": round(sum(k["usage_monthly"] for k in shown), 4),
                            "total": round(sum(k["usage"] for k in shown), 4),
                        }
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


# Séries fines Core Web Vitals via l'API console MIP v1 (token console distinct).
_RUMV_CACHE: dict[str, tuple[float, dict]] = {}
_RUMV_TTL = 60.0
# v1 borne les périodes à 7 j max ; 30 j est rabattu sur 7 j.
_V1_PERIODS = {"1h": "1h", "24h": "24h", "7d": "7d", "30d": "7d"}
_VITALS_ALLOW = {"LCP", "INP", "CLS", "FCP", "TTFB"}


@router.get("/rum-vitals")
async def rum_vitals(window: str = "7d", series: str = "LCP", user: dict = Depends(require_admin)):
    """Proxy vers l'API console MIP v1 (GET /vitals) pour les séries temporelles
    Core Web Vitals (ex. LCP p75 dans le temps). Token console gardé côté serveur.
    Renvoie l'objet `data` de l'enveloppe v1 ({p75, series}). Cache 60 s."""
    import time
    base = (settings.mip_rum_console_url or "").rstrip("/")
    token = settings.mip_rum_console_token
    if not base or not token:
        return {"configured": False,
                "message": "API console MIP non configurée (MIP_RUM_CONSOLE_URL / MIP_RUM_CONSOLE_TOKEN)."}
    period = _V1_PERIODS.get(window, "7d")
    sel = ",".join([s for s in (series or "").upper().split(",") if s in _VITALS_ALLOW]) or "LCP"
    app_id = settings.mip_rum_app_id or "gip-plateforme"

    ck = f"{sel}:{period}:{app_id}"
    now = time.time()
    hit = _RUMV_CACHE.get(ck)
    if hit and now - hit[0] < _RUMV_TTL:
        return hit[1]
    try:
        with httpx.Client(timeout=12) as client:
            resp = client.get(
                f"{base}/vitals",
                params={"series": sel, "period": period, "app": app_id},
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 429:
            return {"configured": True, "ok": False, "period": period,
                    "message": "API console MIP : quota atteint (429), réessayez dans un instant."}
        if resp.status_code >= 400:
            return {"configured": True, "ok": False, "period": period,
                    "message": f"API console MIP : HTTP {resp.status_code}."}
        payload = {"configured": True, "ok": True, "period": period,
                   "data": (resp.json() or {}).get("data") or {}}
        _RUMV_CACHE[ck] = (now, payload)
        return payload
    except Exception as e:  # noqa: BLE001
        print(f"[RUM-V1] lecture vitals MIP échouée: {e}")
        return {"configured": True, "ok": False, "period": period,
                "message": "API console MIP injoignable."}


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

    # Propager le changement d'adresse à l'adresse de CONNEXION d'abord : c'est
    # elle que /auth/login interroge. La contrainte UNIQUE sur
    # user_credentials.email est ce qui refuse une adresse déjà prise (23505),
    # exactement comme GoTrue le faisait auparavant.
    ancien_email = None
    if body.email is not None:
        try:
            existant = credentials.by_user_id(account_id)
            if existant is None:
                raise HTTPException(status_code=404, detail="Compte introuvable")
            ancien_email = existant["email"]
            credentials.set_email(account_id, body.email)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            if "23505" in str(e) or "duplicate key" in str(e).lower():
                raise HTTPException(status_code=409, detail="Cette adresse email est déjà utilisée par un autre compte.")
            print(f"[ADMIN] changement d'email de connexion impossible pour {account_id}: {e}")
            raise HTTPException(status_code=500, detail="Impossible de mettre à jour l'email.")

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
        # Le profil n'existe pas alors que l'adresse de connexion vient d'être
        # changée : on la remet, sinon le compte garderait une adresse de
        # connexion que plus rien n'affiche.
        if ancien_email:
            try:
                credentials.set_email(account_id, ancien_email)
            except Exception as revert_err:  # noqa: BLE001
                print(f"[ADMIN] retour arrière de l'email impossible pour {account_id}: {revert_err}")
        raise HTTPException(status_code=404, detail="Compte introuvable")
    return updated.data[0]


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, user: dict = Depends(require_admin)):
    """Supprime définitivement un compte (profil + identifiants de connexion).

    Un seul DELETE suffit désormais : `user_credentials.user_id` référence
    `profiles(id) ON DELETE CASCADE` (migration 0018), donc la ligne
    d'identifiants part avec le profil. Il n'y a plus d'utilisateur GoTrue à
    supprimer dans un second appel — et donc plus de risque qu'il survive au
    profil parce que l'appel HTTP a échoué en silence.
    """
    if account_id == user["sub"]:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas supprimer votre propre compte.")
    try:
        supabase.table("profiles").delete().eq("id", account_id).execute()
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


class AiBudgetSettings(BaseModel):
    enabled: Optional[bool] = None
    weekly_usd: Optional[float] = None
    monthly_usd: Optional[float] = None


@router.get("/settings")
async def get_settings(user: dict = Depends(require_admin)):
    """Réglages globaux pilotés par l'admin (notifications + relances + budget IA + rétention RGPD)."""
    return {
        "notifications": get_notification_settings(),
        "ai_budget": get_ai_budget_settings(),
        "data_retention": get_retention_settings(),
    }


class RetentionSettings(BaseModel):
    enabled: Optional[bool] = None
    months: Optional[int] = None


@router.put("/settings/retention")
async def update_retention_settings(body: RetentionSettings, user: dict = Depends(require_admin)):
    """Conservation des données (RGPD) : purge auto des CV passé N mois. Opt-in."""
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=422, detail="Aucun réglage fourni.")
    return {"data_retention": set_retention_settings(patch)}


@router.get("/settings/retention-state")
async def retention_state(user: dict = Depends(require_admin)):
    """Ce que la purge RGPD ferait, qu'elle soit active ou non.

    Endpoint séparé de `/settings` à dessein : il coûte deux comptages, alors que
    les réglages sont lus à chaque ouverture d'écran.
    """
    from services.data_retention import retention_state as _state
    return _state()


@router.get("/ai-literacy")
async def ai_literacy_register(user: dict = Depends(require_admin)):
    """Registre de littératie IA (AI Act, art. 4).

    L'obligation est de MOYENS : ce qui se démontre en contrôle, c'est la trace —
    qui a été sensibilisé, à quelle version du contenu, et quand. D'où un registre
    exhaustif (tous les comptes actifs, y compris ceux qui n'ont jamais attesté)
    plutôt qu'une simple liste d'attestations.
    """
    from services import ai_literacy
    try:
        rows = supabase.table("profiles").select(
            "id, name, email, role, status, ai_literacy_ack_at, ai_literacy_version"
        ).order("name").execute().data or []
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Registre indisponible.") from e

    people = []
    for r in rows:
        # Un compte suspendu n'opère plus le système : le compter comme « à faire »
        # gonflerait artificiellement le retard sans rien dire du risque réel.
        if (r.get("status") or "active") != "active":
            continue
        st = ai_literacy.status(r)
        people.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "email": r.get("email"),
            "role": r.get("role"),
            "state": st["state"],
            "ok": st["ok"],
            "ack_at": st["ack_at"],
            "acked_version": st["acked_version"],
            "due_at": st["due_at"],
        })

    # Les personnes à régulariser d'abord.
    order = {ai_literacy.NEVER: 0, ai_literacy.OUTDATED: 1, ai_literacy.EXPIRED: 2, ai_literacy.OK: 3}
    people.sort(key=lambda p: (order.get(p["state"], 9), (p["name"] or "").lower()))

    done = sum(1 for p in people if p["ok"])
    return {
        "current_version": ai_literacy.VERSION,
        "validity_days": ai_literacy.VALIDITY_DAYS,
        "total": len(people),
        "done": done,
        "pending": len(people) - done,
        "coverage_pct": round(done / len(people) * 100) if people else None,
        "people": people,
    }


@router.put("/settings/notifications")
async def update_notif_settings(body: NotificationSettings, user: dict = Depends(require_admin)):
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=422, detail="Aucun réglage fourni.")
    return {"notifications": set_notification_settings(patch)}


@router.put("/settings/ai-budget")
async def update_ai_budget_settings(body: AiBudgetSettings, user: dict = Depends(require_admin)):
    """Budgets IA hebdo/mensuel (USD). Alerte email aux admins à 80 % puis 100 %.
    Une limite à 0 désactive la surveillance de cette période. Alerte seulement."""
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=422, detail="Aucun réglage fourni.")
    return {"ai_budget": set_ai_budget_settings(patch)}


@router.post("/ai-budget/test")
async def test_ai_budget_alert(user: dict = Depends(require_admin)):
    """Envoie un email d'alerte budget d'EXEMPLE à l'admin courant, pour vérifier
    la chaîne d'envoi (SMTP + template) sans attendre le tick horaire."""
    from services.ai_budget import send_test_alert, _admin_recipients
    to = user.get("email")
    if not to:
        raise HTTPException(status_code=400, detail="Aucune adresse email associée à ce compte.")
    ok, err = send_test_alert(to)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Envoi échoué : {err or 'SMTP indisponible'}")
    return {"sent": True, "to": to, "admin_count": len(_admin_recipients())}


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


# Plancher « profil faible » (aligné sur la reco du front) : un profil retenu
# sous ce score est un « outsider retenu » (écart IA↔humain notable).
_DECISION_LOW = 50


@router.get("/decision-insights")
async def decision_insights(days: int = 90, user: dict = Depends(require_admin)):
    """Analytics d'écart IA↔humain (N2) à partir des décisions (`human_decision`).
    Révèle OÙ la reco IA est le plus corrigée par les opérateurs — matière à
    calibrer la grille et les prompts. Purement analytique (aucun changement de
    modèle). Admin only."""
    days = max(7, min(int(days or 90), 365))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = supabase.table("human_decision").select(
            "ao_id, consultant_id, ai_rank, ai_score, decision, justification, decided_by, decided_at"
        ).gte("decided_at", since).order("decided_at", desc=True).execute().data or []
    except Exception:
        rows = []

    totals = {"total": len(rows), "retained": 0, "rejected": 0, "overridden": 0}
    ecarts = {"overridden": 0, "rejected_top": 0, "retained_low": 0}

    def _ecart(r: dict):
        d = r.get("decision")
        if d == "overridden":
            return "overridden"                       # désaccord explicite
        if d == "rejected" and (r.get("ai_rank") or 99) <= 2:
            return "rejected_top"                      # top IA écarté
        if d == "retained" and r.get("ai_score") is not None and r["ai_score"] < _DECISION_LOW:
            return "retained_low"                      # outsider retenu
        return None

    ao_ids, op_ids = set(), set()
    by_ao, by_op, weekly = {}, {}, {}
    recent_overrides = []
    for r in rows:
        d = r.get("decision")
        if d in totals:
            totals[d] += 1
        e = _ecart(r)
        if e:
            ecarts[e] += 1
        aid, oid = r.get("ao_id"), r.get("decided_by")
        if aid:
            ao_ids.add(aid)
            a = by_ao.setdefault(aid, {"ao_id": aid, "total": 0, "ecarts": 0})
            a["total"] += 1
            a["ecarts"] += 1 if e else 0
        if oid:
            op_ids.add(oid)
            o = by_op.setdefault(oid, {"id": oid, "total": 0, "overrides": 0})
            o["total"] += 1
            o["overrides"] += 1 if d == "overridden" else 0
        try:
            dt = datetime.fromisoformat(str(r.get("decided_at")).replace("Z", "+00:00"))
            iso = dt.isocalendar()
            wk = f"{iso[0]}-W{iso[1]:02d}"
        except Exception:
            wk = "?"
        w = weekly.setdefault(wk, {"week": wk, "total": 0, "ecarts": 0})
        w["total"] += 1
        w["ecarts"] += 1 if e else 0
        if d == "overridden" and len(recent_overrides) < 15:
            recent_overrides.append({
                "decided_at": r.get("decided_at"), "ao_id": aid,
                "ai_rank": r.get("ai_rank"), "ai_score": r.get("ai_score"),
                "justification": (r.get("justification") or "")[:400],
            })

    ao_titles, op_names = {}, {}
    if ao_ids:
        try:
            for a in supabase.table("appels_offres").select("id, title").in_("id", list(ao_ids)).execute().data or []:
                ao_titles[a["id"]] = a.get("title")
        except Exception:
            pass
    if op_ids:
        try:
            for p in supabase.table("profiles").select("id, name, email").in_("id", list(op_ids)).execute().data or []:
                op_names[p["id"]] = p.get("name") or p.get("email")
        except Exception:
            pass

    for a in by_ao.values():
        a["title"] = ao_titles.get(a["ao_id"]) or "—"
    for o in by_op.values():
        o["name"] = op_names.get(o["id"]) or "—"
        o["override_rate"] = round(o["overrides"] / o["total"] * 100) if o["total"] else 0
    for ro in recent_overrides:
        ro["ao_title"] = ao_titles.get(ro["ao_id"]) or "—"

    total_ecarts = sum(ecarts.values())
    return {
        "period_days": days,
        "totals": totals,
        "override_rate": round(totals["overridden"] / totals["total"] * 100) if totals["total"] else 0,
        "ecarts": {**ecarts, "total": total_ecarts,
                   "rate": round(total_ecarts / totals["total"] * 100) if totals["total"] else 0},
        "by_ao": sorted(by_ao.values(), key=lambda x: (x["ecarts"], x["total"]), reverse=True)[:8],
        "by_operator": sorted(by_op.values(), key=lambda x: x["total"], reverse=True)[:8],
        "weekly": [weekly[k] for k in sorted(weekly.keys()) if k != "?"],
        "recent_overrides": recent_overrides,
    }


@router.get("/ao-outcomes")
async def ao_outcomes(days: int = 180, user: dict = Depends(require_admin)):
    """Bilan des AO clôturés (issue posée) sur la fenêtre : répartition pourvu /
    non pourvu / sans suite, taux de pourvu, résultats par partenaire gagnant, et
    tendance hebdo. Alimente la Supervision. Admin only. Dégradation propre si la
    migration 0004 n'est pas appliquée (stats vides, jamais de 500)."""
    days = max(7, min(int(days or 180), 730))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    empty = {"available": False, "period_days": days, "total": 0,
             "by_outcome": {"pourvu": 0, "non_pourvu": 0, "sans_suite": 0},
             "pourvu_rate": 0, "by_partner": [], "weekly": [], "to_close": []}
    try:
        rows = supabase.table("appels_offres").select(
            "id, ao_outcome, winning_partner_id, outcome_at"
        ).not_.is_("ao_outcome", "null").gte("outcome_at", since).execute().data or []
    except Exception:
        return empty  # colonnes d'issue absentes ou erreur transitoire

    by_outcome = {"pourvu": 0, "non_pourvu": 0, "sans_suite": 0}
    by_partner: dict = {}
    weekly: dict = {}
    for r in rows:
        o = r.get("ao_outcome")
        if o in by_outcome:
            by_outcome[o] += 1
        pid = r.get("winning_partner_id")
        if o == "pourvu" and pid:
            by_partner[pid] = by_partner.get(pid, 0) + 1
        try:
            dt = datetime.fromisoformat(str(r.get("outcome_at")).replace("Z", "+00:00"))
            iso = dt.isocalendar()
            wk = f"{iso[0]}-W{iso[1]:02d}"
        except Exception:
            wk = "?"
        w = weekly.setdefault(wk, {"week": wk, "pourvu": 0, "total": 0})
        w["total"] += 1
        if o == "pourvu":
            w["pourvu"] += 1

    total = sum(by_outcome.values())
    names: dict = {}
    if by_partner:
        try:
            for p in supabase.table("profiles").select("id, name, email").in_(
                    "id", list(by_partner)).execute().data or []:
                names[p["id"]] = p.get("name") or p.get("email")
        except Exception:
            pass
    partners = sorted(
        [{"id": pid, "name": names.get(pid) or "—", "wins": n} for pid, n in by_partner.items()],
        key=lambda x: x["wins"], reverse=True)[:10]

    # « À clôturer » : AO archivés (échéance passée) SANS bilan posé. Sans ça, ils
    # n'alimentent ni le pipeline ni ces stats -> on les remonte pour inciter à
    # les clôturer. Best-effort (colonnes 0003/0004 requises).
    to_close = []
    try:
        rows2 = supabase.table("appels_offres").select(
            "id, title, reference, archived_at, clients(name)"
        ).eq("archived", True).is_("ao_outcome", "null").order(
            "archived_at", desc=True).limit(30).execute().data or []
        to_close = [{
            "id": r["id"], "title": r.get("title"), "reference": r.get("reference"),
            "archived_at": r.get("archived_at"),
            "client_name": (r.get("clients") or {}).get("name") if isinstance(r.get("clients"), dict) else None,
        } for r in rows2]
    except Exception:
        to_close = []

    return {
        "available": True,
        "period_days": days,
        "total": total,
        "by_outcome": by_outcome,
        "pourvu_rate": round(by_outcome["pourvu"] / total * 100) if total else 0,
        "by_partner": partners,
        "weekly": [weekly[k] for k in sorted(weekly.keys()) if k != "?"],
        "to_close": to_close,
    }


@router.get("/kpis")
async def business_kpis(user: dict = Depends(require_admin)):
    """KPIs opérationnels de staffing (Bilan business) sur l'ensemble des données :
      1. Délai de placement (time-to-fill) : diffusion -> envoi client des AO gagnés
      2. Funnel de transformation : diffusés -> soumissions -> proposés -> retenus -> gagnés
      3. Performance partenaire (top ~10) : répondus / soumis / retenus / gagnés
      4. Taux de pourvu : répartition des AO clôturés (pourvu / non pourvu / sans suite)

    Staff only (service-role -> Python est le seul garde d'accès). Chaque sous-métrique
    est isolée dans son propre try/except : une colonne absente (migration non appliquée)
    renvoie un agrégat vide plutôt qu'un 500. Les tables sont chargées une seule fois puis
    agrégées en Python (aucune requête par ligne)."""

    def _parse_dt(v):
        """Parse ISO date/datetime tolérant (None -> None). Normalise en tz-aware
        (UTC) : une valeur naïve mêlée à une valeur tz-aware ferait échouer la
        soustraction et annulerait TOUT le calcul du délai (un seul mauvais row)."""
        if not v:
            return None
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _median(nums):
        s = sorted(nums)
        n = len(s)
        if n == 0:
            return None
        mid = n // 2
        return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2

    # --- Chargement des tables (une passe, colonnes minimales) -----------------
    aos = []
    try:
        aos = supabase.table("appels_offres").select(
            "id, notified_at, created_at, ao_outcome, outcome_at, status, archived, budget_max"
        ).execute().data or []
    except Exception:
        aos = []

    states = []
    try:
        states = supabase.table("ao_consultant_state").select(
            "ao_id, consultant_id, contact_status, validation, sent_to_client_at, deal_status"
        ).execute().data or []
    except Exception:
        states = []

    subs = []
    try:
        subs = supabase.table("submissions").select(
            "id, ao_id, consultant_id, submitted_by"
        ).execute().data or []
    except Exception:
        subs = []

    # Index AO par id (dates de diffusion) pour éviter des lookups répétés
    ao_by_id = {a.get("id"): a for a in aos}

    # --- 1) Délai de placement (time-to-fill) ---------------------------------
    time_to_fill = {"median_days": None, "avg_days": None, "n": 0}
    try:
        deltas = []
        for st in states:
            if st.get("deal_status") != "gagnee":
                continue
            ao = ao_by_id.get(st.get("ao_id"))
            if not ao:
                continue
            start = _parse_dt(ao.get("notified_at")) or _parse_dt(ao.get("created_at"))
            end = _parse_dt(st.get("sent_to_client_at")) or _parse_dt(ao.get("outcome_at"))
            if not start or not end:
                continue
            days = (end - start).total_seconds() / 86400.0
            if days < 0:
                continue  # incohérence de dates : on ignore
            deltas.append(days)
        if deltas:
            med = _median(deltas)
            time_to_fill = {
                "median_days": round(med, 1) if med is not None else None,
                "avg_days": round(sum(deltas) / len(deltas), 1),
                "n": len(deltas),
            }
    except Exception:
        pass

    # --- 2) Funnel de transformation ------------------------------------------
    funnel = {"stages": []}
    try:
        n_diffuses = sum(1 for a in aos if a.get("notified_at"))
        n_soumis = len(subs)
        n_proposes = sum(
            1 for st in states
            if st.get("contact_status") == "proposed" or st.get("sent_to_client_at")
        )
        n_retenus = sum(1 for st in states if st.get("validation") == "retenu")
        n_gagnes = sum(1 for st in states if st.get("deal_status") == "gagnee")

        raw = [
            ("Diffusés", n_diffuses),
            ("CV soumis", n_soumis),
            ("Proposés client", n_proposes),
            ("Retenus", n_retenus),
            ("Gagnés", n_gagnes),
        ]
        stages = []
        for i, (label, count) in enumerate(raw):
            prev = raw[i - 1][1] if i > 0 else None
            conv = round(count / prev * 100) if prev else None  # None si étage précédent = 0
            stages.append({
                "label": label,
                "count": count,
                "conversion_from_prev": conv,  # % vs étage précédent (None pour le 1er)
            })
        funnel = {"stages": stages}
    except Exception:
        funnel = {"stages": []}

    # --- 3) Performance partenaire (role='ao', top ~10 par activité) -----------
    partners = []
    try:
        # AO répondus (par partenaire) : set d'ao_id distincts issus des soumissions
        per_partner: dict = {}

        def _p(pid):
            return per_partner.setdefault(pid, {
                "aos": set(), "soumis": 0, "consultants": set(), "retenus": 0, "gagnes": 0,
            })

        # Consultant -> submitted_by : pour rattacher validation/deal au partenaire.
        # (ao_id, consultant_id) est la clé de jointure avec ao_consultant_state.
        sub_owner: dict = {}
        for s in subs:
            pid = s.get("submitted_by")
            if not pid:
                continue
            rec = _p(pid)
            rec["soumis"] += 1
            if s.get("ao_id"):
                rec["aos"].add(s.get("ao_id"))
            key = (s.get("ao_id"), s.get("consultant_id"))
            sub_owner[key] = pid

        for st in states:
            pid = sub_owner.get((st.get("ao_id"), st.get("consultant_id")))
            if not pid:
                continue
            rec = per_partner.get(pid)
            if not rec:
                continue
            if st.get("validation") == "retenu":
                rec["retenus"] += 1
            if st.get("deal_status") == "gagnee":
                rec["gagnes"] += 1

        # Filtre role='ao' + noms
        names: dict = {}
        ao_ids: set = set()
        if per_partner:
            try:
                for p in supabase.table("profiles").select(
                        "id, name, email, role").in_("id", list(per_partner)).execute().data or []:
                    if p.get("role") == "ao":
                        ao_ids.add(p["id"])
                        names[p["id"]] = p.get("name") or p.get("email") or "—"
            except Exception:
                # Sans profiles on ne peut pas filtrer role='ao' -> on n'affiche rien
                ao_ids = set()

        for pid, rec in per_partner.items():
            if pid not in ao_ids:
                continue
            soumis = rec["soumis"]
            partners.append({
                "id": pid,
                "name": names.get(pid) or "—",
                "aos": len(rec["aos"]),
                "soumis": soumis,
                "retenus": rec["retenus"],
                "gagnes": rec["gagnes"],
                "retention_rate": round(rec["retenus"] / soumis * 100) if soumis else None,
            })
        partners.sort(key=lambda x: (x["gagnes"], x["soumis"]), reverse=True)
        partners = partners[:10]
    except Exception:
        partners = []

    # --- 4) Taux de pourvu -----------------------------------------------------
    pourvu = {"total": 0, "by_outcome": {"pourvu": 0, "non_pourvu": 0, "sans_suite": 0},
              "pourvu_rate": None}
    try:
        by_outcome = {"pourvu": 0, "non_pourvu": 0, "sans_suite": 0}
        for a in aos:
            o = a.get("ao_outcome")
            if o in by_outcome:
                by_outcome[o] += 1
        total = sum(by_outcome.values())
        pourvu = {
            "total": total,
            "by_outcome": by_outcome,
            "pourvu_rate": round(by_outcome["pourvu"] / total * 100) if total else None,
        }
    except Exception:
        pass

    # --- 5) Marge (STAFF-ONLY) : vente - achat sur affaires gagnées ------------
    # Colonnes tjm_achat/tjm_vente issues de la migration 0007 (ao_consultant_state).
    # Repli plafond : achat -> consultants.tjm ; vente -> appels_offres.budget_max.
    # Tout le bloc est best-effort : colonne absente ou table indisponible ->
    # agrégat vide ({}) plutôt qu'un 500. La marge NÉGATIVE est réelle : conservée.
    marge: dict = {}
    try:
        # États avec les colonnes marge (repli sans elles -> agrégat vide)
        try:
            marge_states = supabase.table("ao_consultant_state").select(
                "ao_id, consultant_id, deal_status, sent_to_client_at, tjm_achat, tjm_vente"
            ).execute().data or []
        except Exception:
            marge_states = []

        # TJM d'achat de repli (coût consultant) si tjm_achat non renseigné
        tjm_by_consultant: dict = {}
        try:
            for c in supabase.table("consultants").select("id, tjm").execute().data or []:
                tjm_by_consultant[c.get("id")] = c.get("tjm")
        except Exception:
            tjm_by_consultant = {}

        n = 0
        n_gagnees = 0
        sold_total = 0
        bought_total = 0
        margin_total = 0
        by_month: dict = {}
        for st in marge_states:
            if st.get("deal_status") != "gagnee":
                continue
            ao = ao_by_id.get(st.get("ao_id")) or {}
            # Deal requalifié perdu (AO non pourvu / sans suite) -> hors marge
            if ao.get("ao_outcome") in ("non_pourvu", "sans_suite"):
                continue
            n_gagnees += 1

            bought = st.get("tjm_achat")
            if bought is None:
                bought = tjm_by_consultant.get(st.get("consultant_id"))
            sold = st.get("tjm_vente")
            if sold is None:
                sold = ao.get("budget_max")
            if bought is None or sold is None:
                continue
            try:
                bought = int(bought)
                sold = int(sold)
            except (TypeError, ValueError):
                continue

            margin = sold - bought  # peut être négatif : on garde
            n += 1
            sold_total += sold
            bought_total += bought
            margin_total += margin

            mdt = _parse_dt(st.get("sent_to_client_at")) or _parse_dt(ao.get("outcome_at"))
            mk = mdt.strftime("%Y-%m") if mdt else None
            if mk:
                b = by_month.setdefault(
                    mk, {"month": mk, "n": 0, "sold": 0, "bought": 0, "margin": 0})
                b["n"] += 1
                b["sold"] += sold
                b["bought"] += bought
                b["margin"] += margin

        by_month_list = []
        for mk in sorted(by_month.keys()):
            b = by_month[mk]
            b["margin_pct"] = round(b["margin"] / b["sold"] * 100) if b["sold"] > 0 else None
            by_month_list.append(b)

        marge = {
            "n": n,
            "n_gagnees": n_gagnees,
            "sold_total": sold_total,
            "bought_total": bought_total,
            "margin_total": margin_total,
            "avg_margin_pct": round(margin_total / sold_total * 100) if sold_total > 0 else None,
            "avg_margin_per_deal": round(margin_total / n, 1) if n > 0 else None,
            "by_month": by_month_list,
        }
    except Exception:
        marge = {}

    return {
        "time_to_fill": time_to_fill,
        "funnel": funnel,
        "partners": partners,
        "pourvu": pourvu,
        "marge": marge,
    }


@router.post("/backfill-structured")
async def backfill_structured(background_tasks: BackgroundTasks, limit: int = 50,
                              user: dict = Depends(require_admin)):
    """Backfill du CV structuré (format GRP-IT) pour les soumissions existantes qui
    n'en ont pas encore. Sans lui, un ancien CV n'est structuré qu'à la 1re ouverture
    de sa vue « CV analysé ». Traitement en tâche de fond (best-effort) ; renvoie le
    nombre mis en file. Nécessite la migration 0002 (sinon rien n'est persisté)."""
    from services.cv_structured import build_structured_bg
    try:
        rows = supabase.table("submissions").select(
            "id, cv_structured, cv_text").limit(1000).execute().data or []
        todo = [r["id"] for r in rows if r.get("cv_text") and not r.get("cv_structured")]
    except Exception:
        # Colonne absente (migration non appliquée) : on ne peut pas savoir lesquels
        # manquent — on le signale plutôt que de tout reconstruire en vain.
        raise HTTPException(
            status_code=409,
            detail="Colonne submissions.cv_structured absente — appliquer d'abord migrations/0002_cv_structured.sql.",
        )
    todo = todo[:max(1, min(limit, 200))]
    for sid in todo:
        background_tasks.add_task(build_structured_bg, sid)
    return {"queued": len(todo)}
