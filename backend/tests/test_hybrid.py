"""
Tests de la fusion hybride (services.llm_scoring.combine_hybrid).

Fonction pure (aucun réseau) : on vérifie que la combinaison grille/IA respecte
l'ancre déterministe SANS écraser un avis IA confiant (plancher A_FLOOR, v2.1).
Démontre la reproductibilité de la fusion (AI Act Art. 15).
"""
import os

# Variables minimales pour que `config` s'importe hors environnement serveur.
for _k, _v in {
    "SUPABASE_URL": "http://x", "SUPABASE_SERVICE_KEY": "x", "SUPABASE_ANON_KEY": "x",
    "JWT_SECRET": "x", "SECRET_KEY": "x",
}.items():
    os.environ.setdefault(_k, _v)

from services.llm_scoring import combine_hybrid  # noqa: E402
from services.scoring import DEFAULTS  # noqa: E402

# Poids par défaut effectifs (grille v2.1) sous forme de dict w_*.
WEIGHTS = {k: v for k, v in DEFAULTS.items() if k.startswith("w_")}
WC = WEIGHTS["w_competences"]

_ZERO_LLM = {
    "seniorite": {"score": 0}, "contexte": {"score": 0},
    "points_forts_cv": {"score": 0}, "elements_differenciants": {"score": 0},
    "tjm": {"score": 0},
}
_ZERO_DET = {
    "seniorite": 0, "contexte_domaine": 0, "points_forts_cv": 0,
    "elements_differenciants": 0, "compatibilite_tjm": 0,
}


def _det(comp):
    return {"breakdown": {"competences_techniques": comp, **_ZERO_DET}, "score_total": comp}


def _llm(comp):
    return {
        "score_llm": comp,
        "llm_breakdown": {"competences": {"score": comp, "justification": ""}, **_ZERO_LLM},
        "llm_global": "",
    }


def test_confident_llm_not_crushed_by_grid():
    # Grille basse (5) mais IA confiante au max sur les compétences : l'hybride
    # doit conserver une influence IA nette (≥ ~30 % du chemin vers l'avis IA),
    # au lieu de retomber sur l'ancre déterministe comme avant v2.1.
    out = combine_hybrid(_det(5), _llm(WC), WEIGHTS)
    hyb = out["hybrid_breakdown"]["competences_techniques"]
    frac = (hyb - 5) / (WC - 5)  # position entre grille (0) et IA (1)
    assert frac >= 0.28
    assert hyb < WC  # la grille reste une ancre : on n'égale pas l'avis IA pur


def test_perfect_agreement_is_preserved():
    # Accord parfait grille/IA => l'hybride vaut exactement cette valeur.
    out = combine_hybrid(_det(WC), _llm(WC), WEIGHTS)
    assert out["hybrid_breakdown"]["competences_techniques"] == WC


def test_no_llm_falls_back_to_deterministic():
    out = combine_hybrid(_det(20), None, WEIGHTS)
    assert out["score_hybride"] == 20
    assert out["score_llm"] is None
