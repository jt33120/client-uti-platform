-- ============================================================
-- 0015 — File d'attente d'envoi des emails
-- ============================================================
--
-- Jusqu'ici, `services/email.py` envoyait en direct, dans le fil de la requête
-- HTTP, sans reprise. Trois conséquences :
--   • un hoquet SMTP de deux secondes perdait l'email définitivement ;
--   • l'utilisateur attendait la poignée de main SMTP (jusqu'à 15 s) ;
--   • 10 des 12 points d'envoi n'étaient tracés nulle part.
--
-- L'appelant ne fait plus qu'y DÉPOSER. Le planificateur dépile, ouvre UNE
-- connexion SMTP pour tout le lot, et marque chaque ligne. La requête HTTP rend
-- la main immédiatement, rien ne se perd, et le journal devient universel par
-- construction puisque tout passe par la file.
--
-- Le corps est stocké RENDU (et non « template + contexte à rendre plus tard ») :
-- ce qui a été mis en file est ce qui part, même si un admin modifie le template
-- entre-temps. `template_key` et `context` sont conservés à titre de traçabilité.

CREATE TABLE IF NOT EXISTS public.email_outbox (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  to_email    TEXT NOT NULL,
  to_name     TEXT,
  reply_to    TEXT,
  subject     TEXT NOT NULL,
  html        TEXT NOT NULL,
  text        TEXT,

  -- Catégorie fonctionnelle : sert au filtrage du journal, et servira de base
  -- au désabonnement par catégorie (List-Unsubscribe).
  category    TEXT NOT NULL,
  template_key TEXT,
  context     JSONB,

  -- Rattachements facultatifs, pour retrouver un envoi depuis un AO ou un
  -- destinataire sans dépendre d'une recherche plein texte.
  ao_id        UUID,
  recipient_id UUID,
  created_by   UUID REFERENCES public.profiles(id) ON DELETE SET NULL,

  -- Rejouer un dépôt identique ne doit pas produire un doublon : un redémarrage
  -- au mauvais moment, un double clic, un tick rejoué. Clé construite par
  -- l'appelant (ex. « ao:<id>:relance:<partenaire>:<n° de relance> »).
  idempotency_key TEXT UNIQUE,

  status      TEXT NOT NULL DEFAULT 'queued'
              CHECK (status IN ('queued', 'sending', 'sent', 'dead')),
  attempts    INT NOT NULL DEFAULT 0,
  last_error  TEXT,
  -- Réessai à intervalle croissant : la ligne n'est éligible qu'à partir d'ici.
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  claimed_at  TIMESTAMPTZ,
  sent_at     TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Dépilement : les lignes prêtes, les plus anciennes d'abord.
CREATE INDEX IF NOT EXISTS idx_email_outbox_ready
  ON public.email_outbox (next_attempt_at)
  WHERE status = 'queued';

-- Journal : consultation par date, par destinataire, par AO.
CREATE INDEX IF NOT EXISTS idx_email_outbox_created ON public.email_outbox (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_outbox_recipient ON public.email_outbox (recipient_id);
CREATE INDEX IF NOT EXISTS idx_email_outbox_ao ON public.email_outbox (ao_id);

-- Reprise après redémarrage : retrouver les lignes coincées en « sending ».
CREATE INDEX IF NOT EXISTS idx_email_outbox_claimed
  ON public.email_outbox (claimed_at)
  WHERE status = 'sending';

-- Deny-all pour anon/authenticated ; le backend passe par `service_role`.
ALTER TABLE public.email_outbox ENABLE ROW LEVEL SECURITY;
