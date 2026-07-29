"""
Conservation des données / purge RGPD (minimisation, art. 5-1-e).

Passé le délai de conservation configuré (depuis la dernière activité = date de
soumission), on ANONYMISE les CV : suppression du fichier stocké + effacement des
textes (cv_text, cv_structured) et des références (cv_url, cv_filename). La ligne
`submissions` est CONSERVÉE (privée de tout contenu personnel) pour ne pas fausser
les statistiques agrégées.

Le RETOUR CLIENT en texte libre (ao_consultant_state.client_decision_note) suit la
MÊME politique : il peut contenir de la PII (nom, appréciation nominative), on le
vide donc pour la même paire (ao, consultant). On conserve client_decision (enum non
identifiant) et client_decision_at pour ne pas fausser les statistiques.

OPT-IN strict : rien n'est purgé tant que l'admin n'a pas activé la rétention
(services.app_settings.get_retention_settings). Best-effort et borné : un lot
maximum par tick, jamais d'exception propagée (le planificateur isole déjà, mais
on double la prudence sur une opération destructive).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

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
    # 3. Retour client en texte libre (PII potentielle) sur la même paire (ao, consultant).
    #    Best-effort : colonne/table absente (migration en retard) → on ignore.
    ao_id, cid = sub.get("ao_id"), sub.get("consultant_id")
    if ao_id and cid:
        try:
            supabase.table("ao_consultant_state").update(
                {"client_decision_note": None}
            ).eq("ao_id", ao_id).eq("consultant_id", cid).execute()
        except Exception:
            pass
    return True


#: Valeur posée sur `consultants.name` après anonymisation. Un libellé explicite
#: vaut mieux qu'une chaîne vide : l'écran reste lisible et l'opérateur comprend
#: qu'il s'agit d'une purge et non d'une donnée manquante.
ANON_NAME = "Consultant anonymisé"


def _purge_consultant(cid: str, now_iso: str) -> bool:
    """Vide les champs identifiants d'une fiche consultant. La LIGNE est conservée.

    Supprimer la ligne cascaderait sur `matchings` et `ao_consultant_state`, et
    mettrait à NULL le `consultant_id` de `human_decision` — la trace de décision
    humaine (AI Act art. 14) serait perdue et les statistiques faussées.
    On conserve donc les champs non identifiants (TJM, compétences, années d'XP,
    type d'emploi) qui portent la valeur analytique.
    """
    cleared = {
        "name": ANON_NAME,
        "email": None, "phone": None, "city": None,
        "latitude": None, "longitude": None,
        "cv_url": None, "cv_text": None, "cv_filename": None,
    }
    supabase.table("consultants").update({**cleared, "purged_at": now_iso}).eq("id", cid).execute()
    return True


def _process_consultants(now: datetime, cutoff: str) -> int:
    """Anonymise les consultants dont la dernière activité dépasse le délai.

    Dernière activité = la plus récente entre la création de la fiche et sa
    dernière soumission. Une fiche jamais soumise se juge donc sur sa seule date
    de création — c'est le cas que `supabase_schema.sql` annonçait sans
    l'implémenter.
    """
    rows = (
        supabase.table("consultants")
        .select("id, created_at")
        .is_("purged_at", "null")
        .lt("created_at", cutoff)
        .order("created_at")
        .limit(_BATCH)
        .execute()
        .data
        or []
    )
    if not rows:
        return 0

    ids = [r["id"] for r in rows if r.get("id")]
    # Dernière soumission par consultant, en UNE requête (pas de N+1).
    last_sub: dict[str, str] = {}
    try:
        subs = (
            supabase.table("submissions")
            .select("consultant_id, submitted_at")
            .in_("consultant_id", ids)
            .execute()
            .data
            or []
        )
        for s in subs:
            cid, ts = s.get("consultant_id"), s.get("submitted_at")
            if cid and ts and ts > last_sub.get(cid, ""):
                last_sub[cid] = ts
    except Exception as e:  # noqa: BLE001
        # Sans cette lecture, impossible de savoir si la fiche est réellement
        # inactive : on s'abstient plutôt que de purger à l'aveugle.
        _record_err("retention", "Lecture des soumissions (purge consultants) en échec", exc=e)
        return 0

    now_iso = now.isoformat()
    purged = 0
    for r in rows:
        cid = r.get("id")
        if not cid:
            continue
        if last_sub.get(cid, "") >= cutoff:
            continue  # encore actif via une soumission récente
        try:
            if _purge_consultant(cid, now_iso):
                purged += 1
        except Exception as e:  # noqa: BLE001
            _record_err("retention", f"Purge consultant {cid} en échec", exc=e, level="warning")
    return purged


def _cutoff(now: datetime, months: int) -> str:
    return (now - timedelta(days=int(months) * 30)).isoformat()


def retention_state(now: Optional[datetime] = None) -> dict:
    """Ce que la rétention ferait, activée ou non — pour rendre l'inaction VISIBLE.

    La purge est en opt-in strict, et rien n'indiquait à l'admin qu'elle était
    à l'arrêt : le réglage par défaut (`enabled: false`) la rendait silencieusement
    inerte. On expose donc le nombre d'enregistrements qui DÉPASSENT déjà le délai
    configuré, que la purge tourne ou non.
    """
    now = now or datetime.now(timezone.utc)
    cfg = get_retention_settings()
    cutoff = _cutoff(now, cfg["months"])
    out = {"enabled": bool(cfg["enabled"]), "months": cfg["months"], "cutoff": cutoff}

    def _count(builder) -> Optional[int]:
        try:
            return builder.execute().count
        except Exception:  # noqa: BLE001 - la visibilité ne doit jamais casser l'écran
            return None

    out["overdue_submissions"] = _count(
        supabase.table("submissions").select("id", count="exact")
        .lt("submitted_at", cutoff)
        .or_("cv_url.not.is.null,cv_text.not.is.null")
        .limit(1)
    )
    out["overdue_consultants"] = _count(
        supabase.table("consultants").select("id", count="exact")
        .is_("purged_at", "null")
        .lt("created_at", cutoff)
        .limit(1)
    )
    return out


async def process_data_retention(now: datetime) -> dict:
    """Purge les CV hors délai si la rétention est activée. Ne lève jamais."""
    cfg = get_retention_settings()
    if not cfg.get("enabled"):
        return {"purged": 0, "status": "disabled"}

    cutoff = _cutoff(now, cfg["months"])
    try:
        # Candidats : soumissions anciennes détenant encore un CV. On récupère un
        # lot, puis on filtre en Python celles qui portent réellement du contenu.
        # IMPORTANT : ne récupérer QUE les soumissions détenant encore un CV.
        # Sans ce filtre serveur, les lignes déjà purgées (cv_* = null) mais
        # toujours anciennes occupent en permanence la fenêtre des 200 plus vieilles
        # → la purge n'avancerait plus jamais dès qu'il y a ≥ _BATCH lignes sans CV.
        rows = (
            supabase.table("submissions")
            .select("id, cv_url, cv_text, submitted_at, ao_id, consultant_id")
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

    # Fiches consultants inactives — isolé du reste : un échec ici ne doit pas
    # annuler l'anonymisation des CV qui vient d'aboutir.
    try:
        purged_consultants = _process_consultants(now, cutoff)
    except Exception as e:  # noqa: BLE001
        _record_err("retention", "Purge des consultants inactifs en échec", exc=e)
        purged_consultants = 0

    if purged or purged_consultants:
        _record_err(
            "retention",
            f"Purge RGPD : {purged} CV anonymisé(s), {purged_consultants} fiche(s) "
            f"consultant anonymisée(s) (conservation {cfg['months']} mois)",
            level="info",
        )
    return {
        "purged": purged,
        "purged_consultants": purged_consultants,
        "status": "ok",
        "months": cfg["months"],
    }
