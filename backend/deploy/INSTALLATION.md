# Installation de PostgreSQL + PostgREST sur le VPS

Procédure d'installation de la base sur le VPS OVH, **sans interruption de
service** : la production reste sur Supabase du début à la fin. La bascule est
une étape distincte, et la dernière.

Compter environ **3 heures**, en une seule séance.

## Ce qui est installé

| | |
|---|---|
| PostgreSQL | **18** — paquet natif d'Ubuntu 26.04, correctifs par `apt` |
| PostgREST | **14.16** — binaire statique, empreinte vérifiée |
| Façade | nginx sur `127.0.0.1:8080`, chemin `/rest/v1/` |
| Exposition | **aucune** — rien n'écoute sur une interface publique |

> **Combinaison éprouvée.** PostgreSQL **18.4** + PostgREST **14.16** +
> `supabase-py` **2.9.0** : `scripts/verify_postgrest.py` passe **15 cas sur 15**
> sur le VPS, sur le schéma réel (6 août 2026). Les 367 requêtes `.table()` du
> backend n'ont pas à changer.
>
> Ce n'était pas acquis : la compatibilité avait d'abord été prouvée sur
> PostgreSQL 16, et le message d'erreur de `.single()` avait déjà changé entre
> PostgREST 12 et 14. Le point à surveiller lors d'une future montée de version
> reste le même — `routers/auth.py`, `routers/aos.py` et
> `services/email_outbox.py` décident en LISANT le texte des erreurs
> PostgreSQL (`23505`, `42703`, `violates foreign key`). Relancer
> `verify_postgrest.py` après toute montée de version majeure : c'est
> exactement ce qu'il vérifie.

PostgreSQL 18 plutôt que 17 : `pg_dump` refuse de sauvegarder un serveur plus
récent que lui, donc le client installé ici doit être au moins en 17 pour
pouvoir archiver Supabase (17.6) avant sa fermeture. La 18 satisfait cette
contrainte tout en étant le paquet de la distribution — les correctifs de
sécurité arrivent alors par le canal LTS d'Ubuntu, sans dépôt tiers à
maintenir. Sur une machine tenue par une seule personne, c'est décisif.

> **Connexion** : `ssh -p 1622 julian.talou@164.132.44.212`
> (le port n'est pas le 22)

---

## 0. Mesurer la machine AVANT toute décision (rien n'est modifié)

```bash
ssh -p 1622 julian.talou@164.132.44.212

# Les trois mesures qui déterminent tous les réglages :
nproc
free -h
df -h /

# Contexte utile pour la suite :
cat /etc/os-release | head -3
swapon --show || echo "aucun swap"
systemctl is-active uti-backend nginx
ss -ltn

# Ce que le backend consomme aujourd'hui, au repos :
systemctl show uti-backend -p MemoryCurrent

# Relever la collation de la base Supabase (elle doit être reproduite ici,
# sinon « ORDER BY name » ne classera plus les accents pareil) :
#   depuis n'importe quelle machine ayant psql et l'URI Supabase :
#   psql "$SUPABASE_DB_URI" -tAc "select datcollate from pg_database where datname=current_database()"
```

**Réussite :** Trois chiffres notés : vCPU, RAM en Mo, Go libres sur /. Le tableau ci-dessous donne alors les réglages, qu'install_db.sh appliquera automatiquement.

  RAM mesurée | shared_buffers | work_mem | maintenance | max_connections | plafond backend
  ------------|----------------|----------|-------------|-----------------|----------------
  < 3 Go      | 128 Mo         | 4 Mo     | 64 Mo       | 30              | 45 % de la RAM
  3 – 6 Go    | 256 Mo         | 8 Mo     | 128 Mo      | 30              | 45 % de la RAM
  6 – 12 Go   | 512 Mo         | 16 Mo    | 256 Mo      | 40              | 45 % de la RAM
  ≥ 12 Go     | 1 Go           | 32 Mo    | 512 Mo      | 50              | 45 % de la RAM

RAPPEL : la base fait 16 Mo. shared_buffers est dimensionné sur ELLE, pas sur la machine — 128 Mo, c'est déjà huit fois la base entière. Le reste de la RAM appartient à l'OCR.

Si df annonce moins de 5 Go libres : régler ça d'abord, install_db.sh refusera de continuer.

**Retour arrière :** Aucun : rien n'a été modifié.

## 1. Récupérer le kit et relire ce qui va être exécuté

```bash
cd ~/app && git pull origin master
ls -l ~/app/backend/deploy/

# Relire les deux fichiers qui décident de l'exposition réseau :
less ~/app/backend/deploy/nginx-postgrest.conf
grep -n 'server-host\|db-anon-role\|db-max-rows' ~/app/backend/deploy/install_db.sh

# Sauvegarde du .env AVANT que quoi que ce soit n'y touche :
cp -a ~/app/backend/.env ~/env-avant-migration-$(date +%F).bak
chmod 600 ~/env-avant-migration-*.bak
```

**Réussite :** backend/deploy/ contient install_db.sh, roles_postgrest.sql, postgrest.service, nginx-postgrest.conf. La commande grep affiche bien « server-host = "127.0.0.1" » et « db-max-rows = ${DB_MAX_ROWS} », et n'affiche AUCUNE ligne db-anon-role. La sauvegarde du .env existe.

**Retour arrière :** git checkout <sha_precedent> -- backend/deploy ; rien n'a encore été installé.

## 2. Lancer l'installation (PostgreSQL, PostgREST, nginx, rôles, clé)

```bash
# Si la collation relevée à l'étape 0 n'est PAS en_US.UTF-8, la passer ici :
#   sudo DB_COLLATE='<valeur relevée>' bash ~/app/backend/deploy/install_db.sh

sudo bash ~/app/backend/deploy/install_db.sh 2>&1 | tee ~/install_db-$(date +%F-%H%M).log
```

**Réussite :** Le script se termine sur « Installation terminée. » et les huit contrôles de l'étape 9 sont tous verts :
  ✓ requête sans jeton → 401
  ✓ clé de service acceptée
  ✓ PostgREST : boucle locale seulement
  ✓ façade nginx : boucle locale seulement
  ✓ PostgreSQL : boucle locale seulement
  ✓ lc_messages = C
  ✓ service_role contourne la RLS
  ✓ rechargement automatique du cache de schéma armé

Le code de sortie est 0. Le backend FastAPI n'a PAS été redémarré et parle toujours à Supabase : la production est intacte.

**Retour arrière :** sudo systemctl disable --now postgrest
sudo rm -f /etc/nginx/sites-enabled/uti-postgrest && sudo nginx -t && sudo systemctl reload nginx
sudo cp /etc/postgresql/17/main/pg_hba.conf.avant-uti /etc/postgresql/17/main/pg_hba.conf
sudo rm -f /etc/postgresql/17/main/conf.d/10-uti.conf
sudo rm -rf /etc/systemd/system/uti-backend.service.d
sudo systemctl daemon-reload && sudo systemctl restart postgresql@17-main uti-backend
cp -a ~/env-avant-migration-*.bak ~/app/backend/.env   # restaure l'ancienne clé Supabase
sudo systemctl restart uti-backend

Rien de tout cela n'est nécessaire tant que .env pointe encore vers Supabase : le backend n'a jamais utilisé la nouvelle pile.

## 3. Fermer le réseau (à la main, dans cet ordre — un ufw enable mal ordonné coupe le SSH)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status verbose

# PUIS, DEPUIS UNE AUTRE MACHINE (pas depuis le VPS) :
for p in 5432 3000 3001 8080; do
  nc -z -w3 164.132.44.212 $p && echo "$p OUVERT — À CORRIGER" || echo "$p fermé (attendu)"
done
curl -s -o /dev/null -w 'API publique: %{http_code}\n' https://vps-cc93f2a8.vps.ovh.net/health
```

**Réussite :** La session SSH n'est PAS coupée. Les quatre ports 5432 / 3000 / 3001 / 8080 sont fermés vus de l'extérieur, et https://vps-cc93f2a8.vps.ovh.net/health répond toujours 200. Seule l'API FastAPI est publique.

**Retour arrière :** sudo ufw disable   (revient à l'état sans pare-feu ; le service reste joignable)

Si le SSH est perdu malgré tout : console KVM depuis l'espace client OVH, puis « ufw disable ».

## 4. Vérifier la nouvelle pile SANS y basculer la production

```bash
# Charger une table jetable pour prouver la chaîne complète, sans toucher au
# schéma applicatif (qui relève du chantier migration) :
psql -d uti -c "create table if not exists _essai_bascule (id uuid primary key default gen_random_uuid(), n text)" \
             -c "alter table _essai_bascule enable row level security" \
             -c "insert into _essai_bascule(n) values ('ok')"

CLE=$(sudo cat /etc/postgrest/service_key.txt)

# a) refus sans jeton
curl -s -o /dev/null -w 'sans jeton : %{http_code} (attendu 401)\n' \
  http://127.0.0.1:8080/rest/v1/_essai_bascule

# b) lecture avec la clé, MALGRÉ la RLS activée sans policy
curl -s -H "Authorization: Bearer $CLE" \
  http://127.0.0.1:8080/rest/v1/_essai_bascule

# c) le client réel du backend, avec sa syntaxe exacte
cd ~/app/backend && source venv/bin/activate
python - <<'PY'
import subprocess
from supabase import create_client
cle = subprocess.run(['sudo','cat','/etc/postgrest/service_key.txt'],
                     capture_output=True, text=True).stdout.strip()
sb = create_client("http://127.0.0.1:8080", cle)
print("rest_url  :", sb.rest_url)
print("select    :", sb.table("_essai_bascule").select("*").execute().data)
print("count     :", sb.table("_essai_bascule").select("id", count="exact").execute().count)
print("maybe_single sur 0 ligne :",
      sb.table("_essai_bascule").select("*").eq("n","zzz").maybe_single().execute())
PY

# d) la RLS est bien opposée aux autres rôles
psql -d uti -c "set role anon; select count(*) from _essai_bascule"   # doit ÉCHOUER

psql -d uti -c "drop table _essai_bascule"
```

**Réussite :** (a) 401. (b) la ligne « ok » est renvoyée. (c) rest_url vaut http://127.0.0.1:8080/rest/v1, le select renvoie la ligne, count vaut 1, maybe_single renvoie None. (d) « permission denied for table _essai_bascule ».

Si les quatre passent, la pile est bonne. La production tourne toujours sur Supabase.

**Retour arrière :** psql -d uti -c "drop table if exists _essai_bascule"  — la table d'essai est le seul objet créé.

## 5. BASCULE (uniquement quand le schéma, les données ET l'auth maison sont en place)

```bash
# ⚠️ NE PAS FAIRE avant que :
#    - le chantier migration ait chargé schéma + données dans la base « uti » ;
#    - le chantier authentification maison soit déployé (sinon la connexion
#      des utilisateurs casse : routers/auth.py:370 et :485 appellent /auth/v1/).
#    La façade répond 501 sur /auth/v1/ pour rendre l'oubli immédiatement visible.

cd ~/app/backend
cp -a .env .env.avant-bascule-$(date +%F-%H%M)
sed -i 's#^SUPABASE_URL=.*#SUPABASE_URL=http://127.0.0.1:8080#' .env
grep -E '^SUPABASE_URL=' .env

bash ~/app/backend/deploy.sh
```

**Réussite :** deploy.sh affiche « ✅ Déploiement OK — backend en bonne santé, base accessible ». Puis, dans l'application : connexion, liste des consultants, ouverture d'un AO, upload d'un CV. Et dans les journaux :

  sudo journalctl -u uti-backend -n 50 | grep STARTUP
  → « [STARTUP] connexion Supabase OK » (le message garde son nom, c'est bien la base locale qui répond)

**Retour arrière :** cd ~/app/backend
cp -a .env.avant-bascule-* .env
sudo systemctl restart uti-backend
curl -s http://127.0.0.1:8000/health

Une seule ligne de .env sépare les deux mondes, et Supabase n'a rien perdu : le retour est immédiat et complet tant que le projet Supabase existe encore.