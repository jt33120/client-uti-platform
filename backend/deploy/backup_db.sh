#!/usr/bin/env bash
# =============================================================================
#  Sauvegarde quotidienne de la base « uti » du VPS.
#
#  POURQUOI CE FICHIER EXISTE
#  Tant que la base vivait chez Supabase, quelqu'un d'autre la sauvegardait.
#  À partir de la bascule, personne ne le fait. Une base de production sans
#  sauvegarde éprouvée n'autorise pas la suppression du filet de sécurité qu'est
#  encore le projet Supabase : c'est le premier des critères de suppression.
#
#  CE QUE ÇA FAIT
#   1. pg_dump au format custom (rejouable table par table) ;
#   2. conservation glissante : 14 quotidiennes + 8 hebdomadaires (dimanche) ;
#   3. en cas d'ÉCHEC, un e-mail part immédiatement aux administrateurs via le
#      SMTP du backend — pas via la base, qui est justement ce qui est en panne.
#
#  Installation : voir backend/deploy/uti-backup.service et uti-backup.timer.
#      sudo install -m 750 -o julian.talou -g julian.talou \
#           ~/app/backend/deploy/backup_db.sh /usr/local/bin/uti-backup
#      sudo install -d -m 750 -o julian.talou -g julian.talou /var/backups/uti
# =============================================================================
set -uo pipefail

DEST="${BACKUP_DIR:-/var/backups/uti}"
BASE="${PGDATABASE:-uti}"
BACKEND="${BACKEND_DIR:-/home/julian.talou/app/backend}"
JOUR=$(date +%F)
FICHIER="$DEST/uti-$JOUR.pgcustom"

alerte() {
  # L'alerte passe par SMTP et non par la file d'e-mails (services/email_outbox) :
  # la file vit EN BASE, or c'est la base qui vient d'échouer. Un canal de secours
  # qui dépend de ce qu'il surveille ne prévient jamais.
  local motif="$1"
  echo "[BACKUP] ÉCHEC : $motif" >&2
  "$BACKEND/venv/bin/python" - "$motif" <<'PY' || echo "[BACKUP] alerte e-mail non partie non plus" >&2
import sys, os, socket
sys.path.insert(0, os.environ.get("BACKEND_DIR", "/home/julian.talou/app/backend"))
os.chdir(sys.path[0])                       # config.py lit ./.env
from config import settings
from services.email import send_email
motif = sys.argv[1]
dest = settings.admin_email or settings.smtp_user
if dest:
    send_email(dest, "🚨 Sauvegarde de la base UTI en échec",
               f"<p>La sauvegarde quotidienne a échoué sur <strong>{socket.gethostname()}</strong>.</p>"
               f"<p>Motif : <code>{motif}</code></p>"
               "<p>Diagnostic : <code>journalctl -u uti-backup -n 50</code></p>")
PY
  exit 1
}

mkdir -p "$DEST" || alerte "répertoire $DEST inaccessible"

# Refuser de sauvegarder s'il ne reste presque rien : un dump tronqué par un
# disque plein est pire qu'une absence de dump, parce qu'il rassure.
libre_ko=$(df -Pk "$DEST" | awk 'NR==2 {print $4}')
[ "$libre_ko" -gt 1048576 ] || alerte "moins de 1 Go libre sur $DEST ($((libre_ko/1024)) Mo)"

pg_dump -d "$BASE" --no-owner --no-privileges -Fc -f "$FICHIER.partiel" \
  || alerte "pg_dump a échoué"

# Renommage atomique : un fichier au nom définitif est un fichier complet.
mv "$FICHIER.partiel" "$FICHIER"
chmod 600 "$FICHIER"

taille=$(stat -c%s "$FICHIER")
# La base fait 16 Mo de données ; un dump compressé sous 50 Ko signifie
# « schéma sans données », donc une catastrophe silencieuse.
[ "$taille" -gt 51200 ] || alerte "dump anormalement petit ($taille octets)"

# Contrôle de lisibilité : pg_restore --list échoue sur une archive corrompue.
# Sans ce contrôle, on découvrirait le problème le jour de la restauration.
pg_restore --list "$FICHIER" > /dev/null || alerte "l'archive produite est illisible"

# ── Rotation ────────────────────────────────────────────────────────────────
# 14 quotidiennes : couvre deux semaines, la durée de la période d'observation.
find "$DEST" -name 'uti-*.pgcustom' -mtime +14 ! -name 'uti-hebdo-*' -delete
# Une hebdomadaire le dimanche, conservée 8 semaines : rattrape une corruption
# découverte tardivement, que 14 jours ne couvriraient pas.
if [ "$(date +%u)" = "7" ]; then
  cp -a "$FICHIER" "$DEST/uti-hebdo-$JOUR.pgcustom"
  find "$DEST" -name 'uti-hebdo-*.pgcustom' -mtime +56 -delete
fi

echo "[BACKUP] OK $FICHIER ($((taille/1024)) Ko) — $(ls -1 "$DEST"/uti-*.pgcustom | wc -l) archive(s) conservée(s)"
