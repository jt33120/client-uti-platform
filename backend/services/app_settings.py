"""
Réglages applicatifs globaux, pilotés par l'admin depuis la plateforme.

Stockés dans la table `app_settings` (clé → valeur JSON). Best-effort : si la
table n'existe pas encore, on retombe sur les valeurs par défaut.
"""
from typing import Any
from services.supabase_client import supabase

# Réglages des notifications partenaires + relances (tous éditables par l'admin).
NOTIFICATION_DEFAULTS: dict[str, Any] = {
    "enabled": True,            # envoi des notifications activé
    "list2_delay_days": 2,      # délai (jours) entre l'envoi liste 1 et liste 2 (48 h)
    "relance_auto_enabled": False,  # relance automatique des partenaires
    "relance_interval_days": 7,     # fréquence des relances automatiques
    "relance_max": 2,               # nombre maximum de relances automatiques
}

_NOTIF_KEY = "notifications"


def get_setting(key: str, default: Any = None) -> Any:
    try:
        rows = supabase.table("app_settings").select("value").eq("key", key).limit(1).execute().data or []
        if rows:
            return rows[0].get("value")
    except Exception as e:  # noqa: BLE001
        print(f"[SETTINGS] lecture '{key}' indisponible, défaut utilisé: {e}")
    return default


def set_setting(key: str, value: Any) -> None:
    supabase.table("app_settings").upsert({"key": key, "value": value}).execute()


def _coerce_notifications(raw: Any) -> dict:
    """Fusionne les valeurs stockées avec les défauts + borne les valeurs."""
    cfg = dict(NOTIFICATION_DEFAULTS)
    if isinstance(raw, dict):
        for k in NOTIFICATION_DEFAULTS:
            if raw.get(k) is not None:
                cfg[k] = raw[k]
    cfg["enabled"] = bool(cfg["enabled"])
    cfg["relance_auto_enabled"] = bool(cfg["relance_auto_enabled"])
    cfg["list2_delay_days"] = max(0, int(cfg["list2_delay_days"]))
    cfg["relance_interval_days"] = max(1, int(cfg["relance_interval_days"]))
    cfg["relance_max"] = max(0, int(cfg["relance_max"]))
    return cfg


def get_notification_settings() -> dict:
    """Réglages de notification effectifs (défauts + surcharges admin)."""
    return _coerce_notifications(get_setting(_NOTIF_KEY))


def set_notification_settings(patch: dict) -> dict:
    """Applique une mise à jour partielle des réglages de notification."""
    current = get_notification_settings()
    current.update({k: patch[k] for k in NOTIFICATION_DEFAULTS if k in patch})
    cfg = _coerce_notifications(current)
    set_setting(_NOTIF_KEY, cfg)
    return cfg


# ── Budget IA (USD) ────────────────────────────────────────────────
# Limites de dépense IA hebdo/mensuelle. À 80 % puis 100 %, un email part aux
# admins (services.ai_budget). Alerte seulement : l'IA n'est jamais coupée.
# Une limite à 0 = surveillance désactivée pour cette période.
AI_BUDGET_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "weekly_usd": 0.0,
    "monthly_usd": 0.0,
}

_AI_BUDGET_KEY = "ai_budget"


def _coerce_ai_budget(raw: Any) -> dict:
    cfg = dict(AI_BUDGET_DEFAULTS)
    if isinstance(raw, dict):
        for k in AI_BUDGET_DEFAULTS:
            if raw.get(k) is not None:
                cfg[k] = raw[k]
    cfg["enabled"] = bool(cfg["enabled"])
    cfg["weekly_usd"] = round(max(0.0, float(cfg["weekly_usd"])), 2)
    cfg["monthly_usd"] = round(max(0.0, float(cfg["monthly_usd"])), 2)
    return cfg


def get_ai_budget_settings() -> dict:
    """Budgets IA effectifs (défauts + surcharges admin)."""
    return _coerce_ai_budget(get_setting(_AI_BUDGET_KEY))


def set_ai_budget_settings(patch: dict) -> dict:
    """Applique une mise à jour partielle des budgets IA."""
    current = get_ai_budget_settings()
    current.update({k: patch[k] for k in AI_BUDGET_DEFAULTS if k in patch})
    cfg = _coerce_ai_budget(current)
    set_setting(_AI_BUDGET_KEY, cfg)
    return cfg
