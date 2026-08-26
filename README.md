# G-IT — Plateforme Partenaires POC

Moteur de matching IA entre consultants et Appels d'Offres.  
Stack : **React + Vite** (frontend) · **FastAPI + Python** (backend) · **Supabase** (DB + Storage) · **OpenAI GPT-4o** (scoring)

---

## Architecture

```
poc-platform/
├── backend/               # FastAPI (Python)
│   ├── main.py            # App entry point
│   ├── config.py          # Settings (env vars)
│   ├── routers/
│   │   ├── auth.py        # Login / register
│   │   ├── consultants.py # CRUD + PDF upload
│   │   ├── aos.py         # Appels d'Offres CRUD
│   │   └── matching.py    # AI scoring endpoint
│   └── services/
│       ├── cv_parser.py     # PDF → text (pdfplumber)
│       ├── pseudonymize.py  # retrait des PII avant LLM (AI Act Art. 10)
│       ├── ai_matching.py   # extraction LLM des features (étape 1)
│       ├── scoring.py       # scoring déterministe & explicable (étape 2, Art. 13/15)
│       └── audit.py         # journal d'audit du matching (Art. 12)
├── frontend/              # React + Vite + Tailwind
│   └── src/
│       ├── pages/         # All pages
│       ├── components/    # Layout, shared UI
│       ├── contexts/      # AuthContext
│       └── lib/api.js     # Axios client
└── supabase_schema.sql    # Run this first in Supabase
```

---

## Setup en 4 étapes

### 1. Supabase

1. Créer un projet sur [supabase.com](https://supabase.com)
2. Aller dans **SQL Editor** et coller + exécuter `supabase_schema.sql`
3. Aller dans **Storage** → créer un bucket nommé **`cvs`** → le mettre en **public**
4. Récupérer dans **Settings → API** :
   - `Project URL` → `SUPABASE_URL`
   - `service_role` key → `SUPABASE_SERVICE_KEY`

### 2. Backend (FastAPI)

```bash
cd backend

# Créer un environnement virtuel
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Installer les dépendances
pip install -r requirements.txt

# Configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec vos vraies clés :
#   SUPABASE_URL=...
#   SUPABASE_SERVICE_KEY=...
#   OPENAI_API_KEY=sk-...
#   JWT_SECRET=une-chaine-aleatoire-longue

# Lancer le serveur
uvicorn main:app --reload --port 8000
```

API disponible sur : http://localhost:8000  
Documentation Swagger : http://localhost:8000/docs

### 3. Frontend (React)

```bash
cd frontend

# Installer les dépendances
npm install

# Lancer le dev server
npm run dev
```

App disponible sur : http://localhost:5173

> Le frontend proxifie `/api/*` vers `http://localhost:8000` automatiquement (config Vite).

### 4. Premier compte

Aller sur http://localhost:5173/register et créer :
- Un compte **Administrateur** (pour créer des AOs et lancer le scoring)
- Un compte **AO / Partenaire** (pour soumettre des consultants)

---

## Fonctionnement du Scoring IA

### Pipeline complet

```
1. PDF uploadé par l'AO
        ↓
2. pdfplumber extrait le texte du CV (avec layout)
        ↓
3. Texte stocké dans Supabase (table consultants.cv_text)
        ↓
4. Admin clique "Lancer le matching" sur un AO
        ↓
5. FastAPI envoie l'AO + tous les CVs à GPT-4o
        ↓
6. GPT-4o score chaque consultant /100 avec breakdown :
   - Compétences techniques  : /40
   - Séniorité               : /20
   - Contexte / domaine      : /20
   - Compatibilité TJM       : /20
        ↓
7. Résultats triés, sauvegardés en base
        ↓
8. Top 3 affichés avec :
   - Score animé (ring SVG)
   - Points forts / points de vigilance
   - Résumé de matching explicatif
   - Recommandation : FORT / MOYEN / FAIBLE
```

### Stratégie IA choisie

On utilise **GPT-4o en mode JSON structuré** plutôt que les embeddings cosine pour plusieurs raisons :
- **Explicabilité** : GPT-4o justifie son score en français, les embeddings sont des boîtes noires
- **Richesse contextuelle** : comprend les synonymes, le contexte métier, les acronymes tech
- **Fiabilité** : `response_format: json_object` garantit un JSON parsable
- **Précision** : température 0.2 pour des scores stables et cohérents

Pour de gros volumes (>50 CVs), le système batchifie automatiquement les appels API.

---

## Variables d'environnement

| Variable | Description | Requis |
|---|---|---|
| `SUPABASE_URL` | URL de votre projet Supabase | ✅ |
| `SUPABASE_SERVICE_KEY` | Clé `service_role` (admin) | ✅ |
| `OPENAI_API_KEY` | Clé API OpenAI (GPT-4o) | ✅ |
| `JWT_SECRET` | Secret pour signer les tokens | ✅ |
| `FRONTEND_URL` | URL du frontend (CORS) | optionnel |
| `SMTP_HOST` | Serveur SMTP — **aucun défaut** (voir `config.py`) | ✅ (emails) |
| `SMTP_PORT` | Port SMTP STARTTLS (défaut `587`) | optionnel |
| `SMTP_USER` | Compte SMTP (envoi des emails) | ✅ (emails) |
| `SMTP_PASSWORD` | Mot de passe SMTP | ✅ (emails) |
| `SMTP_FROM` | Adresse expéditeur (défaut = `SMTP_USER`) | optionnel |
| `SMTP_FROM_NAME` | Nom affiché de l'expéditeur (défaut `Plateforme GRP-IT`) | optionnel |
| `ADMIN_EMAIL` | Destinataire des notifications support/contact | ✅ (support) |

> Les emails transactionnels (invitations partenaires, formulaire de support)
> partent par SMTP standard — **Resend depuis le 26/08/2026**, et le code n'en
> sait rien : `services/email.py` ne connaît que `SMTP_HOST`. Changer de
> fournisseur est un changement de variables d'environnement.
>
> `SMTP_FROM` doit appartenir à un domaine **vérifié** chez le fournisseur,
> sinon l'envoi est refusé.
>
> Pour tester : `cd backend && python scripts/test_smtp.py` (renseignez
> `SMTP_TEST_TO`). ⚠️ Ce test prouve que le relais **accepte** le message, pas
> qu'il **arrive** : vérifiez la boîte du destinataire et le tableau de bord du
> fournisseur.

---

## Rôles

| Rôle | Peut faire |
|---|---|
| **Admin** | Créer AOs · Voir tous les consultants · Lancer le scoring IA · Voir les résultats |
| **AO** | Soumettre des consultants (nom + CV + TJM) · Voir les AOs · Voir ses propres consultants |

---

## Déploiement production

**Backend** → [Railway](https://railway.app) ou [Render](https://render.com) (gratuit)
```bash
# Procfile ou start command :
uvicorn main:app --host 0.0.0.0 --port $PORT
```

**Frontend** → [Vercel](https://vercel.com) (gratuit)
```bash
# Build command :
npm run build
# Output directory :
dist
# Env var à ajouter sur Vercel :
VITE_API_URL=https://your-backend.railway.app
```

> Penser à mettre à jour `vite.config.js` pour pointer vers l'URL du backend en prod.

---

## Coûts estimés

| Service | Coût |
|---|---|
| Supabase | Gratuit (500MB DB, 1GB Storage) |
| Vercel | Gratuit |
| Railway / Render | Gratuit (tier) |
| OpenAI GPT-4o | ~0.01–0.05€ par matching (selon taille CVs) |

**Coût total pour le POC : ~0€ + usage OpenAI**
