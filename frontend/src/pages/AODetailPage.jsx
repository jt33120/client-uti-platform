import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useConfirm } from '../contexts/ConfirmContext'
import {
  ArrowLeft, Zap, Euro, MapPin, Clock, Users, CheckCircle,
  AlertCircle, TrendingUp, Award, ChevronDown, ChevronUp, ChevronLeft, ChevronRight,
  Loader2, FileText, Trash2, RotateCcw, Building2, Plus,
  Upload, X, UserCircle2, Briefcase, Calendar, Pencil,
  CalendarClock, AlertTriangle, BarChart3, Sparkles,
  UploadCloud, Download, Target, Hash, Send, Bell, Mail, MessageSquareWarning, Languages, GripVertical, HelpCircle
} from 'lucide-react'
import ScoringPriorities, { DEFAULT_STARS } from '../components/ScoringPriorities'
import HarmonizedCvModal from '../components/HarmonizedCvModal'
import OnboardingTour from '../components/OnboardingTour'
import { Radar, RadarChart, PolarGrid, PolarAngleAxis, ResponsiveContainer } from 'recharts'

// Tutoriel « lire & utiliser le matching CV » — lancé par le bouton « ? » de la
// page. Chaque étape éclaire un élément [data-tour=…] ; une étape dont la cible
// est absente (carte réduite, aucun résultat) est ignorée automatiquement.
const MATCH_TOUR_STEPS = [
  { title: 'Lire le matching CV',
    text: "En 6 écrans : comment l’IA classe les candidats, comment lire un profil, et comment reprendre la main (classement, contact, désaccord). L’IA propose — vous décidez." },
  { selector: '[data-tour="match-selector"]', title: 'Le classement des candidats',
    text: "Une vignette par candidat (trigramme + score), du meilleur au moins bon. Cliquez-en une pour afficher son détail ; la barre reste visible quand vous faites défiler. Glissez une vignette pour imposer VOTRE ordre — il prime sur celui de l'IA." },
  { selector: '[data-tour="match-score"]', title: 'Le score d’adéquation',
    text: "Note sur 100 combinant la grille déterministe (auditable) et l’avis de l’IA. Vert = fort, orange = moyen, rouge = faible. Le libellé (Fort / À considérer / Faible) résume la reco." },
  { selector: '[data-tour="match-ia"]', title: 'L’analyse IA',
    text: "En 2-3 phrases : qui contacter et pourquoi, points forts et réserves — en citant des éléments concrets du CV. C’est un 2ᵉ avis : la grille reste l’ancre." },
  { selector: '[data-tour="match-criteria"]', title: 'Le détail par critère',
    text: "Chaque critère (compétences, séniorité, contexte, points forts, éléments différenciants, TJM) est noté sur son barème, avec une justification citée du CV. Les langues détectées et la langue exigée par l’AO sont contrôlées ici." },
  { selector: '[data-tour="match-contact"]', title: 'Contacter le partenaire',
    text: "Juste à côté du nom : ouvre un brouillon d’e-mail pré-rempli vers le partenaire porteur. Le suivi (contacté / proposé au client) est mémorisé." },
  { selector: '[data-tour="match-decision"]', title: 'Votre décision (et le désaccord)',
    text: "Retenir, écarter, ou SIGNALER UN DÉSACCORD avec un commentaire. Ce commentaire est réinjecté dans l’analyse : au prochain « Relancer », l’IA réévalue le profil en tenant compte de votre retour." },
]

// Parse date-only strings ("YYYY-MM-DD") as *local* dates to avoid the UTC
// off-by-one; full timestamps fall back to native parsing.
const parseDateLocal = (iso) => {
  if (!iso) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(iso)
}

const formatDate = (iso) => {
  if (!iso) return null
  return new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' }).format(parseDateLocal(iso))
}

// Days between today (midnight) and the deadline date. Negative = past.
const daysUntil = (iso) => {
  if (!iso) return null
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const d = parseDateLocal(iso); d.setHours(0, 0, 0, 0)
  return Math.round((d - today) / 86400000)
}
import clsx from 'clsx'

// Cache de session des résultats de matching, par AO. Évite de relancer un
// scoring LLM (15-30 s) à CHAQUE visite de la page quand la persistance serveur
// est indisponible (table matchings non peuplée). TTL court ; invalidé dès qu'un
// CV est ajouté ou l'AO modifié.
const MATCH_CACHE_TTL = 30 * 60 * 1000
const matchCacheKey = (aoId) => `uti_match_${aoId}`
const readMatchCache = (aoId) => {
  try {
    const v = JSON.parse(sessionStorage.getItem(matchCacheKey(aoId)) || 'null')
    if (!v?.results?.length || Date.now() - v.ts > MATCH_CACHE_TTL) return null
    return v  // { ts, results, allScores }
  } catch { return null }
}
const writeMatchCache = (aoId, results, allScores) => {
  try {
    if (results?.length) {
      sessionStorage.setItem(matchCacheKey(aoId), JSON.stringify({ ts: Date.now(), results, allScores: allScores || null }))
    }
  } catch { /* quota indisponible : non bloquant */ }
}
const clearMatchCache = (aoId) => { try { sessionStorage.removeItem(matchCacheKey(aoId)) } catch { /* noop */ } }

// ─── Score visuals (same as before) ─────────────────────────────
function ScoreRing({ score, size = 80 }) {
  const radius = (size / 2) - 8
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (score / 100) * circumference
  const color = score >= 75 ? '#10b981' : score >= 50 ? '#3b82f6' : score >= 30 ? '#f59e0b' : '#ef4444'

  return (
    <svg width={size} height={size} className="shrink-0 -rotate-90">
      <circle cx={size/2} cy={size/2} r={radius} className="fill-none stroke-white/5" strokeWidth={6} />
      <circle cx={size/2} cy={size/2} r={radius} fill="none" stroke={color} strokeWidth={6}
        strokeLinecap="round" strokeDasharray={circumference} strokeDashoffset={offset}
        style={{ transition: 'stroke-dashoffset 1s ease-out' }} />
      <text x={size/2} y={size/2} dominantBaseline="central" textAnchor="middle"
        fill={color}
        style={{ transform: `rotate(90deg)`, transformOrigin: `${size/2}px ${size/2}px`, fontSize: size < 70 ? '18px' : '22px', fontWeight: 800 }}>
        {score}
      </text>
    </svg>
  )
}

function RecoTag({ reco }) {
  const styles = {
    FORT: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    MOYEN: 'bg-brand-500/10 text-brand-400 border-brand-500/20',
    FAIBLE: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  }
  const labels = { FORT: '★ Recommandé', MOYEN: 'À considérer', FAIBLE: 'Peu adapté' }
  return (
    <span className={clsx('badge border text-xs', styles[reco] || styles.MOYEN)}>
      {labels[reco] || reco}
    </span>
  )
}

function BreakdownBar({ label, value, max }) {
  const pct = Math.min((value / max) * 100, 100)
  return (
    <div>
      <div className="flex justify-between text-xs text-slate-400 mb-1">
        <span>{label}</span>
        <span className="text-white font-medium">{value}/{max}</span>
      </div>
      <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
        <div className="h-full bg-brand-500 rounded-full transition-all duration-700" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// Catégories de score : libellé, clé du breakdown déterministe, clé côté LLM, poids par défaut.
// v2 : deux axes qualitatifs notés par l'IA (points forts / différenciation),
// placés sous Contexte. Le TJM peut être désactivé (poids 0) côté réglages.
const SCORE_CATS = [
  { label: 'Compétences', short: 'Compét.', det: 'competences_techniques', llm: 'competences', wKey: 'w_competences', dflt: 31 },
  { label: 'Séniorité', short: 'Séniorité', det: 'seniorite', llm: 'seniorite', wKey: 'w_seniorite', dflt: 15 },
  { label: 'Contexte / domaine', short: 'Contexte', det: 'contexte_domaine', llm: 'contexte', wKey: 'w_contexte', dflt: 15 },
  { label: 'Points forts du CV', short: 'Atouts', det: 'points_forts_cv', llm: 'points_forts_cv', wKey: 'w_points_forts_cv', dflt: 15 },
  { label: 'Éléments différenciants', short: 'Différ.', det: 'elements_differenciants', llm: 'elements_differenciants', wKey: 'w_elements_differenciants', dflt: 16 },
  { label: 'Compatibilité TJM', short: 'TJM', det: 'compatibilite_tjm', llm: 'tjm', wKey: 'w_tjm', dflt: 8 },
]

// Axes réellement pondérés (masque un critère désactivé à 0★ côté réglages).
const activeCats = (weights) =>
  SCORE_CATS.filter(c => !weights || (weights[c.wKey] ?? c.dflt) > 0)

// Normalise un nom de langue pour comparaison (minuscule, sans accents).
const _normLang = (s) => (s || '').toString().normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim()

// La langue imposée par l'AO est-elle couverte par les langues détectées au CV ?
// Renvoie null si l'AO n'exige rien ; sinon { matched, level }.
function requiredLangStatus(langueRequise, langues) {
  const req = _normLang(langueRequise)
  if (!req) return null
  const reqName = req.split(/[\s,;:/()]+/).find(w => w.length > 2) || req
  const list = Array.isArray(langues) ? langues : []
  const hit = list.find(l => {
    const name = _normLang(l?.langue)
    return name && (name.includes(reqName) || reqName.includes(name))
  })
  return { matched: !!hit, level: hit?.niveau || null }
}

// Rendu compact d'une langue détectée : « anglais (courant) ».
const fmtLang = (l) => l?.niveau ? `${l.langue} (${l.niveau})` : l?.langue

// Radar — score hybride par critère (une seule série, normalisée en %).
// Le score global n'est PAS répété au centre : il est affiché dans l'anneau
// (ScoreRing) en tête de carte.
function ScoreRadar({ breakdown, hybridBreakdown, weights }) {
  const data = activeCats(weights).map(c => {
    const max = (weights && weights[c.wKey]) || c.dflt || 1
    // Priorité : hybrid > det (si pas encore de résultat hybride stocké)
    const val = hybridBreakdown?.[c.det] ?? breakdown?.[c.det] ?? 0
    return { axis: c.label, score: Math.round((val / max) * 100) }
  })
  return (
    <div className="relative">
      <ResponsiveContainer width="100%" height={220}>
        <RadarChart data={data} outerRadius="72%">
          <PolarGrid stroke="rgba(120,120,140,0.20)" />
          <PolarAngleAxis dataKey="axis" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
          <Radar dataKey="score" stroke="#6366f1" fill="#6366f1" fillOpacity={0.30} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}

// Décision humaine sur un résultat (AI Act Art. 14 — supervision & override).
// Le staff retient/écarte un profil ; justification obligatoire pour un ajustement.
function DecisionBar({ aoId, result, rank, initial }) {
  // Décision déjà enregistrée (relue au chargement) → le badge survit au refresh.
  const [recorded, setRecorded] = useState(initial?.decision || null)
  const [overrideMode, setOverrideMode] = useState(false)
  const [justification, setJustification] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const labels = { retained: 'Retenu', rejected: 'Écarté', overridden: 'Désaccord signalé' }

  const record = async (decision, just = null) => {
    if (decision === 'overridden' && !just?.trim()) {
      setError('Une justification est requise pour ajuster le classement.')
      return
    }
    setLoading(true); setError('')
    try {
      await api.post('/decisions', {
        ao_id: aoId,
        submission_id: result.submission_id || null,
        consultant_id: result.consultant_id || null,
        ai_rank: rank,
        ai_score: result.score_total,
        decision,
        justification: just,
      })
      setRecorded(decision)
      setOverrideMode(false)
    } catch (e) {
      setError(e.response?.data?.detail || 'Erreur lors de l’enregistrement')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="border-t border-white/5 pt-3 mt-1" data-tour="match-decision">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide">
          Décision humaine
        </span>
        {recorded ? (
          <span className="badge bg-brand-500/10 text-brand-300 border border-brand-500/20 text-[10px] inline-flex items-center gap-1">
            <CheckCircle size={10} /> {labels[recorded]}
            <button onClick={() => { setRecorded(null); setOverrideMode(false) }} className="ml-1 text-slate-500 hover:text-slate-300">modifier</button>
          </span>
        ) : (
          <div className="flex items-center gap-1.5">
            <button onClick={() => record('retained')} disabled={loading}
              className="btn-ghost text-[11px] px-2 py-1 gap-1 text-emerald-400 hover:text-emerald-300">
              <CheckCircle size={11} /> Retenir
            </button>
            <button onClick={() => record('rejected')} disabled={loading}
              className="btn-ghost text-[11px] px-2 py-1 gap-1 text-slate-400 hover:text-red-400">
              <X size={11} /> Écarter
            </button>
            <button onClick={() => setOverrideMode(o => !o)} disabled={loading}
              className="btn-ghost text-[11px] px-2 py-1 gap-1 text-amber-400 hover:text-amber-300">
              <MessageSquareWarning size={11} /> Signaler un désaccord
            </button>
          </div>
        )}
      </div>

      {recorded === 'overridden' && (
        <p className="text-[11px] text-amber-300/90 mt-2 flex items-start gap-1.5">
          <MessageSquareWarning size={12} className="shrink-0 mt-0.5" />
          Désaccord enregistré. Relancez l'analyse (bouton « Relancer » en haut) : l'IA réévaluera ce profil en tenant compte de votre retour.
        </p>
      )}

      {overrideMode && !recorded && (
        <div className="mt-2 space-y-2">
          <textarea
            className="input text-xs min-h-[56px] resize-y"
            placeholder="Votre désaccord / correction (ex. « sous-évalué : a mené une migration Teradata→Snowflake en 2023 »). Ce texte est réinjecté dans l'analyse IA au prochain lancement."
            value={justification}
            onChange={e => setJustification(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <button onClick={() => setOverrideMode(false)} className="btn-ghost text-[11px] px-2 py-1">Annuler</button>
            <button onClick={() => record('overridden', justification)} disabled={loading}
              className="btn-primary text-[11px] px-3 py-1">
              {loading ? <Loader2 size={11} className="animate-spin" /> : 'Enregistrer'}
            </button>
          </div>
        </div>
      )}

      {error && <p className="text-[11px] text-red-400 mt-1.5">{error}</p>}
    </div>
  )
}

// Libellé du bouton selon la cible de contact (partenaire vs consultant).
function contactLabel(kind) {
  if (kind === 'consultant') return 'Contacter le consultant'
  if (kind === 'owner') return 'Contacter le référent'
  return 'Contacter le partenaire'
}

// Brouillon d'email pré-rempli (le commercial l'édite/envoie). Le texte s'adapte
// selon qu'on écrit au partenaire (confirmer dispo du consultant) ou au
// consultant lui-même (lui proposer la mission).
function buildMailto(result, ao) {
  const to = result.partner_email || ''
  const cli = ao?.clients?.name ? ` (client ${ao.clients.name})` : ''
  const ref = ao?.reference ? `, réf. ${ao.reference}` : ''
  const hi = `Bonjour${result.partner_name ? ' ' + result.partner_name : ''},`
  let subject, lines
  if (result.contact_kind === 'consultant') {
    subject = `Proposition de mission : ${ao?.title || "appel d'offres"}${ref}`
    lines = [
      hi, '',
      `Nous avons une mission « ${ao?.title || ''} »${cli} qui pourrait correspondre à votre profil.`,
      '',
      `Seriez-vous disponible et intéressé(e) ? Le cas échéant, pouvez-vous nous confirmer vos disponibilités et votre TJM ?`,
      '', 'Merci d’avance,',
    ]
  } else {
    subject = `Proposition de consultant : ${ao?.title || "appel d'offres"}${ref}`
    lines = [
      hi, '',
      `Nous souhaitons avancer sur le profil de ${result.consultant_name || 'votre consultant'} pour la mission « ${ao?.title || ''} »${cli}.`,
      '',
      `Pouvez-vous nous confirmer sa disponibilité ainsi que ses conditions (TJM, préavis), afin que nous formalisions la proposition au client ?`,
      '', 'Merci d’avance,',
    ]
  }
  return `mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(lines.join('\n'))}`
}

// Couleur du score selon les paliers (alignée sur ScoreRing).
function scoreColor(s) {
  return s >= 75 ? 'text-emerald-400' : s >= 50 ? 'text-brand-300' : s >= 30 ? 'text-amber-400' : 'text-red-400'
}
// Même palier, en hex (barres d'analyse).
function scoreHex(s) {
  return s >= 75 ? '#10b981' : s >= 50 ? '#3b82f6' : s >= 30 ? '#f59e0b' : '#ef4444'
}

// Nombre de profils qui « se détachent » par RUPTURE NATURELLE de la distribution :
// on part des profils ≥ plancher de qualité (50 = « Bon ») et on coupe à la
// première chute nette de score (écart ≥ 5 pts). Déterministe et auditable
// (AI Act) — alternative explicable à un mixture model. Borné [1, 10].
const RECO_FLOOR = 50
const RECO_MIN_GAP = 5
function recommendedCut(scoresDesc) {
  const eligible = scoresDesc.filter(s => s >= RECO_FLOOR)
  let k = 1
  if (eligible.length >= 2) {
    let bestGap = -1, cut = eligible.length
    for (let i = 1; i < eligible.length; i++) {
      const g = eligible[i - 1] - eligible[i]
      if (g > bestGap) { bestGap = g; cut = i }
    }
    k = bestGap >= RECO_MIN_GAP ? cut : eligible.length
  }
  return Math.max(1, Math.min(k, 10))
}

// Analyses de score sous le classement : stats clés, histogramme, sélection
// statistique (rupture naturelle) et classement complet de TOUS les candidats.
function ScoreAnalytics({ all, isAdmin }) {
  const rows = (all || [])
    .filter(a => a && a.score != null)
    .map(a => ({ ...a, score: Math.round(a.score) }))
    .sort((a, b) => b.score - a.score)
  if (rows.length < 2) return null

  const scores = rows.map(r => r.score)
  const best = scores[0]
  const avg = Math.round(scores.reduce((s, v) => s + v, 0) / scores.length)
  const mid = Math.floor(scores.length / 2)
  const median = scores.length % 2 ? scores[mid] : Math.round((scores[mid - 1] + scores[mid]) / 2)
  const gap = best - scores[1]

  // Sélection statistique dynamique.
  const recoK = recommendedCut(scores)
  const cutoffScore = scores[recoK - 1]

  // Histogramme : bins de 10 points (0-9, 10-19, …, 90-100).
  const bins = Array.from({ length: 10 }, (_, i) => ({ lo: i * 10, count: 0 }))
  scores.forEach(s => { bins[Math.min(9, Math.floor(s / 10))].count++ })
  const maxC = Math.max(...bins.map(b => b.count), 1)

  const Stat = ({ label, value, color, sub }) => (
    <div className="rounded-lg p-3" style={{ background: 'var(--surface-2)' }}>
      <div className="text-[11px]" style={{ color: 'var(--text-faint)' }}>{label}</div>
      <div className="text-xl font-bold tabular" style={{ color }}>
        {value}{sub ? <span className="text-xs font-normal" style={{ color: 'var(--text-faint)' }}>{sub}</span> : null}
      </div>
    </div>
  )

  return (
    <div className="card p-4">
      <p className="text-xs font-semibold uppercase tracking-wide flex items-center gap-1.5 mb-4" style={{ color: 'var(--text-muted)' }}>
        <BarChart3 size={13} className="text-[var(--accent-text)]" /> Analyse des scores · {rows.length} candidats
      </p>

      {/* Sélection statistique (rupture naturelle) */}
      <div className="rounded-lg px-3 py-2 mb-4 flex items-start gap-2 text-[12px]"
           style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>
        <Sparkles size={14} className="shrink-0 mt-0.5" />
        <span>
          <strong>{recoK} profil{recoK > 1 ? 's' : ''}</strong> se détache{recoK > 1 ? 'nt' : ''} de la distribution
          {' '}(score ≥ {cutoffScore}, avant la 1ʳᵉ rupture nette). Les {Math.max(rows.length - recoK, 0)} autres sont en retrait.
        </span>
      </div>

      {/* Stats clés */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
        <Stat label="Meilleur" value={best} sub="/100" color={scoreHex(best)} />
        <Stat label="Moyenne" value={avg} sub="/100" color="var(--text)" />
        <Stat label="Médiane" value={median} sub="/100" color="var(--text)" />
        <div className="rounded-lg p-3" style={{ background: 'var(--surface-2)' }}>
          <div className="text-[11px]" style={{ color: 'var(--text-faint)' }}>Écart 1ᵉʳ / 2ᵉ</div>
          <div className="text-xl font-bold tabular" style={{ color: 'var(--text)' }}>+{gap}</div>
          <div className="text-[10px] mt-0.5" style={{ color: gap >= 10 ? '#10b981' : 'var(--text-faint)' }}>
            {gap >= 10 ? 'un profil se détache' : 'profils serrés'}
          </div>
        </div>
      </div>

      {/* Histogramme des scores */}
      <div className="mb-5">
        <div className="text-[11px] font-semibold uppercase tracking-wide mb-2" style={{ color: 'var(--text-faint)' }}>
          Histogramme des scores
        </div>
        <div className="relative">
          <div className="flex items-end gap-1" style={{ height: 96 }}>
            {bins.map(b => (
              <div key={b.lo} className="flex-1 flex flex-col items-center justify-end">
                {b.count > 0 && <span className="text-[9px] tabular mb-0.5" style={{ color: 'var(--text-faint)' }}>{b.count}</span>}
                <div className="w-full rounded-t" style={{ height: `${(b.count / maxC) * 100}%`, minHeight: b.count ? 3 : 0, background: scoreHex(b.lo + 5) }} />
              </div>
            ))}
          </div>
          <div className="flex gap-1 mt-1">
            {bins.map(b => <span key={b.lo} className="flex-1 text-center text-[9px]" style={{ color: 'var(--text-faint)' }}>{b.lo}</span>)}
          </div>
          {/* Seuil de recommandation (rupture) */}
          <div className="absolute top-0 bottom-5 pointer-events-none" style={{ left: `${cutoffScore}%`, borderLeft: '2px dashed var(--accent-text)' }} />
        </div>
        <div className="text-[10px] mt-1" style={{ color: 'var(--text-faint)' }}>
          La ligne pointillée marque le seuil de recommandation (score {cutoffScore}).
        </div>
      </div>

      {/* Classement complet */}
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wide mb-2" style={{ color: 'var(--text-faint)' }}>Classement complet</div>
        <div className="space-y-1">
          {rows.map((r, i) => (
            <div key={r.consultant_id || i}>
              <div className="flex items-center gap-2.5" style={{ opacity: i < recoK ? 1 : 0.55 }}>
                <span className="w-5 text-[11px] tabular text-right" style={{ color: i < recoK ? 'var(--accent-text)' : 'var(--text-faint)', fontWeight: i < recoK ? 700 : 400 }}>{i + 1}</span>
                <span className="w-28 sm:w-48 truncate text-[12px] font-medium" style={{ color: 'var(--text)' }}>
                  {isAdmin && r.submitter_name && (
                    <span style={{ color: 'var(--text-faint)' }}>{r.submitter_name} · </span>
                  )}
                  {r.consultant_name || '—'}
                </span>
                <div className="flex-1 h-2.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                  <div className="h-full rounded-full" style={{ width: `${Math.max(r.score, 2)}%`, background: scoreHex(r.score) }} />
                </div>
                <span className="w-9 text-right text-[12px] font-bold tabular" style={{ color: scoreHex(r.score) }}>{r.score}</span>
                {r.tjm != null && <span className="hidden sm:inline w-14 text-right text-[11px]" style={{ color: 'var(--text-faint)' }}>{r.tjm}€/j</span>}
              </div>
              {i === recoK - 1 && i < rows.length - 1 && (
                <div className="flex items-center gap-2 my-1.5">
                  <div className="flex-1 border-t border-dashed" style={{ borderColor: 'var(--accent-text)' }} />
                  <span className="text-[9px] uppercase tracking-wide" style={{ color: 'var(--accent-text)' }}>seuil de recommandation</span>
                  <div className="flex-1 border-t border-dashed" style={{ borderColor: 'var(--accent-text)' }} />
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function MatchCard({ result, rank, aoId, isAdmin, ao, onContact, expanded: expandedProp, onToggleExpand, decision }) {
  const [contactStatus, setContactStatus] = useState(result.contact_status || 'none')
  useEffect(() => { setContactStatus(result.contact_status || 'none') }, [result.contact_status])
  // Vue détaillée/réduite : contrôlée par le parent (carousel) si fourni, pour
  // conserver le mode choisi quand on change de profil ; sinon état local.
  const [expandedLocal, setExpandedLocal] = useState(rank === 1)
  const controlled = onToggleExpand != null
  const expanded = controlled ? expandedProp : expandedLocal
  const toggleExpand = controlled ? onToggleExpand : () => setExpandedLocal(p => !p)
  const bd = result.breakdown || {}
  const lbd = result.llm_breakdown || null
  const hbd = result.hybrid_breakdown || null
  const weights = result.weights || null
  const headlineScore = result.score_hybride ?? result.score_total
  // Reco cohérente avec le score affiché (hybride) — seuils par défaut 75 / 50.
  const reco = headlineScore >= 75 ? 'FORT' : headlineScore >= 50 ? 'MOYEN' : 'FAIBLE'
  const cats = activeCats(weights).map(c => ({
    key: c.det,
    label: c.label,
    max: (weights && weights[c.wKey]) || c.dflt,
    hybridVal: hbd?.[c.det] ?? bd[c.det] ?? 0,
    justif: lbd?.[c.llm]?.justification,
  }))

  return (
    <div className={clsx('card overflow-hidden transition-all duration-200', rank === 1 && 'border-emerald-500/30 bg-emerald-500/3')}>
      {rank === 1 && (
        <div className="bg-emerald-500/10 border-b border-emerald-500/20 px-4 py-1.5 flex items-center gap-1.5">
          <Award size={12} className="text-emerald-400" />
          <span className="text-xs text-emerald-400 font-semibold">Meilleur match</span>
        </div>
      )}

      <div className="flex items-center gap-4 p-4 cursor-pointer hover:bg-white/2 transition-colors"
           onClick={toggleExpand}>
        <div className="w-7 h-7 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-xs font-bold text-slate-400 shrink-0">
          {rank}
        </div>
        <span data-tour="match-score" className="inline-flex"><ScoreRing score={headlineScore} size={64} /></span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold text-white">{result.consultant_name}</h3>
            <RecoTag reco={reco} />
            {/* Contacter le partenaire — juste à côté du nom/trigramme (compact),
                plutôt qu'un bouton pleine largeur en bas de carte. */}
            {isAdmin && (
              <a
                data-tour="match-contact"
                href={buildMailto(result, ao)}
                onClick={(e) => { e.stopPropagation(); if (contactStatus === 'none') { setContactStatus('contacted'); onContact?.(result, 'contacted') } }}
                title={`${contactLabel(result.contact_kind)}${result.partner_name ? ` · ${result.partner_name}` : ''}`}
                className="btn-primary !h-7 !px-2.5 text-[11px] gap-1 shrink-0">
                <Mail size={12} /> Contacter
              </a>
            )}
            {contactStatus !== 'none' && (
              <span className={clsx('badge border text-[10px] inline-flex items-center gap-1',
                contactStatus === 'proposed'
                  ? 'bg-violet-500/10 text-violet-300 border-violet-500/20'
                  : 'bg-sky-500/10 text-sky-300 border-sky-500/20')}>
                <CheckCircle size={9} /> {contactStatus === 'proposed' ? 'Proposé' : 'Contacté'}
              </span>
            )}
            {result.employment_type && (
              <span className="badge bg-white/5 text-slate-400 text-[10px]">
                {result.employment_type === 'salarie' ? 'Salarié' : 'Indépendant'}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-1">
            <p className="text-[11px] text-slate-500">Score d'adéquation</p>
            {result.consultant_tjm && (
              <span className="text-xs text-emerald-400 inline-flex items-center gap-0.5">
                <Euro size={10} />{result.consultant_tjm}€/j
              </span>
            )}
          </div>
        </div>
        <button className="text-slate-600 hover:text-slate-300 transition-colors shrink-0">
          {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>

      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-white/5 pt-4 animate-fade-in">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-center">
            {/* Radar : forme du profil — grille vs IA */}
            <div>
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Profil du candidat</p>
              <ScoreRadar breakdown={bd} hybridBreakdown={hbd} weights={weights} />
            </div>
            {/* Auto-justification rédigée par l'IA */}
            <div data-tour="match-ia">
              <p className="text-xs font-semibold text-violet-300 uppercase tracking-wide mb-2 flex items-center gap-1">
                <Sparkles size={11} /> Analyse IA
              </p>
              {result.llm_global
                ? <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-line">{result.llm_global}</p>
                : <p className="text-xs text-slate-500 italic">Avis IA indisponible pour ce profil : score déterministe seul (grille auditable).</p>}
            </div>
          </div>

          {/* Détail par critère : barre hybride + justification IA */}
          <div className="space-y-3" data-tour="match-criteria">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Détail par critère</p>
            {cats.map(c => (
              <div key={c.key}>
                <div className="flex justify-between text-xs text-slate-400 mb-1">
                  <span>{c.label}</span>
                  <span className="tabular text-white font-medium">{c.hybridVal}/{c.max}</span>
                </div>
                <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                  <div className="h-full bg-brand-500 rounded-full transition-all duration-700"
                       style={{ width: `${Math.min((c.hybridVal / c.max) * 100, 100)}%` }} />
                </div>
                {c.justif && <p className="text-[11px] text-slate-500 mt-1">{c.justif}</p>}
              </div>
            ))}

            {/* Langues détectées au CV + contrôle de la langue imposée par l'AO */}
            {(() => {
              const langues = Array.isArray(result.langues) ? result.langues : []
              const req = requiredLangStatus(ao?.langue_requise, langues)
              if (!langues.length && !req) return null
              return (
                <div className="pt-1">
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>Langues</span>
                    {req && (
                      req.matched
                        ? <span className="inline-flex items-center gap-1 text-emerald-400">
                            <CheckCircle size={11} /> {ao.langue_requise}{req.level ? ` · ${req.level}` : ''}
                          </span>
                        : <span className="inline-flex items-center gap-1 text-amber-400">
                            <AlertTriangle size={11} /> {ao.langue_requise} non détecté
                          </span>
                    )}
                  </div>
                  {langues.length
                    ? <p className="text-[11px] text-slate-300">{langues.map(fmtLang).filter(Boolean).join(' · ')}</p>
                    : <p className="text-[11px] text-slate-500 italic">Aucune langue détectée sur le CV.</p>}
                </div>
              )
            })()}
          </div>
          {result.cv_url && (
            <a href={result.cv_url} target="_blank" rel="noopener noreferrer"
               className="btn-ghost text-xs w-full justify-center">
              <FileText size={13} /> Consulter le CV soumis
            </a>
          )}

          {/* Suivi du contact (le bouton « Contacter » est désormais à côté du
              nom, en tête de carte). Ici : seulement le statut + actions de suivi. */}
          {isAdmin && (contactStatus !== 'none' || !result.partner_email) && (
            <div>
              {contactStatus !== 'none' ? (
                <div className="flex items-center justify-between gap-2 text-xs flex-wrap">
                  <span className="text-emerald-400 inline-flex items-center gap-1">
                    <CheckCircle size={12} />
                    {contactStatus === 'proposed' ? 'Proposé au client' : 'Partenaire contacté'}
                    {result.contacted_at && <span className="text-slate-500">· {formatDate(result.contacted_at)}</span>}
                  </span>
                  <div className="flex items-center gap-2">
                    {contactStatus === 'contacted' && (
                      <button onClick={() => { setContactStatus('proposed'); onContact?.(result, 'proposed') }}
                              className="btn-ghost text-[11px] px-2 py-1">
                        Marquer proposé au client
                      </button>
                    )}
                    <button onClick={() => { setContactStatus('none'); onContact?.(result, 'none') }}
                            className="text-[11px] text-slate-500 hover:text-slate-300">réinitialiser</button>
                  </div>
                </div>
              ) : (
                <p className="text-[11px] text-amber-400/80">
                  Aucun email de contact trouvé (ni partenaire, ni consultant) : le brouillon « Contacter » s'ouvrira sans destinataire (à compléter).
                </p>
              )}
            </div>
          )}

          {isAdmin && result.submission_id && (
            <DecisionBar aoId={aoId} result={result} rank={rank} initial={decision} />
          )}
        </div>
      )}
    </div>
  )
}

// ─── Carousel : une carte à la fois, navigable + réordonnable ─────
// Côté staff, l'opérateur a le dernier mot : il peut remonter/descendre un
// profil dans SON classement (persisté en base, prime sur le score IA).
function MatchCarousel({ results: incoming, aoId, isAdmin, ao, decisions }) {
  const [results, setResults] = useState(incoming)
  const [idx, setIdx] = useState(0)
  const [savingRank, setSavingRank] = useState(false)
  // Vue détaillée/réduite PARTAGÉE entre les profils : changer de carte conserve
  // le mode choisi. Détaillée par défaut.
  const [expanded, setExpanded] = useState(true)
  // Resynchronise quand un nouveau matching arrive (relance, nouvelle soumission).
  useEffect(() => { setResults(incoming); setIdx(0) }, [incoming])

  const result = results[idx]
  const prev = () => setIdx(i => Math.max(0, i - 1))
  const next = () => setIdx(i => Math.min(results.length - 1, i + 1))

  // Persiste le classement humain (prime sur l'IA). Best-effort.
  const persistOrder = async (reordered) => {
    setResults(reordered)
    if (!isAdmin) return
    setSavingRank(true)
    try {
      await api.post(`/matching/${aoId}/rank`, { order: reordered.map(r => r.consultant_id) })
    } catch { /* non bloquant : l'ordre local reste appliqué */ }
    finally { setSavingRank(false) }
  }

  // Drag & drop sur la barre de profils : l'opérateur impose son classement en
  // glissant une vignette à sa place (remplace les boutons monter/descendre).
  const [dragIdx, setDragIdx] = useState(null)
  const dropAt = (target) => {
    if (dragIdx === null || dragIdx === target) { setDragIdx(null); return }
    const reordered = results.slice()
    const [moved] = reordered.splice(dragIdx, 1)
    reordered.splice(target, 0, moved)
    setDragIdx(null)
    setIdx(reordered.indexOf(moved))
    persistOrder(reordered)
  }

  const onContact = async (r, status) => {
    if (!isAdmin) return
    try {
      await api.post(`/matching/${aoId}/contact`, {
        consultant_id: r.consultant_id,
        submission_id: r.submission_id || null,
        status,
      })
    } catch { /* best-effort */ }
  }

  return (
    <div>
      {/* Barre de sélection COLLANTE : on navigue entre candidats sans devoir
          remonter en haut après avoir scrollé le détail. */}
      <div data-tour="match-selector" className="sticky top-0 z-20 pt-2 pb-2.5 mb-1 bg-[var(--bg)] border-b border-white/10 shadow-[0_6px_10px_-8px_rgba(0,0,0,0.6)] rounded-b-lg">
        <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <TrendingUp size={12} className="text-brand-400" />
            <span>{isAdmin ? `Profil ${idx + 1}/${results.length}` : `Top ${results.length}`}</span>
            {savingRank && <span className="inline-flex items-center gap-1 text-brand-300"><Loader2 size={11} className="animate-spin" /> classement enregistré…</span>}
          </div>
          <div className="flex items-center gap-1.5">
            <button onClick={() => setExpanded(e => !e)} className="btn-ghost text-[11px] px-2 py-1 gap-1">
              {expanded ? <><ChevronUp size={12} /> Réduire</> : <><ChevronDown size={12} /> Détailler</>}
            </button>
            <button onClick={prev} disabled={idx === 0}
              className="btn-ghost p-1.5 disabled:opacity-30 disabled:cursor-not-allowed" title="Profil précédent">
              <ChevronLeft size={16} />
            </button>
            <button onClick={next} disabled={idx === results.length - 1}
              className="btn-ghost p-1.5 disabled:opacity-30 disabled:cursor-not-allowed" title="Profil suivant">
              <ChevronRight size={16} />
            </button>
          </div>
        </div>

        {/* Vignettes : trigramme + score. Cliquables (sélection) et, côté staff,
            glissables pour réordonner (classement humain, prime sur l'IA). */}
        <div className="flex gap-2 overflow-x-auto pb-0.5">
          {results.map((r, i) => {
            const sc = r.score_hybride ?? r.score_total
            const active = i === idx
            return (
              <button key={r.consultant_id || i} onClick={() => setIdx(i)}
                draggable={isAdmin && results.length > 1}
                onDragStart={() => setDragIdx(i)}
                onDragOver={e => { if (dragIdx !== null) e.preventDefault() }}
                onDrop={() => dropAt(i)}
                onDragEnd={() => setDragIdx(null)}
                className={clsx('group relative flex-1 min-w-[104px] rounded-lg border px-3 py-2 text-left transition-all',
                  isAdmin && results.length > 1 && 'cursor-grab active:cursor-grabbing',
                  dragIdx === i && 'opacity-40',
                  active ? 'border-brand-400 bg-brand-500/10' : 'border-white/10 bg-white/3 hover:border-white/25')}>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] font-bold text-slate-500 inline-flex items-center gap-1">
                    {isAdmin && results.length > 1 && <GripVertical size={11} className="text-slate-600 group-hover:text-slate-400" />}
                    #{i + 1}
                  </span>
                  <span className={clsx('text-sm font-bold tabular', scoreColor(sc))}>{sc}</span>
                </div>
                <div className="text-sm font-semibold text-white truncate mt-0.5">{r.consultant_name}</div>
              </button>
            )
          })}
        </div>
        {isAdmin && results.length > 1 && (
          <p className="text-[10.5px] text-slate-500 mt-1.5 inline-flex items-center gap-1">
            <GripVertical size={10} className="text-slate-600" /> Glissez un profil pour imposer votre classement (il prime sur l'IA).
          </p>
        )}
      </div>

      <div key={`${result?.consultant_id || idx}-${idx}`} className="animate-fade-in mt-3">
        <MatchCard result={result} rank={idx + 1} aoId={aoId} isAdmin={isAdmin} ao={ao}
          onContact={onContact} expanded={expanded} onToggleExpand={() => setExpanded(e => !e)}
          decision={decisions?.[result?.consultant_id]} />
      </div>
    </div>
  )
}

// ─── Submission modal (partner submits a CV to this AO) ─────────
function SubmitModal({ aoId, vivier, onClose, onSubmitted, prefill, clientName }) {
  const fileRef = useRef(null)
  const [mode, setMode] = useState(prefill ? 'new' : (vivier.length > 0 ? 'existing' : 'new'))
  const [consultantId, setConsultantId] = useState(vivier[0]?.id || '')
  const [form, setForm] = useState({
    name: '', skills: '', tjm: '', experience_years: '',
    employment_type: 'independant', availability: '',
    ...(prefill || {}),
  })
  const [cvFile, setCvFile] = useState(null)
  const [aiAck, setAiAck] = useState(false)
  const [consent, setConsent] = useState(false)
  const [workedAtClient, setWorkedAtClient] = useState(false)
  const [exitDate, setExitDate] = useState('')
  const [pointsForts, setPointsForts] = useState('')
  const [elementsDiff, setElementsDiff] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleFile = (file) => {
    const ext = file?.name?.split('.').pop()?.toLowerCase()
    if (file && ['pdf', 'docx', 'xlsx'].includes(ext)) {
      setCvFile(file)
      setError('')
    } else {
      setError('Seuls les fichiers PDF, Word (.docx) et Excel (.xlsx) sont acceptés')
    }
  }

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    // En mode « vivier », le CV est facultatif : le backend réutilise celui
    // déjà présent au vivier pour ce consultant.
    if (!cvFile && mode !== 'existing') { setError('Veuillez joindre un CV (PDF, Word ou Excel)'); return }
    if (!consent) { setError('Vous devez accepter la notice de confidentialité (RGPD)'); return }
    if (!pointsForts.trim() || !elementsDiff.trim()) {
      setError('Merci de renseigner les points forts et les éléments différenciants du CV.'); return
    }
    setLoading(true)
    try {
      const fd = new FormData()
      fd.append('ao_id', aoId)
      if (cvFile) fd.append('cv_file', cvFile)
      fd.append('consent', 'true')
      if (mode === 'existing') {
        if (!consultantId) { setError('Sélectionnez un consultant'); setLoading(false); return }
        fd.append('consultant_id', consultantId)
      } else {
        fd.append('name', form.name)
        fd.append('skills', form.skills)
        if (form.tjm) fd.append('tjm', form.tjm)
        if (form.experience_years) fd.append('experience_years', form.experience_years)
        if (form.employment_type) fd.append('employment_type', form.employment_type)
        if (form.availability) fd.append('availability', form.availability)
      }
      fd.append('worked_at_client', workedAtClient ? 'true' : 'false')
      if (workedAtClient && exitDate) fd.append('worked_at_client_exit_date', exitDate)
      if (pointsForts.trim()) fd.append('points_forts', pointsForts)
      if (elementsDiff.trim()) fd.append('elements_differenciants', elementsDiff)
      await api.post('/submissions', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
      onSubmitted()
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur lors de la soumission')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Proposer un consultant</h2>
          <button onClick={onClose} className="text-slate-500 hover:text-white"><X size={18} /></button>
        </div>

        {vivier.length > 0 && (
          <div className="flex gap-1 bg-white/5 rounded-lg p-1 mb-4">
            <button type="button" onClick={() => setMode('existing')}
              className={clsx('flex-1 px-3 py-1.5 text-xs rounded-md font-medium transition-all',
                mode === 'existing' ? 'seg-active' : 'text-slate-400 hover:text-slate-200')}>
              Depuis le vivier
            </button>
            <button type="button" onClick={() => setMode('new')}
              className={clsx('flex-1 px-3 py-1.5 text-xs rounded-md font-medium transition-all',
                mode === 'new' ? 'seg-active' : 'text-slate-400 hover:text-slate-200')}>
              Nouveau consultant
            </button>
          </div>
        )}

        <form onSubmit={submit} className="space-y-4">
          {mode === 'existing' ? (
            <div>
              <label className="label">Consultant *</label>
              <div className="relative">
                <select className="input appearance-none pr-9" value={consultantId}
                        onChange={e => setConsultantId(e.target.value)}>
                  {vivier.map(c => (
                    <option key={c.id} value={c.id} className="bg-navy-900">
                      {c.name}{c.tjm ? ` · ${c.tjm}€/j` : ''}{c.employment_type ? ` · ${c.employment_type === 'salarie' ? 'Salarié' : 'Indépendant'}` : ''}
                    </option>
                  ))}
                </select>
                <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
              </div>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className="label">Nom complet *</label>
                  <input className="input" required value={form.name}
                         onChange={e => setForm(p => ({ ...p, name: e.target.value }))} />
                </div>
                <div className="col-span-2">
                  <label className="label">Compétences clés *</label>
                  <input className="input" required placeholder="Python, React, AWS..."
                         value={form.skills}
                         onChange={e => setForm(p => ({ ...p, skills: e.target.value }))} />
                </div>
                <div>
                  <label className="label">TJM (€/j)</label>
                  <input className="input" type="number" min="0"
                         value={form.tjm} onChange={e => setForm(p => ({ ...p, tjm: e.target.value }))} />
                </div>
                <div>
                  <label className="label">Expérience (ans)</label>
                  <input className="input" type="number" min="0" max="50"
                         value={form.experience_years}
                         onChange={e => setForm(p => ({ ...p, experience_years: e.target.value }))} />
                </div>
                <div className="col-span-2">
                  <label className="label">Statut *</label>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { v: 'independant', l: 'Indépendant' },
                      { v: 'salarie', l: 'Salarié' },
                    ].map(o => (
                      <button key={o.v} type="button"
                        onClick={() => setForm(p => ({ ...p, employment_type: o.v }))}
                        className={clsx(
                          'px-3 py-2 text-xs rounded-lg border font-medium transition-all',
                          form.employment_type === o.v
                            ? 'bg-brand-600/20 border-brand-500/40 text-brand-300'
                            : 'bg-white/5 border-white/10 text-slate-400 hover:text-slate-200'
                        )}>
                        {o.l}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </>
          )}

          {/* Historique d'intervention chez le client (demande Sullyvan) */}
          <div className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
            <label className="flex items-start gap-2 cursor-pointer">
              <input type="checkbox" checked={workedAtClient}
                onChange={e => setWorkedAtClient(e.target.checked)}
                className="mt-0.5 w-4 h-4 shrink-0" style={{ accentColor: 'var(--accent)' }} />
              <span className="text-xs text-slate-300">
                Merci d'indiquer si votre candidat est déjà intervenu en prestation chez <strong>{clientName || 'ce client'}</strong>.
              </span>
            </label>
            {workedAtClient && (
              <div className="mt-2 pl-6">
                <label className="label">Dernière date de fin de mission</label>
                <input type="date" className="input" value={exitDate}
                  onChange={e => setExitDate(e.target.value)} />
              </div>
            )}
          </div>

          <div>
            <label className="label">
              CV (PDF, Word, Excel) {mode === 'existing' ? <span className="text-slate-500 font-normal">· facultatif (le CV du vivier sera réutilisé)</span> : '*'}
            </label>

            {!cvFile && (
              <label
                className="flex items-start gap-2 p-3 mb-3 rounded-lg border cursor-pointer"
                style={{ background: 'rgba(245,158,11,0.08)', borderColor: 'rgba(245,158,11,0.3)' }}>
                <input type="checkbox" checked={aiAck} onChange={e => setAiAck(e.target.checked)}
                       className="mt-0.5 w-4 h-4 shrink-0" style={{ accentColor: '#f59e0b' }} />
                <span className="text-xs text-slate-300 flex items-start gap-1.5">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" style={{ color: '#f59e0b' }} />
                  <span>
                    Le CV importé sera traité par une IA (extraction, scoring, harmonisation).
                    Merci de vérifier qu'il est <strong>anonymisé</strong> (nom, coordonnées retirés)
                    avant de le déposer.
                  </span>
                </span>
              </label>
            )}

            {cvFile ? (
              <div className="flex items-center gap-3 p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-lg">
                <CheckCircle size={18} className="text-emerald-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-emerald-300 truncate">{cvFile.name}</div>
                  <div className="text-xs text-slate-500">{(cvFile.size / 1024).toFixed(0)} Ko</div>
                </div>
                <button type="button" onClick={() => setCvFile(null)}
                        className="text-slate-500 hover:text-red-400">
                  <X size={15} />
                </button>
              </div>
            ) : (
              <div
                className={clsx(
                  'border-2 border-dashed rounded-xl p-6 text-center transition-all duration-150',
                  !aiAck ? 'opacity-40 cursor-not-allowed border-white/5'
                    : dragOver ? 'border-brand-500 bg-brand-500/5 cursor-pointer' : 'border-white/10 hover:border-white/20 cursor-pointer'
                )}
                onDragOver={e => { e.preventDefault(); if (aiAck) setDragOver(true) }}
                onDragLeave={() => setDragOver(false)}
                onDrop={e => { e.preventDefault(); setDragOver(false); if (aiAck) handleFile(e.dataTransfer.files[0]) }}
                onClick={() => { if (aiAck) fileRef.current?.click() }}>
                <Upload size={24} className="mx-auto text-slate-600 mb-2" />
                <p className="text-sm text-slate-400 font-medium">
                  {!aiAck ? 'Cochez la case ci-dessus pour importer'
                    : mode === 'existing' ? 'Joindre un fichier à jour (optionnel)' : 'Glissez le fichier ou cliquez'}
                </p>
                <p className="text-[10px] text-slate-700 mt-1">
                  {mode === 'existing' ? "Sinon le dernier CV du vivier sera utilisé · PDF, Word, Excel · Max 10MB" : 'PDF, Word, Excel · Max 10MB'}
                </p>
                <input ref={fileRef} type="file" disabled={!aiAck}
                       accept=".pdf,application/pdf,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                       className="hidden"
                       onChange={e => handleFile(e.target.files[0])} />
              </div>
            )}
          </div>

          {/* Évaluation renseignée par le partenaire (déplacée depuis Validation CV) */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="label">Points forts du CV *</label>
              <textarea className="input h-20 resize-y text-sm" placeholder="Atouts, expériences clés…"
                value={pointsForts} onChange={e => setPointsForts(e.target.value)} />
            </div>
            <div>
              <label className="label">Éléments différenciants *</label>
              <textarea className="input h-20 resize-y text-sm" placeholder="Ce qui distingue ce profil…"
                value={elementsDiff} onChange={e => setElementsDiff(e.target.value)} />
            </div>
          </div>

          <label className="flex items-start gap-2 text-xs text-slate-400 cursor-pointer">
            <input type="checkbox" className="mt-0.5 accent-brand-500"
                   checked={consent} onChange={e => setConsent(e.target.checked)} />
            <span>
              Je certifie disposer du consentement du consultant pour le traitement
              de ses données personnelles (CV) au titre de cette candidature, conformément au RGPD.
            </span>
          </label>

          {error && (
            <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <div className="flex gap-2 justify-end pt-1">
            <button type="button" onClick={onClose} className="btn-ghost">Annuler</button>
            <button type="submit" disabled={loading || !consent} className="btn-primary">
              {loading ? <><Loader2 size={14} className="animate-spin" />Envoi...</> : 'Soumettre'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Submission row (admin sees all + partner name, partner sees their own) ────
function SubmissionRow({ sub, onDelete, canDelete, isAdmin, aoSkillsRequired }) {
  const c = sub.consultants || {}
  const submitter = sub.submitter || {}

  // Skills match: highlight consultant skills that match AO required skills
  const aoSkillsNorm = aoSkillsRequired
    ? aoSkillsRequired.split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
    : []
  const cvSkills = c.skills ? c.skills.split(',').map(s => s.trim()).filter(Boolean) : []
  const matchCount = cvSkills.filter(s =>
    aoSkillsNorm.some(as => as.includes(s.toLowerCase()) || s.toLowerCase().includes(as))
  ).length

  return (
    <div className="p-3 rounded-lg bg-white/3 border border-white/5 hover:border-white/10 transition-all group">
      <div className="flex items-center gap-3">
        <UserCircle2 size={28} className="text-slate-500 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {/* Porteur (partenaire) AVANT le trigramme — demande Sullyvan */}
            {isAdmin && submitter.name && (
              <span className="text-sm font-semibold text-brand-300 inline-flex items-center gap-1">
                <Building2 size={12} /> {submitter.name}
              </span>
            )}
            <span className="text-sm font-medium text-white truncate">
              {isAdmin && submitter.name && <span className="text-slate-600">· </span>}
              {c.name || 'Inconnu'}
            </span>
            {c.employment_type && (
              <span className="badge bg-white/5 text-slate-400 text-[10px]">
                {c.employment_type === 'salarie' ? 'Salarié' : 'Indépendant'}
              </span>
            )}
            {c.tjm && (
              <span className="text-[10px] text-emerald-400 inline-flex items-center gap-0.5">
                <Euro size={9} />{c.tjm}/j
              </span>
            )}
            {isAdmin && sub.worked_at_client && (
              <span className="badge text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/20">
                Déjà intervenu chez le client{sub.worked_at_client_exit_date ? ` · sortie ${formatDate(sub.worked_at_client_exit_date)}` : ''}
              </span>
            )}
          </div>
        </div>
        <a href={sub.cv_url} target="_blank" rel="noopener noreferrer"
           className="text-xs text-slate-400 hover:text-brand-400 inline-flex items-center gap-1 shrink-0">
          <FileText size={12} /> CV
        </a>
        {canDelete && (
          <button onClick={() => onDelete(sub.id)}
                  className="text-slate-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
            <Trash2 size={13} />
          </button>
        )}
      </div>
      {cvSkills.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2 pl-10">
          {cvSkills.slice(0, 5).map((skill, i) => {
            const matches = aoSkillsNorm.some(as => as.includes(skill.toLowerCase()) || skill.toLowerCase().includes(as))
            return (
              <span key={i} className={clsx('badge text-[9px]',
                matches
                  ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                  : 'bg-white/5 text-slate-500 border border-white/5'
              )}>
                {skill}
              </span>
            )
          })}
          {cvSkills.length > 5 && (
            <span className="badge bg-white/3 text-slate-600 text-[9px] border border-white/3">+{cvSkills.length - 5}</span>
          )}
          {aoSkillsNorm.length > 0 && (
            <span className="text-[9px] text-slate-600 ml-1 self-center">
              {matchCount}/{aoSkillsNorm.length} skills AO
            </span>
          )}
        </div>
      )}
      {/* Analyse du CV renseignée par le partenaire à la soumission (demande Sullyvan :
          affichée ici, dans l'analyse du CV, plutôt que dans l'onglet Validation). */}
      {(sub.points_forts || sub.elements_differenciants) && (
        <div className="mt-2 pl-10 space-y-1.5">
          {sub.points_forts && (
            <div>
              <div className="text-[10px] font-medium text-slate-500 uppercase tracking-wide">Points forts du CV</div>
              <p className="text-[11px] text-slate-300 whitespace-pre-wrap mt-0.5">{sub.points_forts}</p>
            </div>
          )}
          {sub.elements_differenciants && (
            <div>
              <div className="text-[10px] font-medium text-slate-500 uppercase tracking-wide">Éléments différenciants</div>
              <p className="text-[11px] text-slate-300 whitespace-pre-wrap mt-0.5">{sub.elements_differenciants}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Onglet « Validation CV » (demande Sullyvan) ────────────────
// Reprend la liste des CV reçus, remplace le lien CV par les actions de
// validation commerciale : retenu / non retenu GRP-IT → envoi client, +
// indicateurs échange commercial / affaire gagnée-perdue.
const PILL = 'text-[11px] px-2.5 py-1 rounded-md border inline-flex items-center gap-1 transition-colors disabled:opacity-50'
const TONE = {
  green: { background: 'rgba(16,185,129,0.12)', color: '#10b981', borderColor: 'rgba(16,185,129,0.45)' },
  red: { background: 'rgba(220,38,38,0.12)', color: '#dc2626', borderColor: 'rgba(220,38,38,0.45)' },
  amber: { background: 'rgba(245,158,11,0.12)', color: '#f59e0b', borderColor: 'rgba(245,158,11,0.45)' },
  accent: { background: 'var(--accent-soft)', color: 'var(--accent-text)', borderColor: 'var(--accent)' },
  off: { borderColor: 'var(--border)', color: 'var(--text-muted)' },
}

function ClientSendModal({ aoId, sub, clientId, onClose, onSent }) {
  const [email, setEmail] = useState('')
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')

  // Pré-remplit l'email depuis le contact de la fiche client (best-effort).
  useEffect(() => {
    if (!clientId) return
    api.get(`/clients/${clientId}`).then(r => setEmail(r.data?.contact_email || '')).catch(() => {})
  }, [clientId])

  const send = async () => {
    setSending(true); setError('')
    try {
      await api.post(`/matching/${aoId}/send-cv-to-client`, {
        consultant_id: sub.consultant_id, to_email: email.trim(), message,
      })
      onSent()
    } catch (e) {
      setError(e.response?.data?.detail || "Échec de l'envoi")
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-md" onClick={e => e.stopPropagation()}>
        <h2 className="text-sm font-semibold text-white flex items-center gap-2 mb-1">
          <Send size={14} className="text-brand-400" /> Envoyer le CV au client
        </h2>
        <p className="text-xs text-slate-500 mb-4">
          Un email contenant un <strong>lien sécurisé</strong> vers le CV de {sub.consultants?.name || 'ce profil'} sera envoyé au client. Le partenaire porteur en sera informé.
        </p>
        <label className="label">Email du client *</label>
        <input className="input" type="email" value={email} placeholder="contact@client.fr"
          onChange={e => setEmail(e.target.value)} autoComplete="off" />
        <label className="label mt-3">Message (optionnel)</label>
        <textarea className="input h-24 resize-none" value={message}
          placeholder="Mot d'accompagnement…" onChange={e => setMessage(e.target.value)} />
        {error && <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 mt-3">{error}</div>}
        <div className="flex gap-2 justify-end mt-4">
          <button onClick={onClose} className="btn-ghost">Annuler</button>
          <button onClick={send} disabled={sending || !email.includes('@')} className="btn-primary">
            {sending ? <><Loader2 size={14} className="animate-spin" /> Envoi…</> : <><Send size={14} /> Envoyer</>}
          </button>
        </div>
      </div>
    </div>
  )
}

// Pastille de score (couleur selon le niveau) — visible d'un coup d'œil.
const scoreTone = (v) => v >= 75
  ? { background: 'rgba(16,185,129,0.15)', color: '#10b981', borderColor: 'rgba(16,185,129,0.45)' }
  : v >= 50
    ? { background: 'var(--accent-soft)', color: 'var(--accent-text)', borderColor: 'var(--border)' }
    : { background: 'rgba(245,158,11,0.14)', color: '#f59e0b', borderColor: 'rgba(245,158,11,0.4)' }

function ValidationTab({ aoId, submissions, clientId, scores }) {
  const confirm = useConfirm()
  const [states, setStates] = useState({})
  const [busy, setBusy] = useState(null)
  const [clientModalSub, setClientModalSub] = useState(null)
  const [cvModalSub, setCvModalSub] = useState(null)
  const [selected, setSelected] = useState(() => new Set())
  const toggleSel = (cid) => setSelected(p => { const n = new Set(p); n.has(cid) ? n.delete(cid) : n.add(cid); return n })

  const bulkReject = async (ids) => {
    if (!ids.length) return
    const ok = await confirm({
      title: `Marquer ${ids.length} CV comme « Non retenu » ?`,
      message: 'Les partenaires porteurs de ces profils seront notifiés par email.',
      confirmLabel: 'Confirmer et notifier',
    })
    if (!ok) return
    setBusy('__bulk__')
    try {
      await api.post(`/matching/${aoId}/validation-bulk`, { consultant_ids: ids, validation: 'non_retenu', notify: true })
      setStates(s => { const n = { ...s }; ids.forEach(cid => { n[cid] = { ...(n[cid] || {}), validation: 'non_retenu' } }); return n })
      setSelected(new Set())
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors de la mise à jour groupée')
    } finally {
      setBusy(null)
    }
  }

  useEffect(() => {
    api.get(`/matching/${aoId}/states`)
      .then(r => setStates(r.data.states || {}))
      .catch(() => setStates({}))
  }, [aoId])

  const update = async (consultantId, patch) => {
    setBusy(consultantId)
    try {
      const { data } = await api.post(`/matching/${aoId}/validation`, { consultant_id: consultantId, ...patch })
      const { _notified, ...row } = data
      setStates(s => ({ ...s, [consultantId]: { ...(s[consultantId] || {}), ...row } }))
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors de la mise à jour')
    } finally {
      setBusy(null)
    }
  }

  // Action qui notifie le partenaire : confirmation avant envoi (décision « auto + confirmation »).
  const act = async (consultantId, patch, confirmTitle) => {
    const ok = await confirm({
      title: confirmTitle,
      message: 'Le partenaire porteur du profil sera notifié par email.',
      confirmLabel: 'Confirmer et notifier',
    })
    if (!ok) return
    update(consultantId, { ...patch, notify: true })
  }

  // Un consultant peut avoir plusieurs soumissions : on ne garde que la 1re.
  const rows = []
  const seen = new Set()
  for (const s of submissions) {
    if (s.consultant_id && !seen.has(s.consultant_id)) { seen.add(s.consultant_id); rows.push(s) }
  }

  if (rows.length === 0) {
    return <div className="card p-8 text-center text-sm text-slate-500">Aucun CV reçu pour cet AO.</div>
  }

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <div className="flex items-center gap-3">
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Validation CV ({rows.length})</span>
          <button className="text-[11px] text-slate-400 hover:text-white"
            onClick={() => setSelected(selected.size === rows.length ? new Set() : new Set(rows.map(r => r.consultant_id)))}>
            {selected.size === rows.length && rows.length > 0 ? 'Tout désélectionner' : 'Tout sélectionner'}
          </button>
        </div>
        {selected.size > 0 && (
          <button className={PILL} style={TONE.red} disabled={busy === '__bulk__'}
            onClick={() => bulkReject([...selected])}>
            {busy === '__bulk__' ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
            Marquer {selected.size} « Non retenu »
          </button>
        )}
      </div>
      <div className="space-y-2">
        {rows.map(s => {
          const c = s.consultants || {}
          const st = states[s.consultant_id] || {}
          const busyRow = busy === s.consultant_id
          const retained = st.validation === 'retenu'
          const rejected = st.validation === 'non_retenu'
          return (
            <div key={s.consultant_id} className="p-3 rounded-lg bg-white/3 border border-white/5">
              <div className="flex items-center gap-3 flex-wrap">
                <input type="checkbox" checked={selected.has(s.consultant_id)}
                  onChange={() => toggleSel(s.consultant_id)}
                  className="shrink-0 w-4 h-4 cursor-pointer" style={{ accentColor: 'var(--accent)' }}
                  title="Sélectionner pour une action groupée" />
                <UserCircle2 size={24} className="text-slate-500 shrink-0" />
                <div className="min-w-0 flex-1">
                  {/* Identité + score (mis en avant) */}
                  <div className="flex items-center gap-2 flex-wrap">
                    {s.submitter?.name && (
                      <span className="text-sm font-semibold text-brand-300 inline-flex items-center gap-1">
                        <Building2 size={12} /> {s.submitter.name}
                      </span>
                    )}
                    <span className="text-sm font-medium text-white truncate">
                      {s.submitter?.name && <span className="text-slate-600">· </span>}{c.name || 'Inconnu'}
                    </span>
                    {scores?.[s.consultant_id] != null && (
                      <span className="text-[11px] font-bold px-1.5 py-0.5 rounded border tabular"
                        style={scoreTone(scores[s.consultant_id])}
                        title="Score de matching IA">
                        {scores[s.consultant_id]}
                      </span>
                    )}
                  </div>
                  {/* Méta + liens (discret, hiérarchie visuelle) */}
                  <div className="flex items-center gap-2.5 flex-wrap mt-1 text-[11px]" style={{ color: 'var(--text-faint)' }}>
                    {c.employment_type && (
                      <span>{c.employment_type === 'salarie' ? 'Salarié' : 'Indépendant'}</span>
                    )}
                    {c.tjm && (
                      <span className="inline-flex items-center gap-0.5 text-emerald-400"><Euro size={11} />{c.tjm}/j</span>
                    )}
                    {s.cv_url && (
                      <a href={s.cv_url} target="_blank" rel="noopener noreferrer"
                         className="inline-flex items-center gap-1 hover:text-brand-400 transition-colors">
                        <FileText size={11} /> CV
                      </a>
                    )}
                    <button onClick={() => setCvModalSub(s)}
                      className="inline-flex items-center gap-1 text-brand-300 hover:text-brand-200 transition-colors"
                      title="Régénérer au format Groupement-IT (FR / EN)">
                      <Sparkles size={11} /> CV GRP-IT
                    </button>
                  </div>
                </div>
                {busyRow && <Loader2 size={13} className="animate-spin text-slate-500 shrink-0" />}
              </div>

              {/* Actions de validation */}
              <div className="flex items-center gap-2 flex-wrap mt-3 sm:pl-9">
                <button disabled={busyRow} className={PILL} style={retained ? TONE.green : TONE.off}
                  onClick={() => retained
                    ? update(s.consultant_id, { validation: 'none' })
                    : act(s.consultant_id, { validation: 'retenu' }, 'Marquer « Retenu GRP-IT » ?')}>
                  <CheckCircle size={12} /> Retenu GRP-IT
                </button>
                <button disabled={busyRow} className={PILL} style={rejected ? TONE.red : TONE.off}
                  onClick={() => rejected
                    ? update(s.consultant_id, { validation: 'none' })
                    : act(s.consultant_id, { validation: 'non_retenu' }, 'Marquer « Non retenu » ?')}>
                  <X size={12} /> Non retenu
                </button>
                {retained && (
                  <button disabled={busyRow} className={PILL} style={st.sent_to_client_at ? TONE.accent : TONE.off}
                    onClick={() => setClientModalSub(s)}>
                    <Send size={12} /> {st.sent_to_client_at ? 'Renvoyer au client' : 'Envoi au client'}
                  </button>
                )}

                <span className="w-px h-4 mx-1" style={{ background: 'var(--border)' }} />

                <button disabled={busyRow} className={PILL} style={st.commercial_exchange ? TONE.amber : TONE.off}
                  onClick={() => st.commercial_exchange
                    ? update(s.consultant_id, { commercial_exchange: false })
                    : act(s.consultant_id, { commercial_exchange: true }, 'Marquer un échange commercial en cours ?')}>
                  Échange commercial : {st.commercial_exchange ? 'Oui' : 'Non'}
                </button>
                <button disabled={busyRow} className={PILL} style={st.deal_status === 'gagnee' ? TONE.green : TONE.off}
                  onClick={() => st.deal_status === 'gagnee'
                    ? update(s.consultant_id, { deal_status: 'none' })
                    : act(s.consultant_id, { deal_status: 'gagnee' }, "Marquer l'affaire comme gagnée ?")}>
                  <Award size={12} /> Affaire gagnée
                </button>
                <button disabled={busyRow} className={PILL} style={st.deal_status === 'perdue' ? TONE.red : TONE.off}
                  onClick={() => st.deal_status === 'perdue'
                    ? update(s.consultant_id, { deal_status: 'none' })
                    : act(s.consultant_id, { deal_status: 'perdue' }, "Marquer l'affaire comme perdue ?")}>
                  Affaire perdue
                </button>
              </div>
              {/* Points forts / éléments différenciants : déplacés dans l'onglet
                  « Analyse & CV » (carte du CV reçu), demande Sullyvan. */}
            </div>
          )
        })}
      </div>
      {clientModalSub && (
        <ClientSendModal
          aoId={aoId}
          sub={clientModalSub}
          clientId={clientId}
          onClose={() => setClientModalSub(null)}
          onSent={() => {
            const cid = clientModalSub.consultant_id
            setStates(s => ({ ...s, [cid]: { ...(s[cid] || {}), sent_to_client_at: new Date().toISOString() } }))
            setClientModalSub(null)
          }}
        />
      )}
      {cvModalSub && (
        <HarmonizedCvModal
          submissionId={cvModalSub.id}
          name={cvModalSub.consultants?.name}
          onClose={() => setCvModalSub(null)}
        />
      )}
    </div>
  )
}

// ─── AO edit modal ──────────────────────────────────────────────
function AOEditModal({ ao, onClose, onSaved }) {
  const AO_TYPES = ['Assurance', 'Banque / Finance', 'IT / Dev', 'Énergie', 'Retail', 'Public', 'Santé', 'Autre']
  const [clients, setClients] = useState([])
  const [form, setForm] = useState({
    client_id: ao.client_id || '',
    title: ao.title || '',
    description: ao.description || '',
    skills_required: ao.skills_required || '',
    reference: ao.reference || '',
    budget_max: ao.budget_max?.toString() || '',
    location: ao.location || '',
    duration: ao.duration || '',
    context: ao.context || '',
    ao_type: ao.ao_type || '',
    deadline: ao.deadline || '',
    status: ao.status || 'open',
    work_mode: ao.work_mode || '',
    langue_requise: ao.langue_requise || '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Priorités de matching (mêmes étoiles qu'à la création). Fusion sur les
  // défauts : un AO d'avant-v2 (4 axes) hérite des nouveaux axes sans casser
  // la validation (StarConfig attend désormais les 6 critères).
  const [stars, setStars] = useState({ ...DEFAULT_STARS, ...(ao.scoring_overrides?.stars || {}) })
  const [scoringTouched, setScoringTouched] = useState(false)
  const onStars = (s) => { setStars(s); setScoringTouched(true) }

  // Pièces jointes d'origine — retrouvées depuis le stockage.
  const [sources, setSources] = useState([])
  const [sourcesBusy, setSourcesBusy] = useState(false)

  // Panneau de régénération IA (identique à la création).
  const [aiText, setAiText] = useState('')
  const [aiFiles, setAiFiles] = useState([])
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState('')
  const [aiDone, setAiDone] = useState(false)

  useEffect(() => {
    api.get('/clients').then(r => setClients(r.data)).catch(() => {})
    api.get(`/aos/${ao.id}/sources`).then(r => setSources(r.data.source_files || [])).catch(() => {})
  }, [ao.id])

  const set = (k) => (e) => setForm(p => ({ ...p, [k]: e.target.value }))

  const generateFromSource = async () => {
    setAiError(''); setAiDone(false)
    if (!aiText.trim() && aiFiles.length === 0) {
      setAiError("Collez un email ou ajoutez un fichier (PDF, DOCX, XLSX)."); return
    }
    setAiLoading(true)
    try {
      const fd = new FormData()
      fd.append('pasted_text', aiText)
      aiFiles.forEach(f => fd.append('files', f))
      const { data } = await api.post('/aos/draft', fd)
      setForm(p => ({
        ...p,
        title: data.title || p.title,
        description: data.description || p.description,
        skills_required: data.skills_required || p.skills_required,
        reference: data.reference || p.reference,
        ao_type: data.ao_type || p.ao_type,
        budget_max: data.budget_max != null ? String(data.budget_max) : p.budget_max,
        location: data.location || p.location,
        duration: data.duration || p.duration,
        deadline: data.deadline || p.deadline,
        context: data.context || p.context,
        langue_requise: data.langue_requise || p.langue_requise,
      }))
      if (data.scoring_stars && Object.keys(data.scoring_stars).length) {
        setStars(p => ({ ...p, ...data.scoring_stars })); setScoringTouched(true)
      }
      // Persiste les fichiers ré-uploadés comme nouvelles pièces jointes.
      if (aiFiles.length) {
        try {
          const sfd = new FormData()
          aiFiles.forEach(f => sfd.append('files', f))
          const sres = await api.post(`/aos/${ao.id}/sources`, sfd)
          setSources(sres.data.source_files || [])
        } catch { /* non bloquant */ }
      }
      setAiFiles([])
      setAiDone(true)
    } catch (err) {
      setAiError(err.response?.data?.detail || 'Échec de la génération. Réessayez.')
    } finally {
      setAiLoading(false)
    }
  }

  const removeSource = async (path) => {
    setSourcesBusy(true)
    try {
      const { data } = await api.post(`/aos/${ao.id}/sources/delete`, { path })
      setSources(data.source_files || [])
    } catch { /* ignore */ } finally {
      setSourcesBusy(false)
    }
  }

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      const payload = { ...form }
      if (!payload.budget_max) delete payload.budget_max
      else payload.budget_max = parseInt(payload.budget_max)
      if (!payload.deadline) delete payload.deadline
      if (!payload.work_mode) delete payload.work_mode
      if (scoringTouched) payload.scoring_overrides = { stars }
      await api.patch(`/aos/${ao.id}`, payload)
      onSaved()
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur lors de la mise à jour')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <Pencil size={14} className="text-brand-400" /> Modifier l'AO
          </h2>
          <button onClick={onClose} className="btn-ghost p-1.5"><X size={14} /></button>
        </div>

        {/* Pièces jointes d'origine + régénération IA (reprend la création) */}
        <div className="card p-4 mb-4 border border-violet-500/20 bg-violet-500/[0.04]">
          <h3 className="text-xs font-semibold text-white flex items-center gap-1.5 mb-2">
            <Sparkles size={13} className="text-violet-300" /> Source & régénération IA
          </h3>

          {sources.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {sources.map((s) => (
                <span key={s.path} className="badge bg-white/5 border border-white/10 text-slate-300 text-xs inline-flex items-center gap-1.5">
                  <FileText size={11} />
                  {s.url
                    ? <a href={s.url} target="_blank" rel="noreferrer" className="hover:text-white inline-flex items-center gap-1">{s.name}<Download size={10} /></a>
                    : <span>{s.name}</span>}
                  <button type="button" onClick={() => removeSource(s.path)} disabled={sourcesBusy} className="ml-0.5 text-slate-500 hover:text-red-400"><X size={11} /></button>
                </span>
              ))}
            </div>
          )}

          <textarea
            className="input h-20 resize-none text-sm"
            placeholder="Collez un nouvel email / contexte pour régénérer les champs…"
            value={aiText} onChange={e => setAiText(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <label className="btn-ghost cursor-pointer text-xs">
              <UploadCloud size={14} /> Ajouter un fichier
              <input type="file" multiple accept=".pdf,.docx,.xlsx,.txt,.csv" className="hidden"
                onChange={e => { setAiFiles(prev => [...prev, ...Array.from(e.target.files)]); e.target.value = '' }} />
            </label>
            {aiFiles.map((f, i) => (
              <span key={i} className="badge bg-white/5 border border-white/10 text-slate-300 text-xs">
                <FileText size={11} className="inline mr-1" />{f.name}
                <button type="button" onClick={() => setAiFiles(prev => prev.filter((_, j) => j !== i))} className="ml-1.5 text-slate-500 hover:text-red-400"><X size={11} /></button>
              </span>
            ))}
            <button type="button" onClick={generateFromSource} disabled={aiLoading} className="btn-primary text-xs ml-auto">
              {aiLoading ? <><Loader2 size={13} className="animate-spin" />Génération…</> : <><Sparkles size={13} />Régénérer</>}
            </button>
          </div>
          {aiError && <p className="text-xs text-red-400 mt-2">{aiError}</p>}
          {aiDone && !aiError && <p className="text-xs text-emerald-400 mt-2 flex items-center gap-1.5"><CheckCircle size={12} /> Champs régénérés : vérifiez avant d'enregistrer.</p>}
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="sm:col-span-2">
              <label className="label">Client *</label>
              <div className="relative">
                <select className="input appearance-none pr-9" value={form.client_id} onChange={set('client_id')} required>
                  <option value="" className="bg-navy-900">Choisir un client</option>
                  {clients.map(c => <option key={c.id} value={c.id} className="bg-navy-900">{c.name}</option>)}
                </select>
                <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
              </div>
            </div>
            <div className="sm:col-span-2">
              <label className="label">Titre *</label>
              <input className="input" required value={form.title} onChange={set('title')} />
            </div>
            <div className="sm:col-span-2">
              <label className="label">Référence client / consultation</label>
              <input className="input" value={form.reference} onChange={set('reference')} placeholder="ex: Marché Spécifique n°23915SA230MS" />
            </div>
          </div>

          <div>
            <label className="label">Description *</label>
            <textarea className="input min-h-[80px] resize-y" required value={form.description} onChange={set('description')} />
          </div>

          <div>
            <label className="label">
              Compétences requises * <span className="text-slate-500 font-normal">(séparées par des virgules)</span>
            </label>
            <input className="input" required value={form.skills_required} onChange={set('skills_required')} placeholder="Python, React, AWS..." />
          </div>

          <div>
            <label className="label">Contexte / Notes IA</label>
            <textarea className="input min-h-[60px] resize-y" value={form.context} onChange={set('context')} />
          </div>

          <div>
            <label className="label" style={{ color: 'var(--danger)' }}>Date limite de réponse</label>
            <input className="input" type="date" value={form.deadline} onChange={set('deadline')} />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div>
              <label className="label">Budget max (€/j)</label>
              <input className="input" type="number" min="0" value={form.budget_max} onChange={set('budget_max')} />
            </div>
            <div>
              <label className="label">Localisation</label>
              <input className="input" value={form.location} onChange={set('location')} />
            </div>
            <div>
              <label className="label">Mode de travail</label>
              <div className="relative">
                <select className="input appearance-none pr-9" value={form.work_mode} onChange={set('work_mode')}>
                  <option value="" className="bg-navy-900">Non précisé</option>
                  <option value="onsite" className="bg-navy-900">Sur site</option>
                  <option value="hybrid" className="bg-navy-900">Hybride</option>
                  <option value="remote" className="bg-navy-900">Remote</option>
                </select>
                <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
              </div>
            </div>
            <div>
              <label className="label">Durée</label>
              <input className="input" value={form.duration} onChange={set('duration')} />
            </div>
            <div>
              <label className="label">Type AO</label>
              <div className="relative">
                <select className="input appearance-none pr-9" value={form.ao_type} onChange={set('ao_type')}>
                  <option value="" className="bg-navy-900">—</option>
                  {AO_TYPES.map(t => <option key={t} value={t} className="bg-navy-900">{t}</option>)}
                </select>
                <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
              </div>
            </div>
          </div>

          <div>
            <label className="label">Langue requise</label>
            <input className="input" value={form.langue_requise} onChange={set('langue_requise')}
                   placeholder="ex. Anglais courant (impératif)" />
            <p className="text-[10px] text-slate-500 mt-1">
              Si le client l'exige. Le matching signale les CV où cette langue n'est pas détectée.
            </p>
          </div>

          <div>
            <label className="label flex items-center gap-1.5">
              <Target size={12} className="text-brand-400" /> Priorités de matching
            </label>
            <div className="rounded-lg border border-white/10 p-3 mt-1">
              <ScoringPriorities stars={stars} onStarsChange={onStars} />
            </div>
          </div>

          <div>
            <label className="label">Statut</label>
            <div className="flex gap-2">
              {[{ v: 'open', l: 'Ouvert' }, { v: 'closed', l: 'Fermé' }].map(o => (
                <button key={o.v} type="button"
                  onClick={() => setForm(p => ({ ...p, status: o.v }))}
                  className={clsx(
                    'px-4 py-2 text-xs rounded-lg border font-medium transition-all',
                    form.status === o.v
                      ? 'bg-brand-600/20 border-brand-500/40 text-brand-300'
                      : 'bg-white/5 border-white/10 text-slate-400 hover:text-slate-200'
                  )}>
                  {o.l}
                </button>
              ))}
            </div>
          </div>

          {error && <p className="text-xs text-red-400">{error}</p>}

          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose} className="btn-ghost text-xs px-3">Annuler</button>
            <button type="submit" disabled={loading} className="btn-primary text-xs px-4 flex items-center gap-1.5">
              {loading ? <Loader2 size={13} className="animate-spin" /> : 'Enregistrer'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Deadline banner (big & red) ────────────────────────────────
function DeadlineBanner({ deadline }) {
  if (!deadline) return null
  const days = daysUntil(deadline)
  const overdue = days < 0
  const urgent = days >= 0 && days <= 7

  // Tonalité selon l'urgence réelle : rouge seulement si dépassée, ambre si
  // l'échéance est proche (≤ 7 j), sinon un indigo calme (mise en évidence
  // sans agressivité).
  const tone = overdue
    ? { fg: 'var(--danger)', bg: 'var(--danger-soft)', border: 'var(--danger)', icon: 'var(--danger)' }
    : urgent
      ? { fg: '#b45309', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.4)', icon: '#f59e0b' }
      : { fg: 'var(--accent-text)', bg: 'var(--accent-soft)', border: 'var(--border)', icon: 'var(--accent)' }

  return (
    <div
      className="mb-5 rounded-lg border px-5 py-4 flex items-center gap-4"
      style={{ background: tone.bg, borderColor: tone.border }}
    >
      <div
        className="shrink-0 w-11 h-11 rounded-lg flex items-center justify-center"
        style={{ background: tone.icon, color: '#fff' }}
      >
        {overdue ? <AlertTriangle size={22} /> : <CalendarClock size={22} />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: tone.fg }}>
          Date limite de réponse
        </div>
        <div className="text-2xl font-bold leading-tight" style={{ color: tone.fg }}>
          {formatDate(deadline)}
        </div>
      </div>
      <div className="text-right shrink-0">
        <div className="text-2xl font-extrabold tabular" style={{ color: tone.fg }}>
          {overdue ? 'Dépassée' : days === 0 ? "Aujourd'hui" : `J-${days}`}
        </div>
        <div className="text-[11px]" style={{ color: tone.fg }}>
          {overdue
            ? `depuis ${Math.abs(days)} j`
            : days === 0 ? 'dernier jour' : urgent ? 'échéance proche' : 'restants'}
        </div>
      </div>
    </div>
  )
}

// ─── AO insights chart (admin) ──────────────────────────────────
function StatBar({ label, value, max, color, sublabel }) {
  const pct = max > 0 ? (value / max) * 100 : 0
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-[var(--text-muted)] flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: color }} />
          {label}
        </span>
        <span className="text-xs font-semibold text-[var(--text)] tabular">
          {value}
          {sublabel ? <span className="text-[var(--text-faint)] font-normal">{sublabel}</span> : null}
        </span>
      </div>
      <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${value > 0 ? Math.max(pct, 4) : 0}%`, background: color }}
        />
      </div>
    </div>
  )
}

function AOInsightsChart({ aoId }) {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    api.get(`/aos/${aoId}/stats`)
      .then(r => { if (alive) setStats(r.data) })
      .catch(e => { if (alive) setError(e.response?.data?.detail || 'Erreur') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [aoId])

  if (loading) {
    return (
      <div className="card p-4 flex items-center justify-center h-40">
        <Loader2 size={18} className="animate-spin text-[var(--text-faint)]" />
      </div>
    )
  }
  if (error || !stats) return null

  // Distinct, accessible colors per series
  const C = {
    eligible: '#6366f1',   // indigo  — could answer / matching pool
    responded: '#10b981',  // emerald — answered
    proposed: '#8b5cf6',   // violet  — proposed
    gap: '#f59e0b',        // amber   — matching but not proposed
  }
  const partnersMax = Math.max(stats.partners_eligible, stats.partners_responded, 1)
  const consultantsMax = Math.max(
    stats.consultants_pool_eligible, stats.consultants_proposed, stats.consultants_eligible_not_proposed, 1
  )
  const partnerCoverage = stats.partners_eligible > 0
    ? Math.round((stats.partners_responded / stats.partners_eligible) * 100) : 0
  const consultantCoverage = stats.consultants_pool_eligible > 0
    ? Math.round((stats.consultants_proposed / stats.consultants_pool_eligible) * 100) : 0

  return (
    <div className="card p-4">
      <p className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wide flex items-center gap-1.5 mb-4">
        <BarChart3 size={13} className="text-[var(--accent-text)]" /> Couverture de l'AO
      </p>

      {/* Partners */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-faint)]">Partenaires</span>
          <span className="text-[11px] text-[var(--text-faint)]">{partnerCoverage}% ont répondu</span>
        </div>
        <div className="space-y-2.5">
          <StatBar
            label="Peuvent répondre" value={stats.partners_eligible} max={partnersMax} color={C.eligible}
            sublabel={(stats.partners_list_1 + stats.partners_list_2) > 0 ? ` · L1 ${stats.partners_list_1} / L2 ${stats.partners_list_2}` : ''}
          />
          <StatBar label="Ont répondu" value={stats.partners_responded} max={partnersMax} color={C.responded} />
        </div>
      </div>

      {/* Consultants */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-faint)]">Consultants</span>
          <span className="text-[11px] text-[var(--text-faint)]">{consultantCoverage}% du vivier éligible proposé</span>
        </div>
        <div className="space-y-2.5">
          <StatBar label="Éligibles (vivier qui matche)" value={stats.consultants_pool_eligible} max={consultantsMax} color={C.eligible} />
          <StatBar label="Proposés" value={stats.consultants_proposed} max={consultantsMax} color={C.proposed} />
          <StatBar label="Éligibles non proposés" value={stats.consultants_eligible_not_proposed} max={consultantsMax} color={C.gap} />
        </div>
      </div>

      <p className="text-[10px] text-[var(--text-faint)] mt-4 leading-relaxed">
        « Éligibles » = consultants dont les compétences recoupent celles de l'AO, chez des partenaires ayant accès au client.
      </p>
    </div>
  )
}

// ─── Main page ──────────────────────────────────────────────────
export default function AODetailPage() {
  const { id } = useParams()
  const { isAdmin: isAdminRole, isStaff, user } = useAuth()
  const isAdmin = isStaff // staff view (admin + commerce) — naming kept to avoid touching every usage below
  const confirm = useConfirm()
  const navigate = useNavigate()
  const location = useLocation()

  const [ao, setAo] = useState(null)
  const [summary, setSummary] = useState('')
  const [descOpen, setDescOpen] = useState(true)
  // Onglets de la fiche : 'presentation' (la fiche AO) | 'analyse' (CV & matching)
  const [tab, setTab] = useState('presentation')
  // Liste des CVs repliable — mémorisée (elle peut être longue). Repliée par défaut.
  const [cvsOpen, setCvsOpen] = useState(() => localStorage.getItem('uti_ao_cvs_open') === '1')
  const toggleCvs = () => setCvsOpen(o => { localStorage.setItem('uti_ao_cvs_open', o ? '0' : '1'); return !o })
  const [submissions, setSubmissions] = useState([])
  const [vivier, setVivier] = useState([])
  const [matchResults, setMatchResults] = useState(null)
  const [allScores, setAllScores] = useState(null)  // tous les scores (analyses)
  const [loading, setLoading] = useState(true)
  const [matching, setMatching] = useState(false)
  const [matchError, setMatchError] = useState('')
  const [showMatchTour, setShowMatchTour] = useState(false)  // tutoriel « lire le matching »
  const [decisions, setDecisions] = useState({})  // consultant_id → dernière décision humaine
  const [showSubmitModal, setShowSubmitModal] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  // Assistant can deep-link here to open the "propose consultant" flow,
  // optionally pre-filling the new-consultant fields. It never submits.
  const [submitPrefill, setSubmitPrefill] = useState(location.state?.assistantPrefill || null)
  const [notifBusy, setNotifBusy] = useState('')   // '' | 'notify' | 'relance'
  const [notifMsg, setNotifMsg] = useState('')
  const [targetOpen, setTargetOpen] = useState(false)
  const [eligible, setEligible] = useState(null)
  const [selectedPartners, setSelectedPartners] = useState(() => new Set())
  const [targetBusy, setTargetBusy] = useState(false)

  const fetchAo = async () => {
    const r = await api.get(`/aos/${id}`)
    setAo(r.data)
    if (r.data.ai_summary) setSummary(r.data.ai_summary)
    return r.data
  }
  const fetchSubmissions = async () => {
    const r = await api.get(`/submissions/ao/${id}`)
    setSubmissions(r.data)
    return r.data
  }

  // Décisions humaines déjà enregistrées (retenu / écarté / désaccord) : relues
  // au chargement pour que le badge de la barre « Décision humaine » survive à un
  // rechargement (l'API renvoie du plus récent au plus ancien → 1re par consultant).
  const loadDecisions = async () => {
    try {
      const { data } = await api.get(`/decisions/ao/${id}`)
      const map = {}
      for (const d of (data || [])) {
        const cid = d.consultant_id
        if (cid && !map[cid]) map[cid] = d
      }
      setDecisions(map)
    } catch { /* non bloquant */ }
  }

  const runMatching = async () => {
    setMatching(true)
    setMatchError('')
    try {
      const { data: run } = await api.post('/matching/run', { ao_id: id, top_n: 5 })
      // Source de vérité = les résultats RENVOYÉS par le run (toujours calculés,
      // même si l'enregistrement en base échoue). On tente ensuite une relecture
      // qui FUSIONNE l'email partenaire + l'état humain (classement, badges), mais
      // on ne l'utilise QUE si elle ramène quelque chose : un échec de persistance
      // ne doit jamais faire disparaître un classement fraîchement calculé.
      let results = run.results || []
      try {
        const { data } = await api.get(`/matching/results/${id}`)
        if (data.results?.length) results = data.results
      } catch { /* relecture indisponible : on garde les résultats du run */ }
      setMatchResults(results)
      setAllScores(run.all_scores || null)
      writeMatchCache(id, results, run.all_scores)
      loadDecisions()  // recharge les badges de décision (désaccord pris en compte)
      if (results.length === 0) {
        setMatchError(
          "Le scoring n'a renvoyé aucun profil. Les CV soumis n'ont peut-être pas "
          + "de texte exploitable (CV non lisible / extraction vide)."
        )
      }
    } catch (err) {
      setMatchError(err.response?.data?.detail || 'Erreur lors du matching IA')
    } finally {
      setMatching(false)
    }
  }

  const handleNotify = async () => {
    if (!(await confirm({
      title: 'Envoyer aux partenaires ?',
      message: 'La liste 1 est notifiée immédiatement ; la liste 2 le sera après le délai configuré dans les réglages admin.',
      confirmLabel: 'Envoyer',
    }))) return
    setNotifBusy('notify'); setNotifMsg('')
    try {
      const { data } = await api.post(`/aos/${id}/notify`)
      const l2 = data.list2_scheduled_at
        ? `liste 2 prévue le ${formatDate(data.list2_scheduled_at)}`
        : (data.sent_list_2 ? `liste 2 envoyée (${data.sent_list_2})` : 'liste 2 non planifiée')
      setNotifMsg(`Liste 1 : ${data.sent_list_1} partenaire(s) notifié(s) · ${l2}.`)
      await fetchAo()
    } catch (e) {
      setNotifMsg(
        e.response?.status === 404
          ? "Indisponible : le serveur n'est pas encore à jour (déploiement backend requis)."
          : (e.response?.data?.detail || "Échec de l'envoi")
      )
    } finally {
      setNotifBusy('')
    }
  }

  const handleRelance = async () => {
    setNotifBusy('relance'); setNotifMsg('')
    try {
      const { data } = await api.post(`/aos/${id}/relance`)
      setNotifMsg(`Relance envoyée à ${data.relance_sent} partenaire(s) sans réponse.`)
      await fetchAo()
    } catch (e) {
      setNotifMsg(e.response?.data?.detail || 'Échec de la relance')
    } finally {
      setNotifBusy('')
    }
  }

  const openTarget = async () => {
    setTargetOpen(true); setEligible(null); setSelectedPartners(new Set())
    try {
      const { data } = await api.get(`/aos/${id}/eligible-partners`)
      setEligible(data.partners || [])
    } catch (e) {
      setEligible([]); setNotifMsg(e.response?.data?.detail || 'Impossible de charger les partenaires')
    }
  }
  const togglePartner = (pid) => setSelectedPartners(prev => {
    const n = new Set(prev); n.has(pid) ? n.delete(pid) : n.add(pid); return n
  })
  const sendTarget = async () => {
    if (selectedPartners.size === 0) return
    setTargetBusy(true); setNotifMsg('')
    try {
      const { data } = await api.post(`/aos/${id}/notify-partners`, { partner_ids: [...selectedPartners] })
      setNotifMsg(`Email renvoyé à ${data.sent} partenaire(s) sélectionné(s).`)
      setTargetOpen(false)
      await fetchAo()
    } catch (e) {
      setNotifMsg(e.response?.data?.detail || 'Échec du renvoi ciblé')
    } finally {
      setTargetBusy(false)
    }
  }

  useEffect(() => {
    const init = async () => {
      try {
        const [aoData, subs] = await Promise.all([fetchAo(), fetchSubmissions()])

        // Vivier chargé pour tous : les partenaires le proposent, le staff
        // peut aussi ajouter un CV manuellement à l'AO.
        try {
          const vivierRes = await api.get('/consultants')
          setVivier(vivierRes.data)
        } catch { /* non bloquant */ }

        if (!isAdmin) {
          if (subs.length > 0) {
            try {
              const cached = await api.get(`/matching/results/${id}`)
              if (cached.data.results?.length > 0) setMatchResults(cached.data.results)
            } catch { /* scoring not run yet */ }
          }
        }

        if (isAdmin && subs.length > 0) {
          setLoading(false)

          // SERVEUR D'ABORD : /matching/results est une simple RELECTURE en base
          // (aucun appel LLM), et c'est la seule source qui porte le classement
          // HUMAIN (tri par human_rank) et les statuts de contact. On la privilégie
          // donc toujours, pour que l'état repris à la main survive au rechargement.
          // Le cache de session ne sert QUE de repli hors-ligne (serveur injoignable).
          let served = null
          try {
            const cached = await api.get(`/matching/results/${id}`)
            served = cached.data.results || []
          } catch { served = null }

          if (served && served.length) {
            setMatchResults(served)
            writeMatchCache(id, served)
            await loadDecisions()
            return
          }
          if (served === null) {
            // Relecture serveur en échec → repli sur le dernier scoring en cache.
            const sessionCached = readMatchCache(id)
            if (sessionCached) {
              setMatchResults(sessionCached.results)
              setAllScores(sessionCached.allScores || null)
              return
            }
          }

          // served === [] (aucun matching en base) → premier scoring de cet AO.
          await runMatching()
          return
        }
      } catch {
        navigate('/aos')
      } finally {
        setLoading(false)
      }
    }
    init()
  }, [id, isAdmin])

  // Assistant deep-link: open the "propose consultant" modal for partners.
  useEffect(() => {
    if (!isAdmin && location.state?.openSubmit) {
      setShowSubmitModal(true)
      navigate(location.pathname, { replace: true, state: {} })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin])

  // Résumé IA en 1 phrase : si l'AO n'en a pas encore (créé avant la feature),
  // on le génère à la volée côté staff. Best-effort, non bloquant.
  useEffect(() => {
    if (!ao || !isAdmin || ao.ai_summary || summary) return
    let cancelled = false
    api.post(`/aos/${id}/summary`)
      .then(r => { if (!cancelled) setSummary(r.data.ai_summary || '') })
      .catch(() => {})
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ao, isAdmin])

  const handleRerunMatch = () => runMatching()

  const handleDelete = async () => {
    if (!(await confirm({
      title: "Supprimer cet appel d'offres ?",
      message: 'L\'AO et ses données associées seront supprimés définitivement.',
      confirmLabel: 'Supprimer',
    }))) return
    try {
      await api.delete(`/aos/${id}`)
      navigate('/aos')
    } catch (e) {
      // Action destructive : un échec silencieux laissait l'utilisateur croire
      // que la suppression avait eu lieu (ou ne rien comprendre).
      alert(e.response?.data?.detail || "La suppression de l'AO a échoué. Réessayez.")
    }
  }

  const handleDeleteSubmission = async (sid) => {
    if (!(await confirm({
      title: 'Retirer cette soumission ?',
      message: 'Le CV soumis sera retiré de cet AO.',
      confirmLabel: 'Retirer',
    }))) return
    try {
      await api.delete(`/submissions/${sid}`)
      setSubmissions(p => p.filter(s => s.id !== sid))
      clearMatchCache(id)  // le classement en cache n'est plus à jour
    } catch (e) {
      alert(e.response?.data?.detail || 'Le retrait de la soumission a échoué. Réessayez.')
    }
  }

  const handleSubmissionSuccess = async () => {
    setShowSubmitModal(false)
    await Promise.all([fetchSubmissions(), fetchAo()])
    if (isAdmin) await runMatching()
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-brand-400" />
      </div>
    )
  }
  if (!ao) return null

  return (
    <div className="animate-slide-up">
      {/* Header */}
      <div className="flex items-start gap-3 mb-5">
        <button onClick={() => navigate('/aos')} className="btn-ghost p-2 mt-0.5">
          <ArrowLeft size={16} />
        </button>
        <div className="flex-1">
          {ao.clients?.name && (
            <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-1 flex items-center gap-1">
              <Building2 size={11} className="text-brand-400" /> {ao.clients.name}
              {ao.clients.sector && <span className="text-slate-600">· {ao.clients.sector}</span>}
            </div>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-bold text-white">{ao.title}</h1>
            <span className={clsx('badge', ao.status === 'open'
              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
              : 'bg-slate-500/10 text-slate-400')}>
              {ao.status === 'open' ? 'Ouvert' : 'Fermé'}
            </span>
            {ao.ao_type && (
              <span className="badge bg-violet-500/10 text-violet-300 border border-violet-500/20 text-xs">
                {ao.ao_type}
              </span>
            )}
          </div>
          {summary && (
            <p className="text-sm text-slate-400 mt-1.5 flex items-start gap-1.5">
              <Sparkles size={13} className="text-violet-400 shrink-0 mt-0.5" />
              <span className="italic">{summary}</span>
            </p>
          )}
        </div>
        {isAdmin && (
          <div className="flex items-center gap-1">
            <button onClick={() => setShowEditModal(true)} className="btn-ghost p-2" title="Modifier">
              <Pencil size={15} />
            </button>
            <button onClick={handleDelete} className="btn-danger p-2" title="Supprimer">
              <Trash2 size={15} />
            </button>
          </div>
        )}
      </div>

      {/* Deadline — big & red */}
      <DeadlineBanner deadline={ao.deadline} />

      {/* Onglets : Présentation (la fiche) / Analyse & CV (matching) */}
      <div className="flex items-center gap-1 mb-5 border-b border-white/10">
        {[
          { key: 'presentation', label: 'Présentation', icon: FileText },
          ...(isAdmin ? [{ key: 'envoi', label: 'Envoi des e-mails', icon: Send }] : []),
          { key: 'analyse', label: isAdmin ? 'Analyse & CV' : 'Ma candidature', icon: BarChart3 },
          ...(isAdmin ? [{ key: 'validation', label: 'Validation CV', icon: CheckCircle }] : []),
        ].map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={clsx(
              'flex items-center gap-1.5 px-3.5 py-2 text-sm font-medium -mb-px border-b-2 transition-colors',
              tab === t.key
                ? 'border-brand-400 text-white'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            )}
          >
            <t.icon size={14} /> {t.label}
            {t.key === 'analyse' && (ao.submission_count ?? submissions.length) > 0 && (
              <span className="ml-0.5 text-[11px] rounded-full px-1.5 py-0.5 bg-white/5 text-slate-400">
                {ao.submission_count ?? submissions.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Onglet Présentation : infos clés */}
      {tab === 'presentation' && (
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-5">
        {ao.reference && (
          <div className="card p-4 flex flex-col gap-1">
            <span className="text-xs text-slate-500 flex items-center gap-1"><Hash size={11} className="text-brand-400" />Référence</span>
            <span className="text-sm font-bold text-white leading-tight break-words">{ao.reference}</span>
          </div>
        )}
        {ao.budget_max && (
          <div className="card p-4 flex flex-col gap-1">
            <span className="text-xs text-slate-500 flex items-center gap-1"><Euro size={11} className="text-emerald-400" />Budget max</span>
            <span className="text-lg font-bold text-white">{ao.budget_max}€<span className="text-sm font-normal text-slate-400">/j</span></span>
          </div>
        )}
        {ao.location && (
          <div className="card p-4 flex flex-col gap-1">
            <span className="text-xs text-slate-500 flex items-center gap-1"><MapPin size={11} className="text-brand-400" />Localisation</span>
            <span className="text-lg font-bold text-white leading-tight">{ao.location}</span>
          </div>
        )}
        {ao.duration && (
          <div className="card p-4 flex flex-col gap-1">
            <span className="text-xs text-slate-500 flex items-center gap-1"><Clock size={11} className="text-amber-400" />Durée</span>
            <span className="text-lg font-bold text-white">{ao.duration}</span>
          </div>
        )}
        {ao.langue_requise && (
          <div className="card p-4 flex flex-col gap-1">
            <span className="text-xs text-slate-500 flex items-center gap-1"><Languages size={11} className="text-violet-400" />Langue requise</span>
            <span className="text-lg font-bold text-white leading-tight">{ao.langue_requise}</span>
          </div>
        )}
        <div className="card p-4 flex flex-col gap-1">
          <span className="text-xs text-slate-500 flex items-center gap-1"><Users size={11} className="text-blue-400" />CVs soumis</span>
          <span className="text-lg font-bold text-white">
            {ao.submission_count ?? submissions.length}
            <span className="text-sm font-normal text-slate-400"> reçus</span>
          </span>
        </div>
        {ao.created_at && (
          <div className="card p-4 flex flex-col gap-1">
            <span className="text-xs text-slate-500 flex items-center gap-1"><Calendar size={11} className="text-slate-400" />Date d'ajout</span>
            <span className="text-sm font-semibold text-white leading-tight">{formatDate(ao.created_at)}</span>
          </div>
        )}
      </div>
      )}

      {/* ── Onglet Envoi des e-mails : diffusion aux partenaires + couverture (staff) ── */}
      {tab === 'envoi' && isAdmin && (
      <div className="flex flex-col gap-4 mb-5">
        <div className="card p-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-white flex items-center gap-2">
                <Send size={15} className="text-brand-400" /> Diffusion aux partenaires
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                {ao.notified_at
                  ? <>Partenaires notifiés le {formatDate(ao.notified_at)}
                      {ao.list2_notified_at
                        ? ` · liste 2 envoyée le ${formatDate(ao.list2_notified_at)}`
                        : ao.list2_scheduled_at
                          ? ` · liste 2 prévue le ${formatDate(ao.list2_scheduled_at)}`
                          : ''}
                      {ao.relance_count ? ` · ${ao.relance_count} relance${ao.relance_count > 1 ? 's' : ''}` : ''}.</>
                  : "Aucune notification envoyée aux partenaires pour le moment."}
              </p>
              {notifMsg && <p className="text-xs text-brand-300 mt-1">{notifMsg}</p>}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <button onClick={handleNotify} disabled={notifBusy !== ''} className="btn-ghost">
                {notifBusy === 'notify' ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                {ao.notified_at ? 'Renvoyer aux partenaires' : 'Envoyer aux partenaires'}
              </button>
              <button onClick={openTarget} disabled={notifBusy !== ''} className="btn-ghost" title="Renvoyer à des partenaires précis (sans toucher les autres)">
                <UserCircle2 size={14} /> Renvoyer à un partenaire
              </button>
              {ao.notified_at && (
                <button onClick={handleRelance} disabled={notifBusy !== ''} className="btn-primary">
                  {notifBusy === 'relance' ? <Loader2 size={14} className="animate-spin" /> : <Bell size={14} />}
                  Relancer
                </button>
              )}
            </div>
          </div>

          {/* Renvoi ciblé : sélection de partenaires éligibles */}
          {targetOpen && (
            <div className="mt-3 pt-3 border-t border-white/10">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-semibold text-slate-300">Renvoyer à des partenaires précis</p>
                <button onClick={() => setTargetOpen(false)} className="text-slate-500 hover:text-slate-300"><X size={14} /></button>
              </div>
              {eligible === null ? (
                <div className="py-4 text-center"><Loader2 size={16} className="animate-spin inline text-slate-500" /></div>
              ) : eligible.length === 0 ? (
                <p className="text-xs text-slate-500 py-2">Aucun partenaire en liste 1/2 sur ce client.</p>
              ) : (
                <>
                  <div className="space-y-1 max-h-56 overflow-y-auto">
                    {eligible.map(p => (
                      <label key={p.id} className="flex items-center gap-2.5 px-2 py-1.5 rounded-md hover:bg-white/5 cursor-pointer">
                        <input type="checkbox" checked={selectedPartners.has(p.id)} onChange={() => togglePartner(p.id)} />
                        <span className="flex-1 min-w-0">
                          <span className="text-[13px] text-white">{p.name || p.email}</span>
                          <span className="text-[11px] text-slate-500 ml-2">{p.tier === 'list_1' ? 'Liste 1' : 'Liste 2'}{p.has_submitted ? ' · a déjà répondu' : ''}{p.blocked ? ' · compte bloqué' : ''}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                  <div className="flex items-center justify-end gap-2 mt-2">
                    <button onClick={() => setTargetOpen(false)} className="btn-ghost text-xs">Annuler</button>
                    <button onClick={sendTarget} disabled={targetBusy || selectedPartners.size === 0} className="btn-primary text-xs">
                      {targetBusy ? <><Loader2 size={13} className="animate-spin" /> Envoi…</> : `Renvoyer (${selectedPartners.size})`}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        {/* Couverture de l'AO (qui peut répondre / qui a répondu) */}
        <AOInsightsChart aoId={id} />
      </div>
      )}

      {/* ── Onglet Analyse & CV : top profils, CVs, couverture, diffusion (ordre adaptatif) ── */}
      {tab === 'validation' && isAdmin && (
        <div className="mb-5">
          <ValidationTab aoId={id} submissions={submissions} clientId={ao.client_id}
            scores={(matchResults || []).reduce((m, r) => {
              if (r.consultant_id != null) m[r.consultant_id] = r.score_hybride ?? r.score_total
              return m
            }, {})} />
        </div>
      )}

      {tab === 'analyse' && (
      <div className="flex flex-col gap-4 mb-5">
          {/* Partner view: submit a CV */}
          {!isAdmin && (
            <div className="card p-4">
              <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                  <p className="text-sm font-semibold text-white flex items-center gap-2">
                    <Briefcase size={15} className="text-brand-400" />
                    Proposer un consultant
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {submissions.length === 0
                      ? "Vous n'avez encore soumis aucun CV à cet AO."
                      : `Vous avez soumis ${submissions.length} CV${submissions.length > 1 ? 's' : ''}.`}
                  </p>
                </div>
                <button onClick={() => setShowSubmitModal(true)} className="btn-primary">
                  <Plus size={15} /> Soumettre un CV
                </button>
              </div>
            </div>
          )}

          {/* La diffusion e-mails aux partenaires est dans l'onglet « Envoi des e-mails ». */}

          {/* Submissions list — repliable */}
          {submissions.length > 0 && (
            <div className="card p-4 order-2">
              <button type="button" onClick={toggleCvs} className="w-full flex items-center justify-between text-left">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                  {isAdmin ? `Tous les CVs reçus (${submissions.length})` : `Vos soumissions (${submissions.length})`}
                </span>
                {cvsOpen ? <ChevronUp size={15} className="text-slate-500" /> : <ChevronDown size={15} className="text-slate-500" />}
              </button>
              {cvsOpen ? (
                <div className="space-y-2 mt-3">
                  {submissions.map(s => (
                    <SubmissionRow
                      key={s.id}
                      sub={s}
                      canDelete={isAdminRole || s.submitted_by === user.id}
                      onDelete={handleDeleteSubmission}
                      isAdmin={isAdmin}
                      aoSkillsRequired={ao.skills_required}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-xs text-slate-500 mt-1">
                  Repliée : cliquez pour afficher les {submissions.length} CV{submissions.length > 1 ? 's' : ''}.
                </p>
              )}
            </div>
          )}

          {/* Partner: own match scores */}
          {!isAdmin && submissions.length > 0 && (
            matchResults && matchResults.length > 0 ? (
              <div className="card p-4">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3 flex items-center gap-1.5">
                  <TrendingUp size={12} className="text-brand-400" /> Vos scores IA
                </p>
                <MatchCarousel results={matchResults} aoId={id} ao={ao} />
              </div>
            ) : (
              <div className="card p-4 border-dashed border-white/10 text-center">
                <TrendingUp size={22} className="mx-auto text-slate-700 mb-2" />
                <p className="text-xs text-slate-500">Scoring IA en attente : l'administrateur analysera vos CVs prochainement</p>
              </div>
            )
          )}

          {/* Admin: matching results / controls — « je contacte qui ? » en tête */}
          {isAdmin && (
            <div className="order-1 flex flex-col gap-4">
              <div className="card p-4">
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <div>
                    <p className="text-sm font-semibold text-white flex items-center gap-2">
                      <Zap size={15} className="text-brand-400" />
                      Scoring automatique
                      {/* Tutoriel : comment lire & utiliser le matching CV. */}
                      <button onClick={() => setShowMatchTour(true)}
                              title="Comment lire ce matching ? Lancer le tutoriel"
                              className="h-6 w-6 rounded-md inline-flex items-center justify-center text-slate-400 hover:text-white hover:bg-white/10 transition-colors">
                        <HelpCircle size={15} strokeWidth={1.75} />
                      </button>
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      {submissions.length === 0
                        ? "En attente de soumissions de CVs"
                        : `Analyse automatique de ${submissions.length} CV${submissions.length > 1 ? 's' : ''} · Top ${Math.min(submissions.length, 5)}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => setShowSubmitModal(true)} className="btn-ghost gap-1.5">
                      <Plus size={14} /> Ajouter un CV
                    </button>
                    {submissions.length > 0 && (
                      <button onClick={handleRerunMatch} disabled={matching}
                              className={clsx('btn-ghost gap-2', matching && 'opacity-75')}>
                        {matching
                          ? <><Loader2 size={14} className="animate-spin" />Analyse...</>
                          : <><RotateCcw size={14} />Relancer</>}
                      </button>
                    )}
                  </div>
                </div>
                {matching && (
                  <div className="mt-3 p-3 bg-brand-500/5 border border-brand-500/15 rounded-lg">
                    <div className="flex items-center gap-2 text-xs text-brand-300">
                      <Loader2 size={12} className="animate-spin" />
                      Extraction IA + scoring déterministe en cours... 15–30 secondes.
                    </div>
                  </div>
                )}
                {matchError && (
                  <div className="mt-3 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                    {matchError}
                  </div>
                )}
              </div>

              {matchResults && matchResults.length > 0 ? (
                <MatchCarousel results={matchResults} aoId={id} isAdmin ao={ao} decisions={decisions} />
              ) : !matching && submissions.length === 0 ? (
                <div className="card p-8 text-center border-dashed border-white/10">
                  <Users size={32} className="mx-auto text-slate-700 mb-3" />
                  <p className="text-slate-400 text-sm">Aucun CV n'a encore été soumis</p>
                  <p className="text-xs text-slate-600 mt-1">Le scoring se lancera automatiquement dès la première soumission</p>
                </div>
              ) : null}

              {/* Analyses : distribution + classement complet de tous les scores */}
              {allScores && allScores.length > 1 && <ScoreAnalytics all={allScores} isAdmin={isAdmin} />}
              {matchResults && matchResults.length > 0 && !allScores && (
                <p className="text-[11px] text-center" style={{ color: 'var(--text-faint)' }}>
                  Cliquez sur « Relancer » pour afficher l'analyse complète des scores.
                </p>
              )}
            </div>
          )}
      </div>
      )}

      {/* ── Onglet Présentation : description, contexte, compétences ── */}
      {tab === 'presentation' && (
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-4">
          {/* Description repliable */}
          <div className="card p-4">
            <button type="button" onClick={() => setDescOpen(o => !o)}
              className="w-full flex items-center justify-between text-left">
              <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Description</span>
              {descOpen ? <ChevronUp size={15} className="text-slate-500" /> : <ChevronDown size={15} className="text-slate-500" />}
            </button>
            {descOpen
              ? <p className="text-sm text-slate-300 leading-relaxed mt-2 whitespace-pre-line">{ao.description}</p>
              : <p className="text-xs text-slate-500 mt-1 truncate">{ao.description}</p>}
          </div>
          {ao.context && (
            <div className="card p-4">
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Contexte additionnel</p>
              <p className="text-xs text-slate-400 leading-relaxed whitespace-pre-line">{ao.context}</p>
            </div>
          )}
        </div>
        <div className="space-y-4">
          <div className="card p-4">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Compétences requises</p>
            <div className="flex flex-wrap gap-1.5">
              {ao.skills_required?.split(',').map((s, i) => (
                <span key={i} className="badge bg-brand-600/10 text-brand-300 border border-brand-500/15 text-[10px]">
                  {s.trim()}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
      )}

      {showSubmitModal && (
        <SubmitModal aoId={id} vivier={vivier} prefill={submitPrefill} clientName={ao.clients?.name}
          onClose={() => { setShowSubmitModal(false); setSubmitPrefill(null) }}
          onSubmitted={handleSubmissionSuccess} />
      )}
      {showEditModal && (
        <AOEditModal
          ao={ao}
          onClose={() => setShowEditModal(false)}
          onSaved={async () => {
            setShowEditModal(false)
            await fetchAo()
            // Une modif d'AO (compétences, priorités, budget…) peut changer les
            // scores → on invalide le cache et on re-score, mais SEULEMENT ici,
            // pas à chaque ouverture.
            clearMatchCache(id)
            if (isAdmin && submissions.length > 0) await runMatching()
          }}
        />
      )}
      {showMatchTour && (
        <OnboardingTour steps={MATCH_TOUR_STEPS} onClose={() => setShowMatchTour(false)} />
      )}
    </div>
  )
}
