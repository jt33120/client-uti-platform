"""
`migrer_identifiants.py` : donner à onze comptes existants un mot de passe maison.

CE QUE CES TESTS PROTÈGENT

Ce script écrit à de vraies personnes et arme, pour chacune, une clé d'entrée
temporaire dans son compte. Trois erreurs y seraient irrattrapables, et aucune
ne se verrait à l'exécution :

  * un envoi déclenché par une commande tapée trop vite — on ne rappelle pas un
    e-mail. D'où la SIMULATION par défaut, vérifiée ici comme un comportement,
    pas comme une intention ;
  * le jeton en clair écrit en base. Seule son empreinte SHA-256 doit y figurer,
    faute de quoi une copie de `user_credentials` — une sauvegarde, un export —
    contiendrait des liens d'accès directement utilisables ;
  * un hachage de remplissage IDENTIQUE sur tous les comptes. Il désignerait les
    comptes en attente à qui lirait la base, et le premier qui devinerait la
    valeur les ouvrirait tous.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "migrer_identifiants.py"

PROFILS = [
    {"id": "aaaa1111-0000-0000-0000-000000000001", "email": "Julian.Talou33@gmail.com",
     "name": "Julian Talou", "role": "admin", "status": "active"},
    {"id": "aaaa1111-0000-0000-0000-000000000002", "email": "partenaire@example.org",
     "name": "Partenaire Test", "role": "ao", "status": "active"},
    {"id": "aaaa1111-0000-0000-0000-000000000003", "email": "suspendu@example.org",
     "name": "Compte Suspendu", "role": "ao", "status": "suspended"},
]


class _Requete:
    def __init__(self, table, journal, lignes):
        self._table = table
        self._journal = journal
        self._lignes = lignes
        self._resultat = []
        self._filtre_email = None

    def select(self, *_a, **_k):
        self._resultat = self._lignes.get(self._table, [])
        return self

    def insert(self, data, **_k):
        self._journal.append(("insert", self._table, data))
        self._resultat = [data]
        return self

    def update(self, data, **_k):
        self._journal.append(("update", self._table, data))
        self._resultat = [data]
        return self

    def eq(self, *_a, **_k):
        return self

    def ilike(self, _colonne, valeur):
        self._filtre_email = (valeur or "").lower()
        self._resultat = [
            ligne for ligne in self._resultat
            if (ligne.get("email") or "").lower() == self._filtre_email
        ]
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("Reponse", (), {"data": self._resultat})()


class _FauxClient:
    def __init__(self, lignes):
        self.journal = []
        self._lignes = lignes

    def table(self, nom):
        return _Requete(nom, self.journal, self._lignes)


@pytest.fixture
def script(monkeypatch):
    monkeypatch.syspath_prepend(str(BACKEND))
    bouchon = types.ModuleType("services.supabase_client")
    bouchon.supabase = None
    monkeypatch.setitem(sys.modules, "services.supabase_client", bouchon)
    monkeypatch.delitem(sys.modules, "services.credentials", raising=False)

    spec = importlib.util.spec_from_file_location("migrer_identifiants_sous_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def envois():
    return []


def _brancher(monkeypatch, script, client, envois):
    from services import credentials

    monkeypatch.setattr(script, "supabase", client)
    monkeypatch.setattr(credentials, "supabase", client)
    monkeypatch.setattr(
        script.email_outbox, "enqueue",
        lambda **kw: (envois.append(kw) or {"id": "file-1"}),
    )


def _lancer(monkeypatch, script, argv):
    monkeypatch.setattr(sys, "argv", ["migrer_identifiants.py"] + argv)
    return script.main()


def test_par_defaut_le_script_simule(monkeypatch, script, envois):
    client = _FauxClient({"profiles": PROFILS, "user_credentials": []})
    _brancher(monkeypatch, script, client, envois)

    assert _lancer(monkeypatch, script, []) == 0
    assert client.journal == [], "la simulation a écrit en base"
    assert envois == [], "la simulation a envoyé des e-mails"


def test_les_comptes_suspendus_ne_sont_pas_contactes(monkeypatch, script, envois):
    client = _FauxClient({"profiles": PROFILS, "user_credentials": []})
    _brancher(monkeypatch, script, client, envois)

    _lancer(monkeypatch, script, ["--envoyer"])
    destinataires = {kw["to_email"] for kw in envois}
    assert "suspendu@example.org" not in destinataires, (
        "une suspension est une décision d'administration, pas un oubli à rattraper"
    )
    assert destinataires == {"Julian.Talou33@gmail.com", "partenaire@example.org"}


def test_le_jeton_en_clair_ne_va_que_dans_l_email(monkeypatch, script, envois):
    client = _FauxClient({"profiles": PROFILS, "user_credentials": []})
    _brancher(monkeypatch, script, client, envois)

    _lancer(monkeypatch, script, ["--email", "julian.talou33@gmail.com", "--envoyer"])
    assert len(envois) == 1

    # Le lien porte le jeton en clair : on l'extrait comme le ferait le
    # destinataire, puis on vérifie qu'il n'est écrit NULLE PART en base.
    corps = envois[0]["html"]
    assert "/reset-password?token=" in corps
    clair = corps.split("/reset-password?token=")[1].split('"')[0].split("&")[0]
    assert len(clair) >= 40, "jeton trop court pour 256 bits d'entropie"

    ecrits = repr(client.journal)
    assert clair not in ecrits, "le jeton en clair a été écrit en base"

    # Ce qui EST écrit, c'est son empreinte SHA-256 — et elle correspond.
    from services import passwords
    (_, _, maj), = [e for e in client.journal if e[:2] == ("update", "user_credentials")]
    assert maj["reset_token_hash"] == passwords.hash_reset_token(clair)
    assert maj["reset_token_expires_at"]


def test_le_hachage_de_remplissage_est_different_a_chaque_compte(monkeypatch, script, envois):
    client = _FauxClient({"profiles": PROFILS, "user_credentials": []})
    _brancher(monkeypatch, script, client, envois)

    _lancer(monkeypatch, script, ["--envoyer"])
    hachages = [
        data["password_hash"]
        for op, table, data in client.journal
        if (op, table) == ("insert", "user_credentials")
    ]
    assert len(hachages) == 2
    assert all(h.startswith("$argon2id$") for h in hachages)
    assert len(set(hachages)) == len(hachages), (
        "un hachage commun désignerait les comptes en attente à qui lit la base"
    )


def test_un_compte_deja_pourvu_est_ignore_sans_relance(monkeypatch, script, envois):
    client = _FauxClient({
        "profiles": PROFILS,
        "user_credentials": [{"user_id": PROFILS[0]["id"]}],
    })
    _brancher(monkeypatch, script, client, envois)

    _lancer(monkeypatch, script, ["--envoyer"])
    assert {kw["to_email"] for kw in envois} == {"partenaire@example.org"}

    # …mais --relancer le réarme : c'est le recours quand le lien a expiré.
    envois.clear()
    client.journal.clear()
    _lancer(monkeypatch, script, ["--envoyer", "--relancer"])
    assert "Julian.Talou33@gmail.com" in {kw["to_email"] for kw in envois}
    reecrits = [
        data["user_id"] for op, table, data in client.journal
        if (op, table) == ("insert", "user_credentials")
    ]
    assert PROFILS[0]["id"] not in reecrits, (
        "une relance a réécrit les identifiants d'un compte déjà pourvu : le mot "
        "de passe de la personne aurait été détruit sans qu'elle demande rien"
    )


def test_l_email_explique_pourquoi_il_arrive(script):
    """Un lien de mot de passe non sollicité ressemble à un hameçonnage.

    C'est le seul e-mail de la plateforme que le destinataire n'a pas demandé et
    qui lui réclame une action sur son mot de passe. Une recette en conditions
    réelles l'a dit sans détour : l'expéditeur appartient à un autre domaine que
    la marque, et toute formation anti-hameçonnage apprend à ne pas cliquer.

    Trois choses doivent donc y figurer, et leur absence est un défaut :
    pourquoi il arrive maintenant, ce qui NE change pas, et un moyen de vérifier
    qui ne consiste PAS à nous croire sur parole. Écrire « faites-nous
    confiance » n'a aucune valeur : un hameçonneur écrirait la même phrase.
    """
    from services import email_templates

    sujet, html, texte = email_templates.build_email(
        "password_migration",
        {"name": "Julian", "link": "https://exemple.test/reset-password?token=xyz",
         "validite": "7 jours", "plateforme": "https://exemple.test"},
    )
    assert "Julian" in html
    assert "https://exemple.test/reset-password?token=xyz" in html
    assert "7 jours" in html
    # Ce que le modèle « mot de passe oublié » dirait à tort ici.
    assert "Vous avez demandé" not in html

    # 1. Pourquoi maintenant, sans l'avoir demandé.
    assert "changé de serveur" in html
    assert "rien demandé" in html
    # 2. Ce qui ne change pas.
    for attendu in ("intacts", "double authentification"):
        assert attendu in html, f"l'e-mail ne mentionne pas « {attendu} »"
    # 3. Une vérification qui ne passe pas par cet e-mail, et un interlocuteur.
    assert "Ne cliquez pas" in html, (
        "aucune issue pour le destinataire prudent : il ne lui reste qu'à cliquer "
        "ou à renoncer"
    )
    assert "https://exemple.test/contact" in html, "aucun interlocuteur de vérification"

    assert "définissez votre mot de passe" in sujet.lower()
    assert texte.strip(), "aucune version texte : l'e-mail passerait mal certains filtres"


def test_aucune_variable_ne_reste_non_remplacee(script, monkeypatch, envois):
    """Un `{plateforme}` littéral dans l'e-mail reçu, c'est l'e-mail décrédibilisé.

    Le modèle porte quatre variables et deux appelants les fournissent. Ajouter
    une variable au modèle sans l'ajouter aux DEUX appelants ne casse rien, ne
    lève rien, et se voit uniquement dans la boîte du destinataire — sur un
    message dont tout l'enjeu est d'avoir l'air légitime.

    On vérifie donc sur l'e-mail réellement produit par le script, pas sur un
    contexte fabriqué pour le test.
    """
    import re

    client = _FauxClient({"profiles": PROFILS, "user_credentials": []})
    _brancher(monkeypatch, script, client, envois)
    _lancer(monkeypatch, script, ["--email", "julian.talou33@gmail.com", "--envoyer"])

    assert len(envois) == 1
    for champ in ("subject", "html", "text"):
        restant = re.findall(r"\{[a-z_]+\}", envois[0][champ])
        assert not restant, f"variables non remplacées dans {champ} : {restant}"
