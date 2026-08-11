"""
« Mot de passe oublié » doit fonctionner pour un compte hérité de Supabase.

L'IMPASSE QUE CES TESTS FERMENT

La migration 0019 ne reprend aucun hachage bcrypt : les comptes existants n'ont
pas de ligne `user_credentials`. Or `/auth/forgot-password` partait de
`credentials.by_email` — qui renvoie None dans ce cas. L'endpoint répondait donc
« si un compte existe, un lien a été envoyé » sans rien envoyer.

C'est la pire forme de panne possible ici : elle est INVISIBLE des deux côtés.
L'utilisateur voit un message de succès et attend un e-mail qui n'arrivera
jamais ; les journaux disent « adresse sans compte », ce qui est faux. Et cet
endpoint est précisément le recours que l'écran de connexion propose à quelqu'un
que la migration vient de bloquer.

Le second axe testé est la discrétion : le rattrapage ne doit pas transformer
l'endpoint en oracle d'existence des comptes. Réponse identique, toujours.
"""
import sys
import types

import pytest


@pytest.fixture
def auth(monkeypatch):
    """Le routeur, avec un client de base factice et sans limitation de débit."""
    import routers.auth as module

    monkeypatch.setattr(module, "_throttle", lambda *_a, **_k: None)
    return module


class _Requete:
    def __init__(self, table, journal, lignes):
        self._table, self._journal, self._lignes = table, journal, lignes
        self._resultat = []
        # PostgREST applique `.eq()` comme un FILTRE d'écriture après un update :
        # la réponse est la ligne touchée, pas la charge utile filtrée. Sans
        # cette distinction, le faux client rendrait « 0 ligne mise à jour » sur
        # un update parfaitement valide.
        self._ecriture = False

    def select(self, *_a, **_k):
        self._resultat = self._lignes.get(self._table, [])
        return self

    def insert(self, data, **_k):
        self._journal.append(("insert", self._table, data))
        self._lignes.setdefault(self._table, []).append(data)
        self._resultat = [data]
        self._ecriture = True
        return self

    def update(self, data, **_k):
        self._journal.append(("update", self._table, data))
        self._resultat = [data]
        self._ecriture = True
        return self

    def eq(self, colonne, valeur):
        if self._ecriture:
            return self
        self._resultat = [l for l in self._resultat if l.get(colonne) == valeur]
        return self

    def ilike(self, colonne, valeur):
        cible = (valeur or "").lower()
        self._resultat = [
            l for l in self._resultat if (l.get(colonne) or "").lower() == cible
        ]
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("Reponse", (), {"data": list(self._resultat)})()


class _FauxClient:
    def __init__(self, lignes):
        self.journal = []
        self.lignes = lignes

    def table(self, nom):
        return _Requete(nom, self.journal, self.lignes)


class _FausseRequete:
    """Le minimum que `_client_ip` sait lire."""
    client = types.SimpleNamespace(host="203.0.113.7")
    headers: dict = {}


def _brancher(monkeypatch, auth, client, envois):
    from services import credentials

    monkeypatch.setattr(auth, "supabase", client)
    monkeypatch.setattr(credentials, "supabase", client)
    monkeypatch.setattr(
        auth, "_send_reset_email",
        lambda to_email, url, cle="password_reset", contexte_sup=None:
            (envois.append({"to": to_email, "url": url, "cle": cle,
                            "contexte": contexte_sup or {}}) or (True, None)),
    )


async def _demander(auth, email):
    return await auth.forgot_password(
        auth.ForgotPasswordRequest(email=email), _FausseRequete()
    )


PROFIL = {
    "id": "cccc3333-0000-0000-0000-000000000001",
    "email": "Romain.Aumard@uti-group.com",
    "name": "Romain AUMARD",
    "status": "active",
}


@pytest.mark.asyncio
async def test_un_compte_herite_recoit_bien_un_lien(monkeypatch, auth):
    envois = []
    client = _FauxClient({"profiles": [PROFIL], "user_credentials": []})
    _brancher(monkeypatch, auth, client, envois)

    await _demander(auth, "romain.aumard@uti-group.com")

    assert len(envois) == 1, (
        "aucun e-mail : l'endpoint a répondu « un lien a été envoyé » sans rien envoyer"
    )
    # Le modèle de MIGRATION, pas celui de réinitialisation : cette personne n'a
    # jamais eu de mot de passe chez nous, « vous avez demandé à réinitialiser »
    # serait faux et inquiétant.
    assert envois[0]["cle"] == "password_migration"
    assert envois[0]["contexte"].get("name") == "Romain"

    # La ligne d'identifiants a été créée, avec un hachage inutilisable.
    (_, _, ligne), = [e for e in client.journal if e[:2] == ("insert", "user_credentials")]
    assert ligne["user_id"] == PROFIL["id"]
    assert ligne["email"] == "romain.aumard@uti-group.com"
    assert ligne["password_hash"].startswith("$argon2id$")


@pytest.mark.asyncio
async def test_le_lien_d_un_compte_herite_dure_plus_qu_une_heure(monkeypatch, auth):
    """Ce lien n'est pas demandé : il est SUBI. Une heure ne suffit pas.

    Quelqu'un qui clique sur « mot de passe oublié » relève sa boîte dans la
    minute. Quelqu'un que la migration bloque découvre le problème au pire
    moment, souvent depuis un téléphone, et traitera le sujet plus tard.
    """
    from datetime import datetime, timezone

    envois = []
    client = _FauxClient({"profiles": [PROFIL], "user_credentials": []})
    _brancher(monkeypatch, auth, client, envois)

    await _demander(auth, "romain.aumard@uti-group.com")

    (_, _, maj), = [e for e in client.journal if e[:2] == ("update", "user_credentials")]
    echeance = datetime.fromisoformat(maj["reset_token_expires_at"])
    restant = (echeance - datetime.now(timezone.utc)).total_seconds()
    assert restant > 6 * 24 * 3600, "échéance trop courte pour un compte hérité"


@pytest.mark.asyncio
async def test_la_reponse_ne_dit_pas_si_le_compte_existe(monkeypatch, auth):
    """Le rattrapage ne doit pas transformer l'endpoint en annuaire des comptes."""
    envois = []
    client = _FauxClient({"profiles": [PROFIL], "user_credentials": []})
    _brancher(monkeypatch, auth, client, envois)

    connu = await _demander(auth, "romain.aumard@uti-group.com")
    inconnu = await _demander(auth, "personne@nulle-part.example")

    assert connu == inconnu, "la réponse trahit l'existence du compte"
    assert len(envois) == 1


@pytest.mark.asyncio
async def test_un_compte_suspendu_n_est_pas_rouvert(monkeypatch, auth):
    """Une suspension est une décision d'administration.

    Ce n'est pas à un formulaire public, rempli par n'importe qui, de la défaire.
    """
    envois = []
    client = _FauxClient({
        "profiles": [dict(PROFIL, status="suspended")],
        "user_credentials": [],
    })
    _brancher(monkeypatch, auth, client, envois)

    await _demander(auth, "romain.aumard@uti-group.com")

    assert envois == []
    assert not [e for e in client.journal if e[0] == "insert"]


@pytest.mark.asyncio
async def test_un_compte_deja_migre_recoit_le_modele_ordinaire(monkeypatch, auth):
    """Une fois le mot de passe posé, c'est une réinitialisation comme une autre."""
    envois = []
    client = _FauxClient({
        "profiles": [PROFIL],
        "user_credentials": [{
            "user_id": PROFIL["id"], "email": "romain.aumard@uti-group.com",
            "password_hash": "$argon2id$déjà-posé", "password_defini": True,
        }],
    })
    _brancher(monkeypatch, auth, client, envois)

    await _demander(auth, "romain.aumard@uti-group.com")

    assert len(envois) == 1
    assert envois[0]["cle"] == "password_reset"
    assert not [e for e in client.journal if e[:2] == ("insert", "user_credentials")], (
        "les identifiants d'un compte déjà migré ont été réécrits"
    )


@pytest.mark.asyncio
async def test_deux_demandes_de_suite_restent_une_migration(monkeypatch, auth):
    """Le geste le plus banal du monde : recliquer parce que l'e-mail tarde.

    CE QUI SE PASSAIT (constaté en production le 11 août, deux appels à 1,5 s
    d'intervalle). La première demande CRÉE la ligne d'identifiants — le signe
    auquel on reconnaissait un compte hérité. La seconde ne le trouvait donc
    plus et repartait en réinitialisation ordinaire :

        clic 1  →  « la plateforme a changé de serveur », lien valable 7 jours
        clic 2  →  « vous avez demandé à réinitialiser », lien valable 1 HEURE

    Et comme `issue_reset` écrase le jeton précédent, le second clic TUAIT le
    lien du premier. Il restait donc à l'utilisateur deux e-mails qui se
    contredisent, dont le seul encore valide lui parlait d'une demande qu'il
    n'avait pas faite et expirait soixante fois plus vite.

    Rien n'échouait, rien n'était journalisé comme anormal.
    """
    from datetime import datetime, timezone

    envois = []
    client = _FauxClient({"profiles": [PROFIL], "user_credentials": []})
    _brancher(monkeypatch, auth, client, envois)

    await _demander(auth, "romain.aumard@uti-group.com")
    await _demander(auth, "romain.aumard@uti-group.com")

    assert len(envois) == 2
    assert [e["cle"] for e in envois] == ["password_migration", "password_migration"], (
        "la deuxième demande a basculé en réinitialisation ordinaire"
    )

    # Et la validité ne s'effondre pas non plus au second clic.
    for _, _, maj in [e for e in client.journal if e[:2] == ("update", "user_credentials")]:
        echeance = datetime.fromisoformat(maj["reset_token_expires_at"])
        restant = (echeance - datetime.now(timezone.utc)).total_seconds()
        assert restant > 6 * 24 * 3600, "un des deux liens ne vaut qu'une heure"

    # Une seule ligne créée : la seconde demande ne réinsère pas.
    inserts = [e for e in client.journal if e[:2] == ("insert", "user_credentials")]
    assert len(inserts) == 1
    assert inserts[0][2]["password_defini"] is False, (
        "la ligne provisionnée se déclare « mot de passe choisi » : le compte "
        "sortirait de la liste des personnes à relancer sans que personne n'ait rien choisi"
    )


@pytest.mark.asyncio
async def test_colonne_absente_on_retombe_sur_le_comportement_ordinaire(monkeypatch, auth):
    """Si la migration 0020 n'est pas appliquée, ne pas traiter tout le monde en migré.

    Une base sans la colonne `password_defini` renvoie des lignes qui n'ont pas
    la clé. Lire une absence comme « false » ferait envoyer l'e-mail de MIGRATION
    à des comptes ordinaires — et leur annoncerait que leur mot de passe n'existe
    plus, ce qui serait faux. En cas de doute on retombe donc sur l'ancien
    comportement, qui est vrai de toutes les lignes antérieures à 0020.
    """
    envois = []
    client = _FauxClient({
        "profiles": [PROFIL],
        "user_credentials": [{
            "user_id": PROFIL["id"], "email": "romain.aumard@uti-group.com",
            "password_hash": "$argon2id$peu-importe",  # pas de clé password_defini
        }],
    })
    _brancher(monkeypatch, auth, client, envois)

    await _demander(auth, "romain.aumard@uti-group.com")

    assert len(envois) == 1
    assert envois[0]["cle"] == "password_reset"
