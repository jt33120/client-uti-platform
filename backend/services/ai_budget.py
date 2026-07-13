"""
Surveillance du budget IA.

Compare la dépense réelle OpenRouter (clés « plateforme ») aux limites hebdo /
mensuelle configurées par l'admin, et alerte tous les administrateurs par email
quand un palier est atteint (80 % puis 100 %).

Alerte SEULEMENT — l'IA n'est jamais coupée. Anti-spam : au plus une alerte par
palier et par période (semaine ISO pour l'hebdo, mois calendaire pour le mensuel).
L'état d'alerte est gardé dans `app_settings` (clé `ai_budget_alerts`).

Appelé à chaque tick du planificateur (horaire) — cf. services.scheduler.
"""
from datetime import datetime

import httpx

from config import settings
from services.supabase_client import supabase
from services.app_settings import get_ai_budget_settings, get_setting, set_setting
from services.email import send_email, render_email_html
from services.error_log import record as _record_err

_ALERT_STATE_KEY = "ai_budget_alerts"
_THRESHOLDS = (80, 100)  # paliers d'alerte (% du budget)
_OR_BASE = "https://openrouter.ai/api/v1"


def fetch_platform_cost() -> dict | None:
    """Coût réel des clés « plateforme » (daily/weekly/monthly/total), source
    facturation OpenRouter. None si pas de clé de provisioning ou appel en échec."""
    prov = settings.openrouter_provisioning_key
    if not prov:
        return None
    try:
        # Import paresseux : évite un cycle admin ↔ ai_budget à l'import.
        from routers.admin import _supervised_fragments, _is_platform_key
        with httpx.Client(timeout=12) as client:
            r = client.get(f"{_OR_BASE}/keys", headers={"Authorization": f"Bearer {prov}"})
            if r.status_code >= 400:
                return None
            klist = (r.json() or {}).get("data") or []
        frags = _supervised_fragments()
        shown = [k for k in klist if _is_platform_key(k.get("name"), frags)] or klist
        def _sum(field: str) -> float:
            return round(sum(float(k.get(field) or 0) for k in shown), 4)
        return {
            "daily": _sum("usage_daily"),
            "weekly": _sum("usage_weekly"),
            "monthly": _sum("usage_monthly"),
            "total": _sum("usage"),
        }
    except Exception as e:  # noqa: BLE001
        print(f"[BUDGET] lecture coût plateforme échouée: {e}")
        return None


def _period_ids(now: datetime) -> dict:
    """Identifiants de période pour l'anti-spam (reset au changement de période)."""
    iso = now.isocalendar()
    return {"weekly": f"{iso[0]}-W{iso[1]:02d}", "monthly": now.strftime("%Y-%m")}


def _admin_recipients() -> list[dict]:
    """Tous les administrateurs (profiles.role == 'admin'), repli sur ADMIN_EMAIL."""
    try:
        rows = supabase.table("profiles").select("email, name").eq("role", "admin").execute().data or []
        out = [r for r in rows if r.get("email")]
        if out:
            return out
    except Exception as e:  # noqa: BLE001
        print(f"[BUDGET] lecture des admins échouée: {e}")
    if settings.admin_email:
        return [{"email": settings.admin_email, "name": None}]
    return []


def _send_alert(period: str, threshold: int, spend: float, limit: float, pct: float) -> bool:
    """Envoie l'alerte à tous les admins. True si au moins un email est parti."""
    recipients = _admin_recipients()
    if not recipients:
        _record_err("budget",
                    f"Seuil budget IA {threshold}% ({period}) atteint mais aucun admin à notifier",
                    level="warning")
        return False
    period_fr = "hebdomadaire" if period == "weekly" else "mensuel"
    reached = "dépassé" if pct >= 100 else "atteint"
    subject = f"⚠️ Budget IA {period_fr} — seuil {threshold}% {reached} (${spend:.2f} / ${limit:.2f})"
    body = (
        f"<p>La consommation IA <strong>{period_fr}</strong> de la plateforme a "
        f"{reached} <strong>{pct:.0f}%</strong> du budget défini.</p>"
        f"<p style=\"font-size:15px;margin:14px 0\">"
        f"Dépense : <strong>${spend:.2f}</strong> &nbsp;/&nbsp; budget ${limit:.2f}</p>"
        f"<p style=\"color:#6b7280\">Alerte automatique. L'IA continue de fonctionner "
        f"normalement — ajustez le budget ou surveillez la consommation depuis la Supervision.</p>"
    )
    html_body = render_email_html(
        title=f"Budget IA {period_fr} — seuil {threshold}%",
        body_html=body,
        cta={"label": "Ouvrir la Supervision", "url": f"{settings.frontend_url.rstrip('/')}/supervision?tab=ia"},
        footer_note="Vous recevez cet email en tant qu'administrateur de la plateforme UTI.",
    )
    ok_any = False
    for r in recipients:
        ok, _err = send_email(r["email"], subject, html_body)
        ok_any = ok_any or ok
    return ok_any


async def process_ai_budget(now: datetime) -> None:
    """Vérifie les budgets hebdo/mensuel et alerte au franchissement d'un palier."""
    cfg = get_ai_budget_settings()
    if not cfg.get("enabled"):
        return
    limits = {"weekly": float(cfg.get("weekly_usd") or 0), "monthly": float(cfg.get("monthly_usd") or 0)}
    if limits["weekly"] <= 0 and limits["monthly"] <= 0:
        return  # aucune limite active
    cost = fetch_platform_cost()
    if not cost:
        return  # coût indisponible → on ne peut rien conclure

    pids = _period_ids(now)
    state = get_setting(_ALERT_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
    changed = False

    for period in ("weekly", "monthly"):
        limit = limits[period]
        if limit <= 0:
            continue
        spend = float(cost.get(period) or 0)
        pct = (spend / limit) * 100 if limit else 0
        crossed = max([t for t in _THRESHOLDS if pct >= t], default=None)
        if crossed is None:
            continue
        st = state.get(period) or {}
        if st.get("period_id") != pids[period]:
            st = {"period_id": pids[period], "last_threshold": 0}  # nouvelle période → reset
        if crossed <= int(st.get("last_threshold") or 0):
            continue  # déjà alerté à ce palier (ou plus haut) pour cette période
        if _send_alert(period, crossed, spend, limit, pct):
            st["last_threshold"] = crossed
            state[period] = st
            changed = True
            print(f"[BUDGET] alerte {period} {crossed}% envoyée (${spend:.2f}/${limit:.2f})")

    if changed:
        try:
            set_setting(_ALERT_STATE_KEY, state)
        except Exception as e:  # noqa: BLE001
            _record_err("budget", "Sauvegarde de l'état d'alerte budget IA échouée", exc=e)
