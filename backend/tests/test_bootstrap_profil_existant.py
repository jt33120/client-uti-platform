"""
`bootstrap_admin.py --profil-existant` : rouvrir un compte sans en fabriquer un autre.

CE QUE CES TESTS PROTÈGENT

La migration 0019 ne reprend aucun hachage de Supabase. Sur une base peuplée,
les profils survivent à la sortie de GoTrue — avec les AO, les matchings et les
décisions qu'ils portent — mais leur mot de passe, non : il vivait dans
`auth.users`. Il faut donc pouvoir poser un mot de passe SUR un profil existant.

L'erreur que ces tests rendent impossible est silencieuse : créer un second
profil pour la même personne. Rien n'échoue, la connexion marche — et
`created_by`, `decided_by`, `submitted_by` de l'ancien profil désignent
désormais un compte inaccessible. On ne le découvre qu'en cherchant qui a validé
un dossier, des semaines plus tard.

Le second test couvre l'inverse : le retour arrière en cas d'échec ne doit
JAMAIS supprimer un profil que le script n'a pas créé. Le script d'amorçage
efface le profil quand l'insertion des identifiants échoue (sinon il resterait
un compte sans mot de passe, insupprimable par l'écran d'administration). Sur un
profil préexistant, ce même geste détruirait des données de production.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "bootstrap_admin.py"

MOT_DE_PASSE = "Correct-Cheval-Pile-42"
PROFIL = {
    "id": "11111111-2222-3333-4444-555555555555",
    "email": "Julian.Talou@grp-it.com",
    "name": "Julian Talou",
    "role": "admin",
}


class _Requete:
    """Maillon de chaîne PostgREST : enregistre l'opération, se renvoie lui-même."""

    def __init__(self, table, journal, lignes, echec_insert):
        self._table = table
        self._journal = journal
        self._lignes = lignes
        self._echec_insert = echec_insert
        self._resultat = []

    def select(self, *_a, **_k):
        self._resultat = self._lignes.get(self._table, [])
        return self

    def insert(self, data, **_k):
        self._journal.append(("insert", self._table, data))
        if self._table in self._echec_insert:
            raise RuntimeError(f"insertion refusée sur {self._table}")
        self._resultat = [data]
        return self

    def delete(self, **_k):
        self._journal.append(("delete", self._table, None))
        self._resultat = []
        return self

    def eq(self, *_a, **_k):
        return self

    def ilike(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("Reponse", (), {"data": self._resultat})()


class _FauxClient:
    def __init__(self, lignes, echec_insert=()):
        self.journal = []
        self._lignes = lignes
        self._echec_insert = set(echec_insert)

    def table(self, nom):
        return _Requete(nom, self.journal, self._lignes, self._echec_insert)


def _charger_script():
    spec = importlib.util.spec_from_file_location("bootstrap_admin_sous_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script(monkeypatch):
    monkeypatch.syspath_prepend(str(BACKEND))
    # `services.supabase_client` construit un vrai client PostgREST au
    # chargement, et supabase-py refuse la clé de test (elle n'a pas la forme
    # d'un JWT). On pose un bouchon AVANT l'import : ce qui est vérifié ici est
    # la logique du script, pas le client. `services.credentials` est retiré du
    # cache pour qu'il se lie au bouchon et non à un client déjà importé.
    bouchon = types.ModuleType("services.supabase_client")
    bouchon.supabase = None
    monkeypatch.setitem(sys.modules, "services.supabase_client", bouchon)
    monkeypatch.delitem(sys.modules, "services.credentials", raising=False)

    module = _charger_script()
    monkeypatch.setattr(module.getpass, "getpass", lambda *_a, **_k: MOT_DE_PASSE)
    return module


def _brancher(monkeypatch, script, client):
    """Le script et services.credentials tiennent chacun leur référence au client."""
    from services import credentials

    monkeypatch.setattr(script, "supabase", client)
    monkeypatch.setattr(credentials, "supabase", client)


def _lancer(monkeypatch, script, argv):
    monkeypatch.setattr(sys, "argv", ["bootstrap_admin.py"] + argv)
    return script.main()


def test_profil_existant_ne_cree_pas_de_second_profil(monkeypatch, script):
    # `user_credentials` vide (le mot de passe est parti avec GoTrue), un profil
    # bien présent : exactement l'état de la production après la migration 0019.
    client = _FauxClient({"profiles": [PROFIL], "user_credentials": []})
    _brancher(monkeypatch, script, client)

    code = _lancer(monkeypatch, script, [
        "--profil-existant", "--email", "julian.talou@grp-it.com", "--name", "Julian Talou",
    ])
    assert code == 0

    ecritures = [(op, table) for op, table, _ in client.journal]
    assert ("insert", "profiles") not in ecritures, (
        "un second profil a été créé : l'ancien porte encore les AO et les décisions"
    )
    assert ("insert", "user_credentials") in ecritures

    (_, _, ligne), = [e for e in client.journal if e[:2] == ("insert", "user_credentials")]
    # L'identifiant DOIT être celui du profil trouvé, pas un uuid4 tout neuf.
    assert ligne["user_id"] == PROFIL["id"]
    # L'adresse de connexion est normalisée en minuscules (contrainte CHECK 0019).
    assert ligne["email"] == "julian.talou@grp-it.com"
    # Et le secret est bien haché : une reprise qui poserait le mot de passe en
    # clair passerait tous les contrôles ci-dessus.
    assert ligne["password_hash"].startswith("$argon2id$")
    assert MOT_DE_PASSE not in ligne["password_hash"]


def test_echec_des_identifiants_ne_supprime_pas_un_profil_existant(monkeypatch, script):
    client = _FauxClient(
        {"profiles": [PROFIL], "user_credentials": []},
        echec_insert=("user_credentials",),
    )
    _brancher(monkeypatch, script, client)

    code = _lancer(monkeypatch, script, [
        "--profil-existant", "--email", "julian.talou@grp-it.com", "--name", "Julian Talou",
    ])
    assert code == 1  # l'échec est bien signalé…

    assert ("delete", "profiles", None) not in client.journal, (
        "le retour arrière a supprimé un profil de production que le script n'avait pas créé"
    )


def test_sans_le_drapeau_un_profil_existant_ne_bloque_pas_la_creation(monkeypatch, script):
    """Le mode normal reste intact : sur base vierge, le script crée les deux lignes."""
    client = _FauxClient({"profiles": [], "user_credentials": []})
    _brancher(monkeypatch, script, client)

    code = _lancer(monkeypatch, script, [
        "--email", "nouvelle@grp-it.com", "--name", "Compte Neuf",
    ])
    assert code == 0
    ecritures = [(op, table) for op, table, _ in client.journal]
    assert ("insert", "profiles") in ecritures
    assert ("insert", "user_credentials") in ecritures


def test_adresse_absente_refuse_au_lieu_de_creer(monkeypatch, script):
    """Une faute de frappe sur l'adresse ne doit pas retomber en création silencieuse."""
    client = _FauxClient({"profiles": [], "user_credentials": []})
    _brancher(monkeypatch, script, client)

    code = _lancer(monkeypatch, script, [
        "--profil-existant", "--email", "julain.talou@grp-it.com", "--name", "Julian Talou",
    ])
    assert code == 1
    assert not [e for e in client.journal if e[0] == "insert"]
