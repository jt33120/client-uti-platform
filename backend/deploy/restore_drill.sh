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
#   4. RESTAURE AUSSI L'ARCHIVE DES FICHIERS et vérifie que chaque fichier
#      référencé par la base restaurée s'y trouve réellement ;
#   5. supprime la base jetable, quoi qu'il arrive (trap) ;
#   6. CRIE si quoi que ce soit diverge.
#
#  POURQUOI L'ÉTAPE 4 EST AUSSI IMPORTANTE QUE LES TROIS PREMIÈRES
#  Depuis que les fichiers vivent sur le disque du VPS, une base restaurée seule
#  est une base de LIENS MORTS : `submissions.cv_url` désigne un CV qui n'existe
#  nulle part. Cette panne-là a la particularité de ne pas ressembler à une
#  panne — la restauration réussit, l'application démarre, les écrans
#  s'affichent, et c'est en cliquant sur un CV qu'on découvre, des semaines plus
#  tard, qu'il n'y en a plus. Vérifier la base sans vérifier les fichiers
#  reviendrait à valider la moitié des données en croyant les valider toutes.
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
# Même valeur que dans backup_db.sh et que LOCAL_STORAGE_DIR de backend/.env.
FICHIERS="${FILES_DIR:-/var/lib/uti/files}"
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
  #
  # MAIS le tri porte sur la CLÉ ENTIÈRE, et le conteneur ne contient plus
  # seulement des dumps de base. Depuis que les fichiers y sont déposés, il
  # cohabite trois familles :
  #     uti/2026/08/uti-….pgcustom.age        ← la base
  #     uti/fichiers/2026/08/uti-fichiers-….tar.age
  #     uti/conf/conf-….tar.age
  # « uti/f… » et « uti/c… » trient APRÈS « uti/2… ». Un `tail -1` nu ramenait
  # donc l'archive des FICHIERS, que `pg_restore` refuse — et la répétition
  # échouait sur un message parlant de format d'archive, pas de sélection. Pire
  # cas : elle aurait « réussi » sur la mauvaise famille.
  #
  # On filtre donc sur le SUFFIXE, qui dit ce qu'est l'objet, plutôt que sur le
  # préfixe, qui dit seulement où il est rangé.
  CLE_S3=$("$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" lister "uti/" \
           | grep '\.pgcustom\.age$' | tail -1) \
    || alerte "impossible de lister le conteneur hors-site"
  [ -n "$CLE_S3" ] || alerte "aucun dump de base (*.pgcustom.age) dans le conteneur hors-site"

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

# ── Les FICHIERS : restaurer l'archive et confronter la base restaurée ──────
# Les CINQ requêtes ci-dessous produisent, pour chaque référence de fichier
# stockée en base, le chemin ATTENDU dans le dépôt.
#
# CINQ, ET PAS QUATRE. La cinquième — les images des modèles d'e-mail — a
# manqué jusqu'au 26/08/2026. Ces images ne sont référencées par AUCUNE
# colonne : elles vivent à l'intérieur du HTML de `email_templates.body`
# (scripts/migrate_storage_to_ovh.py:191-201). Une restauration pouvait donc
# les perdre en étant déclarée conforme, et la panne serait apparue chez un
# CLIENT, dans un e-mail aux images cassées, des semaines plus tard.
#
# `email_templates` est vide en production aujourd'hui : le trou était donc
# sans effet, et c'est exactement pour ça qu'il pouvait durer. Il suffit qu'une
# personne personnalise un modèle depuis l'écran d'administration.
#
# L'expression rationnelle est VOLONTAIREMENT identique à celle du script de
# migration, limites comprises — elle s'arrête à la première espace, donc une
# URL non encodée n'est ni réécrite ni vérifiée. Une vérification plus large
# que la réécriture signalerait des fichiers que la migration ne touche jamais.
#
# `clients.logo_url` reste délibérément hors de la liste : la colonne n'est
# réécrite par aucun script, aucun bucket ne lui correspond, et elle est vide
# sur les 21 clients. Ce n'est pas une référence de stockage. Elles reproduisent en SQL ce
# que fait services/storage.py:_object_path : une valeur peut être une URL
# publique héritée (« …/public/cvs/<chemin> »), une URL S3, ou déjà un chemin
# nu. Le faire en SQL plutôt qu'en bash évite d'avoir à découper des URL dans un
# shell, où un nom de fichier avec une espace suffit à tout fausser.
requete_fichiers="
SELECT 'cvs/' || CASE WHEN cv_url LIKE '%/cvs/%'
         THEN split_part(split_part(cv_url, '/cvs/', 2), '?', 1)
         ELSE ltrim(cv_url, '/') END
  FROM public.submissions WHERE cv_url IS NOT NULL AND cv_url <> ''
UNION ALL
SELECT 'avatars/' || CASE WHEN avatar_url LIKE '%/avatars/%'
         THEN split_part(split_part(avatar_url, '/avatars/', 2), '?', 1)
         ELSE ltrim(avatar_url, '/') END
  FROM public.profiles WHERE avatar_url IS NOT NULL AND avatar_url <> ''
UNION ALL
SELECT 'compliance/' || CASE WHEN file_url LIKE '%/compliance/%'
         THEN split_part(split_part(file_url, '/compliance/', 2), '?', 1)
         ELSE ltrim(file_url, '/') END
  FROM public.partner_compliance_docs WHERE file_url IS NOT NULL AND file_url <> ''
UNION ALL
SELECT 'ao-sources/' || (f->>'path')
  FROM public.appels_offres, jsonb_array_elements(source_files) AS f
 WHERE source_files IS NOT NULL AND jsonb_typeof(source_files) = 'array'
   AND f->>'path' IS NOT NULL
UNION ALL
SELECT 'email-assets/' || m[1]
  FROM public.email_templates,
       LATERAL regexp_matches(body, '/email-assets/([^[:space:]\"''<>)?]+)', 'g') AS m
 WHERE body IS NOT NULL AND body <> '';"

# Choix de l'archive de fichiers, selon le même mode que pour la base.
ARCHIVE_F=""
if [ "$MODE" = "--hors-site" ]; then
  # Même filtre par suffixe que pour la base : le préfixe « uti/fichiers/ » est
  # aujourd'hui homogène, mais c'est un fait qu'aucune règle ne garantit — une
  # famille ajoutée demain sous ce préfixe casserait la sélection en silence.
  CLE_S3_F=$("$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" lister "uti/fichiers/" \
             | grep 'uti-fichiers-.*\.tar\.age$' | tail -1)
  if [ -n "$CLE_S3_F" ]; then
    "$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" recuperer "$CLE_S3_F" "$TRAVAIL/fichiers.age" >/dev/null \
      || alerte "téléchargement de $CLE_S3_F impossible"
    age -d -i "$AGE_IDENTITY" -o "$TRAVAIL/fichiers.tar" "$TRAVAIL/fichiers.age" \
      || alerte "DÉCHIFFREMENT IMPOSSIBLE de $CLE_S3_F — la clé privée n'ouvre pas l'archive des fichiers"
    ARCHIVE_F="$TRAVAIL/fichiers.tar"
  fi
else
  derniere=$(find "$DEST" -maxdepth 1 -name 'uti-fichiers-*.tar' -type f -printf '%f\n' 2>/dev/null | sort | tail -1)
  [ -n "$derniere" ] && ARCHIVE_F="$DEST/$derniere"
fi

nb_refs=$(psql -d "$CIBLE" -tA -c "$requete_fichiers" 2>/dev/null | sed '/^$/d' | wc -l)

if [ -z "$ARCHIVE_F" ]; then
  # Pas d'archive. Acceptable seulement si la base restaurée ne référence
  # AUCUN fichier — c'est-à-dire avant la bascule du stockage.
  [ "$nb_refs" -eq 0 ] || ecarts="$ecarts\n  * fichiers : la base restaurée référence $nb_refs fichier(s) et AUCUNE archive de fichiers n'existe (backup_db.sh ne les sauvegarde pas)"
else
  EXTRAIT="$TRAVAIL/fichiers"
  mkdir -p "$EXTRAIT"
  tar -xf "$ARCHIVE_F" -C "$EXTRAIT" || alerte "l'archive de fichiers $ARCHIVE_F est illisible"

  refs_absentes=""
  nb_manquants=0
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    if [ ! -f "$EXTRAIT/$rel" ]; then
      nb_manquants=$((nb_manquants+1))
      # Cinq exemples suffisent : au-delà, l'alerte devient un mur de texte que
      # personne ne lit, et le compte total dit déjà l'ampleur.
      [ "$nb_manquants" -le 5 ] && refs_absentes="$refs_absentes\n      - $rel"
    fi
  done < <(psql -d "$CIBLE" -tA -c "$requete_fichiers" 2>/dev/null | sed '/^$/d')

  if [ "$nb_manquants" -gt 0 ]; then
    ecarts="$ecarts\n  * fichiers : $nb_manquants référence(s) sur $nb_refs pointent vers un fichier ABSENT de l'archive restaurée :$refs_absentes"
  fi

  nb_extraits=$(find "$EXTRAIT" -type f | wc -l)
  nb_vivants=$(find "$FICHIERS" -type f 2>/dev/null | wc -l)
  # L'archive n'est renvoyée que lorsque le contenu change (backup_db.sh), donc
  # elle doit normalement être IDENTIQUE au dépôt vivant. Un écart n'est pas
  # forcément une perte — un dépôt fait il y a trois minutes n'y est pas encore —
  # mais il doit se dire, parce que c'est aussi la signature d'un envoi qui
  # échoue en silence depuis des semaines.
  if [ "$nb_extraits" -lt "$nb_vivants" ]; then
    avertissements="$avertissements\n  * fichiers : $nb_extraits dans la sauvegarde contre $nb_vivants sur le disque (dépôt récent, ou envoi hors-site en échec)"
  fi
  echo "[répétition] fichiers : $nb_extraits restauré(s) depuis $ARCHIVE_F, $nb_refs référence(s) en base, $nb_manquants absente(s)."
fi

if [ -n "$ecarts" ]; then
  alerte "la restauration de $ORIGINE DIVERGE de la base vivante :$(printf '%b' "$ecarts")

Une sauvegarde qui ne se restaure pas à l'identique n'est pas une sauvegarde.
Ne PAS supprimer le projet Supabase tant que ceci n'est pas compris."
fi
[ -z "$avertissements" ] || previens "répétition de restauration" \
  "restauration conforme, mais des lignes présentes dans la sauvegarde ont disparu de la base vivante :$(printf '%b' "$avertissements")"

total=$(awk -F'|' '{s+=$2} END {print s+0}' "$TRAVAIL/restauree.txt")
printf '%s %s %s tables %s lignes %s fichiers\n' "$(date -uIs)" "$MODE" "$nb_restauree" "$total" \
  "${nb_extraits:-0}" > "$DEST/.derniere_repetition"

echo "[répétition] ✅ $ORIGINE restaurée dans $CIBLE : $nb_restauree table(s) (base vivante : $nb_vivante), $total ligne(s), ${nb_extraits:-0} fichier(s), aucun écart."
ping_garde ""
