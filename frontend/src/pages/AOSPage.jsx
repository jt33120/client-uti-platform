import { useState, useEffect, useMemo } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useConfirm } from '../contexts/ConfirmContext'
import {
  FileText, Plus, Euro, MapPin, Clock, ArrowRight, Search,
  Building2, Users, Calendar, CalendarClock,
  Pencil, X, Loader2, ChevronDown, ChevronLeft, ChevronRight, Check, Trash2, ArrowDownUp, Sparkles,
} from 'lucide-react'
import { TierBadge } from '../components/badges'
import { EmptyState } from '../components/EmptyState'

// Parse date-only strings as local to avoid the UTC off-by-one.
const parseDateLocal = (iso) => {
  if (!iso) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(iso)
}

const formatDate = (iso) => {
  const d = parseDateLocal(iso)
  if (!d) return null
  return new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' }).format(d)
}

// Échéance : date formatée + temps restant + tonalité (urgence).
const deadlineMeta = (iso) => {
  const d = parseDateLocal(iso)
  if (!d) return null
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const days = Math.round((d - today) / 86400000)
  let tone, rel
  if (days < 0) { tone = 'past'; rel = 'Dépassée' }
  else if (days === 0) { tone = 'today'; rel = "Aujourd'hui" }
  else if (days === 1) { tone = 'soon'; rel = 'Demain' }
  else if (days <= 7) { tone = 'soon'; rel = `Dans ${days} j` }
  else { tone = 'far'; rel = `Dans ${days} j` }
  return { date: formatDate(iso), days, tone, rel }
}

const DEADLINE_TONE = {
  past:  { background: 'var(--danger-soft)', color: 'var(--danger)' },
  today: { background: 'var(--danger-soft)', color: 'var(--danger)' },
  soon:  { background: 'rgba(245,158,11,0.14)', color: '#f59e0b' },
  far:   { background: 'rgba(99,102,241,0.12)', color: '#a5b4fc' },
}

const deadlineSortKey = (ao) => {
  const d = parseDateLocal(ao.deadline)
  return d ? d.getTime() : Infinity // AO sans échéance en dernier
}
import clsx from 'clsx'

// ── Edit modal ────────────────────────────────────────────────────────────────
function AOEditModal({ ao, onClose, onSaved }) {
  const AO_TYPES = ['Assurance', 'Banque / Finance', 'IT / Dev', 'Énergie', 'Retail', 'Public', 'Santé', 'Autre']
  const [clients, setClients] = useState([])
  const [form, setForm] = useState({
    client_id: ao.client_id || '',
    title: ao.title || '',
    description: ao.description || '',
    skills_required: ao.skills_required || '',
    budget_max: ao.budget_max?.toString() || '',
    location: ao.location || '',
    duration: ao.duration || '',
    context: ao.context || '',
    ao_type: ao.ao_type || '',
    deadline: ao.deadline || '',
    status: ao.status || 'open',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/clients').then(r => setClients(r.data)).catch(() => {})
  }, [])

  const set = (k) => (e) => setForm(p => ({ ...p, [k]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      const payload = { ...form }
      if (!payload.budget_max) delete payload.budget_max
      else payload.budget_max = parseInt(payload.budget_max)
      if (!payload.deadline) delete payload.deadline
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

// ── AO card ───────────────────────────────────────────────────────────────────
function AOCard({ ao, isStaff, onEdit, onDelete, navigate, selected, onToggleSelect }) {
  const isOpen = ao.status === 'open'
  return (
    <div
      className="card p-4 hover:border-white/10 transition-all duration-150 group cursor-pointer relative"
      style={selected ? { borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)' } : undefined}
      onClick={() => navigate(`/aos/${ao.id}`)}
    >
      {isStaff && (
        <button
          onClick={e => { e.stopPropagation(); onToggleSelect(ao.id) }}
          className={clsx(
            'absolute -top-2 -left-2 w-5 h-5 rounded-md flex items-center justify-center transition-all z-10',
            selected ? '' : 'opacity-0 group-hover:opacity-100'
          )}
          style={{
            background: selected ? 'var(--accent)' : 'var(--surface)',
            border: `1px solid ${selected ? 'var(--accent)' : 'var(--border-strong)'}`,
            color: '#fff',
          }}
          title={selected ? 'Désélectionner' : 'Sélectionner'}
        >
          {selected && <Check size={12} strokeWidth={3} />}
        </button>
      )}
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className="flex-1 min-w-0">
          {(ao.clients?.name || ao.reference) && (
            <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 flex items-center gap-1.5 flex-wrap">
              {ao.clients?.name && (
                <span className="inline-flex items-center gap-1"><Building2 size={9} /> {ao.clients.name}</span>
              )}
              {/* Référence à côté du client — demande Sullyvan (repérage quand 200 AO) */}
              {ao.reference && (
                <span className="normal-case text-slate-400 font-medium">
                  {ao.clients?.name && <span className="text-slate-600">· </span>}réf. {ao.reference}
                </span>
              )}
              {/* Date d'émission remontée près du client/réf (provenance) — demande Sullyvan (lisibilité) */}
              {ao.created_at && (
                <span className="normal-case text-slate-500 inline-flex items-center gap-1" title="Date d'émission de l'AO">
                  <span className="text-slate-600">·</span>
                  <Calendar size={9} /> Émis le {formatDate(ao.created_at)}
                </span>
              )}
            </div>
          )}
          <h3 className="text-sm font-semibold text-white group-hover:text-brand-300 transition-colors line-clamp-2">
            {ao.title}
          </h3>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className={clsx(
            'badge',
            isOpen
              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
              : 'bg-slate-500/10 text-slate-500 border border-slate-600/20'
          )}>
            {isOpen ? 'Ouvert' : 'Fermé'}
          </span>
          {ao.ao_type && (
            <span className="badge bg-violet-500/10 text-violet-300 border border-violet-500/20 text-[10px]">
              {ao.ao_type}
            </span>
          )}
          <TierBadge tier={ao.tier} />
        </div>
      </div>

      {/* Échéance : mise en évidence (date + temps restant) */}
      {(() => {
        const dl = deadlineMeta(ao.deadline)
        if (!dl) return null
        return (
          <div
            className="flex items-center gap-1.5 mb-2.5 px-2.5 py-1.5 rounded-md text-[11px] font-semibold"
            style={DEADLINE_TONE[dl.tone]}
            title={`Date d'échéance : ${dl.date}`}
          >
            <CalendarClock size={12} className="shrink-0" />
            <span>Échéance : {dl.date}</span>
            <span className="ml-auto font-medium opacity-90">{dl.rel}</span>
          </div>
        )
      })()}

      <p className="text-xs text-slate-500 mb-3 line-clamp-2">{ao.description}</p>

      <div className="flex flex-wrap gap-1.5 mb-3">
        {ao.skills_required?.split(',').slice(0, 3).map((s, i) => (
          <span key={i} className="badge bg-brand-600/10 text-brand-300 border border-brand-500/15 text-[10px]">
            {s.trim()}
          </span>
        ))}
        {ao.skills_required?.split(',').length > 3 && (
          <span className="badge bg-white/5 text-slate-500 text-[10px]">
            +{ao.skills_required.split(',').length - 3}
          </span>
        )}
      </div>

      <div className="flex items-center gap-3 text-xs text-slate-500 pt-2 border-t border-white/5">
        {ao.budget_max && (
          <span className="flex items-center gap-1">
            <Euro size={10} className="text-emerald-500" />
            {ao.budget_max}€/j
          </span>
        )}
        {ao.location && (
          <span className="flex items-center gap-1">
            <MapPin size={10} />
            {ao.location}
          </span>
        )}
        {ao.duration && (
          <span className="flex items-center gap-1">
            <Clock size={10} />
            {ao.duration}
          </span>
        )}
        {isStaff ? (
          <>
            <span className="flex items-center gap-1 ml-auto text-brand-300">
              <Users size={10} />
              {ao.submission_count ?? 0} CV{(ao.submission_count ?? 0) > 1 ? 's' : ''}
            </span>
            <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
              <button
                onClick={e => { e.stopPropagation(); onEdit(ao) }}
                className="btn-ghost p-1.5"
                title="Modifier"
              >
                <Pencil size={12} />
              </button>
              <button
                onClick={e => { e.stopPropagation(); onDelete(ao) }}
                className="btn-ghost p-1.5 hover:text-red-400"
                title="Supprimer"
              >
                <X size={13} />
              </button>
            </div>
          </>
        ) : (
          <ArrowRight size={12} className="text-slate-700 group-hover:text-brand-400 transition-colors ml-auto" />
        )}
      </div>
    </div>
  )
}

// ── AO table (vue dense — lisibilité quand beaucoup d'AO) ─────────────────────
// Une ligne par AO : client · réf · mission · émis le · lieu · échéance · statut.
// Pensé pour scanner rapidement 50+ AO là où les cartes deviennent trop longues.
function AOTable({ items, isStaff, navigate, onEdit, onDelete, selected, onToggleSelect, allSelected, onToggleAll }) {
  const th = 'font-medium px-4 py-2.5'
  return (
    <div className="card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wide"
              style={{ color: 'var(--text-faint)', borderBottom: '1px solid var(--border)' }}>
              {isStaff && (
                <th className="px-3 py-2.5 w-8">
                  <button
                    onClick={onToggleAll}
                    className="w-4 h-4 rounded flex items-center justify-center transition-all"
                    style={{
                      background: allSelected ? 'var(--accent)' : 'transparent',
                      border: `1px solid ${allSelected ? 'var(--accent)' : 'var(--border-strong)'}`,
                      color: '#fff',
                    }}
                    title={allSelected ? 'Tout désélectionner' : 'Tout sélectionner'}
                  >
                    {allSelected && <Check size={11} strokeWidth={3} />}
                  </button>
                </th>
              )}
              <th className={th}>Client</th>
              <th className={clsx(th, 'hidden lg:table-cell')}>Réf.</th>
              <th className={th}>Mission</th>
              <th className={clsx(th, 'hidden md:table-cell')}>Émis le</th>
              <th className={clsx(th, 'hidden xl:table-cell')}>Lieu</th>
              <th className={th}>Échéance</th>
              <th className={th}>Statut</th>
              {isStaff && <th className="px-4 py-2.5" />}
            </tr>
          </thead>
          <tbody>
            {items.map(ao => {
              const isOpen = ao.status === 'open'
              const dl = deadlineMeta(ao.deadline)
              const isSel = selected.has(ao.id)
              return (
                <tr
                  key={ao.id}
                  onClick={() => navigate(`/aos/${ao.id}`)}
                  className="group cursor-pointer transition-colors hover:bg-[var(--surface-2)]"
                  style={{
                    borderBottom: '1px solid var(--border)',
                    ...(isSel ? { background: 'var(--accent-soft)' } : {}),
                  }}
                >
                  {isStaff && (
                    <td className="px-3 py-2.5" onClick={e => e.stopPropagation()}>
                      <button
                        onClick={() => onToggleSelect(ao.id)}
                        className="w-4 h-4 rounded flex items-center justify-center transition-all"
                        style={{
                          background: isSel ? 'var(--accent)' : 'transparent',
                          border: `1px solid ${isSel ? 'var(--accent)' : 'var(--border-strong)'}`,
                          color: '#fff',
                        }}
                        title={isSel ? 'Désélectionner' : 'Sélectionner'}
                      >
                        {isSel && <Check size={11} strokeWidth={3} />}
                      </button>
                    </td>
                  )}
                  <td className="px-4 py-2.5 whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                    <span className="inline-flex items-center gap-1.5">
                      <Building2 size={11} className="shrink-0 text-slate-500" />
                      {ao.clients?.name || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 hidden lg:table-cell tabular whitespace-nowrap" style={{ color: 'var(--text-faint)' }}>
                    {ao.reference || '—'}
                  </td>
                  <td className="px-4 py-2.5 font-medium" style={{ color: 'var(--text)' }}>
                    <div className="flex items-center gap-2 min-w-[220px]">
                      <span className="line-clamp-1 group-hover:text-brand-300 transition-colors">{ao.title}</span>
                      {ao.ao_type && (
                        <span className="badge bg-violet-500/10 text-violet-300 border border-violet-500/20 text-[10px] shrink-0 hidden sm:inline-flex">
                          {ao.ao_type}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2.5 hidden md:table-cell tabular whitespace-nowrap" style={{ color: 'var(--text-faint)' }}>
                    {formatDate(ao.created_at) || '—'}
                  </td>
                  <td className="px-4 py-2.5 hidden xl:table-cell whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                    {ao.location || '—'}
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    {dl ? (
                      <span
                        className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-semibold"
                        style={DEADLINE_TONE[dl.tone]}
                        title={`${dl.date} · ${dl.rel}`}
                      >
                        <CalendarClock size={11} className="shrink-0" />
                        {dl.date}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-faint)' }}>—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    <span className={clsx(
                      'badge',
                      isOpen
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                        : 'bg-slate-500/10 text-slate-500 border border-slate-600/20'
                    )}>
                      {isOpen ? 'Ouvert' : 'Fermé'}
                    </span>
                  </td>
                  {isStaff && (
                    <td className="px-4 py-2.5 text-right whitespace-nowrap" onClick={e => e.stopPropagation()}>
                      <span className="inline-flex items-center gap-1 text-brand-300 text-xs mr-1" title="CV soumis">
                        <Users size={11} />
                        {ao.submission_count ?? 0}
                      </span>
                      <button onClick={() => onEdit(ao)} className="btn-ghost p-1.5" title="Modifier">
                        <Pencil size={12} />
                      </button>
                      <button onClick={() => onDelete(ao)} className="btn-ghost p-1.5 hover:text-red-400" title="Supprimer">
                        <X size={13} />
                      </button>
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── AO calendar (vue agenda — échéances de réponse) ───────────────────────────
// Grille mensuelle : chaque AO se pose sur sa date d'échéance. Pensé pour voir
// d'un coup d'œil les fermetures à venir (format agenda) plutôt qu'en liste.
const WEEKDAYS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']

function AOCalendar({ items, navigate }) {
  // Mois affiché (1er du mois). On démarre sur le mois courant.
  const [cursor, setCursor] = useState(() => {
    const t = new Date()
    return new Date(t.getFullYear(), t.getMonth(), 1)
  })

  // AO regroupés par jour d'échéance (clé = année-mois-jour, en local).
  const byDay = useMemo(() => {
    const m = new Map()
    for (const ao of items) {
      const d = parseDateLocal(ao.deadline)
      if (!d) continue
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
      if (!m.has(key)) m.set(key, [])
      m.get(key).push(ao)
    }
    return m
  }, [items])

  const noDeadline = useMemo(
    () => items.filter(a => !parseDateLocal(a.deadline)).length,
    [items]
  )

  const year = cursor.getFullYear()
  const month = cursor.getMonth()
  const today = new Date(); today.setHours(0, 0, 0, 0)

  // Grille alignée sur lundi ; on ne rend que le nombre de semaines nécessaire.
  const startOffset = (new Date(year, month, 1).getDay() + 6) % 7 // 0 = lundi
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const weeks = Math.ceil((startOffset + daysInMonth) / 7)
  const cells = Array.from({ length: weeks * 7 }, (_, i) => new Date(year, month, 1 - startOffset + i))

  // Nombre d'échéances tombant dans le mois affiché (indicateur d'en-tête).
  const monthCount = cells.reduce((n, d) => {
    if (d.getMonth() !== month) return n
    return n + (byDay.get(`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`)?.length || 0)
  }, 0)

  const goMonth = (delta) => setCursor(new Date(year, month + delta, 1))
  const goToday = () => { const t = new Date(); setCursor(new Date(t.getFullYear(), t.getMonth(), 1)) }
  const monthLabel = new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric' }).format(cursor)

  return (
    <div className="card overflow-hidden">
      {/* En-tête : navigation mois + compteur d'échéances */}
      <div className="flex items-center justify-between gap-3 px-4 py-3"
        style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2">
          <button onClick={() => goMonth(-1)} className="btn-ghost p-1.5" title="Mois précédent">
            <ChevronLeft size={15} />
          </button>
          <h2 className="text-sm font-semibold capitalize min-w-[150px] text-center" style={{ color: 'var(--text)' }}>
            {monthLabel}
          </h2>
          <button onClick={() => goMonth(1)} className="btn-ghost p-1.5" title="Mois suivant">
            <ChevronRight size={15} />
          </button>
          <button onClick={goToday} className="btn-ghost text-xs px-2.5 py-1 ml-1">
            Aujourd'hui
          </button>
        </div>
        <span className="text-xs" style={{ color: 'var(--text-faint)' }}>
          {monthCount} échéance{monthCount > 1 ? 's' : ''} ce mois-ci
        </span>
      </div>

      <div className="overflow-x-auto">
        <div className="min-w-[720px]">
          {/* Bandeau jours de la semaine */}
          <div className="grid grid-cols-7">
            {WEEKDAYS.map(w => (
              <div key={w} className="px-2 py-2 text-[11px] font-medium uppercase tracking-wide text-center"
                style={{ color: 'var(--text-faint)', borderBottom: '1px solid var(--border)' }}>
                {w}
              </div>
            ))}
          </div>

          {/* Cellules du mois */}
          <div className="grid grid-cols-7">
            {cells.map((d, i) => {
              const inMonth = d.getMonth() === month
              const isToday = d.getTime() === today.getTime()
              const dayAos = byDay.get(`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`) || []
              return (
                <div
                  key={i}
                  className="min-h-[104px] p-1.5 flex flex-col gap-1"
                  style={{
                    borderBottom: '1px solid var(--border)',
                    borderRight: (i % 7 !== 6) ? '1px solid var(--border)' : undefined,
                    background: inMonth ? undefined : 'var(--surface-2)',
                    opacity: inMonth ? 1 : 0.55,
                  }}
                >
                  <div className="flex items-center justify-between">
                    <span
                      className="text-[11px] tabular w-5 h-5 flex items-center justify-center rounded-full"
                      style={isToday
                        ? { background: 'var(--accent)', color: '#fff', fontWeight: 600 }
                        : { color: 'var(--text-muted)' }}
                    >
                      {d.getDate()}
                    </span>
                  </div>
                  <div className="flex flex-col gap-1">
                    {dayAos.slice(0, 3).map(ao => {
                      const dl = deadlineMeta(ao.deadline)
                      return (
                        <button
                          key={ao.id}
                          onClick={() => navigate(`/aos/${ao.id}`)}
                          className="text-left w-full px-1.5 py-1 rounded text-[11px] font-medium leading-tight truncate transition-opacity hover:opacity-80"
                          style={DEADLINE_TONE[dl?.tone] || { background: 'var(--surface-2)', color: 'var(--text-muted)' }}
                          title={`${ao.clients?.name ? ao.clients.name + ' · ' : ''}${ao.title}${dl ? ' · ' + dl.rel : ''}`}
                        >
                          {ao.clients?.name ? `${ao.clients.name} — ` : ''}{ao.title}
                        </button>
                      )
                    })}
                    {dayAos.length > 3 && (
                      <span className="text-[10px] px-1.5" style={{ color: 'var(--text-faint)' }}>
                        +{dayAos.length - 3} autre{dayAos.length - 3 > 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {noDeadline > 0 && (
        <div className="px-4 py-2.5 text-[11px]" style={{ color: 'var(--text-faint)', borderTop: '1px solid var(--border)' }}>
          {noDeadline} AO{noDeadline > 1 ? 's' : ''} sans date d'échéance {noDeadline > 1 ? 'ne sont' : "n'est"} pas affiché{noDeadline > 1 ? 's' : ''} ici.
        </div>
      )}
    </div>
  )
}

export default function AOSPage() {
  const { isStaff } = useAuth()
  const confirm = useConfirm()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const matchedOnly = searchParams.get('matched') === '1'
  const [matchedIds, setMatchedIds] = useState(null) // Set des ao_id ayant un profil potentiel
  const [aos, setAos] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [view, setView] = useState('client') // 'client' (cartes groupées) | 'cards' (cartes) | 'table' (liste dense)
  const [sortBy, setSortBy] = useState('created')  // 'created' (émission) | 'deadline' (échéance)
  const [editAo, setEditAo] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [selected, setSelected] = useState(() => new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)

  const fetchAos = () =>
    api.get('/aos')
      .then(r => setAos(r.data))
      .catch(e => setError(e.response?.data?.detail || e.message || 'Erreur de chargement'))

  const handleDeleteAo = async (ao) => {
    if (!(await confirm({
      title: "Supprimer l'appel d'offres ?",
      message: `« ${ao.title} » sera supprimé définitivement. Cette action est irréversible.`,
      confirmLabel: 'Supprimer',
    }))) return
    setDeleting(ao.id)
    try {
      await api.delete(`/aos/${ao.id}`)
      setAos(p => p.filter(a => a.id !== ao.id))
      setSelected(p => { const n = new Set(p); n.delete(ao.id); return n })
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors de la suppression')
    } finally {
      setDeleting(null)
    }
  }

  const toggleSelect = (id) => {
    setSelected(p => {
      const n = new Set(p)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  const handleBulkDelete = async () => {
    const n = selected.size
    if (!n) return
    if (!(await confirm({
      title: `Supprimer ${n} appel${n > 1 ? 's' : ''} d'offres ?`,
      message: `${n} AO${n > 1 ? 's' : ''} ${n > 1 ? 'seront supprimés' : 'sera supprimé'} définitivement. Cette action est irréversible.`,
      confirmLabel: `Supprimer (${n})`,
    }))) return
    setBulkDeleting(true)
    try {
      const ids = Array.from(selected)
      await api.post('/aos/bulk-delete', { ids })
      setAos(p => p.filter(a => !selected.has(a.id)))
      setSelected(new Set())
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors de la suppression')
    } finally {
      setBulkDeleting(false)
    }
  }

  useEffect(() => {
    api.get('/aos')
      .then(r => setAos(r.data))
      .catch(e => setError(e.response?.data?.detail || e.message || 'Erreur de chargement'))
      .finally(() => setLoading(false))
  }, [])

  // Filtre « AOs avec profil potentiel » (venant du tableau de bord) :
  // on récupère la liste des ao_id ayant au moins un consultant potentiel.
  useEffect(() => {
    if (!matchedOnly || !isStaff) { setMatchedIds(null); return }
    api.get('/matching/stats')
      .then(r => setMatchedIds(new Set(r.data.matched_ao_ids || [])))
      .catch(() => setMatchedIds(new Set()))
  }, [matchedOnly, isStaff])

  const filtered = useMemo(() => aos.filter(ao => {
    const matchSearch = !search ||
      ao.title.toLowerCase().includes(search.toLowerCase()) ||
      ao.skills_required?.toLowerCase().includes(search.toLowerCase()) ||
      ao.reference?.toLowerCase().includes(search.toLowerCase()) ||
      ao.clients?.name?.toLowerCase().includes(search.toLowerCase())
    const matchFilter = filter === 'all' || ao.status === filter
    const matchPotential = !matchedOnly || (matchedIds && matchedIds.has(ao.id))
    return matchSearch && matchFilter && matchPotential
  }), [aos, search, filter, matchedOnly, matchedIds])

  // Sort: list_1 first, then list_2, then par le critère choisi.
  // - 'created'  : émission la plus récente d'abord
  // - 'deadline' : échéance la plus proche d'abord (AO sans échéance en dernier)
  const sorted = useMemo(() => {
    const tierRank = { list_1: 0, list_2: 1 }
    const byCreated = (a, b) => new Date(b.created_at) - new Date(a.created_at)
    return [...filtered].sort((a, b) => {
      const ar = tierRank[a.tier] ?? 2
      const br = tierRank[b.tier] ?? 2
      if (ar !== br) return ar - br
      if (sortBy === 'deadline') {
        const d = deadlineSortKey(a) - deadlineSortKey(b)
        if (d !== 0) return d
      }
      return byCreated(a, b)
    })
  }, [filtered, sortBy])

  // Sélection « tout » dans la vue tableau (dépend du sous-ensemble affiché).
  const allSelected = sorted.length > 0 && sorted.every(a => selected.has(a.id))
  const toggleSelectAll = () => {
    setSelected(prev => {
      if (sorted.length > 0 && sorted.every(a => prev.has(a.id))) return new Set()
      return new Set(sorted.map(a => a.id))
    })
  }

  const groupedByClient = useMemo(() => {
    if (view !== 'client') return null
    const groups = new Map()
    for (const ao of sorted) {
      const key = ao.clients?.id || 'unknown'
      const name = ao.clients?.name || 'Sans client'
      if (!groups.has(key)) groups.set(key, { name, items: [] })
      groups.get(key).items.push(ao)
    }
    return Array.from(groups.entries()).map(([id, v]) => ({ id, name: v.name, items: v.items }))
  }, [sorted, view])

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="section-title flex items-center gap-2">
            <FileText size={20} strokeWidth={1.75} style={{ color: 'var(--accent-text)' }} />
            Appels d'Offres
            <span className="text-sm font-normal text-slate-500">
              ({filtered.length}{filtered.length !== aos.length ? ` / ${aos.length}` : ''})
            </span>
          </h1>
          {!isStaff && (
            <p className="text-sm text-slate-500 mt-0.5">
              Cliquez sur un AO pour proposer un consultant
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {isStaff && selected.size > 0 && (
            <button onClick={handleBulkDelete} disabled={bulkDeleting} className="btn-danger">
              {bulkDeleting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} strokeWidth={1.75} />}
              Supprimer ({selected.size})
            </button>
          )}
          {isStaff && (
            <Link to="/aos/new" className="btn-primary">
              <Plus size={15} />
              Nouvel AO
            </Link>
          )}
        </div>
      </div>

      {matchedOnly && (
        <div className="flex items-center justify-between gap-3 mb-4 px-4 py-2.5 rounded-lg text-sm"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)', border: '1px solid var(--border)' }}>
          <span className="flex items-center gap-2">
            <Sparkles size={14} />
            AOs ayant trouvé un consultant potentiel
            {matchedIds && <span className="opacity-70">· {filtered.length}</span>}
          </span>
          <button onClick={() => setSearchParams({}, { replace: true })}
            className="text-xs font-medium underline underline-offset-2 hover:opacity-80">
            Réinitialiser
          </button>
        </div>
      )}

      <div className="flex gap-3 mb-5 flex-wrap">
        <div className="relative flex-1 min-w-[240px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text" className="input pl-9"
            placeholder="Rechercher par titre, client, compétence, référence..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="flex gap-1 bg-white/5 rounded-lg p-1">
          {['all', 'open', 'closed'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={clsx(
                'px-3 py-1 text-xs rounded-md font-medium transition-all',
                filter === f ? 'seg-active' : 'text-slate-400 hover:text-slate-200'
              )}
            >
              {f === 'all' ? 'Tous' : f === 'open' ? 'Ouverts' : 'Fermés'}
            </button>
          ))}
        </div>
        <div className="flex gap-1 bg-white/5 rounded-lg p-1">
          {[
            { k: 'client', l: 'Par client' },
            { k: 'cards', l: 'Cartes' },
            { k: 'table', l: 'Tableau' },
            { k: 'calendar', l: 'Calendrier' },
          ].map(o => (
            <button
              key={o.k}
              onClick={() => setView(o.k)}
              className={clsx(
                'px-3 py-1 text-xs rounded-md font-medium transition-all',
                view === o.k ? 'seg-active' : 'text-slate-400 hover:text-slate-200'
              )}
            >
              {o.l}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 bg-white/5 rounded-lg p-1" title="Trier les appels d'offres">
          <ArrowDownUp size={12} className="text-slate-500 ml-1.5 shrink-0" />
          {[
            { k: 'created', l: 'Émission' },
            { k: 'deadline', l: 'Échéance' },
          ].map(o => (
            <button
              key={o.k}
              onClick={() => setSortBy(o.k)}
              className={clsx(
                'px-3 py-1 text-xs rounded-md font-medium transition-all',
                sortBy === o.k ? 'seg-active' : 'text-slate-400 hover:text-slate-200'
              )}
            >
              {o.l}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mb-4 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-center py-16 text-slate-500 text-sm">Chargement...</div>
      ) : sorted.length === 0 ? (
        <EmptyState
          icon={FileText}
          message={matchedOnly
            ? "Aucun AO n'a encore trouvé de consultant potentiel."
            : search
              ? 'Aucun résultat'
              : 'Aucun appel d\'offres accessible pour le moment'}
          action={<>
            {/* Ne proposer « créer » que sur un vrai état vide (pas un filtre). */}
            {isStaff && !search && !matchedOnly && (
              <Link to="/aos/new" className="btn-primary mt-4 mx-auto">
                <Plus size={14} /> Créer le premier AO
              </Link>
            )}
            {matchedOnly && (
              <button onClick={() => setSearchParams({}, { replace: true })} className="btn-ghost mt-4 mx-auto">
                Voir tous les AOs
              </button>
            )}
          </>}
        />
      ) : view === 'calendar' ? (
        <AOCalendar items={sorted} navigate={navigate} />
      ) : view === 'table' ? (
        <AOTable
          items={sorted}
          isStaff={isStaff}
          navigate={navigate}
          onEdit={setEditAo}
          onDelete={handleDeleteAo}
          selected={selected}
          onToggleSelect={toggleSelect}
          allSelected={allSelected}
          onToggleAll={toggleSelectAll}
        />
      ) : view === 'client' && groupedByClient ? (
        <div className="space-y-6">
          {groupedByClient.map(group => (
            <div key={group.id}>
              <div className="flex items-center gap-2 mb-3">
                <Building2 size={13} className="text-brand-400" />
                <h2 className="text-sm font-semibold text-white">{group.name}</h2>
                <span className="text-xs text-slate-500">({group.items.length})</span>
                <div className="flex-1 h-px bg-white/5 ml-2" />
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {group.items.map(ao => (
                  <AOCard key={ao.id} ao={ao} isStaff={isStaff}
                    navigate={navigate}
                    onEdit={setEditAo}
                    onDelete={handleDeleteAo}
                    selected={selected.has(ao.id)}
                    onToggleSelect={toggleSelect}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {sorted.map(ao => (
            <AOCard key={ao.id} ao={ao} isStaff={isStaff}
              navigate={navigate}
              onEdit={setEditAo}
              onDelete={handleDeleteAo}
              selected={selected.has(ao.id)}
              onToggleSelect={toggleSelect}
            />
          ))}
        </div>
      )}

      {editAo && (
        <AOEditModal
          ao={editAo}
          onClose={() => setEditAo(null)}
          onSaved={() => { setEditAo(null); fetchAos() }}
        />
      )}
    </div>
  )
}
