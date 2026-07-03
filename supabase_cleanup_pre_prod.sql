-- ============================================================
-- NETTOYAGE PRÉ-PRODUCTION — données de test/démo
-- À exécuter dans le SQL Editor de Supabase (projet UTI).
-- ============================================================
--
-- CONTEXTE : le code ne contient AUCUNE mock data. Les consultants / AO /
-- matchings de test que tu vois dans l'appli sont des LIGNES dans cette base.
-- Ce script t'aide à faire le ménage avant la mise en prod.
--
-- ⚠️  LIS CECI AVANT DE LANCER QUOI QUE CE SOIT :
--   1. Chaque bloc destructif est encadré par  BEGIN; ... ROLLBACK;
--      → tel quel, il NE SUPPRIME RIEN (mode simulation). Tu vois le nombre de
--        lignes qui SERAIENT supprimées. Pour APPLIQUER : remplace le `ROLLBACK;`
--        final du bloc par `COMMIT;` et relance CE bloc.
--   2. FAIS D'ABORD UNE SAUVEGARDE : Supabase → Database → Backups (ou un export
--      pg_dump). C'est irréversible une fois committé.
--   3. Ce script NE touche PAS :
--        - aux COMPTES (`profiles`, `invitations`) → tes utilisateurs restent.
--        - aux RÉGLAGES (`scoring_config`, `app_settings`, `email_templates`).
--      Il ne vise QUE les données métier (clients / AO / consultants / CV / scores).
--   4. Les FICHIERS CV (PDF) sont dans le Storage, PAS en base : supprimer une
--      ligne ici ne supprime pas le PDF. Pour vider les buckets : Supabase →
--      Storage → buckets `cvs` / `ao-sources` (à faire à la main si besoin).
--   5. Grâce aux clés étrangères ON DELETE CASCADE :
--        - supprimer un `appels_offres`  → supprime ses submissions, matchings,
--          human_decision et ao_consultant_state liés.
--        - supprimer un `consultants`    → supprime ses submissions + états liés.
--        - supprimer un `clients`         → supprime ses partner_clients ; ses AO
--          voient `client_id` passer à NULL (l'AO n'est PAS supprimé).

-- ============================================================
-- PARTIE 1 — INVENTAIRE (lecture seule, aucun risque)
-- Lance d'abord ceci pour voir ce que tu as et décider.
-- ============================================================

-- Volumétrie globale
SELECT 'clients'        AS table, count(*) FROM public.clients
UNION ALL SELECT 'appels_offres',    count(*) FROM public.appels_offres
UNION ALL SELECT 'consultants',      count(*) FROM public.consultants
UNION ALL SELECT 'submissions',      count(*) FROM public.submissions
UNION ALL SELECT 'matchings',        count(*) FROM public.matchings
UNION ALL SELECT 'partner_clients',  count(*) FROM public.partner_clients
UNION ALL SELECT 'profiles (comptes, gardés)', count(*) FROM public.profiles
ORDER BY table;

-- Détail des AO (pour repérer les tests et choisir un éventuel cas démo à garder)
SELECT ao.id, ao.title, c.name AS client, ao.status, ao.created_at,
       p.email AS cree_par,
       (SELECT count(*) FROM public.submissions s WHERE s.ao_id = ao.id) AS nb_cv,
       (SELECT count(*) FROM public.matchings   m WHERE m.ao_id = ao.id) AS nb_scores
FROM public.appels_offres ao
LEFT JOIN public.clients  c ON c.id = ao.client_id
LEFT JOIN public.profiles p ON p.id = ao.created_by
ORDER BY ao.created_at DESC;

-- Détail des consultants (vivier)
SELECT id, name, email, skills, created_at,
       (SELECT count(*) FROM public.submissions s WHERE s.consultant_id = consultants.id) AS nb_soumissions
FROM public.consultants
ORDER BY created_at DESC;

-- Détail des clients (⚠️ souvent le VRAI catalogue client, pas de la démo)
SELECT id, name, perimetre, parent_client_id, created_at,
       (SELECT count(*) FROM public.appels_offres a WHERE a.client_id = clients.id) AS nb_ao
FROM public.clients
ORDER BY name;


-- ============================================================
-- PARTIE 2 — OPTION A : suppression CHIRURGICALE par identifiants
-- La plus sûre. Colle les IDs repérés dans la Partie 1.
-- ============================================================
-- Supprimer des AO de test précis (leurs CV/scores partent en cascade) :
BEGIN;
  DELETE FROM public.appels_offres
  WHERE id IN (
    -- 'colle-ici-un-uuid-ao',
    -- 'et-un-autre'
  );
  -- Vérifie le nombre de lignes supprimées ci-dessus, puis :
ROLLBACK;   -- ⇦ remplace par COMMIT; pour appliquer

-- Supprimer des consultants de test précis :
BEGIN;
  DELETE FROM public.consultants
  WHERE id IN (
    -- 'colle-ici-un-uuid-consultant'
  );
ROLLBACK;   -- ⇦ COMMIT; pour appliquer


-- ============================================================
-- PARTIE 3 — OPTION B : GARDER UN SEUL cas démo, purger le reste de l'activité
-- Garde un AO « baseline » (avec ses CV + scores) et supprime TOUS les autres
-- AO + tous les consultants non rattachés à cet AO. Le catalogue clients et les
-- comptes sont CONSERVÉS.
-- ============================================================
BEGIN;
  -- 1) Mets ici l'UUID de l'AO démo à conserver (repéré en Partie 1) :
  --    (tu peux le stocker dans une table temporaire pour lisibilité)
  CREATE TEMP TABLE _keep_ao (id uuid) ON COMMIT DROP;
  INSERT INTO _keep_ao (id) VALUES
    ('REMPLACE-PAR-UUID-AO-DEMO');

  -- 2) Supprime tous les AUTRES AO (cascade : submissions, matchings, décisions)
  DELETE FROM public.appels_offres
  WHERE id NOT IN (SELECT id FROM _keep_ao);

  -- 3) Supprime les consultants qui ne sont plus soumis à aucun AO conservé
  DELETE FROM public.consultants c
  WHERE NOT EXISTS (
    SELECT 1 FROM public.submissions s WHERE s.consultant_id = c.id
  );

  -- Vérification : ce qui reste
  SELECT (SELECT count(*) FROM public.appels_offres) AS ao_restants,
         (SELECT count(*) FROM public.consultants)   AS consultants_restants,
         (SELECT count(*) FROM public.submissions)   AS cv_restants,
         (SELECT count(*) FROM public.matchings)     AS scores_restants;
ROLLBACK;   -- ⇦ COMMIT; pour appliquer


-- ============================================================
-- PARTIE 4 — OPTION C : PURGE COMPLÈTE de l'activité métier
-- Table rase des AO + consultants + CV + scores. À utiliser si tu repars d'une
-- base vierge côté activité. Choisis si tu gardes ou non le catalogue clients.
-- ============================================================
BEGIN;
  DELETE FROM public.matchings;     -- scores IA (regénérables)
  DELETE FROM public.submissions;   -- CV soumis aux AO
  DELETE FROM public.appels_offres; -- appels d'offres
  DELETE FROM public.consultants;   -- vivier

  -- Décommente SEULEMENT si tu veux aussi vider le catalogue clients
  -- (⚠️ ce sont probablement de VRAIS clients — SAFRAN, CMA, AGIRC-ARRCO… —
  --  à NE PAS supprimer dans la plupart des cas) :
  -- DELETE FROM public.partner_clients;   -- matrice d'accès partenaires↔clients
  -- DELETE FROM public.clients;           -- catalogue clients

  SELECT (SELECT count(*) FROM public.appels_offres) AS ao,
         (SELECT count(*) FROM public.consultants)   AS consultants,
         (SELECT count(*) FROM public.submissions)   AS cv,
         (SELECT count(*) FROM public.matchings)     AS scores,
         (SELECT count(*) FROM public.clients)       AS clients;
ROLLBACK;   -- ⇦ COMMIT; pour appliquer


-- ============================================================
-- APRÈS LE NETTOYAGE
-- ============================================================
-- - Vide au besoin les buckets Storage `cvs` et `ao-sources` (fichiers PDF
--   orphelins) via Supabase → Storage.
-- - Les compteurs de la Partie 1 doivent refléter l'état voulu.
-- - Les comptes (profiles) et réglages (scoring_config, app_settings,
--   email_templates) sont intacts : l'appli reste fonctionnelle, juste « vide »
--   côté données métier (ou avec ton seul cas démo).
