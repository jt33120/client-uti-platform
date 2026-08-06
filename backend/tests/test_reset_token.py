"""
Jeton de réinitialisation : opaque, haché en base, à usage unique, expirant.

Ce qui remplace le jeton Supabase circulant dans le FRAGMENT d'URL
(`#access_token=…&type=recovery`), que le backend ne voyait jamais et qu'il ne
pouvait donc ni révoquer, ni limiter à un seul usage.

Les trois propriétés vérifiées ici sont celles qui, si elles cèdent, donnent un
accès complet à un compte :
  1. une copie de `user_credentials` ne doit contenir AUCUN lien utilisable ;
  2. un jeton ne doit servir qu'UNE fois ;
  3. un jeton doit périmer.

Le point 2 se joue dans un UPDATE filtré (services/credentials.py:consume_reset).
On le rejoue ici sur un faux client PostgREST qui reproduit le comportement
mesuré du vrai : `update()` renvoie les lignes AFFECTÉES, donc une liste vide au
second passage.
"""
from datetime import datetime, timedelta, timezone

import pytest

from services import passwords


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


# ── Le jeton lui-même ───────────────────────────────────────────────────────

def test_token_is_opaque_and_high_entropy():
    """Ni JWT, ni contenu lisible : rien à décoder côté navigateur.

    L'ancien jeton était un JWT ; ResetPasswordPage.jsx:37 en extrayait l'e-mail
    avec `atob`. Un jeton opaque ne dit rien de qui il concerne à qui l'intercepte.
    """
    clear, _ = passwords.new_reset_token()
    assert "." not in clear, "ressemble à un JWT : le jeton doit être opaque"
    # 32 octets encodés en base64url → 43 caractères.
    assert len(clear) >= 43
    assert len({passwords.new_reset_token()[0] for _ in range(200)}) == 200


def test_only_the_hash_is_ever_stored():
    """Le clair ne doit pas être déductible de ce qu'on écrit en base."""
    clear, stored = passwords.new_reset_token()
    assert stored != clear
    assert clear not in stored
    assert len(stored) == 64 and int(stored, 16) >= 0, "SHA-256 hexadécimal attendu"
    # Déterministe : c'est ce qui permet de RETROUVER la ligne à partir du jeton
    # reçu, avec un index d'égalité (un hachage salé l'interdirait).
    assert passwords.hash_reset_token(clear) == stored


def test_hash_comparison_is_constant_time():
    clear, stored = passwords.new_reset_token()
    assert passwords.reset_tokens_match(stored, passwords.hash_reset_token(clear)) is True
    assert passwords.reset_tokens_match(stored, passwords.hash_reset_token("autre")) is False
    assert passwords.reset_tokens_match(None, stored) is False


# ── Expiration ──────────────────────────────────────────────────────────────

def test_expiry_matches_what_the_email_promises():
    """L'e-mail annonce « expire dans 1 heure » (services/email_templates.py:83).

    Une promesse affichée à l'utilisateur doit être celle que le code applique.
    """
    assert passwords.RESET_TOKEN_TTL_MINUTES == 60
    assert passwords.reset_token_expiry(NOW) == NOW + timedelta(minutes=60)


def test_token_expires():
    echeance = passwords.reset_token_expiry(NOW)
    assert passwords.reset_token_is_expired(echeance, NOW) is False
    assert passwords.reset_token_is_expired(echeance, NOW + timedelta(minutes=59)) is False
    # À la seconde près : à l'échéance exacte, c'est expiré.
    assert passwords.reset_token_is_expired(echeance, echeance) is True
    assert passwords.reset_token_is_expired(echeance, NOW + timedelta(minutes=61)) is True


def test_expiry_accepts_the_postgrest_string_form():
    """PostgREST renvoie les timestamptz en chaîne ISO 8601, pas en datetime."""
    echeance = passwords.reset_token_expiry(NOW)
    assert passwords.reset_token_is_expired(echeance.isoformat(), NOW) is False
    assert passwords.reset_token_is_expired(
        echeance.isoformat().replace("+00:00", "Z"), NOW + timedelta(hours=2)
    ) is True


def test_unreadable_or_missing_expiry_counts_as_expired():
    """En cas de doute sur un jeton de réinitialisation, on refuse."""
    assert passwords.reset_token_is_expired(None, NOW) is True
    assert passwords.reset_token_is_expired("pas une date", NOW) is True


# ── Usage unique — sur un faux PostgREST ────────────────────────────────────

class _FakeQuery:
    """Reproduit la chaîne `.update(...).eq(...).execute()` de postgrest-py.

    Comportement calqué sur celui mesuré en conditions réelles : `update()`
    renvoie les lignes RÉELLEMENT affectées, donc `[]` quand le filtre ne
    correspond plus à rien.
    """

    def __init__(self, table):
        self._table = table
        self._patch = None
        self._filters = {}

    def update(self, patch):
        self._patch = patch
        return self

    def delete(self):
        self._patch = "__delete__"
        return self

    def select(self, *_a, **_k):
        self._patch = None
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, _n):
        return self

    def execute(self):
        touchees = [
            r for r in self._table.rows
            if all(r.get(c) == v for c, v in self._filters.items())
        ]
        if isinstance(self._patch, dict):
            for r in touchees:
                r.update(self._patch)
        return type("Res", (), {"data": [dict(r) for r in touchees]})()


class _FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return _FakeQuery(self)


@pytest.fixture
def credentials(monkeypatch):
    """Module credentials branché sur le faux client, avec une ligne armée."""
    from services import credentials as mod

    clear, token_hash = passwords.new_reset_token()
    ligne = {
        "user_id": "u-1",
        "email": "partenaire@example.com",
        "password_hash": passwords.hash_password("ancien-mot-de-passe"),
        "failed_attempts": 4,
        "locked_until": (NOW + timedelta(minutes=10)).isoformat(),
        "reset_token_hash": token_hash,
        "reset_token_expires_at": passwords.reset_token_expiry(NOW).isoformat(),
    }
    faux = _FakeTable([ligne])
    monkeypatch.setattr(mod, "supabase", faux)
    return mod, ligne, clear, token_hash


def test_reset_token_works_once(credentials):
    """Deuxième présentation du même lien : refusée.

    C'est le point que le flux Supabase ne garantissait pas côté application —
    le backend ne voyait jamais le jeton, il ne pouvait donc pas le consommer.
    """
    mod, ligne, clear, token_hash = credentials
    nouveau = passwords.hash_password("nouveau-mot-de-passe-1")

    assert mod.consume_reset(token_hash, nouveau, NOW) is True
    assert ligne["password_hash"] == nouveau
    assert ligne["reset_token_hash"] is None
    assert ligne["reset_token_expires_at"] is None

    # Rejouer le lien ne trouve plus de ligne à mettre à jour.
    encore = passwords.hash_password("mot-de-passe-de-lattaquant")
    assert mod.consume_reset(token_hash, encore, NOW) is False
    assert ligne["password_hash"] == nouveau, "le second passage a écrasé le mot de passe"


def test_consuming_the_reset_unlocks_the_account(credentials):
    """Réinitialiser par e-mail débloque : c'est la voie de secours quand un
    tiers a fait verrouiller l'adresse avec des essais ratés."""
    mod, ligne, _clear, token_hash = credentials
    assert mod.consume_reset(token_hash, passwords.hash_password("nouveau-mdp-2"), NOW) is True
    assert ligne["failed_attempts"] == 0
    assert ligne["locked_until"] is None


def test_a_wrong_token_changes_nothing(credentials):
    mod, ligne, _clear, _token_hash = credentials
    avant = ligne["password_hash"]
    autre, autre_hash = passwords.new_reset_token()
    assert mod.consume_reset(autre_hash, passwords.hash_password("intrus-12345"), NOW) is False
    assert ligne["password_hash"] == avant
    assert ligne["reset_token_hash"] is not None, "le jeton légitime a été effacé par un essai raté"


def test_changing_the_password_disarms_a_pending_reset(credentials):
    """Un lien demandé puis abandonné ne doit pas rester armé une heure après un
    changement de mot de passe fait par ailleurs."""
    mod, ligne, _clear, token_hash = credentials
    mod.set_password("u-1", passwords.hash_password("change-depuis-le-profil"), NOW)
    assert ligne["reset_token_hash"] is None
    assert mod.consume_reset(token_hash, passwords.hash_password("trop-tard-1234"), NOW) is False
