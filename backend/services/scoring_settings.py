"""
Chargement de la configuration de scoring (pilotable par l'admin).

Sépare l'accès base (ici) du moteur pur (`services.scoring`, sans I/O). Best-effort :
si la table n'existe pas ou est vide, on retombe sur les valeurs par défaut.
"""
from services.supabase_client import supabase
from services.scoring import DEFAULT_STARS, STAR_CRITERIA


def get_config() -> dict:
    """
    Retourne les surcharges de grille stockées par l'admin.

    Forme canonique = « étoiles » (clés `s_*` → dict `stars`), pilotée par l'UI.
    On fusionne sur les défauts : une config antérieure (4 axes) conserve ses
    étoiles, les axes v2 absents prennent leur défaut. La normalisation
    (`stars_to_weights`) garantit une somme de poids = 100 quel que soit le
    nombre d'axes réellement stockés — on n'utilise donc plus les poids `w_*`
    bruts (qui, avec 4 axes, ne totalisent plus 100 face à 6 critères).
    Best-effort : si la table n'existe pas, on renvoie {} et le moteur applique
    ses DEFAULTS (grille par défaut à 6 axes).
    """
    try:
        rows = supabase.table("scoring_config").select("*").limit(1).execute().data or []
        if rows:
            row = rows[0]
            out = {}
            for k in ("seniority_full_years", "reco_fort_min", "reco_moyen_min"):
                if row.get(k) is not None:
                    out[k] = row[k]
            stars = {c: row[f"s_{c}"] for c in STAR_CRITERIA if row.get(f"s_{c}") is not None}
            if stars:
                out["stars"] = {**DEFAULT_STARS, **stars}
            return out
    except Exception as e:  # noqa: BLE001
        print(f"[SCORING] config indisponible, défauts utilisés: {e}")
    return {}
