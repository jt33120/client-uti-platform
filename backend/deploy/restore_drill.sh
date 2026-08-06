#!/usr/bin/env bash
# =============================================================================
#  Répétition de restauration — le script qui transforme un fichier en sauvegarde.
#
#  POURQUOI CE FICHIER EXISTE
#  backup_db.sh vérifie qu'une archive est LISIBLE (pg_restore --list). Ce n'est
#  pas la même chose que RESTAURABLE : une archive parfaitement lisible peut
#  rejouer avec des erreurs, perdre des tables, ou contenir un schéma sans ses
#  données. La seule preuve qu'une sauvegarde en est une, c'est de la restaurer.
#  Tant que personne ne l'a fait, on possède un fichier et une croyance.
#  C'est la troisième condition posée pour supprimer le projet Supabase.
#
#  CE QUE ÇA FAIT
#   1. crée une base JETABLE, dont le nom contient obligatoirement « _drill_ » ;
#   2. y rejoue la dernière archive avec --exit-on-error (une erreur = un échec,
#      pas un avertissement qu'on relit trois semaines plus tard) ;
#   3. compte les lignes de CHAQUE table des deux côtés et compare ;
#   4. supprime la base jetable, quoi qu'il arrive (trap) ;
#   5. CRIE si quoi que ce soit diverge.
#
#  CE QU'IL NE FAIT JAMAIS
#  Toucher à la base vivante. Trois garde-fous indépendants ci-dessous, parce
#  qu'un script de restauration qui se trompe de cible est la seule chose au
#  monde qui soit pire que pas de sauvegarde du tout.
#
#  DEUX MODES
#   (défaut)    rejoue la dernière archive LOCALE en clair (/var/backups/uti).
#               Tourne tout seul chaque semaine (uti-restore-drill.timer). Ne
#               demande AUCUN secret : c'est pour cela qu'il peut être
#               automatique.
#   --hors-site rejoue le dernier objet DÉPOSÉ CHEZ OVH : téléchargement,
#               déchiffrement age, restauration. Prouve la chaîne complète, y
#               compris que la clé privée ouvre bien les archives. Exige
#               AGE_IDENTITY, donc NE tourne PAS tout seul : à lancer à la main,
#               une fois à l'installation puis chaque trimestre, depuis un poste
#               qui détient la clé. Voir RUNBOOK.md §10.
#
#  USAGE
#      bash ~/app/backend/deploy/restore_drill.sh
#      AGE_IDENTITY=/media/cle-usb/uti-backup.age-key \
#        bash ~/app/backend/deploy/restore_drill.sh --hors-site
#      echo $?     # 0 = la sauvegarde est restaurable, 1 = elle ne l'est pas
# =============================================================================
set -uo pipefail

DEST="${BACKUP_DIR:-/var/backups/uti}"
BASE="${PGDATABASE:-uti}"
BACKEND="${BACKEND_DIR:-/home/julian.talou/app/backend}"
MODE="${1:-local}"

# shellcheck source=./lib_alerte.sh disable=SC1091
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib_alerte.sh" 2>/dev/null \
  || . /usr/local/lib/uti-lib_alerte.sh

alerte() { crie "répétition de restauration" "$1"; }

TRAVAIL=$(mktemp -d /tmp/uti-drill.XXXXXX) || alerte "mktemp -d a échoué"

# ── Garde-fou n°1 : le nom de la cible ──────────────────────────────────────
# « _drill_ » dans le nom, plus le PID et l'horodatage pour qu'aucune exécution
# concurrente ne se marche dessus.
CIBLE="uti_drill_$$_$(date -u +%s)"

# ── Garde-fou n°2 : refus explicite ─────────────────────────────────────────
# Ceinture ET bretelles : si un jour quelqu'un rend CIBLE configurable, ce test
# est ce qui l'empêchera de pointer sur la production.
case "$CIBLE" in
  *_drill_*) : ;;
  *) alerte "la base cible « $CIBLE » ne porte pas le marqueur _drill_ — refus" ;;
esac
[ "$CIBLE" != "$BASE" ] || alerte "la base cible est la base VIVANTE — refus absolu"

# ── Garde-fou n°3 : ménage systématique ─────────────────────────────────────
# Sans ce trap, un échec au milieu laisserait une base orpheline par exécution :
# au bout de quelques mois, le disque se remplit — et un disque plein corrompt
# le VPS entier, ce que la supervision surveille précisément.
nettoyer() {
  psql -d postgres -qc "DROP DATABASE IF EXISTS \"$CIBLE\" WITH (FORCE);" >/dev/null 2>&1
  rm -rf "$TRAVAIL"
}
trap nettoyer EXIT

# ── Choix de l'archive ──────────────────────────────────────────────────────
if [ "$MODE" = "--hors-site" ]; then
  [ -n "${AGE_IDENTITY:-}" ] || alerte "--hors-site exige AGE_IDENTITY (chemin du fichier de clé PRIVÉE)"
  [ -r "$AGE_IDENTITY" ] || alerte "clé privée illisible : $AGE_IDENTITY"
  command -v age >/dev/null || alerte "'age' n'est pas installé (apt install age)"

  # Les objets sont nommés en UTC ISO-8601 : le dernier au sens alphabétique est
  # le plus récent au sens chronologique (voir s3_backup.py:lister).
  CLE_S3=$("$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" lister "uti/" | tail -1) \
    || alerte "impossible de lister le conteneur hors-site"
  [ -n "$CLE_S3" ] || alerte "le conteneur hors-site est VIDE — rien n'y a jamais été déposé"

  "$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" recuperer "$CLE_S3" "$TRAVAIL/archive.age" >/dev/null \
    || alerte "téléchargement de $CLE_S3 impossible"
  age -d -i "$AGE_IDENTITY" -o "$TRAVAIL/archive.pgcustom" "$TRAVAIL/archive.age" \
    || alerte "DÉCHIFFREMENT IMPOSSIBLE de $CLE_S3 — la clé privée ne correspond pas aux archives déposées"
  ARCHIVE="$TRAVAIL/archive.pgcustom"
  ORIGINE="$CLE_S3 (hors-site, déchiffré)"
else
  ARCHIVE=$(find "$DEST" -maxdepth 1 -name 'uti-*.pgcustom' -type f -printf '%f\n' 2>/dev/null | sort | tail -1)
  [ -n "$ARCHIVE" ] || alerte "aucune archive dans $DEST — la sauvegarde ne tourne pas"
  ARCHIVE="$DEST/$ARCHIVE"
  ORIGINE="$ARCHIVE (local)"

  # Une archive de plus de 26 h veut dire que la sauvegarde horaire est morte
  # depuis un jour. Répéter une restauration sur une archive périmée validerait
  # une sauvegarde… qui n'existe plus.
  age_min=$(( ( $(date -u +%s) - $(stat -c%Y "$ARCHIVE") ) / 60 ))
  [ "$age_min" -le 1560 ] || alerte "l'archive la plus récente a $((age_min/60)) h — la sauvegarde ne tourne plus"
fi

echo "[répétition] archive : $ORIGINE"

# ── Restauration dans la base jetable ───────────────────────────────────────
# TEMPLATE template0 : template1 peut contenir des objets ajoutés localement,
# qui feraient échouer la restauration pour une raison sans rapport.
psql -d postgres -qc "CREATE DATABASE \"$CIBLE\" TEMPLATE template0 ENCODING 'UTF8';" \
  || alerte "création de la base jetable $CIBLE impossible"

# --exit-on-error : sans lui, pg_restore signale les erreurs et rend 0. On
# validerait des restaurations partielles pendant des mois.
# --no-owner / --no-privileges : les rôles sont des objets de CLUSTER
# (deploy/roles_postgrest.sql), pas de base ; les réclamer ici ferait échouer la
# répétition pour un motif qui n'a rien à voir avec l'intégrité des données.
journal_restore="$TRAVAIL/pg_restore.log"
if ! pg_restore --exit-on-error --no-owner --no-privileges \
      -d "$CIBLE" "$ARCHIVE" > "$journal_restore" 2>&1; then
  alerte "pg_restore a ÉCHOUÉ sur $ORIGINE :
$(tail -20 "$journal_restore")"
fi

# ── Comparaison des lignes, table par table ─────────────────────────────────
# query_to_xml permet de compter TOUTES les tables en une requête, sans générer
# de SQL depuis bash. Compter seulement quelques tables choisies à la main
# laisserait passer la perte d'une table dont on n'aurait pas pensé à parler —
# et c'est toujours celle-là.
requete_comptes="
SELECT c.relname || '|' || (
         xpath('/row/c/text()',
               query_to_xml(format('select count(*) as c from public.%I', c.relname),
                            false, true, ''))
       )[1]::text
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname;"

psql -d "$BASE"  -tA -c "$requete_comptes" | sed '/^$/d' > "$TRAVAIL/vivante.txt" \
  || alerte "impossible de compter les lignes de la base vivante"
psql -d "$CIBLE" -tA -c "$requete_comptes" | sed '/^$/d' > "$TRAVAIL/restauree.txt" \
  || alerte "impossible de compter les lignes de la base restaurée"

nb_vivante=$(wc -l < "$TRAVAIL/vivante.txt")
nb_restauree=$(wc -l < "$TRAVAIL/restauree.txt")
[ "$nb_restauree" -gt 0 ] || alerte "la base restaurée ne contient AUCUNE table — dump vide"

# Les tables présentes des deux côtés doivent être les mêmes. Une table absente
# de la restauration est une table perdue, quelles que soient les lignes des
# autres.
manquantes=$(comm -23 <(cut -d'|' -f1 "$TRAVAIL/vivante.txt"   | sort) \
                      <(cut -d'|' -f1 "$TRAVAIL/restauree.txt" | sort) | tr '\n' ' ')
[ -z "$manquantes" ] || alerte "table(s) absente(s) de la restauration : $manquantes"

# Comparaison ligne à ligne.
#  * restauré > vivant  → la base VIVANTE a perdu des lignes depuis le dump.
#    C'est peut-être une purge RGPD légitime (services/data_retention.py), donc
#    on prévient sans faire échouer — mais on le DIT, parce que c'est aussi la
#    signature d'un DELETE accidentel qu'on a encore le temps de rattraper.
#  * restauré < vivant  → normal : le dump est une photo d'il y a jusqu'à 1 h.
#    Au-delà de 10 % (et de 20 lignes, pour ne pas hurler sur les petites
#    tables), l'écart n'est plus imputable au décalage : des données manquent.
#  * vivant > 0 et restauré = 0 → une table vidée : la panne la plus grave et
#    la plus discrète, puisque la restauration « réussit ».
ecarts=""
avertissements=""
while IFS='|' read -r table n_vivant; do
  n_restaure=$(grep -m1 "^$table|" "$TRAVAIL/restauree.txt" | cut -d'|' -f2)
  n_restaure=${n_restaure:-0}
  if [ "$n_vivant" -gt 0 ] && [ "$n_restaure" -eq 0 ]; then
    ecarts="$ecarts\n  * $table : VIDE dans la restauration, $n_vivant ligne(s) en base"
    continue
  fi
  if [ "$n_restaure" -gt "$n_vivant" ]; then
    avertissements="$avertissements\n  * $table : $n_restaure dans la sauvegarde > $n_vivant en base (purge légitime, ou suppression accidentelle à rattraper)"
    continue
  fi
  perte=$(( n_vivant - n_restaure ))
  seuil=$(( n_vivant / 10 ))
  [ "$seuil" -lt 20 ] && seuil=20
  [ "$perte" -le "$seuil" ] || \
    ecarts="$ecarts\n  * $table : $n_restaure restaurées contre $n_vivant en base (-$perte, au-delà du décalage attendu)"
done < "$TRAVAIL/vivante.txt"

# ── Contrôles de contenu, pas seulement de volume ───────────────────────────
# Des comptes justes sur des colonnes vides passeraient tous les tests ci-dessus.
# On vérifie donc que les données servant à SE CONNECTER ont bien survécu : sans
# elles, la base restaurée est complète et personne ne peut y entrer — panne
# qu'aucun comptage ne révèle. La table vient de migrations/0019_auth_maison.sql:66.
sans_secret=$(psql -d "$CIBLE" -tA -c \
  "SELECT count(*) FROM public.user_credentials
    WHERE email IS NULL OR password_hash IS NULL OR password_hash NOT LIKE '\$argon2id\$%';" 2>/dev/null)
if [ -n "$sans_secret" ] && [ "$sans_secret" != "0" ]; then
  ecarts="$ecarts\n  * user_credentials : $sans_secret identifiant(s) restauré(s) sans e-mail ou sans empreinte argon2id exploitable — personne ne pourrait se reconnecter"
fi

# Les clés étrangères sont rejouées en fin de restauration : si l'une d'elles
# n'a pas été recréée, la base restaurée accepterait des lignes orphelines et la
# divergence n'apparaîtrait que plus tard, en production.
fk_vivante=$(psql -d "$BASE"  -tA -c "SELECT count(*) FROM pg_constraint WHERE contype='f';")
fk_restauree=$(psql -d "$CIBLE" -tA -c "SELECT count(*) FROM pg_constraint WHERE contype='f';")
[ "${fk_restauree:-0}" -ge "${fk_vivante:-0}" ] || \
  ecarts="$ecarts\n  * clés étrangères : $fk_restauree restaurées contre $fk_vivante en base"

if [ -n "$ecarts" ]; then
  alerte "la restauration de $ORIGINE DIVERGE de la base vivante :$(printf '%b' "$ecarts")

Une sauvegarde qui ne se restaure pas à l'identique n'est pas une sauvegarde.
Ne PAS supprimer le projet Supabase tant que ceci n'est pas compris."
fi
[ -z "$avertissements" ] || previens "répétition de restauration" \
  "restauration conforme, mais des lignes présentes dans la sauvegarde ont disparu de la base vivante :$(printf '%b' "$avertissements")"

total=$(awk -F'|' '{s+=$2} END {print s+0}' "$TRAVAIL/restauree.txt")
printf '%s %s %s tables %s lignes\n' "$(date -uIs)" "$MODE" "$nb_restauree" "$total" \
  > "$DEST/.derniere_repetition"

echo "[répétition] ✅ $ORIGINE restaurée dans $CIBLE : $nb_restauree table(s) (base vivante : $nb_vivante), $total ligne(s), aucun écart."
ping_garde ""
