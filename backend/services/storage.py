"""
Storage abstraction over Supabase Storage, OVH Object Storage (S3) and the
LOCAL DISK of the VPS.

The active backend is selected by ``settings.storage_backend``:
  - "supabase" — Supabase Storage (historique).
  - "s3"       — OVH Object Storage via l'API S3 (boto3).
  - "local"    — le disque du VPS, sous ``settings.local_storage_dir``.

Call sites use logical bucket names ("cvs", "avatars"). Pour S3 ce sont des
préfixes de clé ; pour « local » ce sont des sous-répertoires. Le code appelant
est identique dans les trois cas.

── POURQUOI UN TROISIÈME BACKEND ──────────────────────────────────────────
Obtenir un conteneur de stockage objet exige un accès OVH qui appartient à un
associé. Le volume est de 38 objets pour ~50 Mo, et le VPS a 185 Go libres :
poser les fichiers sur son disque supprime une dépendance externe au lieu d'en
ajouter une. La contrepartie — la SEULE — est que plus personne ne les
sauvegarde à notre place : c'est le rôle de deploy/backup_db.sh, qui embarque
désormais ce répertoire dans la même archive chiffrée que la base.

── CE QUI CHANGE VRAIMENT : SERVIR LES FICHIERS PRIVÉS ────────────────────
Supabase et S3 signent une URL que le NAVIGATEUR ouvre directement. En local,
il n'y a plus personne pour signer : c'est le backend qui sert le fichier
(routers/files.py). Or cette URL est ouverte SANS en-tête Authorization —
nouvel onglet, balise <img>, lien dans un e-mail envoyé à un client. La preuve
d'autorisation doit donc tenir DANS l'URL.

D'où la forme retenue : le chemin de l'objet vit À L'INTÉRIEUR du jeton signé,
et nulle part ailleurs dans l'URL (« /files/d/<jeton> »). Une signature qui ne
couvre pas le chemin est un jeton d'accès arbitraire : ici, la question ne peut
pas se poser, puisqu'il n'y a pas de chemin hors de la signature à comparer.
"""
# PEP 563 : les annotations ne sont plus évaluées à l'exécution. Sans cela,
# toute fonction annotée `list[...]` définie APRÈS le `def list` ci-dessous
# essaierait d'indexer cette fonction et lèverait TypeError à l'import.
from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from jose import jwt, JWTError

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
#: l'objet qui décide, et elle disait « public-read ». En LOCAL, c'est cette
#: liste, et elle seule, qui décide de ce que routers/files.py accepte de servir
#: sans jeton.
#:
#: Liste BLANCHE, et c'est le point : un bucket ajouté demain naît privé. Avec
#: l'ancien test, il naissait public.
PUBLIC_BUCKETS = {"avatars", "email-assets"}

_s3_client = None

# ── Backend local : modes UNIX, imposés explicitement ──────────────────────
# Pas d'umask : le umask est un réglage de process (0022 par défaut sur Ubuntu),
# donc un fichier créé sans mode explicite serait lisible par TOUT compte local
# du VPS. Un CV, une attestation URSSAF ou un KBIS n'ont qu'un lecteur légitime :
# le backend. Voir deploy/INSTALLATION.md §7 pour le raisonnement complet.
LOCAL_DIR_MODE = 0o700
LOCAL_FILE_MODE = 0o600

#: Un nom de bucket est un composant de chemin : il ne doit contenir NI séparateur
#: NI point, sans quoi « ../.. » passerait par la porte du bucket plutôt que par
#: celle du chemin.
_BUCKET_AUTORISE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")

#: Plafond de durée de vie d'une URL signée. Le plus long usage légitime est le
#: lien de CV envoyé au client final (services/cv_notifications.py:114, 7 jours).
#: Le plafonner ici empêche qu'un appelant écrive un jour `expires_in=10**9` et
#: fabrique une URL éternelle sans que personne ne le remarque.
MAX_SIGNED_URL_TTL = 7 * 24 * 3600

#: Algorithme et « audience » du jeton de fichier. L'audience est ce qui rend un
#: jeton de SESSION inutilisable comme jeton de fichier, et réciproquement.
FILE_TOKEN_ALG = "HS256"
FILE_TOKEN_AUDIENCE = "uti-file"


class StoragePathError(ValueError):
    """Chemin d'objet refusé (traversée, chemin absolu, bucket invalide).

    Hérite de ValueError pour que les appelants qui enveloppent déjà leurs
    accès dans `except Exception` (routers/aos.py:44, routers/partners.py:428)
    continuent de se comporter comme avant.
    """


def _use_s3() -> bool:
    return settings.storage_backend == "s3"


def _use_local() -> bool:
    return settings.storage_backend == "local"


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


def _base_publique() -> str:
    """Origine publique du BACKEND (nginx/HTTPS), pas celle du frontend.

    Les URLs produites ici sont ouvertes depuis un autre domaine (le frontend
    est sur Vercel) et depuis un client de messagerie : une URL relative n'y
    signifie rien. config.py refuse de démarrer en backend « local » sans cette
    valeur, pour que l'oubli casse au démarrage plutôt qu'à la première pièce
    jointe cliquée.
    """
    return (settings.public_base_url or "").rstrip("/")


# ── Chemins locaux : la seule fonction qui a le droit de fabriquer un chemin ──

def _racine_locale() -> Path:
    return Path(settings.local_storage_dir).expanduser()


def _safe_join(bucket: str, path: str) -> Path:
    """Chemin absolu de l'objet, ou StoragePathError.

    POURQUOI DEUX CONTRÔLES QUI SEMBLENT REDONDANTS
    `path` vient de la base, et la base a déjà connu des valeurs de formes
    diverses (voir _object_path) : URL publique Supabase, URL S3, chemin nu.
    Une ligne trafiquée, ou simplement un `..` hérité d'un import, ne doit
    jamais pouvoir désigner un fichier hors du dépôt.

      1. contrôle SYNTAXIQUE des segments — attrape « ../.. » et « /etc/passwd »
         avant même de toucher au disque, donc sans dépendre de l'existence des
         répertoires intermédiaires ;
      2. contrôle par RÉSOLUTION — `resolve()` suit les liens symboliques :
         c'est le seul des deux qui attrape un lien déposé DANS le dépôt et
         pointant dehors. Le contrôle syntaxique, lui, ne le voit pas.

    Aucun des deux ne couvre ce que couvre l'autre ; c'est pour cela qu'ils sont
    tous les deux là.
    """
    nom = (bucket or "").strip()
    if not nom or not set(nom) <= _BUCKET_AUTORISE:
        raise StoragePathError(f"nom de bucket invalide : {bucket!r}")

    brut = (path or "").strip()
    if not brut:
        raise StoragePathError("chemin d'objet vide")
    if "\x00" in brut:
        # Un octet nul tronque le nom au niveau de l'appel système : « a.pdf\0.exe »
        # deviendrait « a.pdf » côté noyau et autre chose côté contrôle.
        raise StoragePathError("chemin d'objet contenant un octet nul")

    # Les antislashs ne sont pas des séparateurs sous Linux, mais une valeur
    # héritée peut en contenir. On les normalise AVANT le contrôle des segments
    # plutôt que de laisser « ..\\..\\etc » passer pour un nom de fichier.
    normalise = brut.replace("\\", "/")
    if normalise.startswith("/"):
        raise StoragePathError(f"chemin absolu refusé : {path!r}")

    segments = [s for s in normalise.split("/") if s]
    if not segments:
        raise StoragePathError("chemin d'objet vide après normalisation")
    for segment in segments:
        if segment in (".", ".."):
            raise StoragePathError(f"traversée de chemin refusée : {path!r}")

    base = (_racine_locale() / nom).resolve()
    cible = base.joinpath(*segments).resolve()
    if cible == base or not cible.is_relative_to(base):
        raise StoragePathError(f"chemin hors du dépôt de fichiers : {path!r}")
    return cible


def _mkdir_securise(dossier: Path) -> None:
    """Crée l'arborescence en 0700, indépendamment du umask du process."""
    a_creer = []
    courant = dossier
    while not courant.exists():
        a_creer.append(courant)
        courant = courant.parent
    for chemin in reversed(a_creer):
        chemin.mkdir(exist_ok=True)
        os.chmod(chemin, LOCAL_DIR_MODE)


def local_write(bucket: str, path: str, content: bytes) -> Path:
    """Écrit un objet sur le disque local. Utilisée par upload() ET par
    scripts/migrate_storage_to_ovh.py, pour que la validation de chemin et les
    modes UNIX n'aient qu'une seule définition — c'est la leçon de
    tests/test_storage_acl.py:59, où une règle recopiée avait divergé."""
    cible = _safe_join(bucket, path)
    _mkdir_securise(cible.parent)
    # Écriture atomique : un fichier au nom définitif est un fichier complet.
    # Sans cela, une coupure en cours d'upload laisserait un CV tronqué que la
    # base référencerait comme valide.
    provisoire = cible.parent / f".{cible.name}.partiel"
    descripteur = os.open(provisoire, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, LOCAL_FILE_MODE)
    try:
        with os.fdopen(descripteur, "wb") as sortie:
            sortie.write(content)
            sortie.flush()
            os.fsync(sortie.fileno())
    except BaseException:
        provisoire.unlink(missing_ok=True)
        raise
    os.chmod(provisoire, LOCAL_FILE_MODE)  # O_CREAT applique le umask au mode
    os.replace(provisoire, cible)
    return cible


# ── Jeton d'URL de fichier ─────────────────────────────────────────────────

def _cle_de_signature() -> str:
    """Clé HMAC des URLs de fichiers.

    FAUT-IL LE MÊME SECRET QUE POUR LES SESSIONS ? NON.
    Un jeton de session voyage dans un en-tête Authorization ; un jeton de
    fichier voyage DANS L'URL, donc dans les journaux nginx, dans l'historique
    du navigateur, dans l'en-tête Referer, et — pour le lien de CV envoyé au
    client — dans une boîte mail que nous ne maîtrisons pas. Ce sont deux
    niveaux d'exposition différents, et le dépôt a déjà tranché ce genre de
    question dans le même sens : .env.example:60-65 impose que JWT_SECRET diffère
    du secret PostgREST, parce qu'un jeton de session porte une revendication
    `role` qui en ferait une clé de base de données valide.

    Ce qu'on veut ici est la SÉPARATION DE DOMAINE : qu'un jeton de session ne
    puisse jamais servir d'URL de fichier, ni l'inverse. Deux barrières :
      * une clé DIFFÉRENTE, dérivée de jwt_secret par HMAC quand FILE_URL_SECRET
        n'est pas renseigné — la dérivation donne la séparation cryptographique
        sans ajouter un secret de plus à gérer, à faire tourner et à oublier ;
      * une audience explicite (`aud`), vérifiée au décodage : même avec la même
        clé, un jeton de session serait refusé.

    FILE_URL_SECRET reste disponible pour rendre les deux clés totalement
    indépendantes — utile le jour où l'on voudra invalider d'un coup toutes les
    URLs de fichiers émises sans déconnecter les 11 utilisateurs.
    """
    if settings.file_url_secret:
        return settings.file_url_secret
    return hmac.new(
        settings.jwt_secret.encode("utf-8"), b"uti/file-url/v1", hashlib.sha256
    ).hexdigest()


def sign_file_token(bucket: str, path: str, expires_in: int = 3600) -> str:
    """Jeton court liant EXACTEMENT un bucket et un chemin.

    Le chemin est signé, pas seulement transporté : c'est toute la différence
    entre « ce porteur peut lire CE fichier » et « ce porteur peut lire ».
    """
    if _use_local():
        # Refuser de SIGNER ce qu'on refusera de SERVIR : sans cette ligne, on
        # distribuerait des liens qui échouent, et l'erreur se lirait comme une
        # panne de stockage plutôt que comme une donnée trafiquée en base.
        _safe_join(bucket, path)
    duree = max(1, min(int(expires_in), MAX_SIGNED_URL_TTL))
    maintenant = int(time.time())
    charge = {
        "b": bucket,
        "p": path,
        "aud": FILE_TOKEN_AUDIENCE,
        "iat": maintenant,
        "exp": maintenant + duree,
    }
    return jwt.encode(charge, _cle_de_signature(), algorithm=FILE_TOKEN_ALG)


def verify_file_token(token: str) -> tuple[str, str]:
    """(bucket, chemin) porté par un jeton valide. Lève JWTError sinon.

    `algorithms=[HS256]` interdit `alg: none` et la confusion d'algorithme.

    POURQUOI LES `require_*` NE SONT PAS DÉCORATIFS
    Par défaut, python-jose ne fait que VALIDER les revendications PRÉSENTES.
    Un jeton sans `exp` traverse `_validate_exp` sans un mot — donc il n'expire
    JAMAIS. Et son `_validate_aud` a son `raise` en commentaire quand la
    revendication est absente : un jeton sans `aud` passe aussi.

    Autrement dit, sans les trois lignes ci-dessous, la phrase « l'audience
    interdit de présenter un jeton de session » serait fausse — il suffirait
    d'omettre la revendication. La seule barrière restante serait la clé
    dérivée, ce qui est une barrière de trop peu : elle tombe le jour où
    quelqu'un aligne FILE_URL_SECRET sur JWT_SECRET « pour simplifier »
    (config.py refuse désormais ce cas, mais on ne construit pas une garantie
    sur une seule ligne de configuration).

    Il faut donc EXIGER la présence de ce qu'on prétend vérifier.
    """
    claims = jwt.decode(
        token,
        _cle_de_signature(),
        algorithms=[FILE_TOKEN_ALG],
        audience=FILE_TOKEN_AUDIENCE,
        options={"require_exp": True, "require_aud": True, "require_iat": True},
    )
    bucket, chemin = claims.get("b"), claims.get("p")
    if not isinstance(bucket, str) or not isinstance(chemin, str) or not bucket or not chemin:
        raise JWTError("jeton de fichier sans bucket ni chemin")
    return bucket, chemin


# ── Types servis : ce qu'on accepte d'afficher DANS le navigateur ──────────
# Les fichiers étaient servis par supabase.co ou par s3.io.cloud.ovh.net : une
# AUTRE ORIGINE. Servis par le backend, ils deviennent du contenu SAME-ORIGIN de
# l'API. Un .html ou un .svg déposé comme « pièce de conformité »
# (routers/partners.py:365 n'impose aucune extension) s'exécuterait alors dans
# l'origine de la plateforme. Liste blanche, donc, et tout le reste part en
# téléchargement avec un type opaque — jamais rendu.
INLINE_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
}


def content_disposition_for(nom_fichier: str) -> tuple[str, str]:
    """(content_type, disposition) pour un nom de fichier servi.

    Renvoie toujours un couple sûr : hors liste blanche, « octet-stream » +
    « attachment », qui se télécharge au lieu de s'exécuter. Combiné à
    `X-Content-Type-Options: nosniff`, aucun contenu déposé par un tiers ne peut
    être interprété comme du HTML dans notre origine.
    """
    extension = os.path.splitext(nom_fichier)[1].lower()
    type_inline = INLINE_CONTENT_TYPES.get(extension)
    if type_inline:
        return type_inline, "inline"
    # mimetypes n'est PAS consulté ici : il connaît text/html et image/svg+xml,
    # et son avis est précisément celui qu'on ne veut pas suivre.
    return "application/octet-stream", "attachment"


def entete_disposition(disposition: str, nom_fichier: str) -> str:
    """En-tête `Content-Disposition` complet, sûr quel que soit le nom.

    Le nom vient de `f.filename`, donc de l'utilisateur : `_safe_join` contrôle
    la traversée de chemin, jamais le jeu de caractères. Interpolé brut, il
    produit deux défauts, l'un bloquant et l'autre trompeur :

      * tout octet hors latin-1 fait lever UnicodeEncodeError à l'encodage de
        l'en-tête. « CV d'Alice.pdf » avec l'apostrophe typographique U+2019 —
        celle que produisent Word et macOS — rendrait le fichier DÉFINITIVEMENT
        inouvrable : il est pourtant stocké, référencé en base, et rien
        n'indique que c'est son NOM qui bloque ;
      * un guillemet double referme la valeur quotée et laisse afficher au
        téléchargement un nom sans rapport avec le fichier réel.

    RFC 6266 : les deux formes cohabitent. `filename=` en repli ASCII nettoyé
    pour les clients anciens, `filename*=` en UTF-8 percent-encodé pour le nom
    fidèle. Les navigateurs actuels préfèrent la seconde ; les autres se
    rabattent sur la première, dégradée mais valide.
    """
    # Repli ASCII : on retire d'abord ce qui casserait la SYNTAXE de l'en-tête
    # (guillemet, antislash, retours à la ligne), puis tout ce qui n'est pas
    # représentable en ASCII.
    sans_syntaxe = "".join(c for c in (nom_fichier or "") if c not in '"\\\r\n')
    repli = sans_syntaxe.encode("ascii", "ignore").decode("ascii").strip()
    if not repli:
        # Un nom entièrement non-ASCII (« 履歴書.pdf ») ne doit pas produire
        # `filename=""` : mieux vaut un nom générique que rien du tout.
        repli = "fichier"
    return (
        f"{disposition}; filename=\"{repli}\"; "
        f"filename*=UTF-8''{quote(nom_fichier or repli, safe='')}"
    )


# ── API publique du module (identique pour les trois backends) ─────────────

def ensure_bucket(bucket: str, public: bool = False) -> None:
    """
    Best-effort : garantit l'existence d'un bucket.

    En LOCAL, crée le sous-répertoire en 0700. L'argument `public` est
    délibérément IGNORÉ dans ce cas : c'est PUBLIC_BUCKETS qui décide, jamais
    l'appelant. Sans cela, routers/email_templates.py:224 (`public=True`)
    suffirait à rendre publics des documents si le nom de bucket changeait un
    jour, et un appelant distrait pourrait ouvrir « compliance ».

    En S3 c'est un no-op (les « buckets » logiques ne sont que des préfixes).
    """
    if _use_local():
        try:
            _mkdir_securise(_racine_locale() / bucket)
        except OSError:
            pass  # best-effort, comme la branche Supabase : upload() lèvera clairement
        return
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
    """URL stable et ouvrable SANS signature — donc réservée aux buckets publics.

    En LOCAL, un bucket privé n'a PAS d'URL stable : on renvoie le chemin nu,
    que _object_path relit et que signed_url transforme en lien court. C'est ce
    que la docstring d'upload() promet depuis toujours (« or path for private
    buckets ») et ce dont dépend routers/partners.py:428, qui reconstruit le
    chemin depuis la valeur stockée.
    """
    if _use_local():
        if bucket not in PUBLIC_BUCKETS:
            return path
        # quote() : les chemins publics restent simples aujourd'hui, mais un nom
        # de fichier avec un « ? » ou un « # » couperait l'URL en deux.
        return f"{_base_publique()}/files/public/{bucket}/{quote(path, safe='/')}"
    if _use_s3():
        base = (settings.s3_public_base_url or "").rstrip("/")
        return f"{base}/{bucket}/{path}"
    return supabase.storage.from_(bucket).get_public_url(path)


def upload(bucket: str, path: str, content: bytes, content_type: str) -> str:
    """Upload bytes and return the object's public URL (or path for private buckets).

    `content_type` n'est pas conservé par le backend local : un fichier sur
    disque n'a pas de métadonnée. Le type est redéduit à la lecture depuis
    l'extension (content_disposition_for) — c'est justement pourquoi cette
    déduction passe par une liste blanche et non par mimetypes.
    """
    if _use_local():
        local_write(bucket, path, content)
    elif _use_s3():
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
    """Read an object's raw bytes. Raises on failure."""
    if _use_local():
        return _safe_join(bucket, path).read_bytes()
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
    """Time-limited URL for a private object.

    En LOCAL, le chemin ne figure PAS dans l'URL : il est à l'intérieur du jeton.
    Rien à comparer, donc rien à désynchroniser — et un nom de fichier exotique
    (espaces, accents, « # ») ne peut pas casser l'URL.
    """
    if not path:
        return None
    if _use_local():
        return f"{_base_publique()}/files/d/{sign_file_token(bucket, path, expires_in)}"
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
    if _use_local():
        for chemin in paths:
            cible = _safe_join(bucket, chemin)
            cible.unlink(missing_ok=True)
            # Le répertoire par AO / par utilisateur devient vide après la purge
            # RGPD (services/data_retention.py:42). Le laisser ne coûte rien mais
            # rend l'inventaire des fichiers illisible ; rmdir échoue s'il reste
            # quoi que ce soit, ce qui est exactement la garde qu'on veut.
            try:
                cible.parent.rmdir()
            except OSError:
                pass
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
    if _use_local():
        try:
            dossier = _safe_join(bucket, prefix)
        except StoragePathError:
            return []
        if not dossier.is_dir():
            return []
        return [{"name": e.name} for e in sorted(dossier.iterdir()) if e.is_file()]
    if _use_s3():
        full_prefix = f"{bucket}/{prefix.rstrip('/')}/"
        resp = _s3().list_objects_v2(Bucket=settings.s3_bucket, Prefix=full_prefix)
        return [{"name": obj["Key"].split("/")[-1]} for obj in resp.get("Contents", [])]
    return supabase.storage.from_(bucket).list(prefix)
