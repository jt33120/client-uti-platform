-- ============================================================
-- 0013 — Anonymisation des consultants inactifs (RGPD art. 5-1-e)
-- ============================================================
--
-- `supabase_schema.sql` annonce depuis l'origine que « les consultants inactifs
-- PEUVENT être purgés automatiquement ». Ce n'était pas implémenté : seuls les CV
-- portés par les SOUMISSIONS étaient anonymisés (services/data_retention.py), la
-- fiche consultant conservant nom, email et téléphone indéfiniment.
--
-- Pourquoi anonymiser et non supprimer. Un DELETE sur `consultants` cascade sur
-- `matchings` et `ao_consultant_state`, et met à NULL le `consultant_id` de
-- `human_decision` — soit la destruction de la trace de décision humaine exigée
-- par l'AI Act (art. 14) et la falsification des statistiques agrégées. On vide
-- donc les champs identifiants en conservant la ligne, exactement comme le fait
-- déjà `_purge_one` pour les soumissions.
--
-- `purged_at` sert de marqueur d'idempotence (miroir de submissions.purged_at) :
-- sans lui, chaque tick re-balaierait les mêmes lignes déjà anonymisées.

ALTER TABLE public.consultants ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;

COMMENT ON COLUMN public.consultants.purged_at IS
  'RGPD — horodatage de l''anonymisation. NULL = fiche non purgée.';

-- La purge balaie par date d'activité et ignore les lignes déjà traitées.
CREATE INDEX IF NOT EXISTS idx_consultants_purge
  ON public.consultants (created_at)
  WHERE purged_at IS NULL;
