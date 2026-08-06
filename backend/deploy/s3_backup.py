#!/usr/bin/env python3
"""
Dépôt hors-site des sauvegardes sur OVH Object Storage (S3).

POURQUOI UN FICHIER À PART, ET PAS services/storage.py
services/storage.py sert les CV et les avatars avec la clé S3 de l'application
(config.py:28-30, `s3_access_key`). Réutiliser cette clé ici serait le défaut
central que ce chantier doit fermer : si la clé qui écrit les sauvegardes est la
même que celle du .env applicatif, alors quiconque prend le VPS prend la
production ET les sauvegardes, et il ne reste rien. Ce module ne lit
VOLONTAIREMENT jamais `config.settings` — il n'a que ses propres variables
BACKUP_S3_*, qui désignent un autre utilisateur S3 et un autre conteneur.

POURQUOI boto3 ET PAS L'AWS CLI
boto3 est déjà une dépendance (services/storage.py:47). Ajouter awscli, c'est un
paquet de plus à maintenir sur le VPS pour la seule chose qui doit encore
fonctionner dans six mois. Les identifiants passent par l'environnement du
process, jamais par argv (visible dans `ps` par tout utilisateur du VPS).

SOUS-COMMANDES
    envoyer <fichier> <clé>   dépose l'objet, renvoie sa taille distante
    taille  <clé>             taille de l'objet distant (HEAD, quasi gratuit)
    verifier <fichier> <clé>  re-télécharge et compare octet par octet (SHA-256)
    lister  [préfixe]         clés triées, la plus récente en dernier
    recuperer <clé> <fichier> télécharge (pour la répétition hors-site)

Code de sortie ≠ 0 en cas d'échec, message sur stderr : les scripts bash
appelants n'ont qu'à tester `|| crie ...`.
"""
import hashlib
import os
import sys

ENDPOINT = os.environ.get("BACKUP_S3_ENDPOINT") or ""
REGION = os.environ.get("BACKUP_S3_REGION") or "gra"
BUCKET = os.environ.get("BACKUP_S3_BUCKET") or ""
CLE = os.environ.get("BACKUP_S3_ACCESS_KEY") or ""
SECRET = os.environ.get("BACKUP_S3_SECRET_KEY") or ""


def _client():
    if not (ENDPOINT and BUCKET and CLE and SECRET):
        sys.exit(
            "BACKUP_S3_* incomplet : le dépôt hors-site est INACTIF.\n"
            "Renseigner BACKUP_S3_ENDPOINT / _BUCKET / _ACCESS_KEY / _SECRET_KEY "
            "dans /etc/uti-backup.env (chmod 600). Voir deploy/setup_backup_offsite.sh."
        )
    import boto3  # importé tardivement : le message ci-dessus doit sortir même sans boto3

    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=CLE,
        aws_secret_access_key=SECRET,
    )


def _sha256(chemin: str) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        # Par blocs : le fichier est petit aujourd'hui (quelques Mo), mais un
        # read() entier deviendrait un pic mémoire le jour où il ne l'est plus.
        for bloc in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloc)
    return h.hexdigest()


def envoyer(chemin: str, cle: str) -> None:
    c = _client()
    # ContentType binaire explicite : sans lui, certains proxys « aident » en
    # ré-encodant, et une archive chiffrée ré-encodée est une archive perdue.
    with open(chemin, "rb") as f:
        c.put_object(Bucket=BUCKET, Key=cle, Body=f,
                     ContentType="application/octet-stream")
    print(c.head_object(Bucket=BUCKET, Key=cle)["ContentLength"])


def taille(cle: str) -> None:
    print(_client().head_object(Bucket=BUCKET, Key=cle)["ContentLength"])


def verifier(chemin: str, cle: str) -> None:
    """Re-télécharge l'objet et compare son SHA-256 à celui du fichier local.

    POURQUOI PAS L'ETag : sur un envoi en plusieurs parties, l'ETag n'est pas le
    MD5 du contenu mais un condensé de condensés — le comparer à un hachage
    local échoue sans que rien ne soit cassé, et on apprend à ignorer l'alerte.
    Re-télécharger coûte quelques mégaoctets ; c'est le prix pour que « déposé »
    veuille dire « relisible », seule propriété qui compte.
    """
    c = _client()
    local = _sha256(chemin)
    h = hashlib.sha256()
    corps = c.get_object(Bucket=BUCKET, Key=cle)["Body"]
    for bloc in iter(lambda: corps.read(1024 * 1024), b""):
        h.update(bloc)
    if h.hexdigest() != local:
        sys.exit(f"l'objet distant {cle} DIFFÈRE du fichier local "
                 f"(distant {h.hexdigest()[:16]}… ≠ local {local[:16]}…)")
    print(local)


def lister(prefixe: str = "") -> None:
    c = _client()
    cles = []
    jeton = None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": prefixe}
        if jeton:
            kw["ContinuationToken"] = jeton
        rep = c.list_objects_v2(**kw)
        cles += [o["Key"] for o in rep.get("Contents", [])]
        if not rep.get("IsTruncated"):
            break
        jeton = rep.get("NextContinuationToken")
    # Le nom des objets est horodaté en ISO-8601 UTC : l'ordre lexicographique
    # EST l'ordre chronologique. C'est pour ça que le nom est construit ainsi
    # dans backup_db.sh et pas en « 06-08-2026 ».
    for k in sorted(cles):
        print(k)


def recuperer(cle: str, chemin: str) -> None:
    _client().download_file(BUCKET, cle, chemin)
    print(chemin)


ACTIONS = {"envoyer": 2, "taille": 1, "verifier": 2, "lister": 0, "recuperer": 2}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ACTIONS:
        sys.exit(f"usage : {sys.argv[0]} {{{'|'.join(ACTIONS)}}} [args]")
    action, args = sys.argv[1], sys.argv[2:]
    if action == "lister":
        lister(args[0] if args else "")
    else:
        if len(args) != ACTIONS[action]:
            sys.exit(f"{action} attend {ACTIONS[action]} argument(s)")
        globals()[action](*args)
