#!/bin/bash
# Déploiement du backend sur le VPS, avec healthcheck et rollback automatique :
# si le backend ne répond plus sur /health après la mise à jour, on revient au
# commit précédent et on redémarre. Usage : bash ~/app/backend/deploy.sh
set -e
cd /home/julian.talou/app/backend

PREV_SHA=$(git rev-parse HEAD)
git pull origin master
source venv/bin/activate
pip install -r requirements.txt -q
sudo systemctl restart uti-backend

# ── Healthcheck : jusqu'à 30 s pour que /health réponde ──────────────
health_ok=0
for i in $(seq 1 15); do
  sleep 2
  if curl -sf --max-time 3 http://127.0.0.1:8000/health >/dev/null; then
    health_ok=1
    break
  fi
done

if [ "$health_ok" = "1" ]; then
  echo "✅ Déploiement OK — backend en bonne santé ($(git rev-parse --short HEAD))"
  sudo systemctl status uti-backend --no-pager
else
  echo "❌ /health ne répond pas — ROLLBACK vers $PREV_SHA"
  git reset --hard "$PREV_SHA"
  pip install -r requirements.txt -q
  sudo systemctl reset-failed uti-backend || true
  sudo systemctl restart uti-backend
  sleep 3
  if curl -sf --max-time 3 http://127.0.0.1:8000/health >/dev/null; then
    echo "↩️  Rollback effectué, l'ancienne version répond. Regarde les logs :"
    echo "    sudo journalctl -u uti-backend -n 100 --no-pager"
  else
    echo "🚨 Même l'ancienne version ne répond pas — diagnostic manuel requis (RUNBOOK §6)."
    sudo systemctl status uti-backend --no-pager || true
  fi
  exit 1
fi
