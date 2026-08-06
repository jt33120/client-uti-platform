#!/bin/bash
# Déploiement du backend sur le VPS, avec healthcheck et rollback automatique :
# si le backend ne répond plus après la mise à jour, on revient au commit
# précédent et on redémarre. Usage : bash ~/app/backend/deploy.sh
#
# TROIS sondes, et pas une.
#   /health      — le PROCESSUS vit.
#   /health/db   — il voit ses DONNÉES. Valider sur la première seule laissait
#                  passer un déploiement où le backend répond parfaitement et ne
#                  joint plus la base : le mode de panne exact d'un SUPABASE_URL
#                  erroné, c'est-à-dire de la migration en cours.
#   /auth/login  — il voit ses IDENTIFIANTS. Celle-ci vient d'un incident réel :
#                  la sortie de GoTrue a été déployée avant que la migration
#                  0019 ne soit appliquée à la base de production. Le processus
#                  vivait, la base répondait, les deux premières sondes étaient
#                  vertes — et TOUTE connexion renvoyait 503, parce que
#                  `user_credentials` n'existait pas encore. Le déploiement,
#                  mesuré ainsi, était un succès : aucun rollback ne pouvait se
#                  déclencher, et la panne a tenu des heures sans être vue.
#
# Chaque sonde couvre une dépendance que la précédente ne touche pas. La règle
# à retenir pour la suivante : une sonde vaut ce qu'elle CHARGE, pas ce qu'elle
# affirme.
set -e
cd /home/julian.talou/app/backend

# Adresse qui n'appartient à personne : le domaine n'existe pas. On attend 401
# (« email ou mot de passe incorrect »), preuve que la table des identifiants a
# été LUE ; un 503 dit qu'elle est illisible. Rien n'est créé, et une adresse
# inconnue n'a pas de compteur d'échecs à incrémenter — la sonde ne verrouille
# aucun compte.
SONDE_EMAIL="sonde-de-deploiement@sonde-interne-uti.fr"

# La sonde est tirée UNE SEULE FOIS par tentative, jamais dans la boucle
# d'attente : /auth/login limite à 8 requêtes par adresse sur 5 minutes
# (routers/auth.py), et quinze essais d'affilée feraient échouer la sonde pour
# la mauvaise raison. Un 429 est d'ailleurs toléré ci-dessous : il signifie
# « trop de déploiements rapprochés », pas « base cassée ».
sonde_auth() {
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
    -X POST http://127.0.0.1:8000/auth/login \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${SONDE_EMAIL}\",\"password\":\"sonde-de-deploiement\"}") || return 1
  case "$code" in
    401) return 0 ;;
    429) echo "   ⚠️  sonde d'authentification limitée (429) — déploiements rapprochés, contrôle non concluant" ; return 0 ;;
    *)   echo "   ❌ /auth/login a répondu $code (401 attendu) : la table user_credentials est-elle en place ?" ; return 1 ;;
  esac
}

sondes_de_base() {
  curl -sf --max-time 3 http://127.0.0.1:8000/health >/dev/null \
    && curl -sf --max-time 5 http://127.0.0.1:8000/health/db >/dev/null
}

PREV_SHA=$(git rev-parse HEAD)
git pull origin master
source venv/bin/activate
pip install -r requirements.txt -q
sudo systemctl restart uti-backend

# ── Healthcheck : jusqu'à 30 s pour que le backend ET la base répondent ──
health_ok=0
for _ in $(seq 1 15); do
  sleep 2
  if sondes_de_base; then
    health_ok=1
    break
  fi
done

# L'authentification n'est interrogée qu'une fois les deux premières sondes
# vertes : tant que la base est injoignable, son verdict n'apprendrait rien.
if [ "$health_ok" = "1" ] && ! sonde_auth; then
  health_ok=0
fi

if [ "$health_ok" = "1" ]; then
  echo "✅ Déploiement OK — backend en bonne santé ($(git rev-parse --short HEAD))"
  sudo systemctl status uti-backend --no-pager
else
  echo "❌ backend, base ou authentification injoignable — ROLLBACK vers $PREV_SHA"
  git reset --hard "$PREV_SHA"
  pip install -r requirements.txt -q
  sudo systemctl reset-failed uti-backend || true
  sudo systemctl restart uti-backend
  sleep 3
  if sondes_de_base && sonde_auth; then
    echo "↩️  Rollback effectué, l'ancienne version répond. Regarde les logs :"
    echo "    sudo journalctl -u uti-backend -n 100 --no-pager"
  else
    echo "🚨 Même l'ancienne version ne répond pas — diagnostic manuel requis (RUNBOOK §6)."
    sudo systemctl status uti-backend --no-pager || true
  fi
  exit 1
fi
