"""
Le journal applicatif doit survivre au redémarrage — et rester comptable.

POURQUOI CE FICHIER EXISTE

`services/error_log.py` est le seul endroit où la plateforme consigne ses
dégradations : repli d'un modèle, envoi SMTP raté, 500 inattendu, purge RGPD
qui trébuche. Dix-sept modules y appellent `record()`. Jusqu'ici ces événements
n'existaient QUE dans un tampon circulaire en mémoire, plafonné à 200 entrées
et vidé à chaque redémarrage — donc à chaque déploiement.

Trois conséquences, toutes silencieuses :
  * une dégradation du mardi avait disparu avant qu'on la regarde le dimanche ;
  * une panne qui se répète 400 fois par semaine était indiscernable d'une qui
    s'est produite deux fois ;
  * la seule vue existante (`GET /admin/errors`) suppose que quelqu'un ouvre la
    page — ce que personne ne fait quand tout a l'air de marcher.

Chaque événement part donc aussi en une ligne « UTI_EVT {json} » vers journald,
que `deploy/revue_hebdo.sh` §6 compte sur sept jours. Ce fichier fige le
contrat entre les deux : le préfixe, la forme d'une ligne, et le fait qu'une
ligne écrite par l'émetteur soit effectivement comptée par le lecteur.

CE QUE CES TESTS DÉFENDENT
    Journaliser ne doit jamais casser l'appelant, ne doit jamais produire deux
    lignes pour un événement, et ne doit jamais rester coincé dans un tampon.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services import error_log  # noqa: E402

# Les outils de test de la revue : on éprouve l'ÉMETTEUR et le LECTEUR ensemble,
# plutôt que chacun contre l'idée qu'il se fait de l'autre.
from test_revue_hebdo import compte, joue, section  # noqa: E402


def emet(**kwargs) -> str:
    """Fait émettre un événement par un process NEUF, et rend sa sortie brute.

    Un sous-process plutôt que `capsys` : c'est la seule façon de mesurer ce qui
    sort RÉELLEMENT sur le descripteur 1, tampons compris. capsys remplace
    sys.stdout par un objet Python et masquerait précisément le défaut de
    tamponnage qu'on veut interdire.
    """
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from services.error_log import record\n"
        "record(**%r)\n" % (str(BACKEND), kwargs)
    )
    res = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, timeout=60)
    return res.stdout


def lignes_evt(sortie: str) -> list[dict]:
    return [json.loads(l.split(" ", 1)[1])
            for l in sortie.splitlines()
            if l.startswith(error_log.PREFIXE_JOURNAL + " ")]


# ═══════════════════════════════════════════════════════════════════════════
#  La forme d'une ligne
# ═══════════════════════════════════════════════════════════════════════════
def test_un_evenement_produit_exactement_une_ligne():
    sortie = emet(source="smtp", message="connexion refusée")
    assert len(lignes_evt(sortie)) == 1, sortie


def test_la_ligne_porte_ce_quil_faut_pour_enqueter():
    """Sans `source`, un total hebdomadaire ne dit pas OÙ ça se dégrade ; sans
    `ts`, on ne peut pas distinguer une rafale d'un bruit de fond régulier."""
    evt = lignes_evt(emet(source="llm.extraction", message="repli sur le score déterministe",
                          level="warning", path="/aos/42/matching"))[0]
    assert evt["source"] == "llm.extraction"
    assert evt["level"] == "warning"
    assert evt["path"] == "/aos/42/matching"
    assert evt["message"].startswith("repli")
    assert evt["ts"].startswith("20")


def test_un_message_multiligne_tient_sur_une_seule_ligne():
    """Une trace d'erreur brute produirait vingt lignes dont dix-neuf sans
    préfixe : invisibles au comptage, et illisibles mêlées aux logs d'uvicorn.
    json.dumps échappe les retours à la ligne — encore faut-il que personne ne
    remplace un jour ce dumps par un f-string."""
    sortie = emet(source="http", message="ligne 1\nligne 2\nligne 3")
    assert len(lignes_evt(sortie)) == 1, sortie
    assert lignes_evt(sortie)[0]["message"] == "ligne 1\nligne 2\nligne 3"
    assert len([l for l in sortie.splitlines() if l.strip()]) == 1, (
        f"l'événement s'est étalé sur plusieurs lignes :\n{sortie}"
    )


def test_les_accents_ne_sont_pas_echappes():
    """`ensure_ascii=False` : « \\u00e9chec » au lieu de « échec » rend le journal
    illisible à l'œil, et c'est à l'œil qu'on le lit un jour d'incident."""
    sortie = emet(source="smtp", message="échec d'envoi à l'adhérent")
    assert "échec d'envoi" in sortie, sortie


def test_un_niveau_inconnu_retombe_sur_erreur():
    """Un niveau fantaisiste ne doit pas créer une troisième catégorie que
    personne ne compte : il vaut mieux surestimer une panne que la perdre."""
    evt = lignes_evt(emet(source="x", message="y", level="catastrophe"))[0]
    assert evt["level"] == "error"


# ═══════════════════════════════════════════════════════════════════════════
#  Journaliser ne doit jamais casser l'appelant
# ═══════════════════════════════════════════════════════════════════════════
def test_la_sortie_est_vidée_immediatement():
    """systemd relie stdout à un TUBE, et Python passe alors en tampon par blocs
    de 4 ko : sans flush, un événement resterait en mémoire jusqu'à ce que le
    tampon se remplisse — parfois des heures, précisément pendant l'incident
    qu'on essaie de suivre en direct.

    Le test lit la sortie d'un process ENCORE VIVANT : si la ligne n'est pas
    vidée, rien n'arrive avant l'expiration.

    PYTHONUNBUFFERED est RETIRÉ de l'environnement du fils, et ce détail fait
    tout le test. Cette variable est posée par beaucoup d'outils (elle l'est
    dans l'environnement où ce test a été écrit) ; en la laissant passer, le
    fils écrit sans tampon quoi qu'il arrive et le test reste vert même après
    suppression du flush. Il aurait alors mesuré l'environnement de la machine,
    pas le code — et il l'a fait, une fois, avant cette ligne."""
    import select

    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from services.error_log import record\n"
            "record('scheduler', 'tic')\n"
            "sys.stdin.readline()\n" % str(BACKEND))
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)
    try:
        pret, _, _ = select.select([proc.stdout], [], [], 15)
        assert pret, (
            "rien n'est sorti en 15 s alors que le process a déjà journalisé : "
            "la ligne est restée dans le tampon (flush=True a disparu)"
        )
        assert proc.stdout.readline().startswith(error_log.PREFIXE_JOURNAL)
    finally:
        proc.communicate(input="\n", timeout=30)


def test_une_sortie_fermee_ne_casse_pas_lappelant():
    """Le journal est appelé depuis des blocs `except` : s'il lève, il remplace
    l'erreur d'origine par la sienne et on perd les deux."""
    code = ("import sys, os; sys.path.insert(0, %r)\n"
            "from services.error_log import record, recent\n"
            "os.close(1)\n"
            "record('http', 'stdout fermé')\n"
            "sys.stderr.write('SURVECU:%%d' %% len(recent(5)))\n" % str(BACKEND))
    res = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert "SURVECU:1" in res.stderr, (
        "l'événement n'a pas atteint le tampon mémoire alors que seule la "
        "sortie journald était cassée"
    )


def test_le_tampon_memoire_fonctionne_toujours():
    """Le journald n'a rien remplacé : /admin/errors lit toujours le tampon."""
    avant = len(error_log.recent(200))
    error_log.record("test.regression", "événement de contrôle")
    apres = error_log.recent(200)
    assert len(apres) == min(avant + 1, 200)
    assert apres[0]["message"] == "événement de contrôle"


# ═══════════════════════════════════════════════════════════════════════════
#  Le contrat entre l'émetteur et le lecteur hebdomadaire
# ═══════════════════════════════════════════════════════════════════════════
#  C'est le test qui compte vraiment. Les deux moitiés peuvent être parfaites
#  chacune de son côté et ne pas se parler : il suffit qu'un préfixe change, ou
#  qu'un `json.dumps` se mette à écrire « "level":"error" » sans espace. Le
#  comptage hebdomadaire tomberait alors à zéro — c'est-à-dire au vert.
def test_le_prefixe_du_script_est_celui_du_code():
    assert error_log.PREFIXE_JOURNAL in section(6), (
        f"revue_hebdo.sh ne cherche plus « {error_log.PREFIXE_JOURNAL} » : le "
        f"comptage hebdomadaire des erreurs compterait zéro pour toujours"
    )


def test_une_vraie_ligne_emise_est_bien_comptee_par_la_revue():
    """Bout en bout, sans recopier la forme d'une ligne nulle part : on fabrique
    les lignes avec le VRAI émetteur, on les donne au VRAI lecteur."""
    emises = "".join(emet(source="smtp", message=f"échec {i}") for i in range(3))
    emises += "".join(emet(source="ai_budget", message="plafond atteint",
                           level="warning") for _ in range(2))
    sortie = joue("journalctl() { printf '%s' \"$STUB_JOURNAL\"; }\n" + section(6),
                  {"ERREURS_SEMAINE_MAX": "100", "STUB_JOURNAL": emises})
    assert compte(sortie, "ROUGE") == 0, sortie
    assert "3 erreur" in sortie, (
        f"la revue n'a pas compté 3 erreurs sur les 5 événements émis :\n{sortie}"
    )
    assert "smtp" in sortie, "le classement par source ne retrouve pas l'émetteur"


@pytest.mark.parametrize("niveau, attendu_rouge", [("error", 1), ("warning", 0)])
def test_le_seuil_ne_se_declenche_que_sur_les_erreurs(niveau, attendu_rouge):
    """Un repli maîtrisé qui se produit 200 fois est une information ; le
    traiter comme 200 pannes ferait crier la revue toutes les semaines, et on
    cesserait de l'ouvrir."""
    une = emet(source="llm", message="x", level=niveau)
    sortie = joue("journalctl() { printf '%s' \"$STUB_JOURNAL\"; }\n" + section(6),
                  {"ERREURS_SEMAINE_MAX": "5", "STUB_JOURNAL": une * 10})
    assert compte(sortie, "ROUGE") == attendu_rouge, sortie
