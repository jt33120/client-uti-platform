# Déploiement du backend sur le VPS OVH

Guide pas-à-pas pour faire tourner le backend **FastAPI** sur le VPS OVH, en
**HTTPS**, puis basculer la production de Railway vers OVH — en toute sécurité
(Railway reste actif jusqu'à la bascule finale).

| | Avant | Après |
|---|---|---|
| Frontend | Vercel | **Vercel** (inchangé) |
| Backend | Railway | **VPS OVH** (`vps-cc93f2a8.vps.ovh.net`) |
| Base de données | Supabase | **Supabase** (inchangé) |
| Stockage fichiers | Supabase Storage | **OVH Object Storage** (Phase 2) |

> Le **frontend reste sur Vercel** : ce sont des fichiers statiques, inutile de
> les héberger sur le VPS. Seul le **backend** est migré.

**Infos VPS** : `vps-cc93f2a8.vps.ovh.net` · IPv4 `164.132.44.212` · user `julian.talou`
**Domaine** : `plateforme.groupement-it.com` (DNS chez IONOS)

---

## ✅ Pré-requis (à vérifier avant de commencer)

> Le backend est exposé sur l'adresse technique du VPS, **`vps-cc93f2a8.vps.ovh.net`**,
> qui pointe déjà vers `164.132.44.212`. **Aucune manipulation DNS n'est nécessaire.**
> (`plateforme.groupement-it.com` reste le domaine du frontend, sur Vercel.)

1. **Accès SSH** au VPS : `ssh -p 1622 julian.talou@164.132.44.212`
2. Les **secrets** à portée de main : clés Supabase, OpenAI, `JWT_SECRET`,
   mot de passe SMTP du fournisseur d'envoi (Resend depuis le 26/08/2026).

---

## Phase 1 — Backend sur OVH (toujours avec Supabase)

> À cette étape, le backend OVH utilise **encore Supabase** pour la base ET le
> stockage. On change juste *où tourne le code*. La migration du stockage vient
> en Phase 2.

### 1.1 — Se connecter et installer les outils système
```bash
ssh -p 1622 julian.talou@164.132.44.212

sudo apt update
sudo apt install -y python3-venv python3-pip nginx git
```

> **Repli OCR (CV scannés)** : si un CV est un scan/photo sans texte
> sélectionnable, l'extraction bascule automatiquement sur Tesseract OCR.
> Ça reste dégradé sans ce paquet (le CV est alors refusé comme
> « illisible », comme aujourd'hui) — installe-le pour l'activer :
> ```bash
> sudo apt install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng
> ```
> Rien à faire côté `requirements.txt` : les libs Python (`pytesseract`,
> `pymupdf`, `Pillow`) sont déjà dedans, `pip install -r requirements.txt`
> les installe avec le reste.

### 1.2 — Récupérer le code
```bash
mkdir -p ~/app && cd ~/app
# Si le repo n'est pas déjà cloné :
git clone https://github.com/jt33120/uti-platform.git .
# (si déjà cloné lors d'une session précédente :  git pull origin master )
```
> Le code doit se retrouver dans `~/app/backend` (c.-à-d. `/home/julian.talou/app/backend`).

### 1.3 — Environnement Python
```bash
cd ~/app/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 1.4 — Variables d'environnement
```bash
cp .env.example .env
nano .env
```
Renseigne **les mêmes valeurs que sur Railway** :
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
- `OPENAI_API_KEY`, `JWT_SECRET`
- `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `ADMIN_EMAIL`
- `FRONTEND_URL` = l'URL de ton frontend Vercel (pour le CORS)
- **Laisse `STORAGE_BACKEND=supabase` pour l'instant** (Phase 2 le passera à `s3`).

Test rapide de la config :
```bash
python -c "from config import settings; print('OK', settings.smtp_host)"
```

### 1.5 — Service systemd (le backend tourne en permanence)
```bash
sudo cp ~/app/backend/uti-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now uti-backend
sudo systemctl status uti-backend --no-pager     # doit être "active (running)"

# Vérifie en local sur le VPS :
curl http://127.0.0.1:8000/health                # → {"status":"ok"}
```

### 1.6 — Nginx + HTTPS
```bash
# Reverse proxy
sudo cp ~/app/backend/nginx.conf /etc/nginx/sites-available/plateforme
sudo ln -sf /etc/nginx/sites-available/plateforme /etc/nginx/sites-enabled/plateforme
sudo rm -f /etc/nginx/sites-enabled/default        # retire le site par défaut
sudo nginx -t && sudo systemctl reload nginx

# Certificat HTTPS (Let's Encrypt) — réécrit nginx pour ajouter le 443 + redirection
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d vps-cc93f2a8.vps.ovh.net
```

### 1.7 — Vérifier le backend OVH en HTTPS
Depuis **n'importe quelle machine** :
```bash
curl https://vps-cc93f2a8.vps.ovh.net/health   # → {"status":"ok"}
```
✅ Si tu obtiens `{"status":"ok"}` en **https**, le backend OVH est opérationnel.

### 1.8 — 🔀 Bascule de la production (Vercel → OVH)
> **À ne faire qu'une fois l'étape 1.7 validée.** C'est l'unique étape qui
> impacte la prod. Railway reste en place comme filet de sécurité.

Édite **`vercel.json`** à la racine du repo et remplace la destination de l'API :
```diff
-      "destination": "https://git-production-af3c.up.railway.app/:path*"
+      "destination": "https://vps-cc93f2a8.vps.ovh.net/:path*"
```
Commit + push sur `master`. Vercel redéploie le frontend, qui appelle désormais
le backend OVH. **Teste l'appli** (login, upload de CV, etc.).

> 🔙 **Rollback** : si quelque chose casse, remets l'ancienne URL Railway dans
> `vercel.json` et push — retour immédiat à l'état précédent.

Quand tout est stable depuis quelques jours, tu pourras supprimer le service Railway.

### 🔁 Mises à jour futures du backend
Après chaque `git push` sur `master`, déploie sur le VPS :
```bash
ssh -p 1622 julian.talou@164.132.44.212 'bash ~/app/backend/deploy.sh'
```

---

## Phase 2 — Stockage des fichiers vers OVH Object Storage

> À faire **après** que la Phase 1 soit stable. Le code supporte déjà les deux
> backends de stockage : on bascule via les variables d'environnement, puis on
> copie les fichiers existants.

### 2.1 — Créer le conteneur Object Storage (console OVH)
1. OVH Manager → **Public Cloud** → **Object Storage** → créer un conteneur
   **S3** (ex. région `GRA`), nommé p. ex. `uti-files`, en accès **public**.
2. Crée un **utilisateur S3** et récupère `access_key` + `secret_key`.
3. Note l'**endpoint S3** (ex. `https://s3.gra.io.cloud.ovh.net`) et l'**URL
   publique** du conteneur.

### 2.2 — Configurer le backend (VPS)
Dans `~/app/backend/.env` :
```ini
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://s3.gra.io.cloud.ovh.net
S3_REGION=gra
S3_ACCESS_KEY=...        # ta clé
S3_SECRET_KEY=...        # ton secret
S3_BUCKET=uti-files
S3_PUBLIC_BASE_URL=https://uti-files.s3.gra.io.cloud.ovh.net
```
> ⚠️ Mets **aussi** ces variables sur Railway si tu le gardes en parallèle.

### 2.3 — Copier les fichiers existants
```bash
cd ~/app/backend && source venv/bin/activate

# 1) Simulation (n'écrit rien) :
python scripts/migrate_storage_to_ovh.py --dry-run

# 2) Copie réelle des fichiers + mise à jour des URLs en base :
python scripts/migrate_storage_to_ovh.py --rewrite-db
```
Le script copie les buckets `cvs` et `avatars` vers OVH et réécrit les URLs
stockées en base (`cv_url`, `avatar_url`). **Il ne supprime rien sur Supabase**,
qui reste un filet de sécurité.

### 2.4 — Activer et vérifier
```bash
sudo systemctl restart uti-backend
```
Teste dans l'appli : un **nouveau CV** et un **avatar** s'uploadent bien, et les
**anciens fichiers** s'affichent toujours (URLs OVH). Vérifie une URL au hasard :
```bash
curl -I https://uti-files.s3.gra.io.cloud.ovh.net/cvs/<...>.pdf   # → 200 OK
```

Une fois validé sur la durée, tu peux supprimer les buckets Supabase Storage.

---

## Récapitulatif des secrets à NE jamais committer
`SUPABASE_SERVICE_KEY`, `OPENAI_API_KEY`, `JWT_SECRET`, `SMTP_PASSWORD`,
`S3_ACCESS_KEY`, `S3_SECRET_KEY` → uniquement dans `~/app/backend/.env` (sur le
VPS) et dans les variables Railway. Le fichier `.env` est ignoré par git.
