# BASCULE — Supabase → VPS OVH

> Document opérationnel. Il se lit de haut en bas le jour J, et se coche
> jusqu'à la suppression du projet Supabase.
>
> Connexion VPS : `ssh -p 1622 julian.talou@164.132.44.212` (le port n'est pas le 22).

---

## 1. Calendrier et dépendances

**La date de bascule n'est pas un choix, c'est une soustraction.** Julian pose
comme critère 14 jours de fonctionnement sans intervention avant de supprimer,
et vise fin août. 31 août − 14 jours = **lundi 17 août, dernière date possible
pour basculer**. Tout chantier non terminé le 16 août ne décale pas la bascule :
il décale la suppression, donc la facture Supabase et la charge mentale.

| # | Chantier | Fenêtre | Dépend de | Bloque |
|---|---|---|---|---|
| A | **Stockage → disque du VPS** (`STORAGE_BACKEND=local`) | 6 → 8 août | rien | E |
| B | Installation PostgreSQL 18 + PostgREST (`deploy/install_db.sh`) | 7 → 8 août | rien | C, E |
| C | Schéma + `seed.sql` + `verify_postgrest.py` sur la base neuve | 10 → 12 août | B | D, G |
| D | Authentification maison (remplace GoTrue) | 10 → 14 août | C | G |
| E | **Sauvegarde + restauration éprouvée + supervision** | 12 → 14 août | B | G |
| F | Documentation + conformité (art. 28/30) | 12 → 16 août | rien | I |
| G | **BASCULE** | lundi 17 août, 9 h | C, D, E | H |
| H | Observation, 14 jours | 17 → 30 août | G | I |
| I | **Suppression du projet Supabase** | lundi 31 août | H, F | — |

**En parallèle sans risque :** A avec B ; D avec C dès que la table `profiles`
existe ; E dès que B est fini ; F du début à la fin (c'est de la rédaction).

**Chaîne bloquante, dans cet ordre strict :** B → C → D → G. Aucune de ces
quatre étapes ne peut chevaucher la suivante : la façade nginx renvoie
délibérément `501` sur `/auth/v1/` (`deploy/nginx-postgrest.conf:91-93`) pour
que basculer avant que D soit déployé casse la connexion **bruyamment** plutôt
que subtilement.

**E bloque G, et ce n'est pas négociable.** Basculer la production sur une base
sans sauvegarde éprouvée, c'est remplacer un hébergeur qui sauvegardait pour
nous par une machine où personne ne le fait. Le filet Supabase est encore là ce
jour-là, mais il disparaîtra dans deux semaines.

### Pourquoi les fichiers vont sur le disque du VPS, et pas dans un conteneur objet

**Décision prise, non rediscutée ici.** Obtenir un conteneur de stockage objet
exige un accès OVH qui appartient à un associé. Le volume est de **38 objets
pour ~50 Mo**, le VPS a **185 Go libres**. Poser les fichiers sur son disque
supprime une dépendance externe au lieu d'en ajouter une, et ne laisse qu'une
seule destination hors-site à obtenir : celle des **sauvegardes**.

**Ce que cette décision coûte, et qui doit être payé avant la bascule.** Tant
que les fichiers vivaient chez un hébergeur, quelqu'un d'autre les répliquait.
À partir de maintenant, personne — sauf `deploy/backup_db.sh`, qui embarque
désormais `/var/lib/uti/files` dans la même archive chiffrée que la base, et
`deploy/restore_drill.sh`, qui vérifie chaque lundi que **chaque référence de la
base restaurée désigne un fichier réellement présent**. C'est pour cette raison
que le chantier A **bloque désormais E** : sauvegarder une base dont les
fichiers ne sont pas sauvegardés, c'est valider la moitié des données en croyant
les valider toutes. Voir RUNBOOK.md §9.7.

Une conséquence à connaître avant le jour J : **le RTO des fichiers n'est plus
zéro.** Ils ne sont plus « ailleurs » ; ils meurent avec le VPS et ne reviennent
que par l'étape 7 bis du RUNBOOK §10.3. Cette ligne a été corrigée au §10.1.

### Pourquoi le stockage passe en premier

1. **Il doit précéder la bascule pour une raison mécanique, pas de confort.**
   `scripts/migrate_storage_to_ovh.py` (`--rewrite-db`) réécrit
   `submissions.cv_url`, `profiles.avatar_url`,
   `partner_compliance_docs.file_url` et les images des modèles d'e-mail **dans
   la base courante**. Tant que la base courante est Supabase, la réécriture
   pointe les lignes de production vers le VPS et les 32 CV restent
   téléchargeables. Faite après la bascule, elle s'appliquerait à une base
   vide : les CV existants deviendraient introuvables dès le basculement de
   `STORAGE_BACKEND`, c'est-à-dire aujourd'hui, pas le 17.
2. **Il est réversible en 30 secondes** : une variable (`STORAGE_BACKEND`), et
   le script ne supprime rien côté Supabase (`migrate_storage_to_ovh.py:14-15`).
   Le retour arrière fonctionne parce que `storage._object_path()` relit
   indifféremment une URL Supabase héritée ou un chemin nu.
3. **Il retire une variable du jour J.** Le 17 août, une seule chose doit
   changer. Un incident ce matin-là doit avoir une cause unique.
4. **Il fait apparaître tôt les surprises du service des fichiers** — droits
   UNIX, expiration des liens, affichage d'un PDF dans le navigateur — pendant
   qu'on peut encore revenir en arrière sans conséquence.

---

## 2. À exporter de Supabase avant de supprimer

« Les données sont fausses » est vrai des données métier. C'est faux de tout ce
qui suit, qui n'existe qu'à cet endroit et qu'aucun code ne sait reconstituer.

| Quoi | Pourquoi ça ne se retrouve pas ailleurs |
|---|---|
| `scoring_config` | `services/scoring_settings.py:34` renvoie `{}` si la table est vide, et le moteur retombe alors sur ses propres `DEFAULTS`, **qui ne sont pas la grille réglée à la main**. Perdre cette ligne, c'est changer silencieusement tous les scores. |
| `app_settings` | La ligne `notifications` porte des réglages arbitrés (`list2_delay_days`, `relance_max`). Le code a des défauts, ils ne sont pas forcément ceux de la production. |
| `email_templates` | `services/email_templates.py:258-264` retombe sur les modèles du dépôt. Toute personnalisation faite depuis l'écran admin disparaît sans trace. |
| `auth.users` (id ↔ e-mail ↔ rôle) | `audit_log.actor_id`, `human_decision.decided_by`, `submissions.submitted_by` stockent des UUID d'`auth.users`. **Sans la table de correspondance, l'archive de conformité devient une suite d'UUID qui ne désignent plus personne** — inexploitable en cas de contrôle ou de contestation. |
| `audit_log` (1396 lignes) | AI Act art. 12, journalisation (`services/audit.py:1-8`). Supprimer le projet supprime la seule copie. |
| `human_decision` | AI Act art. 14, trace de la supervision humaine. Même raisonnement. |
| `ai_usage` | Historique de dépense IA : la seule base de comparaison pour calibrer les plafonds `ai_budget`. |
| `email_outbox`, `partner_email_log` | Preuve d'envoi. Un partenaire qui affirme n'avoir jamais été notifié se réfute avec ça. |
| `partner_compliance_docs` **et le bucket `compliance`** | Attestations URSSAF, KBIS : des pièces contractuelles déposées par des tiers. Les lignes ne servent à rien sans les fichiers. |
| Objets stockés | `cvs` 32, `ao-sources` 5, `avatars` 1, plus `compliance` et `email-assets` (créés à la demande, jamais inventoriés). Aucun `pg_dump` ne les emporte. |
| Collation, version, extensions | `deploy/INSTALLATION.md:49-52` en a besoin pour que `ORDER BY name` classe les accents pareil. Ça ne se relève plus après suppression. |

### Commandes

```bash
# L'URI ne passe JAMAIS en argument : `ps` est lisible par tous.
# Console Supabase → Project Settings → Database → Connection string → URI (mode Session).
install -m 600 /dev/null ~/.supabase_db_uri
nano ~/.supabase_db_uri

# pg_dump doit être ≥ 17 (Supabase tourne en 17.6) — d'où PostgreSQL 18 sur le VPS.
export PATH=/usr/lib/postgresql/18/bin:$PATH

bash ~/app/backend/scripts/export_supabase_archive.sh ~/archive-supabase --with-secrets
```

Le script produit `dump.pgcustom`, `dump.sql`, un CSV par table (22),
`auth_users.csv`, `config_replay.sql` (configuration prête à rejouer),
`storage/` (les cinq buckets), `MANIFEST.txt` et `SHA256SUMS`.

`--with-secrets` ajoute un fichier séparé en 0600 contenant les empreintes
bcrypt et les secrets TOTP (`profiles.mfa_secret` est stocké **en clair**).
**À détruire (`shred -u`) dès que les 11 comptes sont recréés.** La
recommandation est de ne PAS réutiliser ces empreintes : recréer les comptes
avec les **mêmes UUID** (pour que l'archive d'audit reste lisible) mais avec un
mot de passe défini par chacun via le lien de réinitialisation. On teste ainsi
la chaîne de reset pour de vrai, sur 11 personnes, avant de supprimer le filet.

---

## 3. Lignes de configuration à créer sur la base neuve

Une ligne **absente** et une ligne **présente avec la valeur par défaut** ne se
comportent pas pareil dans cette application. C'est exactement l'incident
`data_retention` : aucune ligne en base, la purge retombait sur
`enabled: false`, et l'écran d'administration affichait un réglage d'apparence
normale. `backend/migrations/seed.sql` couvre déjà les quatre premières.

| Clé | Valeur | Lu par | Ce que l'absence produit |
|---|---|---|---|
| `app_settings/notifications` | `{"enabled":true,"list2_delay_days":2,"relance_auto_enabled":false,"relance_interval_days":7,"relance_max":2}` | `app_settings.py:53` | Défauts du code appliqués sans le dire |
| `app_settings/data_retention` | `{"enabled":false,"months":24}` | `app_settings.py:117` | **L'incident d'origine** : purge inerte, invisible |
| `app_settings/ai_budget` | `{"enabled":true,"weekly_usd":20.0,"monthly_usd":60.0}` | `app_settings.py:130` | Voir ci-dessous |
| `scoring_config` (1 ligne) | grille réglée à la main | `scoring_settings.py:24` | Le moteur bascule sur d'autres poids, sans erreur |
| `email_templates` | vide en production | `email_templates.py:258` | Rien : les modèles du dépôt s'appliquent (documenté pour qu'une table vide ne passe pas pour un oubli) |
| `app_settings/ai_budget_alerts` | **délibérément absent** | `ai_budget.py:151` | Rien : c'est un état d'anti-spam, pas un réglage. Absent = « aucune alerte encore envoyée », ce qui est vrai sur une base neuve |

**`ai_budget` à 0 rejoue l'incident sous une autre forme.** `ai_budget.py:144`
sort immédiatement quand les deux plafonds valent 0 : la ligne existe, l'écran
l'affiche, et la surveillance ne se déclenchera jamais. Le `seed.sql` actuel
insère précisément `0.0 / 0.0`. On l'arme donc avec un plafond volontairement
bas — une alerte à 80 % n'est qu'un e-mail, alors qu'une boucle d'appels LLM
peut courir un mois sans que rien ne le signale.

```sql
-- Ajuster après une semaine de trafic réel, en lisant la dépense observée
-- dans Supervision → IA (routers/admin.py, miroir OpenRouter).
UPDATE public.app_settings
   SET value = '{"enabled": true, "weekly_usd": 20.0, "monthly_usd": 60.0}'::jsonb
 WHERE key = 'ai_budget';
```

**Deux « lignes manquantes » qui ne sont pas de la configuration mais produisent
le même silence :**

1. **Aucun compte.** `profiles` vide = personne ne peut se connecter. Il faut
   `scripts/bootstrap_admin.py` (livré par le chantier authentification).
2. **`partner_clients` vide.** `services/notifications.py` sélectionne les
   destinataires par `tier`. Table vide → l'envoi d'un AO **réussit avec zéro
   destinataire**, sans erreur, sans avertissement. C'est le contrôle n° 9.

Contrôle de présence, à rejouer autant de fois qu'on veut :

```bash
cd ~/app/backend/migrations && sudo -u postgres psql -d uti < verify_seed.sql
```

---

## 4. Séquence de bascule, minute par minute

**Indisponibilité : 22 minutes techniques**, dont 15 consacrées à vérifier
l'archive — c'est du temps acheté volontairement, pas du temps perdu.
**Indisponibilité fonctionnelle : jusqu'à 11 h 30**, le temps de ressaisir le
référentiel. C'est le prix de « repartir propre », il se paie une fois, et il
se paie un lundi matin.

### Avant le jour J — bascule du stockage (chantier A)

**À faire pendant que `SUPABASE_URL` pointe encore sur Supabase.** L'ordre n'est
pas un confort : `--rewrite-db` écrit dans la base courante.

| # | Geste | On vérifie quoi | Retour arrière |
|---|---|---|---|
| A1 | `sudo install -d -m 750 -o julian.talou -g julian.talou /var/lib/uti` puis `-m 700 … /var/lib/uti/files` | `ls -ld` → `drwx------ julian.talou` | `rm -rf /var/lib/uti` (rien dedans) |
| A2 | Dans `.env` : `LOCAL_STORAGE_DIR=/var/lib/uti/files`, `PUBLIC_BASE_URL=https://vps-cc93f2a8.vps.ovh.net`. **Ne pas encore toucher `STORAGE_BACKEND`.** | Le backend redémarre | `cp -a .env.avant-stockage .env` |
| A3 | `venv/bin/python scripts/migrate_storage_to_ovh.py --dry-run` | 38 objets annoncés, cinq buckets listés | — (n'écrit rien) |
| A4 | `venv/bin/python scripts/migrate_storage_to_ovh.py --vers local` | `find /var/lib/uti/files -type f \| wc -l` = 38 | `rm -rf /var/lib/uti/files/*` — Supabase est intact, le script ne supprime rien |
| A5 | `venv/bin/python scripts/migrate_storage_to_ovh.py --vers local --rewrite-db` | Les CV, avatars, pièces de conformité et images de modèles pointent vers le VPS **dans la base Supabase** | Restaurer les colonnes depuis l'archive du jour, ou relancer `--vers s3` ; les lecteurs acceptent encore les deux formes |
| A6 | `sed -i 's#^STORAGE_BACKEND=.*#STORAGE_BACKEND=local#' .env` puis `sudo systemctl restart uti-backend` | Un CV s'ouvre, un avatar s'affiche, une pièce d'AO se télécharge | Remettre `STORAGE_BACKEND=supabase` + redémarrer → **30 secondes** |
| A7 | `sudo systemctl start uti-backup` puis `cat /var/backups/uti/.dernier_succes_fichiers` | Une archive `uti/fichiers/…tar.age` est partie hors-site | — |
| A8 | `bash ~/app/backend/deploy/restore_drill.sh` | « N fichier(s) restauré(s), 0 absente(s) » | — |

**A7 et A8 ne sont pas facultatifs.** Tant qu'ils ne sont pas verts, les
fichiers ne sont sauvegardés nulle part : la migration aurait remplacé un
hébergeur qui les répliquait par un disque que personne ne copie.

### Dimanche 16 août — répétition générale

- Annonce aux 11 comptes : « plateforme indisponible lundi de 9 h à 12 h,
  chacun recevra un lien pour redéfinir son mot de passe ».
- Archive **à blanc** (`export_supabase_archive.sh`) puis restauration
  chronométrée dans une base jetable. **Si la restauration à blanc échoue, la
  bascule est reportée.** On ne bascule pas vers une base dont on ne sait pas
  restaurer l'ancêtre.

### Lundi 17 août

| Heure | Geste | On vérifie quoi | Retour arrière |
|---|---|---|---|
| 08:55 | Pré-vol : `systemctl is-active postgresql@18-main postgrest nginx uti-backend`, `verify_postgrest.py`, `verify_seed.sql`, dernier dump < 26 h | Les quatre services tournent, les 12 cas PostgREST passent, la configuration est en base | Rien n'a changé |
| 09:00 | `sudo systemctl stop uti-backend` | nginx renvoie 502. **Début de l'indisponibilité.** Plus aucune écriture ne part vers Supabase : l'archive qui suit est un point fixe | `systemctl start uti-backend` |
| 09:01 | `export_supabase_archive.sh ~/archive-supabase --with-secrets` | Le script se termine sur « ✅ Archive complète » | idem |
| 09:08 | `sha256sum -c SHA256SUMS` ; `createdb archive_test` ; `pg_restore --no-owner --no-privileges -d archive_test dump.pgcustom` ; `psql -d archive_test -c 'select count(*) from audit_log'` | 1396 lignes. **Si ce chiffre n'y est pas, on redémarre le backend sur Supabase et on reporte** | `systemctl start uti-backend` — rien n'a été modifié |
| 09:20 | `cp -a .env .env.avant-bascule-$(date +%F-%H%M)` ; `sed -i 's#^SUPABASE_URL=.*#SUPABASE_URL=http://127.0.0.1:8080#' .env` ; remplacer `SUPABASE_SERVICE_KEY` par `sudo cat /etc/postgrest/service_key.txt` | `grep -E '^SUPABASE_(URL\|SERVICE_KEY)=' .env` | `cp -a .env.avant-bascule-* .env` — le backend est arrêté, rien ne s'est produit |
| 09:22 | `sudo systemctl start uti-backend` | **Fin de l'indisponibilité technique (22 min).** `journalctl -u uti-backend -n 30 \| grep STARTUP` → « connexion Supabase OK » (le message garde son nom, c'est la base locale qui répond) | restaurer `.env` + `systemctl restart` → **60 secondes**, Supabase intact |
| 09:25 | `bash scripts/post_bascule_check.sh` | Tous les contrôles verts. Un seul rouge = on ne continue pas | idem 09:22 |
| 09:30 | `python scripts/bootstrap_admin.py` puis connexion + enrôlement MFA | Le premier admin se connecte. `profiles.mfa_required` vaut `true` par défaut (`schema.sql`) : l'enrôlement TOTP est obligatoire, pour les 11 comptes | idem |
| 09:45 | `sudo -u postgres psql -d uti < migrations/verify_seed.sql` puis l'`UPDATE ai_budget` du §3 | Aucune ligne MANQUANT ni INERTE | `UPDATE` inverse |
| 10:00 | Ressaisie du référentiel : clients, partenaires, `partner_clients` (**les tiers**) | `select count(*) from partner_clients` > 0, sinon les campagnes partiront à zéro destinataire | Retour possible, mais la saisie est perdue : l'exporter d'abord (`\copy` des 3 tables) |
| 11:30 | Invitations aux 10 autres comptes, **avec les mêmes UUID** que dans `auth_users.csv` | Chacun reçoit son e-mail (< 40 s, `OUTBOX_TICK_SECONDS=20`) | idem |
| 12:00 → 17:00 | Check-list fonctionnelle du §5, les 14 contrôles | 14/14 | idem |
| 17:30 | Décision : on reste, ou on revient | — | — |

**Supabase en lecture seule ?** Il n'y a rien à faire pour ça : à partir de
09:22, plus aucun processus ne connaît son adresse. Le projet reste **intact et
joignable** — c'est précisément ce qui rend le retour arrière possible. Ne rien
y toucher, ne rien y supprimer, ne pas le mettre en pause avant le 27 août.

**Point de non-retour : aucun, jusqu'à la suppression du projet.** C'est la
propriété la plus utile de ce plan et il faut la garder consciemment.

---

## 5. Plan de vérification avant suppression

À passer intégralement le jour J, puis **à rejouer le 30 août**, la veille de
la suppression. Les marqueurs de journal cités viennent du code réel.

| # | Fonction | Geste | Résultat attendu | Où regarder si ça échoue |
|---|---|---|---|---|
| 1 | **Connexion** | Se connecter avec un compte `ao` | Jeton + arrivée sur le tableau de bord | `journalctl -u uti-backend \| grep '\[AUTH\]'`. « Compte Auth trouvé mais profil introuvable » (`auth.py:531`) = le compte existe mais la ligne `profiles` manque |
| 2 | **MFA** | Scanner le QR, saisir les 6 chiffres | Connexion acceptée | `timedatectl` sur le VPS : un décalage > 30 s casse tous les TOTP. Puis `journalctl \| grep MFA` |
| 3 | **Invitation** | Créer une invitation, ouvrir le lien en navigation privée, créer le compte | Compte créé **avec le rôle de l'invitation** | `select * from invitations order by created_at desc limit 5`. Rappel : `POST /auth/register` exige `invite_token`, 403 sinon |
| 4 | **Reset mot de passe** | « Mot de passe oublié » | E-mail reçu en < 40 s | `select status, attempts, last_error from email_outbox order by created_at desc limit 5` ; `python scripts/test_smtp.py` |
| 5 | **Upload de CV** | Soumettre un CV sur un AO | Ligne dans `submissions` + fichier `/var/lib/uti/files/cvs/<ao_id>/<sid>.pdf` en `0600 julian.talou` | `[ERROR] POST /submissions` dans journald ; taille > 25 Mo refusée par nginx (`client_max_body_size`) ; répertoire absent ou droits |
| 6 | **Téléchargement de CV** | Cliquer sur le CV | Le PDF s'ouvre dans un nouvel onglet, URL `…/files/d/<jeton>` | 410 = lien expiré (normal au-delà d'1 h) ; 403 = signature invalide ; 404 = fichier absent du disque |
| 6bis | **CV NON public** | `curl -si https://<backend>/files/public/cvs/<ao_id>/<sid>.pdf` puis rejouer l'URL du contrôle 6 **une fois le jeton expiré** | **404** dans le premier cas, **410** dans le second | Un 200 sur `/files/public/cvs/…` voudrait dire que « cvs » est entré dans `PUBLIC_BUCKETS` : les 32 CV seraient lisibles par quiconque devine un chemin. Corriger **immédiatement** (`services/storage.py`) |
| 6ter | **Traversée de chemin** | `curl -si 'https://<backend>/files/public/avatars/../../../etc/passwd'` et la même chose avec `%2e%2e%2f` | **404**, jamais 200 | Un 200 signifie que le dépôt n'enferme plus les chemins : le `.env` du backend, qui porte vingt secrets, est un voisin de `/var/lib/uti/files`. `services/storage.py:_safe_join` |
| 6quater | **Un fichier déposé ne s'exécute pas** | Déposer un `.html` comme pièce de conformité, l'ouvrir | Le navigateur **télécharge**, il n'affiche pas | Servis depuis notre propre domaine, ces fichiers sont same-origin. `Content-Type: application/octet-stream` + `Content-Disposition: attachment` + `nosniff` attendus dans les en-têtes |
| 7 | **Matching (jointure embarquée)** | Lancer un matching sur un AO à ≥ 2 soumissions, ouvrir les résultats | `consultants` et `submissions` sont des **objets remplis** dans la réponse, pas `null` (`routers/matching.py:243`) | Un `null` = clé étrangère absente → rejouer `0017_matchings_consultant_fk.sql`. Chercher `PGRST200` dans `/var/log/nginx/uti-postgrest.error.log` |
| 8 | **Cartographie** | Ouvrir la carte | Les clients géocodés apparaissent | Colonnes `clients.latitude/longitude` ajoutées par `0016_schema_drift.sql`. Code `42703` dans les journaux = colonne manquante |
| 9 | **Envoi d'AO** | Publier un AO, notifier la liste 1 | **n > 0 destinataires** | `select count(*) from partner_clients` : à zéro, l'envoi réussit sans destinataire et rien ne le signale |
| 10 | **File d'e-mails** | Après l'envoi, interroger `email_outbox` | `queued` → `sent` en < 40 s | `[OUTBOX]` dans journald ; colonne `last_error` |
| 11 | **Purge RGPD** | Ouvrir l'écran de rétention | « Purge désactivée » **avec** `overdue_submissions` et `overdue_consultants` chiffrés (`data_retention.py:154-185`), pas un blanc | Un blanc = `retention_state()` lève. `[SCHED] purge RGPD en erreur` dans journald |
| 12 | **Journal d'audit** | Après le contrôle 7 : `select count(*) from audit_log where run_id = '<run>'` | > 0 lignes | `[AUDIT] event ... non journalisé` (`audit.py:56`) = la table ou la colonne manque |
| 13 | **Supervision IA** | Supervision → IA → « envoyer une alerte de test » | E-mail reçu | `[BUDGET]` dans journald. Vérifier que `ai_budget.weekly_usd` ≠ 0, sinon la surveillance réelle ne partira jamais (`ai_budget.py:144`) |
| 14 | **Étanchéité réseau** | Depuis une AUTRE machine : `nc -z -w3 164.132.44.212 5432 3000 8080` | Les trois ports **fermés** | `sudo ufw status verbose`. Seule l'API FastAPI doit être publique |

---

## 6. Critères de suppression du projet Supabase

**Toutes ces conditions doivent être vraies simultanément.** Une seule fausse =
on ne supprime pas, on reporte d'une semaine.

- [ ] **1. 14 jours pleins depuis la bascule** (17 → 30 août) sans retour
      arrière, sans intervention manuelle sur la base, sans redémarrage subi.
- [ ] **2. Une restauration réellement effectuée**, pas une intention : dump →
      base neuve → comptages identiques → **backend démarré contre elle** →
      connexion réussie. Deux fois : le 14 août (avant la bascule) et une
      seconde fois pendant l'observation. Durée mesurée et écrite dans le RUNBOOK.
- [ ] **3. Les 14 contrôles du §5 rejoués et verts le 30 août.**
- [ ] **4. `post_bascule_check.sh` en sortie 0 quatorze jours d'affilée.**
- [ ] **5. Sauvegardes automatiques tournant depuis ≥ 14 jours** : 14 fichiers
      datés, taille non nulle, `pg_restore --list` lisible sur le plus récent,
      rotation vérifiée.
- [ ] **6. Archive hors ligne vérifiée** : `sha256sum -c SHA256SUMS` OK, dump
      restauré une fois, 1396 lignes d'`audit_log` retrouvées, **stockée en
      deux endroits distincts dont un hors du VPS** (le VPS peut brûler), en
      0600 ou chiffrée.
- [ ] **7. Zéro dépendance résiduelle** : aucune URL `supabase.co` dans
      `~/app/backend/.env`, `STORAGE_BACKEND=local`, aucun appel `/auth/v1/`
      (la façade renvoie 501, donc un oubli se serait déjà manifesté). **Et
      aucune URL `supabase.co` restante en BASE** :
      `select count(*) from submissions where cv_url like '%supabase%'`,
      idem `profiles.avatar_url`, `partner_compliance_docs.file_url` et
      `email_templates.body` — quatre requêtes, quatre zéros. Une seule non
      nulle et la suppression du projet casse ce lien-là, en silence.
- [ ] **7 bis. Les FICHIERS sont sauvegardés ET restaurables.**
      `/var/backups/uti/.dernier_succes_fichiers` daté de moins de 24 h, et une
      exécution de `restore_drill.sh` qui affiche « 0 absente(s) » : chaque CV,
      pièce d'AO, attestation URSSAF et KBIS que la base restaurée référence
      existe dans l'archive restaurée. **Sans ce point, supprimer Supabase
      supprime la seule autre copie des fichiers.**
- [ ] **8. Les 11 comptes recréés et chacun s'est connecté au moins une fois**
      sur la base neuve. Six seulement s'étaient déjà connectés sur Supabase :
      c'est ce contrôle qui prouve la chaîne de bout en bout, sur de vraies
      personnes et pas sur le compte de l'administrateur.
- [ ] **9. Le secret TOTP en clair a été traité** : `auth_secrets.csv` détruit
      (`shred -u`), et l'enrôlement MFA refait par chacun.
- [ ] **10. Documentation à jour** : `RUNBOOK.md`, `DEPLOYMENT_OVH.md`,
      procédure de restauration écrite et datée.
- [ ] **11. Conformité à jour** : `compliance/ai-act/rgpd/REGISTRE-SOUS-TRAITANTS.md`
      créé, DPIA révisée, mentions légales corrigées. **Le changement de
      sous-traitant d'hébergement relève des art. 28 et 30 : il se documente
      avant, pas après.** Accord explicite du DPO (Sullyvan BIJON) tracé.
- [ ] **12. Projet Supabase en pause depuis ≥ 4 jours** sans le moindre incident.

### Combien de temps garder Supabase, et dans quel état

| Période | État du projet | Ce que ça achète |
|---|---|---|
| 17 → 26 août (10 j) | **Intact et joignable.** On n'y touche pas, on n'y supprime rien | Retour arrière en 60 secondes : une ligne de `.env` et un redémarrage |
| 27 → 30 août (4 j) | **En pause** (bouton Pause de la console — réversible en quelques minutes) | Prouve que plus rien ne dépend de lui. Une dépendance oubliée se manifeste ici, alors qu'il est encore restaurable |
| 31 août | **Suppression**, si les 12 critères sont cochés | — |

**À avoir archivé hors ligne AVANT de supprimer**, et vérifié :
`dump.pgcustom` + `dump.sql`, les 22 CSV, `auth_users.csv` (la correspondance
UUID → personne), `config_replay.sql`, les objets des cinq buckets,
`MANIFEST.txt` et `SHA256SUMS`. En deux exemplaires, dont un hors du VPS.

> Les sauvegardes automatiques de Supabase disparaissent **avec le projet**.
> L'archive hors ligne est le seul survivant. Elle n'est valide que si elle a
> été restaurée au moins une fois.
