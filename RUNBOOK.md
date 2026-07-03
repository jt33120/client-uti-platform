# RUNBOOK — Exploitation de la plateforme en production

Aide-mémoire pour opérer le backend (VPS OVH) et le frontend (Vercel) une fois
en prod. Objectif : savoir **redémarrer, diagnostiquer et revenir en arrière**
sans réfléchir un jour de panne.

- **Frontend** : Vercel (auto-déploiement sur push `master`). Fichiers statiques.
- **Backend** : FastAPI en `systemd` sur le VPS OVH (`vps-cc93f2a8.vps.ovh.net`),
  derrière nginx (HTTPS Let's Encrypt), mono-worker uvicorn.
- **Base + Storage** : Supabase.

Connexion VPS : `ssh julian.talou@164.132.44.212`

---

## 1. Vérifier que tout va bien (30 secondes)

```bash
# Le backend répond ?
curl https://vps-cc93f2a8.vps.ovh.net/health          # → {"status":"ok"}

# Le service tourne ?
sudo systemctl status uti-backend --no-pager           # → active (running)
```

Si le `/health` répond `ok`, l'API est debout. Si le frontend Vercel affiche des
erreurs mais que `/health` est `ok`, le problème est côté Vercel/CORS/DB, pas le
process backend.

---

## 2. Redémarrer le backend

```bash
sudo systemctl restart uti-backend
sudo systemctl status uti-backend --no-pager
```

Le service est en `Restart=always` : il repart seul après un crash ou un reboot
du VPS. En cas de **crash-loop** (5 échecs en 60 s), systemd le met en échec et
arrête d'essayer — après avoir corrigé la cause :

```bash
sudo systemctl reset-failed uti-backend
sudo systemctl start uti-backend
```

---

## 3. Voir les logs

Le backend écrit sur stdout → capturé par `journald`.

```bash
# En direct
sudo journalctl -u uti-backend -f

# Les 200 dernières lignes
sudo journalctl -u uti-backend -n 200 --no-pager

# Depuis 1 h, uniquement les erreurs applicatives
sudo journalctl -u uti-backend --since "1 hour ago" | grep -E "\[ERROR\]|\[SCHED\]|\[STARTUP\]"
```

Marqueurs utiles dans les logs :
- `[ERROR] <METHOD> <path>: ...` → exception non gérée sur une requête (le client
  a reçu un 500 générique, la stack est dans le log).
- `[STARTUP] connexion Supabase OK` / `⚠️ Supabase injoignable` → état de la base
  au démarrage.
- `[SCHED] ...` → planificateur de notifications (liste 2, relances auto).

### Rotation / rétention des logs

`journald` gère la rotation. Pour éviter que les logs remplissent le disque sur
la durée, plafonner la rétention (une fois, en root) :

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\nMaxRetentionSec=1month\n' | \
  sudo tee /etc/systemd/journald.conf.d/retention.conf
sudo systemctl restart systemd-journald
```

---

## 4. Déployer une mise à jour du backend

Après un merge sur `master` :

```bash
ssh julian.talou@164.132.44.212 'bash ~/app/backend/deploy.sh'
```

`deploy.sh` fait : `git pull` + `pip install -r requirements.txt` +
`systemctl restart uti-backend` + affiche le statut.

> Le frontend, lui, se redéploie **tout seul** sur Vercel au push `master`.

---

## 5. Revenir en arrière (rollback)

**Backend** — revenir au commit précédent sur le VPS :

```bash
cd ~/app/backend
git log --oneline -5            # repère le commit stable précédent
git checkout <sha_stable> -- .  # ou : git reset --hard <sha_stable>
bash ~/app/backend/deploy.sh
```

**Frontend** — Vercel garde chaque déploiement : dans le dashboard Vercel →
projet → **Deployments** → sur un déploiement antérieur qui marchait →
**Promote to Production** (rollback instantané, sans toucher au code).

**Bascule d'URL API** (cas extrême) : `vercel.json` route `/api/*` vers le VPS.
En remettant une ancienne destination (ex. Railway) et en poussant `master`, on
rebascule le trafic API immédiatement.

---

## 6. Pannes fréquentes → quoi regarder

| Symptôme | Piste |
|---|---|
| `/health` ne répond pas | `systemctl status uti-backend` ; logs journald ; nginx up ? (`systemctl status nginx`) |
| L'appli répond 500 partout | Supabase joignable ? clé `SUPABASE_SERVICE_KEY` valide dans `.env` ? logs `[ERROR]` |
| Le service refuse de démarrer | `.env` cassé ou `JWT_SECRET` par défaut en prod (fail-closed voulu) → voir logs, corriger `.env`, `reset-failed` |
| Upload de CV en échec | taille > 25 Mo (limite nginx `client_max_body_size`) ; storage Supabase OK ? |
| Matching IA très lent / timeout | clé OpenRouter/OpenAI valide ? quotas ? le proxy coupe à 120 s |
| Pas d'e-mails envoyés | SMTP Infomaniak : `SMTP_USER`/`SMTP_PASSWORD` ; tester `python scripts/test_smtp.py` |
| RAM du VPS saturée | activer les garde-fous `MemoryHigh`/`MemoryMax` dans `uti-backend.service` |
| CORS bloqué (front) | l'origine Vercel doit correspondre à `FRONTEND_URL` / aux markers dans `main.py` |

---

## 7. Certificat HTTPS

Certbot renouvelle automatiquement. Pour vérifier / forcer :

```bash
sudo certbot certificates          # dates d'expiration
sudo certbot renew --dry-run       # test du renouvellement
```

---

## 8. Secrets — rappel

Uniquement dans `~/app/backend/.env` sur le VPS (jamais commités) :
`SUPABASE_SERVICE_KEY`, `OPENAI_API_KEY` / `OPENROUTER_KEY`, `JWT_SECRET`,
`SMTP_PASSWORD`, et le cas échéant `S3_ACCESS_KEY` / `S3_SECRET_KEY`.
En prod, `APP_ENV=production` (durcit `/docs`, headers, garde JWT).
