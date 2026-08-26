# RUNBOOK — Exploitation de la plateforme en production

Aide-mémoire pour opérer le backend (VPS OVH) et le frontend (Vercel) une fois
en prod. Objectif : savoir **redémarrer, diagnostiquer et revenir en arrière**
sans réfléchir un jour de panne.

- **Frontend** : Vercel (auto-déploiement sur push `master`). Fichiers statiques.
- **Backend** : FastAPI en `systemd` sur le VPS OVH (`vps-cc93f2a8.vps.ovh.net`),
  derrière nginx (HTTPS Let's Encrypt), mono-worker uvicorn.
- **Base + Storage** : Supabase — **en cours de migration vers le VPS**
  (PostgreSQL 18 + PostgREST pour la base, OVH Object Storage pour les fichiers).
  Tant que la bascule n'a pas eu lieu, tout ce qui suit reste valable tel quel.

Connexion VPS : `ssh -p 1622 julian.talou@164.132.44.212`
*(le port n'est pas le 22 : une procédure de reprise qui échoue à sa première
ligne ne sert à rien, d'où le rappel ici et dans chaque commande de ce fichier.)*

**Caractéristiques du VPS** — 8 vCPU, 22 Go de RAM, 193 Go de disque,
Ubuntu 26.04 LTS, swap de 2 Go. Utile pour dimensionner : le backend consomme
~90 Mo au repos, l'essentiel de la mémoire est donc disponible pour PostgreSQL.

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
ssh -p 1622 julian.talou@164.132.44.212 'bash ~/app/backend/deploy.sh'
```

`deploy.sh` fait : `git pull` + `pip install -r requirements.txt` +
`systemctl restart uti-backend`, puis **vérifie `/health`** ; si le backend ne
répond pas sous 30 s, il **revient automatiquement au commit précédent** et
redémarre (le déploiement échoue alors avec un code ≠ 0 et t'indique les logs
à regarder).

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
| L'appli répond 500 partout | base joignable ? `curl localhost:8000/health/db` ; logs `[ERROR]` |
| Le service refuse de démarrer | `.env` cassé ou `JWT_SECRET` par défaut en prod (fail-closed voulu) → voir logs, corriger `.env`, `reset-failed` |
| Upload de CV en échec | taille > 25 Mo (limite nginx `client_max_body_size`) ; en mode local : `/var/lib/uti/files` existe et appartient à `julian.talou` ? disque plein ? |
| **Un CV / une pièce d'AO ne s'ouvre plus (404)** | Le fichier a disparu du disque, ou `LOCAL_STORAGE_DIR` a changé. `ls /var/lib/uti/files/cvs/<ao_id>/` ; §9.7 |
| **« Ce lien a expiré » (410)** | Normal : une URL de fichier dure 1 h (7 j pour un CV envoyé à un client). Rouvrir la fiche depuis la plateforme régénère le lien. |
| **Un CV répond 403** | Lien tronqué par le client de messagerie, ou `JWT_SECRET` / `FILE_URL_SECRET` changé depuis l'envoi : cela change la clé de signature, donc invalide tous les liens émis avant. |
| Matching IA très lent / timeout | clé OpenRouter/OpenAI valide ? quotas ? le proxy coupe à 120 s |
| Pas d'e-mails envoyés | Chercher `[OUTBOX] EN PAUSE` dans `journalctl -u uti-backend` : la file annonce le motif. Sinon vérifier `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD`, puis `python scripts/test_smtp.py`. Si le test passe mais que rien n'arrive, le problème est la LIVRAISON : voir le tableau de bord du fournisseur (`delivered` / `bounced`), et DKIM/SPF/DMARC du domaine d'envoi |
| RAM du VPS saturée | activer les garde-fous `MemoryHigh`/`MemoryMax` dans `uti-backend.service` |
| CORS bloqué (front) | l'origine Vercel doit correspondre à `FRONTEND_URL` / aux markers dans `main.py` |
| **E-mail « 🚨 UTI — sauvegarde en échec »** | **§9.5** |
| **E-mail « 🚨 UTI — répétition de restauration en échec »** | **§9.6 — c'est la plus grave : le filet n'est plus un filet** |
| **Rien reçu depuis healthchecks.io** | **§9.4 — le VPS est peut-être entièrement mort → §10** |

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

**`FILE_URL_SECRET`** (facultatif) signe les URLs de fichiers. Laissé vide, la
clé est **dérivée** de `JWT_SECRET` par HMAC : la séparation de domaine est
assurée sans secret supplémentaire à gérer. Le renseigner sert à **invalider
d'un coup toutes les URLs de fichiers déjà émises sans déconnecter personne** —
c'est le geste à faire si un lien de CV a fuité. Ne jamais y recopier
`JWT_SECRET` : un jeton d'URL voyage dans les journaux nginx, l'historique du
navigateur et la boîte mail du client ; un jeton de session, non.

Deux fichiers **hors dépôt et hors `.env`**, en `600`, root :

| Fichier | Contenu | Pourquoi séparé |
|---|---|---|
| `/etc/uti-backup.env` | `BACKUP_S3_*`, `HEALTHCHECK_URL` | La clé S3 des **sauvegardes** ne doit surtout pas être celle de l'application (§9.2). Les mélanger dans un même `.env` finit toujours par les confondre. |
| `/etc/uti-supervision.env`, `/etc/uti-restore-drill.env` | `HEALTHCHECK_URL` de chaque sonde | Trois sondes distinctes : voir §9.4. |

---

## 9. Sauvegardes

### 9.1 Ce qui tourne, et à quelle heure

| Quand | Unité systemd | Ce que ça fait |
|---|---|---|
| Toutes les heures | `uti-backup.timer` | `pg_dump` → chiffrement `age` → dépôt hors-site OVH → rotation locale |
| Toutes les heures, **si le contenu a changé** | la même | `tar` de `/var/lib/uti/files` → chiffrement `age` → dépôt hors-site (§9.7) |
| Tous les jours à 03:xx UTC | la même | + copie quotidienne locale (14 j), + `.env` et `/etc/postgrest` chiffrés hors-site, + vérification octet par octet de l'objet distant |
| Le dimanche à 03:xx UTC | la même | + copie hebdomadaire locale (8 semaines) |
| Le lundi à 04:15 UTC | `uti-restore-drill.timer` | **restaure** la dernière archive dans une base jetable et **compare** les lignes à la base vivante |
| Toutes les 15 min | `uti-supervision.timer` | disque, PostgreSQL, PostgREST, `/health`, `/health/db`, âge de la dernière sauvegarde **réussie** |

```bash
systemctl list-timers 'uti-*' --no-pager      # les trois, avec leur prochaine échéance
journalctl -u uti-backup -n 30 --no-pager
cat /var/backups/uti/.dernier_succes          # date + clé S3 + taille de la dernière réussie
cat /var/backups/uti/.dernier_succes_fichiers # idem pour l'archive des FICHIERS
cat /var/backups/uti/.derniere_repetition     # date de la dernière restauration prouvée
```

### 9.2 Pourquoi `pg_dump` horaire, et pas pgBackRest

La base fait 16 Mo, 22 tables, 1 396 lignes dans la plus grosse. Un dump complet
prend deux secondes et pèse ~2 Mo une fois chiffré. **À ce volume, la fréquence
remplace le PITR** : une sauvegarde par heure ramène la perte maximale à une
heure, pour un coût d'exploitation nul.

Ce qu'on **perd** en ne prenant pas pgBackRest, dit franchement :
- la restauration **à la seconde près** (« reviens juste avant le `DELETE` de
  14 h 32 »). Ici on revient au début de l'heure, et on perd au pire une heure
  de saisie ;
- les sauvegardes **incrémentales** — sans objet à 16 Mo ;
- la **compression différentielle** et le parallélisme — sans objet.

Ce qu'on **évite** en ne le prenant pas, et qui a fait pencher la balance :
`archive_command`. Si l'archivage des WAL échoue (dépôt injoignable, clé
expirée), PostgreSQL cesse de recycler `pg_wal`, le disque se remplit et **la
production s'arrête**. On échangerait « perdre au pire une heure » contre « la
base tombe parce que la sauvegarde a un problème ». Pour un développeur seul qui
devra encore exploiter ça dans six mois, ce n'est pas un bon échange.

**Ce que ça dit de Supabase.** Le PITR de Supabase est facturé ~100 $/mois, soit
~1 200 $/an. Il achète un RPO de quelques secondes. Ici, le RPO est d'une heure,
pour ~0,03 €/mois de stockage objet. La question honnête n'est pas « lequel est
meilleur » mais « une heure de saisie perdue, une fois tous les cinq ans,
coûte-t-elle 1 200 $/an ? » — pour cette plateforme, non. C'est l'argument qui
justifie d'être parti, et il est chiffré.

**Le seuil de bascule**, écrit ici pour qu'il ne se décide pas à l'instinct.
Passer à pgBackRest le jour où **l'une** de ces lignes est franchie :
1. la base dépasse ~2 Go ;
2. une heure de saisie perdue devient inacceptable pour le métier ;
3. il y a plus d'un serveur à sauvegarder.

### 9.3 Chiffrement — et où vit la clé

Les archives contiennent des **CV** (nom, téléphone, parcours), des adresses
e-mail, des empreintes argon2id, et les **secrets TOTP en clair** de
`profiles.mfa_secret`. Elles partent chez un tiers. Elles sont donc chiffrées
avec **`age`, en mode asymétrique**.

Pourquoi `age` et pas autre chose :

| Mécanisme | Pourquoi non |
|---|---|
| `openssl enc` | Symétrique : la phrase de passe devrait vivre sur le VPS, donc le VPS saurait déchiffrer, donc une compromission du VPS lit **tout l'historique**. |
| Chiffrement côté serveur S3 (SSE) | C'est OVH qui détient la clé. Protège d'un disque volé chez OVH, pas d'un compte OVH compromis. Utile **en complément**, jamais seul. |
| `gpg` | Fait le travail, mais son trousseau, son agent et son pinentry sont trois choses à comprendre à 3 h du matin, six mois plus tard. |
| **`age`** | Chiffre vers une clé **publique**. `AGE_RECIPIENT` n'est pas un secret et reste en clair dans l'unité systemd. **Le VPS est mathématiquement incapable de relire ce qu'il vient d'écrire.** C'est exactement la propriété qu'on veut face à un rançongiciel. |

**Où vit la clé privée** — la question qui décide si tout le reste sert.
Une clé dont la seule copie est sur le VPS rend les archives inutiles le jour où
le VPS disparaît, c'est-à-dire **le seul jour où elles servent**. Trois copies,
trois lieux, dont aucun n'est le VPS :

1. **Imprimée sur papier** (une ligne, ~74 caractères), enveloppe scellée, à une
   autre adresse que le bureau ;
2. dans le **gestionnaire de mots de passe** (note sécurisée, compte en 2FA) ;
3. sur une **clé USB chiffrée** (LUKS/VeraCrypt), rangée ailleurs.

Et **jamais** : dans le dépôt git (même privé) ; dans `backend/.env` ni
`/etc/uti-backup.env` ; **dans le conteneur qui contient les archives qu'elle
ouvre** — une clé rangée à côté de son coffre n'est pas une clé.

### 9.4 Le hors-site, et le piège qu'il faut avoir désamorcé

Les archives partent sur un conteneur OVH Object Storage **distinct** de celui
des CV, dans une **région distincte de celle du VPS** (VPS en `GRA`, sauvegardes
en `SBG`) — sinon un incident de datacentre emporte les deux.

Le piège, nommé : si la clé S3 du `.env` du VPS pouvait écrire **et supprimer**
sur ce conteneur, une compromission du VPS détruirait tout, et on aurait déplacé
les sauvegardes de 20 cm. Trois couches indépendantes :

1. **Conteneur distinct** — rien de partagé avec les fichiers applicatifs.
2. **Utilisateur S3 distinct, sans droit de suppression**
   (`deploy/backup_s3_policy.json`). La clé du VPS peut `PutObject` et
   `GetObject`, pas `DeleteObject`.
   ⚠️ **OVH ne supporte pas le refus implicite pour le propriétaire d'un
   conteneur** (« implicit deny is not supported […] if the user is the bucket
   owner ») : le propriétaire garde l'ACL `FULL_CONTROL`. La politique doit donc
   être attachée à un **second** utilisateur, jamais à celui qui a créé le
   conteneur. Sans cela, la restriction est décorative.
3. **Verrou d'objet (Object Lock, WORM) en mode `COMPLIANCE`, 30 jours** — posé
   à la création du conteneur, il tient même si quelqu'un obtient la clé du
   propriétaire (« objects cannot be modified or deleted by any user, including
   administrators »). ⚠️ **Irréversible et non rétroactif** : un conteneur créé
   sans `--object-lock-enabled-for-bucket` ne peut jamais le recevoir. C'est la
   seule décision de ce dispositif qu'on ne peut pas corriger après coup.

Mise en place : `bash backend/deploy/setup_backup_offsite.sh` (guidé, idempotent).
**Le contrôle qui compte** : son étape 4 essaie réellement de supprimer un objet
avec la clé du VPS. Tant qu'il n'a pas affiché « suppression REFUSÉE », le
hors-site n'est pas en place — il est seulement ailleurs.

Sources :
[Object Lock (WORM)](https://docs.ovhcloud.com/en/guides/storage-and-backup/object-storage/s3-managing-object-lock) ·
[Identity and access management](https://docs.ovhcloud.com/en/guides/storage-and-backup/object-storage/s3-identity-and-access-management/)

### 9.5 L'alerte — y compris quand le VPS entier est mort

Deux canaux, parce qu'aucun des deux ne couvre l'autre :

- **De l'intérieur** (`deploy/lib_alerte.sh`) : e-mail SMTP **direct**, via
  `services/email.py:send_email`, jamais via la file `email_outbox` — celle-ci
  vit **en base**, or c'est la base qu'on annonce en panne. Un canal de secours
  qui dépend de ce qu'il surveille ne prévient jamais.
- **De l'extérieur** — le *dead man's switch*. Si le VPS entier est mort, plus
  rien ici ne peut émettre : par construction, aucune alerte locale ne partira.
  Un service tiers qui **attend** un signal régulier détecte ce silence. C'est
  la seule façon d'être prévenu d'une panne totale sans posséder une seconde
  machine. [healthchecks.io](https://healthchecks.io/docs/) suffit — offre
  gratuite (20 sondes), et il ne reçoit qu'un UUID : **aucune donnée de la
  plateforme ne transite par lui**. (Auto-hébergeable, mais l'héberger sur le
  même VPS annulerait tout l'intérêt.)

Trois sondes **distinctes**, une par unité, parce que « la sauvegarde tourne »,
« la sauvegarde est restaurable » et « la machine va bien » sont trois
affirmations différentes :

| Sonde | Pingée par | Période / grâce conseillées |
|---|---|---|
| `uti-sauvegarde` | `backup_db.sh` (fin, si succès) | 1 h / 30 min |
| `uti-repetition` | `restore_drill.sh` (fin, si succès) | 7 j / 1 j |
| `uti-supervision` | `supervision.sh` **seulement si tout est vert** | 15 min / 30 min |

La troisième est le filet de dernier recours : son silence couvre d'un coup
« le VPS est mort », « la supervision est morte » et « une anomalie dure ».

**Test à faire une fois, sinon on ne saura jamais si ça marche :**
```bash
sudo systemctl stop uti-supervision.timer      # puis attendre la fin de la grâce
# → un e-mail de healthchecks.io doit arriver. Le relancer ensuite :
sudo systemctl start uti-supervision.timer
```

### 9.6 La répétition de restauration — le cœur du dispositif

`pg_restore --list` prouve qu'une archive est **lisible**. Ce n'est pas la même
chose que **restaurable** : une archive lisible peut rejouer avec des erreurs,
perdre une table, ou contenir un schéma sans ses données. **Tant que personne
n'a restauré, on possède un fichier et une croyance.**

Chaque lundi, `restore_drill.sh` : crée une base jetable (nom imposé contenant
`_drill_`), y rejoue la dernière archive avec `--exit-on-error`, **compte les
lignes de chaque table des deux côtés**, vérifie que `user_credentials` contient
bien des empreintes argon2id exploitables et que les clés étrangères sont
revenues, puis supprime la base jetable (via un `trap`, même en cas d'échec).
Il **ne touche jamais la base vivante** : trois garde-fous indépendants.

```bash
bash ~/app/backend/deploy/restore_drill.sh      # à la demande
journalctl -u uti-restore-drill -n 50 --no-pager
```

Un e-mail « répétition de restauration en échec » est **la plus grave des
alertes du dispositif** : il dit que le filet n'en est plus un.

**Répétition hors-site, trimestrielle et manuelle.** La répétition automatique
rejoue l'archive **locale en clair** : c'est ce qui lui permet de tourner sans
aucun secret. Elle ne prouve donc pas que la copie *chiffrée et distante* est
exploitable. Une fois par trimestre, depuis un poste qui détient la clé privée :

```bash
AGE_IDENTITY=/media/cle-usb/uti-backup.age-key \
  bash ~/app/backend/deploy/restore_drill.sh --hors-site
```

Elle télécharge le dernier objet OVH, le déchiffre, le restaure et le compare.
C'est le seul contrôle qui prouve que **la clé papier ouvre bien les archives**.
Ne jamais laisser `AGE_IDENTITY` traîner sur le VPS après coup.

La répétition restaure **aussi l'archive des fichiers** et vérifie que **chaque
référence de la base restaurée désigne un fichier réellement présent**
(`submissions.cv_url`, `profiles.avatar_url`,
`partner_compliance_docs.file_url`, `appels_offres.source_files`). C'est le seul
contrôle qui distingue « la base revient » de « la plateforme revient ».

### 9.7 Les fichiers, depuis qu'ils vivent sur ce disque

Les CV, les pièces jointes d'appel d'offres, les attestations de vigilance
URSSAF et les KBIS ne sont plus chez un hébergeur qui les réplique : ils sont
dans **`/var/lib/uti/files`**, sur le même disque que la base. **C'est la
contrepartie de la décision de rapatrier le stockage, et elle n'est pas
optionnelle** : sans le dispositif ci-dessous, un `rm -rf` ou un rançongiciel
emporte la base *et* les fichiers, et seule la base revient.

| Question | Réponse |
|---|---|
| Où ? | `/var/lib/uti/files/{cvs,avatars,ao-sources,compliance,email-assets}` |
| Droits | Répertoires `0700`, fichiers `0600`, propriétaire `julian.talou` |
| Qui écrit ? | Le backend (`uti-backend.service`, `User=julian.talou`) |
| Qui lit ? | **Lui seul.** `nginx` (`www-data`) n'ouvre jamais ces fichiers : il relaie, le backend sert (§9.7 « pourquoi pas X-Accel-Redirect »). |
| Sauvegarde | `tar` → `age` → `uti/fichiers/AAAA/MM/uti-fichiers-*.tar.age` chez OVH, **uniquement quand le contenu a changé** |
| Restauration | Vérifiée chaque lundi par `restore_drill.sh` (§9.6) |

**Pourquoi « quand le contenu a changé » et pas à chaque heure.** 38 objets,
~50 Mo. Redéposer 50 Mo par heure, c'est 1,2 Go par jour dans un conteneur à
**verrou d'objet**, où rien ne s'efface : la facture monte sans fin pour un
contenu identique 23 heures sur 24. Un dépôt quotidien, à l'inverse, donnerait
aux fichiers un RPO de 24 h contre 1 h à la base — un CV déposé le matin et
perdu le soir serait référencé par une ligne restaurée pointant sur un fichier
absent. Le script compare donc chaque heure une empreinte (chemin + taille +
date de chaque fichier) et n'envoie que si elle a bougé.

**Pourquoi le backend sert les fichiers, et pas nginx.** `X-Accel-Redirect`
obligerait `www-data` à pouvoir **lire** le répertoire — un second lecteur pour
des CV et des attestations URSSAF — et scinderait la décision « qui a le droit
de lire » entre deux systèmes. C'est exactement le montage qui avait laissé les
CV en `public-read`. Le gain, à 50 Mo, est nul. *Seuil de bascule, écrit pour ne
pas être décidé à l'instinct :* le jour où un objet unique dépasse ~100 Mo, ou
si les téléchargements concurrents saturent l'unique worker uvicorn.

**Comment un fichier privé est servi.** Le backend émet une URL
`https://…/files/d/<jeton>` où le **bucket et le chemin sont à l'intérieur du
jeton signé** — il n'existe aucun chemin hors de la signature. Durée : 1 h, ou
7 j pour un CV envoyé à un client (plafond absolu). Les avatars et les images de
modèles d'e-mail, eux, ont une URL stable `…/files/public/<bucket>/<chemin>` :
une balise `<img>` et un client de messagerie ne savent pas renouveler un lien
expiré. La liste blanche `PUBLIC_BUCKETS` (`services/storage.py`) est la seule
autorité — tout le reste tombe en 404 sur cette entrée.

```bash
# Inventaire et volume
find /var/lib/uti/files -type f | wc -l && du -sh /var/lib/uti/files
# Droits (doit être drwx------ julian.talou)
ls -ld /var/lib/uti/files /var/lib/uti/files/cvs
# Ce que le backend voit (staff authentifié)
curl -s -H "Authorization: Bearer <jeton>" localhost:8000/files/_diagnostic
```

---

## 10. Reprise après sinistre — « le VPS a brûlé »

### 10.1 RPO et RTO, honnêtement

| | Valeur | D'où elle vient |
|---|---|---|
| **RPO** (données perdues au pire) | **≤ 1 h 05** | Sauvegarde horaire + `RandomizedDelaySec=300`. Concrètement : les CV déposés et les décisions saisies dans l'heure écoulée. |
| **RTO** (retour en service) | **3 h à 4 h**, estimé | Détail au §10.3. |
| **RPO des fichiers** (CV, pièces jointes, URSSAF, KBIS) | **≤ 1 h 05** | Même fenêtre que la base : le dépôt hors-site est déclenché à l'heure, dès que le contenu du répertoire a changé (§9.7). |
| **RTO des fichiers** | **+10 min** sur le RTO ci-dessus | Étape 7 bis du §10.3 : télécharger, déchiffrer, extraire, reposer les droits. |

> ⚠️ **Cette ligne a changé de sens.** Tant que `STORAGE_BACKEND=s3` était visé,
> le RTO des fichiers était **0** : ils vivaient chez OVH et un VPS mort ne les
> touchait pas. Depuis qu'ils sont sur le disque du VPS, **ils meurent avec
> lui** — et ne reviennent que par la sauvegarde. C'est le prix, entièrement
> assumé, de ne plus dépendre d'un compte OVH qu'on ne possède pas. Il se paie
> une fois, à l'étape 7 bis, à condition de ne pas l'oublier.

> ⚠️ **Le RTO ci-dessus est une estimation, pas une mesure.** Il le restera
> jusqu'à la première reprise réelle. Chronométrer la première vraie exécution
> et **remplacer ce chiffre par le chiffre mesuré** : un RTO estimé qu'on n'a
> jamais vérifié est du même ordre qu'une sauvegarde qu'on n'a jamais restaurée.

**Ce que le RPO d'une heure ne couvre pas, et qu'il faut savoir :** une suppression
accidentelle repérée dans les minutes qui suivent se rattrape (l'archive de
l'heure précédente est encore là) ; une corruption logique introduite il y a
trois semaines et découverte aujourd'hui se rattrape aussi (hebdomadaires sur
8 semaines) ; mais **revenir à 14 h 32 précises est impossible** — c'est le
choix assumé du §9.2.

### 10.2 Avant de commencer : ce qu'il faut avoir sous la main

1. La **clé privée `age`** (papier, gestionnaire de mots de passe, ou clé USB) ;
2. les identifiants **OVH** (espace client) ;
3. les identifiants **S3 du conteneur de sauvegarde** (dans le gestionnaire de
   mots de passe — pas seulement dans `/etc/uti-backup.env`, qui a brûlé avec le
   VPS ; **si ce point n'est pas vrai aujourd'hui, le corriger maintenant, pas
   le jour du sinistre**) ;
4. l'accès au dépôt git et au **dashboard Vercel**.

### 10.3 La procédure, dans l'ordre

| # | Étape | Durée | Commande / geste |
|---|---|---|---|
| 1 | Commander un VPS neuf chez OVH, même gabarit | 15 min | Espace client. Noter la nouvelle IP. |
| 2 | Accès : clé SSH, port 1622, `ufw` | 10 min | `ssh-copy-id`, `sudo ufw allow 1622/tcp` puis `ufw enable` **après** avoir vérifié la session |
| 3 | Cloner le dépôt | 5 min | `git clone … ~/app && cd ~/app/backend && python3 -m venv venv && venv/bin/pip install -r requirements.txt` |
| 4 | Installer la pile base | 25 min | `sudo bash ~/app/backend/deploy/install_db.sh` |
| 5 | **Récupérer la configuration** | 10 min | Voir l'encadré ci-dessous — c'est l'étape qui décide du RTO |
| 6 | **Récupérer et déchiffrer la dernière archive** | 10 min | Voir l'encadré ci-dessous |
| 7 | Restaurer la base | 5 min | `pg_restore --exit-on-error --no-owner --no-privileges -d uti archive.pgcustom` |
| 7 bis | **Restaurer les FICHIERS** | 10 min | Voir l'encadré ci-dessous. **Sauter cette étape donne une base parfaite dont tous les liens de CV sont morts.** |
| 8 | Rôles et privilèges (objets de **cluster**, absents du dump) | 5 min | `cd ~/app/backend/deploy && sudo -u postgres psql < roles_postgrest.sql` |
| 9 | Démarrer les services | 10 min | `sudo systemctl enable --now postgrest nginx uti-backend` |
| 10 | HTTPS sur la nouvelle machine | 15 min | `sudo certbot --nginx` (⚠️ si l'IP change, **attendre la propagation DNS** : jusqu'au TTL de la zone) |
| 11 | Rebrancher le frontend | 10 min | `vercel.json` → nouvelle destination `/api/*` → push `master` → build Vercel |
| 12 | **Vérifier** | 10 min | `bash ~/app/backend/scripts/post_bascule_check.sh` puis `bash ~/app/backend/deploy/supervision.sh` |
| 13 | Remettre le dispositif de sauvegarde | 20 min | §9.1 : réinstaller les trois timers, recréer **l'utilisateur S3 déposant** (l'ancienne clé est compromise si le VPS a été piraté plutôt que détruit), relancer `restore_drill.sh` |

**Total : ~2 h 30 de travail effectif, 3 h à 4 h en réel** en comptant les
attentes (provisioning, `apt`, DNS) et le fait qu'on fait ça en état de stress.

> **Étape 5 — la configuration.** C'est elle, et non la base, qui domine le
> temps de reprise : `.env` porte une vingtaine de secrets et `/etc/postgrest`
> porte le secret JWT que PostgREST doit **partager** avec le backend. Elle est
> sauvegardée chiffrée une fois par jour :
> ```bash
> export BACKUP_S3_ENDPOINT=… BACKUP_S3_REGION=sbg BACKUP_S3_BUCKET=uti-sauvegardes
> export BACKUP_S3_ACCESS_KEY=… BACKUP_S3_SECRET_KEY=…
> venv/bin/python deploy/s3_backup.py lister uti/conf/ | tail -1
> venv/bin/python deploy/s3_backup.py recuperer uti/conf/conf-AAAA-MM-JJ.tar.age /tmp/conf.age
> age -d -i /media/cle-usb/uti-backup.age-key -o /tmp/conf.tar /tmp/conf.age
> tar -xf /tmp/conf.tar -C /tmp/conf/ && shred -u /tmp/conf.tar /tmp/conf.age
> ```
> Sans elle : compter **une demi-journée de plus** à retrouver vingt secrets un
> par un. Avec elle : dix minutes.

> **Étape 6 — l'archive.**
> ```bash
> venv/bin/python deploy/s3_backup.py lister uti/ | tail -1        # la plus récente
> venv/bin/python deploy/s3_backup.py recuperer <cle> /tmp/uti.age
> age -d -i /media/cle-usb/uti-backup.age-key -o /tmp/uti.pgcustom /tmp/uti.age
> pg_restore --list /tmp/uti.pgcustom | head        # lisible avant d'aller plus loin
> ```
> Le nom des objets est horodaté **UTC ISO-8601** : le dernier au sens
> alphabétique est le plus récent au sens chronologique.

> **Étape 7 bis — les fichiers.** La base restaurée référence des CV, des pièces
> jointes d'AO et des attestations URSSAF qui ne sont **nulle part** tant que
> cette étape n'est pas faite. L'application démarrera, les écrans s'afficheront,
> et c'est en cliquant sur un CV qu'on découvrira le problème.
> ```bash
> venv/bin/python deploy/s3_backup.py lister uti/fichiers/ | tail -1
> venv/bin/python deploy/s3_backup.py recuperer <cle> /tmp/fichiers.age
> age -d -i /media/cle-usb/uti-backup.age-key -o /tmp/fichiers.tar /tmp/fichiers.age
>
> sudo install -d -m 750 -o julian.talou -g julian.talou /var/lib/uti
> sudo install -d -m 700 -o julian.talou -g julian.talou /var/lib/uti/files
> sudo tar -xf /tmp/fichiers.tar -C /var/lib/uti/files
> # tar restaure les modes de l'archive, mais PAS le propriétaire quand on
> # n'extrait pas en root, et l'inverse quand on l'est. On tranche explicitement :
> sudo chown -R julian.talou:julian.talou /var/lib/uti/files
> sudo find /var/lib/uti/files -type d -exec chmod 700 {} +
> sudo find /var/lib/uti/files -type f -exec chmod 600 {} +
> shred -u /tmp/fichiers.tar /tmp/fichiers.age
>
> # Contrôle : autant de fichiers que la base en référence
> psql -d uti -tAc "SELECT count(*) FROM submissions WHERE cv_url IS NOT NULL;"
> find /var/lib/uti/files/cvs -type f | wc -l
> ```
> Le contrôle complet (chaque référence ↔ chaque fichier, les quatre familles)
> est celui que fait `restore_drill.sh` : le relancer une fois la machine debout.

### 10.4 Les trois façons dont cette procédure échoue

Nommées ici parce qu'on ne les découvre pas le jour même :

1. **La clé privée `age` est introuvable.** Les archives deviennent des octets
   sans valeur, définitivement. Aucun recours. C'est pourquoi le §9.3 impose
   trois copies, et pourquoi la répétition hors-site trimestrielle (§9.6) existe :
   elle est le seul contrôle qui prouve que la copie qu'on garde ouvre vraiment
   les archives.
2. **Les identifiants S3 n'existaient que dans `/etc/uti-backup.env`**, sur le
   VPS qui a brûlé. Les archives sont intactes et inaccessibles. Recours :
   l'espace client OVH permet de régénérer un utilisateur — à condition d'avoir
   gardé l'accès à l'espace client, donc à un e-mail qui n'est pas hébergé ici.
3. **Le DNS.** Si l'IP change, rien ne remarche tant que la zone n'a pas
   propagé, quel que soit l'état du serveur. Baisser le TTL de l'enregistrement
   à 300 s **maintenant**, pendant que tout va bien, coûte cinq minutes et
   économise potentiellement des heures.

---

## 11. Supervision

`uti-supervision.timer` (toutes les 15 min) surveille les quatre pannes qui ne
se voient pas :

| Contrôle | Pourquoi celui-là | Seuil |
|---|---|---|
| Espace disque `/` et `/var/backups/uti` | Une base qui remplit le disque ne ralentit pas : elle met PostgreSQL en lecture seule, empêche `journald` d'écrire et empêche le script de sauvegarde de **prévenir**. | 85 % |
| PostgreSQL vivant | Tout le reste en dépend. | `pg_isready` |
| PostgREST vivant | PostgreSQL debout + PostgREST mort = panne totale que `systemctl status postgresql` déclare verte. Le contrôle attend **401** sans jeton : un **200** signifierait que la base est lisible sans authentification, ce qui est **pire** qu'une panne. | HTTP 401 |
| `/health` et `/health/db` | `/health` ne touche pas la base (`main.py:170`) : seul `/health/db` (`main.py:193`) distingue « le backend tourne » de « le backend voit ses données ». | 200 |
| Âge de la dernière sauvegarde **réussie** | Lit `.dernier_succes`, écrit à la toute fin de `backup_db.sh` — et non la date du dernier `.pgcustom`, qu'une exécution interrompue laisse toute fraîche. | 3 h |
| Âge de la dernière **répétition** réussie | Une sauvegarde qu'on n'a pas rejouée depuis dix jours est redevenue une croyance. | 10 j |
| Bases `uti_drill_%` orphelines | Un `kill -9` pendant une répétition contourne son `trap` ; trente bases orphelines ramènent au premier point. | 2 |

```bash
bash ~/app/backend/deploy/supervision.sh    # à la demande, sortie lisible
systemctl list-units --failed               # tableau de bord : l'unité reste en échec
ls /var/lib/uti-supervision/                # un fichier = une anomalie en cours
```

L'anti-répétition (`RAPPEL_MIN`, 6 h par défaut) fait qu'une anomalie qui dure
produit **un** e-mail toutes les 6 h et non 96 par jour : au bout de deux jours,
96 e-mails/jour sont filtrés en « Autres » et l'alerte suivante — la vraie — ne
sera pas lue. Le retour à la normale est annoncé explicitement, sinon on ne sait
jamais si le silence veut dire « réparé » ou « la supervision est morte aussi ».
