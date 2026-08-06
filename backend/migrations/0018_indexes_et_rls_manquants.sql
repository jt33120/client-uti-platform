-- 0018 — Index et RLS présents en production, absents des fichiers SQL du repo.
--
-- Troisième et dernier lot de dérive, trouvé en comparant les DÉFINITIONS
-- d'objets (et non leurs noms) entre la production et le schéma reconstruit :
-- trois « index manquants » n'étaient que des renommages (idx_aos_client vs
-- idx_aos_client_id, etc.), six étaient réellement absents.
--
-- Aucun n'est décoratif : chacun sert une requête que le code fait déjà.
-- Sur 26 consultants et 14 appels d'offres, leur absence ne se voit pas. Elle se
-- verrait sur un vrai volume, sous la forme d'écrans qui ralentissent sans
-- raison apparente — le genre de régression qu'on impute au réseau ou au LLM
-- pendant des semaines avant de regarder les plans de requête.
--
-- Idempotent. Sans effet sur la production, qui possède déjà ces objets.

-- ── Index ───────────────────────────────────────────────────────────
-- Recherche et tri de clients par nom (routers/clients.py, cartographie).
CREATE INDEX IF NOT EXISTS idx_clients_name ON public.clients USING btree (name);

-- Filtre sur la disponibilité des consultants. Index PARTIEL : la colonne est
-- majoritairement NULL (elle n'est renseignée que par les partenaires qui la
-- remplissent), donc indexer les NULL coûterait de l'écriture pour rien.
CREATE INDEX IF NOT EXISTS idx_consultants_availability
  ON public.consultants USING btree (availability_status)
  WHERE availability_status IS NOT NULL;

-- Remontée du matching vers la candidature d'origine (routers/matching.py).
CREATE INDEX IF NOT EXISTS idx_matchings_submission
  ON public.matchings USING btree (submission_id);

-- Liste des PAC, affichée du plus récent au plus ancien.
CREATE INDEX IF NOT EXISTS idx_pacs_created_at
  ON public.pacs USING btree (created_at DESC);

-- Sélection des partenaires par tier — le cœur du ciblage des campagnes d'AO
-- (services/notifications.py:31, appelé à chaque envoi et à chaque relance).
CREATE INDEX IF NOT EXISTS idx_partner_clients_tier
  ON public.partner_clients USING btree (tier);

-- Candidatures d'un consultant donné (fiche consultant, purge RGPD).
CREATE INDEX IF NOT EXISTS idx_submissions_consultant
  ON public.submissions USING btree (consultant_id);

-- ── RLS ─────────────────────────────────────────────────────────────
-- En production, les 22 tables ont la RLS ACTIVÉE et AUCUNE policy : c'est un
-- refus total, qui rend les données invisibles à tout rôle ne contournant pas la
-- RLS. Le backend, lui, utilise une clé service_role qui la contourne.
--
-- Le repo n'activait la RLS que sur 20 tables. Ces deux-là étaient donc ouvertes
-- sur une base reconstruite — et `ai_usage` contient les prompts et coûts de
-- chaque appel LLM, `client_reviews` les évaluations de consultants par les
-- clients. Ni l'une ni l'autre n'a vocation à être lisible sans passer par l'API.
ALTER TABLE public.ai_usage       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_reviews ENABLE ROW LEVEL SECURITY;
