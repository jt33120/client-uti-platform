-- 0007 — Retour client (page de review par lien sécurisé) + marge sur affaire gagnée.
--
-- Deux features de cette itération, une seule migration (idempotente, appliquée à
-- la main dans le SQL editor Supabase). Toutes les colonnes sont additives et le
-- code backend dégrade proprement si cette migration n'est pas encore appliquée
-- (front déployé avant backend avant migration).
--
-- (A) RETOUR CLIENT — le client, via un lien sécurisé (sans compte), donne son avis
--     sur les profils qui lui ont été présentés. La décision est portée AU NIVEAU
--     CANDIDAT (même table que sent_to_client_at, qui définit déjà la short-list).
alter table public.ao_consultant_state
  add column if not exists client_decision      text,          -- 'interesse' | 'refuse' | 'a_revoir' (NULL = pas de retour)
  add column if not exists client_decision_at   timestamptz,   -- horodatage du retour client
  add column if not exists client_decision_note text;          -- commentaire libre du client (optionnel)

-- (C) MARGE — TJM vendu au client vs TJM acheté au consultant, en €/jour. STAFF-ONLY :
--     ces colonnes ne sont JAMAIS exposées au partenaire. NULLable (une marge non
--     saisie ≠ 0 €). La marge elle-même est dérivée (vente - achat) côté applicatif,
--     jamais stockée en colonne générée (incompatible upsert).
alter table public.ao_consultant_state
  add column if not exists tjm_achat integer,   -- coût d'achat (pré-rempli depuis consultants.tjm)
  add column if not exists tjm_vente integer;    -- prix vendu négocié (pré-rempli depuis appels_offres.budget_max)

-- Lien de retour client : token public, révocable et expirant (modèle table, PAS de
-- JWT — pour pouvoir révoquer et porter un état). Scope = un AO + son client. La page
-- publique n'expose QUE les profils de cet AO déjà présentés (sent_to_client_at NOT NULL).
create table if not exists public.client_reviews (
  id          uuid primary key default gen_random_uuid(),
  token       text not null unique,
  ao_id       uuid not null references public.appels_offres(id) on delete cascade,
  client_id   uuid references public.clients(id) on delete set null,
  created_by  uuid,
  expires_at  timestamptz,
  revoked_at  timestamptz,
  created_at  timestamptz not null default now()
);
create index if not exists idx_client_reviews_token on public.client_reviews (token);
create index if not exists idx_client_reviews_ao    on public.client_reviews (ao_id);
