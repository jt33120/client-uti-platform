-- 0016 — Colonnes présentes en production mais absentes des fichiers SQL du repo.
--
-- POURQUOI CE FICHIER EXISTE
--
-- Une comparaison colonne par colonne entre la base de production et le schéma
-- reconstruit à partir des seuls fichiers SQL du repo (supabase_schema.sql +
-- supabase_migration_*.sql + backend/migrations/*.sql, rejoués sur un Postgres
-- vierge) a montré 22 tables sur 22 correctement recréées, mais SIX colonnes
-- manquantes. Elles avaient été ajoutées directement en base, sans migration.
--
-- Tant que la production tourne, ça ne se voit pas. Ça se voit le jour où l'on
-- reconstruit la base ailleurs — environnement de test, reprise après sinistre,
-- ou migration vers un autre hébergeur — et là deux fonctionnalités tombent
-- SILENCIEUSEMENT :
--
--   * la cartographie (routers/cartography.py lit clients.city / latitude /
--     longitude) : la carte se vide, sans erreur visible ;
--   * la purge RGPD (services/data_retention.py filtre sur
--     submissions.purged_at) : la requête échoue, la purge ne s'exécute plus.
--
-- Idempotent : rejouable sans effet sur une base qui a déjà ces colonnes.

-- ── Cartographie des clients ────────────────────────────────────────
-- Géocodage stocké en base pour éviter de rappeler le service de géocodage à
-- chaque affichage de la carte (cf. routers/cartography.py, backfill inclus).
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS city      TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS latitude  DOUBLE PRECISION;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;

-- Vestige : la colonne existe en production, entièrement NULL sur toutes les
-- lignes, et aucun code ne la lit — le « tier » réellement utilisé est
-- partner_clients.tier. On la recrée quand même pour que le schéma reconstruit
-- soit à l'identique : une migration doit refléter la réalité, pas la corriger
-- au passage. Sa suppression, si elle est décidée, mérite sa propre migration.
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS tier TEXT;

-- ── Traçabilité de l'affectation partenaire ↔ client ────────────────
-- Également sans lecture côté code aujourd'hui, mais alimentée par défaut.
ALTER TABLE public.partner_clients
  ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ DEFAULT NOW();

-- ── Purge RGPD des candidatures ─────────────────────────────────────
-- services/data_retention.py s'en sert à la fois comme marqueur d'anonymisation
-- et comme FILTRE (.is_("purged_at", "null")). Sans la colonne, la requête ne
-- renvoie pas « rien à purger » : elle échoue. La purge est alors inerte, ce
-- qui est le pire des deux mondes — aucune donnée effacée, aucune alerte.
ALTER TABLE public.submissions ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;

-- L'index partiel équivalent existe déjà sur consultants (0013). Le même motif
-- de requête s'applique aux candidatures : on l'ajoute pour que la purge reste
-- efficace quand la table grossit.
CREATE INDEX IF NOT EXISTS idx_submissions_purge
  ON public.submissions (submitted_at) WHERE purged_at IS NULL;
