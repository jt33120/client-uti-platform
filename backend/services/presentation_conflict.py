"""
Détection des « conflits de présentation » (conflit multi-partenaires).

Piège classique du portage/staffing : la MÊME personne réelle est présentée
par DEUX partenaires différents (deux lignes `consultants` distinctes, avec des
noms proches à la faute de frappe près) — sur le même AO, ou sur un autre AO
actif du même client. C'est un conflit commercial/juridique de propriété du
profil : le staff doit en être AVERTI.

Ce module est un DRAPEAU CONSULTATIF (advisory). Il ne bloque rien et ne doit
JAMAIS faire échouer l'endpoint de résultats : en cas d'erreur (table absente,
schéma partiel…) on renvoie {} (fail-open « pas de conflit »), après avoir
journalisé via services.error_log si disponible.

La détection de similarité de noms reprend EXACTEMENT la normalisation + le
coefficient de Dice sur bigrammes du front (`frontend/src/lib/similarity.js`),
réimplémentés ici en Python (pas d'import JS).
"""
from __future__ import annotations

import unicodedata

from services.supabase_client import supabase

# Seuil « même personne » sur noms complets normalisés. Un match exact du nom
# normalisé est considéré comme certain ; sinon Dice ≥ SIMILARITY_THRESHOLD.
SIMILARITY_THRESHOLD = 0.82


def normalize_name(s: str | None) -> str:
    """Minuscules, sans accents, sans ponctuation/espaces. Mirroir de
    `normalizeName` (similarity.js)."""
    if not s:
        return ""
    # NFD puis suppression des marques diacritiques (accents).
    decomposed = unicodedata.normalize("NFD", s.lower())
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return "".join(c for c in without_accents if c.isalnum() and c.isascii())


def _bigrams(s: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in range(len(s) - 1):
        g = s[i : i + 2]
        counts[g] = counts.get(g, 0) + 1
    return counts


def dice_coefficient(a: str | None, b: str | None) -> float:
    """Coefficient de Dice ∈ [0,1] ; 1 = identique. Mirroir de `diceCoefficient`."""
    na = normalize_name(a)
    nb = normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) < 2 or len(nb) < 2:
        return 1.0 if na == nb else 0.0
    ba = _bigrams(na)
    bb = _bigrams(nb)
    inter = 0
    for g, count in ba.items():
        if g in bb:
            inter += min(count, bb[g])
    total = (len(na) - 1) + (len(nb) - 1)
    return (2 * inter) / total


def _same_person(name_a: str | None, name_b: str | None) -> bool:
    """Vrai si les deux noms désignent vraisemblablement la même personne
    (nom normalisé identique, ou Dice ≥ seuil)."""
    na, nb = normalize_name(name_a), normalize_name(name_b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return dice_coefficient(name_a, name_b) >= SIMILARITY_THRESHOLD


def _consultant_names(consultant_ids: list[str]) -> dict[str, str]:
    """id consultant → nom (en bloc, pas de requête par ligne)."""
    ids = [c for c in {cid for cid in consultant_ids} if c]
    if not ids:
        return {}
    rows = supabase.table("consultants").select("id, name").in_("id", ids).execute().data or []
    return {r["id"]: r.get("name") for r in rows}


def _partner_names(partner_ids: list[str]) -> dict[str, str]:
    """id profil (submitted_by) → nom du partenaire (en bloc)."""
    ids = [p for p in {pid for pid in partner_ids} if p]
    if not ids:
        return {}
    rows = supabase.table("profiles").select("id, name").in_("id", ids).execute().data or []
    return {r["id"]: r.get("name") for r in rows}


def find_conflicts(ao_id: str) -> dict[str, dict]:
    """
    Retourne une map { consultant_id → info_conflit } pour les consultants de CET
    AO qui sont AUSSI présentés par un partenaire DIFFÉRENT.

    Un conflit existe si, pour une soumission sur cet AO par le partenaire P pour
    le consultant C (nom N), il existe une AUTRE soumission (sur le même AO OU sur
    un autre AO actif du MÊME client) par un partenaire DIFFÉRENT P' pour un
    consultant C' dont le nom normalisé correspond à N (exact ou Dice ≥ 0.82).

    Forme de l'info conflit :
      {
        "conflict": True,
        "with_partners": [noms des autres partenaires…],
        "scope": "same_ao" | "same_client",
        "sample_ao_id": <id d'un AO où le profil concurrent a été trouvé>,
      }

    Fail-open : renvoie {} sur toute erreur (jamais d'exception).
    """
    try:
        # 1. Soumissions de CET AO.
        this_ao_subs = supabase.table("submissions").select(
            "id, consultant_id, submitted_by"
        ).eq("ao_id", ao_id).execute().data or []
        if not this_ao_subs:
            return {}

        # 2. Client de l'AO → AOs frères actifs (mêmes client, hors archivés/brouillons).
        sibling_ao_ids: list[str] = []
        try:
            ao_row = supabase.table("appels_offres").select("id, client_id").eq(
                "id", ao_id
            ).single().execute().data
        except Exception:
            ao_row = None
        client_id = (ao_row or {}).get("client_id")
        if client_id:
            try:
                sib_rows = supabase.table("appels_offres").select(
                    "id, archived, is_draft"
                ).eq("client_id", client_id).execute().data or []
            except Exception:
                # Colonnes récentes (archived/is_draft) possiblement absentes → repli.
                sib_rows = supabase.table("appels_offres").select(
                    "id"
                ).eq("client_id", client_id).execute().data or []
            sibling_ao_ids = [
                r["id"] for r in sib_rows
                if r.get("id") and r["id"] != ao_id
                and not r.get("archived") and not r.get("is_draft")
            ]

        # 3. Soumissions des AOs frères (en bloc).
        sibling_subs: list[dict] = []
        if sibling_ao_ids:
            sibling_subs = supabase.table("submissions").select(
                "id, ao_id, consultant_id, submitted_by"
            ).in_("ao_id", sibling_ao_ids).execute().data or []

        # 4. Noms consultants + partenaires en bloc (pas de requête en boucle).
        all_consultant_ids = [s.get("consultant_id") for s in this_ao_subs + sibling_subs]
        all_partner_ids = [s.get("submitted_by") for s in this_ao_subs + sibling_subs]
        consultant_name = _consultant_names(all_consultant_ids)
        partner_name = _partner_names(all_partner_ids)

        # « Autres » soumissions candidates au conflit : celles du même AO + frères.
        # On tague le scope pour chacune.
        others = [{**s, "ao_id": ao_id, "_scope": "same_ao"} for s in this_ao_subs]
        others += [{**s, "_scope": "same_client"} for s in sibling_subs]

        conflicts: dict[str, dict] = {}

        for sub in this_ao_subs:
            cid = sub.get("consultant_id")
            pid = sub.get("submitted_by")
            if not cid:
                continue
            name = consultant_name.get(cid)
            if not name:
                continue

            with_partners: dict[str, None] = {}  # dict pour dédupliquer en gardant l'ordre
            scope: str | None = None
            sample_ao_id: str | None = None

            for other in others:
                o_pid = other.get("submitted_by")
                o_cid = other.get("consultant_id")
                # Partenaire DIFFÉRENT obligatoire (le cœur du conflit).
                if not o_pid or o_pid == pid:
                    continue
                if not o_cid:
                    continue
                o_name = consultant_name.get(o_cid)
                if not _same_person(name, o_name):
                    continue

                pname = partner_name.get(o_pid) or "Partenaire inconnu"
                with_partners.setdefault(pname, None)
                # « same_ao » prime sur « same_client » (conflit plus direct).
                if other["_scope"] == "same_ao":
                    scope = "same_ao"
                    if sample_ao_id is None or other.get("ao_id") == ao_id:
                        sample_ao_id = other.get("ao_id") or ao_id
                elif scope != "same_ao":
                    scope = "same_client"
                    if sample_ao_id is None:
                        sample_ao_id = other.get("ao_id")

            if with_partners:
                conflicts[cid] = {
                    "conflict": True,
                    "with_partners": list(with_partners.keys()),
                    "scope": scope or "same_client",
                    "sample_ao_id": sample_ao_id,
                }

        return conflicts
    except Exception as exc:  # noqa: BLE001
        # Drapeau consultatif : on n'échoue jamais. On journalise si possible.
        try:
            from services import error_log
            error_log.record(
                "presentation_conflict",
                f"find_conflicts a échoué (AO {ao_id})",
                level="warning",
                exc=exc,
            )
        except Exception:  # noqa: BLE001
            pass
        return {}
