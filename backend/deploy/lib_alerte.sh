#!/usr/bin/env bash
# =============================================================================
#  Bibliothèque commune aux scripts de sauvegarde / répétition / supervision.
#
#  POURQUOI CE FICHIER EXISTE
#  backup_db.sh portait sa propre fonction alerte(). restore_drill.sh et
#  supervision.sh en auraient chacun recopié une. Trois copies d'un canal
#  d'alerte, c'est trois occasions qu'une seule cesse de marcher — et on ne s'en
#  aperçoit que le jour où c'est ELLE qu'on attendait. Même raisonnement que
#  tests/test_storage_acl.py:59 sur la règle d'ACL : une seule définition.
#
#  Utilisation :
#      source "$(dirname "${BASH_SOURCE[0]}")/lib_alerte.sh"
#      crie "sauvegarde" "pg_dump a échoué"     # e-mail + journal + exit 1
#      previens "sauvegarde" "dump un peu petit" # e-mail + journal, continue
#
#  Variables lues (toutes surchargeables par l'unité systemd) :
#      BACKEND_DIR      chemin du backend (défaut /home/julian.talou/app/backend)
#      HEALTHCHECK_URL  URL de ping du chien de garde externe (facultatif)
# =============================================================================

BACKEND="${BACKEND_DIR:-/home/julian.talou/app/backend}"

# ── Chien de garde externe (« dead man's switch ») ──────────────────────────
# POURQUOI : les fonctions ci-dessous ne partent que si le VPS est vivant pour
# les exécuter. Le jour où le VPS entier est mort — le seul jour qui compte —
# aucune alerte locale ne peut partir, par construction. Un service tiers qui
# ATTEND un signal régulier détecte ce silence : c'est la seule façon d'être
# prévenu d'une panne totale sans posséder une seconde machine.
# healthchecks.io suffit (offre gratuite, 20 sondes) et ne reçoit qu'un UUID :
# aucune donnée de la plateforme ne transite par lui.
ping_garde() {
  # $1 = "" (succès) | "/start" | "/fail" | "/<code>"
  local suffixe="${1:-}"
  [ -n "${HEALTHCHECK_URL:-}" ] || return 0
  # --retry 3 : un ping perdu sur un hoquet réseau déclencherait une fausse
  # alerte de « sauvegarde silencieuse » — exactement le bruit qui apprend à
  # ignorer les alertes.
  curl -fsS -m 10 --retry 3 --retry-delay 2 -o /dev/null \
    "${HEALTHCHECK_URL%/}${suffixe}" 2>/dev/null || true
}

# ── E-mail direct par SMTP ──────────────────────────────────────────────────
# POURQUOI PAS LA FILE : services/email_outbox stocke les messages EN BASE. Or
# ce qu'on annonce ici, c'est justement une panne de la base ou de sa
# sauvegarde. Un canal de secours qui dépend de ce qu'il surveille ne prévient
# jamais. On réutilise services/email.py:send_email (envoi synchrone assumé) et
# render_email_html pour que l'alerte ressemble aux autres e-mails de la
# plateforme — un e-mail qui n'a pas l'air officiel se fait ignorer.
courriel() {
  local sujet="$1" corps="$2"
  "$BACKEND/venv/bin/python" - "$sujet" "$corps" <<'PY' \
    || echo "[ALERTE] l'e-mail n'est pas parti non plus — SMTP en panne ?" >&2
import os, sys, socket, html
sys.path.insert(0, os.environ.get("BACKEND_DIR", "/home/julian.talou/app/backend"))
os.chdir(sys.path[0])                       # config.py lit ./.env
from config import settings
from services.email import send_email, render_email_html

sujet, corps = sys.argv[1], sys.argv[2]
dest = settings.admin_email or settings.smtp_user
if not dest:
    sys.exit("ni ADMIN_EMAIL ni SMTP_USER : personne à prévenir")

html_corps = render_email_html(
    title=sujet,
    body_html=(
        f"<p>Machine : <strong>{html.escape(socket.gethostname())}</strong></p>"
        f"<pre style='background:#f6f6f9;padding:12px;border-radius:8px;"
        f"white-space:pre-wrap;font-size:13px'>{html.escape(corps)}</pre>"
        "<p>Diagnostic :<br>"
        "<code>ssh -p 1622 julian.talou@164.132.44.212</code><br>"
        "<code>journalctl -u uti-backup -u uti-restore-drill -u uti-supervision -n 80 --no-pager</code></p>"
    ),
    footer_note="Alerte automatique — RUNBOOK.md §9 à §11.",
)
ok, err = send_email(dest, sujet, html_corps, text=corps)
if not ok:
    sys.exit(f"envoi refusé : {err}")
PY
}

journal() { printf '[%s] %s\n' "$1" "$2"; }

# Panne : on prévient et on s'arrête. Le code de sortie ≠ 0 fait passer l'unité
# systemd en « failed », ce que supervision.sh sait relire.
crie() {
  local domaine="$1" motif="$2"
  journal "$domaine" "ÉCHEC : $motif" >&2
  ping_garde "/fail"
  courriel "🚨 UTI — $domaine en échec" "$motif"
  exit 1
}

# Dégradation : on prévient mais on continue. Sert aux cas « ça a marché, mais
# quelque chose mérite un regard » — les traiter comme des pannes ferait rater
# la vraie panne le jour venu.
previens() {
  local domaine="$1" motif="$2"
  journal "$domaine" "ATTENTION : $motif" >&2
  courriel "⚠️ UTI — $domaine : anomalie" "$motif"
}
