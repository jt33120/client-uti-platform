-- ============================================================
-- Désabonnement par type de notification (lien « Ne plus recevoir
-- ce type de notification » en pied de chaque email de notification).
--
-- Clé par (adresse, catégorie) et NON par utilisateur : un destinataire
-- n'a pas toujours de compte (annonce à une adresse générique, CV envoyé
-- à un client), et un partenaire peut changer de compte sans changer
-- d'adresse. C'est l'adresse qui reçoit, c'est donc l'adresse qui se
-- désabonne.
--
-- Idempotent. À exécuter dans le SQL Editor / psql.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.email_optouts (
  email       text        NOT NULL,   -- toujours écrit en minuscules (services/email_optout.py)
  category    text        NOT NULL,   -- 'ao_new' | 'ao_relance' | 'cv_suivi' | 'annonces'
  created_at  timestamptz NOT NULL DEFAULT now(),
  source      text,                   -- 'lien' (clic dans l'email) | 'admin' | 'entete' (RFC 8058)
  PRIMARY KEY (email, category)
);

-- Le seul accès en lecture est « cette adresse s'est-elle désabonnée de
-- cette catégorie ? », fait juste avant chaque envoi.
CREATE INDEX IF NOT EXISTS email_optouts_email_idx ON public.email_optouts (email);

-- Verrouillage RLS, cohérent avec supabase_migration_rls_lockdown.sql :
-- la table ne porte aucune politique, donc aucun rôle applicatif ne la lit
-- ni ne l'écrit. Seul le backend y accède, avec la clé de service.
ALTER TABLE public.email_optouts ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.email_optouts IS
  'Désabonnements par (adresse, catégorie de notification). Écrit par le lien de désabonnement des emails ; lu avant chaque envoi.';
