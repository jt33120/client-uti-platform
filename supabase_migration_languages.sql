-- ============================================================
-- Langues du CV + langue requise par AO
-- À exécuter dans le SQL Editor du projet UTI (Supabase).
-- ============================================================
-- Additif et idempotent. Objectif : détecter les langues d'un CV (surtout
-- l'anglais + niveau) et vérifier qu'un candidat couvre la langue imposée par
-- certains clients (ex. SAFRAN SES, CMA : « Langue impérative : Anglais courant »).
--
-- Le backend dégrade proprement si ces colonnes n'existent pas encore (les
-- écritures les concernant retombent sur le sous-ensemble déjà migré), mais
-- sans elles les langues détectées et la langue requise ne sont pas persistées.

-- 1) Langue exigée par le client sur un AO (texte libre concis, ex. "Anglais courant").
--    Auto-suggérée par la génération IA de l'AO, éditable par le staff.
ALTER TABLE public.appels_offres ADD COLUMN IF NOT EXISTS langue_requise TEXT;

-- 2) Langues détectées dans le CV, par résultat de matching.
--    Forme : [{"langue": "anglais", "niveau": "courant"}, ...].
ALTER TABLE public.matchings ADD COLUMN IF NOT EXISTS langues JSONB;
