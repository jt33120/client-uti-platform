-- 0003 — Archivage des appels d'offres.
--
-- Un AO dont l'échéance est passée est auto-archivé par le planificateur
-- (services/scheduler.py, tick horaire). L'admin et les commerciaux peuvent
-- archiver / désarchiver à la main. Les AO archivés disparaissent des vues
-- partenaires ; ils restent visibles à l'équipe UTI (onglet « Archivés »).
--
-- Modèle volontairement à 2 colonnes (pas de booléen tri-état) :
--   archived     : true = archivé, false = actif.
--   archived_at  : horodatage du (dernier) archivage. JAMAIS remis à null au
--                  désarchivage — il sert de marqueur « déjà passé par
--                  l'archivage ». L'auto-archivage ne cible que
--                  archived = false AND archived_at IS NULL, donc il ne
--                  ré-archive JAMAIS un AO qu'un humain vient de désarchiver.
alter table public.appels_offres add column if not exists archived    boolean not null default false;
alter table public.appels_offres add column if not exists archived_at  timestamptz;

-- Balayage rapide des AO éligibles à l'auto-archivage (échéance passée, jamais
-- archivés). Index partiel : ne porte que sur la poignée de lignes concernées.
create index if not exists idx_aos_auto_archive
  on public.appels_offres (deadline)
  where archived = false and archived_at is null;

-- Filtrage courant actif / archivé (listes par onglet).
create index if not exists idx_aos_archived on public.appels_offres (archived);
