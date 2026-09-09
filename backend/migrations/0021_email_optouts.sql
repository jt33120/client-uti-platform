-- ============================================================
-- 0021 — Désabonnement par type de notification
-- ============================================================
--
-- Support du lien « Ne plus recevoir ce type de notification » posé en pied de
-- chaque email de notification (backend/services/email_optout.py).
--
-- CLÉ PAR (ADRESSE, CATÉGORIE), PAS PAR UTILISATEUR
--
-- Une partie des destinataires n'a pas de compte : une annonce part parfois à
-- une adresse générique, et le CV envoyé au client final ne correspond à aucun
-- `profiles.id`. À l'inverse, un partenaire peut changer de compte sans changer
-- d'adresse. C'est l'adresse qui reçoit, c'est donc elle qui se désabonne — une
-- clé étrangère vers `profiles` rendrait la moitié des cas inexprimables.
--
-- L'adresse est TOUJOURS écrite en minuscules, normalisée par le code
-- (email_optout.normalize) avant écriture comme avant lecture. Pas de citext :
-- l'extension n'est pas installée sur cette base, et une normalisation faite en
-- un seul endroit du code se lit mieux qu'un type dont le comportement est
-- invisible à la lecture d'une requête.
--
-- Idempotent : rejouable sans effet sur une base qui a déjà cet état.

CREATE TABLE IF NOT EXISTS public.email_optouts (
  email       text        NOT NULL,
  category    text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  source      text,
  PRIMARY KEY (email, category)
);

COMMENT ON TABLE public.email_optouts IS
  'Désabonnements par (adresse, catégorie de notification). Écrit par '
  '/emails/unsubscribe ; lu avant chaque envoi (services/email_optout.py).';
COMMENT ON COLUMN public.email_optouts.email IS
  'Toujours en minuscules — normalisé par email_optout.normalize().';
COMMENT ON COLUMN public.email_optouts.category IS
  'ao_new | ao_relance | cv_suivi | annonces (email_optout.CATEGORIES).';
COMMENT ON COLUMN public.email_optouts.source IS
  'lien = clic dans l''email · entete = bouton natif du client mail (RFC 8058) '
  '· admin = retrait manuel.';

-- « Cette adresse s'est-elle désabonnée de cette catégorie ? » est la seule
-- lecture faite, et elle porte sur les DEUX colonnes de la clé primaire, qui
-- l'indexe déjà. Aucun index supplémentaire : il ne servirait aucune requête
-- existante et se paierait à chaque écriture.

-- ── RLS ─────────────────────────────────────────────────────────────────────
-- Activée, ZÉRO policy : c'est le verrouillage retenu pour les 22 autres tables
-- (supabase_migration_rls_lockdown.sql). « RLS activée sans policy » = aucune
-- ligne visible, quels que soient les GRANT. Le backend passe malgré tout,
-- parce que `service_role` porte l'attribut de contournement de la RLS, posé
-- une fois pour toutes dans deploy/roles_postgrest.sql §2 — un attribut de
-- rôle, hors de portée d'une requête, qui reproduit ce que faisait la clé
-- service_role de Supabase.
--
-- Cet attribut n'est PAS reposé ici, et le nom exact de l'attribut n'est même
-- pas écrit : tests/test_deploy_db_kit.py interdit littéralement ces mots dans
-- backend/migrations/0*.sql, parce que check_schema_drift.py rejoue ces
-- fichiers sur une base jetable — un simple contrôle de dérive modifierait
-- alors des rôles de CLUSTER partagés avec la production.
ALTER TABLE public.email_optouts ENABLE ROW LEVEL SECURITY;

-- ── LE PIÈGE DES GRANT, NOMMÉ DANS roles_postgrest.sql §4 ───────────────────
-- Les GRANT de `roles_postgrest.sql` ne portent que sur les tables existant AU
-- MOMENT où il tourne. Une table créée par une migration ultérieure resterait
-- invisible à `service_role`, et l'API répondrait 403 « permission denied for
-- table email_optouts » sur cette seule table, longtemps après le déploiement —
-- ici, sous la forme d'un lien de désabonnement qui échoue pour tout le monde.
--
-- ALTER DEFAULT PRIVILEGES couvre ce cas, mais UNIQUEMENT pour les objets créés
-- par uti_admin.
--
-- ⚠️ OÙ JOUER CE FICHIER : DANS LA BASE QUE LE BACKEND LIT, pas dans celle qui
-- porte le même nom que le projet. Au 9 septembre 2026, `backend/.env` de
-- production porte encore SUPABASE_URL=https://….supabase.co : la base servie
-- est SUPABASE. Cette migration a d'abord été jouée sur le PostgreSQL du VPS —
-- une base réelle, complète, et que rien ne lit. La table existait, et le lien
-- de désabonnement échouait quand même. Vérifier AVANT :
--
--   grep '^SUPABASE_URL=' ~/app/backend/.env
--
--   • …supabase.co     → éditeur SQL de la console Supabase. `service_role` y
--                        existe et porte le contournement de RLS : le fichier
--                        passe tel quel.
--   • …127.0.0.1:8080  → sur le VPS, PAR LA SOCKET UNIX. Il n'existe AUCUN mot
--                        de passe de base (install_db.sh installe une
--                        authentification « peer » avec correspondance) : ni
--                        PGPASSWORD, ni -h, qui forceraient TCP puis scram.
--
--       ssh -p 1622 julian.talou@164.132.44.212
--       psql -U uti_admin -d uti -v ON_ERROR_STOP=1 \
--         -f ~/app/backend/migrations/0021_email_optouts.sql
--
-- Le GRANT explicite ci-dessous est une ceinture en plus des bretelles : il rend
-- la table utilisable même si quelqu'un joue ce fichier en tant que `postgres`,
-- ce qui est le geste le plus naturel et exactement celui qui casse les
-- privilèges par défaut. Redondant dans le cas nominal, et c'est voulu — le
-- coût d'une ligne inutile est nul, celui d'un 403 découvert en production ne
-- l'est pas.
GRANT ALL PRIVILEGES ON TABLE public.email_optouts TO service_role;

-- Le cache de schéma de PostgREST se recharge tout seul : le déclencheur
-- d'événement `pgrst_watch_ddl` (roles_postgrest.sql §5) émet NOTIFY à chaque
-- DDL. Rien à faire après cette migration — pas de `NOTIFY pgrst` manuel, pas
-- de redémarrage.
