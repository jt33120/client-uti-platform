import clsx from 'clsx'
import { Star, ListChecks, AlertCircle, Ban } from 'lucide-react'

// Pastille de niveau d'accès partenaire (Liste 1 / Liste 2 / Suspendu).
// Composant unique et partagé — évite les 5 copies divergentes qui existaient
// dans ClientsPage / AOSPage / PacsPage / ClientDetailPage / PartnerDetailPage.
//   <TierBadge tier={tier} />              → rien si pas de tier
//   <TierBadge tier={tier} showEmpty />    → « Aucun accès » si pas de tier
const TIER_MAP = {
  list_1: { cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20', label: 'Liste 1', Icon: Star },
  list_2: { cls: 'bg-brand-500/10 text-brand-300 border-brand-500/20', label: 'Liste 2', Icon: ListChecks },
  suspended: { cls: 'bg-red-500/10 text-red-400 border-red-500/20', label: 'Suspendu', Icon: AlertCircle },
}

export function TierBadge({ tier, showEmpty = false }) {
  const c = TIER_MAP[tier]
  if (!c) {
    if (!showEmpty) return null
    return (
      <span className="badge border border-slate-700 bg-slate-800 text-slate-500 text-[10px]">
        <Ban size={9} /> Aucun accès
      </span>
    )
  }
  return (
    <span className={clsx('badge border text-[10px]', c.cls)}>
      <c.Icon size={9} /> {c.label}
    </span>
  )
}
