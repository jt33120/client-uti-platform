# Déploiement du backend sur le VPS OVH

> ## État au 9 septembre 2026 — à lire avant tout le reste
>
> **La Phase 1 de ce document est FAITE.** Le backend tourne sur le VPS OVH
> depuis juillet 2026 ; `frontend/vercel.json` y renvoie déjà `/api/*`. Railway
> n'est plus dans la boucle et le service a été supprimé.
>
> **La Phase 2 de ce document est ANNULÉE.** Le stockage objet OVH exigeait un
> accès au compte OVH du client qui n'a pas été obtenu. La destination retenue
> est le **disque du VPS** (`STORAGE_BACKEND=local`), pour 38 objets et 15 Mo —
> raisonnement dans `BASCULE.md`, « Pourquoi les fichiers vont sur le disque du
> VPS ». La section correspondante ci-dessous a été remplacée.
>
> **Ce document ne sert donc plus à migrer, mais à RECONSTRUIRE** : c'est la
> procédure à suivre si le VPS est perdu et qu'il faut le refaire. À ce titre il
> reste à jour et vérifié.
>
> Pour l'exploitation quotidienne : `RUNBOOK.md`.
> Pour la sortie de Supabase : `BASCULE.md` et `backend/scripts/bascule.sh`.
> Pour la vue d'ensemble : `ARCHITECTURE.md`.

**VPS** : `vps-cc93f2a8.vps.ovh.net` · IPv4 `164.132.44.212` · SSH port **1622** · user `julian.talou`
**Frontend** : Vercel, `plateforme.groupement-it.com` (DNS chez IONOS)
**Dépôt** : `github.com/jt33120/client-uti-platform`

---

## Ce qui tourne sur ce VPS

| Service | Unité systemd | Rôle |
|---|---|---|
| Backend FastAPI | `uti-backend` | uvicorn `127.0.0.1:8000`, **un seul worker** |
| Reverse proxy | `nginx` | HTTPS public (certbot) → backend |
| PostgreSQL 18 | `postgresql` | base `uti` — **installée, pas encore branchée** |
| PostgREST | `postgrest` | API REST devant PostgreSQL, `127.0.0.1:3000` |
| Sauvegarde | `uti-backup.timer` | horaire, chiffrée, déposée hors-site |
| Répétition de restauration | `uti-restore-drill.timer` | lundi 04h15 |
| Supervision | `uti-supervision.timer` | toutes les 15 minutes |

Les quatre derniers viennent de `backend/deploy/` et s'installent avec
`backend/deploy/install_db.sh` — voir `backend/deploy/INSTALLATION.md`.

---

## Pré-requis

1. **Accès SSH** : `ssh -p 1622 julian.talou@164.132.44.212`
2. Les **secrets** à portée de main : clés de base de données, `OPENROUTER_KEY`,
   `JWT_SECRET`, identifiants SMTP du fournisseur d'envoi (Resend).
3. Le backend est exposé sur l'adresse technique du VPS, qui pointe déjà vers
   `164.132.44.212`. **Aucune manipulation DNS n'est nécessaire** pour le
   remonter à l'identique.

> **Le seul levier qui réduirait vraiment le temps de reprise** est un nom DNS
> propre pour l'API — `api.groupement-it.com` → `164.132.44.212`, TTL 300. Il est
> **gratuit** (la zone est déjà payée) et doit être demandé au client. Sans lui,
> reconstruire sur une autre machine impose de repasser par Vercel pour changer
> `vercel.json`, donc un déploiement front. Avec lui, c'est un changement d'IP.

---

## 1. Installer le backend

### 1.1 — Outils système

```bash
ssh -p 1622 julian.talou@164.132.44.212
sudo apt update && sudo apt install -y python3-venv python3-pip nginx git \
     tesseract-ocr tesseract-ocr-fra postgresql-client
```

`tesseract` est le repli OCR des CV scannés (`services/cv_parser.py`) : sans lui
l'extraction se dégrade proprement, mais elle se dégrade.

### 1.2 — Le code

```bash
git clone https://github.com/jt33120/client-uti-platform.git ~/app
# ou, si déjà cloné :  cd ~/app && git pull origin master
```

### 1.3 — Environnement Python

```bash
cd ~/app/backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 1.4 — Variables d'environnement

```bash
cp .env.example .env && nano .env
```

Le minimum, et rien de plus (la liste complète fait autorité dans `config.py`) :

```ini
SUPABASE_URL=https://<projet>.supabase.co     # ou http://127.0.0.1:8080 après bascule
SUPABASE_SERVICE_KEY=<clé service_role>
JWT_SECRET=<openssl rand -hex 32>             # le défaut fait ÉCHOUER le démarrage
OPENROUTER_KEY=sk-or-...                      # les appels IA passent par OpenRouter
FRONTEND_URL=https://plateforme.groupement-it.com
SMTP_HOST=... SMTP_USER=... SMTP_PASSWORD=... SMTP_FROM=...
ADMIN_EMAIL=...
```

`STORAGE_BACKEND` n'est **pas** posé aujourd'hui : il vaut donc son défaut,
`supabase`. Il passera à `local` lors de la bascule, avec `PUBLIC_BASE_URL`
(sans laquelle le backend refuse de démarrer en mode local).

Contrôle : `python -c "from config import settings; print('OK', settings.smtp_host)"`

### 1.5 — Service systemd

```bash
sudo cp ~/app/backend/uti-backend.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now uti-backend
sudo systemctl status uti-backend --no-pager     # → active (running)
curl http://127.0.0.1:8000/health                # → {"status":"ok", ...}
```

⚠️ **Un seul worker uvicorn**, et c'est délibéré : le rate-limit et le
planificateur de notifications vivent en mémoire du processus. Passer en
multi-worker dupliquerait le planificateur.

### 1.6 — nginx + HTTPS

```bash
sudo cp ~/app/backend/nginx.conf /etc/nginx/sites-available/plateforme
sudo ln -sf /etc/nginx/sites-available/plateforme /etc/nginx/sites-enabled/plateforme
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d vps-cc93f2a8.vps.ovh.net
```

⚠️ **certbot RÉÉCRIT `/etc/nginx/sites-available/plateforme`** à chaque
renouvellement. C'est pour cela que la façade PostgREST vit dans un fichier
séparé (`backend/deploy/nginx-postgrest.conf`) : certbot ne doit jamais y toucher.

### 1.7 — Vérifier

```bash
curl https://vps-cc93f2a8.vps.ovh.net/health      # → {"status":"ok", "commit":"..."}
curl https://vps-cc93f2a8.vps.ovh.net/health/db   # → {"status":"ok","db":"reachable"}
```

Les deux, pas une seule : `/health` ne touche pas la base. Un backend
parfaitement démarré au-dessus d'une base injoignable répond `ok` sur la
première et 503 sur la seconde.

### 1.8 — Brancher le frontend (déjà fait)

`frontend/vercel.json` contient la destination de l'API :

```json
{ "source": "/api/:path*", "destination": "https://vps-cc93f2a8.vps.ovh.net/:path*" }
```

Sur une machine neuve à une autre adresse, c'est cette ligne qu'il faut changer,
puis pousser sur `master` — Vercel redéploie. Une reconstruction à l'identique
n'y touche pas.

---

## 2. Mettre à jour le backend

```bash
ssh -p 1622 julian.talou@164.132.44.212 'bash ~/app/backend/deploy.sh'
```

C'est **le seul chemin correct**. `deploy.sh` fait `git pull`, réinstalle les
dépendances, redémarre, puis valide sur trois sondes — `/health`, `/health/db`
et `POST /auth/login` attendu en 401 — et **revient automatiquement au commit
précédent** si l'une échoue.

Un `git pull && sudo systemctl restart uti-backend` à la main court-circuite les
trois sondes et le rollback ; il produit en plus des 502 fantômes, parce que
`systemctl restart` rend la main avant qu'uvicorn ait fini d'importer l'app.

---

## 3. La base de données sur le VPS

Cette partie remplace l'ancienne « Phase 2 ».

**Installation** de PostgreSQL 18, PostgREST, des rôles, de la façade nginx
interne, des sauvegardes et de la supervision :

```bash
bash ~/app/backend/deploy/install_db.sh
```

Tout est documenté pas à pas dans `backend/deploy/INSTALLATION.md`. Points qui
surprennent et qu'il vaut mieux connaître avant :

* **Il n'existe aucun mot de passe de base.** L'authentification est `peer` par
  socket UNIX, avec une correspondance `pg_ident` du compte UNIX `julian.talou`
  vers le rôle `uti_admin`. Donc `psql -U uti_admin -d uti` ✅ et
  `psql -h 127.0.0.1 -U uti_admin` ❌ (le `-h` force TCP, donc scram).
* **PostgREST n'écoute que sur `127.0.0.1`.** Le frontend ne parle jamais à la
  base ; seul le backend, sur cette machine, l'interroge.
* **La RLS est activée sur toutes les tables, sans aucune policy.** C'est un
  verrou, pas un oubli : l'autorisation métier est en Python.
* **Les `GRANT` ne couvrent que les tables existantes** au moment où
  `roles_postgrest.sql` tourne. Toute migration ultérieure doit être jouée en
  tant qu'`uti_admin`, ou reposer un `GRANT` explicite.

**Bascule** de la production vers cette base : elle est scriptée, en dix étapes
vérifiées, avec retour arrière.

```bash
bash ~/app/backend/scripts/bascule.sh --dry-run    # ne modifie rien
bash ~/app/backend/scripts/bascule.sh              # pour de vrai
bash ~/app/backend/scripts/bascule.sh --rollback   # revient à Supabase
```

C'est elle qui déplace aussi les fichiers vers `/var/lib/uti/files` et pose
`STORAGE_BACKEND=local`. Ne pas faire ces gestes à la main : leur **ordre** est
la seule chose qui compte, et il n'est pas intuitif (les fichiers et les URLs se
migrent pendant que `.env` désigne encore Supabase).

---

## 4. Sauvegardes et supervision

Une fois `install_db.sh` passé, trois minuteurs tournent seuls :

```bash
systemctl list-timers 'uti-*'                     # les voir
bash ~/app/backend/deploy/supervision.sh          # contrôle à la demande
PGUSER=uti_admin bash ~/app/backend/deploy/backup_db.sh   # sauvegarde à la demande
```

⚠️ **`PGUSER` doit être nommé.** Son absence est le défaut qui a fait échouer
silencieusement toutes les sauvegardes jusqu'au 26 août : sans lui, libpq
demandait le rôle « julian.talou », qui n'existe pas. Corrigé dans les scripts,
mais à connaître si l'on invoque `pg_dump` à la main.

Détail du dispositif — ce qu'il sauvegarde, où, avec quel chiffrement, et
comment on restaure : `RUNBOOK.md` §9 et §10.

---

## Secrets à ne jamais committer

`SUPABASE_SERVICE_KEY`, `OPENROUTER_KEY`, `MISTRAL_KEY`, `JWT_SECRET`,
`FILE_URL_SECRET`, `SMTP_PASSWORD`, les jetons `MIP_RUM_*` et `XSOM_*`, et
l'URI de connexion Supabase (`~/.supabase_db_uri`, en 0600).

Ils vivent uniquement dans `~/app/backend/.env` sur le VPS, ignoré par git.
Le dépôt est privé, mais un secret dans l'historique git y reste pour toujours.
