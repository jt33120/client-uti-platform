-- 0017 — matchings.consultant_id : aligner le type déclaré sur la réalité.
--
-- Seconde dérive trouvée en comparant la production au schéma reconstruit
-- depuis le repo. Celle-ci ne porte pas sur une colonne absente mais sur son
-- TYPE, ce qu'une comparaison par nom de colonne ne voit pas :
--
--   supabase_schema.sql:121  →  consultant_id TEXT NOT NULL
--                               -- "String to handle GPT responses"
--   production               →  consultant_id UUID, avec la clé étrangère
--                               matchings_consultant_id_fkey vers consultants(id)
--
-- POURQUOI ÇA COMPTE
--
-- routers/matching.py:243 lit `select("*, consultants(id, name, tjm, …)")`.
-- Cette jointure embarquée n'est possible que si PostgREST connaît une relation
-- entre les deux tables, donc si la clé étrangère existe. Or elle est
-- INCONSTRUCTIBLE avec le type déclaré dans le repo : Postgres refuse une clé
-- étrangère TEXT → UUID.
--
-- Sur une base reconstruite depuis le repo, l'écran de matching — le cœur du
-- produit — renverrait donc 500. Contrairement à la cartographie, l'échec ne
-- serait pas silencieux (routers/matching.py:308-310 relève l'exception), mais
-- la fonctionnalité serait morte.
--
-- Le commentaire « String to handle GPT responses » explique l'intention
-- d'origine : le modèle renvoyait des identifiants qui n'étaient pas toujours
-- des UUID valides. La production a depuis tranché dans l'autre sens, et c'est
-- le bon sens — un identifiant qui référence une ligne doit être contraint.
--
-- Sans effet sur la production, qui est déjà dans cet état. Utile aux
-- reconstructions : base de test, reprise après sinistre, changement d'hébergeur.

DO $$
BEGIN
  -- Conversion du type, seulement si elle n'a pas déjà eu lieu. Le USING
  -- convertit les valeurs existantes ; une valeur non convertible ferait
  -- échouer la migration, ce qui est le comportement voulu — mieux vaut le
  -- savoir que rattacher un matching à un consultant qui n'existe pas.
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'matchings'
      AND column_name = 'consultant_id' AND data_type <> 'uuid'
  ) THEN
    ALTER TABLE public.matchings
      ALTER COLUMN consultant_id TYPE UUID USING consultant_id::uuid;
  END IF;

  -- La clé étrangère : c'est elle que PostgREST utilise pour résoudre la
  -- jointure embarquée `consultants(...)`. Sans elle, pas d'écran de matching.
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.matchings'::regclass
      AND conname = 'matchings_consultant_id_fkey'
  ) THEN
    ALTER TABLE public.matchings
      ADD CONSTRAINT matchings_consultant_id_fkey
      FOREIGN KEY (consultant_id) REFERENCES public.consultants(id) ON DELETE CASCADE;
  END IF;
END $$;
