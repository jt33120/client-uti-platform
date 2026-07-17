-- Enrichit la fiche profil (cf. SettingsModal front + backend/routers/auth.py).
-- Complète la modale « Paramètres du profil » jusque-là limitée à
-- avatar / nom / email / mot de passe :
--   • title  : fonction / poste de la personne (ex. « Responsable recrutement »)
--   • phone  : téléphone direct (plateforme de staffing = on s'appelle)
--   • preferred_language : langue par défaut des documents générés (CV harmonisé
--     pré-sélectionné dans cette langue) — 'fr' par défaut
--   • notif_deadline_alerts / notif_missing_info : préférences de la CLOCHE
--     (feed /notifications) — chaque catégorie peut être masquée par l'utilisateur.
--     Défaut true : comportement actuel conservé pour les comptes existants.
-- Idempotent (IF NOT EXISTS) : réexécutable sans risque.

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS phone TEXT;

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS preferred_language TEXT NOT NULL DEFAULT 'fr';
ALTER TABLE public.profiles DROP CONSTRAINT IF EXISTS profiles_preferred_language_check;
ALTER TABLE public.profiles
  ADD CONSTRAINT profiles_preferred_language_check CHECK (preferred_language IN ('fr', 'en'));

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS notif_deadline_alerts BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS notif_missing_info BOOLEAN NOT NULL DEFAULT true;
