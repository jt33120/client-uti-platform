"""
Le système de la synthèse vivier ne doit exposer QUE les critères actifs.

Motivation (recette PR #184) : avec le TJM à 0★, aucune donnée TJM n'était
transmise au modèle — mais la clé `compatibilite_tjm` restait dans le schéma
JSON du prompt. Le critère restait donc saillant, et le modèle le ressortait en
argument libre dans `recommendation` (« le seul candidat techniquement aligné,
avec un TJM compatible »), contredisant à l'écran la décision métier de le
sortir de la grille.
"""

from services.matching_synthesis import _CATS, _system

# Grille réellement en production : TJM exclu, les 5 autres axes actifs.
W_PROD = {
    "w_competences": 30,
    "w_seniorite": 29,
    "w_contexte": 29,
    "w_points_forts_cv": 6,
    "w_elements_differenciants": 6,
    "w_tjm": 0,
}


def _active(weights: dict) -> list[str]:
    """Même filtre que `_profiles_block` : un barème à 0 sort de la liste."""
    return [k for k, wk, _ in _CATS if int(weights.get(wk, 0) or 0) > 0]


def test_disabled_criterion_is_absent_from_the_prompt():
    prompt = _system(_active(W_PROD))
    assert "tjm" not in prompt.lower()
    assert "compatibilite_tjm" not in prompt


def test_active_criteria_are_all_present():
    prompt = _system(_active(W_PROD))
    for key in ("competences_techniques", "seniorite", "contexte_domaine",
                "points_forts_cv", "elements_differenciants"):
        assert f'"{key}"' in prompt


def test_criterion_comes_back_when_an_ao_reactivates_it():
    # Le TJM n'est pas supprimé : un AO peut le remonter à ≥ 1★, et il doit
    # alors réapparaître dans le schéma demandé au modèle.
    prompt = _system(_active({**W_PROD, "w_tjm": 9}))
    assert "compatibilite_tjm" in prompt


def test_recommendation_is_explicitly_fenced_to_active_criteria():
    # La règle qui empêche la fuite en texte libre doit rester dans le prompt.
    prompt = _system(_active(W_PROD))
    assert "recommendation" in prompt
    assert "exclu" in prompt.lower()
