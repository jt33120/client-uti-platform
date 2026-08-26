-- ============================================================================
-- Rôles PostgreSQL du remplacement de Supabase — à exécuter en SUPERUSER sur la
-- base applicative. Idempotent : rejouable autant de fois que nécessaire.
--
--     sudo -u postgres psql -d uti -v owner=uti_admin \
--          < backend/deploy/roles_postgrest.sql
--
-- CE FICHIER N'EST PAS DANS backend/migrations/ ET NE DOIT PAS Y ALLER.
-- scripts/check_schema_drift.py rejoue `backend/migrations/0*.sql` sur une base
-- jetable (check_schema_drift.py:103) ; ces rôles sont des objets de CLUSTER, pas
-- de base. Les y placer ferait créer/altérer par un simple contrôle de dérive des
-- rôles partagés avec la production.
-- ============================================================================
\set ON_ERROR_STOP on

-- ── 1. Les quatre rôles, calqués sur Supabase ──────────────────────────────
-- Le backend n'utilise que `service_role`. `anon` et `authenticated` sont créés
-- quand même : le SQL versionné du dépôt les nomme (supabase_migration_rls_roles.sql)
-- et un GRANT vers un rôle inexistant fait échouer tout le fichier.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticator') then
    create role authenticator login;
  end if;
end $$;

-- ── 2. Pourquoi BYPASSRLS et pas « seulement des GRANT » ────────────────────
-- Les 22 tables ont la RLS ACTIVÉE et ZÉRO policy (pg_policies est vide en
-- production, cf. supabase_migration_rls_lockdown.sql). « RLS activée sans
-- policy » signifie : aucune ligne visible, quels que soient les GRANT. Un
-- GRANT SELECT donne le droit d'interroger la table ; il ne rend aucune ligne.
-- Trois issues existaient :
--   a) écrire des policies « permissive using (true) » pour service_role —
--      c'est réintroduire, table par table, les policies que le durcissement a
--      supprimées, avec le risque de s'appliquer aussi à anon si l'on oublie
--      un TO ;
--   b) faire de service_role le PROPRIÉTAIRE des tables — un propriétaire
--      échappe à la RLS, mais gagne aussi DROP et ALTER sur tout le schéma :
--      la clé de l'API deviendrait une clé de DDL ;
--   c) BYPASSRLS — un attribut de rôle, hors de portée d'une requête, qui
--      reproduit EXACTEMENT ce que fait la clé service_role de Supabase.
-- (c) est retenu. BYPASSRLS ne donne AUCUN privilège par lui-même : il faut
-- toujours les GRANT de la section 4. Les deux sont nécessaires, ni l'un ni
-- l'autre suffisant.
alter role service_role bypassrls;

-- `authenticator` est le seul rôle qui se connecte, et il ne doit RIEN pouvoir
-- faire par lui-même : NOINHERIT l'oblige à passer par le SET ROLE que PostgREST
-- exécute d'après la revendication `role` du jeton. Sans NOINHERIT, une requête
-- arrivant sans revendication exploitable s'exécuterait avec l'union des
-- privilèges des trois rôles.
alter role authenticator noinherit nobypassrls;
alter role anon nobypassrls;
alter role authenticated nobypassrls;

grant anon, authenticated, service_role to authenticator;

-- Plafond de durée pour TOUT ce qui entre par l'API. Posé sur `authenticator`
-- (le rôle de connexion) : les réglages par rôle s'appliquent à l'ouverture de
-- session et survivent au SET ROLE, donc service_role est couvert aussi.
-- Les migrations, elles, passent par uti_admin et ne sont pas bridées.
alter role authenticator set statement_timeout = '30s';
alter role authenticator set idle_in_transaction_session_timeout = '60s';

-- ── 3. Le schéma ────────────────────────────────────────────────────────────
-- PUBLIC (= tout rôle présent ou futur) perd l'accès au schéma ; on le rouvre
-- nommément. Sur PostgreSQL 15+ le CREATE était déjà retiré, pas le USAGE.
revoke all on schema public from public;
grant usage on schema public to authenticator, anon, authenticated, service_role;

-- ── 3bis. Propriétaire effectif des tables déjà chargées ───────────────────
-- « create database uti owner uti_admin » (install_db.sh) rend uti_admin
-- propriétaire de la BASE. Ça ne dit rien du propriétaire de chaque TABLE : si
-- le schéma a un jour été chargé par un autre rôle que :"owner" — par exemple
-- `sudo -u postgres psql -f backend/migrations/schema.sql`, le geste le plus
-- naturel pour un premier chargement — chaque table appartient à ce rôle-là,
-- et rien plus bas (section 4) ne le corrige : les GRANT qui suivent ne visent
-- QUE service_role, jamais :"owner" lui-même. :"owner" se retrouve alors sans
-- AUCUN privilège sur des tables dont il est censé être responsable.
--
-- Mesuré le 26 août 2026 : `PGUSER=uti_admin bash deploy/backup_db.sh`
-- (uti_admin correctement connecté, cf. le correctif PGUSER plus haut dans
-- l'historique) échoue quand même, sur la toute première table que pg_dump
-- tente de verrouiller :
--   pg_dump: error: query failed: ERROR:  permission denied for table ai_usage
--   pg_dump: detail: Query was: LOCK TABLE public.ai_usage, ... IN ACCESS SHARE MODE
-- Reproduit et vérifié en local : une table créée par `postgres` avec
-- uniquement le GRANT de la section 4 ci-dessous refuse même un LOCK ACCESS
-- SHARE à :"owner" — verrouiller une table exige au moins SELECT, et
-- :"owner" n'a jamais reçu ce SELECT nulle part. service_role, lui, fonctionne
-- sans interruption : c'est pour ça que la panne ne s'est jamais vue tant que
-- personne n'avait lancé un pg_dump.
--
-- Le correctif ci-dessous rend :"owner" propriétaire de tout ce qui existe
-- DÉJÀ dans public, à chaque exécution — donc sans effet la fois où la bonne
-- discipline (migrations jouées en tant que :"owner") a été respectée depuis
-- le début. Générer les ALTER puis les exécuter via \gexec, plutôt qu'un DO
-- $$ ... $$, pour que :'owner' se substitue normalement (l'interpolation
-- psql ne regarde pas l'intérieur d'un bloc PL/pgSQL de la même façon).
select format('alter table public.%I owner to %I', tablename, :'owner')
from pg_tables where schemaname = 'public'
union all
select format('alter sequence public.%I owner to %I', sequencename, :'owner')
from pg_sequences where schemaname = 'public';
\gexec

-- ── 4. Privilèges de table ──────────────────────────────────────────────────
-- anon / authenticated : rien. Le frontend ne parle jamais à la base (aucune
-- dépendance @supabase/* dans frontend/package.json) ; tout passe par FastAPI.
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;

grant all privileges on all tables    in schema public to service_role;
grant all privileges on all sequences in schema public to service_role;
grant execute on all functions        in schema public to service_role;

-- LE PIÈGE : les deux GRANT ci-dessus ne portent que sur les tables qui existent
-- MAINTENANT. La prochaine migration qui crée une table la laisserait invisible
-- à service_role — l'API répondrait 403 « permission denied » sur cette seule
-- table, longtemps après le déploiement. ALTER DEFAULT PRIVILEGES ferme la
-- brèche, mais UNIQUEMENT pour les objets créés par le rôle indiqué : les
-- migrations doivent donc être jouées en tant que :"owner", jamais en postgres.
alter default privileges for role :"owner" in schema public
  grant all privileges on tables to service_role;
alter default privileges for role :"owner" in schema public
  grant all privileges on sequences to service_role;
alter default privileges for role :"owner" in schema public
  grant execute on functions to service_role;

-- ── 5. Cache de schéma de PostgREST ─────────────────────────────────────────
-- PostgREST garde en mémoire la liste des tables, colonnes et clés étrangères.
-- Après un CREATE/ALTER TABLE il répond PGRST205 (« Could not find the table »)
-- jusqu'à rechargement. Ce déclencheur d'événement le prévient à chaque DDL, ce
-- qui rend le rechargement automatique et supprime une étape à oublier après
-- chaque migration. Placé dans un schéma dédié pour ne pas polluer `public`,
-- dont l'inventaire de fonctions sert de référence d'audit.
create schema if not exists maintenance;
comment on schema maintenance is
  'Outillage d''exploitation (non exposé par PostgREST). Ne jamais y mettre de données applicatives.';

create or replace function maintenance.pgrst_reload_schema()
returns event_trigger language plpgsql as $$
begin
  notify pgrst, 'reload schema';
end $$;

drop event trigger if exists pgrst_watch_ddl;
create event trigger pgrst_watch_ddl on ddl_command_end
  execute procedure maintenance.pgrst_reload_schema();

-- ── 6. Contrôles ────────────────────────────────────────────────────────────
-- Doit afficher : service_role bypassrls=t ; authenticator inherit=f, login=t.
select rolname, rolcanlogin as login, rolinherit as inherit, rolbypassrls as bypassrls
from pg_roles
where rolname in ('anon', 'authenticated', 'service_role', 'authenticator')
order by rolname;
