#!/usr/bin/env python3
"""
Migration des fichiers existants depuis Supabase Storage vers leur destination
définitive : le DISQUE DU VPS (``--vers local``, le choix retenu) ou OVH Object
Storage (``--vers s3``, la piste abandonnée faute d'accès au compte OVH).

Copie tous les objets des CINQ buckets logiques, en conservant l'arborescence,
puis — avec ``--rewrite-db`` — met à jour les valeurs stockées en base pour
qu'elles pointent vers la nouvelle destination.

⚠️ ORDRE : ce script réécrit la base ENCORE SUPABASE. Il doit donc tourner
AVANT la bascule de ``SUPABASE_URL`` vers le VPS. Lancé après, il réécrirait la
base neuve — vide — et les CV existants deviendraient introuvables.

⚠️ POURQUOI UN MODE DE PLUS DANS CE FICHIER, ET PAS UN SCRIPT FRÈRE
Un second script devrait recopier trois choses : la liste BUCKETS, la mise en
garde d'ordre ci-dessus, et l'import de PUBLIC_BUCKETS. Or c'est précisément une
règle recopiée qui avait laissé les CV en lecture publique
(tests/test_storage_acl.py:4-11) : deux définitions cohérentes chacune avec
elle-même, et divergentes. Les garde-fous de test_storage_acl.py lisent CE
fichier ; un frère naîtrait sans eux.

⚠️  À LIRE AVANT DE LANCER :
  - Lance d'abord en simulation : `python scripts/migrate_storage_to_ovh.py --dry-run`
  - Mode local : le script a besoin des variables Supabase, de LOCAL_STORAGE_DIR
    et de PUBLIC_BASE_URL (voir .env.example). Il n'a PAS besoin que
    STORAGE_BACKEND soit déjà passé à « local » : il écrit sur le disque
    directement, ce qui permet de migrer AVANT de basculer l'application.
  - Ne supprime RIEN sur Supabase : la copie est non destructive, tu pourras
    garder Supabase comme filet de sécurité le temps de vérifier.

Usage :
  cd backend
  python scripts/migrate_storage_to_ovh.py --dry-run                    # simulation (local)
  python scripts/migrate_storage_to_ovh.py --vers local                 # copie sur le disque
  python scripts/migrate_storage_to_ovh.py --vers local --rewrite-db    # copie + MAJ des URLs
  python scripts/migrate_storage_to_ovh.py --vers s3 --rewrite-db       # variante OVH
"""
import argparse
import os
import re
import sys
from typing import Optional
from urllib.parse import quote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))
except ImportError:
    pass

from config import settings  # noqa: E402
from services.supabase_client import supabase  # noqa: E402

from services import storage  # noqa: E402
from services.storage import PUBLIC_BUCKETS  # noqa: E402

#: Les CINQ buckets logiques du code, et pas trois. Deux sont créés à la demande
#: et se voient donc mal :
#:   * « compliance »   — routers/partners.py:273, attestations de vigilance
#:                        URSSAF et KBIS des partenaires ;
#:   * « email-assets » — routers/email_templates.py:21, images des modèles.
#: Oubliés, leurs objets resteraient sur Supabase et leurs liens mourraient le
#: jour de la fermeture du projet — c'est-à-dire APRÈS la bascule, quand plus
#: personne ne regarde.
BUCKETS = ["cvs", "avatars", "ao-sources", "compliance", "email-assets"]

#: La règle d'ACL n'est PAS redéfinie ici : elle est IMPORTÉE de
#: services/storage.py, qui l'applique à l'exécution.
#:
#: C'est la correction de fond. Ce script portait sa propre copie de la règle, et
#: les deux ont divergé : le code d'exécution gardait les CV privés, le script
#: écrivait « public-read » sur tout. Chacun des deux fichiers était cohérent
#: avec lui-même — c'est précisément pour ça que l'écart ne se voyait pas.
#: Une seule source de vérité, et la question ne peut plus se poser.


def _s3():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


def _walk_supabase(bucket: str, prefix: str = "") -> list:
    """Recursively list every object path inside a Supabase bucket."""
    paths = []
    entries = supabase.storage.from_(bucket).list(prefix) or []
    for entry in entries:
        name = entry["name"]
        child = f"{prefix}/{name}" if prefix else name
        # A folder has no id/metadata in Supabase Storage listings.
        if entry.get("id") is None and entry.get("metadata") is None:
            paths.extend(_walk_supabase(bucket, child))
        else:
            paths.append(child)
    return paths


def migrate_files(dry_run: bool, vers: str) -> int:
    s3 = None if (dry_run or vers != "s3") else _s3()
    total = 0
    for bucket in BUCKETS:
        paths = _walk_supabase(bucket)
        print(f"\n[{bucket}] {len(paths)} objet(s) à migrer")
        for path in paths:
            key = f"{bucket}/{path}"
            prive = bucket not in PUBLIC_BUCKETS
            if dry_run:
                print(f"  DRY-RUN copierait → {key}  [{'privé' if prive else 'public'}]")
                continue
            data = supabase.storage.from_(bucket).download(path)
            if vers == "local":
                # local_write() plutôt qu'un open() maison : c'est elle qui
                # valide le chemin (traversée) et impose 0600/0700 quel que soit
                # le umask. Une seconde écriture ici finirait par ne plus poser
                # les mêmes droits que celle du chemin d'exécution.
                cible = storage.local_write(bucket, path, data)
                print(f"  ✓ {cible}{'  (privé)' if prive else ''}")
            else:
                content_type = (
                    "application/pdf" if path.endswith(".pdf") else "application/octet-stream"
                )
                extra = {} if prive else {"ACL": "public-read"}
                s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=data,
                              ContentType=content_type, **extra)
                print(f"  ✓ {key}{'' if not prive else '  (privé)'}")
            total += 1
    return total


def _chemin_objet(ancienne_valeur: str, bucket: str) -> Optional[str]:
    """Chemin de l'objet dans `bucket`, extrait d'une valeur stockée.

    Deux formes rencontrées en base : l'URL publique Supabase
    (« …/storage/v1/object/public/<bucket>/<chemin> ») et, sur un déploiement
    déjà passé en S3, « …/<bucket>/<chemin> ». Un chemin déjà nu ne contient
    aucun des deux marqueurs : on renvoie None, et la ligne est laissée telle
    quelle — ce qui rend le script REJOUABLE sans dégât.
    """
    for marqueur in (f"/public/{bucket}/", f"/{bucket}/"):
        if marqueur in ancienne_valeur:
            return ancienne_valeur.split(marqueur, 1)[1].split("?", 1)[0]
    return None


def _nouvelle_valeur(ancienne: str, bucket: str, vers: str) -> Optional[str]:
    """Ce qu'il faut écrire en base à la place de `ancienne`, ou None.

    En LOCAL, un bucket privé n'a pas d'URL stable : on stocke le CHEMIN NU,
    exactement ce que renvoie storage.upload() dans ce mode
    (services/storage.py:get_public_url). Les lecteurs existants s'en accommodent
    déjà — storage._object_path() rend le chemin tel quel quand il n'y trouve
    pas de marqueur (routers/submissions.py:210, routers/partners.py:428).
    """
    chemin = _chemin_objet(ancienne or "", bucket)
    if not chemin:
        return None
    if vers == "local":
        if bucket in PUBLIC_BUCKETS:
            base = (settings.public_base_url or "").rstrip("/")
            return f"{base}/files/public/{bucket}/{quote(chemin, safe='/')}"
        return chemin
    base = (settings.s3_public_base_url or "").rstrip("/")
    return f"{base}/{bucket}/{chemin}"


def _reecrire_colonne(table: str, colonne: str, bucket: str, vers: str, dry_run: bool) -> int:
    """Réécrit `table.colonne` pour toutes les lignes qui portent encore une URL."""
    lignes = supabase.table(table).select(f"id, {colonne}").execute().data or []
    modifiees = 0
    for row in lignes:
        ancienne = row.get(colonne) or ""
        nouvelle = _nouvelle_valeur(ancienne, bucket, vers)
        if nouvelle and nouvelle != ancienne:
            print(f"  {table} {row['id']}: → {nouvelle}")
            if not dry_run:
                supabase.table(table).update({colonne: nouvelle}).eq("id", row["id"]).execute()
            modifiees += 1
    return modifiees


def _reecrire_modeles_email(vers: str, dry_run: bool) -> int:
    """Réécrit les URLs d'images DANS le corps HTML des modèles d'e-mail.

    POURQUOI CETTE FONCTION EXISTE
    Les images des modèles ne sont référencées par aucune colonne : elles vivent
    à l'intérieur du HTML de `email_templates.body`
    (supabase_migration_email_templates.sql:7). Copier le bucket « email-assets »
    sans toucher aux modèles laisserait chaque image pointer vers un projet
    Supabase supprimé : les e-mails partiraient avec des images cassées, chez le
    CLIENT, plusieurs semaines après la bascule — quand plus personne ne fait le
    lien avec ce chantier.
    """
    motif = re.compile(r"https?://[^\s\"'<>)]+?/email-assets/([^\s\"'<>)?]+)")
    lignes = supabase.table("email_templates").select("key, body").execute().data or []
    modifiees = 0
    for row in lignes:
        corps = row.get("body") or ""

        def _remplacer(m):
            return _nouvelle_valeur(m.group(0), "email-assets", vers) or m.group(0)

        nouveau = motif.sub(_remplacer, corps)
        if nouveau != corps:
            print(f"  email_templates {row['key']}: {len(motif.findall(corps))} image(s) réécrite(s)")
            if not dry_run:
                supabase.table("email_templates").update({"body": nouveau}).eq(
                    "key", row["key"]
                ).execute()
            modifiees += 1
    return modifiees


def rewrite_db(dry_run: bool, vers: str) -> None:
    """Réécrit les valeurs stockées vers la nouvelle destination."""
    print("\n[DB] Réécriture des URLs…")
    total = 0
    total += _reecrire_colonne("submissions", "cv_url", "cvs", vers, dry_run)
    total += _reecrire_colonne("profiles", "avatar_url", "avatars", vers, dry_run)
    # partner_compliance_docs.file_url manquait : ces lignes portent des URLs
    # Supabase vers des attestations URSSAF et des KBIS. routers/partners.py:428
    # sait encore retrouver le chemin dedans, donc rien ne casse tout de suite —
    # mais la base garderait indéfiniment l'adresse d'un projet supprimé comme
    # référence de pièces contractuelles.
    total += _reecrire_colonne("partner_compliance_docs", "file_url", "compliance", vers, dry_run)
    total += _reecrire_modeles_email(vers, dry_run)
    # appels_offres.source_files stocke déjà un CHEMIN NU (routers/aos.py:657) :
    # rien à réécrire, et c'est la forme cible dans les deux modes.
    print(f"[DB] {total} ligne(s) concernée(s).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrer le stockage Supabase → disque du VPS (ou OVH Object Storage)"
    )
    parser.add_argument("--dry-run", action="store_true", help="simulation, n'écrit rien")
    parser.add_argument("--rewrite-db", action="store_true", help="met aussi à jour les URLs en base")
    parser.add_argument(
        "--vers", choices=("local", "s3"), default="local",
        help="destination : « local » = disque du VPS (défaut), « s3 » = OVH Object Storage",
    )
    args = parser.parse_args()

    if not args.dry_run:
        if args.vers == "s3" and (not settings.s3_bucket or not settings.s3_access_key):
            print("❌ Variables S3 manquantes (S3_BUCKET / S3_ACCESS_KEY / …). Voir .env.example.")
            return 2
        if args.vers == "local" and args.rewrite_db and not settings.public_base_url:
            # Sans elle, les avatars seraient réécrits vers « /files/public/… »,
            # une URL relative que le frontend Vercel résoudrait sur son propre
            # domaine. La panne se verrait à la première page chargée.
            print("❌ PUBLIC_BASE_URL manquant : les URLs d'avatars seraient relatives.")
            return 2

    destination = (
        settings.local_storage_dir if args.vers == "local"
        else f"{settings.s3_bucket} @ {settings.s3_endpoint_url}"
    )
    print("=== Migration du stockage depuis Supabase ===")
    print(f"  Destination : [{args.vers}] {destination}")
    print(f"  Mode        : {'DRY-RUN' if args.dry_run else 'RÉEL'}")

    copied = migrate_files(args.dry_run, args.vers)
    print(f"\n{copied} fichier(s) copié(s).")

    if args.rewrite_db:
        rewrite_db(args.dry_run, args.vers)

    print("\n✅ Terminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
