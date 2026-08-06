#!/usr/bin/env python3
"""
Migration des fichiers existants : Supabase Storage → OVH Object Storage (S3).

Copie tous les objets des buckets Supabase ``cvs`` et ``avatars`` vers le
bucket OVH (en conservant l'arborescence sous les préfixes ``cvs/`` et
``avatars/``), puis — avec ``--rewrite-db`` — met à jour les URLs stockées en
base (``submissions.cv_url`` et ``profiles.avatar_url``) pour qu'elles pointent
vers OVH.

⚠️  À LIRE AVANT DE LANCER :
  - Lance d'abord en simulation : `python scripts/migrate_storage_to_ovh.py --dry-run`
  - Le script a besoin des variables Supabase ET S3 (voir .env.example).
  - Ne supprime RIEN sur Supabase : la copie est non destructive, tu pourras
    garder Supabase comme filet de sécurité le temps de vérifier.

Usage :
  cd backend
  python scripts/migrate_storage_to_ovh.py --dry-run        # simulation
  python scripts/migrate_storage_to_ovh.py                  # copie les fichiers
  python scripts/migrate_storage_to_ovh.py --rewrite-db     # copie + MAJ des URLs en base
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))
except ImportError:
    pass

from config import settings  # noqa: E402
from services.supabase_client import supabase  # noqa: E402

#: Buckets logiques à copier. « ao-sources » manquait : les fichiers sources des
#: appels d'offres (5 objets en production) seraient restés sur Supabase, et les
#: liens vers eux auraient cessé de fonctionner le jour où l'on ferme le projet.
BUCKETS = ["cvs", "avatars", "ao-sources"]

#: Buckets qui doivent rester PRIVÉS sur S3. La règle vient du code d'exécution
#: (services/storage.py:78, « CVs stay private on S3 too — no public-read ACL »)
#: et elle est reprise ici parce que ce script écrivait, lui, en public-read sans
#: distinction : une seule exécution aurait rendu les 32 CV lisibles par
#: quiconque connaît l'URL. Un CV, c'est un nom, un téléphone, un parcours —
#: exactement ce qu'on ne publie pas. Les deux endroits qui posent une ACL
#: doivent suivre la même règle, sinon la plus permissive gagne en silence.
PRIVATE_BUCKETS = {"cvs"}


def _s3():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


def _walk_supabase(bucket: str, prefix: str = "") -> list[str]:
    """Recursively list every object path inside a Supabase bucket."""
    paths: list[str] = []
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


def migrate_files(dry_run: bool) -> int:
    s3 = None if dry_run else _s3()
    total = 0
    for bucket in BUCKETS:
        paths = _walk_supabase(bucket)
        print(f"\n[{bucket}] {len(paths)} objet(s) à migrer")
        for path in paths:
            key = f"{bucket}/{path}"
            prive = bucket in PRIVATE_BUCKETS
            if dry_run:
                print(f"  DRY-RUN copierait → {key}  [{'privé' if prive else 'public'}]")
                continue
            data = supabase.storage.from_(bucket).download(path)
            content_type = "application/pdf" if path.endswith(".pdf") else "application/octet-stream"
            extra = {} if prive else {"ACL": "public-read"}
            s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=data,
                          ContentType=content_type, **extra)
            print(f"  ✓ {key}{'' if not prive else '  (privé)'}")
            total += 1
    return total


def rewrite_db(dry_run: bool) -> None:
    """Réécrit les URLs Supabase → S3 pour les lignes existantes."""
    base = (settings.s3_public_base_url or "").rstrip("/")

    def to_s3_url(old_url: str, bucket: str) -> str | None:
        # Extrait le chemin après .../public/<bucket>/ et le reconstruit côté S3.
        marker = f"/public/{bucket}/"
        if marker not in old_url:
            return None
        path = old_url.split(marker, 1)[1].split("?", 1)[0]
        return f"{base}/{bucket}/{path}"

    print("\n[DB] Réécriture des URLs…")
    subs = supabase.table("submissions").select("id, cv_url").execute().data or []
    for row in subs:
        new = to_s3_url(row.get("cv_url") or "", "cvs")
        if new and new != row["cv_url"]:
            print(f"  submission {row['id']}: → {new}")
            if not dry_run:
                supabase.table("submissions").update({"cv_url": new}).eq("id", row["id"]).execute()

    profiles = supabase.table("profiles").select("id, avatar_url").execute().data or []
    for row in profiles:
        new = to_s3_url(row.get("avatar_url") or "", "avatars")
        if new and new != row["avatar_url"]:
            print(f"  profile {row['id']}: → {new}")
            if not dry_run:
                supabase.table("profiles").update({"avatar_url": new}).eq("id", row["id"]).execute()


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrer le stockage Supabase → OVH Object Storage")
    parser.add_argument("--dry-run", action="store_true", help="simulation, n'écrit rien")
    parser.add_argument("--rewrite-db", action="store_true", help="met aussi à jour les URLs en base")
    args = parser.parse_args()

    if not args.dry_run and (not settings.s3_bucket or not settings.s3_access_key):
        print("❌ Variables S3 manquantes (S3_BUCKET / S3_ACCESS_KEY / …). Voir .env.example.")
        return 2

    print("=== Migration stockage Supabase → OVH ===")
    print(f"  Bucket OVH : {settings.s3_bucket} | Endpoint : {settings.s3_endpoint_url}")
    print(f"  Mode       : {'DRY-RUN' if args.dry_run else 'RÉEL'}")

    copied = migrate_files(args.dry_run)
    print(f"\n{copied} fichier(s) copié(s).")

    if args.rewrite_db:
        rewrite_db(args.dry_run)

    print("\n✅ Terminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
