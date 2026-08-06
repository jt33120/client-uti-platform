"""
Storage abstraction over Supabase Storage and OVH Object Storage (S3-compatible).

The active backend is selected by ``settings.storage_backend``:
  - "supabase" (default) — keeps using Supabase Storage. Nothing changes.
  - "s3"               — uses OVH Object Storage via the S3 API (boto3).

Call sites use logical bucket names ("cvs", "avatars"). For S3 these are
mapped to key prefixes inside a single OVH bucket, so the application code
stays identical regardless of the backend.
"""
from typing import Optional

from config import settings
from services.supabase_client import supabase

#: Buckets dont les objets sont lus DIRECTEMENT par le navigateur, donc rendus
#: publics sur S3 : l'avatar s'affiche dans une balise <img>, et les images des
#: modèles d'e-mails doivent se charger dans le client de messagerie du
#: destinataire — une URL signée y serait périmée avant d'être ouverte.
#:
#: Tout le reste est PRIVÉ et ne s'ouvre que par URL signée ou côté serveur.
#:
#: Cette liste remplace un test `bucket == "cvs"` qui ne protégeait que les CV et
#: laissait passer en lecture publique deux catégories de fichiers que le code
#: traite pourtant comme privées :
#:   * les pièces jointes d'appel d'offres — routers/aos.py crée le bucket avec
#:     public=False et les sert par URL signée ;
#:   * les documents de conformité des partenaires — c'est-à-dire des
#:     attestations de vigilance URSSAF et des KBIS.
#:
#: Sur Supabase, la politique du bucket les protégeait. Sur S3, c'est l'ACL de
#: l'objet qui décide, et elle disait « public-read ».
#:
#: Liste BLANCHE, et c'est le point : un bucket ajouté demain naît privé. Avec
#: l'ancien test, il naissait public.
PUBLIC_BUCKETS = {"avatars", "email-assets"}

_s3_client = None


def _use_s3() -> bool:
    return settings.storage_backend == "s3"


def _s3():
    """Lazily build a boto3 S3 client (only imported when S3 is actually used)."""
    global _s3_client
    if _s3_client is None:
        import boto3  # imported lazily so Supabase-only deploys don't need boto3

        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )
    return _s3_client


def ensure_bucket(bucket: str, public: bool = False) -> None:
    """
    Best-effort : garantit l'existence d'un bucket Supabase (no-op en S3, où les
    « buckets » logiques ne sont que des préfixes dans l'unique bucket OVH).
    """
    if _use_s3():
        return
    try:
        existing = supabase.storage.list_buckets() or []
        names = {
            (getattr(b, "name", None) or (b.get("name") if isinstance(b, dict) else None))
            for b in existing
        }
        if bucket in names:
            return
    except Exception:
        pass
    for attempt in (
        lambda: supabase.storage.create_bucket(bucket, options={"public": public}),
        lambda: supabase.storage.create_bucket(bucket),
    ):
        try:
            attempt()
            return
        except Exception:
            continue


def get_public_url(bucket: str, path: str) -> str:
    if _use_s3():
        base = (settings.s3_public_base_url or "").rstrip("/")
        return f"{base}/{bucket}/{path}"
    return supabase.storage.from_(bucket).get_public_url(path)


def upload(bucket: str, path: str, content: bytes, content_type: str) -> str:
    """Upload bytes and return the object's public URL (or path for private buckets)."""
    if _use_s3():
        extra = {"ACL": "public-read"} if bucket in PUBLIC_BUCKETS else {}
        _s3().put_object(
            Bucket=settings.s3_bucket,
            Key=f"{bucket}/{path}",
            Body=content,
            ContentType=content_type,
            **extra,
        )
    else:
        supabase.storage.from_(bucket).upload(path, content, {"content-type": content_type})
    return get_public_url(bucket, path)


def download(bucket: str, path: str) -> bytes:
    """Read an object's raw bytes (S3 or Supabase). Raises on failure."""
    if _use_s3():
        obj = _s3().get_object(Bucket=settings.s3_bucket, Key=f"{bucket}/{path}")
        return obj["Body"].read()
    return supabase.storage.from_(bucket).download(path)


def _object_path(bucket: str, stored: Optional[str]) -> Optional[str]:
    """Recover the object path inside `bucket` from a stored value that may be a
    full public URL (legacy rows) or already a bare path."""
    if not stored:
        return stored
    marker = f"/{bucket}/"
    if marker in stored:
        return stored.split(marker, 1)[1].split("?", 1)[0]
    return stored.lstrip("/")


def signed_url(bucket: str, path: str, expires_in: int = 3600) -> Optional[str]:
    """Time-limited URL for a private object (Supabase or S3)."""
    if not path:
        return None
    if _use_s3():
        return _s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": f"{bucket}/{path}"},
            ExpiresIn=expires_in,
        )
    res = supabase.storage.from_(bucket).create_signed_url(path, expires_in)
    url = None
    if isinstance(res, dict):
        url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
    if url and not url.startswith("http"):
        base = settings.supabase_url.rstrip("/")
        url = f"{base}{url if url.startswith('/') else '/' + url}"
    return url


def signed_cv_url(stored: Optional[str], expires_in: int = 3600) -> Optional[str]:
    """Fresh signed URL for a CV, whatever is stored in submissions.cv_url
    (legacy public URL or bare path). Works whether the bucket is public or
    private, so it's safe to ship before flipping the bucket to private."""
    if not stored:
        return stored
    return signed_url("cvs", _object_path("cvs", stored), expires_in)


def remove(bucket: str, paths: list[str]) -> None:
    if not paths:
        return
    if _use_s3():
        _s3().delete_objects(
            Bucket=settings.s3_bucket,
            Delete={"Objects": [{"Key": f"{bucket}/{p}"} for p in paths]},
        )
    else:
        supabase.storage.from_(bucket).remove(paths)


def list(bucket: str, prefix: str) -> list[dict]:
    """List objects under ``prefix``. Returns dicts with at least a ``name`` key."""
    if _use_s3():
        full_prefix = f"{bucket}/{prefix.rstrip('/')}/"
        resp = _s3().list_objects_v2(Bucket=settings.s3_bucket, Prefix=full_prefix)
        return [{"name": obj["Key"].split("/")[-1]} for obj in resp.get("Contents", [])]
    return supabase.storage.from_(bucket).list(prefix)
