-- ============================================================
-- 0014 — Coffre-fort de conformité partenaire (obligation de vigilance)
-- ============================================================
--
-- Art. L.8222-1 du code du travail : pour toute opération d'un montant au moins
-- égal à 5 000 € HT (art. R.8222-1 — PAR OPÉRATION, et non par cumul annuel avec
-- un même prestataire), le donneur d'ordre doit vérifier que son cocontractant
-- s'acquitte de ses obligations, à la conclusion du contrat PUIS TOUS LES SIX MOIS
-- jusqu'à la fin de son exécution (art. D.8222-5).
--
-- Sanction en cas de manquement : solidarité financière (L.8222-2 et L.8222-3) et
-- annulation des réductions de cotisations dont UTI bénéficie au titre de SES
-- PROPRES salariés (L.133-4-5 CSS).
--
-- ⚠️ Les paramètres retenus ici (seuil, périodicité, liste des pièces) suivent la
-- lecture consignée dans `compliance/QUESTIONS-CONSEIL-JURIDIQUE.md` (point D1),
-- EN ATTENTE de confirmation par le conseil juridique.
--
-- Deux régimes DISTINCTS, volontairement portés par le même modèle mais avec des
-- règles de validité différentes (cf. services/partner_compliance.py) :
--   • vigilance + immatriculation → art. L.8222-1, périodicité 6 mois ;
--   • liste nominative des salariés étrangers → art. L.8254-1 et D.8254-2,
--     exigible À LA CONCLUSION SEULEMENT, et uniquement pour les salariés soumis
--     à autorisation de travail.
--
-- Historique conservé : chaque dépôt crée une LIGNE. La pièce courante d'un type
-- est la plus récente. On ne met jamais à jour en place — l'attestation produite
-- il y a huit mois doit rester consultable pour démontrer qu'on l'avait bien
-- demandée à l'époque.

CREATE TABLE IF NOT EXISTS public.partner_compliance_docs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id    UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  doc_type      TEXT NOT NULL CHECK (doc_type IN ('vigilance', 'immatriculation', 'salaries_etrangers')),

  file_url      TEXT,
  filename      TEXT,

  -- Date d'émission de la pièce elle-même (et non du dépôt) : c'est elle qui
  -- fait courir la validité. Une attestation URSSAF de 5 mois déposée
  -- aujourd'hui n'est valable qu'un mois de plus.
  issued_at     DATE,

  -- Vérification d'authenticité auprès de l'URSSAF. Un PDF téléversé sans
  -- vérification NE PURGE PAS l'obligation : le texte impose de s'assurer de
  -- l'authenticité de l'attestation, pas seulement de la détenir.
  authenticity_checked_at TIMESTAMPTZ,
  authenticity_ref        TEXT,   -- code de sécurité relevé sur l'attestation
  checked_by              UUID REFERENCES public.profiles(id) ON DELETE SET NULL,

  uploaded_by   UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Recherche de la pièce courante par partenaire et par type.
CREATE INDEX IF NOT EXISTS idx_partner_compliance_lookup
  ON public.partner_compliance_docs (partner_id, doc_type, issued_at DESC);

-- Deny-all pour anon/authenticated ; le backend passe par `service_role`.
-- Cf. supabase_migration_rls_lockdown.sql.
ALTER TABLE public.partner_compliance_docs ENABLE ROW LEVEL SECURITY;
