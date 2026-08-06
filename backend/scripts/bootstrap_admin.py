#!/usr/bin/env python3
"""
Crée le PREMIER administrateur sur une base vierge.

POURQUOI CE SCRIPT EXISTE — LE PROBLÈME DE L'ŒUF ET DE LA POULE

Sur une base neuve, aucun compte ne peut être créé par l'interface :

  * `POST /auth/register` exige un `invite_token` (routers/auth.py) — sans
    invitation, c'est 403 ;
  * `POST /invitations` exige `require_admin` (routers/invitations.py:78) —
    seul un administrateur peut inviter ;
  * `RegisterPage.jsx` n'affiche même aucun formulaire sans `?invite=…`.

La boucle est fermée : il faut un admin pour créer un admin. Tant que GoTrue
existait, on pouvait la casser depuis le tableau de bord Supabase (« Add user »).
Ce tableau de bord disparaît avec le projet Supabase — d'où ce script, qui est
désormais le SEUL point d'entrée d'une base vierge.

CE QU'IL FAIT

Une ligne dans `profiles`, une ligne dans `user_credentials`. Rien d'autre :
pas d'invitation à consommer, pas d'e-mail à envoyer. Le compte créé est
soumis aux mêmes règles que les autres — en particulier `mfa_required` vaut
`true` par défaut, donc la première connexion IMPOSE l'enrôlement TOTP.

USAGE (sur le VPS)

    cd ~/app/backend
    source venv/bin/activate
    python scripts/bootstrap_admin.py --email julian@grp-it.com --name "Julian Talou"

Le mot de passe est demandé de façon MASQUÉE (getpass), jamais passé en
argument : un mot de passe sur la ligne de commande finit dans ~/.bash_history
et reste lisible par tout le monde dans `ps aux` le temps de l'exécution.

Ensuite : se connecter, enrôler la 2FA, puis inviter les autres comptes par
l'interface. Ce script n'a plus à resservir.
"""
import argparse
import getpass
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Le script vit dans backend/scripts/ ; les modules sont dans backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.supabase_client import supabase  # noqa: E402
from services import credentials, passwords    # noqa: E402

#: Plus exigeant que les 8 caractères imposés aux comptes ordinaires
#: (services/passwords.MIN_LENGTH) : ce compte-ci peut créer d'autres
#: administrateurs, effacer des données au titre du RGPD et lire tous les CV.
#: Il n'y a aucune raison de lui appliquer le plancher commun.
MIN_ADMIN_LENGTH = 12

VALID_ROLES = ("admin", "commerce", "ao")


def _erreur(message: str) -> int:
    print(f"❌ {message}", file=sys.stderr)
    return 1


def _demander_mot_de_passe() -> str:
    """Saisie masquée, avec confirmation. Boucle jusqu'à obtenir un couple valide."""
    while True:
        premier = getpass.getpass("Mot de passe (masqué) : ")
        if len(premier) < MIN_ADMIN_LENGTH:
            print(f"   Trop court : {MIN_ADMIN_LENGTH} caractères minimum pour un administrateur.")
            continue
        try:
            passwords.check_password(premier)
        except passwords.PasswordRejected as e:
            print(f"   {e}")
            continue
        if premier != getpass.getpass("Confirmation             : "):
            print("   Les deux saisies diffèrent.")
            continue
        return premier


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--email", required=True, help="adresse de connexion")
    p.add_argument("--name", required=True, help="nom affiché")
    p.add_argument("--role", default="admin", choices=VALID_ROLES,
                   help="rôle du compte (défaut : admin)")
    p.add_argument("--org", default=None, choices=("uti", "groupement-it"),
                   help="entité commerciale (n'a de sens que pour un rôle 'commerce')")
    p.add_argument(
        "--force", action="store_true",
        help="créer ce compte même s'il existe déjà des administrateurs "
             "(par défaut le script refuse : il est fait pour amorcer, pas pour "
             "contourner l'écran d'administration)",
    )
    args = p.parse_args()

    email = args.email.strip().lower()
    name = args.name.strip()
    if "@" not in email or len(name) < 2:
        return _erreur("Adresse ou nom invalide.")

    # ── Garde-fou 1 : la base répond-elle ? ─────────────────────────
    # Sans ce contrôle, une erreur de configuration (.env pointant encore sur
    # Supabase, PostgREST arrêté) se manifesterait par une trace illisible au
    # milieu de la création.
    try:
        admins = supabase.table("profiles").select("id, email").eq(
            "role", "admin"
        ).limit(5).execute().data or []
    except Exception as e:  # noqa: BLE001
        return _erreur(
            f"Base injoignable : {e}\n"
            "   Vérifiez SUPABASE_URL / SUPABASE_SERVICE_KEY dans backend/.env "
            "et que PostgREST tourne (systemctl status postgrest)."
        )

    # ── Garde-fou 2 : ne pas doubler un amorçage déjà fait ──────────
    if admins and not args.force:
        liste = ", ".join(a.get("email", "?") for a in admins)
        return _erreur(
            f"Il existe déjà {len(admins)} administrateur(s) : {liste}\n"
            "   Créez les comptes suivants par invitation depuis l'écran "
            "« Comptes » de l'interface. Utilisez --force uniquement pour une "
            "reprise après incident (perte de tous les accès administrateur)."
        )

    # ── Garde-fou 3 : l'adresse est-elle libre ? ────────────────────
    # La contrainte UNIQUE le dirait aussi, mais un message clair vaut mieux
    # qu'une erreur 23505 remontée brute.
    try:
        if credentials.by_email(email):
            return _erreur(f"Un compte utilise déjà l'adresse {email}.")
    except Exception as e:  # noqa: BLE001
        return _erreur(
            f"Table user_credentials illisible : {e}\n"
            "   Avez-vous appliqué backend/migrations/0019_auth_maison.sql ?"
        )

    print(f"Création du compte {args.role} « {name} » <{email}>")
    mot_de_passe = _demander_mot_de_passe()

    print("Hachage argon2id…", end=" ", flush=True)
    password_hash = passwords.hash_password(mot_de_passe)
    print("fait.")

    # L'UUID est tiré ici, exactement comme dans routers/auth.register : la
    # ligne d'identifiants a besoin de l'identifiant du profil, donc il faut le
    # connaître AVANT l'insertion. (La migration 0018 pose aussi un DEFAULT
    # gen_random_uuid() sur profiles.id, qui sert de filet aux autres chemins.)
    user_id = str(uuid.uuid4())
    org = args.org if (args.role == "commerce" and args.org == "groupement-it") else None

    profil = {
        "id": user_id,
        "email": args.email.strip(),  # casse d'origine : c'est de l'affichage
        "name": name,
        "role": args.role,
        "org": org,
    }
    try:
        try:
            supabase.table("profiles").insert(profil).execute()
        except Exception:
            profil.pop("org", None)  # colonne 'org' non migrée
            supabase.table("profiles").insert(profil).execute()
    except Exception as e:  # noqa: BLE001
        return _erreur(f"Insertion du profil impossible : {e}")

    try:
        credentials.create(user_id, email, password_hash)
    except Exception as e:  # noqa: BLE001
        # Un profil sans identifiants est inutilisable ET bloque toute nouvelle
        # tentative (email UNIQUE). Même retour arrière que dans register().
        try:
            supabase.table("profiles").delete().eq("id", user_id).execute()
        except Exception:  # noqa: BLE001
            pass
        return _erreur(f"Insertion des identifiants impossible : {e}")

    print()
    print(f"✅ Compte créé — {email} (rôle {args.role}, id {user_id})")
    print(f"   Mot de passe posé le {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print()
    print("Prochaine étape : se connecter sur "
          f"{os.environ.get('FRONTEND_URL', 'https://plateforme.groupement-it.com')}/login")
    print("   La double authentification est OBLIGATOIRE par défaut : la première")
    print("   connexion affiche un QR code à scanner. Gardez le téléphone à portée —")
    print("   sans second facteur, le compte n'ouvre aucune session.")
    print("   En cas de perte du téléphone, un autre administrateur réinitialise la")
    print("   2FA via POST /auth/mfa/reset/{user_id}. S'il n'y a qu'un seul admin,")
    print("   il faut repasser par ce script (--force) après avoir supprimé la ligne.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
