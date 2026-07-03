"""
Planificateur interne (asyncio) — service uvicorn mono-worker.

À chaque tick (horaire) :
  * envoie la « liste 2 » des AO dont l'échéance est atteinte ;
  * effectue les relances automatiques selon les réglages admin.

Tout est best-effort et borné par les réglages (services.app_settings). Démarré
au startup de l'app (main.py). Mono-worker → pas de double-envoi ; on pose quand
même un « claim » par horodatage avant l'envoi pour rester robuste.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from services.supabase_client import supabase
from services import notifications
from services.app_settings import get_notification_settings
from services.error_log import record as _record_err

TICK_SECONDS = 3600  # 1 h — granularité largement suffisante pour des délais en jours


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


async def _process_due_list2(now: datetime) -> None:
    """Envoie la liste 2 des AO dont l'échéance est passée et pas encore envoyée."""
    try:
        rows = supabase.table("appels_offres").select(
            "*, clients(name)"
        ).eq("status", "open").is_("list2_notified_at", "null").lte(
            "list2_scheduled_at", now.isoformat()
        ).execute().data or []
    except Exception as e:  # noqa: BLE001
        print(f"[SCHED] lecture liste 2 due échouée: {e}")
        return

    for ao in rows:
        # Claim : pose l'horodatage AVANT l'envoi pour éviter tout double-envoi.
        try:
            claimed = supabase.table("appels_offres").update(
                {"list2_notified_at": now.isoformat()}
            ).eq("id", ao["id"]).is_("list2_notified_at", "null").execute().data
        except Exception as e:  # noqa: BLE001
            print(f"[SCHED] claim liste 2 AO {ao['id']} échoué: {e}")
            continue
        if not claimed:
            continue  # déjà pris
        try:
            n = notifications.notify_tier(ao, "list_2")
        except Exception as e:  # noqa: BLE001
            n = 0
            print(f"[SCHED] envoi liste 2 AO {ao['id']} en erreur: {e}")
        if n == 0:
            # Rien n'est parti (SMTP down, zéro destinataire…) : on relâche le
            # claim pour que le prochain tick retente — sinon la liste 2 de cet
            # AO ne partirait JAMAIS. (Ré-essai sans double-envoi : soit tout a
            # échoué, soit il n'y avait personne à notifier.)
            try:
                supabase.table("appels_offres").update(
                    {"list2_notified_at": None}
                ).eq("id", ao["id"]).execute()
            except Exception as e:  # noqa: BLE001
                _record_err("scheduler", f"Liste 2 AO {ao['id']} : envoi ET libération du claim en échec — liste 2 bloquée", exc=e)
            else:
                _record_err("scheduler", f"Liste 2 AO {ao['id']} : aucun e-mail parti, nouvel essai au prochain tick", level="warning")
            continue
        print(f"[SCHED] AO {ao['id']} — liste 2 envoyée à {n} partenaire(s)")


async def _process_relances(now: datetime, cfg: dict) -> None:
    """Relance automatique des AO ouverts selon la fréquence / le max configurés."""
    if not cfg.get("relance_auto_enabled"):
        return
    interval = timedelta(days=cfg["relance_interval_days"])
    max_relances = cfg["relance_max"]
    try:
        rows = supabase.table("appels_offres").select(
            "*, clients(name)"
        ).eq("status", "open").not_.is_("notified_at", "null").execute().data or []
    except Exception as e:  # noqa: BLE001
        print(f"[SCHED] lecture relances échouée: {e}")
        return

    for ao in rows:
        if (ao.get("relance_count") or 0) >= max_relances:
            continue
        last = _parse(ao.get("last_relance_at")) or _parse(ao.get("notified_at"))
        if last is None or (now - last) < interval:
            continue
        # Claim d'abord (compteur + horodatage), envoi ensuite. Dans l'autre
        # ordre, un update en échec après l'envoi ferait re-relancer les mêmes
        # partenaires à CHAQUE tick (spam) ; ici le pire cas est une relance
        # sautée, rattrapée à l'intervalle suivant.
        try:
            supabase.table("appels_offres").update({
                "last_relance_at": now.isoformat(),
                "relance_count": (ao.get("relance_count") or 0) + 1,
            }).eq("id", ao["id"]).execute()
        except Exception as e:  # noqa: BLE001
            print(f"[SCHED] claim relance AO {ao['id']} échoué: {e}")
            continue
        try:
            n = notifications.relance(ao, only_pending=True)
        except Exception as e:  # noqa: BLE001
            _record_err("scheduler", f"Relance auto AO {ao['id']} : envoi en échec", exc=e)
            continue
        print(f"[SCHED] AO {ao['id']} — relance auto envoyée à {n} partenaire(s)")


async def _tick() -> None:
    cfg = get_notification_settings()
    if not cfg.get("enabled"):
        return
    now = datetime.now(timezone.utc)
    await _process_due_list2(now)
    await _process_relances(now, cfg)


async def run_scheduler() -> None:
    """Boucle de fond. Lancée au startup ; ne lève jamais (chaque tick est protégé)."""
    print("[SCHED] planificateur de notifications démarré")
    while True:
        try:
            await _tick()
        except Exception as e:  # noqa: BLE001
            print(f"[SCHED] tick en erreur (ignoré): {e}")
            _record_err("scheduler", "Tick du planificateur en erreur", exc=e)
        await asyncio.sleep(TICK_SECONDS)
