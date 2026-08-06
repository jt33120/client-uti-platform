"""
Identifiants de connexion : lecture, écriture, verrouillage après échecs.

Pourquoi une table SÉPARÉE de `profiles` (cf. migration 0018)
-------------------------------------------------------------
Six endpoints font `select("*")` sur `profiles` et renvoient la ligne au
navigateur — routers/auth.py:744 (`GET /auth/me`), routers/auth.py:1002
(`PATCH /auth/me`), et les lectures de `login` / `mfa_verify` / `mfa_enroll`.
Un `password_hash` posé dans `profiles` partirait donc dans le navigateur au
premier chargement de l'écran « Mon profil ». Le seul rempart serait un
`data.pop("password_hash")` à ne jamais oublier — exactement le mécanisme
fragile qui protège aujourd'hui `mfa_secret`, et qui suppose que chaque futur
endpoint y pense. Une table distincte rend la fuite IMPOSSIBLE par construction :
PostgREST ne joint que ce qu'on lui demande explicitement, et personne ne
demande `user_credentials`.

Séparation des responsabilités avec services/passwords.py
---------------------------------------------------------
`passwords` ne fait que du calcul (hacher, vérifier, tirer un jeton). Ce module
fait les entrées/sorties. Entre les deux, la POLITIQUE de verrouillage est
écrite ici en fonctions PURES (`lock_delay_minutes`, `failure_patch`,
`lock_seconds_remaining`) pour être testable sans base — c'est le cœur du
dispositif anti-force-brute, il ne peut pas rester non couvert.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from services.supabase_client import supabase
from services import passwords

TABLE = "user_credentials"

# ── Politique de verrouillage ───────────────────────────────────────────────
#
# Le `_throttle` de routers/auth.py vit en MÉMOIRE DU PROCESSUS : il disparaît à
# chaque `systemctl restart uti-backend` (donc à chaque déploiement, cf.
# backend/deploy.sh) et ne serait pas partagé si l'on passait un jour à
# plusieurs workers uvicorn. Il reste utile — c'est lui qui refuse la requête
# AVANT les 44 ms d'Argon2, donc lui qui protège le CPU — mais il ne peut pas
# être le seul frein. Le compteur ci-dessous, lui, est en base : il survit aux
# redémarrages et il est vu par tous les processus.
#
# Les deux se complètent et ne se remplacent pas.

#: Nombre d'échecs consécutifs tolérés avant le premier verrouillage. Cinq :
#: assez pour trois fautes de frappe et deux essais de mot de passe d'un autre
#: site, trop peu pour explorer un dictionnaire.
LOCK_AFTER = 5

#: Durée du verrou, en minutes, selon le rang de l'échec au-delà de LOCK_AFTER.
#: Croissante : un utilisateur qui se trompe cinq fois attend une minute, un
#: automate qui insiste attend une demi-heure entre chaque tentative.
LOCK_STEPS_MINUTES = (1, 5, 15, 30)

#: PLAFOND ASSUMÉ. Un verrou qui grandirait sans fin transformerait l'anti-force-
#: brute en arme : il suffirait de saisir dix mauvais mots de passe sur l'adresse
#: du dirigeant pour lui interdire la plateforme jusqu'à intervention. Au-delà de
#: 30 minutes par tentative, le débit résiduel (2 essais/heure) est déjà hors de
#: portée d'une attaque, et la gêne pour le titulaire reste bornée.
LOCK_MAX_MINUTES = LOCK_STEPS_MINUTES[-1]


def lock_delay_minutes(failed_attempts: int) -> Optional[int]:
    """Durée du verrou après `failed_attempts` échecs, ou None si pas de verrou."""
    if failed_attempts < LOCK_AFTER:
        return None
    index = min(failed_attempts - LOCK_AFTER, len(LOCK_STEPS_MINUTES) - 1)
    return LOCK_STEPS_MINUTES[index]


def failure_patch(row: Optional[dict], now: Optional[datetime] = None) -> dict:
    """Champs à écrire après un mot de passe refusé.

    Fonction pure : elle décide, elle n'écrit pas. Voir `record_failure`.
    """
    now = now or datetime.now(timezone.utc)
    attempts = int((row or {}).get("failed_attempts") or 0) + 1
    patch: dict = {"failed_attempts": attempts, "locked_until": None}
    minutes = lock_delay_minutes(attempts)
    if minutes is not None:
        patch["locked_until"] = (now + timedelta(minutes=minutes)).isoformat()
    return patch


def success_patch() -> dict:
    """Champs à écrire après une authentification réussie : le compteur repart à zéro.

    Remis à zéro dès que le MOT DE PASSE est reconnu, avant l'étape TOTP : ce
    compteur mesure les tentatives de devinette du mot de passe, pas les erreurs
    de saisie du code à six chiffres — celles-ci ont leur propre garde
    (`_throttle(f"mfa:verify:{user_id}", 5, 300)`, routers/auth.py:591).
    """
    return {"failed_attempts": 0, "locked_until": None}


def lock_seconds_remaining(row: Optional[dict], now: Optional[datetime] = None) -> int:
    """Secondes restantes avant la fin du verrou. 0 si le compte n'est pas verrouillé.

    Une échéance illisible est traitée comme NON verrouillée : mieux vaut laisser
    passer une tentative (le compteur d'échecs reste, lui, opérant) que barrer
    définitivement un compte à cause d'une valeur mal formée.
    """
    locked_until = (row or {}).get("locked_until")
    if not locked_until:
        return 0
    now = now or datetime.now(timezone.utc)
    if isinstance(locked_until, str):
        try:
            locked_until = datetime.fromisoformat(locked_until.replace("Z", "+00:00"))
        except ValueError:
            return 0
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    remaining = (locked_until - now).total_seconds()
    return int(remaining) + 1 if remaining > 0 else 0


# ── Accès à la base ─────────────────────────────────────────────────────────

def _first(rows) -> Optional[dict]:
    """Première ligne d'une réponse PostgREST, ou None.

    On n'utilise pas `.single()` : il lève une APIError PGRST116 sur zéro ligne,
    ce qui obligerait chaque appelant à envelopper sa lecture dans un try/except
    et rendrait un compte inexistant indiscernable d'une base injoignable.
    """
    data = getattr(rows, "data", None) or []
    return data[0] if data else None


def by_email(email: str) -> Optional[dict]:
    """Identifiants d'un compte par son adresse de connexion (insensible à la casse).

    L'adresse est stockée en minuscules (contrainte CHECK dans la migration
    0018) : on normalise ici pour que « Jean.Dupont@x.fr » et « jean.dupont@x.fr »
    ouvrent le même compte, comme le faisait GoTrue.
    """
    return _first(
        supabase.table(TABLE).select("*").eq("email", (email or "").strip().lower())
        .limit(1).execute()
    )


def by_user_id(user_id: str) -> Optional[dict]:
    """Identifiants d'un compte par son identifiant.

    Utilisé pour la RE-AUTHENTIFICATION (changer d'e-mail, changer de mot de
    passe, désactiver la 2FA). Volontairement PAS `by_email` : le jeton de
    session porte l'adresse figée à la connexion, or l'utilisateur peut avoir
    changé d'adresse depuis. Chercher par identifiant ne peut pas se désynchroniser.
    """
    return _first(
        supabase.table(TABLE).select("*").eq("user_id", user_id).limit(1).execute()
    )


def by_reset_token_hash(token_hash: str) -> Optional[dict]:
    """Identifiants portant cette empreinte de jeton de réinitialisation."""
    if not token_hash:
        return None
    return _first(
        supabase.table(TABLE).select("*").eq("reset_token_hash", token_hash)
        .limit(1).execute()
    )


def existing_user_ids() -> set:
    """Identifiants des comptes qui ont DÉJÀ une ligne d'identifiants.

    Sert à la migration des comptes existants (scripts/migrer_identifiants.py) :
    savoir qui reste à contacter. Volontairement ici plutôt qu'un
    `table("user_credentials")` dans le script — c'est l'unicité du point
    d'accès qui rend vérifiable la promesse de ce module (cf.
    tests/test_credentials_never_leak.py).

    Ne projette QUE `user_id` : ni hachage, ni empreinte de jeton, ni compteur
    d'échecs. Un appelant qui n'a besoin que de savoir « qui existe » ne doit
    pas se retrouver un secret entre les mains.
    """
    rows = supabase.table(TABLE).select("user_id").execute().data or []
    return {r["user_id"] for r in rows if r.get("user_id")}


def create(user_id: str, email: str, password_hash: str) -> dict:
    """Crée la ligne d'identifiants d'un compte neuf.

    Laisse remonter l'exception : l'appelant (routers/auth.py:register) doit
    pouvoir défaire l'insertion du profil si celle-ci échoue. Un doublon d'e-mail
    ressort en 23505, que `_credentials_error` traduit en 409.
    """
    now = datetime.now(timezone.utc).isoformat()
    res = supabase.table(TABLE).insert({
        "user_id": user_id,
        "email": (email or "").strip().lower(),
        "password_hash": password_hash,
        "password_changed_at": now,
    }).execute()
    return (res.data or [{}])[0]


def verify(row: Optional[dict], password: str) -> bool:
    """Vérifie un mot de passe contre une ligne d'identifiants (éventuellement absente).

    Quand `row` est None (adresse inconnue), on vérifie quand même contre un
    hachage jetable : sans cela, le temps de réponse trahirait l'existence du
    compte, alors que le message d'erreur, lui, est identique dans les deux cas.

    Bloquant ~44 ms : appeler via `run_in_threadpool` depuis un endpoint async.
    """
    if row is None:
        passwords.verify_password(passwords.dummy_hash(), password)
        return False
    return passwords.verify_password(row.get("password_hash") or "", password)


def record_failure(row: dict, now: Optional[datetime] = None) -> None:
    """Incrémente le compteur d'échecs et pose le verrou s'il y a lieu.

    Lecture-puis-écriture, non atomique : deux tentatives simultanées peuvent
    n'en compter qu'une. C'est assumé — le `_throttle` par e-mail borne déjà
    l'entrée à 8 tentatives / 5 min par processus, donc la concurrence réelle est
    d'au plus quelques requêtes. Ce compteur n'a pas à être exact ; il a à
    SURVIVRE au redémarrage, ce que le throttle mémoire ne fait pas.

    Best-effort : un échec d'écriture ne doit pas transformer un « mot de passe
    incorrect » (401) en erreur serveur (500), ce qui distinguerait ce compte.
    """
    try:
        supabase.table(TABLE).update(failure_patch(row, now)).eq(
            "user_id", row["user_id"]
        ).execute()
    except Exception as e:  # noqa: BLE001
        print(f"[AUTH] compteur d'échecs non mis à jour pour {row.get('user_id')}: {e}")


def record_success(user_id: str) -> None:
    """Remet le compteur d'échecs à zéro. Best-effort, pour la même raison."""
    try:
        supabase.table(TABLE).update(success_patch()).eq("user_id", user_id).execute()
    except Exception as e:  # noqa: BLE001
        print(f"[AUTH] remise à zéro du compteur impossible pour {user_id}: {e}")


def set_password(user_id: str, password_hash: str, now: Optional[datetime] = None) -> bool:
    """Pose un nouveau mot de passe et libère le compte.

    Un changement de mot de passe efface le verrou et le compteur : quelqu'un qui
    prouve qu'il connaît l'ancien mot de passe (ou qui contrôle la boîte mail,
    via `consume_reset`) n'a plus à purger la pénalité laissée par un tiers.
    """
    now = now or datetime.now(timezone.utc)
    res = supabase.table(TABLE).update({
        "password_hash": password_hash,
        "password_changed_at": now.isoformat(),
        "failed_attempts": 0,
        "locked_until": None,
        # Un mot de passe qui change périme les liens de réinitialisation en
        # cours : sinon un lien demandé puis abandonné resterait armé une heure.
        "reset_token_hash": None,
        "reset_token_expires_at": None,
    }).eq("user_id", user_id).execute()
    return bool(res.data)


def set_email(user_id: str, email: str) -> bool:
    """Change l'adresse de CONNEXION. Laisse remonter un doublon (23505 → 409)."""
    res = supabase.table(TABLE).update({
        "email": (email or "").strip().lower(),
    }).eq("user_id", user_id).execute()
    return bool(res.data)


def issue_reset(user_id: str, token_hash: str, expires_at: datetime) -> bool:
    """Arme un jeton de réinitialisation (empreinte + échéance).

    Écrase tout jeton précédent : demander deux liens n'en laisse qu'un valide,
    le dernier. Sinon chaque demande ajouterait une clé d'entrée supplémentaire
    au compte, pour une heure.
    """
    res = supabase.table(TABLE).update({
        "reset_token_hash": token_hash,
        "reset_token_expires_at": expires_at.isoformat(),
    }).eq("user_id", user_id).execute()
    return bool(res.data)


def consume_reset(token_hash: str, password_hash: str, now: Optional[datetime] = None) -> bool:
    """Consomme le jeton et pose le nouveau mot de passe. USAGE UNIQUE GARANTI.

    Tout tient dans le filtre de l'UPDATE : on ne met à jour QUE la ligne dont
    `reset_token_hash` vaut encore l'empreinte présentée, et le même ordre efface
    cette empreinte. PostgreSQL sérialise les écritures concurrentes sur une
    ligne ; le second appel ne trouve donc plus rien à mettre à jour et PostgREST
    renvoie une liste VIDE. C'est exactement le motif anti-double-envoi déjà
    validé sur la file d'e-mails (services/email_outbox.py), et il ne demande ni
    verrou applicatif ni transaction explicite.

    Retourne True si le jeton a bien été consommé par CET appel.
    """
    now = now or datetime.now(timezone.utc)
    res = supabase.table(TABLE).update({
        "password_hash": password_hash,
        "password_changed_at": now.isoformat(),
        "reset_token_hash": None,
        "reset_token_expires_at": None,
        # Réinitialiser par e-mail débloque le compte : c'est la voie de secours
        # prévue quand un tiers a fait verrouiller l'adresse par des essais ratés.
        "failed_attempts": 0,
        "locked_until": None,
    }).eq("reset_token_hash", token_hash).execute()
    return bool(res.data)


def delete(user_id: str) -> int:
    """Supprime explicitement les identifiants d'un compte.

    La clé étrangère `ON DELETE CASCADE` vers `profiles` s'en charge déjà quand
    le profil part. On garde cet appel explicite pour les suppressions RGPD
    (routers/gdpr.py), où l'on doit pouvoir DIRE combien de lignes ont été
    effacées, table par table — un « effacement » sans décompte ne prouve rien.
    """
    try:
        res = supabase.table(TABLE).delete().eq("user_id", user_id).execute()
        return len(res.data or [])
    except Exception:  # noqa: BLE001
        return 0
