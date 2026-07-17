import { Megaphone, Inbox, Hourglass, AlertTriangle } from 'lucide-react'

// File staff « À traiter » — kinds actionnables du feed /notifications/feed.
// Doit rester aligné sur les kinds servis par le backend (notifications.py).
export const TASK_KINDS = ['ao_undiffused', 'cv_untreated', 'stale_presentation']

export const TASK_ICON = {
  ao_undiffused: Megaphone,
  cv_untreated: Inbox,
  stale_presentation: Hourglass,
}
export const TASK_FALLBACK_ICON = AlertTriangle

// Libellés de regroupement (page « À traiter » dédiée).
export const KIND_LABEL = {
  ao_undiffused: 'AO à diffuser aux partenaires',
  cv_untreated: 'CV en attente de tri',
  stale_presentation: 'Présentations sans réponse',
}
