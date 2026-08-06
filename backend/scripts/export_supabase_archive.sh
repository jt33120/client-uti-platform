#!/usr/bin/env bash
# =============================================================================
#  Archive HORS LIGNE du projet Supabase, à prendre AVANT sa suppression.
#
#  POURQUOI CE SCRIPT EXISTE ALORS QU'ON REPART SUR UNE BASE VIDE
#
#  « Les données sont fausses, on efface tout » est vrai des données MÉTIER
#  (consultants, AO, soumissions). Ce n'est pas vrai de trois autres choses,
#  qui n'existent qu'ici et que personne ne pourra reconstituer :
#
#   1. La CONFIGURATION calibrée à la main. `scoring_config` n'a aucun
#      équivalent dans le code : services/scoring_settings.py:34 renvoie {} si
#      la table est vide, et le moteur retombe alors sur ses propres DEFAULTS,
#      qui ne sont pas la grille réglée en production. Idem pour les modèles
#      d'e-mails personnalisés (services/email_templates.py:258-264) et pour
#      la ligne `app_settings` des notifications.
#   2. Les JOURNAUX que la conformité impose de conserver : `audit_log`
#      (AI Act art. 12, services/audit.py:1-8) et `human_decision`
#      (AI Act art. 14). Supprimer le projet supprime la seule copie.
#   3. La CORRESPONDANCE identifiant → personne. `audit_log.actor_id`,
#      `human_decision.decided_by` et `submissions.submitted_by` stockent des
#      UUID d'`auth.users`. Sans la table de correspondance, l'archive
#      conservée pour la conformité devient une suite d'UUID qui ne désignent
#      plus rien — donc inexploitable en cas de contrôle ou de contestation.
#
#  CE QUE LE SCRIPT PRODUIT
#      <dest>/AAAA-MM-JJ-HHMM/
#        ├── dump.pgcustom          pg_dump -Fc (public + auth + storage)
#        ├── dump.sql               le même en texte, relisible sans outil
#        ├── csv/<table>.csv        une par table, en-têtes inclus
#        ├── auth_users.csv         id, email, dates — SANS les empreintes
#        ├── config_replay.sql      configuration prête à rejouer sur la base neuve
#        ├── storage/<bucket>/...   tous les objets des 5 buckets
#        ├── MANIFEST.txt           versions, comptages, empreintes SHA-256
#        └── SHA256SUMS
#
#  USAGE
#      # 1. Déposer l'URI de connexion Supabase dans un fichier en 0600.
#      #    Console Supabase → Project Settings → Database → Connection string
#      #    → URI (mode « Session », port 5432).
#      install -m 600 /dev/null ~/.supabase_db_uri
#      nano ~/.supabase_db_uri          # une seule ligne : postgresql://...
#
#      bash ~/app/backend/scripts/export_supabase_archive.sh ~/archive-supabase
#
#      # Variante avec les secrets (empreintes de mots de passe, secrets TOTP) :
#      bash ~/app/backend/scripts/export_supabase_archive.sh ~/archive-supabase --with-secrets
#
#  L'URI ne passe JAMAIS en argument : la ligne de commande d'un processus est
#  lisible par tous les utilisateurs de la machine via `ps`.
# =============================================================================
set -euo pipefail

DEST="${1:?Usage: $0 <repertoire_destination> [--with-secrets]}"
WITH_SECRETS="${2:-}"
URI_FILE="${SUPABASE_URI_FILE:-$HOME/.supabase_db_uri}"

# Les 22 tables applicatives, dans l'ordre de backend/migrations/schema.sql.
# Liste EXPLICITE et non « toutes les tables du schéma » : une table qui
# apparaîtrait sans être déclarée ici doit être remarquée, pas archivée en
# silence.
TABLES=(
  ai_usage ao_consultant_state app_settings appels_offres audit_log
  client_reviews clients consultants email_outbox email_templates
  human_decision invitations matchings pac_clients pacs
  partner_clients partner_compliance_docs partner_email_log profiles
  scoring_config submissions support_messages
)

# Les cinq buckets réellement utilisés par le code. « compliance » et
# « email-assets » manquaient à l'inventaire initial : routers/partners.py:277
# et routers/email_templates.py:21 les créent à la demande, ils n'apparaissent
# donc dans aucune configuration.
BUCKETS=(cvs avatars ao-sources compliance email-assets)

[ -r "$URI_FILE" ] || {
  echo "❌ URI Supabase introuvable : $URI_FILE"
  echo "   install -m 600 /dev/null $URI_FILE && nano $URI_FILE"
  exit 2
}
PGURI="$(tr -d '\r\n' < "$URI_FILE")"
export PGCONNECT_TIMEOUT=15

command -v pg_dump >/dev/null || { echo "❌ pg_dump absent (paquet postgresql-client)"; exit 2; }

# Supabase tourne en PostgreSQL 17.6 ; pg_dump refuse de sauvegarder un serveur
# plus récent que lui. C'est la raison pour laquelle backend/deploy/INSTALLATION.md
# installe PostgreSQL 18 : le client sert aussi à archiver Supabase.
DUMP_MAJOR="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
if [ "$DUMP_MAJOR" -lt 17 ]; then
  echo "❌ pg_dump $DUMP_MAJOR est trop ancien pour Supabase (17.6) — il refusera de sauvegarder."
  echo "   Utiliser le client de PostgreSQL 18 installé sur le VPS :"
  echo "   export PATH=/usr/lib/postgresql/18/bin:\$PATH"
  exit 2
fi

STAMP="$(date +%F-%H%M)"
OUT="$DEST/$STAMP"
mkdir -p "$OUT/csv" "$OUT/storage"
chmod 700 "$DEST" "$OUT"

echo "=== Archive Supabase → $OUT ==="

# ── 1. Sauvegarde logique complète ──────────────────────────────────────────
# --no-owner / --no-privileges : les rôles Supabase (supabase_admin, anon,
# authenticated…) n'existent pas ailleurs. Sans ces options, la restauration de
# contrôle échouerait sur chaque GRANT et l'archive ne serait pas vérifiable —
# une archive qu'on ne sait pas restaurer n'est pas une archive.
echo "[1/6] pg_dump…"
pg_dump "$PGURI" --schema=public --schema=auth --schema=storage \
        --no-owner --no-privileges -Fc -f "$OUT/dump.pgcustom"
pg_dump "$PGURI" --schema=public --schema=auth --schema=storage \
        --no-owner --no-privileges -f "$OUT/dump.sql"

# ── 2. CSV par table ────────────────────────────────────────────────────────
# Le CSV est le format qui survit à tout : il se relit dans dix ans sans
# PostgreSQL. C'est lui qu'on ouvre en cas de contrôle, pas le dump binaire.
echo "[2/6] CSV des 22 tables…"
for t in "${TABLES[@]}"; do
  psql "$PGURI" -v ON_ERROR_STOP=1 -q \
    -c "\copy public.$t TO '$OUT/csv/$t.csv' WITH (FORMAT csv, HEADER true)"
  printf '  %-28s %s ligne(s)\n' "$t" \
    "$(( $(wc -l < "$OUT/csv/$t.csv") - 1 ))"
done

# ── 3. Comptes : la correspondance UUID → personne ──────────────────────────
echo "[3/6] auth.users (sans empreintes)…"
psql "$PGURI" -v ON_ERROR_STOP=1 -q -c "\copy (
  SELECT u.id, u.email, p.name, p.role, u.created_at, u.last_sign_in_at,
         u.email_confirmed_at, u.banned_until
  FROM auth.users u LEFT JOIN public.profiles p ON p.id = u.id
  ORDER BY u.created_at
) TO '$OUT/auth_users.csv' WITH (FORMAT csv, HEADER true)"

if [ "$WITH_SECRETS" = "--with-secrets" ]; then
  # Empreintes bcrypt et secrets TOTP. Fichier SÉPARÉ et en 0600 pour qu'il
  # puisse être détruit indépendamment du reste de l'archive : le reste doit
  # pouvoir être conservé des années, ceci non.
  echo "[3b/6] secrets d'authentification (fichier séparé, 0600)…"
  install -m 600 /dev/null "$OUT/auth_secrets.csv"
  psql "$PGURI" -v ON_ERROR_STOP=1 -q -c "\copy (
    SELECT u.id, u.email, u.encrypted_password, p.mfa_enabled, p.mfa_secret
    FROM auth.users u LEFT JOIN public.profiles p ON p.id = u.id
  ) TO '$OUT/auth_secrets.csv' WITH (FORMAT csv, HEADER true)"
  echo "  ⚠️  $OUT/auth_secrets.csv contient des empreintes de mots de passe et"
  echo "     des secrets TOTP EN CLAIR (profiles.mfa_secret). À détruire dès que"
  echo "     les 11 comptes ont été recréés : shred -u <fichier>"
fi

# ── 4. Configuration prête à rejouer ────────────────────────────────────────
# C'est la réponse au seul incident déjà survenu sur ce sujet : une ligne de
# configuration ABSENTE ne se voit pas, elle se comporte comme un défaut. On
# ne réécrit donc pas la configuration à la main sur la base neuve, on la
# rejoue depuis la production.
echo "[4/6] config_replay.sql…"
psql "$PGURI" -At -v ON_ERROR_STOP=1 > "$OUT/config_replay.sql" <<'SQL'
SELECT '-- Configuration extraite de la production Supabase le ' || now()::date;
SELECT '-- À rejouer sur la base neuve APRÈS schema.sql :';
SELECT '--   psql -d uti -v ON_ERROR_STOP=1 -f config_replay.sql';
SELECT '';
SELECT format(
  'INSERT INTO public.app_settings (key, value) VALUES (%L, %L::jsonb) ON CONFLICT (key) DO NOTHING;',
  key, value::text)
FROM public.app_settings ORDER BY key;
SELECT '';
SELECT format(
  'INSERT INTO public.email_templates (key, subject, body, format) VALUES (%L, %L, %L, %L) ON CONFLICT (key) DO NOTHING;',
  key, subject, body, coalesce(format, 'html'))
FROM public.email_templates ORDER BY key;
SELECT '';
-- scoring_config n'a qu'une ligne et pas de clé naturelle : on la rejoue
-- seulement si la table est vide, pour ne jamais écraser un réglage postérieur.
SELECT format(
  'INSERT INTO public.scoring_config (w_competences, w_seniorite, w_contexte, w_tjm, w_points_forts_cv, w_elements_differenciants, s_competences, s_seniorite, s_contexte, s_tjm, s_points_forts_cv, s_elements_differenciants, seniority_full_years, reco_fort_min, reco_moyen_min) SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM public.scoring_config);',
  w_competences, w_seniorite, w_contexte, w_tjm, w_points_forts_cv, w_elements_differenciants,
  s_competences, s_seniorite, s_contexte, s_tjm, s_points_forts_cv, s_elements_differenciants,
  seniority_full_years, reco_fort_min, reco_moyen_min)
FROM public.scoring_config LIMIT 1;
SQL

# ── 5. Objets stockés ───────────────────────────────────────────────────────
# Les fichiers ne sont pas dans la base : aucun pg_dump ne les emporte. Ce sont
# pourtant les pièces les plus sensibles (CV) et les plus contractuelles
# (attestations URSSAF du bucket « compliance »).
echo "[5/6] objets des 5 buckets…"
PY="${BACKEND_DIR:-$HOME/app/backend}"
if [ -x "$PY/venv/bin/python" ]; then
  ARCHIVE_DIR="$OUT/storage" BUCKETS_CSV="$(IFS=,; echo "${BUCKETS[*]}")" \
  "$PY/venv/bin/python" - <<'PY'
import os, sys, pathlib
sys.path.insert(0, os.environ.get("BACKEND_DIR", os.path.expanduser("~/app/backend")))
from services.supabase_client import supabase  # lit .env du backend

root = pathlib.Path(os.environ["ARCHIVE_DIR"])

def walk(bucket, prefix=""):
    """Parcours récursif : Supabase Storage ne liste qu'un niveau à la fois.
    Un « dossier » se reconnaît à l'absence d'id ET de metadata."""
    out = []
    for e in supabase.storage.from_(bucket).list(prefix) or []:
        child = f"{prefix}/{e['name']}" if prefix else e["name"]
        if e.get("id") is None and e.get("metadata") is None:
            out += walk(bucket, child)
        else:
            out.append(child)
    return out

for bucket in os.environ["BUCKETS_CSV"].split(","):
    try:
        paths = walk(bucket)
    except Exception as exc:
        # Un bucket jamais utilisé n'existe pas côté Supabase : ce n'est pas une
        # anomalie, il est créé à la demande au premier envoi de fichier.
        print(f"  [{bucket}] absent ou illisible ({exc}) — ignoré")
        continue
    print(f"  [{bucket}] {len(paths)} objet(s)")
    for p in paths:
        dest = root / bucket / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(supabase.storage.from_(bucket).download(p))
PY
else
  echo "  ⚠️  venv introuvable dans $PY — objets NON archivés."
  echo "     Relancer depuis le VPS, ou : BACKEND_DIR=/chemin/vers/backend $0 $DEST"
  exit 3
fi

# ── 6. Manifeste et empreintes ──────────────────────────────────────────────
echo "[6/6] manifeste…"
{
  echo "Archive du projet Supabase zeaqvlbimsstzgiabvrr"
  echo "Prise le            : $(date -Is)"
  echo "Par                 : $(whoami)@$(hostname)"
  echo "pg_dump             : $(pg_dump --version)"
  echo "Serveur PostgreSQL  : $(psql "$PGURI" -tAc 'select version()')"
  echo "Collation de la base: $(psql "$PGURI" -tAc 'select datcollate from pg_database where datname=current_database()')"
  echo "Extensions          : $(psql "$PGURI" -tAc "select string_agg(extname||' '||extversion, ', ' order by extname) from pg_extension")"
  echo ""
  echo "Lignes par table :"
  for t in "${TABLES[@]}"; do
    printf '  %-28s %s\n' "$t" "$(( $(wc -l < "$OUT/csv/$t.csv") - 1 ))"
  done
  echo ""
  echo "Objets stockés : $(find "$OUT/storage" -type f | wc -l)"
  find "$OUT/storage" -type f -printf '  %P\n' | sort
} > "$OUT/MANIFEST.txt"

( cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
chmod -R go-rwx "$OUT"

echo ""
echo "✅ Archive complète : $OUT"
echo "   Taille : $(du -sh "$OUT" | cut -f1)"
echo ""
echo "OBLIGATOIRE avant de considérer l'archive comme valide — la restaurer :"
echo "   createdb archive_test"
echo "   pg_restore --no-owner --no-privileges -d archive_test $OUT/dump.pgcustom"
echo "   psql -d archive_test -c \"select count(*) from public.audit_log\"   # attendu : 1396"
echo "   dropdb archive_test"
