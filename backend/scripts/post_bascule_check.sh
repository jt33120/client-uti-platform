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
API="${API_URL:-http://127.0.0.1:8000}"
PGRST="${PGRST_URL:-http://127.0.0.1:8080}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/uti}"

ROUGE=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
ko()   { printf '  \033[31m✗\033[0m %s\n' "$1"; ROUGE=$((ROUGE+1)); }
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

grep -q '^SUPABASE_URL=http://127.0.0.1:8080' "$BACKEND/.env" \
  && ok ".env pointe sur la base locale" \
  || ko ".env pointe encore ailleurs : $(grep '^SUPABASE_URL=' "$BACKEND/.env")"

grep -q '^STORAGE_BACKEND=s3' "$BACKEND/.env" \
  && ok "STORAGE_BACKEND=s3" \
  || ko "STORAGE_BACKEND n'est pas à s3 — le stockage parle encore à Supabase"

grep -qi 'supabase\.co' "$BACKEND/.env" \
  && ko "il reste une URL supabase.co dans .env : $(grep -i 'supabase\.co' "$BACKEND/.env" | cut -d= -f1 | tr '\n' ' ')" \
  || ok "aucune URL supabase.co résiduelle dans .env"

titre "2. Comportements PostgREST dont le code dépend"

BACKEND_DIR="$BACKEND" "$BACKEND/venv/bin/python" - <<'PY'
# Chaque cas rejoue la syntaxe EXACTE d'un appel du dépôt. Un écart de forme
# invaliderait le test : c'est la forme qui casse, pas l'intention.
import os, sys
sys.path.insert(0, os.environ["BACKEND_DIR"])
from services.supabase_client import supabase

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
      lambda: supabase.table("appels_offres").select("*, clients(name)").limit(1).execute())

# routers/matching.py:243 — la jointure la plus fragile : deux relations, dont
# celle qui dépend de la FK ajoutée par migrations/0017_matchings_consultant_fk.sql.
essai("jointure matchings → consultants + submissions",
      lambda: supabase.table("matchings")
              .select("*, consultants(name, tjm, skills, employment_type), submissions(cv_url, cv_filename)")
              .limit(1).execute())

# routers/aos.py — agrégat embarqué, doit renvoyer [{'count': N}].
essai("agrégat appels_offres → submissions(count)",
      lambda: supabase.table("appels_offres").select("id, submissions(count)").limit(1).execute())

# 52 sites appellent .single() et comptent sur l'exception pour produire un 404
# (routers/auth.py:530 par exemple). Si .single() renvoyait None au lieu de lever,
# ces 52 sites répondraient 500.
def single_leve():
    try:
        supabase.table("profiles").select("*").eq("id", "00000000-0000-0000-0000-000000000000").single().execute()
    except Exception:
        return
    raise AssertionError(".single() sur 0 ligne n'a pas levé")
essai(".single() sur 0 ligne lève bien", single_leve)

# services/data_retention.py:174 — count exact, utilisé par les écrans admin.
def compte():
    r = supabase.table("submissions").select("id", count="exact").limit(1).execute()
    assert r.count is not None, "count est None"
essai('count="exact" renvoie un entier', compte)

# services/data_retention.py:118 — in_() sur liste vide ne doit pas planter.
essai("in_([]) renvoie [] sans erreur",
      lambda: supabase.table("submissions").select("id").in_("consultant_id", []).execute())

# services/email_outbox.py:enqueue traite un conflit d'unicité comme un SUCCÈS,
# en cherchant '23505' et 'duplicate' dans str(e). Ces chaînes viennent des
# messages de PostgreSQL : avec lc_messages en français, le mot « duplicate »
# disparaît et un doublon serait compté comme une panne d'envoi.
def conflit():
    try:
        supabase.table("app_settings").insert({"key": "notifications", "value": {}}).execute()
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

psql -d uti -v ON_ERROR_STOP=1 -tA -f "$BACKEND/migrations/verify_seed.sql" | while read -r ligne; do
  case "$ligne" in
    *MANQUANT*|*INERTE*) ko "$ligne" ;;
    *)                   ok "$ligne" ;;
  esac
done
# Le pipe crée un sous-shell : on recompte séparément pour que ROUGE soit juste.
manquants=$(psql -d uti -tA -f "$BACKEND/migrations/verify_seed.sql" | grep -cE 'MANQUANT|INERTE' || true)
[ "$manquants" -gt 0 ] && ROUGE=$((ROUGE+manquants))

titre "4. Stockage OVH — ce qui doit être privé l'est"

# services/storage.py distingue les buckets publics des privés par une ACL posée
# objet par objet. Si le conteneur OVH est lui-même en lecture publique, cette
# distinction ne sert à rien : tout devient lisible par URL. Un CV, c'est un nom,
# un téléphone et un parcours — le contrôle ci-dessous n'est pas théorique.
BASE=$(grep '^S3_PUBLIC_BASE_URL=' "$BACKEND/.env" | cut -d= -f2-)
if [ -n "$BASE" ]; then
  CLE_CV=$(BACKEND_DIR="$BACKEND" "$BACKEND/venv/bin/python" - <<'PY'
import os, sys
sys.path.insert(0, os.environ["BACKEND_DIR"])
from services import storage
objets = storage.list("cvs", "")
print(objets[0]["name"] if objets else "")
PY
)
  if [ -n "$CLE_CV" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "${BASE%/}/cvs/$CLE_CV")
    [ "$code" = "403" ] || [ "$code" = "401" ] \
      && ok "un CV n'est PAS lisible anonymement (HTTP $code)" \
      || ko "un CV répond $code en anonyme — le conteneur OVH est public, corriger MAINTENANT"
  else
    ok "aucun CV en stockage (base neuve) — contrôle à refaire après le premier envoi"
  fi
else
  ko "S3_PUBLIC_BASE_URL absent de .env"
fi

titre "5. Boucles de fond"

# services/scheduler.py:184 et :206 impriment ces marqueurs au démarrage. Leur
# absence signifie que le planificateur (liste 2, relances, purge RGPD, budget IA)
# et l'envoyeur d'e-mails ne tournent pas — panne totalement silencieuse.
journalctl -u uti-backend --since "-10 min" --no-pager 2>/dev/null | grep -q "\[SCHED\] planificateur" \
  && ok "planificateur démarré" || ko "marqueur [SCHED] absent des 10 dernières minutes"
journalctl -u uti-backend --since "-10 min" --no-pager 2>/dev/null | grep -q "\[OUTBOX\] envoyeur" \
  && ok "envoyeur d'e-mails démarré" || ko "marqueur [OUTBOX] absent des 10 dernières minutes"

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
if [ -f /etc/uti-backup.env ]; then
  # shellcheck disable=SC1091  # fichier de secrets, absent du dépôt par construction
  set -a; . /etc/uti-backup.env; set +a
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
grep -q '^Environment=AGE_RECIPIENT=age1' /etc/systemd/system/uti-backup.service 2>/dev/null \
  && ! grep -qi 'REMPLACER' /etc/systemd/system/uti-backup.service \
  && ok "chiffrement age actif (clé publique renseignée dans l'unité)" \
  || ko "AGE_RECIPIENT absent ou encore à sa valeur gabarit : les archives partiraient EN CLAIR (backup_db.sh refuse, donc rien ne part)"

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
