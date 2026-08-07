#!/usr/bin/env python3
"""
Migration des comptes existants vers l'authentification maison.

LE PROBLÈME QUE CE SCRIPT RÉSOUT

La migration 0019 ne reprend AUCUN hachage de Supabase (décision assumée : les
onze comptes portaient des bcrypt `$2a$10$`, en dessous des recommandations
actuelles, et un repli à deux algorithmes en base est une dette permanente pour
un gain d'un seul jour). Les profils survivent donc — avec leurs AO, leurs
matchings, leurs décisions — mais plus personne ne peut se connecter.

Sans ce script, la reprise se ferait compte par compte : supprimer, réinviter,
réexpliquer. Ici chaque utilisateur reçoit UN e-mail avec UN lien, choisit son
mot de passe, et retrouve son compte tel qu'il l'a laissé.

COMMENT

Pour chaque profil sans identifiants :
  1. une ligne `user_credentials` est créée avec un hachage INCONNU DE TOUS
     (`credentials.provision_for_migration`) — la colonne est NOT NULL, et le
     compte doit exister pour que le circuit de réinitialisation le retrouve par
     son adresse ;
  2. un jeton opaque de 256 bits est armé, dont seule l'empreinte SHA-256 est
     écrite en base ;
  3. l'e-mail « password_migration » est déposé dans la file d'envoi.

Le circuit est celui, déjà éprouvé, de « mot de passe oublié » : même page
`/reset-password`, même usage unique garanti par l'UPDATE filtré
(`credentials.consume_reset`), même politique de mot de passe. Rien de neuf
n'est introduit sur le chemin d'un secret.

LA DOUBLE AUTHENTIFICATION N'EST PAS AFFECTÉE. `mfa_secret` vit dans `profiles`,
que la migration ne touche pas : qui était enrôlé le reste, et son application
d'authentification continue de fonctionner. Qui ne l'était pas s'enrôlera à sa
première connexion, comme aujourd'hui.

USAGE

    cd ~/app/backend && source venv/bin/activate

    # 1. Voir qui serait contacté — n'écrit rien, n'envoie rien
    python scripts/migrer_identifiants.py

    # 2. Un seul destinataire, pour vérifier le rendu de l'e-mail
    python scripts/migrer_identifiants.py --email julian.talou33@gmail.com --envoyer

    # 3. Tout le monde
    python scripts/migrer_identifiants.py --envoyer

LA SIMULATION EST LE DÉFAUT, et c'est délibéré : ce script écrit à de vraies
personnes. Un envoi de masse déclenché par une commande tapée trop vite ne se
rattrape pas — on ne rappelle pas un e-mail.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                          # noqa: E402
from services.supabase_client import supabase        # noqa: E402
from services import credentials, email_outbox, email_templates, passwords  # noqa: E402

#: Validité du lien, en jours. Sans rapport avec l'heure d'un « mot de passe
#: oublié » : celui-là est demandé à l'instant, celui-ci arrive sans prévenir
#: dans une boîte qu'on relèvera peut-être lundi. Trop court, et l'opération se
#: transforme en support téléphonique ; trop long, et un lien d'accès traîne
#: dans les boîtes mail. Sept jours couvre une semaine de congés.
JOURS_PAR_DEFAUT = 7

#: Comptes ignorés par défaut. Un compte suspendu ou désactivé ne doit pas
#: recevoir d'invitation à revenir : la suspension est une décision
#: d'administration, et la migration n'est pas le moment de la défaire.
STATUTS_EXCLUS = ("suspended", "disabled")


def _validite_humaine(jours: int) -> str:
    return "24 heures" if jours == 1 else f"{jours} jours"


def _profils(email_cible: str | None, inclure_suspendus: bool) -> list[dict]:
    req = supabase.table("profiles").select("id, email, name, role, status")
    if email_cible:
        # `ilike` sans joker : égalité insensible à la casse. `profiles.email`
        # conserve la casse d'origine, une comparaison exacte raterait
        # « Julian.Talou@… ».
        req = req.ilike("email", email_cible)
    lignes = req.execute().data or []
    if inclure_suspendus:
        return lignes
    return [p for p in lignes if (p.get("status") or "active") not in STATUTS_EXCLUS]


def _deja_pourvus() -> set:
    """Comptes déjà munis d'identifiants. Passe par `services/credentials` : ce
    module est le SEUL à interroger `user_credentials`, et c'est cette unicité
    qui rend vérifiable la promesse « aucun hachage ne fuite »."""
    return credentials.existing_user_ids()


def _envoyer(profil: dict, jours: int) -> tuple[bool, str]:
    """Arme un jeton et dépose l'e-mail. Retourne (succès, message)."""
    user_id = profil["id"]
    email = (profil.get("email") or "").strip()
    if not email:
        return False, "profil sans adresse"

    clear, empreinte = passwords.new_reset_token()
    echeance = datetime.now(timezone.utc) + timedelta(days=jours)
    if not credentials.issue_reset(user_id, empreinte, echeance):
        return False, "jeton non armé (0 ligne mise à jour)"

    # Le clair ne va QUE dans l'e-mail : ni journal, ni base, ni valeur de
    # retour. Le journaliser reviendrait à écrire un mot de passe temporaire
    # dans journalctl, lisible par tout le groupe `adm`.
    lien = f"{settings.frontend_url}/reset-password?token={clear}"
    contexte = {
        "name": (profil.get("name") or "").split(" ")[0] or "bonjour",
        "link": lien,
        "validite": _validite_humaine(jours),
        # Permet au destinataire de vérifier sans cliquer : ouvrir lui-même la
        # plateforme et y demander « mot de passe oublié » aboutit au même lien.
        "plateforme": settings.frontend_url.rstrip("/"),
    }
    sujet, html, texte = email_templates.build_email("password_migration", contexte)
    ligne = email_outbox.enqueue(
        to_email=email,
        to_name=profil.get("name"),
        subject=sujet, html=html, text=texte,
        category="password_migration",
        template_key="password_migration",
        recipient_id=user_id,
        # L'empreinte du jeton rend la clé unique à chaque exécution : relancer
        # le script envoie bien un NOUVEAU lien, tandis qu'un double dépôt au
        # sein d'une même exécution reste impossible.
        idempotency_key=f"migration:{empreinte[:32]}",
    )
    if ligne is None:
        return False, "dépôt en file impossible"
    return True, "e-mail déposé en file"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--email", default=None,
                   help="restreindre à cette adresse (essai avant l'envoi général)")
    p.add_argument("--envoyer", action="store_true",
                   help="ÉCRIRE et ENVOYER pour de bon. Sans ce drapeau, le script simule.")
    p.add_argument("--jours", type=int, default=JOURS_PAR_DEFAUT,
                   help=f"validité du lien en jours (défaut : {JOURS_PAR_DEFAUT})")
    p.add_argument("--relancer", action="store_true",
                   help="inclure aussi les comptes qui ont DÉJÀ des identifiants "
                        "(lien expiré, e-mail perdu). Leur mot de passe actuel "
                        "reste valable tant qu'ils n'utilisent pas le lien.")
    p.add_argument("--inclure-suspendus", action="store_true", dest="inclure_suspendus",
                   help="contacter aussi les comptes suspendus ou désactivés")
    args = p.parse_args()

    if args.jours < 1 or args.jours > 30:
        print("❌ --jours doit être compris entre 1 et 30.", file=sys.stderr)
        return 1

    try:
        profils = _profils(args.email, args.inclure_suspendus)
        pourvus = _deja_pourvus()
    except Exception as e:  # noqa: BLE001
        print(f"❌ Base injoignable : {e}", file=sys.stderr)
        print("   Vérifiez SUPABASE_URL / SUPABASE_SERVICE_KEY dans backend/.env,",
              file=sys.stderr)
        print("   et que la migration 0019 a été appliquée à CETTE base.", file=sys.stderr)
        return 1

    if not profils:
        cible = f" pour {args.email}" if args.email else ""
        print(f"Aucun profil à traiter{cible}.")
        return 1 if args.email else 0

    a_traiter = [p for p in profils if args.relancer or p["id"] not in pourvus]
    ignores = [p for p in profils if p not in a_traiter]

    print(f"Base : {settings.supabase_url}")
    print(f"Lien valable {_validite_humaine(args.jours)} — envoi depuis {settings.smtp_from or '(SMTP_FROM absent)'}")
    print()
    for profil in ignores:
        print(f"  ⏭  {profil['email']:<45} déjà pourvu d'identifiants (--relancer pour forcer)")
    if not a_traiter:
        print("\nRien à faire.")
        return 0

    if not args.envoyer:
        print("SIMULATION — rien n'est écrit, rien n'est envoyé.\n")
        for profil in a_traiter:
            etat = "création des identifiants" if profil["id"] not in pourvus else "relance"
            print(f"  ✉️  {profil['email']:<45} {profil['role']:<9} {etat}")
        print(f"\n{len(a_traiter)} e-mail(s) seraient envoyés. Ajoutez --envoyer pour le faire.")
        return 0

    envoyes, echecs = 0, 0
    for profil in a_traiter:
        if profil["id"] not in pourvus:
            try:
                # Même geste que le rattrapage de /auth/forgot-password : une
                # seule définition de « ce qu'est un compte migré en attente ».
                credentials.provision_for_migration(
                    profil["id"], (profil.get("email") or "").strip().lower()
                )
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {profil['email']:<45} identifiants non créés : {e}")
                echecs += 1
                continue

        ok, message = _envoyer(profil, args.jours)
        if ok:
            print(f"  ✅ {profil['email']:<45} {message}")
            envoyes += 1
        else:
            # La ligne d'identifiants reste en place : le compte est retrouvable
            # par « Mot de passe oublié », qui refera exactement le même geste.
            # La détruire ici rendrait la reprise plus fragile, pas plus sûre.
            print(f"  ❌ {profil['email']:<45} {message}")
            echecs += 1

    print()
    print(f"{envoyes} e-mail(s) déposés en file, {echecs} échec(s).")
    if envoyes:
        print("La file est vidée par le backend toutes les 20 s. Suivi :")
        print("   sudo journalctl -u uti-backend -n 50 | grep -i outbox")
        print("   … ou l'onglet Supervision → E-mails.")
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
