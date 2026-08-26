#!/usr/bin/env python3
"""
Script de test SMTP — fournisseur au choix.

Le serveur est lu dans SMTP_HOST : aucun fournisseur n'est codé en dur, ici pas
plus qu'ailleurs. En production depuis le 26/08/2026 : smtp.resend.com:587.

CE QUE CE SCRIPT PROUVE, ET CE QU'IL NE PROUVE PAS

Il prouve que le relais ACCEPTE le message : connexion, STARTTLS,
authentification, adresse d'expéditeur autorisée. C'est déjà beaucoup — un
domaine non vérifié échoue ici.

Il ne prouve PAS que le message ARRIVE. « ✅ envoyé » signifie « accepté par le
relais », rien de plus. Un domaine sans DKIM peut être accepté à l'émission et
jeté en silence à l'arrivée : c'est arrivé sur ce projet. Pour savoir, il faut
regarder la boîte du destinataire ET le tableau de bord du fournisseur.

Équivalent Python du script PowerShell de test. Toute la configuration est
lue depuis les variables d'environnement (ou le fichier backend/.env) — aucun
secret n'est codé en dur.

Variables utilisées :
    SMTP_HOST       serveur SMTP (requis — aucun défaut, délibérément)
    SMTP_PORT       (défaut: 587)
    SMTP_USER       compte SMTP (requis)
    SMTP_PASSWORD   mot de passe SMTP (requis)
    SMTP_FROM       expéditeur (défaut: SMTP_USER)
    SMTP_FROM_NAME  nom affiché (défaut: "Test SMTP UTI")
    SMTP_TEST_TO    destinataire du test (requis)

Usage :
    cd backend && python scripts/test_smtp.py
    # ou en surchargeant le destinataire :
    SMTP_TEST_TO=qqn@example.com python scripts/test_smtp.py
"""
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))
except ImportError:
    pass


def main() -> int:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    mail_from = os.getenv("SMTP_FROM") or user
    from_name = os.getenv("SMTP_FROM_NAME", "Test SMTP UTI")
    mail_to = os.getenv("SMTP_TEST_TO")

    missing = [
        name
        for name, value in (
            # SMTP_HOST n'a plus de défaut (config.py) : sans lui, la connexion
            # partirait vers une chaîne vide et échouerait sur un message
            # incompréhensible. Mieux vaut nommer la variable qui manque.
            ("SMTP_HOST", host),
            ("SMTP_USER", user),
            ("SMTP_PASSWORD", password),
            ("SMTP_TEST_TO", mail_to),
        )
        if not value
    ]
    if missing:
        print(f"❌ Variables manquantes : {', '.join(missing)}")
        return 2

    now = datetime.now()
    subject = f"✅ Test SMTP — {now:%d/%m/%Y %H:%M:%S}"
    body = (
        "Bonjour,\n\n"
        "Ce message confirme que le relais SMTP a ACCEPTÉ le message.\n"
        "Il ne dit rien de sa livraison : vérifiez la boîte du destinataire.\n\n"
        "Détails de la connexion :\n"
        f"  - Serveur    : {host}\n"
        f"  - Port       : {port} (STARTTLS)\n"
        f"  - Expéditeur : {mail_from}\n"
        f"  - Date       : {now:%d/%m/%Y à %H:%M:%S}\n\n"
        "Cordialement,\n"
        f"{from_name}\n"
    )

    print("=" * 40)
    print("   Test SMTP - Python")
    print("=" * 40)
    print(f"  Serveur     : {host}")
    print(f"  Port        : {port} (STARTTLS)")
    print(f"  Expéditeur  : {mail_from}")
    print(f"  Destinataire: {mail_to}")
    print("  Connexion en cours...")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, mail_from))
    msg["To"] = mail_to
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(msg)
        print(f"\n  ✅ Email envoyé avec succès ! → Vérifiez la boîte de {mail_to}")
        return 0
    except smtplib.SMTPException as e:
        print(f"\n  ❌ Erreur SMTP : {e}")
        print("  Vérifiez :")
        print("    - Le mot de passe SMTP")
        print("    - Que l'adresse d'expéditeur est autorisée par le fournisseur")
        print("    - Que le domaine d'envoi est vérifié chez le fournisseur")
        print("    - Que le port 587 n'est pas bloqué par votre pare-feu")
        return 1
    except Exception as e:
        print(f"\n  ❌ Erreur inattendue : {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
