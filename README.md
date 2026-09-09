# Groupement-IT — Plateforme Partenaires

Mise en relation entre des **appels d'offres** clients et un **vivier de consultants**
proposés par des partenaires, avec un scoring assisté par IA et une piste d'audit
conforme à l'AI Act.

**Stack** — React 18 + Vite (Vercel) · FastAPI + Python 3.11 (VPS OVH) ·
PostgreSQL + stockage de fichiers (Supabase aujourd'hui, voir ci-dessous) ·
Claude Haiku 4.5 / Sonnet 4.5 via OpenRouter, repli Mistral ·
authentification **maison** (Argon2id + TOTP).

---

## Où tourne quoi, aujourd'hui

Mesuré le 9 septembre 2026. Ce tableau prime sur tout le reste du document.

| Brique | Où elle tourne | Preuve |
|---|---|---|
| Frontend | **Vercel** — `plateforme.groupement-it.com` | `frontend/vercel.json` |
| Backend | **VPS OVH** — `vps-cc93f2a8.vps.ovh.net`, uvicorn derrière nginx, unité systemd `uti-backend` | `frontend/vercel.json:5` réécrit `/api/*` vers lui |
| **Base de données** | **Supabase** — 24 tables, projet `zeaqvlbimsstzgiabvrr` | `SUPABASE_URL` dans le `.env` du VPS |
| **Fichiers** | **Supabase Storage** — 38 objets, 3 buckets | `STORAGE_BACKEND` absent du `.env` → défaut `"supabase"` (`backend/config.py`) |
| Authentification | **Maison**, table `user_credentials` | `backend/routers/auth.py`, `backend/services/credentials.py` |
| E-mails | SMTP standard — **Resend** depuis le 26/08/2026 | `backend/services/email.py` ne connaît que `SMTP_HOST` |
| IA | **OpenRouter** (Claude), repli **Mistral** | `backend/config.py`, section « Modèles LLM » |

> **Une bascule vers le PostgreSQL du VPS est préparée mais N'A PAS EU LIEU.**
> Le VPS porte une pile complète — PostgreSQL 18, PostgREST, rôles, sauvegardes,
> supervision — qui n'est branchée sur rien. Le basculement est scripté et
> vérifié pas à pas : `bash backend/scripts/bascule.sh --dry-run`.
> Contexte et critères : `BASCULE.md`. Cartographie complète : `ARCHITECTURE.md`.

---

## Arborescence

```
client-uti-platform/
├── backend/                   # FastAPI — 22 routeurs, 152 endpoints
│   ├── main.py                #   montage, CORS, en-têtes de sécurité, sondes, boucles de fond
│   ├── config.py              #   toutes les variables d'env + garde-fous « fail-closed »
│   ├── deploy.sh              #   LE chemin de déploiement : 3 sondes + rollback automatique
│   ├── routers/               #   auth, aos, matching, partners, admin, emails…
│   ├── services/              #   38 modules : scoring, IA, e-mails, stockage, RGPD
│   ├── migrations/            #   SOURCE DE VÉRITÉ du schéma (voir « Base de données »)
│   ├── deploy/                #   PostgreSQL, PostgREST, rôles, sauvegardes, supervision
│   ├── scripts/               #   bascule, exports, contrôles, amorçage
│   └── tests/                 #   289 tests (pytest)
├── frontend/                  # React 18 + Vite + Tailwind — 32 pages
│   ├── src/lib/api.js         #   deux instances axios (authentifiée / publique)
│   └── vercel.json            #   réécriture /api/* vers le VPS + CSP
├── compliance/ai-act/         # 26 documents de conformité (AI Act, RGPD, DPIA)
├── ARCHITECTURE.md            # cartographie complète de la plateforme
├── RUNBOOK.md                 # exploitation au quotidien
├── BASCULE.md                 # sortie de Supabase : état, critères, procédure
└── supabase_*.sql             # HÉRITAGE — voir « Base de données » ci-dessous
```

---

## Installation locale

### 1. Base de données

**Pour travailler contre la production :** rien à créer. Demander `SUPABASE_URL`
et la clé `service_role` à un administrateur.

**Pour monter une base neuve :** appliquer, *dans cet ordre*, le schéma consolidé
puis les migrations postérieures.

```bash
psql "$URI" -v ON_ERROR_STOP=1 -f backend/migrations/schema.sql
for f in backend/migrations/00{19,20,21}_*.sql; do psql "$URI" -v ON_ERROR_STOP=1 -f "$f"; done
```

> Les 31 fichiers `supabase_*.sql` **à la racine du dépôt** sont un héritage.
> Ils ont bâti la base de production entre juillet et mi-août 2026, puis
> `backend/migrations/` a pris le relais sur **la même base**. Les deux lignées
> sont disjointes (aucune table en commun), mais leur ordre de dépendance n'est
> écrit nulle part : `backend/migrations/schema.sql` a été extrait d'une base
> réelle puis vérifié objet par objet contre la production, et c'est lui qu'il
> faut rejouer. Ne pas boucler sur `*.sql` à la racine.

### 2. Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # puis éditer — le minimum pour démarrer :
#   APP_ENV=dev                          ← sans lui, /docs est coupé et les gardes prod s'appliquent
#   SUPABASE_URL=https://<projet>.supabase.co
#   SUPABASE_SERVICE_KEY=<clé service_role>
#   OPENROUTER_KEY=sk-or-...             ← les appels IA passent par OpenRouter, PAS par OpenAI
#   JWT_SECRET=$(openssl rand -hex 32)   ← la valeur par défaut fait ÉCHOUER le démarrage en prod

uvicorn main:app --reload --port 8000
```

API sur http://localhost:8000 · Swagger sur `/docs` **uniquement si `APP_ENV=dev`**
(en production `/docs`, `/redoc` et `/openapi.json` sont coupés, `main.py`).
Sondes : `/health` (le processus vit) et `/health/db` (il voit ses données).

### 3. Frontend

```bash
cd frontend && npm install && npm run dev
```

App sur http://localhost:5173. Vite proxifie `/api/*` vers `http://localhost:8000`.

### 4. Premier compte

`/register` **n'est pas une inscription libre** : le serveur refuse toute
inscription sans jeton d'invitation valide (403), et seul un administrateur peut
en émettre. Sur une base vierge, c'est une boucle fermée — on la casse par script :

```bash
cd backend && source venv/bin/activate
python scripts/bootstrap_admin.py
```

---

## Matching et scoring

```
CV déposé (PDF, DOCX, XLSX)
     ↓  pdfplumber, avec repli OCR (Tesseract) sur les CV scannés
Texte extrait
     ↓  analyse VISION optionnelle (VISION_ENABLED) : les pages sont rendues EN IMAGE
     ↓  et lues par un modèle multimodal — jauges, étoiles, colonnes que le texte manque
CV structuré anonymisé (JSON canonique) — SOURCE DE VÉRITÉ du scoring et du surlignage
     ↓
Scoring DÉTERMINISTE (services/scoring.py) : compétences, séniorité, langues,
mobilité, disponibilité — pondérations réglables par l'administrateur
     ↓  + second avis du LLM, + rétroaction des décisions humaines (human_decision)
Classement, explications, recommandation FORT / MOYEN / FAIBLE
```

**Le score n'est pas produit par une IA.** Il est calculé par une grille
explicite et versionnée ; le LLM sert à l'extraction et à un second avis. C'est
ce qui rend le résultat explicable et contestable — exigence AI Act (art. 13, 14,
15), documentée dans `compliance/ai-act/`.

Chaque appel LLM écrit une ligne dans `ai_usage` avec le **coût réel** renvoyé par
OpenRouter (`services/ai_ledger.py`), et une surveillance de budget alerte sans
jamais couper l'IA.

---

## Rôles

| Rôle | Peut faire |
|---|---|
| **admin** | Tout : comptes, clients, habilitations, réglages de scoring, modèles d'e-mails, supervision |
| **commerce** | Staff UTI : AO, consultants, matching, envoi aux partenaires, suivi des CV |
| **ao** | Partenaire : voit les AO des clients où il est habilité, propose des consultants, suit ses CV |

Les gardes côté frontend (`ProtectedRoute`) sont du confort d'affichage :
l'autorisation réelle est côté backend (`require_admin`, `require_staff`).

---

## Variables d'environnement

Toutes sont déclarées dans `backend/config.py`, qui fait autorité.

| Variable | Description | Requis |
|---|---|---|
| `SUPABASE_URL` | Base de données. Aujourd'hui l'URL Supabase ; après bascule `http://127.0.0.1:8080` | ✅ |
| `SUPABASE_SERVICE_KEY` | Clé `service_role` | ✅ |
| `JWT_SECRET` | Signe les sessions. **Le démarrage échoue en prod si laissé au défaut** | ✅ |
| `APP_ENV` | `production` (défaut, durci) ou `dev` | optionnel |
| `OPENROUTER_KEY` | Tous les appels LLM | ✅ (IA) |
| `MISTRAL_KEY` | Repli quand OpenRouter est indisponible | optionnel |
| `FRONTEND_URL` | Origine du front (CORS, liens des e-mails) | optionnel |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | Envoi — **aucun serveur par défaut**, délibérément | ✅ (e-mails) |
| `SMTP_PORT` / `SMTP_FROM` / `SMTP_FROM_NAME` | Port 587 par défaut · expéditeur · nom affiché | optionnel |
| `ADMIN_EMAIL` | Destinataire des notifications support | ✅ (support) |
| `STORAGE_BACKEND` | `supabase` (défaut) · `s3` · `local` | optionnel |
| `PUBLIC_BASE_URL` | Origine HTTPS du **backend**. **Obligatoire si `STORAGE_BACKEND=local`** | conditionnel |
| `FILE_URL_SECRET` | Signe les URLs de fichiers. Vide = dérivée de `JWT_SECRET` par HMAC | optionnel |
| `VISION_ENABLED` | Analyse visuelle des CV (envoie les pages en image au LLM) | optionnel |
| `EXTRACTION_MODEL` / `SCORING_MODEL` / `DRAFT_MODEL` / `VISION_MODEL` / `ASSISTANT_MODEL` | Modèles par usage | optionnel |
| `MIP_RUM_*` / `XSOM_*` | Observabilité (traces HTTP et IA). Inactifs si absents | optionnel |

`config.py` **refuse de démarrer** dans quatre cas, délibérément : `JWT_SECRET`
au défaut en production, `STORAGE_BACKEND` inconnu, `STORAGE_BACKEND=local` sans
`PUBLIC_BASE_URL`, et `FILE_URL_SECRET` identique à `JWT_SECRET`.

> **E-mails.** Le fournisseur est un détail de configuration : `services/email.py`
> ne fait que du SMTP. `SMTP_FROM` doit appartenir à un domaine **vérifié** chez
> le fournisseur, sinon l'envoi est refusé. Test : `python scripts/test_smtp.py`
> — il prouve que le relais **accepte** le message, pas qu'il **arrive**.

---

## Déploiement

| Quoi | Où | Comment |
|---|---|---|
| Frontend | Vercel | automatique à chaque push sur `master` |
| Backend | VPS OVH | `ssh -p 1622 julian.talou@164.132.44.212 'bash ~/app/backend/deploy.sh'` |

`deploy.sh` fait `git pull`, réinstalle les dépendances, redémarre, puis valide
sur **trois sondes** — `/health` (le processus vit), `/health/db` (il voit ses
données), `POST /auth/login` attendu en 401 (il voit ses identifiants) — et
**revient automatiquement au commit précédent** si l'une échoue.

Un `git pull && systemctl restart` à la main court-circuite les trois et le
rollback : `systemctl restart` rend la main avant qu'uvicorn ait fini d'importer
l'application, d'où des 502 fantômes.

CI : `pytest` et le build front tournent sur chaque PR et sur `master`
(`.github/workflows/ci.yml`).

---

## Tests

```bash
cd backend && source venv/bin/activate && pytest -q
```

289 tests. Ils couvrent le scoring, l'anti-biais, l'authentification, le
stockage, les e-mails, le kit de sauvegarde et le versionnement du schéma —
aucun n'a besoin de réseau ni de base.

---

## Coûts

| Service | Coût |
|---|---|
| Vercel | gratuit |
| Supabase | gratuit (offre de base) |
| VPS OVH | payé par **le compte du client** |
| SMTP (Resend) | gratuit à ce volume |
| OpenRouter | à l'usage — suivi à la ligne près dans `ai_usage`, avec alerte de budget |
