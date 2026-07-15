// Disponibilité STRUCTURÉE d'un consultant (miroir de l'enum backend).
// En plus de la note libre `availability`, deux champs requêtables :
//   availability_status ∈ {available, soon, on_mission, unknown}
//   available_from      : date de libération (surtout pour soon / on_mission).

export const AVAILABILITY_STATUS = {
  available:  { label: 'Disponible',      tone: 'emerald' },
  soon:       { label: 'Bientôt dispo',   tone: 'amber' },
  on_mission: { label: 'En mission',      tone: 'slate' },
  unknown:    { label: 'Non renseigné',   tone: 'slate' },
}

export const AVAILABILITY_OPTIONS = [
  { value: 'available',  label: 'Disponible' },
  { value: 'soon',       label: 'Bientôt disponible' },
  { value: 'on_mission', label: 'En mission' },
  { value: 'unknown',    label: 'Non renseigné' },
]

// Classes Tailwind par tonalité (badge). Clair + sombre gérés par les couleurs.
export const AVAILABILITY_TONE = {
  emerald: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/25',
  amber:   'text-amber-400 bg-amber-500/10 border-amber-500/25',
  slate:   'text-slate-400 bg-slate-500/10 border-slate-500/20',
}

const _fmt = (d) => {
  if (!d) return null
  const dt = new Date(d)
  return Number.isNaN(dt.getTime()) ? null : dt.toLocaleDateString('fr-FR')
}

// Libellé compact : « Disponible » · « Dispo au 12/03/2026 » · « En mission → 12/03/2026 ».
export function availabilityLabel(status, availableFrom) {
  const meta = AVAILABILITY_STATUS[status]
  if (!meta) return status || null  // statut hors-enum : on affiche au moins le brut
  const d = _fmt(availableFrom)
  if (status === 'soon' && d) return `Dispo au ${d}`
  if (status === 'on_mission' && d) return `En mission → ${d}`
  return meta.label
}

export function availabilityTone(status) {
  return AVAILABILITY_TONE[AVAILABILITY_STATUS[status]?.tone] || AVAILABILITY_TONE.slate
}
