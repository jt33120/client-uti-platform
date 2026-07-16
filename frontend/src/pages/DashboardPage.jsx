import { useState, useEffect, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import api from '../lib/api'
import {
  Users, FileText, Plus, ArrowRight, Building2, UserPlus, Sparkles,
  Briefcase, Layers, Zap, Award, BarChart3, CalendarClock, AlertTriangle,
  Megaphone, Inbox, Hourglass,
} from 'lucide-react'
import InviteModal from '../components/InviteModal'
import UTILoader, { ChartLoader } from '../components/UTILoader'
import { ChartCard, EmptyHint, Donut, Legend, VBars, HBars, BRAND, NEUTRAL } from '../components/charts'

const parseSkills = (s) => (s || '').split(/[,;/]+/).map(x => x.trim()).filter(Boolean)

// File staff « À traiter » — kinds actionnables du feed /notifications/feed.
// Doit rester aligné sur les kinds servis par le backend (notifications.py).
const TASK_KINDS = ['ao_undiffused', 'cv_untreated', 'stale_presentation']
const TASK_ICON = { ao_undiffused: Megaphone, cv_untreated: Inbox, stale_presentation: Hourglass }

// KPI — frameless. A number, a quiet label, a monochrome glyph. Separation
// comes from a hairline divider on wide screens, not a box around each one.
function Kpi({ icon: Icon, label, value, sub, to }) {
  const inner = (
    <div className="flex flex-col gap-1.5 lg:px-5 lg:border-l lg:first:border-l-0 lg:first:pl-0 border-[color:var(--border)] group">
      <div className="flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
        <Icon size={14} strokeWidth={2} />
        <span className="text-[11px] uppercase tracking-[0.07em] font-semibold">{label}</span>
        {to && <ArrowRight size={12} strokeWidth={2} className="opacity-0 group-hover:opacity-100 transition-opacity -ml-0.5" />}
      </div>
      <div className="text-[30px] font-semibold tabular leading-none" style={{ color: 'var(--text)' }}>{value ?? '—'}</div>
      {sub && <div className="text-[11px]" style={{ color: 'var(--text-faint)' }}>{sub}</div>}
    </div>
  )
  return to ? <Link to={to} className="block">{inner}</Link> : inner
}

// Quick action — single hairline row, monochrome glyph. The accent appears
// only on hover, so the resting state stays calm.
function QuickAction({ to, onClick, icon: Icon, title, desc }) {
  const inner = (
    <div className="flex items-center gap-3 py-2.5 transition-colors group cursor-pointer">
      <div
        className="w-8 h-8 rounded-md flex items-center justify-center shrink-0 transition-colors"
        style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}
      >
        <Icon size={15} strokeWidth={2} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>{title}</div>
        <div className="text-[11px] truncate" style={{ color: 'var(--text-faint)' }}>{desc}</div>
      </div>
      <ArrowRight
        size={14} strokeWidth={1.75}
        className="opacity-40 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all"
        style={{ color: 'var(--accent-text)' }}
      />
    </div>
  )
  if (to) return <Link to={to}>{inner}</Link>
  return <button onClick={onClick} className="text-left w-full">{inner}</button>
}

// Résumé « avancement des AO » (staff) — les AO actifs comptés par étape du
// pipeline. Auto-portant ; masqué s'il n'y a aucun AO actif.
function AoPipelineSummary() {
  const [data, setData] = useState(null)
  useEffect(() => {
    let c = false
    api.get('/aos/pipeline').then(r => { if (!c) setData(r.data) }).catch(() => { if (!c) setData(false) })
    return () => { c = true }
  }, [])
  if (!data || !data.stages) return null
  // On ne montre que les étapes ACTIVES : Gagné/Perdu sont cumulatifs (historique)
  // et gonfleraient un « snapshot » d'avancement — ils vivent dans la Supervision.
  const active = data.stages.filter(s => s.key !== 'gagne' && s.key !== 'perdu')
  const counts = {}
  active.forEach(s => { counts[s.key] = 0 })
  ;(data.aos || []).forEach(a => { if (counts[a.stage] !== undefined) counts[a.stage] += 1 })
  const activeTotal = active.reduce((n, s) => n + (counts[s.key] || 0), 0)
  if (!activeTotal) return null
  return (
    <div className="pt-7" style={{ borderTop: '1px solid var(--border)' }}>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-[11px] uppercase tracking-[0.08em] font-semibold" style={{ color: 'var(--text-faint)' }}>AO en cours</h2>
        <Link to="/aos" className="text-[12px] font-medium" style={{ color: 'var(--accent-text)' }}>Voir le pipeline →</Link>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-y-4">
        {active.map(s => (
          <Link key={s.key} to="/aos" className="block hover:opacity-80 transition-opacity">
            <div className="text-[22px] font-semibold tabular leading-tight" style={{ color: 'var(--text)' }}>{counts[s.key] || 0}</div>
            <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-faint)' }}>{s.label}</div>
          </Link>
        ))}
      </div>
    </div>
  )
}

export default function DashboardPage() {
  const { user, isAdmin, isCommerce, isStaff } = useAuth()
  const navigate = useNavigate()
  const [inviteOpen, setInviteOpen] = useState(false)
  const [staffTasks, setStaffTasks] = useState([])
  const [consultants, setConsultants] = useState([])
  const [aos, setAos] = useState([])
  const [clients, setClients] = useState([])
  const [ai, setAi] = useState({ matchings: null, model: null })
  const [submissions, setSubmissions] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    const settle = (p) => p.then(r => ({ ok: true, data: r.data })).catch(() => ({ ok: false, data: null }))
    const run = async () => {
      setLoadError(false)
      const [c, a, cl, subs, m] = await Promise.all([
        settle(api.get('/consultants')),
        settle(api.get('/aos')),
        settle(api.get('/clients')),
        isStaff ? Promise.resolve({ ok: false, skipped: true }) : settle(api.get('/submissions/mine')),
        isStaff ? settle(api.get('/matching/stats')) : Promise.resolve({ ok: false, skipped: true }),
      ])
      if (c.ok) setConsultants(c.data)
      if (a.ok) setAos(a.data)
      if (cl.ok) setClients(cl.data)
      if (subs.ok) setSubmissions(subs.data.length)
      // Le coût IA n'est volontairement pas exposé ici : ce n'est pas une
      // métrique commerciale. Il est réservé aux admins (page /admin).
      if (m.ok) setAi({ matchings: m.data.total_matchings, model: m.data.extraction_model, aosMatched: m.data.aos_matched })
      // Un des blocs de base a échoué → le dire, sinon les « 0 » et donuts
      // vides se lisent comme des vraies valeurs.
      if (!c.ok || !a.ok || !cl.ok) setLoadError(true)
      setLoading(false)
    }
    run()
  }, [isStaff, reloadKey])

  // File staff « À traiter » — items actionnables du feed. Best-effort : si
  // l'endpoint n'est pas déployé ou échoue, la liste reste vide (section masquée).
  useEffect(() => {
    if (!isStaff) { setStaffTasks([]); return }
    let c = false
    api.get('/notifications/feed')
      .then(r => { if (!c) setStaffTasks((r.data?.items || []).filter(it => TASK_KINDS.includes(it.kind))) })
      .catch(() => { if (!c) setStaffTasks([]) })
    return () => { c = true }
  }, [isStaff, reloadKey])

  const d = useMemo(() => {
    const open = aos.filter(a => a.status === 'open').length
    // Status reads as a duotone: brand = active, neutral = the rest.
    const aoStatus = [
      { name: 'Ouverts', value: open, color: BRAND },
      { name: 'Fermés', value: aos.length - open, color: NEUTRAL },
    ].filter(x => x.value > 0)

    const typeMap = {}
    aos.forEach(a => { const t = a.ao_type || 'Non typé'; typeMap[t] = (typeMap[t] || 0) + 1 })
    const aoTypes = Object.entries(typeMap).map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value)

    const secMap = {}
    clients.forEach(c => { const s = c.sector || 'Autre'; secMap[s] = (secMap[s] || 0) + 1 })
    const sectors = Object.entries(secMap).map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value)

    const b = [['0-2 ans', 0], ['3-5 ans', 0], ['6-9 ans', 0], ['10+ ans', 0]]
    consultants.forEach(c => {
      const y = c.experience_years || 0
      if (y <= 2) b[0][1]++; else if (y <= 5) b[1][1]++; else if (y <= 9) b[2][1]++; else b[3][1]++
    })
    const seniority = b.map(([name, value]) => ({ name, value }))

    const skillMap = {}
    consultants.forEach(c => parseSkills(c.skills).forEach(s => { skillMap[s] = (skillMap[s] || 0) + 1 }))
    aos.forEach(a => parseSkills(a.skills_required).forEach(s => { skillMap[s] = (skillMap[s] || 0) + 1 }))
    const topSkills = Object.entries(skillMap)
      .map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value).slice(0, 7).reverse()

    const tjms = consultants.map(c => c.tjm).filter(Boolean)
    const avgTjm = tjms.length ? Math.round(tjms.reduce((x, y) => x + y, 0) / tjms.length) : null

    // Nb d'organisations clientes (racines) — cohérent avec la page Clients,
    // qui regroupe les périmètres sous leur organisation parente.
    const clientOrgs = clients.filter(c => !c.parent_client_id).length

    return { open, aoStatus, aoTypes, sectors, seniority, topSkills, avgTjm, clientOrgs }
  }, [aos, consultants, clients])

  const recentAOs = aos.slice(0, 5)
  const hairline = { borderTop: '1px solid var(--border)' }

  // AO urgents : ouverts, non archivés/brouillons, échéance dans ≤ 3 jours.
  // Calculé sur les AO déjà chargés (aucun appel supplémentaire).
  const urgentAos = useMemo(() => {
    const today = new Date(); today.setHours(0, 0, 0, 0)
    const horizon = new Date(today); horizon.setDate(horizon.getDate() + 3)
    return (aos || [])
      .filter(a => a.status === 'open' && !a.archived && !a.is_draft && a.deadline)
      .map(a => { const dl = new Date(a.deadline); dl.setHours(0, 0, 0, 0); return { ...a, _dl: dl } })
      .filter(a => !Number.isNaN(a._dl.getTime()) && a._dl >= today && a._dl <= horizon)
      .sort((a, b) => a._dl - b._dl)
      .map(a => ({ ...a, _days: Math.round((a._dl - today) / 86400000) }))
  }, [aos])

  return (
    <div>
      {/* Hero — greeting only. The IA figure lives in its own KPI below,
          so no duplicate badge here. */}
      <div className="mb-8">
        <h1 className="text-[22px] font-semibold tracking-tightest" style={{ color: 'var(--text)' }}>
          Bonjour, {user?.name?.split(' ')[0]}
        </h1>
        <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
          {isStaff ? "Pilotez vos appels d'offres et le scoring IA en un coup d'œil." : 'Soumettez des consultants et suivez les appels d\'offres.'}
        </p>
      </div>

      {/* Alerte AO urgents (échéance ≤ 3 j) — la première chose qu'on voit. */}
      {!loading && urgentAos.length > 0 && (
        <div className="mb-8 rounded-xl overflow-hidden"
             style={{ border: '1px solid rgba(245,158,11,0.35)', background: 'rgba(245,158,11,0.06)' }}>
          <div className="flex items-center gap-2 px-4 py-2.5" style={{ borderBottom: '1px solid rgba(245,158,11,0.20)' }}>
            <AlertTriangle size={15} className="text-amber-400 shrink-0" />
            <span className="text-[13px] font-semibold" style={{ color: 'var(--text)' }}>
              {urgentAos.length} appel{urgentAos.length > 1 ? 's' : ''} d'offres à échéance imminente
            </span>
            <span className="text-[11px]" style={{ color: 'var(--text-faint)' }}>· dans 3 jours ou moins</span>
          </div>
          <ul className="divide-y" style={{ borderColor: 'var(--border)' }}>
            {urgentAos.slice(0, 5).map(a => {
              const d0 = a._days <= 0
              const label = d0 ? "aujourd'hui" : a._days === 1 ? 'demain' : `dans ${a._days} j`
              return (
                <li key={a.id}>
                  <Link to={`/aos/${a.id}`}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--surface-2)] transition-colors">
                    <CalendarClock size={14} className={d0 ? 'text-red-400 shrink-0' : 'text-amber-400 shrink-0'} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium truncate" style={{ color: 'var(--text)' }}>{a.title}</span>
                      {a.clients?.name && <span className="block text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>{a.clients.name}</span>}
                    </span>
                    <span className="text-[11px] font-semibold shrink-0 px-2 py-0.5 rounded-full"
                          style={d0 ? { background: 'rgba(239,68,68,0.12)', color: '#ef4444' } : { background: 'rgba(245,158,11,0.12)', color: '#f59e0b' }}>
                      {label}
                    </span>
                    <ArrowRight size={13} className="text-slate-600 shrink-0" />
                  </Link>
                </li>
              )
            })}
          </ul>
          {urgentAos.length > 5 && (
            <Link to="/aos" className="block px-4 py-2 text-[11px] font-medium hover:bg-[var(--surface-2)]" style={{ color: 'var(--accent-text)' }}>
              +{urgentAos.length - 5} autre{urgentAos.length - 5 > 1 ? 's' : ''} → voir tous les AO
            </Link>
          )}
        </div>
      )}

      {/* File staff « À traiter » — items actionnables (AO non diffusés, CV en
          attente, présentations dormantes). Masquée si vide ou en erreur. */}
      {isStaff && !loading && staffTasks.length > 0 && (
        <div className="mb-8 rounded-xl overflow-hidden"
             style={{ border: '1px solid rgba(245,158,11,0.35)', background: 'rgba(245,158,11,0.06)' }}>
          <div className="flex items-center gap-2 px-4 py-2.5" style={{ borderBottom: '1px solid rgba(245,158,11,0.20)' }}>
            <Inbox size={15} className="text-amber-400 shrink-0" />
            <span className="text-[13px] font-semibold" style={{ color: 'var(--text)' }}>
              {staffTasks.length} élément{staffTasks.length > 1 ? 's' : ''} à traiter
            </span>
          </div>
          <ul className="divide-y" style={{ borderColor: 'var(--border)' }}>
            {staffTasks.map(it => {
              const Icon = TASK_ICON[it.kind] || AlertTriangle
              return (
                <li key={it.id}>
                  <button
                    onClick={() => { if (it.link) navigate(it.link) }}
                    className="w-full text-left flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--surface-2)] transition-colors">
                    <Icon size={14} className="text-amber-400 shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium truncate" style={{ color: 'var(--text)' }}>{it.title}</span>
                      {it.subtitle && <span className="block text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>{it.subtitle}</span>}
                    </span>
                    <ArrowRight size={13} className="text-slate-600 shrink-0" />
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {loadError && (
        <div className="mb-6 px-4 py-3 rounded-lg text-[13px] flex items-center justify-between gap-3"
          style={{ background: 'var(--surface-2)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
          <span>Certaines données n'ont pas pu être chargées — les chiffres affichés sont incomplets.</span>
          <button onClick={() => { setLoading(true); setReloadKey(k => k + 1) }} className="btn-ghost !h-7 !px-2.5 text-[12px] shrink-0">
            Réessayer
          </button>
        </div>
      )}

      {/* Stat band — no boxes. Numbers carry the weight; hairlines do the splitting.
          While data loads, a quiet shimmer holds each number's place. */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-6 pt-7 pb-8" style={hairline}>
        <Kpi icon={Users} label="Consultants" value={loading ? <span className="uti-skel" /> : consultants.length} to="/consultants"
          sub={!loading && d.avgTjm ? `TJM moy. ${d.avgTjm} €` : null} />
        <Kpi icon={Briefcase} label={isStaff ? "Appels d'offres" : 'Mes AOs'} value={loading ? <span className="uti-skel" /> : aos.length} to="/aos"
          sub={loading ? null : `${d.open} ouvert${d.open > 1 ? 's' : ''}`} />
        <Kpi icon={Building2} label="Clients" value={loading ? <span className="uti-skel" /> : d.clientOrgs} to="/clients"
          sub={!loading && d.sectors.length ? `${d.sectors.length} secteurs` : null} />
        {isStaff
          ? <Kpi icon={Sparkles} label="AOs avec profil" value={loading ? <span className="uti-skel" /> : (ai.aosMatched ?? 0)}
              sub={loading ? null : 'consultant potentiel trouvé'} to="/aos?matched=1" />
          : <Kpi icon={FileText} label="CVs soumis" value={loading ? <span className="uti-skel" /> : submissions} />}
      </div>

      {isStaff && <AoPipelineSummary />}

      {/* Analyse — frameless charts on the page surface, split by whitespace.
          The brand tone encodes magnitude, so colour informs, never decorates. */}
      <div className="pt-7" style={hairline}>
        <h2 className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-5" style={{ color: 'var(--text-faint)' }}>Analyse</h2>

        {/* Each plot carries its own loader while the data is in flight,
            sized to the chart it replaces so the grid never jumps. */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-x-8 gap-y-9 mb-9">
          <ChartCard title="Appels d'offres par type" icon={Layers} className="lg:col-span-2">
            {loading ? <ChartLoader /> : d.aoTypes.length ? <VBars data={d.aoTypes} /> : <EmptyHint />}
          </ChartCard>
          <ChartCard title="Statut des AO" icon={BarChart3}>
            {loading ? <ChartLoader height={212} /> : d.aoStatus.length ? <><Donut data={d.aoStatus} centerLabel="AO" /><Legend data={d.aoStatus} /></> : <EmptyHint />}
          </ChartCard>

          <ChartCard title="Top compétences demandées" icon={Zap}>
            {loading ? <ChartLoader height={200} /> : d.topSkills.length ? <HBars data={d.topSkills} /> : <EmptyHint />}
          </ChartCard>
          <ChartCard title="Séniorité du vivier" icon={Award}>
            {loading ? <ChartLoader /> : consultants.length ? <VBars data={d.seniority} /> : <EmptyHint />}
          </ChartCard>
          <ChartCard title="Clients par secteur" icon={Building2}>
            {loading ? <ChartLoader height={212} /> : d.sectors.length ? <><Donut data={d.sectors} centerLabel="clients" /><Legend data={d.sectors} /></> : <EmptyHint />}
          </ChartCard>
        </div>
      </div>

      {/* Recent + quick actions — a list earns its container (it groups rows
          that belong together); the shortcut column stays open. */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-x-8 gap-y-7 pt-7" style={hairline}>
        <div className="lg:col-span-2">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-[11px] uppercase tracking-[0.08em] font-semibold flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
              <FileText size={13} strokeWidth={2} /> Derniers appels d'offres
            </h2>
            <Link to="/aos" className="text-[12px] font-medium flex items-center gap-1 hover:underline" style={{ color: 'var(--accent-text)' }}>
              Voir tout <ArrowRight size={11} strokeWidth={2} />
            </Link>
          </div>
          {loading ? (
            <div className="py-10 flex justify-center">
              <UTILoader size={34} label="Chargement…" />
            </div>
          ) : recentAOs.length === 0 ? (
            <div className="py-10 text-center text-[13px]" style={{ color: 'var(--text-faint)' }}>Aucun appel d'offres pour le moment.</div>
          ) : (
            <ul>
              {recentAOs.map((ao) => (
                <li key={ao.id} style={hairline}>
                  <Link to={`/aos/${ao.id}`} className="flex items-center gap-3 h-12 px-1 -mx-1 rounded-md hover:bg-[var(--surface-2)] transition-colors group">
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-medium truncate" style={{ color: 'var(--text)' }}>{ao.title}</div>
                      <div className="text-[11px] truncate" style={{ color: 'var(--text-faint)' }}>{ao.skills_required}</div>
                    </div>
                    <div className="flex items-center gap-2.5 shrink-0">
                      {ao.budget_max && <span className="text-[11px] tabular" style={{ color: 'var(--text-muted)' }}>{ao.budget_max}€/j</span>}
                      <span className="badge" style={{
                        background: ao.status === 'open' ? 'var(--success-soft)' : 'var(--surface-2)',
                        color: ao.status === 'open' ? 'var(--success)' : 'var(--text-faint)',
                      }}>
                        <span className="w-1 h-1 rounded-full" style={{ background: 'currentColor' }} />
                        {ao.status === 'open' ? 'Ouvert' : 'Fermé'}
                      </span>
                      <ArrowRight size={12} strokeWidth={2} className="group-hover:translate-x-0.5 transition-transform" style={{ color: 'var(--text-faint)' }} />
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Quick actions */}
        <div>
          <h2 className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-1.5" style={{ color: 'var(--text-faint)' }}>Raccourcis</h2>
          <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
            {isStaff && <QuickAction to="/aos/new" icon={Plus} title="Nouvel appel d'offres" desc="IA : générer depuis un email" />}
            {isAdmin && <QuickAction to="/clients/new" icon={Building2} title="Nouveau client" desc="Créer un dossier client" />}
            {!isCommerce && <QuickAction to="/consultants/new" icon={Users} title="Ajouter un consultant" desc="Profil + CV PDF" />}
            {isAdmin && <QuickAction onClick={() => setInviteOpen(true)} icon={UserPlus} title="Inviter un compte" desc="Partenaire ou commercial UTI" />}
          </div>
        </div>
      </div>

      {inviteOpen && <InviteModal onClose={() => setInviteOpen(false)} />}
    </div>
  )
}
