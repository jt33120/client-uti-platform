import { useState } from 'react'
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, ResponsiveContainer } from 'recharts'
import { ChevronDown, SlidersHorizontal, RotateCcw } from 'lucide-react'
import StarRating from './StarRating'

// Les 6 axes du scoring, libellés non-techs + libellé court pour le radar.
// « Points forts du CV » et « Éléments différenciants » sont notés par l'IA
// (2e avis), placés sous Contexte/domaine. Le TJM peut être mis à 0★ (exclu).
export const CRITERIA = [
  { key: 'competences', label: 'Compétences techniques', short: 'Compét.' },
  { key: 'seniorite', label: 'Séniorité', short: 'Séniorité' },
  { key: 'contexte', label: 'Contexte / domaine', short: 'Contexte' },
  { key: 'points_forts_cv', label: 'Points forts du CV', short: 'Atouts', ai: true },
  { key: 'elements_differenciants', label: 'Éléments différenciants', short: 'Différ.', ai: true },
  { key: 'tjm', label: 'Compatibilité TJM', short: 'TJM' },
]

// Étoiles par défaut (grille v2.2 — miroir de services.scoring.DEFAULT_STARS).
// Un critère à 0★ est exclu du score.
// Les axes IA (points forts / différenciation) sont à 1★ : sans signal
// déterministe, un poids trop élevé gelait ~30 % de la note au neutre.
// Le TJM est à 0★ : le budget est cadré sur l'AO (budget max), il ne sert plus
// à noter les candidats. Reste remontable à la main si un AO l'exige.
export const DEFAULT_STARS = {
  competences: 4, seniorite: 2, contexte: 2,
  points_forts_cv: 1, elements_differenciants: 1, tjm: 0,
}

// Miroir exact de services.scoring.stars_to_weights (somme garantie = 100).
// Un critère à 0★ reçoit un poids nul et sort de la répartition.
export function starsToWeights(stars) {
  const s = {}
  CRITERIA.forEach(({ key }) => {
    const v = parseInt(stars?.[key], 10)
    s[key] = Number.isFinite(v) ? Math.max(0, Math.min(5, v)) : (DEFAULT_STARS[key] ?? 2)
  })
  let active = CRITERIA.filter(({ key }) => s[key] > 0).map(c => c.key)
  if (active.length === 0) { active = CRITERIA.map(c => c.key); CRITERIA.forEach(({ key }) => { s[key] = 1 }) }
  const total = active.reduce((a, k) => a + s[k], 0) || 1
  const raw = {}
  const floor = {}
  CRITERIA.forEach(({ key }) => {
    raw[key] = active.includes(key) ? (s[key] / total) * 100 : 0
    floor[key] = Math.floor(raw[key])
  })
  const remainder = 100 - CRITERIA.reduce((a, { key }) => a + floor[key], 0)
  const order = [...active].sort((a, b) => (raw[b] - floor[b]) - (raw[a] - floor[a]))
  for (let i = 0; i < remainder; i++) floor[order[i]] += 1
  return floor
}

function PriorityRadar({ weights }) {
  // Un critère à 0★ ne doit pas laisser d'axe fantôme : afficher « TJM » à 0
  // juste à côté de « Non pris en compte (0★) » se contredit à l'écran.
  // Même règle que `activeCats` sur le radar candidat (AODetailPage).
  // Repli sur tous les axes si la grille est entièrement à zéro : un radar sans
  // axe ne se dessine pas, et starsToWeights garantit déjà ce cas indésirable.
  const shown = CRITERIA.filter(({ key }) => (weights[key] ?? 0) > 0)
  const axes = shown.length ? shown : CRITERIA
  const data = axes.map(({ key, short }) => ({ axis: short, value: weights[key] }))
  return (
    <ResponsiveContainer width="100%" height={200}>
      <RadarChart data={data} outerRadius="68%">
        <PolarGrid stroke="var(--border)" />
        <PolarAngleAxis dataKey="axis" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
        <Radar dataKey="value" stroke="var(--accent-text)" fill="var(--accent-text)" fillOpacity={0.22} />
      </RadarChart>
    </ResponsiveContainer>
  )
}

function NumberField({ label, hint, value, onChange, min, max }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className="text-[13px]" style={{ color: 'var(--text)' }}>{label}</div>
        {hint && <div className="text-[11px]" style={{ color: 'var(--text-faint)' }}>{hint}</div>}
      </div>
      <input
        type="number" min={min} max={max} value={value}
        onChange={(e) => onChange(parseInt(e.target.value || '0', 10))}
        className="input w-20 text-right"
      />
    </div>
  )
}

/**
 * Bloc « Priorités de matching » réutilisable (réglages globaux + par-AO).
 * `stars`/`onStarsChange` pilotent l'importance ; les seuils (avancés) sont
 * optionnels via `thresholds`/`onThresholdsChange`.
 */
export default function ScoringPriorities({ stars, onStarsChange, thresholds, onThresholdsChange }) {
  const [advOpen, setAdvOpen] = useState(false)
  const weights = starsToWeights(stars)
  const setStar = (key) => (n) => onStarsChange({ ...stars, [key]: n })
  const fortError = thresholds && thresholds.reco_fort_min <= thresholds.reco_moyen_min

  // « Déjà aux défauts ? » — pour désactiver le bouton de réinitialisation.
  const isDefault = CRITERIA.every(({ key }) => {
    const v = parseInt(stars?.[key], 10)
    const cur = Number.isFinite(v) ? v : DEFAULT_STARS[key]
    return cur === DEFAULT_STARS[key]
  })

  return (
    <div className="space-y-5">
      {onStarsChange && (
        <div className="flex items-center justify-end -mb-1">
          <button
            type="button"
            onClick={() => onStarsChange({ ...DEFAULT_STARS })}
            disabled={isDefault}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium transition-opacity disabled:opacity-40 disabled:cursor-default"
            style={{ color: 'var(--text-muted)' }}
            title="Rétablir les poids par défaut de la grille"
          >
            <RotateCcw size={12} />
            Poids par défaut
          </button>
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4 items-center">
        <div className="space-y-3.5">
          {CRITERIA.map(({ key, label, ai }) => (
            <div key={key} className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[13px] flex items-center gap-1.5" style={{ color: 'var(--text)' }}>
                  {label}
                  {ai && (
                    <span className="text-[9px] font-semibold px-1 py-px rounded"
                          style={{ color: 'var(--accent-text)', background: 'var(--surface-2)' }}
                          title="Critère évalué par l'IA (2e avis)">IA</span>
                  )}
                </div>
                <div className="text-[11px] tabular" style={{ color: 'var(--text-faint)' }}>
                  {weights[key] > 0 ? `${weights[key]} % du score` : 'Non pris en compte (0★)'}
                </div>
              </div>
              <StarRating value={stars?.[key] ?? 0} onChange={onStarsChange ? setStar(key) : undefined} />
            </div>
          ))}
        </div>
        <div className="rounded-lg" style={{ background: 'var(--surface-2)' }}>
          <PriorityRadar weights={weights} />
        </div>
      </div>

      {thresholds && onThresholdsChange && (
        <div className="border-t pt-3" style={{ borderColor: 'var(--border)' }}>
          <button
            type="button"
            onClick={() => setAdvOpen((o) => !o)}
            className="flex items-center gap-1.5 text-[12px] font-medium"
            style={{ color: 'var(--text-muted)' }}
          >
            <SlidersHorizontal size={13} />
            Réglages avancés (seuils &amp; séniorité)
            <ChevronDown size={13} className="transition-transform" style={{ transform: advOpen ? 'rotate(180deg)' : 'none' }} />
          </button>
          {advOpen && (
            <div className="mt-3 space-y-3">
              <NumberField
                label="Séniorité cible" hint="années d'XP pour le score séniorité maximal"
                value={thresholds.seniority_full_years} min={1} max={40}
                onChange={(v) => onThresholdsChange({ ...thresholds, seniority_full_years: v })}
              />
              <NumberField
                label="Seuil recommandation FORT" hint="score ≥ pour une reco forte"
                value={thresholds.reco_fort_min} min={0} max={100}
                onChange={(v) => onThresholdsChange({ ...thresholds, reco_fort_min: v })}
              />
              <NumberField
                label="Seuil recommandation MOYEN" hint="score ≥ pour une reco moyenne"
                value={thresholds.reco_moyen_min} min={0} max={100}
                onChange={(v) => onThresholdsChange({ ...thresholds, reco_moyen_min: v })}
              />
              {fortError && (
                <p className="text-[12px]" style={{ color: 'var(--danger)' }}>
                  Le seuil FORT doit être strictement supérieur au seuil MOYEN.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
