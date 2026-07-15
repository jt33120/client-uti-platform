-- 0004 — Issue (bilan de clôture) d'un appel d'offres, au niveau AO.
--
-- Modèle PARTAGÉ : le « Bilan de clôture » (écriture) pose l'issue ici ; la vue
-- Pipeline (lecture) la consomme pour sa colonne terminale Gagné/Perdu, et la
-- Supervision l'agrège. Un seul propriétaire d'écriture -> jamais de divergence.
--
-- Aucune modif au niveau candidat : ao_consultant_state.deal_status (gagnee/
-- perdue), validation, sent_to_client_at… restent inchangés et servent de repli
-- (agrégation) tant que l'AO n'est pas clôturé (ao_outcome IS NULL).
alter table public.appels_offres
  add column if not exists ao_outcome         text,          -- pourvu | non_pourvu | sans_suite (NULL tant que non clôturé)
  add column if not exists winning_partner_id uuid,          -- profiles.id du partenaire gagnant (= submitted_by), NULL si pourvu hors plateforme / non pourvu
  add column if not exists outcome_note        text,          -- note libre de clôture
  add column if not exists outcome_at          timestamptz,   -- date du bilan (comble l'absence de deal_decided_at / closed_at)
  add column if not exists outcome_by          uuid;          -- profiles.id de l'auteur du bilan (audit)

-- CHECK posé via bloc gardé : ADD COLUMN IF NOT EXISTS n'ajoute PAS la contrainte
-- si la colonne préexistait, donc on la (re)pose ici de façon idempotente.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'appels_offres_ao_outcome_chk') then
    alter table public.appels_offres
      add constraint appels_offres_ao_outcome_chk
      check (ao_outcome is null or ao_outcome in ('pourvu', 'non_pourvu', 'sans_suite'));
  end if;
end $$;

-- Filtrage / agrégation par issue (pipeline + stats supervision).
create index if not exists idx_appels_offres_ao_outcome on public.appels_offres (ao_outcome);
