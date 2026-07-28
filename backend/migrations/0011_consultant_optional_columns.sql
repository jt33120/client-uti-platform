-- ============================================================
-- 0011 — Consultants : colonnes présentes en prod, jamais versionnées
-- ============================================================
--
-- Ces six colonnes ont été ajoutées à la main en production. Le code s'en
-- accommodait via `_OPTIONAL_COLS` dans `routers/consultants.py` : en cas
-- d'échec d'insertion, la ligne était rejouée SANS elles, silencieusement.
--
-- Ce repli est acceptable pour de la géolocalisation (au pire, un consultant
-- n'apparaît pas sur la carte). Il ne l'est pas pour `consent_at`, qui porte la
-- PREUVE du consentement RGPD : sur un environnement reconstruit depuis le
-- dépôt, la colonne aurait manqué et chaque consultant aurait été créé sans
-- trace de consentement, sans qu'aucune erreur ne remonte.
--
-- D'où ce fichier + le retrait de `consent_at` de `_OPTIONAL_COLS`.
--
-- Idempotent : sans effet là où les colonnes existent déjà (donc en prod).

ALTER TABLE public.consultants ADD COLUMN IF NOT EXISTS city                TEXT;
ALTER TABLE public.consultants ADD COLUMN IF NOT EXISTS latitude            DOUBLE PRECISION;
ALTER TABLE public.consultants ADD COLUMN IF NOT EXISTS longitude           DOUBLE PRECISION;
ALTER TABLE public.consultants ADD COLUMN IF NOT EXISTS availability_status TEXT;
ALTER TABLE public.consultants ADD COLUMN IF NOT EXISTS available_from      DATE;

-- Preuve RGPD : horodatage du consentement au traitement. NULL = pas de
-- consentement recueilli (et non « consentement inconnu ») : le champ ne doit
-- jamais être renseigné autrement que par un acte explicite de l'utilisateur.
ALTER TABLE public.consultants ADD COLUMN IF NOT EXISTS consent_at          TIMESTAMPTZ;
