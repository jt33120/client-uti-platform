-- ============================================================
-- Grille de matching v2 — 2 axes qualitatifs + critère désactivable
-- À exécuter dans le SQL Editor du projet UTI (Supabase).
-- ============================================================
-- Additif et idempotent. Nouveautés v2 :
--   • deux axes notés par l'IA : « points forts du CV » et « éléments
--     différenciants » (colonnes étoiles s_* + poids dérivés w_*) ;
--   • un critère peut être mis à 0★ (poids 0) => EXCLU du score. La contrainte
--     de somme passe donc à 6 axes et accepte des poids nuls.
--
-- ⚠️ REQUISE pour v2 : sans elle, l'enregistrement de la grille échoue (la
-- contrainte historique exigeait que w_competences+…+w_tjm fasse 100, ce que
-- 4 axes sur 6 ne totalisent plus). La lecture/scoring dégradent proprement
-- sur les valeurs par défaut tant qu'elle n'est pas appliquée.

-- 1) Étoiles (0-5) des deux nouveaux axes qualitatifs.
ALTER TABLE public.scoring_config ADD COLUMN IF NOT EXISTS s_points_forts_cv         SMALLINT;
ALTER TABLE public.scoring_config ADD COLUMN IF NOT EXISTS s_elements_differenciants SMALLINT;

-- 2) Poids dérivés correspondants (rétro-compat + lisibilité de l'audit).
--    DEFAULT 0 pour que la ligne existante reste valide AVANT toute réécriture.
ALTER TABLE public.scoring_config ADD COLUMN IF NOT EXISTS w_points_forts_cv         INT NOT NULL DEFAULT 0;
ALTER TABLE public.scoring_config ADD COLUMN IF NOT EXISTS w_elements_differenciants INT NOT NULL DEFAULT 0;

-- 3) Remplace la contrainte de somme (désormais 6 axes) et autorise les poids
--    nuls (critère désactivé à 0★).
ALTER TABLE public.scoring_config DROP CONSTRAINT IF EXISTS scoring_weights_sum;
ALTER TABLE public.scoring_config ADD CONSTRAINT scoring_weights_sum CHECK (
  w_competences + w_seniorite + w_contexte
  + w_points_forts_cv + w_elements_differenciants + w_tjm = 100
);
