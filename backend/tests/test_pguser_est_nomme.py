"""
Les scripts de sauvegarde doivent NOMMER le rôle PostgreSQL.

CE QUE CES TESTS PROTÈGENT

Le 26 août 2026, à la première exécution réelle de `backup_db.sh` :

    pg_dump: FATAL: Peer authentication failed for user "julian.talou"

`/var/backups/uti` n'existait pas. Aucune sauvegarde n'avait jamais abouti — et
aucune n'aurait pu, sur aucune installation conforme.

LA CAUSE, ET POURQUOI ELLE ÉTAIT INVISIBLE

`install_db.sh` configure une authentification « peer » AVEC correspondance :

    pg_hba.conf     local all all peer map=uti
    pg_ident.conf   uti  "julian.talou"  ->  uti_admin

Le compte UNIX est donc autorisé à se connecter comme `uti_admin`, et seulement
comme lui. Mais libpq, faute de `-U` ou de `PGUSER`, prend le nom du compte UNIX
comme nom de RÔLE : il demande « julian.talou », qui n'en est pas un. Les
scripts contredisaient la conception de leur propre installation.

Rien ne pouvait le révéler à la lecture : le code est correct isolément, l'unité
systemd est correcte isolément, et c'est leur rencontre qui échoue. Seule une
exécution le disait — et personne n'avait jamais exécuté ces scripts. C'est le
défaut le plus grave possible sur un outil dont la seule raison d'être est de
fonctionner un jour où tout le reste a échoué.

POURQUOI DANS LE SCRIPT ET PAS DANS L'UNITÉ SYSTEMD

Le RUNBOOK §10 fait lancer ces scripts À LA MAIN le jour d'un sinistre. Une
variable qui ne vivrait que dans `uti-backup.service` laisserait échouer
précisément l'exécution qui compte le plus.
"""
import pathlib
import re

import pytest

DEPLOY = pathlib.Path(__file__).resolve().parents[1] / "deploy"

#: Les trois scripts qui parlent à PostgreSQL sous l'identité de l'appelant.
SCRIPTS = ["backup_db.sh", "restore_drill.sh", "supervision.sh"]


def lire(nom: str) -> str:
    return (DEPLOY / nom).read_text(encoding="utf-8")


def code_seul(nom: str) -> str:
    """Le script sans ses commentaires : une intention en commentaire n'exécute rien."""
    return "\n".join(
        l for l in lire(nom).splitlines() if not l.lstrip().startswith("#")
    )


@pytest.mark.parametrize("nom", SCRIPTS)
def test_le_role_postgresql_est_nomme(nom):
    assert re.search(r"(?m)^\s*export\s+PGUSER=", code_seul(nom)), (
        f"{nom} ne nomme plus le rôle PostgreSQL. Sur une installation "
        f"conforme (peer + pg_ident), il échouera à sa première commande avec "
        f"« Peer authentication failed » — et ne le dira qu'à l'exécution."
    )


@pytest.mark.parametrize("nom", SCRIPTS)
def test_la_valeur_reste_surchargeable(nom):
    """`${PGUSER:-…}` et non `PGUSER=…` : une installation qui a choisi un autre
    nom de rôle (DB_OWNER est paramétrable dans install_db.sh) doit pouvoir le
    passer par l'environnement sans modifier le script."""
    assert re.search(r'export PGUSER="\$\{PGUSER:-[^}]+\}"', code_seul(nom)), (
        f"{nom} force la valeur au lieu de fournir un défaut surchargeable."
    )


@pytest.mark.parametrize("nom", SCRIPTS)
def test_le_defaut_suit_celui_de_l_installation(nom):
    """Le rôle par défaut doit être celui que `install_db.sh` crée réellement.

    Les deux valeurs vivent dans deux fichiers ; c'est le nombre d'endroits où
    elles peuvent diverger. Une divergence rendrait les sauvegardes
    inopérantes exactement comme avant, et de nouveau en silence.
    """
    install = (DEPLOY / "install_db.sh").read_text(encoding="utf-8")
    m = re.search(r'(?m)^DB_OWNER="\$\{DB_OWNER:-([^}"]+)\}"', install)
    assert m, "DB_OWNER introuvable dans install_db.sh"
    attendu = m.group(1)

    trouve = re.search(r'export PGUSER="\$\{PGUSER:-([^}"]+)\}"', code_seul(nom))
    assert trouve, f"{nom} : PGUSER introuvable"
    assert trouve.group(1) == attendu, (
        f"{nom} vise le rôle « {trouve.group(1)} » alors que install_db.sh crée "
        f"« {attendu} ». La sauvegarde échouerait, et seulement à l'exécution."
    )


def test_le_role_vise_a_le_droit_de_creer_une_base():
    """`restore_drill.sh` crée une base jetable : le rôle doit avoir CREATEDB.

    Sans cet attribut, la répétition de restauration échouerait au CREATE
    DATABASE — soit exactement le même mode de panne, déplacé d'un cran.
    """
    install = (DEPLOY / "install_db.sh").read_text(encoding="utf-8")
    assert "CREATEDB" in install, (
        "install_db.sh ne donne plus CREATEDB au rôle : restore_drill.sh ne "
        "pourra plus créer sa base jetable."
    )
