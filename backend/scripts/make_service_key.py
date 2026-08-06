#!/usr/bin/env python3
"""
Fabrique la clé d'API que le backend présente à PostgREST.

POURQUOI CE SCRIPT EXISTE

Le backend parle à la base via `supabase-py`, qui n'est rien d'autre qu'un
client PostgREST. Il attend une « clé d'API » et la présente en
`Authorization: Bearer <clé>`. Chez Supabase, cette clé est un JWT signé par
Supabase. Sur notre PostgREST, c'est un JWT que NOUS signons, avec le secret
donné à PostgREST par `PGRST_JWT_SECRET`.

Autrement dit : la clé n'a rien de magique, c'est un jeton dont PostgREST
vérifie la signature et dont il lit le champ `role` pour choisir le rôle
PostgreSQL sous lequel exécuter la requête.

Deux contraintes, toutes deux vérifiées expérimentalement :

  * `supabase-py` valide la FORME de la clé à la construction du client —
    une expression régulière exigeant trois segments séparés par des points.
    Une chaîne quelconque lève « Invalid API key » avant tout appel réseau.
    Un vrai JWT satisfait évidemment cette forme.

  * Le secret doit faire au moins 32 octets : PostgREST refuse HS256 en
    dessous.

DURÉE DE VIE

Par défaut, la clé n'expire pas. C'est délibéré et c'est le comportement de la
clé service_role de Supabase : une expiration ferait tomber toute la plateforme
d'un coup, un jour, sans prévenir — et personne ne se souviendrait pourquoi.
La rotation se fait en changeant le SECRET (donc en invalidant toutes les clés
d'un coup), pas en attendant qu'une date passe. Utiliser --expire-jours pour
une clé temporaire (test, prestataire).

USAGE

    # Générer d'abord un secret, une seule fois, et le garder :
    openssl rand -hex 32

    # Puis la clé de service, à mettre dans SUPABASE_SERVICE_KEY :
    python scripts/make_service_key.py --secret "<le secret>"

    # Une clé anon (utile pour vérifier que la RLS bloque bien) :
    python scripts/make_service_key.py --secret "<le secret>" --role anon

Le secret ne doit apparaître NI dans le dépôt, NI dans l'historique du shell
(préfixer la commande d'un espace si l'historique le permet), NI dans les
journaux. Il vit dans le .env du backend et dans la configuration de PostgREST.
"""
import argparse
import datetime
import sys

ROLES = ("service_role", "authenticated", "anon")
SECRET_MIN = 32


def build(secret: str, role: str, expire_jours: int | None) -> str:
    try:
        from jose import jwt
    except ImportError:
        sys.exit("python-jose est requis : pip install 'python-jose[cryptography]'")

    charge: dict = {"role": role}
    if expire_jours:
        fin = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=expire_jours)
        charge["exp"] = int(fin.timestamp())
    return jwt.encode(charge, secret, algorithm="HS256")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--secret", required=True,
                   help="Le même secret que PGRST_JWT_SECRET (>= 32 caractères)")
    p.add_argument("--role", default="service_role", choices=ROLES,
                   help="Rôle PostgreSQL sous lequel PostgREST exécutera les requêtes")
    p.add_argument("--expire-jours", type=int, default=None,
                   help="Expiration en jours. Par défaut : aucune (voir le commentaire du fichier)")
    args = p.parse_args()

    if len(args.secret) < SECRET_MIN:
        # Ce n'est pas une coquetterie : PostgREST rejette purement et simplement
        # les secrets HS256 trop courts, et le message d'erreur qu'il produit
        # alors ne dit pas que c'est la longueur qui est en cause.
        p.error(f"Le secret doit faire au moins {SECRET_MIN} caractères "
                f"(il en fait {len(args.secret)}). Générez-le avec : openssl rand -hex 32")

    cle = build(args.secret, args.role, args.expire_jours)
    print(cle)
    if args.role == "service_role":
        print("\n# À placer dans backend/.env :", file=sys.stderr)
        print(f"SUPABASE_SERVICE_KEY={cle}", file=sys.stderr)
        print("# (le nom de la variable ne change pas : le backend continue "
              "d'utiliser le client supabase-py, seule sa destination change)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
