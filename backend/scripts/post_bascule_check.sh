#!/usr/bin/env bash
# =============================================================================
#  Contrôle automatique de la bascule — à lancer sur le VPS juste après avoir
#  changé SUPABASE_URL, puis chaque jour pendant la période d'observation.
#
#  POURQUOI CE SCRIPT ET PAS deploy.sh
#
#  deploy.sh:18 vérifie /health. Or /health (main.py:170-179) renvoie un dict
#  constant : il ne touche PAS la base. Un backend parfaitement démarré au-dessus
#  d'une base injoignable répond donc {"status":"ok"} et le rollback automatique
#  de deploy.sh ne se déclenche jamais. C'est acceptable — un /health qui tape la
#  base tomberait au moindre hoquet — mais cela veut dire que le jour de la
#  bascule, le feu vert de deploy.sh ne prouve rien de ce qui vient de changer.
#  Ce script comble exactement ce trou.
#
#  Chaque contrôle correspond à un comportement dont du code réel dépend ; la
#  référence fichier:ligne est donnée en commentaire.
#
#  USAGE
#      bash ~/app/backend/scripts/post_bascule_check.sh
#      echo $?      # 0 = tout vert, 1 = au moins un contrôle rouge
# =============================================================================
set -uo pipefail

BACKEND="${BACKEND_DIR:-$HOME/app/backend}"

# SE PLACER DANS backend/ AVANT TOUT. config.py déclare `env_file: ".env"`, que
# pydantic-settings résout RELATIVEMENT AU RÉPERTOIRE COURANT. Lancé depuis
# ~/app (ou depuis n'importe où ailleurs), chaque heredoc python de ce script
# échouait donc sur « supabase_url Field required » — et le contrôle concluait
# « au moins un comportement PostgREST diffère », ce qui envoie chercher une
# incompatibilité de base de données là où il n'y a qu'un chemin relatif.
cd "$BACKEND" || { echo "❌ répertoire introuvable : $BACKEND"; exit 2; }
API="${API_URL:-http://127.0.0.1:8000}"
PGRST="${PGRST_URL:-http://127.0.0.1:8080}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/uti}"

ROUGE=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
ko()   { printf '  \033[31m✗\033[0m %s\n' "$1"; ROUGE=$((ROUGE+1)); }
# TROISIÈME ÉTAT, ET IL MANQUAIT. Un contrôle qui n'a PAS PU s'exécuter n'est
# pas un contrôle en échec. Le confondre produit le pire message possible :
# « la clé S3 du VPS PEUT supprimer » affirmé par un script qui n'a jamais
# réussi à s'y connecter. Une alerte fausse coûte plus cher que pas d'alerte —
# elle envoie corriger un problème qui n'existe pas, et elle apprend à ignorer
# les rouges suivants.
nv()   { printf '  \033[33m?\033[0m %s\n' "$1"; }
titre(){ printf '\n\033[1m%s\033[0m\n' "$1"; }

titre "1. Backend et façade"

curl -sf --max-time 5 "$API/health" | grep -q '"status":"ok"' \
  && ok "/health répond ok ($(curl -s --max-time 5 "$API/health" | grep -o '"commit":"[^"]*"'))" \
  || ko "/health ne répond pas — systemctl status uti-backend"

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$PGRST/rest/v1/profiles")
[ "$code" = "401" ] && ok "PostgREST refuse une requête sans jeton (401)" \
                    || ko "PostgREST répond $code sans jeton — attendu 401. La base est-elle ouverte ?"

# La façade renvoie 501 sur /auth/v1/ (deploy/nginx-postgrest.conf:91-93) : si un
# chemin d'authentification appelle encore GoTrue, on le voit ici plutôt que sur
# un utilisateur qui ne peut plus se connecter.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$PGRST/auth/v1/token")
[ "$code" = "501" ] && ok "/auth/v1/ renvoie 501 (garde-fou GoTrue en place)" \
                    || ko "/auth/v1/ répond $code — la façade a changé, vérifier nginx-postgrest.conf"

# Guillemets TOLÉRÉS. Le .env de production écrit ses valeurs entre guillemets
# (SUPABASE_URL="https://…") : un grep ancré sans eux déclarait rouge une
# bascule parfaitement faite, pour une raison purement typographique.
grep -qE '^SUPABASE_URL="?http://127\.0\.0\.1:8080"?$' "$BACKEND/.env" \
  && ok ".env pointe sur la base locale" \
  || ko ".env pointe encore ailleurs : $(grep '^SUPABASE_URL=' "$BACKEND/.env")"

# `local`, PAS `s3`. Ce contrôle attendait « s3 » — la piste OVH Object Storage,
# abandonnée faute d'accès au compte (BASCULE.md, « Pourquoi les fichiers vont
# sur le disque du VPS »). Il aurait donc déclaré ROUGE la bascule correcte, le
# jour où elle a lieu, et poussé à « corriger » vers un backend qu'on ne peut
# pas provisionner. Un contrôle faux coûte plus cher que pas de contrôle.
grep -qE '^STORAGE_BACKEND="?local"?$' "$BACKEND/.env" \
  && ok "STORAGE_BACKEND=local (fichiers sur le disque du VPS)" \
  || ko "STORAGE_BACKEND n'est pas à local — le stockage parle encore à Supabase"

# PUBLIC_BASE_URL conditionne le DÉMARRAGE en mode local (config.py refuse de
# booter sans elle) : si le backend tourne, elle est là. On la vérifie quand
# même — ce script sert aussi à relire un .env avant de redémarrer.
grep -qE '^PUBLIC_BASE_URL="?https://' "$BACKEND/.env" \
  && ok "PUBLIC_BASE_URL posée (liens de CV et d'avatars absolus)" \
  || ko "PUBLIC_BASE_URL absente — le backend refusera de démarrer en mode local"

grep -qi 'supabase\.co' "$BACKEND/.env" \
  && ko "il reste une URL supabase.co dans .env : $(grep -i 'supabase\.co' "$BACKEND/.env" | cut -d= -f1 | tr '\n' ' ')" \
  || ok "aucune URL supabase.co résiduelle dans .env"

titre "2. Comportements PostgREST dont le code dépend"

BACKEND_DIR="$BACKEND" "$BACKEND/venv/bin/python" - <<'PY'
# Chaque cas rejoue la syntaxe EXACTE d'un appel du dépôt. Un écart de forme
# invaliderait le test : c'est la forme qui casse, pas l'intention.
import os, sys
sys.path.insert(0, os.environ["BACKEND_DIR"])
from services.postgrest_client import db

V, R = "  \033[32m✓\033[0m", "  \033[31m✗\033[0m"
rouge = 0
def essai(libelle, fn):
    global rouge
    try:
        fn()
        print(f"{V} {libelle}")
    except Exception as e:
        print(f"{R} {libelle} — {type(e).__name__}: {str(e)[:160]}")
        rouge += 1

# services/scheduler.py:44 — jointure embarquée AO → clients.
# Sur une base vide le résultat est [] : c'est justement ce qui prouve quelque
# chose. Si la relation était inconnue, PostgREST répondrait PGRST200 et lèverait.
# Un [] silencieux vaut donc « la clé étrangère est résolue ».
essai("jointure appels_offres → clients(name)",
      lambda: db.table("appels_offres").select("*, clients(name)").limit(1).execute())

# routers/matching.py:243 — la jointure la plus fragile : deux relations, dont
# celle qui dépend de la FK ajoutée par migrations/0017_matchings_consultant_fk.sql.
essai("jointure matchings → consultants + submissions",
      lambda: db.table("matchings")
              .select("*, consultants(name, tjm, skills, employment_type), submissions(cv_url, cv_filename)")
              .limit(1).execute())

# routers/aos.py — agrégat embarqué, doit renvoyer [{'count': N}].
essai("agrégat appels_offres → submissions(count)",
      lambda: db.table("appels_offres").select("id, submissions(count)").limit(1).execute())

# 52 sites appellent .single() et comptent sur l'exception pour produire un 404
# (routers/auth.py:530 par exemple). Si .single() renvoyait None au lieu de lever,
# ces 52 sites répondraient 500.
def single_leve():
    try:
        db.table("profiles").select("*").eq("id", "00000000-0000-0000-0000-000000000000").single().execute()
    except Exception:
        return
    raise AssertionError(".single() sur 0 ligne n'a pas levé")
essai(".single() sur 0 ligne lève bien", single_leve)

# services/data_retention.py:174 — count exact, utilisé par les écrans admin.
def compte():
    r = db.table("submissions").select("id", count="exact").limit(1).execute()
    assert r.count is not None, "count est None"
essai('count="exact" renvoie un entier', compte)

# services/data_retention.py:118 — in_() sur liste vide ne doit pas planter.
essai("in_([]) renvoie [] sans erreur",
      lambda: db.table("submissions").select("id").in_("consultant_id", []).execute())

# services/email_outbox.py:enqueue traite un conflit d'unicité comme un SUCCÈS,
# en cherchant '23505' et 'duplicate' dans str(e). Ces chaînes viennent des
# messages de PostgreSQL : avec lc_messages en français, le mot « duplicate »
# disparaît et un doublon serait compté comme une panne d'envoi.
def conflit():
    try:
        db.table("app_settings").insert({"key": "notifications", "value": {}}).execute()
    except Exception as e:
        s = str(e).lower()
        assert "23505" in s, f"code 23505 absent du message : {s[:120]}"
        assert "duplicate" in s, f"mot 'duplicate' absent (lc_messages non C) : {s[:120]}"
        return
    raise AssertionError("l'insertion en doublon a réussi — la clé primaire manque")
essai("conflit d'unicité : message contenant 23505 ET duplicate", conflit)

sys.exit(1 if rouge else 0)
PY
[ $? -eq 0 ] && ok "les 7 comportements PostgREST sont conformes" \
             || { ko "au moins un comportement PostgREST diffère (détail ci-dessus)"; }

titre "3. Configuration présente en base"

# UNE SEULE INTERROGATION, CAPTURÉE — et le silence compté comme un échec.
#
# Le montage précédent appelait psql DEUX fois : la première derrière un pipe,
# donc dans un sous-shell, où les ROUGE de ko() mouraient avec lui ; la seconde
# pour recompter. Ce recomptage avait un angle mort : psql qui NE PEUT PAS SE
# CONNECTER sort non nul en n'écrivant RIEN sur la sortie standard. `grep -c`
# comptait alors 0, `|| true` avalait son code, et le script annonçait « aucun
# réglage manquant » — un vert tiré d'un silence. La panne la plus grave que ce
# bloc puisse rencontrer était donc la seule qu'il ne pouvait pas signaler.
#
# La capture supprime le sous-shell (donc le besoin du second appel) et rend
# l'absence de réponse visible en tant que telle.
if seed_sortie=$(psql -d uti -v ON_ERROR_STOP=1 -tA \
                      -f "$BACKEND/migrations/verify_seed.sql" 2>&1) \
   && [ -n "$seed_sortie" ]; then
  while IFS= read -r ligne; do
    case "$ligne" in
      *MANQUANT*|*INERTE*) ko "$ligne" ;;   # ko() incrémente ROUGE lui-même
      *)                   ok "$ligne" ;;
    esac
  done <<< "$seed_sortie"
else
  # ROUGE et non « ? », alors que c'est bien un contrôle EMPÊCHÉ. La distinction
  # posée plus haut tient toujours, mais elle départage selon ce que coûte le
  # silence : le hors-site non lu reste prouvé par ailleurs (systemd a déposé
  # l'archive), tandis qu'ici plus rien n'atteste des réglages. Or ROUGE=0 vaut
  # « Supabase peut être supprimé ». Un « ? » laisserait donc ce feu vert
  # s'allumer sur une base muette, ce qui est précisément le vert-tiré-d'un-
  # silence que le bloc ci-dessus élimine.
  ko "la base n'a pas répondu : les réglages ne sont PAS vérifiés"
  printf '%s\n' "$seed_sortie" | sed 's/^/       /'
  nv "   psql s'authentifie en « peer » : c'est le compte UNIX qui choisit le"
  nv "   rôle (pg_ident.conf). Lancer sous julian.talou, PGUSER=uti_admin."
fi

titre "4. Stockage — ce qui doit être privé l'est"

# CE CONTRÔLE ÉTAIT ROUGE PAR CONSTRUCTION APRÈS UNE BASCULE RÉUSSIE.
#
# Il exigeait S3_PUBLIC_BASE_URL, donc le backend « s3 » — la piste OVH Object
# Storage, abandonnée faute d'accès au compte. Une bascule correcte vers le
# disque du VPS le faisait donc échouer, et l'opérateur aurait « corrigé » vers
# un backend qu'on ne peut pas provisionner. Il branche désormais sur le backend
# RÉELLEMENT actif, chacun ayant son propre mode de fuite.
BACKEND_ACTIF=$(grep -E '^STORAGE_BACKEND=' "$BACKEND/.env" | cut -d= -f2- | tr -d '"')
BACKEND_ACTIF="${BACKEND_ACTIF:-supabase}"     # même défaut que backend/config.py

# LA CLÉ D'UN CV RÉEL — PRISE EN BASE, ET PLUS PAR UN LISTAGE QUI NE VOIT RIEN.
#
# Ce bloc rendait systématiquement une chaîne vide, et les deux contrôles de
# fuite ci-dessous n'ont donc JAMAIS tourné — en affichant vert. Mesuré le 9
# septembre sur une plateforme qui servait 31 CV : « aucun CV en stockage ».
#
# La cause est structurelle, pas circonstancielle. En mode local,
# storage.list() ne rend que les fichiers posés DIRECTEMENT dans le répertoire
# du bucket (services/storage.py:584, `if e.is_file()`), or un CV s'écrit en
# cvs/<ao_id>/<uuid>.pdf (routers/submissions.py:185). Le listage ne voyait donc
# que des répertoires, et rendait []. Aucun envoi de CV, jamais, n'aurait pu
# faire passer ce contrôle de « à refaire après le premier envoi » à un test.
#
# La base est de toute façon la meilleure source : c'est la valeur RÉELLEMENT
# stockée qu'on veut éprouver, celle que le navigateur demanderait, y compris
# ses formes héritées (URL Supabase complète) que _object_path sait relire.
CLE_CV=$(BACKEND_DIR="$BACKEND" "$BACKEND/venv/bin/python" - 2>/dev/null <<'PY'
import os, sys
sys.path.insert(0, os.environ["BACKEND_DIR"])
from services import storage
from services.postgrest_client import db
r = (db.table("submissions").select("cv_url")
       .not_.is_("cv_url", "null").limit(1).execute())
if r.data:
    print(storage._object_path("cvs", r.data[0]["cv_url"]) or "")
PY
)

case "$BACKEND_ACTIF" in
  local)
    # En local, la fuite possible est le service PUBLIC d'un bucket privé :
    # routers/files.py ne sert sans jeton que les buckets de storage.PUBLIC_BUCKETS,
    # dont « cvs » ne fait PAS partie. On vérifie que la porte est bien fermée.
    BASE=$(grep -E '^PUBLIC_BASE_URL=' "$BACKEND/.env" | cut -d= -f2- | tr -d '"')
    if [ -z "$BASE" ]; then
      ko "PUBLIC_BASE_URL absent alors que STORAGE_BACKEND=local — le backend ne devrait pas démarrer"
    else
      if [ -n "$CLE_CV" ]; then
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "${BASE%/}/files/public/cvs/$CLE_CV")
        { [ "$code" = "404" ] || [ "$code" = "403" ]; } \
          && ok "un CV n'est PAS servi par la route publique (HTTP $code)" \
          || ko "un CV répond $code sur /files/public/ — « cvs » est traité comme public, corriger MAINTENANT"
      else
        # « ? » et non « ✓ ». C'est ici que le contrôle mentait : sans clé, il
        # n'a rien éprouvé, et le dire est le minimum. Reste vert au verdict
        # tant qu'aucun CV n'existe — un envoi le rendra concluant, ce que
        # l'ancien listage ne pouvait pas faire.
        nv "aucun CV trouvé en base : la route publique n'est PAS éprouvée"
      fi
      # Indépendant du CV, et il était pourtant enfermé dans la même branche :
      # un jeton invalide se refuse avec ou sans fichier en stockage.
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "${BASE%/}/files/d/jeton-invalide")
      { [ "$code" = "403" ] || [ "$code" = "400" ]; } \
        && ok "un jeton invalide est refusé (HTTP $code)" \
        || ko "un jeton invalide répond $code — la signature des URLs ne protège rien"
    fi
    MODE=$(stat -c %a "${FILES_DIR:-/var/lib/uti/files}" 2>/dev/null)
    [ "$MODE" = "700" ] && ok "répertoire des fichiers en 0700" \
                        || ko "répertoire des fichiers en ${MODE:-absent} — attendu 700"
    ;;
  s3)
    # Si le conteneur OVH est lui-même en lecture publique, l'ACL objet par objet
    # ne sert à rien : tout devient lisible par URL. Un CV, c'est un nom, un
    # téléphone et un parcours — le contrôle n'est pas théorique.
    BASE=$(grep '^S3_PUBLIC_BASE_URL=' "$BACKEND/.env" | cut -d= -f2- | tr -d '"')
    if [ -z "$BASE" ]; then
      ko "S3_PUBLIC_BASE_URL absent alors que STORAGE_BACKEND=s3"
    elif [ -n "$CLE_CV" ]; then
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "${BASE%/}/cvs/$CLE_CV")
      { [ "$code" = "403" ] || [ "$code" = "401" ]; } \
        && ok "un CV n'est PAS lisible anonymement (HTTP $code)" \
        || ko "un CV répond $code en anonyme — le conteneur OVH est public, corriger MAINTENANT"
    else
      nv "aucun CV trouvé en base : la lecture anonyme n'est PAS éprouvée"
    fi
    ;;
  supabase)
    ko "STORAGE_BACKEND=supabase : les fichiers sont ENCORE chez Supabase, la bascule du stockage n'est pas faite"
    ;;
  *)
    ko "STORAGE_BACKEND=$BACKEND_ACTIF inconnu — le backend refuse de démarrer sur cette valeur (config.py)"
    ;;
esac

titre "5. Boucles de fond"

# services/scheduler.py:184 et :206 impriment ces marqueurs au démarrage. Leur
# absence signifie que le planificateur (liste 2, relances, purge RGPD, budget IA)
# et l'envoyeur d'e-mails ne tournent pas — panne totalement silencieuse.
# CES DEUX MARQUEURS SONT ÉCRITS UNE SEULE FOIS, AU DÉMARRAGE. Les chercher sur
# une fenêtre de 10 minutes ne pouvait réussir que si le backend venait de
# redémarrer — donc échouer presque toujours. Pire pour l'envoyeur d'e-mails :
# sa boucle n'écrit RIEN quand la file est vide, c'est délibéré
# (services/scheduler.py) ; attendre un battement d'un processus volontairement
# silencieux est une erreur de lecture du code, pas un signal.
#
# On lit donc le journal DEPUIS LE DÉMARRAGE DU SERVICE, ce qui est exactement
# la question posée : « les deux boucles ont-elles démarré dans ce processus ? »
_depuis=$(systemctl show uti-backend -p ActiveEnterTimestamp --value 2>/dev/null)
[ -n "$_depuis" ] || _depuis="-1 h"
_jrn=$(journalctl -u uti-backend --since "$_depuis" --no-pager 2>/dev/null)

grep -q "\[SCHED\] planificateur" <<<"$_jrn" \
  && ok "planificateur démarré (depuis le lancement du service)" \
  || ko "marqueur [SCHED] absent depuis le démarrage du service — la boucle de notifications n'a pas démarré"
grep -q "\[OUTBOX\] envoyeur" <<<"$_jrn" \
  && ok "envoyeur d'e-mails démarré (depuis le lancement du service)" \
  || ko "marqueur [OUTBOX] absent depuis le démarrage du service — la file d'envoi n'a pas démarré"

erreurs=$(journalctl -u uti-backend --since "-1 h" --no-pager 2>/dev/null | grep -c "\[ERROR\]" || true)
[ "$erreurs" -eq 0 ] && ok "aucune erreur applicative sur la dernière heure" \
                     || ko "$erreurs ligne(s) [ERROR] sur la dernière heure — journalctl -u uti-backend | grep ERROR"

titre "6. Sauvegarde"

# Ce bloc contrôle les TROIS conditions posées pour supprimer le projet
# Supabase. Elles ne sont pas interchangeables et aucune ne suffit seule :
# une sauvegarde locale non chiffrée hors-site, ou hors-site jamais restaurée,
# ne remplit pas le contrat — elle rassure, ce qui est pire.

# — Condition 0 : elle tourne, et elle a RÉUSSI. Le marqueur .dernier_succes est
#   écrit à la toute dernière ligne de backup_db.sh : une exécution interrompue
#   laisse un .pgcustom tout frais mais PAS ce fichier-là.
if [ -f "$BACKUP_DIR/.dernier_succes" ]; then
  age_min=$(( ( $(date -u +%s) - $(stat -c%Y "$BACKUP_DIR/.dernier_succes") ) / 60 ))
  [ "$age_min" -le 180 ] \
    && ok "dernière sauvegarde RÉUSSIE il y a $age_min min — $(cat "$BACKUP_DIR/.dernier_succes")" \
    || ko "aucune sauvegarde réussie depuis $((age_min/60)) h — systemctl list-timers uti-backup.timer"
else
  ko "aucune sauvegarde n'a jamais RÉUSSI ($BACKUP_DIR/.dernier_succes absent) — systemctl start uti-backup"
fi

dernier=$(find "$BACKUP_DIR" -name 'uti-*.pgcustom' -mmin -180 2>/dev/null | sort | tail -1)
if [ -n "$dernier" ]; then
  taille=$(stat -c%s "$dernier")
  [ "$taille" -gt 100000 ] \
    && ok "archive locale récente : $(basename "$dernier") ($((taille/1024)) Ko)" \
    || ko "la dernière archive ne fait que $taille octets — dump vide ?"
else
  ko "aucune archive de moins de 3 h dans $BACKUP_DIR — la sauvegarde est horaire"
fi

# — Condition 1 : elle vit HORS du VPS. Sans ce contrôle, on validerait un
#   dispositif qu'un seul `rm -rf` (ou un rançongiciel) annule intégralement.
# Ce script tourne sous julian.talou ; /etc/uti-backup.env est en 0600 root.
# `[ -f ]` était donc vrai, le `.` échouait en « Permission denied », les
# variables restaient vides, et TROIS contrôles se déclaraient rouges sans avoir
# rien testé. On tente sudo sans mot de passe (utilisable depuis une unité
# systemd ou un sudoers dédié) ; à défaut, on annonce « non vérifié ».
_secrets_lus=0
if [ -r /etc/uti-backup.env ]; then
  # shellcheck disable=SC1091
  set -a; . /etc/uti-backup.env; set +a; _secrets_lus=1
elif sudo -n cat /etc/uti-backup.env >/dev/null 2>&1; then
  set -a; . <(sudo -n cat /etc/uti-backup.env); set +a; _secrets_lus=1
fi

if [ "$_secrets_lus" = 0 ] && [ -f /etc/uti-backup.env ]; then
  nv "hors-site NON VÉRIFIÉ : /etc/uti-backup.env illisible sous $(id -un) (0600 root)."
  # SURTOUT PAS `sudo -E $0`, ce que ce message conseillait. PostgreSQL
  # s'authentifie ici en « peer » : c'est le compte UNIX appelant qui décide du
  # rôle, et pg_ident.conf (install_db.sh:263-268) ne mappe que julian.talou et
  # postgrest. Sous root, les deux psql de la section 3 échouent — donc pour
  # rendre trois contrôles au hors-site, on en aurait cassé sept ailleurs.
  # La bonne manœuvre est de rafraîchir le laissez-passer sudo : le repli
  # `sudo -n cat` ci-dessus s'en sert, et le script reste sous julian.talou.
  nv "   Rafraîchir le laissez-passer sudo, puis relancer SOUS TON COMPTE :"
  nv "     sudo -v && PGUSER=uti_admin bash $0"
  nv "   La sauvegarde elle-même, lancée par systemd, lit bien ce fichier —"
  nv "   voir la ligne « dernière sauvegarde RÉUSSIE » ci-dessus, qui fait foi."
elif [ -f /etc/uti-backup.env ]; then
  recent=$(BACKEND_DIR="$BACKEND" "$BACKEND/venv/bin/python" "$BACKEND/deploy/s3_backup.py" lister "uti/" 2>/dev/null | tail -1)
  [ -n "$recent" ] \
    && ok "dépôt hors-site alimenté — dernier objet : $recent" \
    || ko "le conteneur hors-site est VIDE ou injoignable : les sauvegardes ne survivraient pas au VPS"

  # — Le piège central : la clé posée sur le VPS ne doit PAS pouvoir effacer.
  #   Une politique qu'on n'a pas essayé de violer n'est qu'une intention.
  BACKEND_DIR="$BACKEND" "$BACKEND/venv/bin/python" - <<'PY'
import os, sys, boto3
from botocore.exceptions import ClientError
c = boto3.client("s3", endpoint_url=os.environ["BACKUP_S3_ENDPOINT"],
                 region_name=os.environ.get("BACKUP_S3_REGION", "sbg"),
                 aws_access_key_id=os.environ["BACKUP_S3_ACCESS_KEY"],
                 aws_secret_access_key=os.environ["BACKUP_S3_SECRET_KEY"])
b, k = os.environ["BACKUP_S3_BUCKET"], "uti/_essai_suppression"
try:
    c.put_object(Bucket=b, Key=k, Body=b"x")
except ClientError as e:
    sys.exit(f"dépôt refusé ({e.response['Error']['Code']}) : la sauvegarde ne peut plus écrire")
try:
    c.delete_object(Bucket=b, Key=k)
except ClientError:
    sys.exit(0)          # refusé = c'est le but
sys.exit("SUPPRESSION ACCEPTÉE : la clé du VPS peut effacer l'historique")
PY
  [ $? -eq 0 ] \
    && ok "la clé S3 du VPS ne peut PAS supprimer (politique + verrou d'objet actifs)" \
    || ko "la clé S3 du VPS PEUT supprimer — une compromission détruirait tout. Voir deploy/backup_s3_policy.README.md"
else
  ko "/etc/uti-backup.env absent : aucun dépôt hors-site configuré — bash deploy/setup_backup_offsite.sh"
fi

# — Condition 2 : le chiffrement. Les archives contiennent des CV et les secrets
#   TOTP en clair de profiles.mfa_secret ; elles partent chez un tiers.
# LA GARDE ANTI-GABARIT NE DOIT REGARDER QUE LA LIGNE, PAS LE FICHIER.
# Elle faisait `! grep -qi REMPLACER <fichier entier>` : or l'unité porte, deux
# lignes plus haut, le commentaire « Remplacer par la sortie de age-keygen ».
# La recherche étant insensible à la casse et non ancrée, ce contrôle ne pouvait
# JAMAIS être vert — même avec une clé publique parfaitement posée. Il annonçait
# « les archives partiraient EN CLAIR » pendant que le dépôt chiffré réussissait.
_age_ligne=$(grep -m1 '^Environment=AGE_RECIPIENT=' \
             /etc/systemd/system/uti-backup.service 2>/dev/null)
case "$_age_ligne" in
  "")                                 ko "AGE_RECIPIENT absent de l'unité uti-backup.service : les archives partiraient EN CLAIR (backup_db.sh refuse, donc rien ne part)" ;;
  *REMPLACER*|*remplacer*)            ko "AGE_RECIPIENT est encore à sa valeur gabarit : les archives partiraient EN CLAIR (backup_db.sh refuse, donc rien ne part)" ;;
  Environment=AGE_RECIPIENT=age1?*)   ok "chiffrement age actif (clé publique renseignée dans l'unité)" ;;
  *)                                  ko "AGE_RECIPIENT ne ressemble pas à une clé publique age (attendu : age1…)" ;;
esac

# — Condition 3 : une restauration a été faite POUR DE VRAI, et récemment.
#   C'est la seule qui transforme un fichier en sauvegarde.
if [ -f "$BACKUP_DIR/.derniere_repetition" ]; then
  age_j=$(( ( $(date -u +%s) - $(stat -c%Y "$BACKUP_DIR/.derniere_repetition") ) / 86400 ))
  [ "$age_j" -le 10 ] \
    && ok "restauration ÉPROUVÉE il y a $age_j j — $(cat "$BACKUP_DIR/.derniere_repetition")" \
    || ko "aucune restauration éprouvée depuis $age_j jours — bash $BACKEND/deploy/restore_drill.sh"
else
  ko "AUCUNE restauration n'a jamais été éprouvée. Tant que ce point est rouge, on possède un fichier et une croyance, pas une sauvegarde — bash $BACKEND/deploy/restore_drill.sh"
fi

printf '\n'
if [ "$ROUGE" -eq 0 ]; then
  printf '\033[32m✅ %s : tous les contrôles sont verts.\033[0m\n' "$(date -Is)"
  exit 0
fi
printf '\033[31m❌ %s : %d contrôle(s) rouge(s) — NE PAS supprimer Supabase.\033[0m\n' "$(date -Is)" "$ROUGE"
exit 1
