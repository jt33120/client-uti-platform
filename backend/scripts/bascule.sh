#!/usr/bin/env bash
# =============================================================================
#  BASCULE — Supabase → PostgreSQL du VPS, en dix étapes vérifiées.
#
#  POURQUOI CE SCRIPT EXISTE
#
#  Tout ce qu'il enchaîne existait déjà, éparpillé dans six fichiers et deux
#  documents. Ce qui manquait n'était pas un outil : c'était L'ORDRE, et la
#  PREUVE entre deux gestes. Le chantier s'est arrêté en août parce que personne
#  ne pouvait dire « l'étape précédente a réussi » autrement qu'en le croyant.
#
#  Chaque étape ci-dessous se termine par un CONTRÔLE qui échoue bruyamment.
#  Aucune étape ne commence si la précédente n'a pas laissé sa preuve.
#
#  L'ORDRE N'EST PAS ARBITRAIRE — trois contraintes le figent :
#
#   * La copie des fichiers et la réécriture des URLs doivent avoir lieu TANT
#     QUE .env DÉSIGNE ENCORE SUPABASE. Lancées après, elles réécriraient la
#     base neuve et les 38 objets deviendraient introuvables
#     (scripts/migrate_storage_to_ovh.py:12-15).
#   * L'export qui SERT À CHARGER le VPS doit être pris APRÈS la réécriture,
#     sinon la base neuve naît avec des URLs Supabase mortes. D'où deux exports :
#     l'étape 2 est le filet de sécurité « avant », l'étape 5 la source de vérité.
#   * Les GRANT de roles_postgrest.sql ne portent que sur les tables EXISTANT au
#     moment où il tourne (roles_postgrest.sql §4). Un rechargement crée des
#     tables neuves : il faut le rejouer, sinon service_role n'a aucun droit et
#     l'API répond 403 sur tout.
#
#  CE QUI N'EST JAMAIS DÉTRUIT
#  Le projet Supabase n'est ni modifié dans sa structure ni supprimé : seules
#  quatre colonnes d'URL y sont réécrites (réversible, cf. étape 4). La base
#  « uti » d'avant est conservée sous « uti_avant_bascule ». Le .env est
#  sauvegardé. Le retour arrière tient en une commande : voir --rollback.
#
#  USAGE
#      bash ~/app/backend/scripts/bascule.sh --dry-run      # ne modifie RIEN
#      bash ~/app/backend/scripts/bascule.sh                # pour de vrai
#      bash ~/app/backend/scripts/bascule.sh --depuis 6     # reprend à l'étape 6
#      bash ~/app/backend/scripts/bascule.sh --rollback     # revient à Supabase
#
#  PRÉREQUIS, à préparer AVANT (l'étape 0 les vérifie et refuse sinon) :
#      install -m 600 /dev/null ~/.supabase_db_uri
#      nano ~/.supabase_db_uri     # Console Supabase → Settings → Database
#                                  # → Connection string → URI (mode Session)
# =============================================================================
set -uo pipefail

BACKEND="${BACKEND_DIR:-$HOME/app/backend}"
ETAT="${ETAT_FILE:-$HOME/.bascule_etat}"
ARCHIVES="${ARCHIVES_DIR:-$HOME/archive-supabase}"
URI_FILE="${SUPABASE_URI_FILE:-$HOME/.supabase_db_uri}"
BASE="${PGDATABASE:-uti}"
PROPRIO="${DB_OWNER:-uti_admin}"
FICHIERS="${FILES_DIR:-/var/lib/uti/files}"
API="${API_URL:-http://127.0.0.1:8000}"
BASE_PUBLIQUE="${PUBLIC_BASE_URL:-https://vps-cc93f2a8.vps.ovh.net}"

DRY=0; DEPUIS=1; ROLLBACK=0
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
mort() { printf '\n\033[31m\033[1mARRÊT à l'\''étape %s : %s\033[0m\n' "$1" "$2"
         printf 'Rien n'\''a été fait au-delà. Corrige, puis : bash %s --depuis %s\n' "$0" "$1"
         exit 1; }
faire() { if [ "$DRY" = 1 ]; then printf '    \033[33m[simulation]\033[0m %s\n' "$*"; else eval "$@"; fi; }
fait()  { [ "$DRY" = 1 ] || echo "$1" >> "$ETAT"; }
deja()  { [ -f "$ETAT" ] && grep -qx "$1" "$ETAT"; }

# ── Retour arrière ──────────────────────────────────────────────────────────
if [ "$ROLLBACK" = 1 ]; then
  g "RETOUR ARRIÈRE vers Supabase"
  SAUVE="$(ls -1t "$BACKEND"/.env.avant-bascule-* 2>/dev/null | head -1)"
  [ -n "$SAUVE" ] || { ko "aucune sauvegarde .env.avant-bascule-* trouvée"; exit 1; }
  info "restauration de $SAUVE"
  cp "$SAUVE" "$BACKEND/.env"
  sudo systemctl restart uti-backend
  sleep 3
  curl -sf --max-time 5 "$API/health/db" >/dev/null \
    && ok "backend revenu sur Supabase, base joignable" \
    || ko "la base ne répond toujours pas — journalctl -u uti-backend -n 100"
  info "Les fichiers copiés sur $FICHIERS sont laissés en place (sans effet en mode supabase)."
  info "⚠️  Les URLs réécrites à l'étape 4 restent réécrites. C'est SANS EFFET sur les"
  info "    buckets privés (chemin nu, lu tel quel par storage._object_path), mais les"
  info "    AVATARS pointeront vers le VPS tant que STORAGE_BACKEND n'est pas 'local'."
  exit 0
fi

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  BASCULE Supabase → VPS                                          ║"
[ "$DRY" = 1 ] \
&& echo "║  MODE SIMULATION — rien ne sera modifié                           ║" \
|| echo "║  MODE RÉEL                                                        ║"
echo "╚══════════════════════════════════════════════════════════════════╝"

etape() {  # etape <n> <titre> ; renvoie 1 si on doit la sauter
  local n="$1" t="$2"
  if [ "$n" -lt "$DEPUIS" ]; then printf '\n\033[2m── %s. %s — sautée (--depuis %s)\033[0m\n' "$n" "$t" "$DEPUIS"; return 1; fi
  if deja "etape$n"; then printf '\n\033[2m── %s. %s — déjà faite\033[0m\n' "$n" "$t"; return 1; fi
  g "$n. $t"; return 0
}

# ═══ 0. Préalables ══════════════════════════════════════════════════════════
# Tout ce qui suit suppose ces conditions. Les vérifier ici coûte deux secondes ;
# les découvrir à l'étape 5, la base à moitié chargée, coûte une soirée.
if etape 0 "Préalables"; then
  MANQUE=0
  for outil in psql pg_dump curl python3; do
    command -v "$outil" >/dev/null && ok "$outil présent" || { ko "$outil ABSENT"; MANQUE=1; }
  done
  DUMP_MAJ="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
  [ "$DUMP_MAJ" -ge 17 ] && ok "pg_dump $DUMP_MAJ ≥ 17 (Supabase tourne en 17.6)" \
                         || { ko "pg_dump $DUMP_MAJ trop ancien : il REFUSERA de sauvegarder Supabase"; MANQUE=1; }
  [ -r "$URI_FILE" ] && ok "URI Supabase lisible ($URI_FILE)" \
                     || { ko "URI Supabase absente — voir l'en-tête de ce script"; MANQUE=1; }
  [ -r "$BACKEND/.env" ] && ok ".env du backend lisible" || { ko ".env introuvable"; MANQUE=1; }
  grep -q 'supabase\.co' "$BACKEND/.env" \
    && ok ".env désigne encore Supabase (c'est bien le point de départ attendu)" \
    || { ko ".env ne désigne plus Supabase — bascule déjà faite ? Vérifie avant d'insister"; MANQUE=1; }
  LIBRE_KO="$(df -k / | awk 'NR==2{print $4}')"
  [ "$LIBRE_KO" -gt 2097152 ] && ok "espace disque : $((LIBRE_KO/1024)) Mo libres" \
                              || { ko "moins de 2 Go libres — l'archive et le dump ne tiendront pas"; MANQUE=1; }
  systemctl is-active --quiet postgresql && ok "PostgreSQL actif" || { ko "PostgreSQL inactif"; MANQUE=1; }
  systemctl is-active --quiet postgrest  && ok "PostgREST actif"  || { ko "PostgREST inactif"; MANQUE=1; }
  [ "$MANQUE" = 0 ] || mort 0 "un préalable manque"
  fait etape0
fi

# ═══ 1. La sauvegarde qui aboutit ═══════════════════════════════════════════
# C'EST LA CONDITION QUI BLOQUE TOUT DEPUIS AOÛT (BASCULE.md §0.6). Elle passe
# en premier, et pas en dernier : une bascule dont on ne peut pas revenir n'est
# pas une bascule, c'est un pari. PGUSER est nommé explicitement — son absence
# est le défaut exact qui faisait échouer les trois scripts.
if etape 1 "Première sauvegarde réussie du VPS"; then
  faire "PGUSER='$PROPRIO' bash '$BACKEND/deploy/backup_db.sh'"
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
# Le filet. Prise avant toute modification de Supabase, elle permet de tout
# reconstituer même si les étapes 3 et 4 se passaient mal.
if etape 2 "Archive hors ligne de Supabase (état AVANT)"; then
  faire "bash '$BACKEND/scripts/export_supabase_archive.sh' '$ARCHIVES' --with-secrets"
  if [ "$DRY" = 0 ]; then
    AV="$(ls -1dt "$ARCHIVES"/*/ 2>/dev/null | head -1)"
    [ -n "$AV" ] || mort 2 "aucune archive produite"
    ( cd "$AV" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
      && ok "empreintes vérifiées ($AV)" || mort 2 "SHA256SUMS ne se vérifie pas"
    N=$(ls -1 "$AV/csv" | wc -l)
    [ "$N" -ge 24 ] && ok "$N tables exportées en CSV" \
                    || mort 2 "seulement $N CSV — la liste TABLES du script est incomplète"
    echo "$AV" > "$HOME/.bascule_archive_avant"
  fi
  fait etape2
fi

# ═══ 3. Les fichiers sur le disque ══════════════════════════════════════════
# Non destructif : rien n'est supprimé de Supabase Storage. On copie, on compte.
if etape 3 "Copie des 38 objets vers $FICHIERS"; then
  info "simulation d'abord — elle liste ce qui serait copié :"
  faire "cd '$BACKEND' && python3 scripts/migrate_storage_to_ovh.py --dry-run"
  faire "cd '$BACKEND' && python3 scripts/migrate_storage_to_ovh.py --vers local"
  if [ "$DRY" = 0 ]; then
    N=$(find "$FICHIERS" -type f 2>/dev/null | wc -l)
    [ "$N" -ge 38 ] && ok "$N fichiers sur le disque" \
                    || mort 3 "seulement $N fichiers copiés (38 attendus) — relire la sortie ci-dessus"
    MODE=$(stat -c %a "$FICHIERS")
    [ "$MODE" = "700" ] && ok "répertoire en 0700" || ko "répertoire en $MODE — attendu 700"
  fi
  fait etape3
fi

# ═══ 4. Réécriture des URLs, DANS SUPABASE ══════════════════════════════════
# Étape la plus contre-intuitive : on modifie la base qu'on s'apprête à quitter.
# C'est voulu. L'export de l'étape 5 emportera ces valeurs déjà correctes, donc
# la base neuve naît propre. Réversible : l'archive de l'étape 2 contient l'état
# d'avant, et les buckets PRIVÉS reçoivent un CHEMIN NU que les deux modes
# savent lire (services/storage.py:_object_path) — rien ne casse dans l'intervalle.
# Seuls les AVATARS pointeront vers le VPS avant la bascule du .env : quelques
# images cassées pendant les minutes qui suivent, et rien d'autre.
if etape 4 "Réécriture des URLs de fichiers dans Supabase"; then
  faire "cd '$BACKEND' && PUBLIC_BASE_URL='$BASE_PUBLIQUE' python3 scripts/migrate_storage_to_ovh.py --vers local --rewrite-db --dry-run"
  faire "cd '$BACKEND' && PUBLIC_BASE_URL='$BASE_PUBLIQUE' python3 scripts/migrate_storage_to_ovh.py --vers local --rewrite-db"
  if [ "$DRY" = 0 ]; then
    PGURI="$(tr -d '\r\n' < "$URI_FILE")"
    RESTE=$(psql "$PGURI" -tAc "
      select coalesce(sum(n),0) from (
        select count(*) n from public.submissions             where cv_url   like '%supabase%'
        union all select count(*) from public.profiles        where avatar_url like '%supabase%'
        union all select count(*) from public.partner_compliance_docs where file_url like '%supabase%'
        union all select count(*) from public.email_templates where body     like '%supabase%'
      ) t")
    [ "$RESTE" = "0" ] && ok "plus aucune URL supabase.co dans les 4 colonnes" \
                       || mort 4 "$RESTE valeur(s) pointent encore vers Supabase"
  fi
  fait etape4
fi

# ═══ 5. Archive « APRÈS » — la source de chargement ═════════════════════════
if etape 5 "Second export de Supabase (état APRÈS réécriture)"; then
  faire "bash '$BACKEND/scripts/export_supabase_archive.sh' '$ARCHIVES' --with-secrets"
  if [ "$DRY" = 0 ]; then
    AP="$(ls -1dt "$ARCHIVES"/*/ | head -1)"
    AV="$(cat "$HOME/.bascule_archive_avant" 2>/dev/null || echo)"
    [ "$AP" != "$AV" ] || mort 5 "l'export n'a produit aucun répertoire nouveau"
    ( cd "$AP" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
      && ok "empreintes vérifiées ($AP)" || mort 5 "SHA256SUMS ne se vérifie pas"
    echo "$AP" > "$HOME/.bascule_archive_apres"
    ok "source de chargement : $AP"
  fi
  fait etape5
fi

# ═══ 6. Chargement dans une base NEUVE, à côté ══════════════════════════════
# On ne touche PAS à « uti » ici. On charge dans « uti_verif », on compare, et
# seule l'étape 7 promeut. Une restauration qui échoue à mi-chemin ne doit pas
# laisser la seule base locale dans un état intermédiaire.
if etape 6 "Chargement dans la base de vérification"; then
  AP="$(cat "$HOME/.bascule_archive_apres" 2>/dev/null || echo)"
  [ "$DRY" = 1 ] || [ -n "$AP" ] || mort 6 "archive de l'étape 5 introuvable"
  faire "sudo -u postgres psql -v ON_ERROR_STOP=1 -q -c \"drop database if exists uti_verif\""
  faire "sudo -u postgres psql -v ON_ERROR_STOP=1 -q -c \"create database uti_verif owner $PROPRIO\""
  for ext in pgcrypto pg_trgm; do
    faire "sudo -u postgres psql -v ON_ERROR_STOP=1 -q -d uti_verif -c \"create extension if not exists $ext\""
  done
  # --no-owner/--no-acl : le dump vient de Supabase, dont les rôles n'existent
  # pas ici. La propriété et les droits sont reposés juste après par
  # roles_postgrest.sql, qui est la seule autorité sur ce sujet.
  faire "sudo -u postgres pg_restore --no-owner --no-acl --schema=public -d uti_verif '$AP/dump.pgcustom' 2>&1 | tail -20"
  # LE POINT QUI A DÉJÀ CASSÉ LA SAUVEGARDE EN AOÛT : les tables viennent d'être
  # créées par « postgres », donc uti_admin n'en possède aucune et service_role
  # n'a aucun droit dessus. roles_postgrest.sql §3bis+§4 répare les deux.
  faire "sudo -u postgres psql -v ON_ERROR_STOP=1 -q -d uti_verif -v owner='$PROPRIO' < '$BACKEND/deploy/roles_postgrest.sql'"
  if [ "$DRY" = 0 ]; then
    N=$(sudo -u postgres psql -tAd uti_verif -c \
      "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")
    [ "$N" -ge 24 ] && ok "$N tables chargées" || mort 6 "seulement $N tables — restauration incomplète"
    PROP=$(sudo -u postgres psql -tAd uti_verif -c \
      "select count(*) from pg_tables where schemaname='public' and tableowner<>'$PROPRIO'")
    [ "$PROP" = "0" ] && ok "toutes les tables appartiennent à $PROPRIO" \
                      || mort 6 "$PROP table(s) appartiennent à un autre rôle — pg_dump échouera comme en août"
  fi
  fait etape6
fi

# ═══ 7. Comparaison ligne à ligne, puis promotion ═══════════════════════════
if etape 7 "Comparaison avec Supabase, puis promotion de la base"; then
  if [ "$DRY" = 0 ]; then
    PGURI="$(tr -d '\r\n' < "$URI_FILE")"
    ECART=0
    for t in profiles user_credentials clients consultants appels_offres submissions \
             matchings audit_log ai_usage email_outbox email_optouts invitations \
             partner_clients ao_consultant_state human_decision partner_email_log \
             pacs pac_clients scoring_config app_settings email_templates \
             client_reviews partner_compliance_docs support_messages; do
      A=$(psql "$PGURI" -tAc "select count(*) from public.$t" 2>/dev/null || echo ERR)
      B=$(sudo -u postgres psql -tAd uti_verif -c "select count(*) from public.$t" 2>/dev/null || echo ERR)
      if [ "$A" = "$B" ] && [ "$A" != "ERR" ]; then
        printf '    %-26s %6s = %-6s ✓\n' "$t" "$A" "$B"
      else
        printf '    \033[31m%-26s %6s ≠ %-6s ✗\033[0m\n' "$t" "$A" "$B"; ECART=$((ECART+1))
      fi
    done
    [ "$ECART" = 0 ] && ok "les 24 tables ont des comptages identiques" \
                     || mort 7 "$ECART table(s) divergent — NE PAS BASCULER"
  fi
  # Promotion. PostgREST doit lâcher ses connexions avant le renommage.
  faire "sudo systemctl stop postgrest"
  faire "sudo -u postgres psql -v ON_ERROR_STOP=1 -q -c \"alter database $BASE rename to uti_avant_bascule\""
  faire "sudo -u postgres psql -v ON_ERROR_STOP=1 -q -c \"alter database uti_verif rename to $BASE\""
  faire "sudo systemctl start postgrest"
  if [ "$DRY" = 0 ]; then
    sleep 2
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/rest/v1/profiles)
    [ "$code" = "401" ] && ok "PostgREST répond 401 sans jeton sur la base neuve" \
                        || mort 7 "PostgREST répond $code — attendu 401"
    ok "ancienne base conservée sous « uti_avant_bascule »"
  fi
  fait etape7
fi

# ═══ 8. Les trois lignes de .env ════════════════════════════════════════════
# Sans guillemets, délibérément : post_bascule_check.sh et install_db.sh font
# des grep ancrés (^SUPABASE_URL=http://…) qu'une paire de guillemets ferait
# échouer — un contrôle rouge pour une raison purement typographique.
if etape 8 "Bascule des trois lignes de .env"; then
  SAUVE="$BACKEND/.env.avant-bascule-$(date +%F-%H%M%S)"
  faire "cp '$BACKEND/.env' '$SAUVE'"
  info "sauvegarde : $SAUVE"
  faire "sed -i 's#^SUPABASE_URL=.*#SUPABASE_URL=http://127.0.0.1:8080#' '$BACKEND/.env'"
  faire "grep -q '^STORAGE_BACKEND=' '$BACKEND/.env' \
         && sed -i 's#^STORAGE_BACKEND=.*#STORAGE_BACKEND=local#' '$BACKEND/.env' \
         || echo 'STORAGE_BACKEND=local' >> '$BACKEND/.env'"
  faire "grep -q '^PUBLIC_BASE_URL=' '$BACKEND/.env' \
         && sed -i 's#^PUBLIC_BASE_URL=.*#PUBLIC_BASE_URL=$BASE_PUBLIQUE#' '$BACKEND/.env' \
         || echo 'PUBLIC_BASE_URL=$BASE_PUBLIQUE' >> '$BACKEND/.env'"
  # La clé de service : celle de Supabase ne vaut rien contre PostgREST local.
  # install_db.sh l'a écrite au moment de l'installation ; si elle manque, le
  # backend démarrera et répondra 401 sur tout — panne totale et muette.
  if [ "$DRY" = 0 ]; then
    grep -qE '^SUPABASE_URL=http://127\.0\.0\.1:8080$' "$BACKEND/.env" \
      && ok "SUPABASE_URL bascule sur la façade locale" || mort 8 "la réécriture de SUPABASE_URL a échoué"
    grep -qE '^STORAGE_BACKEND=local$'   "$BACKEND/.env" && ok "STORAGE_BACKEND=local"   || mort 8 "STORAGE_BACKEND non posé"
    grep -qE '^PUBLIC_BASE_URL=https://' "$BACKEND/.env" && ok "PUBLIC_BASE_URL posé"     || mort 8 "PUBLIC_BASE_URL non posé"
    grep -qi 'supabase\.co' "$BACKEND/.env" \
      && { ko "il reste une référence supabase.co dans .env :"; grep -in 'supabase\.co' "$BACKEND/.env"; \
           mort 8 "nettoie-la avant de continuer"; } \
      || ok "plus aucune référence supabase.co dans .env"
    ok "clé de service : vérifiée à l'étape 9 par le démarrage réel"
  fi
  fait etape8
fi

# ═══ 9. Déploiement, avec ses trois sondes et son rollback ══════════════════
if etape 9 "Redémarrage contrôlé (deploy.sh)"; then
  info "deploy.sh valide sur /health, /health/db ET /auth/login, et revient"
  info "automatiquement au commit précédent si l'une des trois échoue."
  faire "bash '$BACKEND/deploy.sh'"
  if [ "$DRY" = 0 ]; then
    curl -sf --max-time 5 "$API/health/db" >/dev/null \
      && ok "/health/db répond — le backend voit la base LOCALE" \
      || mort 9 "le backend ne voit pas sa base — bash $0 --rollback"
  fi
  fait etape9
fi

# ═══ 10. Contrôle final ═════════════════════════════════════════════════════
if etape 10 "Contrôle de bascule"; then
  faire "bash '$BACKEND/scripts/post_bascule_check.sh'"
  faire "bash '$BACKEND/deploy/supervision.sh'"
  fait etape10
fi

g "TERMINÉ"
cat <<'SUITE'
  Ce qui reste à faire À LA MAIN, et qu'aucun script ne doit décider :

   1. Se connecter à la plateforme avec un vrai compte. C'est le seul contrôle
      qui prouve la chaîne entière ; les sondes prouvent des morceaux.
   2. Ouvrir un CV depuis un AO — il passe désormais par /files/d/<jeton>.
   3. Laisser tourner la période d'observation (BASCULE.md §6) avant de mettre
      le projet Supabase en pause, puis de le supprimer. Le retour arrière reste
      une commande tant qu'il existe :  bash scripts/bascule.sh --rollback
   4. app_settings ne contient qu'une ligne (« notifications ») : « data_retention »
      et « ai_budget » manquent depuis toujours (BASCULE.md §0.5), donc la purge
      RGPD et la surveillance du budget IA sont inertes. À poser depuis
      l'administration, ce n'est pas un sujet de bascule.
SUITE
