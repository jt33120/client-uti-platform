"""
Supprimer un compte doit laisser une trace, et cette trace doit dire QUI.

CE QUE CES TESTS PROTÈGENT

Le 26 août 2026, la question « ce compte a-t-il été supprimé ? » s'est posée en
production. La base n'a pas pu répondre : aucun type d'événement d'`audit_log`
ne couvrait la suppression d'un compte — ni pour confirmer, ni pour infirmer.
Une plateforme soumise à l'AI Act art. 12 ne peut pas être muette sur la
disparition d'un acteur dont les décisions restent journalisées.

L'ORDRE DE LECTURE EST LE CŒUR DU SUJET

Journaliser après coup avec le seul UUID ne servirait presque à rien : c'est
justement ce qui reste partout ailleurs. `audit_log.actor_id`,
`human_decision.decided_by` et `submissions.submitted_by` continuent de
référencer la personne effacée. Sans l'adresse et le nom capturés AVANT le
DELETE, l'archive de conformité devient une suite d'UUID qui ne désignent plus
personne — le motif exact pour lequel BASCULE.md §2 exige d'exporter la
correspondance `auth.users` avant de supprimer Supabase.

ET RIEN NE DOIT ÊTRE JOURNALISÉ SI LA SUPPRESSION ÉCHOUE

Une trace de suppression pour un compte toujours vivant est pire que pas de
trace : elle ferait chercher au mauvais endroit, le jour où l'on cherche.
"""
import asyncio

import pytest

from routers import admin

COMPTE = "aaaa1111-2222-3333-4444-555555555555"
ADMIN = "bbbb9999-8888-7777-6666-555555555555"

PROFIL = {
    "id": COMPTE,
    "email": "personne.supprimee@example.org",
    "name": "Personne Supprimée",
    "role": "commerce",
    "status": "active",
    "created_at": "2026-05-06T07:34:06+00:00",
}


class _Requete:
    def __init__(self, table, journal, lignes, delete_leve):
        self._table, self._journal = table, journal
        self._lignes, self._delete_leve = lignes, delete_leve
        self._op = None

    def select(self, *_a, **_k):
        self._op = "select"; return self

    def delete(self, *_a, **_k):
        self._op = "delete"; return self

    def insert(self, data, **_k):
        self._journal.append(("insert", self._table, data)); self._op = "insert"
        self._data = data; return self

    def eq(self, *_a, **_k): return self

    def limit(self, *_a, **_k): return self

    def execute(self):
        if self._op == "delete":
            if self._delete_leve:
                raise RuntimeError("DELETE refusé par la base")
            self._journal.append(("delete", self._table, None))
            return type("R", (), {"data": []})()
        if self._op == "select":
            self._journal.append(("select", self._table, None))
            return type("R", (), {"data": self._lignes})()
        return type("R", (), {"data": [getattr(self, "_data", {})]})()


class _Supabase:
    def __init__(self, lignes, delete_leve=False):
        self.journal = []
        self._lignes, self._delete_leve = lignes, delete_leve

    def table(self, nom):
        return _Requete(nom, self.journal, self._lignes, self._delete_leve)


def _supprimer(faux, monkeypatch):
    monkeypatch.setattr(admin, "supabase", faux)
    monkeypatch.setattr(admin.audit, "supabase", faux, raising=False)
    return asyncio.run(admin.delete_account(COMPTE, user={"sub": ADMIN, "role": "admin"}))


def _lignes_audit(faux):
    return [d for op, t, d in faux.journal if op == "insert" and t == "audit_log"]


def test_la_suppression_est_journalisee(monkeypatch):
    faux = _Supabase([PROFIL])
    _supprimer(faux, monkeypatch)
    lignes = _lignes_audit(faux)
    assert len(lignes) == 1, "Aucune trace : la suppression redevient invisible."
    assert lignes[0]["event_type"] == "account_deleted"
    assert lignes[0]["actor_id"] == ADMIN, "On doit savoir QUI a supprimé."


def test_la_trace_porte_l_identite_et_pas_seulement_un_uuid(monkeypatch):
    """Un UUID seul ne sert à rien : c'est ce qui reste déjà partout ailleurs."""
    faux = _Supabase([PROFIL])
    _supprimer(faux, monkeypatch)
    p = _lignes_audit(faux)[0]["payload"]
    assert p["email"] == PROFIL["email"], (
        "Sans l'adresse, audit_log.actor_id et human_decision.decided_by "
        "désignent quelqu'un que plus rien ne permet de nommer."
    )
    assert p["nom"] == PROFIL["name"]
    assert p["role"] == PROFIL["role"]
    assert p["compte_id"] == COMPTE
    assert p["identite_capturee"] is True


def test_le_profil_est_lu_avant_d_etre_supprime(monkeypatch):
    """Après le DELETE, il n'y a plus rien à lire. L'ordre est la correction."""
    faux = _Supabase([PROFIL])
    _supprimer(faux, monkeypatch)
    ops = [(op, t) for op, t, _ in faux.journal]
    assert ("select", "profiles") in ops and ("delete", "profiles") in ops
    assert ops.index(("select", "profiles")) < ops.index(("delete", "profiles")), (
        "Le profil est lu APRÈS la suppression : la trace ne peut plus porter "
        "que l'UUID, ce qui la vide de son intérêt."
    )


def test_rien_n_est_journalise_si_la_suppression_echoue(monkeypatch):
    """Une trace de suppression pour un compte vivant ferait chercher à tort."""
    faux = _Supabase([PROFIL], delete_leve=True)
    monkeypatch.setattr(admin, "supabase", faux)
    monkeypatch.setattr(admin.audit, "supabase", faux, raising=False)
    with pytest.raises(RuntimeError):
        asyncio.run(admin.delete_account(COMPTE, user={"sub": ADMIN, "role": "admin"}))
    assert _lignes_audit(faux) == [], "Suppression échouée, mais journalisée."


def test_un_profil_introuvable_donne_un_trou_explicite(monkeypatch):
    """Mieux vaut un champ nul qu'une identité reconstituée."""
    faux = _Supabase([])
    _supprimer(faux, monkeypatch)
    p = _lignes_audit(faux)[0]["payload"]
    assert p["identite_capturee"] is False
    assert p["email"] is None
    assert p["compte_id"] == COMPTE, "L'UUID reste, même sans identité."


def test_on_ne_supprime_toujours_pas_son_propre_compte(monkeypatch):
    """Garde-fou préexistant : il ne doit pas tomber en ajoutant l'audit."""
    from fastapi import HTTPException
    faux = _Supabase([PROFIL])
    monkeypatch.setattr(admin, "supabase", faux)
    with pytest.raises(HTTPException) as e:
        asyncio.run(admin.delete_account(ADMIN, user={"sub": ADMIN, "role": "admin"}))
    assert e.value.status_code == 400
    assert faux.journal == [], "Rien ne doit être touché sur un refus."
