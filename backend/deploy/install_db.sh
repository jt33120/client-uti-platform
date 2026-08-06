#!/usr/bin/env bash
# =============================================================================
# Installe et règle PostgreSQL + PostgREST sur le VPS OVH, en remplacement de
# Supabase. À lancer une fois, puis autant de fois qu'on veut : le script est
# idempotent (il ne régénère jamais un secret existant, ne recrée jamais un rôle
# existant, ne réécrit une configuration que si elle a changé).
#
#     sudo bash ~/app/backend/deploy/install_db.sh
#
# CE QU'IL NE FAIT PAS, VOLONTAIREMENT :
#   * il ne charge AUCUN schéma et AUCUNE donnée — c'est le chantier migration ;
#   * il n'active PAS le pare-feu : un « ufw enable » mal ordonné coupe la
#     session SSH en cours et rend le VPS injoignable. Les commandes sont
#     imprimées à la fin, à exécuter à la main, dans l'ordre ;
#   * il ne change PAS SUPABASE_URL dans .env : basculer l'URL avant que
#     l'authentification maison n'existe couperait la connexion des utilisateurs
#     (routers/auth.py:370 et :485 appellent encore /auth/v1/…).
# =============================================================================
set -euo pipefail

# ── Paramètres (surchargeables par variable d'environnement) ────────────────
PG_VERSION="${PG_VERSION:-18}"
DB_NAME="${DB_NAME:-uti}"
DB_OWNER="${DB_OWNER:-uti_admin}"
DB_COLLATE="${DB_COLLATE:-en_US.UTF-8}"
APP_USER="${APP_USER:-julian.talou}"
APP_DIR="${APP_DIR:-/home/${APP_USER}/app/backend}"

PGRST_VERSION="${PGRST_VERSION:-14.16}"
# Empreinte de l'archive officielle. Sans elle, une redirection détournée
# installerait un binaire arbitraire, lancé ensuite en service permanent.
PGRST_SHA256="36b8ae140f188cfcd6003494805bf35a41e895f88c12be9183d60f91782145c6"
PGRST_PORT="${PGRST_PORT:-3000}"
PGRST_ADMIN_PORT="${PGRST_ADMIN_PORT:-3001}"

# Port de la façade nginx interne : c'est LUI que .env désignera par SUPABASE_URL.
REST_PORT="${REST_PORT:-8080}"
DB_MAX_ROWS="${DB_MAX_ROWS:-20000}"

PG_CONF_DIR="/etc/postgresql/${PG_VERSION}/main"
PGRST_ETC="/etc/postgrest"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

etape()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info()   { printf '   %s\n' "$*"; }
alerte() { printf '   \033[33m! %s\033[0m\n' "$*"; }

[ "${EUID}" -eq 0 ] || { echo "À lancer avec sudo."; exit 1; }
[ "$(uname -m)" = "x86_64" ] || {
  echo "Le binaire PostgREST épinglé ici est x86_64 ; ce VPS est en $(uname -m)."
  echo "Prendre l'archive correspondante sur github.com/PostgREST/postgrest/releases"
  exit 1; }
id "${APP_USER}" >/dev/null 2>&1 || { echo "Utilisateur ${APP_USER} inconnu."; exit 1; }

# =============================================================================
etape "1. Mesure de la machine"
# =============================================================================
CPUS="$(nproc)"
RAM_MB="$(free -m | awk '/^Mem:/ {print $2}')"
SWAP_MB="$(free -m | awk '/^Swap:/ {print $2}')"
DISK_LIBRE_G="$(df -BG --output=avail / | tail -1 | tr -dc '0-9')"
info "vCPU               : ${CPUS}"
info "RAM                : ${RAM_MB} Mo"
info "Swap               : ${SWAP_MB} Mo"
info "Disque libre sur / : ${DISK_LIBRE_G} Go"
[ "${DISK_LIBRE_G}" -ge 5 ] || { echo "Moins de 5 Go libres sur / : arrêt."; exit 1; }

# Le dimensionnement NE suit PAS la règle habituelle « shared_buffers = 25 % de
# la RAM ». Cette base fait 16 Mo : 25 % de 4 Go lui réserveraient 1 Go de cache
# pour un jeu de données soixante fois plus petit — de la RAM prise à PyMuPDF /
# Tesseract, qui en ont réellement besoin par pics pendant l'OCR des CV.
# shared_buffers est donc dimensionné sur la TAILLE DE LA BASE, avec une marge
# de croissance large, pas sur la taille de la machine.
if   [ "${RAM_MB}" -lt 3000 ];  then PROFIL="≈2 Go";  SHARED=128MB; WORK=4MB;  MAINT=64MB;  EFF=1GB; MAXCONN=30; AUTOVAC=2; PARALLEL=0
elif [ "${RAM_MB}" -lt 6000 ];  then PROFIL="≈4 Go";  SHARED=256MB; WORK=8MB;  MAINT=128MB; EFF=2GB; MAXCONN=30; AUTOVAC=3; PARALLEL=0
elif [ "${RAM_MB}" -lt 12000 ]; then PROFIL="≈8 Go";  SHARED=512MB; WORK=16MB; MAINT=256MB; EFF=4GB; MAXCONN=40; AUTOVAC=3; PARALLEL=2
else                                 PROFIL="≥16 Go"; SHARED=1GB;   WORK=32MB; MAINT=512MB; EFF=8GB; MAXCONN=50; AUTOVAC=3; PARALLEL=2
fi
info "Profil retenu      : ${PROFIL} → shared_buffers=${SHARED}, work_mem=${WORK}, max_connections=${MAXCONN}"

# =============================================================================
etape "2. Swap et pression mémoire"
# =============================================================================
# Le swap ne sert pas à faire tourner la base dedans : il sert à ce qu'un pic
# d'OCR sur un CV volumineux se paie en lenteur plutôt qu'en processus tué.
# Sans swap, le noyau n'a pas d'autre issue que l'OOM-killer.
if [ "${SWAP_MB}" -lt 512 ]; then
  if [ ! -f /swapfile ]; then
    info "Création d'un swapfile de 2 Go"
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile   # AVANT mkswap : le swap contient de la mémoire de processus
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile 2>/dev/null || true
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
  info "Swap déjà présent (${SWAP_MB} Mo) — inchangé"
fi

cat > /etc/sysctl.d/60-uti-db.conf <<'EOF'
# swappiness bas : le swap est un filet pour les pics, pas un régime de
# croisière. Une page de shared_buffers partie au swap coûte très cher.
vm.swappiness = 10
# Garder plus longtemps le cache d'inodes : la base est petite et ses fichiers
# sont relus en permanence.
vm.vfs_cache_pressure = 50
EOF
sysctl -q --system

# =============================================================================
etape "3. PostgreSQL ${PG_VERSION}"
# =============================================================================
# POURQUOI CETTE VERSION
#
# Contrainte dure : pg_dump REFUSE de sauvegarder un serveur plus récent que
# lui. La production Supabase tourne en 17.6, donc le pg_dump installé ici doit
# être en 17 ou au-delà, faute de quoi l'archivage de Supabase avant fermeture
# s'arrête à la première commande.
#
# Sur Ubuntu 26.04, PostgreSQL 18 est le paquet NATIF de la distribution
# (18.4 au 6 août 2026). Le prendre satisfait la contrainte ci-dessus ET évite
# un dépôt tiers : les correctifs de sécurité mineurs arrivent alors par le
# canal LTS d'Ubuntu, sans rien à maintenir. Sur une machine tenue par une seule
# personne, c'est ce qui compte le plus.
#
# Le dépôt PGDG n'est ajouté QUE si la version demandée est absente des dépôts
# de la distribution — par exemple si l'on force PG_VERSION=17 pour coller
# exactement à Supabase pendant la période où les deux bases coexistent.
if dpkg -s "postgresql-${PG_VERSION}" >/dev/null 2>&1; then
  info "postgresql-${PG_VERSION} déjà installé"
elif apt-cache show "postgresql-${PG_VERSION}" >/dev/null 2>&1; then
  info "postgresql-${PG_VERSION} disponible dans les dépôts de la distribution"
  apt-get update -qq
  apt-get install -y -qq "postgresql-${PG_VERSION}" "postgresql-client-${PG_VERSION}"
else
  alerte "postgresql-${PG_VERSION} absent des dépôts Ubuntu — ajout du dépôt PGDG"
  apt-get update -qq
  apt-get install -y -qq postgresql-common
  # Dépôt au format deb822 signé par la clé livrée AVEC postgresql-common :
  # aucune clé n'est récupérée sur le réseau au moment de l'installation.
  . /etc/os-release
  cat > /etc/apt/sources.list.d/pgdg.sources <<EOF
Types: deb
URIs: https://apt.postgresql.org/pub/repos/apt
Suites: ${VERSION_CODENAME}-pgdg
Components: main
Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
EOF
  apt-get update -qq
  apt-get install -y -qq "postgresql-${PG_VERSION}" "postgresql-client-${PG_VERSION}"
fi
info "$(/usr/lib/postgresql/${PG_VERSION}/bin/pg_dump --version)"

# =============================================================================
etape "4. Réglages PostgreSQL"
# =============================================================================
install -d -m 0755 "${PG_CONF_DIR}/conf.d"
CONF="${PG_CONF_DIR}/conf.d/10-uti.conf"
CONF_TMP="$(mktemp)"
cat > "${CONF_TMP}" <<EOF
# Fichier produit par backend/deploy/install_db.sh — ne pas éditer à la main, la
# prochaine exécution l'écraserait. Pour surcharger un réglage, créer
# ${PG_CONF_DIR}/conf.d/20-local.conf (chargé après celui-ci).

# ── Écoute ────────────────────────────────────────────────────────────────
# Rien depuis l'extérieur. PostgREST et FastAPI sont sur cette machine et
# passent par la socket Unix ; « localhost » n'est gardé que pour permettre un
# tunnel SSH d'administration (ssh -L 5432:127.0.0.1:5432 …).
listen_addresses = 'localhost'
port = 5432

# ── Mémoire — profil ${PROFIL}, pour une base de 16 Mo ────────────────────
shared_buffers = ${SHARED}
work_mem = ${WORK}
maintenance_work_mem = ${MAINT}
effective_cache_size = ${EFF}
# max_connections borne le pire cas mémoire (max_connections × work_mem). Le
# besoin réel est faible : PostgREST n'ouvre au plus que db-pool connexions, et
# les routes FastAPI appellent .execute() de façon BLOQUANTE dans des handlers
# « async def » (routers/consultants.py:112-124), ce qui sérialise de fait les
# accès base sur la boucle d'événements.
max_connections = ${MAXCONN}
autovacuum_max_workers = ${AUTOVAC}
max_parallel_workers_per_gather = ${PARALLEL}

# ── Disque (SSD/NVMe OVH) ─────────────────────────────────────────────────
random_page_cost = 1.1
effective_io_concurrency = 200
wal_compression = on
checkpoint_completion_target = 0.9
max_wal_size = 1GB
min_wal_size = 128MB
# La base devient le système de référence : on ne troque pas la durabilité
# contre de la vitesse sur un jeu de données de cette taille.
synchronous_commit = on

# ── Langue des messages — RÉGLAGE FONCTIONNEL, PAS COSMÉTIQUE ─────────────
# Le backend décide de son comportement en LISANT le texte des erreurs
# PostgreSQL : routers/auth.py:287 (« relation » + « does not exist »), :290
# (« violates foreign key »), :293 (« violates unique constraint »), :296
# (« permission denied »), routers/client_review.py:175 (« does not exist »).
# Le paquet postgresql-${PG_VERSION} embarque les traductions françaises : sur un VPS
# réglé en fr_FR, ces messages arriveraient en français et TOUS ces tests
# deviendraient faux — sans aucune erreur visible, juste des cas particuliers
# qui cessent d'être reconnus. « C » fige l'anglais.
# Sans effet sur le tri : la collation est portée par la base (voir étape 5).
lc_messages = 'C'
timezone = 'UTC'

# ── Journalisation et supervision ─────────────────────────────────────────
log_min_duration_statement = 500ms
log_line_prefix = '%m [%p] %q%u@%d '
log_checkpoints = on
log_autovacuum_min_duration = 0
track_io_timing = on
shared_preload_libraries = 'pg_stat_statements'
EOF

PG_RESTART=0
if ! cmp -s "${CONF_TMP}" "${CONF}" 2>/dev/null; then
  mv "${CONF_TMP}" "${CONF}"; chmod 644 "${CONF}"
  info "Réglages écrits dans ${CONF} → redémarrage nécessaire"
  PG_RESTART=1
else
  rm -f "${CONF_TMP}"; info "Réglages inchangés"
fi

# ── pg_hba.conf / pg_ident.conf ────────────────────────────────────────────
# Authentification « peer » : PostgreSQL fait confiance à l'identité UNIX de
# l'appelant, vérifiée par le noyau. Conséquence directe : il n'existe AUCUN mot
# de passe de base de données sur cette machine — donc rien à stocker, à
# protéger ni à faire tourner. La correspondance compte UNIX → rôle SQL est
# écrite noir sur blanc dans pg_ident.conf.
[ -f "${PG_CONF_DIR}/pg_hba.conf.avant-uti" ] || cp -a "${PG_CONF_DIR}/pg_hba.conf" "${PG_CONF_DIR}/pg_hba.conf.avant-uti"
cat > "${PG_CONF_DIR}/pg_hba.conf" <<EOF
# Fichier géré par backend/deploy/install_db.sh.
# Original conservé dans pg_hba.conf.avant-uti
# TYPE  DATABASE     UTILISATEUR   ADRESSE        MÉTHODE
local   all          postgres                     peer
local   replication  postgres                     peer
# Tout autre accès local doit être nommément déclaré dans pg_ident.conf : un
# compte UNIX absent de cette table ne peut se connecter à AUCUN rôle.
local   all          all                          peer map=uti
# TCP réservé à un tunnel SSH d'administration. Aucun rôle n'ayant de mot de
# passe aujourd'hui, ces lignes ne servent encore personne — elles évitent
# d'avoir à rouvrir ce fichier le jour où l'on en aura besoin.
host    all          all           127.0.0.1/32   scram-sha-256
host    all          all           ::1/128        scram-sha-256
EOF
chown postgres:postgres "${PG_CONF_DIR}/pg_hba.conf"; chmod 640 "${PG_CONF_DIR}/pg_hba.conf"

cat > "${PG_CONF_DIR}/pg_ident.conf" <<EOF
# Fichier géré par backend/deploy/install_db.sh.
# MAP   COMPTE UNIX        RÔLE POSTGRESQL
uti     "postgrest"        authenticator
uti     "${APP_USER}"      ${DB_OWNER}
# Le compte postgres reste superuser via la ligne « peer » SANS map de pg_hba.
EOF
chown postgres:postgres "${PG_CONF_DIR}/pg_ident.conf"; chmod 640 "${PG_CONF_DIR}/pg_ident.conf"

# ── OOM-killer : protéger le postmaster, pas ses enfants ───────────────────
# Si le noyau manque de mémoire et choisit le postmaster, TOUT le cluster tombe.
# On le rend quasi intuable (-900). Mais oom_score_adj est HÉRITÉ au fork : sans
# précaution, on protégerait aussi les backends, alors qu'eux peuvent être
# sacrifiés sans perdre le cluster. Les deux variables PG_OOM_ADJUST_* disent à
# PostgreSQL de remettre le compteur à zéro dans chaque processus fils.
install -d -m 0755 "/etc/systemd/system/postgresql@${PG_VERSION}-main.service.d"
cat > "/etc/systemd/system/postgresql@${PG_VERSION}-main.service.d/10-oom.conf" <<'EOF'
[Service]
OOMScoreAdjust=-900
Environment=PG_OOM_ADJUST_FILE=/proc/self/oom_score_adj
Environment=PG_OOM_ADJUST_VALUE=0
EOF

# ── Plafond mémoire du backend FastAPI ─────────────────────────────────────
# La vraie protection de la base n'est pas de blinder la base : c'est de borner
# le processus glouton. L'OCR d'un CV (PyMuPDF + Tesseract + Pillow) est le seul
# consommateur capable de doubler son empreinte en quelques secondes. Bridé ici,
# c'est LUI que le noyau arrête, et systemd le relance (Restart=always,
# OOMPolicy=continue, uti-backend.service:29-35). Posé en drop-in : le fichier
# du dépôt reste valable pour n'importe quelle machine.
BACKEND_MAX=$(( RAM_MB * 45 / 100 ))
BACKEND_HIGH=$(( RAM_MB * 35 / 100 ))
install -d -m 0755 /etc/systemd/system/uti-backend.service.d
cat > /etc/systemd/system/uti-backend.service.d/10-memoire.conf <<EOF
[Service]
MemoryHigh=${BACKEND_HIGH}M
MemoryMax=${BACKEND_MAX}M
EOF
info "Backend FastAPI plafonné à ${BACKEND_MAX} Mo (seuil doux ${BACKEND_HIGH} Mo)"

systemctl daemon-reload
systemctl enable -q "postgresql@${PG_VERSION}-main"
if [ "${PG_RESTART}" = "1" ]; then
  systemctl restart "postgresql@${PG_VERSION}-main"
else
  systemctl reload-or-restart "postgresql@${PG_VERSION}-main"
fi
for _ in $(seq 1 30); do pg_isready -q && break; sleep 1; done
pg_isready || { echo "PostgreSQL ne répond pas — journalctl -u postgresql@${PG_VERSION}-main"; exit 1; }

# =============================================================================
etape "5. Base, propriétaire et rôles PostgREST"
# =============================================================================
# NB : « sudo -u postgres psql -f » échouerait sur un fichier situé sous
# /home/${APP_USER} (répertoire personnel en 0750). Le SQL est donc passé par
# l'entrée standard, ouverte par root avant le changement d'utilisateur.
psql_su() { sudo -u postgres psql -v ON_ERROR_STOP=1 -q "$@"; }

psql_su -d postgres -tAc "select 1 from pg_roles where rolname='${DB_OWNER}'" | grep -q 1 || {
  # CREATEDB parce que scripts/check_schema_drift.py crée une base jetable
  # (check_schema_drift.py:119). Rien de plus : le propriétaire du schéma
  # applicatif n'a aucune raison de pouvoir toucher au reste du cluster.
  info "Création du rôle ${DB_OWNER} (LOGIN, CREATEDB, PAS superuser)"
  psql_su -d postgres -c "create role \"${DB_OWNER}\" login createdb"
}

psql_su -d postgres -tAc "select 1 from pg_database where datname='${DB_NAME}'" | grep -q 1 || {
  # La collation doit être CELLE DE LA PRODUCTION, sinon « ORDER BY name » ne
  # classe plus les accents pareil. À relever côté Supabase avant de lancer :
  #   select datcollate from pg_database where datname = current_database();
  # puis relancer avec DB_COLLATE=… si ce n'est pas en_US.UTF-8.
  info "Création de la base ${DB_NAME} (collation ${DB_COLLATE})"
  locale -a | grep -qiF "$(echo "${DB_COLLATE}" | tr -d '-' | tr 'A-Z' 'a-z')" || {
    info "Génération de la locale ${DB_COLLATE}"
    sed -i "s/^# *${DB_COLLATE}/${DB_COLLATE}/" /etc/locale.gen 2>/dev/null || true
    locale-gen "${DB_COLLATE}" >/dev/null 2>&1 || true
  }
  psql_su -d postgres -c "create database \"${DB_NAME}\" owner \"${DB_OWNER}\" \
      encoding 'UTF8' lc_collate '${DB_COLLATE}' lc_ctype '${DB_COLLATE}' template template0"
}

# Extensions relevées en production : pgcrypto, pg_trgm, uuid-ossp,
# pg_stat_statements, plpgsql (implicite). supabase_vault est écarté :
# vault.secrets est vide, donc inutilisé.
for ext in pgcrypto pg_trgm "uuid-ossp" pg_stat_statements; do
  psql_su -d "${DB_NAME}" -c "create extension if not exists \"${ext}\""
done

psql_su -d "${DB_NAME}" -v owner="${DB_OWNER}" < "${HERE}/roles_postgrest.sql"

# =============================================================================
etape "6. PostgREST ${PGRST_VERSION}"
# =============================================================================
id postgrest >/dev/null 2>&1 || {
  info "Création du compte système postgrest (sans shell, sans répertoire personnel)"
  # --user-group : garantit l'existence du groupe « postgrest », dont dépendent
  # le Group= de l'unité systemd et le mode 640 root:postgrest du secret JWT.
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin postgrest
}

if ! /usr/local/bin/postgrest --version 2>/dev/null | grep -q "${PGRST_VERSION}"; then
  ARCHIVE="$(mktemp -d)/postgrest.tar.xz"
  info "Téléchargement du binaire statique v${PGRST_VERSION}"
  curl -fsSL -o "${ARCHIVE}" \
    "https://github.com/PostgREST/postgrest/releases/download/v${PGRST_VERSION}/postgrest-v${PGRST_VERSION}-linux-static-x86-64.tar.xz"
  echo "${PGRST_SHA256}  ${ARCHIVE}" | sha256sum -c - >/dev/null || {
    echo "Empreinte SHA-256 incorrecte : archive NON installée."; rm -rf "$(dirname "${ARCHIVE}")"; exit 1; }
  tar -xJf "${ARCHIVE}" -C /usr/local/bin postgrest
  chown root:root /usr/local/bin/postgrest; chmod 755 /usr/local/bin/postgrest
  rm -rf "$(dirname "${ARCHIVE}")"
fi
info "$(/usr/local/bin/postgrest --version)"

# ── Secret JWT ─────────────────────────────────────────────────────────────
# Fichier séparé, lisible du seul compte postgrest, jamais dans la configuration
# ni dans la ligne de commande. PostgREST le charge via « @chemin » et RETIRE
# LES BLANCS DE FIN : le saut de ligne d'openssl est donc sans conséquence — à
# condition que make_service_key.py fasse le même .strip(), ce qu'il fait.
install -d -m 0750 -o root -g postgrest "${PGRST_ETC}"
if [ ! -f "${PGRST_ETC}/jwt.secret" ]; then
  info "Génération du secret JWT PostgREST"
  ( umask 077; openssl rand -hex 32 > "${PGRST_ETC}/jwt.secret" )
  chown root:postgrest "${PGRST_ETC}/jwt.secret"; chmod 640 "${PGRST_ETC}/jwt.secret"
else
  alerte "Secret JWT déjà présent — CONSERVÉ (le régénérer invaliderait la clé de service en place)"
fi

cat > "${PGRST_ETC}/postgrest.conf" <<EOF
## Configuration PostgREST — produite par backend/deploy/install_db.sh.
## Ne pas éditer à la main : la prochaine exécution du script l'écrase.

## ── Base ──────────────────────────────────────────────────────────────────
## Socket Unix + authentification peer : il n'existe aucun mot de passe de base
## de données sur cette machine, donc rien à stocker ni à faire tourner.
db-uri = "postgresql:///${DB_NAME}?host=/var/run/postgresql&user=authenticator"
db-schemas = "public"
db-extra-search-path = "public"

## db-anon-role est VOLONTAIREMENT ABSENT : non défini, PostgREST refuse en 401
## toute requête sans jeton valide au lieu de la jouer avec un rôle anonyme.
## C'est l'inverse du réglage Supabase, et c'est ce que l'on veut — le frontend
## ne parle jamais à la base, aucun accès anonyme n'a de raison d'exister.

## Pool volontairement petit : voir le commentaire de max_connections dans
## ${PG_CONF_DIR}/conf.d/10-uti.conf.
db-pool = 10
db-pool-acquisition-timeout = 10

## Plafond de lignes par réponse. ATTENTION : la troncature est SILENCIEUSE.
## PostgREST répond 200 avec « Content-Range: 0-N/* » et supabase-py ne lève
## rien (postgrest/_sync/request_builder.py:66 accepte tout code 2xx). Un
## plafond trop bas dégraderait donc le produit sans laisser de trace : la
## valeur est choisie très au-dessus de la plus grosse table (audit_log,
## 1396 lignes) pour ne jouer que le rôle de garde-fou anti-requête folle.
db-max-rows = ${DB_MAX_ROWS}

## ── Jetons ────────────────────────────────────────────────────────────────
jwt-secret = "@${PGRST_ETC}/jwt.secret"
jwt-secret-is-base64 = false
jwt-role-claim-key = ".role"

## ── Écoute ────────────────────────────────────────────────────────────────
## LIGNE À NE JAMAIS RETIRER. Le défaut de PostgREST est server-host = "!4",
## c'est-à-dire 0.0.0.0 : sans elle, PostgREST écoute sur l'interface PUBLIQUE
## du VPS et la base entière devient joignable depuis Internet, protégée par le
## seul jeton. Contrôle depuis une AUTRE machine : « nc -z IP ${PGRST_PORT} » doit échouer.
server-host = "127.0.0.1"
server-port = ${PGRST_PORT}

## Serveur d'administration : /live, /ready et /metrics, sans authentification,
## sur la même interface locale. C'est la sonde utilisée par deploy.sh.
admin-server-port = ${PGRST_ADMIN_PORT}

log-level = "error"
db-config = false
EOF
chown root:postgrest "${PGRST_ETC}/postgrest.conf"; chmod 640 "${PGRST_ETC}/postgrest.conf"

install -m 0644 "${HERE}/postgrest.service" /etc/systemd/system/postgrest.service
systemctl daemon-reload
systemctl enable -q postgrest
systemctl restart postgrest
for _ in $(seq 1 20); do
  curl -sf --max-time 2 "http://127.0.0.1:${PGRST_ADMIN_PORT}/ready" >/dev/null && break
  sleep 1
done
curl -sf --max-time 2 "http://127.0.0.1:${PGRST_ADMIN_PORT}/ready" >/dev/null || {
  echo "PostgREST ne devient pas prêt — sudo journalctl -u postgrest -n 50"; exit 1; }
info "PostgREST prêt sur 127.0.0.1:${PGRST_PORT}"

# =============================================================================
etape "7. Façade nginx interne (/rest/v1/)"
# =============================================================================
command -v nginx >/dev/null || { echo "nginx absent : sudo apt install nginx"; exit 1; }
sed -e "s/127\.0\.0\.1:8080/127.0.0.1:${REST_PORT}/" \
    -e "s#http://127\.0\.0\.1:3000/#http://127.0.0.1:${PGRST_PORT}/#" \
    "${HERE}/nginx-postgrest.conf" > /etc/nginx/sites-available/uti-postgrest
ln -sf /etc/nginx/sites-available/uti-postgrest /etc/nginx/sites-enabled/uti-postgrest
nginx -t && systemctl reload nginx

# =============================================================================
etape "8. Clé de service et .env"
# =============================================================================
KEY_FILE="${PGRST_ETC}/service_key.txt"
if [ ! -f "${KEY_FILE}" ]; then
  python3 "${HERE}/../scripts/make_service_key.py" \
    --secret-file "${PGRST_ETC}/jwt.secret" --out "${KEY_FILE}"
else
  alerte "Clé de service déjà générée — CONSERVÉE (${KEY_FILE})"
fi
KEY="$(cat "${KEY_FILE}")"

ENV_FILE="${APP_DIR}/.env"
if [ -f "${ENV_FILE}" ]; then
  cp -a "${ENV_FILE}" "${ENV_FILE}.avant-$(date +%Y%m%d%H%M%S)"
  # On remplace la CLÉ, pas l'URL : basculer SUPABASE_URL maintenant couperait
  # la connexion des utilisateurs tant que l'authentification maison n'est pas
  # déployée. L'URL se change à la bascule, en une seule ligne.
  ( umask 077; grep -v '^SUPABASE_SERVICE_KEY=' "${ENV_FILE}" > "${ENV_FILE}.tmp"
    echo "SUPABASE_SERVICE_KEY=${KEY}" >> "${ENV_FILE}.tmp" )
  mv "${ENV_FILE}.tmp" "${ENV_FILE}"
  chown "${APP_USER}:$(id -gn "${APP_USER}")" "${ENV_FILE}"; chmod 600 "${ENV_FILE}"
  info "SUPABASE_SERVICE_KEY remplacée dans ${ENV_FILE} (sauvegarde .avant-*)"
  # Les deux secrets HS256 de la machine ne doivent JAMAIS coïncider : le jeton
  # de session que le backend délivre à chaque utilisateur contient déjà une
  # revendication « role » (routers/auth.py:75-83). Secrets identiques = le
  # navigateur de n'importe quel utilisateur détient un jeton valide pour
  # PostgREST, et il suffirait qu'un rôle SQL porte le même nom qu'un rôle
  # applicatif pour transformer cela en accès SQL direct.
  APP_JWT="$(sed -n 's/^JWT_SECRET=//p' "${ENV_FILE}" | head -1)"
  [ "${APP_JWT}" = "$(cat "${PGRST_ETC}/jwt.secret")" ] &&
    alerte "DANGER : JWT_SECRET est IDENTIQUE au secret PostgREST. En changer un."
else
  alerte "${ENV_FILE} absent — clé à recopier depuis ${KEY_FILE}"
fi

# =============================================================================
etape "9. Contrôles"
# =============================================================================
ECHECS=0
ok() { printf '   \033[32m✓\033[0m %s\n' "$*"; }
ko() { printf '   \033[31m✗ %s\033[0m\n' "$*"; ECHECS=1; }

[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${REST_PORT}/rest/v1/")" = "401" ] \
  && ok "requête sans jeton → 401" || ko "une requête sans jeton n'est PAS refusée"
[ "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${KEY}" \
      "http://127.0.0.1:${REST_PORT}/rest/v1/")" = "200" ] \
  && ok "clé de service acceptée" || ko "la clé de service est REFUSÉE par PostgREST"
ss -ltn | grep -qE "(0\.0\.0\.0|\[::\]):${PGRST_PORT}\b" \
  && ko "PostgREST écoute sur TOUTES les interfaces" || ok "PostgREST : boucle locale seulement"
ss -ltn | grep -qE "(0\.0\.0\.0|\[::\]):${REST_PORT}\b" \
  && ko "la façade nginx écoute sur TOUTES les interfaces" || ok "façade nginx : boucle locale seulement"
ss -ltn | grep -qE "(0\.0\.0\.0|\[::\]):5432\b" \
  && ko "PostgreSQL écoute sur TOUTES les interfaces" || ok "PostgreSQL : boucle locale seulement"
[ "$(sudo -u postgres psql -tAd "${DB_NAME}" -c 'show lc_messages')" = "C" ] \
  && ok "lc_messages = C (erreurs en anglais)" || ko "lc_messages n'est pas C"
sudo -u postgres psql -tAd "${DB_NAME}" -c \
  "select rolbypassrls from pg_roles where rolname='service_role'" | grep -q '^t$' \
  && ok "service_role contourne la RLS" || ko "service_role ne contourne PAS la RLS"
sudo -u postgres psql -tAd "${DB_NAME}" -c \
  "select count(*) from pg_event_trigger where evtname='pgrst_watch_ddl'" | grep -q '^1$' \
  && ok "rechargement automatique du cache de schéma armé" || ko "déclencheur pgrst_watch_ddl absent"

echo
[ "${ECHECS}" = "0" ] \
  && printf '\033[32mInstallation terminée.\033[0m\n' \
  || printf '\033[31mDes contrôles ont échoué — ne rien basculer.\033[0m\n'

cat <<EOF

À FAIRE À LA MAIN, DANS CET ORDRE (le script ne touche pas au pare-feu) :

  sudo ufw allow OpenSSH          # AVANT tout le reste, sinon perte du SSH
  sudo ufw allow 'Nginx Full'
  sudo ufw enable
  sudo ufw status verbose

Puis DEPUIS UNE AUTRE MACHINE, vérifier qu'aucun de ces ports ne répond :

  for p in 5432 ${PGRST_PORT} ${PGRST_ADMIN_PORT} ${REST_PORT}; do
    nc -z -w3 164.132.44.212 \$p && echo "\$p OUVERT — À CORRIGER" || echo "\$p fermé (attendu)"
  done

Le schéma et les données ne sont PAS chargés : c'est l'objet du chantier
migration. Tant que .env porte encore l'URL Supabase, la production est intacte.
EOF
exit "${ECHECS}"
