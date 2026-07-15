"""
Conservation des données / purge RGPD (minimisation, art. 5-1-e).

Passé le délai de conservation configuré (depuis la dernière activité = date de
soumission), on ANONYMISE les CV : suppression du fichier stocké + effacement des
textes (cv_text, cv_structured) et des références (cv_url, cv_filename). La ligne
`submissions` est CONSERVÉE (privée de tout contenu personnel) pour ne pas fausser
les statistiques agrégées.

OPT-IN strict : rien n'est purgé tant que l'admin n'a pas activé la rétention
(services.app_settings.get_retention_settings). Best-effort et borné : un lot
maximum par tick, jamais d'exception propagée (le planificateur isole déjà, mais
on double la prudence sur une opération destructive).
"""
from datetime import datetime, timedelta

from services.supabase_client import supabase
from services.app_settings import get_retention_settings
from services.error_log import record as _record_err
from services import storage

# Garde-fou : au plus N soumissions purgées par tick (évite un balayage massif
# et bloquant si la rétention est activée sur une base historique volumineuse).
_BATCH = 200


def _purge_one(sub: dict, now_iso: str) -> bool:
    """Anonymise une soumission. Retourne True si la ligne a été nettoyée."""
    sid = sub.get("id")
    if not sid:
        return False
    # 1. Fichier stocké (best-effort — l'échec ne bloque pas l'effacement DB).
    cv_url = sub.get("cv_url")
    if cv_url:
        try:
            storage.remove("cvs", [storage._object_path("cvs", cv_url)])
        except Exception as e:  # noqa: BLE001
            _record_err("retention", f"Suppression fichier CV {sid} en échec", exc=e, level="warning")
    # 2. Effacement des champs personnels. `purged_at` en best-effort (colonne
    #    optionnelle : si absente, on nettoie quand même le contenu).
    cleared = {"cv_url": None, "cv_filename": None, "cv_text": None, "cv_structured": None}
    try:
        supabase.table("submissions").update({**cleared, "purged_at": now_iso}).eq("id", sid).execute()
    except Exception:
        supabase.table("submissions").update(cleared).eq("id", sid).execute()
    return True


async def process_data_retention(now: datetime) -> dict:
    """Purge les CV hors délai si la rétention est activée. Ne lève jamais."""
    cfg = get_retention_settings()
    if not cfg.get("enabled"):
        return {"purged": 0, "status": "disabled"}

    cutoff = (now - timedelta(days=int(cfg["months"]) * 30)).isoformat()
    try:
        # Candidats : soumissions anciennes détenant encore un CV. On récupère un
        # lot, puis on filtre en Python celles qui portent réellement du contenu.
        # IMPORTANT : ne récupérer QUE les soumissions détenant encore un CV.
        # Sans ce filtre serveur, les lignes déjà purgées (cv_* = null) mais
        # toujours anciennes occupent en permanence la fenêtre des 200 plus vieilles
        # → la purge n'avancerait plus jamais dès qu'il y a ≥ _BATCH lignes sans CV.
        rows = (
            supabase.table("submissions")
            .select("id, cv_url, cv_text, submitted_at")
            .lt("submitted_at", cutoff)
            .or_("cv_url.not.is.null,cv_text.not.is.null")
            .order("submitted_at")
            .limit(_BATCH)
            .execute()
            .data
            or []
        )
    except Exception as e:  # noqa: BLE001
        _record_err("retention", "Lecture des soumissions à purger en échec", exc=e)
        return {"purged": 0, "status": "error"}

    to_purge = [r for r in rows if r.get("cv_url") or r.get("cv_text")]
    now_iso = now.isoformat()
    purged = 0
    for sub in to_purge:
        try:
            if _purge_one(sub, now_iso):
                purged += 1
        except Exception as e:  # noqa: BLE001
            _record_err("retention", f"Purge soumission {sub.get('id')} en échec", exc=e, level="warning")

    if purged:
        _record_err(
            "retention",
            f"Purge RGPD : {purged} CV anonymisé(s) (conservation {cfg['months']} mois)",
            level="info",
        )
    return {"purged": purged, "status": "ok", "months": cfg["months"]}
