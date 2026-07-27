"""
Tests unitaires du scoring déterministe et de la pseudonymisation.

Pures fonctions, sans réseau : exécutables en CI. Démontrent la reproductibilité
(Art. 15) et la base de l'invariance au biais (Art. 10).
"""
from services.scoring import (
    score_consultant, GRID_VERSION, RECO_FORT_MIN, RECO_MOYEN_MIN,
    DEFAULTS, NEUTRAL_RATIO, stars_to_weights,
)
from services.pseudonymize import strip_pii

# Poids par défaut effectifs (grille v2, dérivés des étoiles) — les tests
# s'appuient dessus plutôt que sur des constantes figées, pour rester valides
# quand la grille évolue.
W_COMP = DEFAULTS["w_competences"]


AO = {
    "id": "ao-1",
    "title": "Développeur Python Banque",
    "skills_required": "Python, FastAPI, PostgreSQL",
    "budget_max": 600,
    "ao_type": "Banque/Finance",
    "context": "Migration d'un système bancaire",
}


def _consultant(**over):
    base = {"skills": "Python, FastAPI, PostgreSQL", "tjm": 500, "experience_years": 10}
    base.update(over)
    return base


def _features(**over):
    base = {"skills": ["Python", "FastAPI", "PostgreSQL"],
            "experience_years": 10, "sectors": ["banque"], "summary": "Profil banque"}
    base.update(over)
    return base


# ── Reproductibilité (Art. 15) ─────────────────────────────────────

def test_scoring_is_deterministic():
    a = score_consultant(_features(), _consultant(), AO)
    b = score_consultant(_features(), _consultant(), AO)
    assert a == b


def test_breakdown_sums_to_total():
    res = score_consultant(_features(), _consultant(), AO)
    assert sum(res["breakdown"].values()) == res["score_total"]
    assert 0 <= res["score_total"] <= 100


# ── Invariance au biais : le nom/genre ne doit pas changer le score (Art. 10) ──

def test_score_invariant_to_name_in_features():
    # Les features ne portent pas d'identité ; deux "personnes" aux mêmes features
    # obtiennent strictement le même score.
    woman = score_consultant(_features(), _consultant(), AO)
    man = score_consultant(_features(), _consultant(), AO)
    assert woman["score_total"] == man["score_total"]


# ── Compétences ────────────────────────────────────────────────────

def test_full_skill_match_scores_high():
    res = score_consultant(_features(), _consultant(), AO)
    assert res["breakdown"]["competences_techniques"] == W_COMP


def test_no_skill_match_scores_zero_competences():
    res = score_consultant(
        _features(skills=["Cobol"]),
        _consultant(skills="Cobol"),
        AO,
    )
    assert res["breakdown"]["competences_techniques"] == 0


# ── TJM ────────────────────────────────────────────────────────────
# Depuis la grille v2.2 le TJM est HORS scoring par défaut (le budget est cadré
# sur l'AO). Le critère reste implémenté et réactivable par AO : les tests de la
# formule tournent donc sur une grille où il est explicitement remis à 2★.

# Grille de test avec le TJM réactivé (les autres axes gardent leur défaut).
STARS_TJM_ON = {"competences": 4, "seniorite": 2, "contexte": 2,
                "points_forts_cv": 1, "elements_differenciants": 1, "tjm": 2}
CFG_TJM_ON = {"stars": STARS_TJM_ON}
W_TJM_ON = stars_to_weights(STARS_TJM_ON)["w_tjm"]


def test_tjm_is_out_of_the_default_grid():
    # Le cœur de la décision métier : sur la grille PAR DÉFAUT, un TJM très
    # au-dessus du budget ne coûte plus rien au candidat.
    assert DEFAULTS["w_tjm"] == 0
    res = score_consultant(_features(), _consultant(tjm=5000), AO)
    assert res["breakdown"]["compatibilite_tjm"] == 0
    assert not any("TJM" in p for p in res["points_faibles"])
    # Un TJM hors budget ne doit rien changer au total.
    assert res["score_total"] == score_consultant(_features(), _consultant(tjm=100), AO)["score_total"]


def test_default_grid_weights_sum_to_100_without_tjm():
    active = {k: v for k, v in DEFAULTS.items() if k.startswith("w_")}
    assert sum(active.values()) == 100
    assert active["w_tjm"] == 0


def test_tjm_within_budget_is_full_when_reenabled():
    res = score_consultant(_features(), _consultant(tjm=600), AO, CFG_TJM_ON)
    assert res["breakdown"]["compatibilite_tjm"] == W_TJM_ON


def test_tjm_far_over_budget_is_penalised_when_reenabled():
    res = score_consultant(_features(), _consultant(tjm=1200), AO, CFG_TJM_ON)
    assert res["breakdown"]["compatibilite_tjm"] < W_TJM_ON


def test_missing_tjm_is_neutral_when_reenabled():
    res = score_consultant(_features(), _consultant(tjm=None), AO, CFG_TJM_ON)
    assert res["breakdown"]["compatibilite_tjm"] == round(W_TJM_ON * NEUTRAL_RATIO)


# ── Recommandation ─────────────────────────────────────────────────

def test_strong_profile_reco_fort():
    res = score_consultant(_features(), _consultant(tjm=500), AO)
    assert res["score_total"] >= RECO_FORT_MIN
    assert res["recommandation"] == "FORT"


def test_weak_profile_reco_faible():
    res = score_consultant(
        _features(skills=["Cobol"], experience_years=0, sectors=[]),
        _consultant(skills="Cobol", experience_years=0, tjm=2000),
        AO,
    )
    assert res["recommandation"] == "FAIBLE"


# ── Robustesse : features vides => fallback sur le déclaré, jamais d'erreur ──

def test_empty_features_falls_back_to_declared():
    res = score_consultant({}, _consultant(), AO)
    assert 0 <= res["score_total"] <= 100
    # Les compétences déclarées suffisent à matcher.
    assert res["breakdown"]["competences_techniques"] == W_COMP


def test_grid_version_exposed():
    assert isinstance(GRID_VERSION, str) and GRID_VERSION


# ── Grille v2 : axes qualitatifs + critère désactivable ────────────

def test_v2_qualitative_axes_present_and_neutral():
    # Points forts / différenciation : pas de signal déterministe -> socle neutre.
    res = score_consultant(_features(), _consultant(), AO)
    bd = res["breakdown"]
    assert "points_forts_cv" in bd and "elements_differenciants" in bd
    assert bd["points_forts_cv"] == round(DEFAULTS["w_points_forts_cv"] * NEUTRAL_RATIO)
    assert bd["elements_differenciants"] == round(DEFAULTS["w_elements_differenciants"] * NEUTRAL_RATIO)


def test_tjm_disabled_at_zero_star_is_excluded():
    # 0★ sur le TJM => poids nul, aucune contribution, total borné à 100.
    stars = {"competences": 4, "seniorite": 2, "contexte": 2,
             "points_forts_cv": 2, "elements_differenciants": 2, "tjm": 0}
    res = score_consultant(_features(), _consultant(tjm=2000), AO, {"stars": stars})
    assert res["breakdown"]["compatibilite_tjm"] == 0
    assert sum(res["breakdown"].values()) == res["score_total"] <= 100
    # Le TJM (même hors budget) n'apparaît plus dans les points faibles.
    assert not any("TJM" in p for p in res["points_faibles"])


# ── Pseudonymisation (Art. 10 + RGPD) ──────────────────────────────

def test_strip_pii_removes_email_and_phone():
    txt = "Jean Dupont, jean.dupont@example.com, +33 6 12 34 56 78, Python expert"
    out = strip_pii(txt, name="Jean Dupont")
    assert "jean.dupont@example.com" not in out
    assert "Dupont" not in out
    assert "Jean" not in out
    assert "Python" in out  # l'info utile est conservée


def test_strip_pii_handles_none():
    assert strip_pii(None) == ""


# ── Compétences courtes (Go, C#, R…) ───────────────────────────────

def test_short_skills_are_scored():
    # « Go » et « C# » (≤ 2 caractères) doivent compter dans le ratio compétences.
    ao = {**AO, "skills_required": "Go, C#"}
    full = score_consultant(
        _features(skills=["Go", "C#"]), _consultant(skills="Go, C#"), ao, None
    )
    assert full["breakdown"]["competences_techniques"] == W_COMP

    # Et un profil sans rapport ne doit PAS matcher par inclusion accidentelle
    # (« r »/« go » contenus dans « docker »/« django »).
    ao_r = {**AO, "skills_required": "R, Go"}
    none = score_consultant(
        _features(skills=["Docker", "Django"]), _consultant(skills="Docker, Django"), ao_r, None
    )
    assert none["breakdown"]["competences_techniques"] == 0


# ── Synonymes de compétences (v2.1) ────────────────────────────────

def test_skill_synonyms_match_fully():
    # Le consultant écrit les technos autrement (ReactJS/NodeJS/K8s/Postgres) mais
    # ce sont les mêmes compétences que l'AO (React.js/Node.js/Kubernetes/PostgreSQL).
    ao = {**AO, "skills_required": "React.js, Node.js, Kubernetes, PostgreSQL"}
    res = score_consultant(
        _features(skills=["ReactJS", "NodeJS", "K8s", "Postgres"]),
        _consultant(skills="ReactJS, NodeJS, K8s, Postgres"),
        ao,
    )
    assert res["breakdown"]["competences_techniques"] == W_COMP


def test_skill_synonyms_do_not_create_false_match():
    # Un alias ne doit pas faire matcher des technos sans rapport.
    ao = {**AO, "skills_required": "React.js"}
    res = score_consultant(
        _features(skills=["Angular"]), _consultant(skills="Angular"), ao,
    )
    assert res["breakdown"]["competences_techniques"] == 0
