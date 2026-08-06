#!/usr/bin/env python3
"""
Signe la clé « service_role » que le backend présente à PostgREST.

CE QUE C'EST

Chez Supabase, `SUPABASE_SERVICE_KEY` est un JWT HS256 signé par Supabase et
contenant la revendication `role: service_role`. PostgREST lit cette
revendication et exécute la requête après un `SET LOCAL ROLE service_role`.
Rien là-dedans n'est propre à Supabase : dès lors que NOUS détenons le secret
donné à PostgREST (`jwt-secret`), NOUS pouvons signer la même clé.

supabase-py ne valide que la FORME de la clé — trois segments séparés par des
points (_sync/client.py:60-64) — jamais son émetteur. Le client reste donc
inchangé, et les 367 appels `.table()` du backend avec.

POURQUOI PAS DE DATE D'EXPIRATION PAR DÉFAUT

Un `exp` sur cette clé fait tomber TOUTE la plateforme à l'heure dite, sans
préavis et sans rapport avec un déploiement. Le jeton ne quitte jamais la
machine (PostgREST n'écoute que sur 127.0.0.1) et vit dans un `.env` en 0600 :
l'expiration achèterait très peu de sécurité pour un risque d'interruption
certain. `--expires-days` reste disponible si la politique change ; la rotation
se fait alors en changeant le secret PostgREST, ce qui invalide d'un coup toutes
les clés émises.

USAGE

    # clé de production (secret lu dans le fichier de PostgREST)
    sudo python3 backend/scripts/make_service_key.py --out /root/service_key.txt

    # affichage direct (pour un copier-coller dans .env)
    sudo python3 backend/scripts/make_service_key.py

Le secret ne se passe JAMAIS en argument : la ligne de commande d'un processus
est lisible par tout le monde dans `ps`.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path

# PostgREST refuse les secrets HS256 de moins de 32 octets : sa bibliothèque JOSE
# applique la RFC 7518 §3.2 (clé au moins aussi longue que l'empreinte). Le
# symptôme est un 401 « JWSInvalidSignature » qui n'évoque en rien la longueur du
# secret — d'où ce contrôle ici, où le message peut être explicite.
MIN_SECRET_BYTES = 32

# Forme acceptée par supabase-py (_sync/client.py:61-63). Vérifier ici évite de
# découvrir le problème au démarrage du backend, sous la forme d'un
# « Invalid API key » sans rapport apparent avec ce script.
SUPABASE_KEY_RE = re.compile(
    r"^[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*$"
)

DEFAULT_SECRET_FILE = "/etc/postgrest/jwt.secret"


def _b64url(raw: bytes) -> str:
    """Base64 URL-safe sans remplissage, comme l'exige la RFC 7515 §2."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_hs256(payload: dict, secret: str) -> str:
    """JWT HS256 en bibliothèque standard uniquement.

    Volontairement sans PyJWT ni python-jose : ce script tourne pendant
    l'installation du VPS, avant que le venv du backend n'existe. Une dépendance
    ici transformerait une étape d'installation en problème d'installation.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    parts = [
        _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
        _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
    ]
    signature = hmac.new(
        secret.encode("utf-8"), ".".join(parts).encode("ascii"), hashlib.sha256
    ).digest()
    parts.append(_b64url(signature))
    return ".".join(parts)


def read_secret(path: Path) -> str:
    """Lit le secret PostgREST en reproduisant EXACTEMENT son traitement.

    PostgREST, avec `jwt-secret = "@fichier"`, retire les blancs de fin. Un
    secret créé par `openssl rand -hex 32 > fichier` contient un saut de ligne :
    signer avec le contenu brut produit une clé que PostgREST rejette en 401,
    sans autre indice. Le `.strip()` n'est donc pas de la coquetterie.
    """
    if not path.exists():
        sys.exit(
            f"Secret introuvable : {path}\n"
            "Il est créé par backend/deploy/install_db.sh. Sur une machine de "
            "test : openssl rand -hex 32 | sudo tee /etc/postgrest/jwt.secret"
        )
    secret = path.read_text(encoding="utf-8").strip()
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        sys.exit(
            f"Secret trop court ({len(secret.encode())} octets) : PostgREST exige "
            f"au moins {MIN_SECRET_BYTES} octets pour HS256 et répondrait 401 à "
            "toutes les requêtes. Régénérez-le : openssl rand -hex 32"
        )
    return secret


def build_payload(role: str, expires_days: int | None) -> dict:
    payload: dict = {"role": role, "iat": int(time.time())}
    if expires_days:
        payload["exp"] = payload["iat"] + expires_days * 86400
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--secret-file", default=DEFAULT_SECRET_FILE, type=Path,
        help=f"fichier contenant le secret donné à PostgREST (défaut : {DEFAULT_SECRET_FILE})",
    )
    parser.add_argument(
        "--role", default="service_role",
        help="revendication `role` du jeton (défaut : service_role)",
    )
    parser.add_argument(
        "--expires-days", type=int, default=None,
        help="ajoute une expiration (défaut : aucune — voir l'en-tête du fichier)",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="écrit la clé dans ce fichier en 0600 au lieu de l'afficher",
    )
    args = parser.parse_args()

    token = sign_hs256(build_payload(args.role, args.expires_days),
                       read_secret(args.secret_file))

    if not SUPABASE_KEY_RE.match(token):  # pragma: no cover - défense en profondeur
        sys.exit("Clé produite refusée par la validation de forme de supabase-py.")

    if args.out:
        # Créé en 0600 AVANT la première écriture : un chmod après coup laisse une
        # fenêtre pendant laquelle la clé est lisible par tout le monde.
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(token + "\n")
        print(f"Clé écrite dans {args.out} (0600).")
    else:
        print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
