-- ============================================================
-- 0020 — Distinguer « compte provisionné » de « mot de passe choisi »
-- ============================================================
--
-- LE DÉFAUT QUE CE FICHIER CORRIGE
--
-- Depuis la migration 0019, un compte hérité de Supabase n'a pas de ligne
-- `user_credentials` : c'est ce qui le rend reconnaissable. `/auth/forgot-password`
-- s'en sert pour choisir quoi envoyer — l'e-mail de MIGRATION (« la plateforme a
-- changé de serveur, vous n'avez rien demandé, c'est normal », lien valable
-- 7 jours) plutôt que celui de RÉINITIALISATION (« vous avez demandé à
-- réinitialiser », 1 heure).
--
-- Sauf que la première demande CRÉE cette ligne. Le signe distinctif disparaît
-- donc au premier clic, et la deuxième demande — celle qu'on fait quand le
-- premier e-mail tarde, c'est-à-dire le geste le plus naturel du monde —
-- traite la personne comme un compte ordinaire :
--
--   clic 1  →  e-mail de migration, lien valable 7 jours
--   clic 2  →  e-mail de réinitialisation, lien valable 1 HEURE,
--              et il INVALIDE le lien du premier (issue_reset écrase le jeton)
--
-- L'utilisateur se retrouve donc avec deux e-mails contradictoires, dont le
-- seul encore valide est celui qui lui parle d'une demande qu'il n'a pas faite
-- et qui expire soixante fois plus vite. Constaté en production le 11 août.
--
-- CE QUE CETTE COLONNE APPORTE
--
-- L'état « le compte existe » et l'état « la personne a choisi son mot de
-- passe » cessent d'être confondus. Le premier est une conséquence technique de
-- notre provisionnement ; le second est le seul qui décrive l'utilisateur.
--
-- DEFAULT true, et c'est important : toutes les lignes déjà en base ont été
-- créées par une inscription ou par bootstrap_admin, donc avec un vrai mot de
-- passe choisi par quelqu'un. Le défaut décrit correctement l'existant, et
-- seul le provisionnement de migration pose false.
--
-- Idempotent : rejouable sans effet sur une base qui a déjà cet état.

ALTER TABLE public.user_credentials
  ADD COLUMN IF NOT EXISTS password_defini BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.user_credentials.password_defini IS
  'false = ligne provisionnée pour une migration, la personne n''a pas encore '
  'choisi son mot de passe (le hachage en place n''est connu de personne). '
  'Passe à true en même temps que le mot de passe, dans consume_reset.';

-- Qui n'a pas encore repris son compte ? La question se pose à chaque relance
-- pendant la fenêtre de migration, et l'index la rend immédiate. Partiel :
-- il ne porte que sur les lignes en attente, qui sont l'exception et qui
-- disparaissent une à une — l'index rétrécit tout seul jusqu'à ne plus rien
-- coûter.
CREATE INDEX IF NOT EXISTS idx_user_credentials_en_attente
  ON public.user_credentials (user_id)
  WHERE password_defini = false;
