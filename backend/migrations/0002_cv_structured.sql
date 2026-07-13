-- CV structuré canonique (format Groupement-IT) par soumission.
-- Source de vérité de la vue « CV analysé » ET du texte cité par l'IA au scoring :
-- l'IA cite des extraits de CE JSON → le surlignage devient exact (P1 transparence).
-- Construit à l'upload (best-effort, services/cv_structured.py) ; backfillé à la
-- demande. À exécuter dans Supabase → SQL Editor. Idempotent (IF NOT EXISTS).
--
-- Le code backend est résilient à l'absence de cette colonne (repli sur cv_text
-- brut) : l'ordre déploiement / migration n'a pas d'importance.
alter table public.submissions
  add column if not exists cv_structured jsonb;

comment on column public.submissions.cv_structured is
  'CV structuré anonymisé (format GRP-IT) : {title, synthese[], experiences[], competences{}, langues[], formation[]}. Source de la vue « CV analysé » et des citations IA (surlignage exact).';
