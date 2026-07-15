"""
Paramètres de scoring pilotables par l'admin.

L'importance des critères est exprimée en **étoiles (1-5)** — forme pensée pour
des utilisateurs non techniques. Les poids w_* (somme = 100) en sont dérivés par
normalisation (`services.scoring.stars_to_weights`). Les seuils de recommandation
et la séniorité cible restent réglables.

Lecture : staff UTI. Écriture : admin uniquement. Toute modification est
journalisée (audit_log, Art. 12) et impacte les scorings suivants.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional

from services.supabase_client import supabase
from services.scoring import (
    DEFAULTS, DEFAULT_STARS, GRID_VERSION, STAR_CRITERIA, stars_to_weights,
)
from services import audit
from routers.auth import require_staff, require_admin

router = APIRouter(prefix="/scoring-config", tags=["scoring-config"])


class StarConfig(BaseModel):
    """
    Importance relative de chaque critère, notée de 0 à 5 étoiles.
    0★ = critère EXCLU du score (utile p. ex. pour le TJM, déjà borné par le
    TJM max de l'AO). Les axes « points forts du CV » et « éléments
    différenciants » sont notés par le 2e avis IA.
    """
    competences: int = Field(ge=0, le=5)
    seniorite: int = Field(ge=0, le=5)
    contexte: int = Field(ge=0, le=5)
    points_forts_cv: int = Field(ge=0, le=5)
    elements_differenciants: int = Field(ge=0, le=5)
    tjm: int = Field(ge=0, le=5)


class AOScoringOverrides(BaseModel):
    """
    Surcharge de la grille pour un AO donné. Tous les champs sont optionnels :
    ceux laissés vides retombent sur la config globale (puis sur les défauts).
    """
    stars: Optional[StarConfig] = None
    seniority_full_years: Optional[int] = Field(default=None, ge=1, le=40)
    reco_fort_min: Optional[int] = Field(default=None, ge=0, le=100)
    reco_moyen_min: Optional[int] = Field(default=None, ge=0, le=100)

    def to_storage(self) -> Optional[dict]:
        """Dict prêt à stocker (None si aucune surcharge réelle)."""
        data = self.model_dump(exclude_none=True)
        return data or None


class ScoringConfig(BaseModel):
    stars: StarConfig
    seniority_full_years: int = Field(ge=1, le=40)
    reco_fort_min: int = Field(ge=0, le=100)
    reco_moyen_min: int = Field(ge=0, le=100)


def _validate_thresholds(fort: Optional[int], moyen: Optional[int]):
    if fort is not None and moyen is not None and fort <= moyen:
        raise HTTPException(
            status_code=422,
            detail="Le seuil FORT doit être strictement supérieur au seuil MOYEN.",
        )


def _stored_row() -> dict:
    """Ligne de config stockée, ou {} (best-effort si la table n'existe pas)."""
    try:
        rows = supabase.table("scoring_config").select("*").limit(1).execute().data or []
        return rows[0] if rows else {}
    except Exception as e:  # noqa: BLE001
        print(f"[SCORING] lecture config: {e}")
        return {}


@router.get("")
async def get_scoring_config(user: dict = Depends(require_admin)):
    """Config effective (étoiles + poids dérivés + seuils) et métadonnées.

    Réservée à l'admin (comme la page UI /admin/scoring et l'écriture PUT) : les
    pondérations du matching sont un réglage de gouvernance, pas une donnée
    consultée par les commerciaux."""
    row = _stored_row()

    # Fusion critère par critère : une config antérieure (4 axes) conserve ses
    # étoiles enregistrées, les axes v2 absents retombent sur leur défaut.
    stars = {c: row[f"s_{c}"] for c in STAR_CRITERIA if row.get(f"s_{c}") is not None}
    is_custom_stars = bool(stars)
    effective_stars = {**DEFAULT_STARS, **stars}

    def _eff(key: str):
        return row[key] if row.get(key) is not None else DEFAULTS[key]

    seniority = _eff("seniority_full_years")
    fort = _eff("reco_fort_min")
    moyen = _eff("reco_moyen_min")
    is_custom = is_custom_stars or any(
        row.get(k) is not None for k in ("seniority_full_years", "reco_fort_min", "reco_moyen_min")
    )

    return {
        "stars": effective_stars,
        "weights": stars_to_weights(effective_stars),  # %, pour l'affichage
        "seniority_full_years": seniority,
        "reco_fort_min": fort,
        "reco_moyen_min": moyen,
        "defaults": {
            "stars": DEFAULT_STARS,
            "seniority_full_years": DEFAULTS["seniority_full_years"],
            "reco_fort_min": DEFAULTS["reco_fort_min"],
            "reco_moyen_min": DEFAULTS["reco_moyen_min"],
        },
        "grid_version": GRID_VERSION,
        "is_custom": is_custom,
    }


@router.put("")
async def update_scoring_config(body: ScoringConfig, user: dict = Depends(require_admin)):
    """Met à jour la grille (admin). Valide la cohérence des seuils."""
    _validate_thresholds(body.reco_fort_min, body.reco_moyen_min)

    weights = stars_to_weights(body.stars.model_dump())
    payload = {f"s_{c}": getattr(body.stars, c) for c in STAR_CRITERIA}
    payload.update({
        **weights,  # poids dérivés stockés aussi (back-compat + lisibilité audit)
        "seniority_full_years": body.seniority_full_years,
        "reco_fort_min": body.reco_fort_min,
        "reco_moyen_min": body.reco_moyen_min,
        "updated_by": user["sub"],
    })

    def _write(data: dict):
        existing = supabase.table("scoring_config").select("id").limit(1).execute().data or []
        if existing:
            supabase.table("scoring_config").update(data).eq("id", existing[0]["id"]).execute()
        else:
            supabase.table("scoring_config").insert(data).execute()

    try:
        _write(payload)
    except Exception:
        # Colonnes étoiles (s_*) pas encore migrées → on stocke au moins les
        # poids dérivés + seuils, sans casser l'enregistrement.
        fallback = {k: v for k, v in payload.items() if not k.startswith("s_")}
        try:
            _write(fallback)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur enregistrement config: {e}")

    audit.log_event(
        "config_change", audit.new_run_id(),
        actor_id=user["sub"], grid_version=GRID_VERSION,
        payload={**body.model_dump(), "weights": weights},
    )
    return {
        "stars": body.stars.model_dump(),
        "weights": weights,
        "seniority_full_years": body.seniority_full_years,
        "reco_fort_min": body.reco_fort_min,
        "reco_moyen_min": body.reco_moyen_min,
        "message": "Grille de scoring mise à jour.",
    }
