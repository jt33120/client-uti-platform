"""
Verrouillage après échecs — la partie qui doit survivre à un redémarrage.

Le garde-fou historique (`_throttle`, routers/auth.py:33) vit dans la MÉMOIRE du
processus : `bash deploy.sh` fait un `systemctl restart`, et le compteur repart
de zéro. Un attaquant patient n'a donc qu'à attendre un déploiement — ou, le
jour où l'on passera à plusieurs workers uvicorn, qu'à retomber sur un autre
processus. D'où un compteur PERSISTANT en base, testé ici sur ses fonctions
pures, sans base.
"""
from datetime import datetime, timedelta, timezone

from services import credentials as cred

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def test_first_attempts_are_not_locked():
    """On ne verrouille pas sur une faute de frappe."""
    for n in range(cred.LOCK_AFTER):
        assert cred.lock_delay_minutes(n) is None


def test_lock_starts_at_the_declared_threshold():
    assert cred.lock_delay_minutes(cred.LOCK_AFTER) == cred.LOCK_STEPS_MINUTES[0]


def test_lock_duration_grows_then_plateaus():
    """Croissante pour décourager l'insistance, PLAFONNÉE pour ne pas devenir une arme.

    Sans plafond, dix mauvais mots de passe saisis sur l'adresse d'un dirigeant
    lui interdiraient la plateforme jusqu'à intervention manuelle : le dispositif
    anti-force-brute se retournerait en déni de service ciblé.
    """
    durees = [cred.lock_delay_minutes(cred.LOCK_AFTER + i) for i in range(12)]
    assert durees == sorted(durees), "la durée doit croître, jamais décroître"
    assert durees[0] < durees[-1]
    assert max(durees) == cred.LOCK_MAX_MINUTES
    assert cred.LOCK_MAX_MINUTES <= 60, "un verrou de plus d'une heure est un DoS sur le titulaire"


def test_failure_increments_and_arms_the_lock():
    ligne = {"user_id": "u-1", "failed_attempts": cred.LOCK_AFTER - 1, "locked_until": None}
    patch = cred.failure_patch(ligne, NOW)
    assert patch["failed_attempts"] == cred.LOCK_AFTER
    attendu = NOW + timedelta(minutes=cred.LOCK_STEPS_MINUTES[0])
    assert patch["locked_until"] == attendu.isoformat()


def test_failure_below_threshold_leaves_the_account_open():
    patch = cred.failure_patch({"user_id": "u-1", "failed_attempts": 0}, NOW)
    assert patch["failed_attempts"] == 1
    assert patch["locked_until"] is None


def test_failure_patch_tolerates_a_missing_counter():
    """Ligne créée avant la migration, ou colonne à NULL : on repart de 0, on ne casse pas."""
    assert cred.failure_patch({"user_id": "u-1"}, NOW)["failed_attempts"] == 1
    assert cred.failure_patch({"user_id": "u-1", "failed_attempts": None}, NOW)["failed_attempts"] == 1


def test_success_clears_the_counter_and_the_lock():
    patch = cred.success_patch()
    assert patch == {"failed_attempts": 0, "locked_until": None}


def test_lock_seconds_remaining_counts_down_then_releases():
    ligne = {"locked_until": (NOW + timedelta(minutes=5)).isoformat()}
    assert cred.lock_seconds_remaining(ligne, NOW) == 301  # arrondi au-dessus : jamais « 0 s d'attente »
    assert cred.lock_seconds_remaining(ligne, NOW + timedelta(minutes=4)) > 0
    assert cred.lock_seconds_remaining(ligne, NOW + timedelta(minutes=5)) == 0
    assert cred.lock_seconds_remaining(ligne, NOW + timedelta(minutes=6)) == 0


def test_no_lock_when_the_column_is_empty():
    assert cred.lock_seconds_remaining({}, NOW) == 0
    assert cred.lock_seconds_remaining({"locked_until": None}, NOW) == 0
    assert cred.lock_seconds_remaining(None, NOW) == 0


def test_unreadable_lock_does_not_wall_the_account_in():
    """Une valeur mal formée ne doit pas barrer un compte pour toujours.

    Choix inverse de celui fait sur l'expiration des jetons de réinitialisation
    (où le doute vaut refus) : ici, laisser passer une tentative ne donne rien à
    l'attaquant — le compteur d'échecs, lui, reste opérant — alors qu'un verrou
    indélébile coûterait un accès à un utilisateur légitime.
    """
    assert cred.lock_seconds_remaining({"locked_until": "pas une date"}, NOW) == 0


def test_naive_timestamp_is_read_as_utc():
    """PostgREST peut renvoyer un horodatage sans fuseau selon la colonne ;
    l'interpréter en heure locale décalerait le verrou de plusieurs heures."""
    naif = (NOW + timedelta(minutes=10)).replace(tzinfo=None).isoformat()
    assert cred.lock_seconds_remaining({"locked_until": naif}, NOW) > 0


def test_verify_against_a_missing_row_is_false_but_still_hashes():
    """E-mail inconnu : on renvoie False APRÈS avoir payé un vrai hachage.

    Sans cela, `/auth/login` répond en 2 ms pour une adresse inexistante et en
    ~60 ms pour une adresse existante. Le message d'erreur est identique dans les
    deux cas, mais le chronomètre, lui, énumère les comptes.
    """
    import time
    from services import passwords

    passwords.dummy_hash()  # on sort le coût de première utilisation de la mesure

    debut = time.perf_counter()
    assert cred.verify(None, "peu importe") is False
    inconnu = time.perf_counter() - debut

    ligne = {"user_id": "u-1", "password_hash": passwords.hash_password("le-bon-mot-de-passe")}
    debut = time.perf_counter()
    assert cred.verify(ligne, "mauvais-mot-de-passe") is False
    connu = time.perf_counter() - debut

    # Même ordre de grandeur : les deux paient un Argon2id complet. Borne large
    # à dessein — ce test doit attraper « on ne hache pas du tout », pas mesurer
    # la charge de la machine de CI.
    assert inconnu > connu / 4, (
        f"réponse trop rapide sur e-mail inconnu ({inconnu * 1000:.1f} ms contre "
        f"{connu * 1000:.1f} ms) : l'écart énumère les comptes"
    )


def test_verify_accepts_the_right_password():
    from services import passwords
    ligne = {"user_id": "u-1", "password_hash": passwords.hash_password("le-bon-mot-de-passe")}
    assert cred.verify(ligne, "le-bon-mot-de-passe") is True
