import clsx from 'clsx'
import { Star, ListChecks, AlertCircle, Ban, Sparkles } from 'lucide-react'

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

// Divulgation « contenu généré par IA » — AI Act art. 50 (applicable 2 août 2026).
//
// Une icône ✨ ne vaut PAS divulgation : le règlement demande que la personne
// exposée au contenu soit informée, de façon claire et distinguable, qu'il est
// généré artificiellement. D'où une mention textuelle, et non un pictogramme.
//
// À poser partout où un texte produit par un modèle est affiché : résumé d'AO,
// synthèse de vivier, CV harmonisé, motif de refus, réponses de l'assistant.
//
//   <AiGeneratedBadge />                        → « Généré par IA »
//   <AiGeneratedBadge label="Résumé généré par IA" />
//   <AiGeneratedBadge inline />                 → variante discrète en fil de texte
export function AiGeneratedBadge({ label = 'Généré par IA', inline = false, className }) {
  // Le titre porte la nuance qui compte juridiquement : l'IA propose, un humain
  // décide. Il ne s'agit pas d'un avertissement mais d'une information.
  const title = 'Contenu produit par un modèle de langage, susceptible d’erreurs. ' +
    'Il ne constitue pas une décision : la sélection reste prise par un opérateur humain.'

  if (inline) {
    return (
      <span title={title}
            className={clsx('inline-flex items-center gap-1 text-[10px] align-middle', className)}
            style={{ color: 'var(--text-faint)' }}>
        <Sparkles size={9} className="shrink-0" /> {label}
      </span>
    )
  }
  return (
    <span title={title}
          className={clsx('badge border text-[10px] bg-violet-500/10 text-violet-300 border-violet-500/20', className)}>
      <Sparkles size={9} /> {label}
    </span>
  )
}
