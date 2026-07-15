-- 0005 — Brouillons d'appels d'offres.
--
-- Un AO en brouillon (is_draft = true) est INVISIBLE des partenaires, non matché
-- et non notifié, jusqu'à sa publication : POST /aos/:id/publish -> is_draft=false
-- (déclenche alors le matching). Publication à SENS UNIQUE (pas de dé-publication).
--
-- Réservé aux profils staff (onglet « Brouillons »). Les vues actives/archivées et
-- toutes les surfaces partenaires excluent is_draft = true.
alter table public.appels_offres add column if not exists is_draft boolean not null default false;

-- Index partiel : ne porte que sur la poignée de brouillons en cours.
create index if not exists idx_aos_is_draft on public.appels_offres (is_draft) where is_draft = true;
