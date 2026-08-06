"""
Hachage des mots de passe — les propriétés dont dépend toute l'authentification.

Ce fichier ne teste PAS Argon2 (c'est le travail d'argon2-cffi). Il teste la
façon dont on s'en sert, et surtout les endroits où une régression ne se verrait
pas : un `verify_password` qui lèverait au lieu de renvoyer False rendrait la
connexion impossible sur un hachage corrompu ; un `check_password` sans borne
haute rouvrirait un déni de service sur une route publique.
"""
import pytest

from services import passwords

PW = "correct horse battery staple"


def test_hash_is_argon2id_with_the_declared_parameters():
    """La chaîne PHC porte ses paramètres : on peut donc vérifier ce qui a servi.

    C'est le seul contrôle qui attrape une baisse silencieuse du coût — quelqu'un
    qui divise MEMORY_COST_KIB par dix pour « accélérer les tests » ne casse
    aucun autre test.
    """
    h = passwords.hash_password(PW)
    assert h.startswith("$argon2id$"), f"algorithme inattendu : {h[:20]}"
    assert f"m={passwords.MEMORY_COST_KIB}" in h
    assert f"t={passwords.TIME_COST}" in h
    assert f"p={passwords.PARALLELISM}" in h
    # OWASP : 19 Mio est le plancher pour t=2. En dessous, Argon2id ne vaut plus
    # mieux que bcrypt face à une attaque parallèle.
    assert passwords.MEMORY_COST_KIB >= 19456
    assert passwords.TIME_COST >= 2


def test_same_password_hashes_differently():
    """Deux hachages du même mot de passe diffèrent : le sel est bien aléatoire.

    Sans sel, deux comptes partageant un mot de passe seraient visibles comme
    tels dans une copie de la table, et une seule table pré-calculée les
    ouvrirait tous les deux.
    """
    assert passwords.hash_password(PW) != passwords.hash_password(PW)


def test_verify_accepts_the_right_password_and_refuses_the_others():
    h = passwords.hash_password(PW)
    assert passwords.verify_password(h, PW) is True
    assert passwords.verify_password(h, PW + " ") is False
    assert passwords.verify_password(h, PW.upper()) is False
    assert passwords.verify_password(h, "") is False


def test_verify_never_raises_on_garbage():
    """Un hachage illisible doit donner False, jamais une exception.

    Une exception remonterait en 500 alors que les autres comptes répondent 401 :
    l'écart suffirait à repérer le compte dont la ligne est abîmée.
    """
    for mauvais in ("", None, "pas-un-hachage", "$argon2id$tronqué", "$2a$10$abc"):
        assert passwords.verify_password(mauvais, PW) is False


def test_long_passphrases_are_fully_significant():
    """Contrôle anti-bcrypt : au-delà de 72 octets, tout compte encore.

    bcrypt tronquait à 72 octets sans le dire — deux phrases de passe qui ne
    diffèrent qu'après le 72e caractère y étaient interchangeables. Ce test
    échouerait si l'on revenait à bcrypt.
    """
    base = "a" * 72
    h = passwords.hash_password(base + "SUFFIXE-UN")
    assert passwords.verify_password(h, base + "SUFFIXE-UN") is True
    assert passwords.verify_password(h, base + "SUFFIXE-DEUX") is False


def test_short_password_is_rejected():
    with pytest.raises(passwords.PasswordRejected):
        passwords.check_password("a" * (passwords.MIN_LENGTH - 1))


def test_oversized_password_is_rejected_before_hashing():
    """Argon2 hache TOUT ce qu'on lui donne — la borne haute est indispensable.

    bcrypt tronquait à 72 octets, ce qui bornait le coût d'office. Sans
    MAX_BYTES, un POST de 10 Mio dans le champ « mot de passe » ferait travailler
    le serveur pendant des secondes, sur une route publique et non authentifiée.
    """
    with pytest.raises(passwords.PasswordRejected):
        passwords.check_password("a" * (passwords.MAX_BYTES + 1))
    with pytest.raises(passwords.PasswordRejected):
        passwords.hash_password("a" * (passwords.MAX_BYTES + 1))


def test_needs_rehash_tracks_the_current_parameters():
    """Un hachage produit avec les paramètres courants n'est pas à refaire ;
    un hachage plus faible l'est. C'est ce qui permettra de relever le coût
    plus tard sans imposer une réinitialisation générale."""
    h = passwords.hash_password(PW)
    assert passwords.needs_rehash(h) is False

    from argon2 import PasswordHasher, Type
    faible = PasswordHasher(
        time_cost=1, memory_cost=8, parallelism=1,
        hash_len=passwords.HASH_LEN, salt_len=passwords.SALT_LEN, type=Type.ID,
    ).hash(PW)
    assert passwords.needs_rehash(faible) is True
    # Et il reste vérifiable : on ne verrouille personne dehors en changeant les
    # paramètres, on le rehache à sa connexion suivante.
    assert passwords.verify_password(faible, PW) is True


def test_needs_rehash_on_garbage_is_true():
    """Hachage illisible → à refaire. En cas de doute, on ne conserve pas."""
    assert passwords.needs_rehash("") is True
    assert passwords.needs_rehash("pas-un-hachage") is True


def test_dummy_hash_is_usable_and_stable():
    """Le hachage jetable sert à égaliser le temps de réponse sur e-mail inconnu.

    Il doit être un VRAI hachage argon2id (sinon la vérification échoue tout de
    suite et l'écart de temps réapparaît) et rester stable dans le processus
    (sinon on paierait un hachage complet à chaque adresse inconnue, ce qui
    offrirait justement le levier de charge qu'on cherche à retirer).
    """
    d = passwords.dummy_hash()
    assert d.startswith("$argon2id$")
    assert d is passwords.dummy_hash()
    assert passwords.verify_password(d, "n'importe quoi") is False
