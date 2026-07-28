-- ============================================================
-- 0010 — PACs (Profils d'Accès Client) : rattrapage de DDL
-- ============================================================
--
-- Ces deux tables existaient en production sans DDL versionné : elles ont été
-- créées à la main dans l'éditeur SQL Supabase, et `routers/pacs.py` les utilise
-- depuis. Ce fichier reproduit à l'identique le schéma constaté en base
-- (contraintes et RLS comprises) pour qu'un environnement reconstruit depuis le
-- dépôt soit conforme à la production.
--
-- Idempotent : sans effet si les tables sont déjà là.
--
-- Un PAC est un modèle d'habilitation : un jeu de couples (client, niveau de
-- diffusion) applicable en masse à des partenaires, plutôt que de régler la
-- matrice partenaire × client ligne à ligne.

CREATE TABLE IF NOT EXISTS public.pacs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  description TEXT,
  created_by  UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.pac_clients (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pac_id     UUID NOT NULL REFERENCES public.pacs(id) ON DELETE CASCADE,
  client_id  UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
  -- Mêmes niveaux que la matrice partenaire × client (cf. Tier dans routers/pacs.py).
  tier       TEXT NOT NULL CHECK (tier IN ('list_1', 'list_2', 'suspended')),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  -- Un client n'apparaît qu'une fois par PAC : sinon deux niveaux contradictoires
  -- s'appliqueraient au même partenaire.
  UNIQUE (pac_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_pac_clients_pac_id    ON public.pac_clients(pac_id);
CREATE INDEX IF NOT EXISTS idx_pac_clients_client_id ON public.pac_clients(client_id);

-- Modèle de sécurité du projet (cf. supabase_migration_rls_lockdown.sql) :
-- RLS activée et AUCUNE policy ⇒ deny-all pour anon/authenticated. Le backend
-- passe par `service_role`, qui contourne la RLS et porte lui-même
-- l'autorisation (ici : require_admin).
ALTER TABLE public.pacs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pac_clients ENABLE ROW LEVEL SECURITY;
