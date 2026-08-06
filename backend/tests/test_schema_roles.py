"""
Garde-fou : schema.sql doit provisionner les rôles, pas seulement les tables.

CE QUE CE TEST EMPÊCHE DE REVENIR

La première version de schema.sql activait la RLS sur les 22 tables sans créer
aucun rôle. PostgREST se connecte avec `authenticator` puis fait `SET ROLE` vers
le rôle porté par le jeton ; un rôle sans BYPASSRLS lit alors **zéro ligne, sans
la moindre erreur**.

L'application ne serait pas tombée. Elle aurait servi des listes vides et un
PGRST116 « 0 rows » sur chaque `.single()` — une panne qui ressemble à une base
vide, donc qu'on serait allé chercher du côté des données pendant des heures.

Le test d'acceptation (scripts/verify_postgrest.py) passait malgré tout, parce
que le BYPASSRLS avait été posé à la main dans le bac à sable : il vérifiait le
fonctionnement, pas le provisionnement. C'est exactement l'angle mort que ce
fichier ferme.

Lecture du SQL en texte, sans base : le test doit pouvoir échouer en CI, où
aucun PostgreSQL ne tourne.
"""
import pathlib
import re

SCHEMA = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "schema.sql"
SEED = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "seed.sql"


def _sql() -> str:
    return SCHEMA.read_text(encoding="utf-8")


def _sans_commentaires(sql: str) -> str:
    """Retire les lignes de commentaire : les mots-clés cités dans les
    explications ne doivent pas faire passer un test pour du code réel."""
    return "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))


def test_les_trois_roles_sont_crees():
    code = _sans_commentaires(_sql())
    for role in ("anon", "service_role", "authenticator"):
        assert re.search(rf"CREATE ROLE {role}\b", code), (
            f"schema.sql ne crée plus le rôle « {role} ». PostgREST fera SET ROLE "
            f"vers un rôle inexistant et renverra 400 sur chaque requête."
        )


def test_service_role_contourne_la_rls():
    """Sans BYPASSRLS, le backend lit zéro ligne sans erreur — la pire panne."""
    code = _sans_commentaires(_sql())
    assert re.search(r"CREATE ROLE service_role[^;]*BYPASSRLS", code), (
        "service_role n'est plus créé avec BYPASSRLS."
    )
    # Réaffirmé hors du bloc de création : les rôles sont globaux au cluster, un
    # service_role préexistant garderait ses propres attributs.
    assert re.search(r"ALTER ROLE service_role\s+BYPASSRLS", code), (
        "Le ALTER ROLE service_role BYPASSRLS a disparu. Sur un cluster qui "
        "possède déjà ce rôle, le CREATE ROLE est sauté et l'attribut manque."
    )


def test_authenticator_est_noinherit():
    """Sinon une requête sans jeton s'exécute avec les droits de service_role."""
    code = _sans_commentaires(_sql())
    assert re.search(r"CREATE ROLE authenticator[^;]*NOINHERIT", code), (
        "authenticator n'est plus NOINHERIT : il cumulerait par héritage les "
        "droits de ses rôles membres, donc ceux de service_role, et une requête "
        "non authentifiée aurait un accès complet."
    )


def test_anon_ne_recoit_aucun_droit():
    """Second verrou, indépendant de la RLS."""
    code = _sans_commentaires(_sql())
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in code, (
        "Le REVOKE sur le schéma public a disparu : anon retomberait sur la "
        "seule protection de la RLS, et une liste vide au lieu d'un refus."
    )
    assert not re.search(r"GRANT[^;]*\bTO\s+anon\b", code), (
        "Un GRANT vers anon est apparu. anon ne doit RIEN recevoir : c'est ce "
        "qui transforme une mauvaise configuration de PostgREST en « permission "
        "denied » plutôt qu'en divulgation des 22 tables."
    )


def test_la_trace_de_decision_humaine_resiste_a_la_suppression():
    """decided_by en RESTRICT, jamais en SET NULL.

    La colonne est NOT NULL : un SET NULL ne détache pas la trace, il fait
    échouer la suppression sur une violation de contrainte dont le message ne
    désigne pas la règle. RESTRICT l'énonce — une trace de décision humaine
    (AI Act art. 14) ne se détruit pas avec le compte de son auteur ; le droit
    à l'effacement se sert par anonymisation du profil.
    """
    code = _sans_commentaires(_sql())
    fk = re.search(r"CONSTRAINT human_decision_decided_by_fkey[^;]*;", code, re.S)
    assert fk, "La clé étrangère human_decision_decided_by_fkey a disparu."
    assert "ON DELETE RESTRICT" in fk.group(0), (
        f"decided_by n'est plus en ON DELETE RESTRICT : {fk.group(0)[:160]}"
    )


def test_le_schema_ne_cree_aucune_donnee_metier():
    """Les INSERT vivent dans seed.sql. Un schéma qui invente des clients n'en
    est pas un — c'est ce qui faisait apparaître deux clients fantômes dans
    toute base reconstruite depuis le dépôt."""
    code = _sans_commentaires(_sql())
    assert not re.search(r"\bINSERT\s+INTO\b", code, re.I), (
        "schema.sql contient un INSERT. Les données de référence vont dans "
        "seed.sql, et les données métier nulle part."
    )


def test_le_seed_ne_cree_pas_de_donnees_metier():
    code = _sans_commentaires(SEED.read_text(encoding="utf-8"))
    inserts = re.findall(r"INSERT\s+INTO\s+(?:public\.)?(\w+)", code, re.I)
    autorisees = {"app_settings", "scoring_config"}
    intrus = sorted(set(inserts) - autorisees)
    assert not intrus, (
        f"seed.sql insère dans {intrus}. Il ne doit contenir que de la "
        f"CONFIGURATION ({', '.join(sorted(autorisees))}), jamais de clients, "
        f"consultants ou appels d'offres."
    )
