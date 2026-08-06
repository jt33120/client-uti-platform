"""
Hachage des mots de passe et jetons de réinitialisation — remplacement de GoTrue.

DEUX SECRETS, DEUX TRAITEMENTS, ET C'EST VOLONTAIRE
---------------------------------------------------

* Le MOT DE PASSE est choisi par un humain : entropie faible, devinable, souvent
  réutilisé ailleurs. Il faut rendre CHAQUE essai coûteux, y compris pour un
  attaquant qui a volé une copie de la base → Argon2id, mémoire-dur.

* Le JETON DE RÉINITIALISATION est tiré par `secrets` : 256 bits d'entropie.
  Il n'y a rien à deviner — énumérer 2^256 valeurs est hors de portée, quel que
  soit le coût unitaire. Le hacher ne sert donc PAS à ralentir un attaquant,
  mais à ce qu'une copie de la table ne contienne aucun lien de réinitialisation
  utilisable. SHA-256 remplit exactement cet office, en gardant la recherche sur
  un simple index d'égalité. Un Argon2 ici ajouterait 40 ms par tentative sans
  rien apporter, et rendrait la recherche par jeton impossible (sel aléatoire).

POURQUOI PAS BCRYPT
-------------------

1. bcrypt TRONQUE silencieusement au-delà de 72 octets. Une phrase de passe
   longue (que l'on cherche justement à encourager) voit sa fin ignorée sans le
   moindre avertissement — deux mots de passe différents deviennent équivalents.
2. bcrypt n'utilise que ~4 Kio de mémoire. Le coût d'un essai est donc quasi nul
   sur GPU/ASIC, où l'on parallélise par dizaines de milliers. Argon2id, lauréat
   de la Password Hashing Competition, est *mémoire-dur* par construction : les
   19 Mio par essai sont ce qui rend une attaque massivement parallèle chère.
3. Ce que la production Supabase utilisait — `$2a$10$` — c'est 2^10 = 1024 tours,
   en dessous des recommandations actuelles. Reprendre bcrypt reviendrait à
   changer d'hébergeur sans rien corriger.
4. Côté Python, bcrypt s'utilise en pratique via passlib, dont la version 1.7.4
   est cassée avec bcrypt >= 4 (AttributeError sur `bcrypt.__about__`). C'est une
   panne d'authentification déclenchée par une simple mise à jour de dépendance.
   `argon2-cffi` est autonome, sans passlib.

Module PUR : aucune I/O, aucune dépendance à la base ni à FastAPI. La
persistance et le verrouillage vivent dans services/credentials.py — c'est ce
qui rend ce fichier testable sans base (backend/tests/test_passwords.py).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional, Tuple

from argon2 import PasswordHasher, Type
from argon2.exceptions import Argon2Error, InvalidHashError

# ── Paramètres Argon2id ─────────────────────────────────────────────────────
#
# Profil « OWASP Password Storage Cheat Sheet » pour Argon2id :
# m = 19 Mio, t = 2, p = 1. Justification de chaque valeur :
#
#   MEMORY_COST_KIB = 19456 (19 Mio) — c'est le paramètre qui compte. Il fixe le
#     coût MATÉRIEL d'une attaque parallèle : 10 000 essais simultanés exigent
#     190 Gio de RAM, ce qu'aucune carte graphique ne fournit. Descendre ce
#     chiffre annulerait l'intérêt d'Argon2 par rapport à bcrypt.
#
#   TIME_COST = 2 — nombre de passes sur le bloc mémoire. Deux passes est le
#     minimum recommandé pour Argon2id à 19 Mio (une seule passe n'est admise
#     qu'avec 46 Mio et plus).
#
#   PARALLELISM = 1 — le backend tourne sur UN worker uvicorn (cf.
#     backend/uti-backend.service) et le VPS a peu de cœurs. Répartir un hachage
#     sur plusieurs fils ne ferait que voler du temps CPU aux requêtes en cours,
#     sans rendre l'attaquant plus lent.
#
#   HASH_LEN = 32 / SALT_LEN = 16 — valeurs par défaut d'Argon2, largement
#     au-delà du nécessaire (128 bits de sel, 256 bits de sortie).
#
# Coût mesuré : ~44 ms sur un x86_64 de classe CI (Python 3.11, argon2-cffi
# 23.1.0). Compter 60 à 150 ms sur un petit VPS OVH. C'est imperceptible à la
# connexion et volontairement pénible à répéter.
MEMORY_COST_KIB = 19456
TIME_COST = 2
PARALLELISM = 1
HASH_LEN = 32
SALT_LEN = 16

#: Longueur minimale acceptée. Alignée sur la règle déjà appliquée par les
#: routeurs avant cette migration : on ne durcit pas la règle en même temps
#: qu'on change d'algorithme, sinon on ne saurait pas ce qui a cassé.
MIN_LENGTH = 8

#: Garde-fou ABSENT avec bcrypt, nécessaire avec Argon2id : bcrypt tronquait à
#: 72 octets, donc la taille de l'entrée était bornée d'office. Argon2 hache
#: TOUT ce qu'on lui donne — un POST de 10 Mio dans le champ « mot de passe »
#: deviendrait un déni de service gratuit sur un endpoint public.
MAX_BYTES = 1024

#: Durée de vie d'un lien de réinitialisation. Alignée sur le texte de l'e-mail
#: (services/email_templates.py:83 — « à usage unique et expire dans 1 heure ») :
#: une promesse affichée à l'utilisateur doit être celle que le code applique.
RESET_TOKEN_TTL_MINUTES = 60

#: Entropie du jeton de réinitialisation, en octets. 32 octets = 256 bits, soit
#: 43 caractères une fois encodés en base64url par `secrets.token_urlsafe`.
RESET_TOKEN_BYTES = 32

_hasher = PasswordHasher(
    time_cost=TIME_COST,
    memory_cost=MEMORY_COST_KIB,
    parallelism=PARALLELISM,
    hash_len=HASH_LEN,
    salt_len=SALT_LEN,
    type=Type.ID,  # Argon2id : résiste à la fois aux GPU et aux attaques par canal auxiliaire
)


class PasswordRejected(ValueError):
    """Mot de passe refusé par la politique (longueur). Message destiné à l'utilisateur."""


def check_password(password: str) -> None:
    """Valide la forme du mot de passe. Lève PasswordRejected avec un message FR.

    Appelée AVANT `hash_password` par tous les points d'entrée, pour que la borne
    haute (MAX_BYTES) soit refusée en 422 plutôt qu'en 500 après coup.
    """
    if not isinstance(password, str) or len(password) < MIN_LENGTH:
        raise PasswordRejected(
            f"Le mot de passe doit contenir au moins {MIN_LENGTH} caractères."
        )
    if len(password.encode("utf-8")) > MAX_BYTES:
        raise PasswordRejected("Mot de passe trop long (maximum 1024 octets).")


def hash_password(password: str) -> str:
    """Hache un mot de passe. Retourne la chaîne PHC complète (`$argon2id$v=19$m=…`).

    La chaîne porte SES PROPRES paramètres et son sel : changer les constantes
    ci-dessus n'invalide aucun hachage existant, `needs_rehash` les rattrape à la
    connexion suivante. Bloquant ~44 ms : à appeler via `run_in_threadpool`
    depuis un endpoint `async def`, sinon la boucle d'événements est figée.
    """
    check_password(password)
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Vérifie un mot de passe. Ne lève JAMAIS — retourne True ou False.

    Toutes les erreurs argon2 (mauvais mot de passe, hachage corrompu, chaîne
    vide) sont ramenées à False : un hachage illisible en base ne doit pas
    produire une 500 qui, elle, distinguerait ce compte des autres.
    """
    if not stored_hash or not isinstance(password, str):
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (Argon2Error, InvalidHashError, TypeError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True si le hachage a été produit avec des paramètres plus faibles que les
    paramètres courants. Permet de relever le coût (MEMORY_COST_KIB, TIME_COST)
    plus tard sans imposer une réinitialisation à tout le monde : chacun est
    rehaché à sa connexion suivante, quand son mot de passe est en clair.

    Ce n'est PAS un double algorithme : on reste sur Argon2id de bout en bout.
    """
    if not stored_hash:
        return True
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (Argon2Error, InvalidHashError, TypeError, ValueError):
        return True


@lru_cache(maxsize=1)
def dummy_hash() -> str:
    """Hachage d'une valeur aléatoire jetable, vérifié quand l'e-mail est inconnu.

    Sans cela, `/auth/login` répond en 2 ms pour une adresse inexistante et en
    60 ms pour une adresse existante : l'écart suffit à énumérer les comptes,
    alors même que le message d'erreur, lui, est identique dans les deux cas.

    Calculé à la première utilisation (et non à l'import) pour ne pas ajouter
    50 ms au démarrage du service, et jamais écrit en dur : un hachage figé dans
    le code serait un candidat tout trouvé pour une table pré-calculée.
    """
    return _hasher.hash(secrets.token_urlsafe(32))


def placeholder_hash() -> str:
    """Hachage d'un secret aléatoire immédiatement oublié, destiné à être STOCKÉ.

    Sert aux comptes migrés depuis Supabase : `user_credentials.password_hash`
    est NOT NULL et la ligne doit exister avant l'envoi du lien — c'est elle que
    `by_email` retrouve, et c'est sur elle que le jeton est armé. Aucun mot de
    passe ne peut donc correspondre tant que la personne n'a pas cliqué.

    Ce qu'il ne faut SURTOUT pas mettre ici : une valeur fixe (« ! », une chaîne
    vide, un hachage constant). Elle serait identique sur tous les comptes en
    attente, ce qui les DÉSIGNERAIT à qui lit une copie de la base — et la
    première personne à retrouver le clair correspondant les ouvrirait tous d'un
    coup. Un secret de 256 bits par compte, jamais écrit nulle part, n'est
    devinable par personne, pas même par nous.

    Calcul identique à `dummy_hash`, intention opposée : celui-là est vérifié
    puis jeté, celui-ci est écrit en base. Deux fonctions pour que faire évoluer
    l'une ne change pas l'autre par accident.
    """
    return _hasher.hash(secrets.token_urlsafe(32))


# ── Jetons de réinitialisation ──────────────────────────────────────────────

def new_reset_token() -> Tuple[str, str]:
    """Retourne (jeton_en_clair, empreinte_à_stocker).

    Le clair ne part QUE dans l'e-mail. L'empreinte seule est écrite en base :
    une copie de `user_credentials` ne donne alors aucun lien exploitable, elle
    ne donne que des SHA-256 dont on ne peut pas remonter à un jeton de 256 bits.
    """
    clear = secrets.token_urlsafe(RESET_TOKEN_BYTES)
    return clear, hash_reset_token(clear)


def hash_reset_token(clear: str) -> str:
    """SHA-256 hexadécimal du jeton. Déterministe — c'est ce qui permet de
    RETROUVER la ligne à partir du jeton reçu, avec un simple index d'égalité.
    Un hachage salé (argon2, bcrypt) l'interdirait : il faudrait parcourir toutes
    les lignes pour en tester une."""
    return hashlib.sha256((clear or "").encode("utf-8")).hexdigest()


def reset_tokens_match(stored_hash: Optional[str], candidate_hash: str) -> bool:
    """Comparaison à temps constant de deux empreintes de jeton.

    Les deux valeurs sont publiques du point de vue de l'attaquant (il fournit
    l'une), mais comparer avec `==` fuite la longueur du préfixe commun, ce qui
    permettrait de reconstruire l'empreinte octet par octet si la comparaison
    était faite côté serveur sur un jeton deviné. Coût nul, on le fait.
    """
    if not stored_hash:
        return False
    return hmac.compare_digest(stored_hash, candidate_hash)


def reset_token_expiry(now: Optional[datetime] = None) -> datetime:
    """Échéance d'un jeton émis maintenant."""
    now = now or datetime.now(timezone.utc)
    return now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)


def reset_token_is_expired(expires_at, now: Optional[datetime] = None) -> bool:
    """True si l'échéance est absente, illisible ou dépassée.

    Tolère la forme rendue par PostgREST (chaîne ISO 8601 avec « +00:00 » ou
    « Z ») comme un datetime déjà construit. Une échéance illisible est traitée
    comme EXPIRÉE : en cas de doute sur un jeton de réinitialisation, on refuse.
    """
    if expires_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
    if expires_at.tzinfo is None:  # colonne lue sans fuseau : on suppose UTC
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now
