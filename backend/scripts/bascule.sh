#!/usr/bin/env bash
# =============================================================================
#  BASCULE — Supabase → PostgreSQL du VPS, en douze étapes vérifiées.
#
#  POURQUOI CE SCRIPT EXISTE
#
#  Tout ce qu'il enchaîne existait déjà, éparpillé dans six fichiers et deux
#  documents. Ce qui manquait n'était pas un outil : c'était L'ORDRE, et la
#  PREUVE entre deux gestes. Le chantier s'est arrêté en août parce que personne
#  ne pouvait dire « l'étape précédente a réussi » autrement qu'en le croyant.
#
#  CE QUE LA PREMIÈRE VERSION DE CE FICHIER A APPRIS
#
#  Elle a été attaquée sous six angles avant d'avoir jamais tourné. Vingt
#  défauts, dont celui-ci : l'étape 0 — les huit contrôles préalables — n'était
#  JAMAIS exécutée, parce que `[ 0 -lt 1 ]` est vrai et que la valeur par défaut
#  de --depuis était 1. Le script annonçait « sautée » à chaque lancement et
#  personne ne l'aurait lu. Les autres défauts sont nommés à l'endroit où ils
#  ont été corrigés, pour qu'on ne les réintroduise pas.
#
#  TROIS RÈGLES TENUES PARTOUT
#
#   1. AUCUN GESTE DESTRUCTEUR SANS VÉRIFIER SON CODE DE RETOUR. Le script
#      tourne sous `set -uo pipefail` sans -e (on veut nos propres messages) :
#      chaque geste qui compte est donc suivi de `|| mort`.
#   2. UNE FENÊTRE DE MAINTENANCE RÉELLE. Le backend est ARRÊTÉ pendant que la
#      base est copiée puis promue. Sans cela, tout ce qu'un utilisateur écrit
#      entre l'export et le redémarrage part dans Supabase et n'arrive jamais
#      dans la base neuve : une perte silencieuse, invisible à toute sonde.
#   3. TOUT EST RÉVERSIBLE TANT QUE SUPABASE EXISTE. Le .env est sauvegardé sous
#      un nom FIXE qu'on n'écrase jamais, l'ancienne base est conservée, et
#      Supabase n'est jamais modifié autrement que par la réécriture d'URLs de
#      l'étape 4 — dont l'archive de l'étape 2 garde l'état d'avant.
#
#  L'ORDRE N'EST PAS ARBITRAIRE :
#
#   * La copie des fichiers et la réécriture des URLs ont lieu TANT QUE .env
#     DÉSIGNE ENCORE SUPABASE. Lancées après, elles réécriraient la base neuve
#     et les 38 objets deviendraient introuvables (migrate_storage_to_ovh.py).
#   * L'export qui SERT À CHARGER le VPS est pris APRÈS la réécriture ET APRÈS
#     la fermeture de la fenêtre, sinon la base neuve naît avec des URLs mortes
#     ou avec des lignes manquantes.
#   * Les GRANT de roles_postgrest.sql ne portent que sur les tables existant au
#     moment où il tourne (§4 du fichier). Un rechargement crée des tables
#     neuves : il faut le rejouer, sinon service_role n'a aucun droit et l'API
#     répond 403 sur tout.
#
#  USAGE
#      bash ~/app/backend/scripts/bascule.sh --dry-run      # ne modifie RIEN
#      bash ~/app/backend/scripts/bascule.sh                # pour de vrai
#      bash ~/app/backend/scripts/bascule.sh --depuis 7     # reprend à l'étape 7
#      bash ~/app/backend/scripts/bascule.sh --rollback     # revient à Supabase
#
#  PRÉREQUIS (l'étape 0 les vérifie et refuse sinon) :
#      install -m 600 /dev/null ~/.supabase_db_uri
#      nano ~/.supabase_db_uri     # Console Supabase → Settings → Database
#                                  # → Connection string → URI (mode Session)
# =============================================================================
set -uo pipefail

BACKEND="${BACKEND_DIR:-$HOME/app/backend}"
VENV="$BACKEND/venv/bin/python"
ETAT="${ETAT_FILE:-$HOME/.bascule_etat}"
ARCHIVES="${ARCHIVES_DIR:-$HOME/archive-supabase}"
URI_FILE="${SUPABASE_URI_FILE:-$HOME/.supabase_db_uri}"
BASE="${PGDATABASE:-uti}"
PROPRIO="${DB_OWNER:-uti_admin}"
FICHIERS="${FILES_DIR:-/var/lib/uti/files}"
API="${API_URL:-http://127.0.0.1:8000}"
BASE_PUBLIQUE="${PUBLIC_BASE_URL:-https://vps-cc93f2a8.vps.ovh.net}"
#: Nom FIXE, jamais horodaté, jamais écrasé. La version horodatée était un piège :
#: l'étape 9 pouvant échouer APRÈS avoir modifié .env, un second passage
#: sauvegardait un .env DÉJÀ basculé — et --rollback restaurait alors l'état
#: qu'il était censé défaire.
ENV_SAUVE="$BACKEND/.env.avant-bascule"

DRY=0; DEPUIS=0; ROLLBACK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)  DRY=1 ;;
    # Pas d'apostrophe dans ce message : bash ouvre une chaîne sur un « ' »
    # même à l'intérieur de "${...:?}", et le fichier entier cesse d'analyser.
    --depuis)   DEPUIS="${2:?--depuis exige un numero d etape}"; shift ;;
    --rollback) ROLLBACK=1 ;;
    *) echo "Option inconnue : $1"; exit 2 ;;
  esac
  shift
done

g()    { printf '\n\033[1m\033[36m══ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
ko()   { printf '  \033[31m✗\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
mort() { printf '\n\033[31m\033[1mARRET etape %s : %s\033[0m\n' "$1" "$2"
         printf 'Rien de plus loin. Corrige, puis : bash %s --depuis %s\n' "$0" "$1"
         printf 'Pour revenir a Supabase : bash %s --rollback\n' "$0"
         exit 1; }

# `faire` REND MAINTENANT SON CODE DE RETOUR. La version précédente l'avalait :
# un « alter database » refusé parce qu'une session était ouverte ne produisait
# qu'une ligne sur stderr, et le script continuait comme si de rien n'était.
faire() {
  if [ "$DRY" = 1 ]; then printf '    \033[33m[simulation]\033[0m %s\n' "$*"; return 0; fi
  eval "$@"
}

# Certaines commandes sont EN LECTURE SEULE et doivent tourner même en
# simulation : c'est tout l'intérêt d'une simulation. Un --dry-run qui se
# contente d'imprimer les commandes ne prouve rien qu'une lecture du script ne
# prouverait — et surtout, il n'attrape ni un bucket illisible, ni une URI
# Supabase refusée, ni un comptage de tables qui ne tombe pas juste.
lire() { eval "$@"; }
fait()  { [ "$DRY" = 1 ] || echo "$1" >> "$ETAT"; }
deja()  { [ -f "$ETAT" ] && grep -qx "$1" "$ETAT"; }

# `--depuis N` EFFACE l'état des étapes ≥ N. Sans cela, `deja` les sautait quand
# même : --depuis ne servait qu'aux étapes jamais terminées, donc à rien.
if [ "$DEPUIS" -gt 0 ] && [ "$DRY" = 0 ] && [ -f "$ETAT" ]; then
  for k in $(seq "$DEPUIS" 11); do sed -i "/^etape$k\$/d" "$ETAT"; done
  info "état des étapes ≥ $DEPUIS effacé pour permettre le rejeu"
fi

psql_su() { sudo -u postgres psql -v ON_ERROR_STOP=1 -q "$@"; }

# L'URI Supabase porte un mot de passe. Le passer en ARGUMENT à psql l'inscrirait
# dans /proc/<pid>/cmdline, donc dans la sortie de `ps`, lisible par TOUT compte
# de la machine — y compris `postgrest`, `www-data` et un éventuel intrus sans
# privilèges. L'en-tête de export_supabase_archive.sh énonce d'ailleurs la règle
# (« L'URI ne passe JAMAIS en argument ») ; son code ne la tient que pour les
# arguments DU SCRIPT, pas pour ceux de psql.
#
# On découpe donc l'URI en variables libpq. L'environnement d'un processus n'est
# lisible que par son propriétaire et par root (/proc/<pid>/environ, mode 0600),
# jamais par `ps`.
_uri_vers_env() {
  local uri; uri="$(tr -d "\r\n" < "$URI_FILE")"
  # postgresql://UTILISATEUR:MOTDEPASSE@HOTE:PORT/BASE[?options]
  local reste="${uri#*://}"
  local creds="${reste%%@*}" hostpart="${reste#*@}"
  PGUSER_S="${creds%%:*}"
  PGPASSWORD_S="${creds#*:}"
  local hostport="${hostpart%%/*}" dbpart="${hostpart#*/}"
  PGHOST_S="${hostport%%:*}"
  PGPORT_S="${hostport##*:}"; [ "$PGPORT_S" = "$PGHOST_S" ] && PGPORT_S=5432
  PGDATABASE_S="${dbpart%%\?*}"
  [ -n "$PGHOST_S" ] && [ -n "$PGDATABASE_S" ]
}

psql_supabase() {
  _uri_vers_env || return 1
  PGHOST="$PGHOST_S" PGPORT="$PGPORT_S" PGUSER="$PGUSER_S" \
  PGPASSWORD="$PGPASSWORD_S" PGDATABASE="$PGDATABASE_S" \
  PGSSLMODE=require psql "$@" 2>/dev/null
}

# ── Retour arrière ──────────────────────────────────────────────────────────
if [ "$ROLLBACK" = 1 ]; then
  g "RETOUR ARRIÈRE vers Supabase"
  [ -r "$ENV_SAUVE" ] || { ko "sauvegarde introuvable : $ENV_SAUVE"; exit 1; }
  grep -qi 'supabase\.co' "$ENV_SAUVE" \
    || { ko "$ENV_SAUVE ne désigne pas Supabase — refus de restaurer un .env déjà basculé"; exit 1; }

  cp "$ENV_SAUVE" "$BACKEND/.env" || { ko "restauration du .env impossible"; exit 1; }
  ok ".env restauré depuis $ENV_SAUVE"

  # Remettre les bases dans leur état d'avant, si la promotion a eu lieu.
  if sudo -u postgres psql -tAc "select 1 from pg_database where datname='uti_avant_bascule'" | grep -q 1; then
    sudo systemctl stop postgrest
    psql_su -c "alter database $BASE rename to uti_verif" 2>/dev/null
    psql_su -c "alter database uti_avant_bascule rename to $BASE" \
      && ok "base « uti » d'origine remise en place" \
      || ko "renommage inverse impossible — inspecter : sudo -u postgres psql -l"
    sudo systemctl start postgrest
  fi

  sudo systemctl start uti-backup.timer 2>/dev/null
  sudo systemctl restart uti-backend
  for _ in $(seq 1 10); do
    sleep 2
    curl -sf --max-time 5 "$API/health/db" >/dev/null && break
  done
  curl -sf --max-time 5 "$API/health/db" >/dev/null \
    && ok "backend revenu sur Supabase, base joignable" \
    || ko "la base ne répond toujours pas — journalctl -u uti-backend -n 100"

  # L'état DOIT être remis à zéro : sinon la relance suivante saute toutes les
  # étapes « déjà faites » et annonce TERMINÉ sans rien faire.
  rm -f "$ETAT" "$HOME/.bascule_archive_avant" "$HOME/.bascule_archive_apres"
  ok "état de bascule remis à zéro"
  info "Les fichiers copiés sur $FICHIERS restent en place (sans effet en mode supabase)."
  info "⚠️  Les URLs réécrites à l'étape 4 restent réécrites. SANS EFFET sur les"
  info "    buckets privés (chemin nu, lu tel quel par storage._object_path), mais"
  info "    les AVATARS pointeront vers le VPS tant que STORAGE_BACKEND ≠ local."
  info "    Pour les remettre : archive de l'étape 2, colonne profiles.avatar_url."
  exit 0
fi

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  BASCULE Supabase → VPS                                          ║"
[ "$DRY" = 1 ] \
&& echo "║  MODE SIMULATION — rien ne sera modifié                           ║" \
|| echo "║  MODE RÉEL — le service sera INTERROMPU aux étapes 5 à 10         ║"
echo "╚══════════════════════════════════════════════════════════════════╝"

etape() {  # etape <n> <titre> ; renvoie 1 si on doit la sauter
  local n="$1" t="$2"
  if [ "$n" -lt "$DEPUIS" ]; then printf '\n\033[2m── %s. %s — sautée (--depuis %s)\033[0m\n' "$n" "$t" "$DEPUIS"; return 1; fi
  if deja "etape$n"; then printf '\n\033[2m── %s. %s — déjà faite\033[0m\n' "$n" "$t"; return 1; fi
  g "$n. $t"; return 0
}

# ═══ 0. Préalables ══════════════════════════════════════════════════════════
# NON SAUTABLE. C'est le défaut le plus grave de la version précédente : cette
# étape ne s'exécutait jamais, donc le garde-fou « la bascule est-elle déjà
# faite ? » ne se déclenchait pas non plus. Elle ignore --depuis et l'état.
g "0. Préalables"
MANQUE=0
for outil in psql pg_dump curl tar; do
  command -v "$outil" >/dev/null && ok "$outil présent" || { ko "$outil ABSENT"; MANQUE=1; }
done
# L'interpréteur du VENV, pas python3 : les dépendances (supabase-py, boto3…)
# ne sont pas dans le python système. Tous les autres scripts du dépôt le
# nomment ; celui-ci ne le faisait pas.
if [ -x "$VENV" ]; then
  "$VENV" -c "import supabase, boto3" 2>/dev/null \
    && ok "venv du backend utilisable (supabase-py importable)" \
    || { ko "le venv existe mais ses dépendances manquent : pip install -r requirements.txt"; MANQUE=1; }
else
  ko "interpréteur du venv introuvable : $VENV"; MANQUE=1
fi
DUMP_MAJ="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
[ "${DUMP_MAJ:-0}" -ge 17 ] && ok "pg_dump $DUMP_MAJ ≥ 17 (Supabase tourne en 17.6)" \
                            || { ko "pg_dump ${DUMP_MAJ:-?} trop ancien : il REFUSERA de sauvegarder Supabase"; MANQUE=1; }
[ -r "$URI_FILE" ] && ok "URI Supabase lisible ($URI_FILE)" \
                   || { ko "URI Supabase absente — voir l'en-tête de ce script"; MANQUE=1; }
[ "$(stat -c %a "$URI_FILE" 2>/dev/null)" = "600" ] \
  && ok "URI en 0600" || ko "URI pas en 0600 — chmod 600 $URI_FILE (elle contient un mot de passe)"
[ -r "$BACKEND/.env" ] && ok ".env du backend lisible" || { ko ".env introuvable"; MANQUE=1; }
if grep -qi 'supabase\.co' "$BACKEND/.env" 2>/dev/null; then
  ok ".env désigne encore Supabase (point de départ attendu)"
else
  ko ".env ne désigne plus Supabase — bascule déjà faite ? Vérifie avant d'insister"; MANQUE=1
fi
LIBRE_KO="$(df -k / | awk 'NR==2{print $4}')"
[ "${LIBRE_KO:-0}" -gt 2097152 ] && ok "espace disque : $((LIBRE_KO/1024)) Mo libres" \
                                 || { ko "moins de 2 Go libres — l'archive et le dump ne tiendront pas"; MANQUE=1; }
systemctl is-active --quiet postgresql && ok "PostgreSQL actif" || { ko "PostgreSQL inactif"; MANQUE=1; }
systemctl is-active --quiet postgrest  && ok "PostgREST actif"  || { ko "PostgREST inactif"; MANQUE=1; }
psql_supabase -tAc "select 1" >/dev/null 2>&1 \
  && ok "connexion à Supabase établie" \
  || { ko "impossible de joindre Supabase avec l'URI fournie"; MANQUE=1; }
[ "$MANQUE" = 0 ] || mort 0 "un préalable manque"

# ═══ 1. La sauvegarde qui aboutit ═══════════════════════════════════════════
# C'EST LA CONDITION QUI BLOQUE TOUT DEPUIS AOÛT (BASCULE.md §0.6).
#
# Déclenchée PAR SYSTEMD, et pas par « bash backup_db.sh ». L'unité
# uti-backup.service porte AGE_RECIPIENT (la clé de chiffrement) et
# EnvironmentFile=/etc/uti-backup.env (les identifiants du dépôt hors-site et
# l'URL du chien de garde). Lancé depuis un shell, le script perd tout ça et son
# propre fail-closed l'arrête : « pas de destinataire age ». Type=oneshot, donc
# `systemctl start` est synchrone.
if etape 1 "Première sauvegarde réussie du VPS"; then
  faire "sudo systemctl start uti-backup" || mort 1 "l'unité uti-backup a échoué — journalctl -u uti-backup -n 50"
  if [ "$DRY" = 0 ]; then
    MARQUEUR=/var/backups/uti/.dernier_succes
    [ -f "$MARQUEUR" ] || mort 1 "aucun marqueur de succès — la sauvegarde n'a PAS abouti"
    AGE=$(( $(date +%s) - $(stat -c %Y "$MARQUEUR") ))
    [ "$AGE" -lt 900 ] && ok "sauvegarde datée d'il y a ${AGE}s" \
                       || mort 1 "le marqueur date de ${AGE}s : c'est une VIEILLE sauvegarde, pas celle-ci"
    ls -1 /var/backups/uti/*.pgcustom* >/dev/null 2>&1 \
      && ok "archive présente : $(ls -1t /var/backups/uti/*.pgcustom* | head -1)" \
      || mort 1 "marqueur présent mais aucune archive : incohérent"
  fi
  fait etape1
fi

# ═══ 2. Archive « AVANT » ═══════════════════════════════════════════════════
if etape 2 "Archive hors ligne de Supabase (état AVANT)"; then
  faire "install -d -m 700 '$ARCHIVES'" || mort 2 "répertoire d'archives non créable"
  # Photo de l'existant AVANT l'export. Sans elle, `ls -1dt | head -1` validait
  # en vert une archive de la semaine dernière quand l'export venait d'échouer —
  # l'étape 1 vérifie la fraîcheur de son résultat, l'étape 6 la nouveauté du
  # sien, celle-ci ne vérifiait rien.
  AVANT_EXPORT="$(ls -1dt "$ARCHIVES"/*/ 2>/dev/null | head -1)"
  faire "bash '$BACKEND/scripts/export_supabase_archive.sh' '$ARCHIVES' --with-secrets" \
    || mort 2 "l'export a échoué"
  if [ "$DRY" = 0 ]; then
    AV="$(ls -1dt "$ARCHIVES"/*/ 2>/dev/null | head -1)"
    [ -n "$AV" ] || mort 2 "aucune archive produite"
    [ "$AV" != "$AVANT_EXPORT" ] \
      || mort 2 "aucune archive NOUVELLE — l'export a échoué en laissant l'ancienne en place"
    ( cd "$AV" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
      && ok "empreintes vérifiées ($AV)" || mort 2 "SHA256SUMS ne se vérifie pas"
    N=$(ls -1 "$AV/csv" | wc -l)
    [ "$N" -ge 24 ] && ok "$N tables exportées en CSV" \
                    || mort 2 "seulement $N CSV — la liste TABLES du script est incomplète"
    echo "$AV" > "$HOME/.bascule_archive_avant"
    ko "RAPPEL : $ARCHIVES n'est dans AUCUNE sauvegarde automatique."
    info "backup_db.sh ne couvre que la base et $FICHIERS. Cette archive est le"
    info "seul état « avant » de Supabase : copie-la hors du VPS avant de continuer."
  fi
  fait etape2
fi

# ═══ 3. Les fichiers sur le disque ══════════════════════════════════════════
if etape 3 "Copie des objets vers $FICHIERS"; then
  info "simulation d'abord — elle liste ce qui serait copié :"
  lire "cd '$BACKEND' && '$VENV' scripts/migrate_storage_to_ovh.py --dry-run" \
    || mort 3 "la simulation de copie a échoué"
  faire "cd '$BACKEND' && '$VENV' scripts/migrate_storage_to_ovh.py --vers local" \
    || mort 3 "la copie des fichiers a échoué"
  if [ "$DRY" = 0 ]; then
    N=$(find "$FICHIERS" -type f 2>/dev/null | wc -l)
    ATTENDU=$(psql_supabase -tAc "select count(*) from storage.objects" 2>/dev/null || echo 38)
    [ "$N" -ge "${ATTENDU:-38}" ] && ok "$N fichiers sur le disque (${ATTENDU:-38} attendus)" \
      || mort 3 "seulement $N fichiers copiés sur ${ATTENDU:-38} — relire la sortie ci-dessus"
    MODE=$(stat -c %a "$FICHIERS")
    [ "$MODE" = "700" ] && ok "répertoire en 0700" || ko "répertoire en $MODE — attendu 700"
  fi
  fait etape3
fi

# ═══ 4. Réécriture des URLs, DANS SUPABASE ══════════════════════════════════
# Étape la plus contre-intuitive : on modifie la base qu'on s'apprête à quitter.
# C'est voulu. L'export de l'étape 6 emportera ces valeurs déjà correctes, donc
# la base neuve naît propre. Réversible par l'archive de l'étape 2, et les
# buckets PRIVÉS reçoivent un CHEMIN NU que les deux modes savent lire
# (services/storage.py:_object_path) — rien ne casse dans l'intervalle. Seuls
# les AVATARS pointeront vers le VPS avant la bascule du .env.
if etape 4 "Réécriture des URLs de fichiers dans Supabase"; then
  lire "cd '$BACKEND' && PUBLIC_BASE_URL='$BASE_PUBLIQUE' '$VENV' scripts/migrate_storage_to_ovh.py --vers local --rewrite-db --dry-run" \
    || mort 4 "la simulation de réécriture a échoué"
  faire "cd '$BACKEND' && PUBLIC_BASE_URL='$BASE_PUBLIQUE' '$VENV' scripts/migrate_storage_to_ovh.py --vers local --rewrite-db" \
    || mort 4 "la réécriture a échoué"
  if [ "$DRY" = 0 ]; then
    RESTE=$(psql_supabase -tAc "
      select coalesce(sum(n),0) from (
        select count(*) n from public.submissions             where cv_url   like '%supabase%'
        union all select count(*) from public.profiles        where avatar_url like '%supabase%'
        union all select count(*) from public.partner_compliance_docs where file_url like '%supabase%'
        union all select count(*) from public.email_templates where body     like '%supabase%'
      ) t")
    [ "${RESTE:-1}" = "0" ] && ok "plus aucune URL supabase.co dans les 4 colonnes" \
                            || mort 4 "${RESTE:-?} valeur(s) pointent encore vers Supabase"
  fi
  fait etape4
fi

# ═══ 5. FERMETURE DE LA FENÊTRE ═════════════════════════════════════════════
# À partir d'ici, la plateforme est INDISPONIBLE. C'est le prix d'une bascule
# sans perte : sans cet arrêt, tout ce qu'un utilisateur écrit entre l'export de
# l'étape 6 et le redémarrage de l'étape 10 part dans Supabase et n'arrive
# JAMAIS dans la base neuve. Aucune sonde ne verrait cette perte.
#
# uti-backup.timer est arrêté aussi : il ouvre une session sur « uti » toutes
# les heures, et une seule session suffit à faire échouer le renommage de
# l'étape 8.
if etape 5 "Fermeture de la fenêtre de maintenance"; then
  faire "sudo systemctl stop uti-backup.timer" || ko "uti-backup.timer non arrêté (peut-être absent)"
  faire "sudo systemctl stop uti-backend"      || mort 5 "impossible d'arrêter le backend"
  if [ "$DRY" = 0 ]; then
    sleep 2
    curl -sf --max-time 3 "$API/health" >/dev/null \
      && mort 5 "le backend répond encore — il n'est pas arrêté" \
      || ok "backend arrêté : plus aucune écriture ne part vers Supabase"
    info "⏱  La plateforme est indisponible à partir de maintenant."
  fi
  fait etape5
fi

# ═══ 6. Archive « APRÈS » — la source de chargement ═════════════════════════
if etape 6 "Second export de Supabase (source de chargement)"; then
  faire "bash '$BACKEND/scripts/export_supabase_archive.sh' '$ARCHIVES' --with-secrets" \
    || mort 6 "l'export a échoué"
  if [ "$DRY" = 0 ]; then
    AP="$(ls -1dt "$ARCHIVES"/*/ | head -1)"
    AV="$(cat "$HOME/.bascule_archive_avant" 2>/dev/null || echo)"
    [ "$AP" != "$AV" ] || mort 6 "l'export n'a produit aucun répertoire nouveau (collision d'horodatage)"
    ( cd "$AP" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
      && ok "empreintes vérifiées ($AP)" || mort 6 "SHA256SUMS ne se vérifie pas"
    echo "$AP" > "$HOME/.bascule_archive_apres"
    ok "source de chargement : $AP"
  fi
  fait etape6
fi

# ═══ 7. Chargement dans une base NEUVE, à côté ══════════════════════════════
# On ne touche PAS à « uti » ici. On charge dans « uti_verif », on compare, et
# seule l'étape 8 promeut.
if etape 7 "Chargement dans la base de vérification"; then
  AP="$(cat "$HOME/.bascule_archive_apres" 2>/dev/null || echo)"
  [ "$DRY" = 1 ] || [ -n "$AP" ] || mort 7 "archive de l'étape 6 introuvable — rejouer --depuis 6"
  # `with (force)` : sans lui, une session oubliée sur uti_verif fait échouer le
  # DROP, le CREATE échoue à son tour, et pg_restore recharge PAR-DESSUS
  # l'ancien contenu. C'est ce que fait déjà restore_drill.sh.
  faire "psql_su -d postgres -c \"drop database if exists uti_verif with (force)\"" \
    || mort 7 "suppression de uti_verif impossible"
  faire "psql_su -d postgres -c \"create database uti_verif owner $PROPRIO\"" \
    || mort 7 "création de uti_verif impossible"
  for ext in pgcrypto pg_trgm; do
    faire "psql_su -d uti_verif -c \"create extension if not exists $ext\"" \
      || mort 7 "extension $ext non installable"
  done
  # --exit-on-error : sans lui, pg_restore continue après une erreur et rend 0.
  # Journal complet dans un fichier : `| tail -20` masquait la cause.
  JOURNAL="/tmp/bascule-restore-$$.log"
  faire "sudo -u postgres pg_restore --no-owner --no-acl --exit-on-error --schema=public -d uti_verif '$AP/dump.pgcustom' > '$JOURNAL' 2>&1" \
    || { [ "$DRY" = 0 ] && tail -30 "$JOURNAL"; mort 7 "pg_restore a échoué — journal complet : $JOURNAL"; }
  # LE POINT QUI A CASSÉ LA SAUVEGARDE EN AOÛT : les tables viennent d'être
  # créées par « postgres », donc uti_admin n'en possède aucune et service_role
  # n'a aucun droit dessus. roles_postgrest.sql §3bis+§4 répare les deux.
  faire "psql_su -d uti_verif -v owner='$PROPRIO' -f '$BACKEND/deploy/roles_postgrest.sql'" \
    || mort 7 "roles_postgrest.sql a échoué — service_role n'aurait aucun droit"
  if [ "$DRY" = 0 ]; then
    N=$(sudo -u postgres psql -tAd uti_verif -c \
      "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")
    [ "${N:-0}" -ge 24 ] && ok "$N tables chargées" || mort 7 "seulement ${N:-0} tables — restauration incomplète"
    PROP=$(sudo -u postgres psql -tAd uti_verif -c \
      "select count(*) from pg_tables where schemaname='public' and tableowner<>'$PROPRIO'")
    [ "${PROP:-1}" = "0" ] && ok "toutes les tables appartiennent à $PROPRIO" \
                           || mort 7 "${PROP:-?} table(s) appartiennent à un autre rôle — pg_dump échouera comme en août"
  fi
  fait etape7
fi

# ═══ 8. Comparaison ligne à ligne, puis promotion ═══════════════════════════
if etape 8 "Comparaison avec Supabase, puis promotion de la base"; then
  if [ "$DRY" = 0 ]; then
    ECART=0
    for t in profiles user_credentials clients consultants appels_offres submissions \
             matchings audit_log ai_usage email_outbox email_optouts invitations \
             partner_clients ao_consultant_state human_decision partner_email_log \
             pacs pac_clients scoring_config app_settings email_templates \
             client_reviews partner_compliance_docs support_messages; do
      A=$(psql_supabase -tAc "select count(*) from public.$t" 2>/dev/null || echo ERR)
      B=$(sudo -u postgres psql -tAd uti_verif -c "select count(*) from public.$t" 2>/dev/null || echo ERR)
      if [ "$A" = "$B" ] && [ "$A" != "ERR" ]; then
        printf '    %-26s %6s = %-6s ✓\n' "$t" "$A" "$B"
      else
        printf '    \033[31m%-26s %6s ≠ %-6s ✗\033[0m\n' "$t" "$A" "$B"; ECART=$((ECART+1))
      fi
    done
    [ "$ECART" = 0 ] && ok "les 24 tables ont des comptages identiques" \
                     || mort 8 "$ECART table(s) divergent — NE PAS PROMOUVOIR"
  fi

  # Promotion. PostgREST doit lâcher ses connexions, et toute session résiduelle
  # sur les deux bases doit être coupée : un « alter database rename » échoue
  # dès qu'UNE session est ouverte.
  faire "sudo systemctl stop postgrest" || mort 8 "impossible d'arrêter PostgREST"
  faire "psql_su -d postgres -c \"select pg_terminate_backend(pid) from pg_stat_activity where datname in ('$BASE','uti_verif') and pid <> pg_backend_pid()\" >/dev/null" \
    || ko "terminaison des sessions : rien à couper, ou échec bénin"
  # Les deux renommages, chacun vérifié. Si le second échoue, on REMET le
  # premier : sinon il n'existe plus aucune base nommée « uti » et PostgREST
  # repart sur une db-uri qui ne désigne rien.
  if ! faire "psql_su -d postgres -c \"alter database $BASE rename to uti_avant_bascule\""; then
    faire "sudo systemctl start postgrest"
    mort 8 "renommage de $BASE impossible (session ouverte ?) — rien n'a changé"
  fi
  if ! faire "psql_su -d postgres -c \"alter database uti_verif rename to $BASE\""; then
    ko "second renommage impossible — remise en place immédiate du premier"
    faire "psql_su -d postgres -c \"alter database uti_avant_bascule rename to $BASE\"" \
      || ko "🚨 REMISE EN PLACE ÉCHOUÉE : plus aucune base « $BASE ». sudo -u postgres psql -l"
    faire "sudo systemctl start postgrest"
    mort 8 "promotion annulée"
  fi
  faire "sudo systemctl start postgrest" || mort 8 "PostgREST ne redémarre pas"
  if [ "$DRY" = 0 ]; then
    sleep 2
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/rest/v1/profiles)
    [ "$code" = "401" ] && ok "PostgREST répond 401 sans jeton sur la base neuve" \
                        || mort 8 "PostgREST répond $code — attendu 401"
    ok "ancienne base conservée sous « uti_avant_bascule »"
  fi
  fait etape8
fi

# ═══ 9. Les trois lignes de .env ════════════════════════════════════════════
# Sans guillemets, délibérément : post_bascule_check.sh et install_db.sh font des
# grep ancrés qu'une paire de guillemets ferait échouer.
if etape 9 "Bascule des trois lignes de .env"; then
  if [ "$DRY" = 0 ] && [ ! -e "$ENV_SAUVE" ]; then
    cp "$BACKEND/.env" "$ENV_SAUVE" || mort 9 "sauvegarde du .env impossible"
    chmod 600 "$ENV_SAUVE"
    ok "sauvegarde unique du .env : $ENV_SAUVE (jamais écrasée)"
  elif [ "$DRY" = 0 ]; then
    ok "sauvegarde du .env déjà présente, conservée telle quelle : $ENV_SAUVE"
  fi
  faire "sed -i 's#^SUPABASE_URL=.*#SUPABASE_URL=http://127.0.0.1:8080#' '$BACKEND/.env'" \
    || mort 9 "réécriture de SUPABASE_URL impossible"
  faire "grep -q '^STORAGE_BACKEND=' '$BACKEND/.env' && sed -i 's#^STORAGE_BACKEND=.*#STORAGE_BACKEND=local#' '$BACKEND/.env' || echo 'STORAGE_BACKEND=local' >> '$BACKEND/.env'"
  faire "grep -q '^PUBLIC_BASE_URL=' '$BACKEND/.env' && sed -i 's#^PUBLIC_BASE_URL=.*#PUBLIC_BASE_URL=$BASE_PUBLIQUE#' '$BACKEND/.env' || echo 'PUBLIC_BASE_URL=$BASE_PUBLIQUE' >> '$BACKEND/.env'"
  if [ "$DRY" = 0 ]; then
    grep -qE '^SUPABASE_URL=http://127\.0\.0\.1:8080$' "$BACKEND/.env" && ok "SUPABASE_URL → façade locale" || mort 9 "SUPABASE_URL non basculée"
    grep -qE '^STORAGE_BACKEND=local$'   "$BACKEND/.env" && ok "STORAGE_BACKEND=local" || mort 9 "STORAGE_BACKEND non posé"
    grep -qE '^PUBLIC_BASE_URL=https://' "$BACKEND/.env" && ok "PUBLIC_BASE_URL posée"  || mort 9 "PUBLIC_BASE_URL non posée"
    if grep -qi 'supabase\.co' "$BACKEND/.env"; then
      ko "il reste une référence supabase.co dans .env :"; grep -in 'supabase\.co' "$BACKEND/.env"
      mort 9 "nettoie-la avant de continuer (la sauvegarde $ENV_SAUVE est intacte)"
    fi
    ok "plus aucune référence supabase.co dans .env"
  fi
  fait etape9
fi

# ═══ 10. Redémarrage, et PREUVE que la configuration a été rechargée ════════
# On n'appelle PAS deploy.sh : sa première action est « git pull origin master »,
# qui amènerait au milieu d'une bascule tout ce que master a accumulé — y compris
# d'éventuelles migrations non appliquées. Et s'il échoue sur ce pull (set -e), il
# sort AVANT le restart : le backend continuerait de tourner sur Supabase pendant
# que /health/db répondrait vert. On redémarre donc ici, et on PROUVE le
# rechargement par le changement de `deployed_at` (main.py : instant de démarrage
# du processus), qu'aucune sonde d'état ne peut simuler.
if etape 10 "Redémarrage du backend et réouverture de la fenêtre"; then
  AVANT_DEP=$(curl -s --max-time 3 "$API/health" 2>/dev/null | grep -o '"deployed_at":"[^"]*"' || echo "arrete")
  faire "cd '$BACKEND' && '$BACKEND/venv/bin/pip' install -q -r requirements.txt" || ko "pip a signalé un problème"
  faire "sudo systemctl start uti-backend" || mort 10 "le backend ne démarre pas — journalctl -u uti-backend -n 100"
  if [ "$DRY" = 0 ]; then
    for _ in $(seq 1 15); do sleep 2; curl -sf --max-time 3 "$API/health" >/dev/null && break; done
    curl -sf --max-time 3 "$API/health" >/dev/null || mort 10 "/health muet — bash $0 --rollback"
    APRES_DEP=$(curl -s --max-time 3 "$API/health" | grep -o '"deployed_at":"[^"]*"')
    [ "$APRES_DEP" != "$AVANT_DEP" ] && ok "processus redémarré (deployed_at a changé) : il a relu .env" \
      || mort 10 "deployed_at inchangé — le processus n'a PAS redémarré, il lit encore Supabase"
    curl -sf --max-time 5 "$API/health/db" >/dev/null \
      && ok "/health/db répond — le backend voit la base LOCALE" \
      || mort 10 "le backend ne voit pas sa base — bash $0 --rollback"
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X POST "$API/auth/login" \
      -H 'Content-Type: application/json' \
      -d '{"email":"sonde-de-bascule@sonde-interne-uti.fr","password":"sonde"}')
    { [ "$code" = "401" ] || [ "$code" = "429" ]; } \
      && ok "/auth/login répond $code — la table des identifiants est lisible" \
      || mort 10 "/auth/login répond $code (401 attendu) — user_credentials illisible"
  fi
  faire "sudo systemctl start uti-backup.timer" || ko "uti-backup.timer non relancé"
  ok "⏱  Fenêtre de maintenance refermée : la plateforme est de nouveau servie."
  fait etape10
fi

# ═══ 11. Contrôle final ═════════════════════════════════════════════════════
if etape 11 "Contrôle de bascule"; then
  faire "bash '$BACKEND/scripts/post_bascule_check.sh'" || mort 11 "contrôle de bascule ROUGE — lire ci-dessus, puis décider entre corriger et --rollback"
  faire "bash '$BACKEND/deploy/supervision.sh'" || ko "supervision : au moins une anomalie (voir ci-dessus)"
  fait etape11
fi

g "TERMINÉ"
cat <<'SUITE'
  Ce qui reste à faire À LA MAIN, et qu'aucun script ne doit décider :

   1. Se connecter à la plateforme avec un vrai compte. C'est le seul contrôle
      qui prouve la chaîne entière ; les sondes prouvent des morceaux.
   2. Ouvrir un CV depuis un AO — il passe désormais par /files/d/<jeton>.
   3. Vérifier qu'un avatar s'affiche (c'est le seul lien réécrit vers une URL
      absolue du VPS, donc le seul qui casse si PUBLIC_BASE_URL est faux).
   4. Copier l'archive ~/archive-supabase HORS du VPS : aucune sauvegarde
      automatique ne la couvre, et c'est le seul état « avant » de Supabase.
   5. Laisser tourner la période d'observation (BASCULE.md §6) avant de mettre
      le projet Supabase en pause, puis de le supprimer. Le retour arrière reste
      une commande tant qu'il existe :  bash scripts/bascule.sh --rollback
   6. app_settings ne contient qu'une ligne (« notifications ») : « data_retention »
      et « ai_budget » manquent depuis toujours (BASCULE.md §0.5), donc la purge
      RGPD et la surveillance du budget IA sont inertes. À poser depuis
      l'administration — ce n'est pas un sujet de bascule.
SUITE
