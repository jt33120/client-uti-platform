-- ============================================================
-- 0018 — Authentification maison : sortie de GoTrue
-- ============================================================
--
-- CE QUE CE FICHIER REND POSSIBLE
--
-- Jusqu'ici, l'identité vivait dans `auth.users`, une table gérée par GoTrue
-- (le service d'authentification de Supabase). Le backend lui parlait en HTTP
-- sur onze points d'appel, et `public.profiles.id` référençait cette table par
-- clé étrangère. Supprimer le projet Supabase revenait donc à supprimer la
-- moitié de l'identité des comptes.
--
-- Cette migration déplace cette moitié chez nous, en trois gestes :
--   1. une table `user_credentials` qui porte le secret d'authentification ;
--   2. `profiles` qui cesse de dépendre de `auth.users` et fabrique son propre
--      identifiant ;
--   3. la levée d'un NOT NULL qui rendait certains comptes insupprimables.
--
-- ⚠️ AUCUN HACHAGE N'EST REPRIS DE SUPABASE. Les onze comptes existants portent
-- des bcrypt `$2a$10$` ; ils ne sont pas importés (décision assumée). Les trois
-- ou quatre comptes réellement actifs sont recréés par invitation, en argon2id.
-- Il n'y a donc jamais deux algorithmes en base, et aucun code de repli.
--
-- Idempotent : rejouable sans effet sur une base qui a déjà cet état.

-- ── 1. Identifiants de connexion ────────────────────────────────────
--
-- POURQUOI UNE TABLE, ET PAS DES COLONNES DANS `profiles`
--
-- `GET /auth/me` (routers/auth.py) et `PATCH /auth/me` font
-- `select("*")` sur `profiles` et renvoient la ligne au navigateur. Un
-- `password_hash` posé dans `profiles` partirait donc au premier chargement de
-- l'écran « Mon profil ». Le seul rempart serait un `data.pop("password_hash")`
-- à ne jamais oublier — c'est déjà le montage fragile qui protège `mfa_secret`,
-- et il suppose que chaque futur endpoint y pense.
--
-- Avec une table distincte, la fuite devient IMPOSSIBLE par construction :
-- PostgREST ne joint que ce qu'on lui demande, et il faudrait écrire
-- explicitement `select("*, user_credentials(*)")` pour l'atteindre.

CREATE TABLE IF NOT EXISTS public.user_credentials (
  -- Clé primaire ET clé étrangère : un compte, une ligne d'identifiants, pas
  -- deux. La cascade est ce qui remplace le `ON DELETE CASCADE` que GoTrue
  -- appliquait depuis auth.users — supprimer un profil emporte ses identifiants,
  -- sans second appel réseau susceptible d'échouer en silence.
  user_id                UUID PRIMARY KEY
                         REFERENCES public.profiles(id) ON DELETE CASCADE,

  -- Adresse de CONNEXION. Dupliquée avec profiles.email à dessein : ce sont
  -- deux choses différentes. `profiles.email` est une donnée d'affichage qu'un
  -- administrateur modifie depuis l'écran « Comptes » ; celle-ci est la clé de
  -- recherche de /auth/login. Les garder distinctes permet d'écrire l'une sans
  -- risquer de casser l'autre, et de rendre la recherche de connexion
  -- indépendante de l'état de `profiles`.
  -- Normalisée en minuscules (CHECK ci-dessous) : GoTrue traitait les adresses
  -- sans distinction de casse, et perdre cette propriété ferait « échouer » des
  -- connexions parfaitement légitimes.
  email                  TEXT NOT NULL UNIQUE
                         CHECK (email = lower(email)),

  -- Chaîne PHC argon2id complète : `$argon2id$v=19$m=19456,t=2,p=1$<sel>$<clé>`.
  -- Elle porte SES paramètres et SON sel — relever le coût plus tard n'invalide
  -- donc aucune ligne existante (cf. services/passwords.needs_rehash).
  -- 97 caractères aux paramètres courants ; TEXT plutôt que VARCHAR(n) pour ne
  -- pas avoir à migrer la colonne le jour où les paramètres changent.
  password_hash          TEXT NOT NULL,

  -- Échecs consécutifs. Remis à zéro à la première réussite.
  -- POURQUOI EN BASE : le garde-fou actuel (`_throttle`, routers/auth.py) vit
  -- dans la mémoire du processus. `bash deploy.sh` fait un `systemctl restart`,
  -- donc chaque déploiement remet ce compteur à zéro — et il ne serait pas
  -- partagé si l'on passait un jour à plusieurs workers uvicorn. Ici, il
  -- survit aux redémarrages et il est vu par tout le monde.
  failed_attempts        INTEGER NOT NULL DEFAULT 0,

  -- Fin du verrouillage. NULL = compte ouvert. Colonne distincte du compteur
  -- parce qu'elle répond à une autre question : « puis-je essayer maintenant ? »
  -- se lit sans avoir à rejouer la table des paliers côté application.
  locked_until           TIMESTAMPTZ,

  -- Date du dernier changement de mot de passe. Sert à répondre en revue
  -- (« depuis quand ce mot de passe est-il en place ? ») et à instruire un
  -- incident : un mot de passe changé la nuit précédant un accès anormal est un
  -- signal, et rien d'autre en base ne le porterait.
  password_changed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- EMPREINTE SHA-256 du jeton de réinitialisation — jamais le jeton lui-même.
  -- Une copie de cette table ne doit donner aucun lien utilisable : c'est toute
  -- la différence avec le stockage d'un jeton en clair. SHA-256 et non argon2
  -- parce que le jeton fait 256 bits d'entropie (il n'y a rien à deviner) et
  -- qu'un hachage salé interdirait de RETROUVER la ligne à partir du jeton reçu.
  -- UNIQUE : deux comptes ne peuvent pas porter la même empreinte, et l'index
  -- rend la recherche par jeton immédiate.
  reset_token_hash       TEXT UNIQUE,

  -- Échéance du jeton. Séparée de l'empreinte pour qu'un jeton périmé reste
  -- DISTINGUABLE d'un jeton déjà consommé côté exploitation, tout en renvoyant
  -- le même message à l'utilisateur.
  reset_token_expires_at TIMESTAMPTZ,

  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Une empreinte sans échéance serait un jeton éternel ; une échéance sans
  -- empreinte, une ligne morte. La contrainte interdit les deux.
  CONSTRAINT user_credentials_reset_pair CHECK (
    (reset_token_hash IS NULL AND reset_token_expires_at IS NULL)
    OR (reset_token_hash IS NOT NULL AND reset_token_expires_at IS NOT NULL)
  )
);

-- Recherche de connexion : `WHERE email = $1`. L'index UNIQUE de la colonne
-- suffit, aucun index supplémentaire n'est nécessaire ici.
-- L'index sur reset_token_hash vient lui aussi de sa contrainte UNIQUE.

-- RLS deny-all, comme les 22 autres tables (pg_policies est VIDE en production).
-- RLS activée + zéro policy = refus total pour `anon` et `authenticated` ; seul
-- le rôle utilisé par notre PostgREST (qui contourne la RLS) y accède.
-- Sur cette table-ci, c'est le dernier rempart si une clé publique fuitait.
ALTER TABLE public.user_credentials ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.user_credentials IS
  'Secrets d''authentification. SÉPARÉE de profiles parce que plusieurs endpoints '
  'renvoient une ligne profiles entière au navigateur. Ne jamais joindre.';
COMMENT ON COLUMN public.user_credentials.password_hash IS
  'Chaîne PHC argon2id. Aucun bcrypt n''a été repris de Supabase.';
COMMENT ON COLUMN public.user_credentials.reset_token_hash IS
  'SHA-256 hexadécimal du jeton de réinitialisation. Le clair ne part que dans l''e-mail.';

-- ── 2. `profiles` cesse de dépendre de GoTrue ───────────────────────
--
-- supabase_schema.sql:13 déclare :
--     id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE
--
-- Deux conséquences, toutes deux bloquantes après le retrait de GoTrue :
--   * le schéma `auth` n'existe pas sur un PostgreSQL nu — la clé étrangère est
--     inconstructible (backend/scripts/check_schema_drift.py:126-132 fabrique
--     d'ailleurs une fausse table `auth.users` pour pouvoir rejouer le schéma) ;
--   * la colonne n'a AUCUN DEFAULT : l'UUID venait de la réponse de GoTrue.
--     Sans lui, tout INSERT dans profiles sans `id` explicite échoue.
--
-- On retire donc la dépendance et on redonne à la table les moyens de fabriquer
-- son propre identifiant. `routers/auth.register` tire l'UUID côté Python
-- (uuid.uuid4) parce qu'il en a besoin AVANT l'insertion pour créer la ligne
-- d'identifiants dans la foulée ; le DEFAULT ci-dessous est le filet pour tout
-- autre chemin d'insertion, présent ou futur.

DO $$
DECLARE
  contrainte TEXT;
BEGIN
  -- Le nom de la contrainte n'est pas garanti (`profiles_id_fkey` par
  -- convention, mais une base reconstruite peut l'avoir nommée autrement) :
  -- on la retrouve par sa cible plutôt que par son nom.
  SELECT con.conname INTO contrainte
  FROM pg_constraint con
  JOIN pg_class ref ON ref.oid = con.confrelid
  JOIN pg_namespace ns ON ns.oid = ref.relnamespace
  WHERE con.conrelid = 'public.profiles'::regclass
    AND con.contype = 'f'
    AND ns.nspname = 'auth'
    AND ref.relname = 'users'
  LIMIT 1;

  IF contrainte IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.profiles DROP CONSTRAINT %I', contrainte);
    RAISE NOTICE 'profiles : contrainte % vers auth.users supprimée', contrainte;
  END IF;
END $$;

-- pgcrypto est déjà présent en production (et créé par check_schema_drift.py) ;
-- on le déclare quand même pour que ce fichier soit rejouable sur une base nue.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
ALTER TABLE public.profiles ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- ── 3. Un compte qui a pris une décision doit rester supprimable ────
--
-- supabase_migration_human_decision.sql:18 déclare :
--     decided_by UUID NOT NULL REFERENCES public.profiles(id) ON DELETE SET NULL
--
-- NOT NULL et ON DELETE SET NULL se contredisent : la suppression du profil
-- déclenche un SET NULL que le NOT NULL refuse. Tant que GoTrue existait, la
-- suppression passait par `auth.users` et la cascade emportait le profil — mais
-- routers/admin.delete_account supprime le profil EN PREMIER, donc le blocage
-- était déjà là : tout compte ayant validé un profil devenait insupprimable,
-- et l'effacement RGPD (routers/gdpr.py) avec lui.
--
-- On lève le NOT NULL, alignant cette colonne sur `created_by` / `submitted_by`
-- / `ran_by`, toutes nullables pour la même raison. La ligne d'audit survit à la
-- suppression de son auteur, ce qui est le comportement attendu d'un journal
-- (AI Act art. 12) : on perd le lien vers un compte effacé, pas la trace de la
-- décision.

ALTER TABLE public.human_decision ALTER COLUMN decided_by DROP NOT NULL;

COMMENT ON COLUMN public.human_decision.decided_by IS
  'Auteur de la décision. NULL après suppression du compte (ON DELETE SET NULL) : '
  'la trace de la décision survit à son auteur, comme l''exige un journal d''audit.';
