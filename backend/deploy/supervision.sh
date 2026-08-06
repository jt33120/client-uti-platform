#!/usr/bin/env bash
# =============================================================================
#  Supervision minimale du VPS — les quatre choses dont la panne est invisible.
#
#  POURQUOI CE FICHIER EXISTE
#  post_bascule_check.sh (backend/scripts/) est un contrôle qu'on LANCE : il
#  prouve un état à un instant choisi, pendant la période d'observation. Ce
#  script-ci est un contrôle qui TOURNE : il parle quand personne ne regarde,
#  c'est-à-dire tout le temps. Les deux sont nécessaires et ne se remplacent pas.
#
#  CE QU'IL SURVEILLE, ET POURQUOI CES QUATRE-LÀ
#   1. ESPACE DISQUE — une base qui remplit le disque ne « ralentit » pas : elle
#      corrompt le VPS entier. PostgreSQL passe en lecture seule, journald ne
#      peut plus écrire, et le script de sauvegarde ne peut plus prévenir. C'est
#      la panne dont on se remet le moins bien, donc la première à voir venir.
#   2. PostgreSQL VIVANT — tout le reste en dépend.
#   3. PostgREST VIVANT — l'application parle à la base à travers lui
#      (nginx-postgrest.conf) : PostgreSQL debout et PostgREST mort, c'est une
#      panne totale que `systemctl status postgresql` déclare verte.
#   4. ÂGE DE LA DERNIÈRE SAUVEGARDE RÉUSSIE — la seule métrique qui dise si le
#      filet est encore là. On lit .dernier_succes, écrit par backup_db.sh à la
#      toute fin : la date d'un fichier .pgcustom ne prouve rien, une exécution
#      interrompue en laisse un tout frais.
#
#  Il n'invente aucune sonde : /health et /health/db existent (main.py:170 et
#  :193), et le contrôle PostgREST reprend celui de post_bascule_check.sh:41.
#
#  USAGE
#      bash ~/app/backend/deploy/supervision.sh     # ou uti-supervision.timer
#      echo $?     # 0 = tout vert, 1 = au moins une anomalie
# =============================================================================
set -uo pipefail

DEST="${BACKUP_DIR:-/var/backups/uti}"
BACKEND="${BACKEND_DIR:-/home/julian.talou/app/backend}"
API="${API_URL:-http://127.0.0.1:8000}"
PGRST="${PGRST_URL:-http://127.0.0.1:8080}"
BASE="${PGDATABASE:-uti}"
ETAT="${ETAT_DIR:-/var/lib/uti-supervision}"

# Seuils. Écrits ici, en clair, plutôt que noyés dans les tests : ce sont les
# seuls chiffres que Julian aura envie de changer.
DISQUE_PCT_MAX="${DISQUE_PCT_MAX:-85}"   # % d'occupation au-delà duquel on crie
SAUVEGARDE_AGE_MAX="${SAUVEGARDE_AGE_MAX:-180}"   # minutes (horaire + marge)
REPETITION_AGE_MAX="${REPETITION_AGE_MAX:-14400}" # minutes = 10 jours (hebdo + marge)
RAPPEL_MIN="${RAPPEL_MIN:-360}"          # ne pas ré-alerter avant 6 h sur le même motif

# shellcheck source=./lib_alerte.sh disable=SC1091
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib_alerte.sh" 2>/dev/null \
  || . /usr/local/lib/uti-lib_alerte.sh

mkdir -p "$ETAT"
ROUGE=0

# ── Anti-répétition ─────────────────────────────────────────────────────────
# Toutes les 15 minutes, une anomalie qui dure produirait 96 e-mails par jour.
# Au bout de deux jours, ces e-mails sont filtrés en « Autres » et l'alerte
# suivante — la vraie — ne sera pas lue. On ne renvoie donc qu'une fois par
# RAPPEL_MIN, et on annonce le RETOUR À LA NORMALE : sans ça, on ne sait jamais
# si le silence veut dire « réparé » ou « la supervision est morte aussi ».
signaler() {
  local motif="$1" texte="$2"
  local marqueur="$ETAT/$motif"
  ROUGE=$((ROUGE+1))
  printf '  \033[31m✗\033[0m %s\n' "$texte"
  if [ -f "$marqueur" ] && [ -z "$(find "$marqueur" -mmin +"$RAPPEL_MIN")" ]; then
    return   # déjà signalé récemment : on journalise, on n'inonde pas
  fi
  date -uIs > "$marqueur"
  courriel "🚨 UTI — $motif" "$texte"
  ping_garde "/fail"
}

resoudre() {
  local motif="$1" texte="$2"
  printf '  \033[32m✓\033[0m %s\n' "$texte"
  if [ -f "$ETAT/$motif" ]; then
    rm -f "$ETAT/$motif"
    courriel "✅ UTI — $motif : retour à la normale" "$texte"
  fi
}

# ── 1. Espace disque ────────────────────────────────────────────────────────
for point in / "$DEST"; do
  [ -d "$point" ] || continue
  pct=$(df -P "$point" | awk 'NR==2 {gsub("%","",$5); print $5}')
  libre=$(df -Ph "$point" | awk 'NR==2 {print $4}')
  cle="disque$(echo "$point" | tr '/' '_')"
  if [ "${pct:-0}" -ge "$DISQUE_PCT_MAX" ]; then
    signaler "$cle" "$point occupé à $pct % ($libre libres). Une base qui remplit le disque
corrompt le VPS entier. À regarder : /var/backups/uti (rotation),
journalctl --vacuum-size=200M, et les bases jetables uti_drill_* oubliées :
  psql -d postgres -c \"SELECT datname, pg_size_pretty(pg_database_size(datname)) FROM pg_database ORDER BY 2 DESC;\""
  else
    resoudre "$cle" "$point : $pct % occupé, $libre libres"
  fi
done

# ── 2. PostgreSQL ───────────────────────────────────────────────────────────
if pg_isready -q -d "$BASE" 2>/dev/null; then
  resoudre "postgresql" "PostgreSQL répond ($BASE)"
else
  signaler "postgresql" "PostgreSQL ne répond pas sur la base « $BASE ».
  sudo systemctl status postgresql ; sudo journalctl -u postgresql -n 50 --no-pager"
fi

# ── 3. PostgREST ────────────────────────────────────────────────────────────
# 401 sans jeton est le comportement CORRECT (deploy/install_db.sh ne définit pas
# db-anon-role) : c'est donc 401 qu'on attend, pas 200. Un 200 signifierait que
# la base est ouverte sans authentification — pire qu'une panne.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$PGRST/rest/v1/profiles" 2>/dev/null)
case "$code" in
  401) resoudre "postgrest" "PostgREST répond 401 sans jeton (attendu)" ;;
  200) signaler "postgrest" "PostgREST répond 200 SANS JETON : la base est lisible sans authentification.
Vérifier db-anon-role dans /etc/postgrest/postgrest.conf — à couper MAINTENANT." ;;
  *)   signaler "postgrest" "PostgREST répond « $code » (attendu 401) — service mort ou façade nginx cassée.
  sudo systemctl status postgrest nginx" ;;
esac

# ── 4. Backend applicatif ───────────────────────────────────────────────────
if curl -sf --max-time 5 "$API/health" | grep -q '"status":"ok"'; then
  resoudre "backend" "/health répond ok"
else
  signaler "backend" "/health ne répond pas — sudo systemctl status uti-backend"
fi

# /health/db est la seule sonde qui distingue « le backend tourne » de « le
# backend voit ses données » (main.py:193). Elle rend 503, pas une exception :
# on lit donc le code HTTP, pas le corps.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$API/health/db" 2>/dev/null)
if [ "$code" = "200" ]; then
  resoudre "backend_db" "/health/db : la base est joignable depuis l'application"
else
  signaler "backend_db" "/health/db répond $code : le backend tourne mais ne voit plus ses données.
  curl -s $API/health/db     # le détail est dans la réponse"
fi

# ── 5. Âge de la dernière sauvegarde RÉUSSIE ────────────────────────────────
if [ -f "$DEST/.dernier_succes" ]; then
  age_min=$(( ( $(date -u +%s) - $(stat -c%Y "$DEST/.dernier_succes") ) / 60 ))
  if [ "$age_min" -le "$SAUVEGARDE_AGE_MAX" ]; then
    resoudre "sauvegarde_age" "dernière sauvegarde réussie il y a $age_min min — $(cat "$DEST/.dernier_succes")"
  else
    signaler "sauvegarde_age" "aucune sauvegarde RÉUSSIE depuis $((age_min/60)) h $((age_min%60)) min.
Dernière trace : $(cat "$DEST/.dernier_succes")
  systemctl list-timers uti-backup.timer ; journalctl -u uti-backup -n 50 --no-pager"
  fi
else
  signaler "sauvegarde_age" "$DEST/.dernier_succes n'existe pas : AUCUNE sauvegarde n'a jamais réussi
depuis l'installation de ce contrôle. systemctl start uti-backup"
fi

# ── 6. Âge de la dernière RÉPÉTITION de restauration ────────────────────────
# Une sauvegarde qu'on n'a pas rejouée depuis dix jours est redevenue une
# croyance. C'est ce contrôle qui empêche le dispositif de se dégrader en
# silence après la mise en place.
if [ -f "$DEST/.derniere_repetition" ]; then
  age_min=$(( ( $(date -u +%s) - $(stat -c%Y "$DEST/.derniere_repetition") ) / 60 ))
  if [ "$age_min" -le "$REPETITION_AGE_MAX" ]; then
    resoudre "repetition_age" "dernière répétition de restauration il y a $((age_min/1440)) j — $(cat "$DEST/.derniere_repetition")"
  else
    signaler "repetition_age" "aucune répétition de restauration RÉUSSIE depuis $((age_min/1440)) jours.
  systemctl list-timers uti-restore-drill.timer ; journalctl -u uti-restore-drill -n 50 --no-pager"
  fi
else
  signaler "repetition_age" "aucune répétition de restauration n'a jamais réussi.
  bash $BACKEND/deploy/restore_drill.sh"
fi

# ── 7. Bases jetables orphelines ────────────────────────────────────────────
# Le trap de restore_drill.sh les supprime, mais un « kill -9 » ou un reboot en
# plein milieu le contourne. Une base orpheline de 16 Mo n'est rien ; trente le
# deviennent, et on retombe sur le point 1.
orphelines=$(psql -d postgres -tAc \
  "SELECT count(*) FROM pg_database WHERE datname LIKE 'uti_drill_%';" 2>/dev/null || echo 0)
if [ "${orphelines:-0}" -gt 2 ]; then
  signaler "bases_orphelines" "$orphelines bases « uti_drill_% » traînent : une répétition a été
interrompue sans exécuter son ménage. Les lister puis les supprimer :
  psql -d postgres -c \"SELECT datname FROM pg_database WHERE datname LIKE 'uti_drill_%';\""
else
  resoudre "bases_orphelines" "aucune base de répétition orpheline"
fi

if [ "$ROUGE" -eq 0 ]; then
  # Le ping n'est envoyé QUE si tout est vert : le chien de garde externe
  # devient ainsi la sonde de dernier recours. Il aboie sur trois cas d'un coup
  # — VPS mort, supervision morte, anomalie détectée — sans rien savoir d'eux.
  ping_garde ""
  printf '\033[32m✅ %s : supervision verte.\033[0m\n' "$(date -Is)"
  exit 0
fi
printf '\033[31m❌ %s : %d anomalie(s).\033[0m\n' "$(date -Is)" "$ROUGE"
exit 1
