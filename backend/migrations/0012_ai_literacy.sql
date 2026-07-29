-- ============================================================
-- 0012 — Registre de littératie IA (AI Act, art. 4)
-- ============================================================
--
-- L'art. 4 impose aux fournisseurs et déployeurs de garantir un niveau suffisant
-- de maîtrise de l'IA chez les personnes qui utilisent leurs systèmes. C'est la
-- SEULE obligation de l'AI Act déjà exigible pour UTI (depuis le 2 février 2025,
-- assouplie en obligation de moyens depuis le 27/07/2026) — et la seule qui ne
-- soit pas concernée par le report au 2 décembre 2027.
--
-- Une obligation de moyens se démontre par une trace. D'où l'attestation
-- horodatée, par utilisateur, et versionnée : si le contenu de la sensibilisation
-- change de façon substantielle, la version change et les attestations
-- précédentes cessent d'être à jour.
--
-- Idempotent.

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS ai_literacy_ack_at  TIMESTAMPTZ;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS ai_literacy_version TEXT;

COMMENT ON COLUMN public.profiles.ai_literacy_ack_at IS
  'AI Act art. 4 — horodatage de la dernière attestation de sensibilisation IA.';
COMMENT ON COLUMN public.profiles.ai_literacy_version IS
  'Version du contenu attesté (services/ai_literacy.py). Une version antérieure = à refaire.';
