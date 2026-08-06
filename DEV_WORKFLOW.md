# Workflow de développement — `dev` puis `master` (prod)

Objectif : pouvoir **itérer sans jamais casser la prod**. La prod est le frontend
Vercel et le backend VPS, tous deux alimentés par la branche `master`.

## Les deux branches

| Branche | Rôle | Déploiement |
|---|---|---|
| `master` | **Production**. Toujours stable. | Vercel (prod) au push + `deploy.sh` sur le VPS |
| `dev` | **Intégration**. Le travail en cours s'y accumule et se teste. | Vercel crée une **URL de preview** à chaque push |

Règle d'or : **on ne pousse jamais directement sur `master`**. Tout passe par
`dev`, on teste sur la preview, puis on promeut vers `master`.

## Cycle type

```bash
# 1. Partir de dev à jour
git checkout dev
git pull origin dev

# 2. (Optionnel mais conseillé) une branche par sujet
git checkout -b feat/mon-sujet

# 3. Coder, committer
git add -A && git commit -m "feat: ..."

# 4. Pousser et ouvrir une PR vers dev
git push -u origin feat/mon-sujet
#   → PR  feat/mon-sujet → dev

# 5. Merger dans dev → Vercel déploie une PREVIEW. On teste sur l'URL de preview.

# 6. Quand dev est validé, promotion vers la prod :
#   → PR  dev → master   (c'est CE merge qui met en prod)
```

Après un merge sur `master` : le frontend se redéploie seul (Vercel), et pour le
backend on déploie sur le VPS (voir `RUNBOOK.md` §4) :

```bash
ssh -p 1622 julian.talou@164.132.44.212 'bash ~/app/backend/deploy.sh'
```

## Garder `dev` aligné sur `master`

Après chaque mise en prod (merge dans `master`), resynchroniser `dev` pour
qu'il reparte de l'état prod :

```bash
git checkout dev
git fetch origin
git merge origin/master        # dev = master + travaux non encore promus
git push origin dev
```

## Preview Vercel — mise en place (une fois)

Vercel déploie **automatiquement une preview pour chaque branche/PR** poussée
sur le repo GitHub connecté : aucune config à écrire dans le repo. L'URL de
preview apparaît :
- dans le **check Vercel** de la PR (`dev` → `master`, ou toute PR),
- dans le **dashboard Vercel** → projet → **Deployments** (badge *Preview*).

Points à vérifier côté dashboard Vercel (Settings → Git) :
- **Production Branch** = `master` (c'est déjà le cas : seule `master` déploie en prod).
- Les **Preview Deployments** sont activés (valeur par défaut).

> ⚠️ **CORS backend** : la preview Vercel a une URL `*.vercel.app`. Le backend
> n'autorise en CORS que les origines connues (`FRONTEND_URL`, localhost, et les
> previews de **ce** compte via les markers `utiplatform-` / `julian-talou` dans
> `backend/main.py`). Si une preview est bloquée en CORS, ajouter son marker
> d'URL à `_VERCEL_PREVIEW_MARKERS`. Les previews tapent le **backend de prod**
> (VPS) : pratique pour tester le front, mais toute écriture agit sur la **vraie
> base**. Pour tester des changements backend sans risque, préférer un backend
> local (`APP_ENV=dev`) ou une base Supabase de test.

## Hotfix urgent en prod

Si la prod casse et que `dev` contient du travail non fini :

```bash
git checkout -b hotfix/xxx origin/master   # on part de la prod, pas de dev
# ... correctif minimal ...
git push -u origin hotfix/xxx              # PR hotfix/xxx → master
# puis resync dev sur master (voir plus haut)
```
