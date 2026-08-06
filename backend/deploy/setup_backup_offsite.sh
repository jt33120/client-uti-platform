#!/usr/bin/env bash
# =============================================================================
#  Mise en place du hors-site : paire de clés age + conteneur OVH inviolable.
#  À lancer UNE FOIS, et à relire le jour où l'on renouvelle une clé.
#
#  ── LE PIÈGE QUE CE FICHIER DÉSAMORCE ─────────────────────────────────────
#  backend/.env contient déjà S3_ACCESS_KEY / S3_SECRET_KEY (config.py:28-29)
#  pour les CV et les avatars. Réutiliser cette clé pour déposer les sauvegardes
#  serait la faute qui annule tout le chantier : celui qui prend le VPS lit le
#  .env, et avec la même clé il efface la production ET l'historique des
#  sauvegardes. On aurait dépensé un chantier entier pour déplacer les
#  sauvegardes de 20 cm.
#
#  ── LA PARADE, EN TROIS COUCHES INDÉPENDANTES ─────────────────────────────
#  1. CONTENEUR DISTINCT. Les sauvegardes ne partagent rien avec les CV. Une
#     erreur de préfixe ou une règle de cycle de vie mal posée d'un côté ne peut
#     pas atteindre l'autre.
#  2. UTILISATEUR S3 DISTINCT, SANS DROIT DE SUPPRESSION. La clé posée sur le
#     VPS peut DÉPOSER et RELIRE, pas EFFACER (backup_s3_policy.json).
#     ⚠️ PIÈGE OVH, à connaître : « implicit deny is not supported by OVHcloud
#     Object Storage if the user is the bucket owner » — le propriétaire d'un
#     conteneur garde l'ACL FULL_CONTROL et une politique restrictive ne
#     s'applique PAS à lui. Une politique « PutObject seulement » attachée au
#     propriétaire ne protège donc de RIEN. C'est pour cela que le conteneur
#     doit être créé par un PREMIER utilisateur (le propriétaire, dont la clé ne
#     touche jamais le VPS) et écrit par un SECOND (le déposant, dont la clé vit
#     dans /etc/uti-backup.env).
#     https://docs.ovhcloud.com/en/guides/storage-and-backup/object-storage/s3-identity-and-access-management/
#  3. VERROU D'OBJET (Object Lock, WORM) EN MODE COMPLIANCE. Dernière ligne, et
#     la seule qui tienne face à un attaquant qui obtiendrait AUSSI la clé du
#     propriétaire : en mode COMPLIANCE, « objects cannot be modified or deleted
#     by any user, including administrators, during the entire retention
#     period ». Activer le verrou active aussi le versionnage.
#     ⚠️ IRRÉVERSIBLE et NON RÉTROACTIF : un conteneur créé sans
#     --object-lock-enabled-for-bucket ne peut PAS le recevoir plus tard. C'est
#     la seule décision de ce chantier qu'on ne peut pas corriger après coup.
#     https://docs.ovhcloud.com/en/guides/storage-and-backup/object-storage/s3-managing-object-lock
#
#  ── CE QUE CE SCRIPT NE PEUT PAS FAIRE ────────────────────────────────────
#  OVH n'expose pas la création d'utilisateur S3 ni l'attachement de politique
#  par l'API S3 : cela passe par l'espace client (« Object Storage → Utilisateurs
#  → Importer une politique JSON »). Le script fait donc tout le reste et
#  IMPRIME les deux gestes manuels, dans l'ordre. Prétendre les automatiser
#  produirait un script qui échoue à mi-course et laisse un conteneur à moitié
#  configuré — le pire état possible.
#
#  USAGE
#      # identifiants du PROPRIÉTAIRE, utilisés une seule fois, jamais stockés
#      export OWNER_S3_ACCESS_KEY=… OWNER_S3_SECRET_KEY=…
#      export BACKUP_S3_ENDPOINT=https://s3.sbg.io.cloud.ovh.net
#      export BACKUP_S3_REGION=sbg BACKUP_S3_BUCKET=uti-sauvegardes
#      bash backend/deploy/setup_backup_offsite.sh
# =============================================================================
set -euo pipefail

BACKEND="${BACKEND_DIR:-/home/julian.talou/app/backend}"
BUCKET="${BACKUP_S3_BUCKET:-uti-sauvegardes}"
REGION="${BACKUP_S3_REGION:-sbg}"
ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
# 30 jours de verrou, 35 jours de cycle de vie. L'ordre compte : en mode
# COMPLIANCE, une règle d'expiration ne peut pas effacer un objet encore
# verrouillé. Expiration ≤ rétention produirait des objets que RIEN ne supprime
# jamais, et une facture qui monte sans fin.
RETENTION_J="${RETENTION_J:-30}"
EXPIRATION_J="${EXPIRATION_J:-35}"
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

etape() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info()  { printf '   %s\n' "$*"; }

# ── 1. Paire de clés age ────────────────────────────────────────────────────
etape "1/4 — Paire de clés age"
command -v age-keygen >/dev/null || { echo "age n'est pas installé : sudo apt install age"; exit 1; }

CLE_PRIVEE="${AGE_KEY_OUT:-$PWD/uti-backup.age-key}"
if [ -f "$CLE_PRIVEE" ]; then
  info "$CLE_PRIVEE existe déjà — AUCUNE régénération."
  info "Régénérer une clé rendrait ILLISIBLES toutes les archives déjà déposées."
else
  # umask 077 AVANT la création : sinon la clé existe en 644 pendant l'intervalle
  # entre open() et chmod, et un autre utilisateur peut la lire dans cet intervalle.
  ( umask 077; age-keygen -o "$CLE_PRIVEE" 2>/dev/null )
  info "Clé privée écrite dans $CLE_PRIVEE (mode 600)."
fi
PUBLIQUE=$(age-keygen -y "$CLE_PRIVEE")
info "Clé PUBLIQUE (à mettre dans uti-backup.service, ce n'est pas un secret) :"
printf '\n     AGE_RECIPIENT=%s\n\n' "$PUBLIQUE"

cat <<'AVERTISSEMENT'
   ┌──────────────────────────────────────────────────────────────────────┐
   │  OÙ VIT LA CLÉ PRIVÉE — la question qui décide si tout ceci sert.    │
   │                                                                      │
   │  Une clé privée dont la SEULE copie est sur le VPS rend les archives │
   │  inutiles le jour où le VPS disparaît : c'est-à-dire le seul jour où │
   │  elles servent. Trois copies, dans trois lieux, dont AUCUN n'est le  │
   │  VPS ni le conteneur qui contient les archives :                     │
   │                                                                      │
   │    1. IMPRIMÉE sur papier (une ligne, ~74 caractères), enveloppe     │
   │       scellée, à une autre adresse que le bureau.                    │
   │    2. Dans le gestionnaire de mots de passe (Bitwarden / 1Password), │
   │       en note sécurisée, compte protégé par 2FA.                     │
   │    3. Sur une clé USB chiffrée (LUKS / VeraCrypt), rangée ailleurs.  │
   │                                                                      │
   │  ET JAMAIS :                                                         │
   │    ✗ dans le dépôt git, même privé ;                                 │
   │    ✗ dans backend/.env ni dans /etc/uti-backup.env ;                 │
   │    ✗ dans le conteneur qui contient les archives qu'elle ouvre —     │
   │      une clé rangée à côté de son coffre n'est pas une clé ;         │
   │    ✗ protégée par une phrase de passe dont la copie papier dépend :  │
   │      une phrase oubliée à 3 h du matin vaut une clé perdue.          │
   │                                                                      │
   │  Puis SUPPRIMER le fichier de cette machine :                        │
   │      shred -u uti-backup.age-key                                     │
   └──────────────────────────────────────────────────────────────────────┘
AVERTISSEMENT

# ── 2. Conteneur verrouillé ─────────────────────────────────────────────────
etape "2/4 — Conteneur « $BUCKET » avec verrou d'objet (WORM)"
[ -n "$ENDPOINT" ] || { echo "BACKUP_S3_ENDPOINT non défini."; exit 1; }
if [ -z "${OWNER_S3_ACCESS_KEY:-}" ] || [ -z "${OWNER_S3_SECRET_KEY:-}" ]; then
  echo "OWNER_S3_ACCESS_KEY / OWNER_S3_SECRET_KEY requis (utilisateur PROPRIÉTAIRE)."
  echo "Ce sont des identifiants d'usage unique : ils ne doivent PAS finir dans"
  echo "/etc/uti-backup.env, sinon la couche 2 de la parade tombe."
  exit 1
fi

BACKUP_S3_ENDPOINT="$ENDPOINT" BACKUP_S3_REGION="$REGION" BACKUP_S3_BUCKET="$BUCKET" \
BACKUP_S3_ACCESS_KEY="$OWNER_S3_ACCESS_KEY" BACKUP_S3_SECRET_KEY="$OWNER_S3_SECRET_KEY" \
RETENTION_J="$RETENTION_J" EXPIRATION_J="$EXPIRATION_J" \
"$BACKEND/venv/bin/python" - <<'PY'
import os, sys, boto3
from botocore.exceptions import ClientError

bucket = os.environ["BACKUP_S3_BUCKET"]
c = boto3.client("s3",
                 endpoint_url=os.environ["BACKUP_S3_ENDPOINT"],
                 region_name=os.environ["BACKUP_S3_REGION"],
                 aws_access_key_id=os.environ["BACKUP_S3_ACCESS_KEY"],
                 aws_secret_access_key=os.environ["BACKUP_S3_SECRET_KEY"])

try:
    c.head_bucket(Bucket=bucket)
    existe = True
except ClientError:
    existe = False

if not existe:
    # ObjectLockEnabledForBucket ne peut PAS être ajouté après coup : si ce
    # drapeau manque à la création, il faut créer un AUTRE conteneur. On refuse
    # donc de créer un conteneur sans verrou « pour aller plus vite ».
    c.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True,
                    CreateBucketConfiguration={"LocationConstraint": os.environ["BACKUP_S3_REGION"]})
    print(f"   conteneur « {bucket} » créé avec verrou d'objet (donc versionné).")
else:
    print(f"   conteneur « {bucket} » déjà présent — non recréé.")
    try:
        conf = c.get_object_lock_configuration(Bucket=bucket)
        print(f"   verrou d'objet actif : {conf['ObjectLockConfiguration'].get('ObjectLockEnabled')}")
    except ClientError:
        sys.exit(
            f"   ⚠️  « {bucket} » N'A PAS de verrou d'objet, et il est IMPOSSIBLE de\n"
            f"       l'ajouter. Créer un conteneur neuf (ex. {bucket}-worm) et refaire\n"
            f"       cette étape. Sans verrou, une clé volée efface l'historique."
        )

# Rétention par défaut : s'applique à tout objet déposé, sans que le script de
# sauvegarde ait à demander quoi que ce soit — donc sans qu'il puisse l'oublier.
c.put_object_lock_configuration(
    Bucket=bucket,
    ObjectLockConfiguration={
        "ObjectLockEnabled": "Enabled",
        "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE",
                                      "Days": int(os.environ["RETENTION_J"])}},
    })
print(f"   rétention par défaut : COMPLIANCE {os.environ['RETENTION_J']} jours "
      "(ni le déposant, ni le propriétaire, ni OVH ne peut effacer avant).")

# Cycle de vie : c'est LUI qui fait le ménage, côté serveur. Volontairement pas
# le script de sauvegarde — un script capable d'effacer l'historique est un
# script qu'un attaquant peut détourner pour effacer l'historique.
jours = int(os.environ["EXPIRATION_J"])
c.put_bucket_lifecycle_configuration(
    Bucket=bucket,
    LifecycleConfiguration={"Rules": [{
        "ID": "purge-sauvegardes-uti",
        "Status": "Enabled",
        "Filter": {"Prefix": "uti/"},
        "Expiration": {"Days": jours},
        # Le versionnage étant actif (imposé par le verrou), l'expiration ci-dessus
        # ne pose qu'un marqueur de suppression : sans les deux règles suivantes,
        # les versions s'accumuleraient indéfiniment et la facture avec.
        "NoncurrentVersionExpiration": {"NoncurrentDays": jours},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 2},
    }]})
print(f"   cycle de vie : purge à {jours} jours (> {os.environ['RETENTION_J']} j de verrou, "
      "sinon rien ne serait jamais supprimé).")
PY

# ── 3. Utilisateur déposant ─────────────────────────────────────────────────
etape "3/4 — Utilisateur S3 déposant (geste MANUEL dans l'espace client OVH)"
cat <<MANUEL
   a) Espace client OVH → Public Cloud → Object Storage → onglet « Utilisateurs »
      → « Ajouter un utilisateur » → nom : uti-backup-writer
      ⚠️ Ce doit être un utilisateur DIFFÉRENT de celui qui vient de créer le
         conteneur. Sur un PROPRIÉTAIRE, une politique restrictive ne s'applique
         pas (ACL FULL_CONTROL implicite) : la restriction serait décorative.
   b) Sélectionner uti-backup-writer → « Importer une politique JSON » →
      charger : $ICI/backup_s3_policy.json
      (remplacer d'abord NOM_DU_CONTENEUR par « $BUCKET » dans ce fichier)
   c) Reporter sa clé et son secret dans /etc/uti-backup.env :
        sudo install -m 600 -o root -g root /dev/null /etc/uti-backup.env
        sudo tee /etc/uti-backup.env >/dev/null <<'ENV'
      BACKUP_S3_ENDPOINT=$ENDPOINT
      BACKUP_S3_REGION=$REGION
      BACKUP_S3_BUCKET=$BUCKET
      BACKUP_S3_ACCESS_KEY=<clé de uti-backup-writer>
      BACKUP_S3_SECRET_KEY=<secret de uti-backup-writer>
      HEALTHCHECK_URL=<URL de ping de la sonde « sauvegarde »>
      ENV
MANUEL

# ── 4. Vérification que la restriction MORD vraiment ────────────────────────
etape "4/4 — Preuve que la clé du VPS ne peut PAS effacer"
cat <<'PREUVE'
   Une politique qu'on n'a pas essayé de violer n'est qu'une intention. Une fois
   /etc/uti-backup.env rempli, exécuter ceci sur le VPS :

     set -a; . /etc/uti-backup.env; set +a
     ~/app/backend/venv/bin/python - <<'PY'
     import os, boto3
     from botocore.exceptions import ClientError
     c = boto3.client("s3", endpoint_url=os.environ["BACKUP_S3_ENDPOINT"],
                      region_name=os.environ["BACKUP_S3_REGION"],
                      aws_access_key_id=os.environ["BACKUP_S3_ACCESS_KEY"],
                      aws_secret_access_key=os.environ["BACKUP_S3_SECRET_KEY"])
     b = os.environ["BACKUP_S3_BUCKET"]
     c.put_object(Bucket=b, Key="uti/_essai_suppression", Body=b"x")
     print("dépôt : OK (attendu)")
     try:
         c.delete_object(Bucket=b, Key="uti/_essai_suppression")
         print("❌ SUPPRESSION ACCEPTÉE — la politique ne s'applique pas.")
         print("   Cause la plus probable : cet utilisateur EST le propriétaire")
         print("   du conteneur. Recréer un utilisateur distinct.")
         raise SystemExit(1)
     except ClientError as e:
         print(f"✅ suppression REFUSÉE ({e.response['Error']['Code']}) — c'est le but.")
     PY

   Tant que ce contrôle n'a pas affiché « suppression REFUSÉE », le hors-site
   n'est pas en place : il est seulement ailleurs.
PREUVE
