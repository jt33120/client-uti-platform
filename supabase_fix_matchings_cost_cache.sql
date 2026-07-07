-- ============================================================
-- Réparation : colonne matchings.cost_usd « introuvable » (erreur PGRST204)
-- À exécuter dans le SQL Editor du projet UTI (Supabase). Idempotent.
-- ============================================================
-- Symptôme observé en prod (recette QA) : la persistance des matchings échoue
-- avec « Could not find the 'cost_usd' column of 'matchings' in the schema
-- cache ». La colonne est pourtant définie dans supabase_schema.sql.
--
-- Deux causes possibles, couvertes ci-dessous :
--   1) la colonne manque réellement (table créée avant son ajout) → on l'ajoute ;
--   2) elle existe mais le CACHE de schéma de PostgREST est périmé → on le
--      recharge (NOTIFY).
--
-- NB : le backend a été durci pour ne plus PERDRE le score si une colonne
-- manque (il l'insère sans), mais le suivi du coût IA et le score hybride ne
-- sont complets qu'une fois cette réparation faite.

-- 1) Garantir la colonne (ne fait rien si elle existe déjà).
ALTER TABLE public.matchings
  ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 4) DEFAULT 0;

-- 2) Forcer PostgREST à recharger son cache de schéma (corrige le PGRST204
--    même quand la colonne existait déjà).
NOTIFY pgrst, 'reload schema';

-- 3) Vérification : doit renvoyer une ligne (data_type = numeric).
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'matchings' AND column_name = 'cost_usd';
