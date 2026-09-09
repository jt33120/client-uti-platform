# ARCHITECTURE — cartographie de la plateforme

> Ce document décrit **ce qui existe**, pas ce qui est prévu. Chaque affirmation
> renvoie au fichier qui la porte. Quand une chose n'est pas vérifiable depuis le
> dépôt — parce qu'elle vit dans un `.env` sur le VPS — c'est dit explicitement,
> avec la commande qui tranche.
>
> Pour les procédures d'exploitation : `RUNBOOK.md`.
> Pour l'histoire de la sortie de Supabase : `BASCULE.md`.
> Pour l'installation d'une machine neuve : `backend/deploy/INSTALLATION.md`.

---

## 1. Vue d'ensemble

```
                            NAVIGATEUR
                                │
             ┌──────────────────┴──────────────────┐
             │ HTML/JS statiques          /api/*   │
             ▼                                     ▼
   ┌───────────────────┐              ┌──────────────────────────┐
   │  VERCEL           │   rewrite    │  VPS OVH                 │
   │  React 18 + Vite  │─────────────▶│  164.132.44.212          │
   │  plateforme.      │  vercel.json │  vps-cc93f2a8.vps.ovh.net│
   │  groupement-it.com│              └──────────┬───────────────┘
   └───────────────────┘                         │
                                                 ▼
                                      ┌─────────────────────┐
                                      │ nginx :443 (certbot)│
                                      │  /files/  → backend │
                                      │  /        → backend │
                                      └──────────┬──────────┘
                                                 ▼
                                   ┌───────────────────────────┐
                                   │ uvicorn 127.0.0.1:8000    │
                                   │ FastAPI — UN SEUL worker  │
                                   │ systemd: uti-backend      │
                                   │  ├─ 22 routeurs           │
                                   │  ├─ scheduler (1 h)       │
                                   │  └─ outbox e-mail (20 s)  │
                                   └──────┬──────────────┬─────┘
                                          │              │
              ┌───────────────────────────┘              └────────────┐
              ▼                                                       ▼
  ┌───────────────────────────┐                        ┌──────────────────────┐
  │ nginx 127.0.0.1:8080      │                        │ /var/lib/uti/files   │
  │   /rest/v1/ → :3000/      │                        │ (0700 julian.talou)  │
  └────────────┬──────────────┘                        │  cvs/ avatars/       │
               ▼                                       │  ao-sources/         │
  ┌───────────────────────────┐                        │  compliance/         │
  │ PostgREST 127.0.0.1:3000  │                        │  email-assets/       │
  │ systemd: postgrest        │                        └──────────────────────┘
  └────────────┬──────────────┘
               ▼ socket UNIX
  ┌───────────────────────────┐
  │ PostgreSQL 18 — base uti  │
  │ 4 rôles, RLS, peer auth   │
  └───────────────────────────┘

  TIERS SORTANTS (aucun n'entre) :
   OpenRouter (LLM) · Mistral (repli) · SMTP Resend · OVH Object Storage (sauvegardes)
   MIP RUM (traces) · xSOM (métriques IA) · healthchecks.io (chien de garde)
```

**Le point de bascule est `frontend/vercel.json:5`** : Vercel réécrit `/api/*`
vers `https://vps-cc93f2a8.vps.ovh.net/*`. Le navigateur ne connaît donc qu'une
seule origine, celle de Vercel — c'est aussi ce qui fait que le CSP du front
(`connect-src 'self'`) suffit.

---

## 2. Les trois plans d'exécution

| Plan | Ce qui y tourne | Qui paie | Qui redéploie |
|---|---|---|---|
| **Vercel** | frontend statique React (build Vite) | compte Vercel `julian-talous-projects` | push sur `master` → build automatique |
| **VPS OVH** | backend, base, fichiers, sauvegardes, supervision | compte OVH **du client** | `bash ~/app/backend/deploy.sh` |
| **Tiers** | LLM, SMTP, observabilité, dépôt hors-site | comptes API dédiés | — |

Un seul VPS, un seul worker uvicorn, une seule base. **Il n'y a pas de
redondance** : c'est un choix assumé au volume actuel (16 Mo de base, ~38
fichiers, ~50 Mo), documenté avec ses seuils de bascule dans
`backend/deploy/backup_db.sh` et `backend/nginx.conf`.

---

## 3. Le chemin d'une requête, de bout en bout

1. Le navigateur appelle `/api/aos` sur `plateforme.groupement-it.com`.
2. **Vercel** réécrit vers `https://vps-cc93f2a8.vps.ovh.net/aos` (`vercel.json`).
3. **nginx** (443, certificat certbot) proxifie vers `127.0.0.1:8000`, en posant
   `X-Real-IP` — le seul en-tête d'IP non falsifiable, et donc le seul auquel le
   backend se fie (`services/ratelimit.py:22`, `routers/auth.py:_client_ip`).
4. **FastAPI** : middleware CORS à origine dynamique (`main.py:is_allowed_origin`,
   regex ancrée sur les previews Vercel de ce compte), en-têtes de sécurité,
   middleware MIP RUM.
5. La dépendance `get_current_user` décode le JWT **et re-vérifie l'état du
   compte en base** (cache 60 s) : une suspension prend effet en moins d'une
   minute au lieu d'attendre l'expiration du jeton à 3 h.
6. Le routeur appelle `services/supabase_client.supabase` — qui est un client
   **supabase-py pointé sur la façade PostgREST locale**, pas sur Supabase.
7. **nginx :8080** traduit `/rest/v1/aos` en `/aos` (la barre oblique finale du
   `proxy_pass` fait tout le travail) et transmet à **PostgREST :3000**.
8. **PostgREST** ouvre la socket UNIX de **PostgreSQL** au nom du rôle
   `authenticator`, qui bascule vers `service_role` selon le JWT.
9. Le JSON remonte la même chaîne en sens inverse.

---

## 4. Frontend — Vercel

- **React 18 + Vite 5 + Tailwind 3**, `frontend/package.json`.
- 32 pages, 35 routes (`frontend/src/App.jsx`), 26 composants, 2 contextes
  (`AuthContext`, `ConfirmContext`).
- **Aucune dépendance `@supabase/*`.** Le front n'a jamais parlé à la base : il
  ne connaît que l'API FastAPI. C'est ce qui permet à PostgREST de n'écouter que
  sur la boucle locale.
- `lib/api.js` : deux instances axios. `api` attache le JWT depuis
  `localStorage` et redirige sur 401 ; `publicApi` n'a **aucun intercepteur** —
  utilisée par les pages publiques (`/client-review/:token`) pour ne jamais
  fuiter le jeton du staff sur une route anonyme.
- Chargement paresseux avec **auto-récupération de chunk** (`lazyWithReload`) :
  après un déploiement, un onglet resté ouvert référence des fichiers hashés
  disparus ; la page se recharge une fois au lieu de rester blanche.
- Pages lourdes sorties du bundle principal : `GraphPage` (force-graph),
  `CartePage` (Leaflet), `AdminPage`, `SupervisionPage`, `TicketsPage`,
  `ScoringSettingsPage`, `EmailsPage`.
- CSP posé par Vercel (`vercel.json`) : `default-src 'self'`, pas de
  `unsafe-eval`, `frame-ancestors 'none'`.
- Garde de rôle côté client : `ProtectedRoute roles={STAFF|ADMIN}` — **confort
  d'affichage, jamais une sécurité** ; l'autorisation réelle est côté backend
  (`require_staff` / `require_admin`).

---

## 5. Backend — FastAPI sur le VPS

`backend/main.py` monte **22 routeurs**, soit 152 endpoints :

| Routeur | Préfixe | Rôle |
|---|---|---|
| `auth` | `/auth` | connexion, MFA TOTP, mot de passe, profil (18) |
| `admin` | `/admin` | comptes, réglages, journal d'erreurs, supervision IA (22) |
| `aos` | `/aos` | appels d'offres, pièces jointes, rédaction IA (22) |
| `matching` | `/matching` | lancement et lecture des scores (17) |
| `partners` | `/partners` | partenaires, habilitations, conformité (14) |
| `consultants` | `/consultants` | vivier consultants (9) |
| `email_templates` | `/email-templates` | modèles éditables, aperçu, diffusion (7) |
| `pacs` | `/pacs` | modèles d'habilitation (7) |
| `clients`, `submissions` | | comptes clients, candidatures (6 chacun) |
| `files` | `/files` | service des fichiers du disque local (3) |
| `invitations`, `cartography` | | invitations, graphe/carte (3) |
| `emails` | `/emails` | **désabonnement public** (2) |
| `client_review`, `decisions`, `notifications`, `scoring_config`, `support` | | (2 chacun) |
| `assistant`, `cv`, `gdpr` | | assistant IA, CV, effacement RGPD (1 chacun) |

**Contraintes structurantes du processus** (`backend/uti-backend.service`) :

- **`--workers 1`, délibérément.** Le rate-limit (`services/ratelimit.py`) et
  l'anti-force-brute (`routers/auth.py:_throttle`) vivent en mémoire du
  processus, et le planificateur est une boucle asyncio unique. Passer en
  multi-worker dupliquerait le scheduler → double envoi d'e-mails.
- `TimeoutStopSec=90` : un matching IA peut durer 1 à 2 minutes, on ne le coupe
  pas au redémarrage.
- Anti crash-loop : 5 échecs en 60 s → arrêt. Relance :
  `sudo systemctl reset-failed uti-backend`.

**Deux boucles de fond** démarrées au startup (`services/scheduler.py`) :

| Boucle | Cadence | Ce qu'elle fait |
|---|---|---|
| `run_scheduler` | 1 h | liste 2 des AO à échéance, relances auto, auto-archivage des AO échus, surveillance du budget IA, purge RGPD |
| `run_outbox` | 20 s | dépile la file d'e-mails, une seule session SMTP par lot |

Les deux sont séparées à dessein : une erreur dans les relances ne doit pas
empêcher un lien de réinitialisation de partir.

---

## 6. Base de données — le remplacement de Supabase

C'est la partie la plus importante à comprendre, parce que **le code parle
toujours « supabase »** alors que Supabase n'est plus dans la boucle.

### Le montage

```
services/supabase_client.py     create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        │                        ↓ supabase-py construit {URL}/rest/v1/<table>
        ▼
nginx 127.0.0.1:8080            /rest/v1/  →  proxy_pass http://127.0.0.1:3000/
        │                        (la barre finale REMPLACE le préfixe)
        ▼
PostgREST 127.0.0.1:3000        sert les tables à la racine
        │                        rôle authenticator → service_role selon le JWT
        ▼
PostgreSQL 18, base « uti »     socket UNIX, authentification peer
```

`backend/deploy/nginx-postgrest.conf` **n'existe que pour cette traduction** :
supabase-py code en dur `rest_url = f"{SUPABASE_URL}/rest/v1"`, PostgREST n'a
aucune option de préfixe. Le fichier est séparé de `backend/nginx.conf` parce
que certbot réécrit celui du site public à chaque renouvellement.

Le bloc `/auth/v1/` de cette même façade renvoie **501** avec un message
explicite : c'est le garde-fou qui empêche qu'un reste d'appel à GoTrue soit
traduit en « email ou mot de passe incorrect » et coûte une heure de diagnostic.

### Les rôles (`backend/deploy/roles_postgrest.sql`)

| Rôle | Ce qu'il est |
|---|---|
| `anon` | visiteur non authentifié |
| `authenticated` | utilisateur connecté |
| `service_role` | contourne la RLS — c'est celui que le backend utilise |
| `authenticator` | `NOINHERIT`, celui avec lequel PostgREST se connecte ; il bascule vers les trois autres |
| `uti_admin` | propriétaire des objets, celui des migrations et des sauvegardes |

**La RLS est active sur toutes les tables, avec zéro politique.** Ce n'est pas
un oubli : c'est un verrou. L'autorisation métier est écrite dans le backend
Python (`require_staff`, `require_admin`, filtrage par `partner_clients`), pas
en SQL. La RLS sans politique garantit que si quelqu'un se connectait un jour
avec autre chose que `service_role`, il ne verrait rien du tout.

Le piège documenté au §4 du même fichier : `ALTER DEFAULT PRIVILEGES` ne couvre
que les objets créés par le rôle qui l'a posé. Une table créée par un autre rôle
naît sans droits pour `service_role` — d'où le `GRANT` explicite dans
`0021_email_optouts.sql`.

### Authentification PostgreSQL : il n'y a aucun mot de passe

`backend/deploy/install_db.sh` configure `pg_hba.conf` en `peer` sur la socket
UNIX, et `pg_ident.conf` mappe l'utilisateur UNIX `julian.talou` → rôle
`uti_admin`. **Aucun mot de passe de base n'existe.** Conséquence pratique :
toute commande `psql` doit passer par la socket, jamais par `-h 127.0.0.1` (qui
force TCP, donc scram, donc échec).

```bash
ssh -p 1622 julian.talou@164.132.44.212
psql -U uti_admin -d uti          # ✅
psql -h 127.0.0.1 -U uti_admin    # ❌ demandera un mot de passe qui n'existe pas
```

### Les tables

22 tables dans `backend/migrations/schema.sql` + 2 ajoutées par migrations
postérieures (`user_credentials` en 0019, `email_optouts` en 0021) :

`profiles` · `user_credentials` · `consultants` · `clients` · `appels_offres` ·
`submissions` · `matchings` · `human_decision` · `ao_consultant_state` ·
`partner_clients` · `partner_compliance_docs` · `pacs` · `pac_clients` ·
`client_reviews` · `invitations` · `email_outbox` · `email_templates` ·
`email_optouts` · `partner_email_log` · `support_messages` · `audit_log` ·
`ai_usage` · `app_settings` · `scoring_config`

### Versionnement du schéma

- `backend/migrations/schema.sql` — l'état de référence, rejouable.
- `0001` … `0021` — les migrations postérieures, appliquées **à la main** par
  `psql -f` (il n'y a pas d'outil de migration ; c'est assumé au rythme actuel).
- `backend/scripts/check_schema_drift.py` rejoue `schema.sql` **plus toutes les
  migrations ≥ 0019** dans une base jetable et compare à la production. Les
  migrations 0001-0018 sont déjà intégrées dans `schema.sql` ; les suivantes ne
  le sont pas, d'où la constante `PREMIERE_MIGRATION_HORS_SCHEMA = 19`.
- `backend/tests/test_schema_versioned.py` vérifie que **toute table nommée dans
  le code Python est reconstruite par le rejeu** — le test qui aurait attrapé
  l'oubli de `user_credentials`.

---

## 7. Authentification maison

GoTrue (Supabase Auth) est mort. Ce qui l'a remplacé vit entièrement dans
`backend/routers/auth.py` + `backend/services/{passwords,credentials}.py`.

| Élément | Choix | Pourquoi |
|---|---|---|
| Hachage | **Argon2id** (`argon2-cffi`) | bcrypt tronque silencieusement au-delà de 72 octets et son coût est purement CPU, donc parallélisable sur GPU. Argon2id impose un coût **mémoire** : 10 000 essais simultanés exigeraient 190 Gio de RAM |
| Stockage | table **`user_credentials`**, séparée de `profiles` | six endpoints font `select("*")` sur `profiles` et renvoient la ligne au navigateur. Un `password_hash` dans `profiles` partirait dans le navigateur au premier chargement du profil. Une table distincte rend la fuite **impossible par construction** |
| Session | JWT HS256, 3 h, signé avec `JWT_SECRET` | non révocable — d'où la re-vérification de l'état du compte en base à chaque requête (cache 60 s) |
| Second facteur | **TOTP** (`pyotp`), QR en SVG data-URI | jeton de défi court (10 min) entre le mot de passe et le code, avec un champ `stage` qui empêche ce jeton d'authentifier un appel API |
| Anti-force-brute | **deux étages** | `_throttle` en mémoire refuse la requête **avant** les 44 ms d'Argon2 (protège le CPU) ; le compteur en base (`credentials.py`) survit aux redémarrages. 5 échecs → verrou 1/5/15/30 min, **plafonné à 30 min** pour qu'on ne puisse pas verrouiller volontairement le compte du dirigeant |
| Rôles | `admin`, `commerce`, `ao` (partenaire) | `require_admin` / `require_staff` |

Le mot de passe oublié et l'invitation passent par des jetons signés à durée de
vie courte, envoyés via la file d'e-mails.

---

## 8. Stockage des fichiers

`backend/services/storage.py` — **trois backends derrière une seule interface**,
choisis par `STORAGE_BACKEND` :

| Valeur | Où vont les fichiers |
|---|---|
| `supabase` | Supabase Storage (historique) |
| `s3` | OVH Object Storage via boto3 |
| `local` | disque du VPS, sous `/var/lib/uti/files` |

Les appelants ne voient que des noms logiques de « bucket » : `cvs`, `avatars`,
`ao-sources`, `compliance`, `email-assets`. En S3 ce sont des préfixes de clé,
en local des sous-répertoires.

**Ce qui change vraiment en mode local**, c'est servir les fichiers privés.
Supabase et S3 signent une URL que le navigateur ouvre directement. En local,
c'est le backend qui sert (`routers/files.py`) — et cette URL est ouverte **sans
en-tête `Authorization`** (nouvel onglet, balise `<img>`, lien dans un e-mail au
client). La preuve d'autorisation doit donc tenir dans l'URL :

- forme `/files/d/<jeton>` — **le chemin de l'objet vit à l'intérieur du jeton
  signé**, et nulle part ailleurs. Une signature qui ne couvre pas le chemin
  serait un jeton d'accès arbitraire ; ici la question ne peut pas se poser.
- clé de signature **dérivée de `JWT_SECRET` par HMAC** avec un domaine dédié.
  `config.py` refuse de démarrer si `FILE_URL_SECRET == JWT_SECRET` : le jeton
  de fichier circule dans des URLs, donc dans des journaux et des boîtes mail ;
  celui de session dans un en-tête. Pas la même exposition.
- **`access_log off` sur `/files/`** dans `backend/nginx.conf`. Sans cette ligne,
  quiconque lit `/var/log/nginx/access.log` récupère des jetons utilisables
  depuis n'importe où — et le lien de CV envoyé au client final vaut **7 jours**.
- liste **blanche** des buckets publics : `{avatars, email-assets}`. Tout le
  reste naît privé — y compris les attestations URSSAF et KBIS, qui étaient
  passées en `public-read` sur S3 avec l'ancien test `bucket == "cvs"`.
- pas de `X-Accel-Redirect`, décision motivée dans `nginx.conf` : nginx devrait
  pouvoir lire un répertoire en 0700, et la décision « qui a le droit de lire »
  vivrait à deux endroits. Seuil de bascule écrit : un objet > 100 Mo.
- modes UNIX explicites : `0700` répertoires, `0600` fichiers — pas d'umask, qui
  laisserait les CV lisibles par tout compte local.

`config.py` refuse aussi de démarrer si `STORAGE_BACKEND` a une valeur inconnue
(une faute de frappe retombait autrefois **silencieusement sur Supabase**), et
si `local` est choisi sans `PUBLIC_BASE_URL` (les URLs seraient relatives, donc
résolues sur le domaine Vercel où rien ne répond).

---

## 9. E-mails

Chaîne complète, du dépôt à la boîte de réception :

```
appelant métier
   └─▶ email_outbox.enqueue()        ← filtre les désabonnés AVANT d'écrire
          └─▶ table email_outbox     status=queued
                 └─▶ run_outbox (20 s) → _claim() → SmtpSession (1 par lot)
                        ├─ succès → status=sent
                        └─ échec  → replanifié 1/5/15/60/180/360 min, 6 essais, puis « dead »
```

- **`services/email.py`** : coquille de marque unique (`render_email_html`),
  construction MIME (`build_message`), session SMTP réutilisable sur tout un lot
  avec une reconnexion automatique. Aucun serveur SMTP par défaut — un défaut
  cachait une configuration manquante derrière un comportement plausible.
- **`services/email_templates.py`** : 13 modèles éditables depuis
  l'administration (`ao_new`, `ao_relance`, `invite`, `password_reset`,
  `password_migration`, `annonce_pilote`, `cv_retenu`, `cv_non_retenu`,
  `cv_envoye_client`, `echange_commercial`, `affaire_gagnee`, `affaire_perdue`,
  `cv_client`), avec aperçu et envoi de test rendus par **la même fonction** que
  l'envoi réel.
- **`services/email_optout.py`** : désabonnement par catégorie. Jeton JWT signé
  avec une clé dérivée de `JWT_SECRET` et un `aud` explicite, **sans expiration**
  — un lien de désabonnement périmé pousse vers le bouton Spam, qui coûte au
  domaine d'envoi entier. En-têtes **RFC 2369 / RFC 8058** (`List-Unsubscribe` +
  `List-Unsubscribe-Post`) pour le bouton natif de Gmail et Outlook.
  Lecture *fail-open*, écriture *fail-closed*.
- **`routers/emails.py`** : `GET`/`POST /emails/unsubscribe`, publics, limités à
  30 requêtes/h par IP, et qui rendent **toujours une page HTML lisible**, même
  pour un jeton malformé — la page ne dépend pas du frontend.
- Les e-mails transactionnels (mot de passe, invitation) n'ont **pas** de lien de
  désabonnement : s'y désabonner reviendrait à se couper l'accès à son compte.

Fournisseur actuel : **Resend** depuis le 26/08/2026. Le code ne le sait pas —
`services/email.py` ne connaît que `SMTP_HOST`.

---

## 10. IA

Tous les appels LLM passent par **OpenRouter** (client OpenAI-compatible), avec
**Mistral** en repli.

| Système | Modèle par défaut | Où |
|---|---|---|
| `matching/extract` | Haiku 4.5 | `services/ai_matching.py` — features du CV |
| `matching/score` | Haiku 4.5 | `services/llm_scoring.py` — 2ᵉ avis sur le score |
| `matching/synthesis` | Haiku 4.5 | `services/matching_synthesis.py` |
| `matching/refusal` | Haiku 4.5 | `services/refusal_reason.py` |
| `ao/summary` | Haiku 4.5 | résumé d'AO en une phrase |
| `ao/draft` | Sonnet 4.5 | `services/ao_drafter.py` — rédaction de fiche |
| `cv/harmonize` | Sonnet 4.5 | `services/cv_harmonizer.py` — CV au format GRP-IT, **anonymisé** |
| `cv/vision` | Sonnet 4.5 | `services/cv_vision.py` — lit les pages **rendues en image** |
| `assistant/chat` | Sonnet 4.5 | assistant conversationnel in-app |

Tous configurables par variable d'environnement : un retrait de modèle upstream
est un changement de `.env`, pas un redéploiement.

**Le scoring est hybride, pas « IA ».** `services/scoring.py:score_consultant`
est déterministe (compétences, séniorité, langues, mobilité, disponibilité,
pondérations en étoiles réglables par l'admin). Le LLM donne un second avis, et
les retours humains (`human_decision`) rétroagissent sur les rangs.

**Pipeline CV** : `cv_parser` (pdfplumber, repli OCR pytesseract à moins de 50
caractères) → `cv_vision` (PyMuPDF rasterise, modèle multimodal — voit les
jauges, étoiles, colonnes que le texte seul manque) → `cv_structured` (JSON
canonique anonymisé, **source de vérité** du scoring et du surlignage) →
`cv_harmonizer`. Chaque étage est *best-effort* et retombe proprement sur le
précédent. `VISION_ENABLED=false` coupe la vision : elle envoie les pages du CV
**en image** (donc photo et identité) au fournisseur LLM, ce qui est un flux de
données personnelles distinct du texte.

**Registre de coût** (`services/ai_ledger.py`) : une ligne dans `ai_usage` par
appel, avec le **coût réel renvoyé par OpenRouter** (pas une estimation),
l'`generation_id` pour réconciliation, et l'attribution (compte, système, AO,
consultant) propagée par `contextvars`. Écriture non bloquante dans un thread
démon. `services/ai_budget.py` compare la dépense aux plafonds hebdo/mensuel et
**alerte sans jamais couper**.

**Observabilité** : `mip_rum_middleware.py` émet un span `http.server` par
requête, `mip_rum_ai.py` un span OTel `gen_ai` par appel LLM — **métadonnées
seulement, aucun contenu de prompt**. Double émission vers MIP RUM et xSOM.

**Conformité AI Act** : 26 documents dans `compliance/ai-act/` (gouvernance,
information des personnes, gestion des risques, plan de test de biais,
journalisation, supervision humaine, DPIA, registre des sous-traitants,
politique de conservation). Tests anti-biais dans `backend/tests/bias/`.

---

## 11. Sauvegardes, supervision, reprise

Depuis que la base **et** les fichiers vivent sur le VPS, plus personne ne les
sauvegarde à notre place. Le dispositif est dans `backend/deploy/` :

| Unité systemd | Cadence | Ce qu'elle fait |
|---|---|---|
| `uti-backup.timer` | **horaire** | `pg_dump` custom + répertoire des fichiers → chiffrement **age** → dépôt hors-site OVH → rotation 72 h / 14 j / 8 sem |
| `uti-restore-drill.timer` | **lundi 04:15** | restaure réellement dans une base jetable, compte les lignes, vérifie que **chaque fichier référencé existe** |
| `uti-supervision.timer` | **15 min** | disque 85 %, PostgreSQL vivant, PostgREST répond **401** (un 200 serait pire qu'une panne), `/health` + `/health/db`, âge de la dernière sauvegarde réussie (3 h), âge de la dernière répétition (10 j), bases `uti_drill_%` orphelines |

Choix qui méritent d'être connus :

- **`pg_dump` horaire plutôt que pgBackRest.** À 16 Mo, la fréquence remplace le
  PITR. pgBackRest apporterait `archive_command` — et si l'archivage des WAL
  échoue, PostgreSQL cesse de recycler `pg_wal`, le disque se remplit, **la
  production s'arrête**. On échangerait « perdre au pire une heure » contre « la
  base tombe parce que la sauvegarde a un problème ». Seuils de bascule écrits :
  base > 2 Go, ou une heure de saisie devenue inacceptable, ou plus d'un serveur.
- **Chiffrement `age` vers une clé publique.** Le VPS peut écrire une
  sauvegarde, il ne peut **pas** la relire. Les archives contiennent des CV, des
  empreintes Argon2id et les secrets TOTP en clair : une compromission du VPS ne
  doit pas donner l'historique.
- **Clé S3 distincte** de celle de l'application, et rétention posée côté
  serveur (cycle de vie + verrou d'objet) pour que le script **ne puisse pas**
  effacer l'historique distant.
- **Chien de garde externe** (healthchecks.io) : si le VPS entier meurt, plus
  rien ici ne peut alerter — c'est l'**absence** du signal qui déclenche.

`RUNBOOK.md` §10 donne le RPO/RTO mesuré et la procédure « le VPS a brûlé ».

---

## 12. CI/CD et environnements

| Étape | Où | Déclencheur |
|---|---|---|
| Tests backend (`pytest`, 31 fichiers) | GitHub Actions | chaque PR + push sur `master` |
| Build frontend (`npm run build`) | GitHub Actions | idem |
| Déploiement frontend | Vercel | push sur `master` (webhook GitHub App) |
| Déploiement backend | **manuel** | `bash ~/app/backend/deploy.sh` |

**`deploy.sh` est le seul chemin de déploiement correct.** Il fait `git pull`,
réinstalle les dépendances, redémarre, puis valide sur **trois sondes** — et
revient automatiquement au commit précédent si l'une échoue :

| Sonde | Ce qu'elle prouve | Pourquoi elle existe |
|---|---|---|
| `/health` | le processus vit | nginx, systemd |
| `/health/db` | il voit ses **données** | un `SUPABASE_URL` erroné laisse `/health` vert |
| `POST /auth/login` (401 attendu) | il voit ses **identifiants** | incident réel : la sortie de GoTrue déployée avant la migration 0019 — les deux premières sondes vertes, **toute connexion en 503**, pendant des heures |

La règle qu'elles résument : **une sonde vaut ce qu'elle charge, pas ce qu'elle
affirme.** Un `git pull && systemctl restart` à la main court-circuite les trois
et le rollback (`systemctl restart` rend la main avant qu'uvicorn ait fini
d'importer l'app — d'où les 502 fantômes).

Environnements : il n'y en a **que deux**, le poste local et la production. Pas
de staging. Les previews Vercel existent pour le front seul et tapent l'API de
production.

---

## 13. Sécurité — les décisions structurantes

- **Surface publique = l'API FastAPI, et elle seule.** PostgREST n'écoute que
  sur `127.0.0.1` — strictement moins exposé que Supabase, dont `/rest/v1` est
  joignable depuis n'importe où.
- **Cloisonnement systemd de PostgREST** (`postgrest.service`) : c'est le
  processus le plus sensible de la machine (il contourne la RLS). `ProtectHome`
  lui interdit de lire le `.env` du backend et les CV ; `SystemCallFilter`,
  `CapabilityBoundingSet=` vide, une seule voie en écriture
  (`/run/postgresql`).
- **En-têtes de réponse** posés sur *chaque* réponse (`main.py`) :
  `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, HSTS en
  prod, `Server` supprimé. CSP `default-src 'none'` sur l'API ;
  `script-src 'none'` sur `/files/` — c'est cette ligne qui ferme le XSS
  same-origin qu'introduit le fait de servir des fichiers déposés par des tiers
  depuis notre propre domaine.
- **CORS à origine dynamique**, regex **ancrée**. Une vérification par sous-chaîne
  (`"julian-talou" in origin`) était contournable en enregistrant
  `x-julian-talou.vercel.app` sur un compte Vercel gratuit.
- **`/docs`, `/redoc`, `/openapi.json` coupés en production.**
- **Gardes fail-closed au démarrage** (`config.py`) : refus de démarrer avec le
  `JWT_SECRET` par défaut, avec un `STORAGE_BACKEND` inconnu, en mode `local`
  sans `PUBLIC_BASE_URL`, ou avec `FILE_URL_SECRET == JWT_SECRET`.
- **Jamais de stack trace au client** : gestionnaire global qui journalise, écrit
  dans le journal d'erreurs admin, et renvoie un message générique.
- **Séparation de domaine des clés** : session, URL de fichier, désabonnement —
  trois clés dérivées de `JWT_SECRET` par HMAC avec des domaines distincts. Un
  jeton d'un usage ne vaut rien dans un autre.

---

## 14. Ce qui reste de Supabase

**Le mot, pas la chose.** Quatre couches, et elles n'en sont pas au même point :

| Couche | État | Comment on le sait |
|---|---|---|
| Supabase **Auth** (GoTrue) | ❌ mort | `routers/auth.py` lit `user_credentials`, aucun repli ; la façade renvoie 501 sur `/auth/v1/` |
| Supabase **API/Postgres** | remplacé par PostgREST + PostgreSQL 18 | le code et l'infra sont en place ; **ce qui est réellement branché dépend de `SUPABASE_URL` dans le `.env` du VPS** (voir ci-dessous) |
| Supabase **Storage** | remplacé par le disque du VPS | idem : dépend de `STORAGE_BACKEND` |
| Le **nom** dans le code | ✅ toujours là | `supabase_client.py`, `SUPABASE_URL`, `supabase.table(...)` dans 42 fichiers |

Garder le nom est **volontaire** : `supabase-py` est resté comme simple client
HTTP PostgREST, ce qui a réduit la bascule à deux lignes de `.env` au lieu d'une
réécriture de 42 fichiers. Le renommer un jour est un travail de confort, pas de
correction.

### Ce qui n'est pas vérifiable depuis le dépôt

Deux lignes du `.env` de production décident de tout, et elles ne sont pas dans
git. La dernière mesure écrite date du 26 août (`BASCULE.md` §0.1) et disait
alors : base et fichiers **encore sur Supabase**, code prêt mais `.env` non
basculé. Depuis, la migration `0021` a été appliquée à la base `uti` du VPS.

**La commande qui tranche :**

```bash
ssh -p 1622 julian.talou@164.132.44.212 \
  'grep -E "^(SUPABASE_URL|STORAGE_BACKEND|PUBLIC_BASE_URL)=" ~/app/backend/.env'
```

Bascule complète attendue :

```
SUPABASE_URL=http://127.0.0.1:8080
STORAGE_BACKEND=local
PUBLIC_BASE_URL=https://vps-cc93f2a8.vps.ovh.net
```

Puis, pour le reste de l'état :

```bash
bash ~/app/backend/scripts/post_bascule_check.sh   # sortie 0 = tout vert
bash ~/app/backend/deploy/supervision.sh
```

### Les 12 critères de suppression du projet Supabase

`BASCULE.md` §6 les liste. Les trois qui bloquent réellement :

1. **Aucune URL `supabase.co` restante en base** — quatre requêtes, quatre
   zéros : `submissions.cv_url`, `profiles.avatar_url`,
   `partner_compliance_docs.file_url`, `email_templates.body`. Une seule non
   nulle et la suppression casse ce lien-là, en silence.
2. **Les fichiers sauvegardés ET restaurables** — une exécution de
   `restore_drill.sh` qui ne signale aucune référence absente de l'archive
   restaurée. Sans ce point, supprimer Supabase supprime la **seule autre
   copie** des fichiers.
3. **Le secret TOTP en clair traité** — `auth_secrets.csv` détruit au `shred`,
   enrôlement MFA refait par chacun.

---

## 15. Dettes et points d'attention connus

| Sujet | État | Pourquoi ça compte |
|---|---|---|
| **Un seul VPS** | assumé | Le RTO en cas de perte totale dépend d'un enregistrement DNS. `api.groupement-it.com` → `164.132.44.212` (TTL 300), **gratuit**, à demander au client : c'est le seul levier qui réduit matériellement le temps de reprise |
| **Copie des archives hors du compte OVH du client** | non fait | Le hors-site vit dans le compte OVH **du client**. Un incident sur ce compte emporte le VPS *et* les sauvegardes |
| **Pas de staging** | assumé | Les previews Vercel tapent l'API de prod |
| **`--workers 1`** | assumé | Plafond de charge. À lever : externaliser rate-limit et scheduler (Redis / worker dédié) **avant** d'ajouter des workers |
| **Migrations appliquées à la main** | assumé | `check_schema_drift.py` détecte l'écart mais ne le corrige pas |
| **Pas d'écran d'annulation de désabonnement** | ouvert | Aujourd'hui c'est un `delete` en base |
| **Anciens `supabase_migration_*.sql` à la racine** | héritage | 28 fichiers doublonnant `backend/migrations/`. Ils ne sont plus la source de vérité |
| **`README.md` / `DEPLOYMENT_OVH.md`** | périmés | Décrivent encore un projet Supabase et Railway |

---

## 16. Repères — où trouver quoi

```
backend/
  main.py                  montage des routeurs, CORS, en-têtes, sondes, boucles de fond
  config.py                TOUTES les variables d'environnement + gardes fail-closed
  routers/                 22 routeurs HTTP
  services/                38 modules métier
    supabase_client.py     ← le client PostgREST (nom hérité)
    storage.py             ← 3 backends de fichiers + signature des URLs
    scheduler.py           ← les deux boucles de fond
    email*.py              ← coquille, modèles, file, désabonnement
    scoring.py             ← scoring déterministe
    matching_runner.py     ← orchestration du matching
    ai_ledger.py           ← coût réel des LLM
  migrations/              schema.sql + 0001…0021
  deploy/                  PostgreSQL, PostgREST, rôles, sauvegardes, supervision
  scripts/                 bootstrap, contrôles, migrations de données
  tests/                   31 fichiers pytest
  deploy.sh                ← LE chemin de déploiement du backend
  nginx.conf               site public (certbot le réécrit)
  uti-backend.service      unité systemd du backend

frontend/
  src/pages/               32 pages
  src/lib/api.js           les deux instances axios
  vercel.json              ← la réécriture /api/* vers le VPS + le CSP

compliance/ai-act/         26 documents de conformité
RUNBOOK.md                 exploitation au quotidien
BASCULE.md                 histoire et critères de sortie de Supabase
graft/                     index de code (non suivi par git)
_bmad/, .claude/skills/    outillage agent
```
