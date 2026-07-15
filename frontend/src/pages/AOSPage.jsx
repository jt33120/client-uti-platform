import { useState, useEffect, useMemo, useRef, useLayoutEffect } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useConfirm } from '../contexts/ConfirmContext'
import {
  FileText, Plus, Euro, MapPin, Clock, ArrowRight, Search,
  Building2, Users, Calendar, CalendarClock,
  Pencil, X, Loader2, ChevronDown, ChevronLeft, ChevronRight, Check, Trash2, ArrowDownUp, Sparkles,
  SlidersHorizontal, Archive, ArchiveRestore, Award, Send,
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

// Filtre « Échéance » du panneau avancé. Fenêtres glissantes (jours à partir
// d'aujourd'hui), volontairement intuitives : en retard / sous 7 j / sous 30 j /
// sans date. Sélection unique.
const DEADLINE_FILTERS = [
  { k: 'overdue', l: 'En retard' },
  { k: 'week', l: 'Sous 7 j' },
  { k: 'month', l: 'Sous 30 j' },
  { k: 'none', l: 'Sans date' },
]
const matchesDeadline = (ao, f) => {
  const d = parseDateLocal(ao.deadline)
  if (f === 'none') return !d
  if (!d) return false
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const days = Math.round((d - today) / 86400000)
  if (f === 'overdue') return days < 0
  if (f === 'week') return days >= 0 && days <= 7
  if (f === 'month') return days >= 0 && days <= 30
  return true
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
function AOCard({ ao, isStaff, onEdit, onDelete, onArchive, onPublish, archivedView, draftView, navigate, selected, onToggleSelect }) {
  const isOpen = ao.status === 'open'
  return (
    <div
      className="card p-4 hover:border-white/10 transition-all duration-150 group cursor-pointer relative"
      style={selected ? { borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)' } : undefined}
      onClick={() => navigate(`/aos/${ao.id}`)}
    >
      {/* Post-it « CV trouvé » (staff) : le matching a repéré au moins un CV
          au-dessus du seuil « à considérer » (≥ 50, notre plancher de scoring). */}
      {isStaff && (ao.potential_count ?? 0) > 0 && (
        <div
          className="absolute -top-2.5 -right-2 z-20 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold text-white shadow-lg rotate-3"
          style={{ background: '#10b981', boxShadow: '0 4px 10px -2px rgba(16,185,129,0.5)' }}
          title={`Matching : ${ao.potential_count} CV au-dessus du seuil « à considérer » (score ≥ 50)`}
        >
          <Sparkles size={10} /> CV trouvé
        </div>
      )}
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
          {archivedView && (
            <span className="badge bg-amber-500/10 text-amber-400 border border-amber-500/20 text-[10px] inline-flex items-center gap-1">
              <Archive size={9} /> Archivé
            </span>
          )}
          {draftView && (
            <span className="badge bg-slate-500/10 text-slate-300 border border-slate-500/20 text-[10px]">Brouillon</span>
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
            {/* Simple présence de CV déposés (le signal de matching « CV trouvé »
                est porté par le post-it en coin). */}
            <span className="flex items-center gap-1 ml-auto text-slate-500"
                  title={`${ao.submission_count ?? 0} CV déposé(s)`}>
              <Users size={10} />
              {(ao.submission_count ?? 0) > 0
                ? `CV déposé${(ao.submission_count ?? 0) > 1 ? 's' : ''}`
                : 'Aucun CV'}
            </span>
            {draftView && (
              <button
                onClick={e => { e.stopPropagation(); onPublish(ao) }}
                className="inline-flex items-center gap-1 px-2 h-6 rounded-md text-[11px] font-semibold text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/10 transition-colors"
                title="Publier — rendre visible aux partenaires + lancer le matching"
              >
                <Send size={11} /> Publier
              </button>
            )}
            <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
              {!draftView && (
                <button
                  onClick={e => { e.stopPropagation(); onArchive(ao, !archivedView) }}
                  className="btn-ghost p-1.5"
                  title={archivedView ? 'Désarchiver' : 'Archiver'}
                >
                  {archivedView ? <ArchiveRestore size={12} /> : <Archive size={12} />}
                </button>
              )}
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
function AOTable({ items, isStaff, navigate, onEdit, onDelete, onArchive, onPublish, archivedView, draftView, selected, onToggleSelect, allSelected, onToggleAll }) {
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
              <th className={th}>Échéance</th>
              <th className={clsx(th, 'hidden md:table-cell')}>Émis le</th>
              <th className={clsx(th, 'hidden xl:table-cell')}>Lieu</th>
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
                  <td className="px-4 py-2.5 hidden md:table-cell tabular whitespace-nowrap" style={{ color: 'var(--text-faint)' }}>
                    {formatDate(ao.created_at) || '—'}
                  </td>
                  <td className="px-4 py-2.5 hidden xl:table-cell whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                    {ao.location || '—'}
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    {draftView ? (
                      <span className="badge bg-slate-500/10 text-slate-300 border border-slate-500/20">Brouillon</span>
                    ) : (
                      <span className={clsx(
                        'badge',
                        isOpen
                          ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                          : 'bg-slate-500/10 text-slate-500 border border-slate-600/20'
                      )}>
                        {isOpen ? 'Ouvert' : 'Fermé'}
                      </span>
                    )}
                  </td>
                  {isStaff && (
                    <td className="px-4 py-2.5 text-right whitespace-nowrap" onClick={e => e.stopPropagation()}>
                      <span className="inline-flex items-center gap-1 text-brand-300 text-xs mr-1" title="CV soumis">
                        <Users size={11} />
                        {ao.submission_count ?? 0}
                      </span>
                      {draftView && (
                        <button onClick={() => onPublish(ao)} className="btn-ghost p-1.5 text-emerald-300" title="Publier">
                          <Send size={12} />
                        </button>
                      )}
                      {!draftView && (
                        <button onClick={() => onArchive(ao, !archivedView)} className="btn-ghost p-1.5" title={archivedView ? 'Désarchiver' : 'Archiver'}>
                          {archivedView ? <ArchiveRestore size={12} /> : <Archive size={12} />}
                        </button>
                      )}
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

// ── Issue d'un candidat (« Mes réponses ») — libellé + teinte ─────────────────
const OUTCOME_META = {
  gagne:    { l: 'Gagné',            bg: 'rgba(16,185,129,0.14)',  c: '#34d399' },
  perdu:    { l: 'Perdu',            bg: 'rgba(239,68,68,0.14)',   c: '#f87171' },
  presente: { l: 'Présenté client',  bg: 'rgba(99,102,241,0.14)',  c: '#a5b4fc' },
  retenu:   { l: 'Retenu',           bg: 'rgba(16,185,129,0.10)',  c: '#34d399' },
  ecarte:   { l: 'Écarté',           bg: 'rgba(148,163,184,0.14)', c: '#94a3b8' },
  contacte: { l: 'Contacté',         bg: 'rgba(245,158,11,0.14)',  c: '#f59e0b' },
  soumis:   { l: 'Soumis',           bg: 'rgba(148,163,184,0.10)', c: '#94a3b8' },
}

// « Mes réponses » enrichi (partenaire) : un bloc par AO répondu, avec l'issue
// de chaque candidat proposé (score + label). Auto-portant (fetch dédié).
function MyResponses({ navigate }) {
  const [aos, setAos] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    let ignore = false
    setLoading(true); setError('')
    api.get('/submissions/mine/outcomes')
      .then(r => { if (!ignore) setAos(r.data?.aos || []) })
      .catch(e => { if (!ignore) setError(e.response?.data?.detail || 'Erreur de chargement') })
      .finally(() => { if (!ignore) setLoading(false) })
    return () => { ignore = true }
  }, [])
  if (loading) return <div className="text-center py-16 text-slate-500 text-sm">Chargement…</div>
  if (error) return <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">{error}</div>
  if (!aos || aos.length === 0) return <EmptyState icon={FileText} message="Vous n'avez encore répondu à aucun AO." />
  return (
    <div className="space-y-3">
      {aos.map(ao => (
        <div key={ao.ao_id} onClick={() => navigate(`/aos/${ao.ao_id}`)}
          className="card p-4 cursor-pointer hover:border-white/10 transition-colors">
          <div className="flex items-start justify-between gap-2 mb-2.5">
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5 flex items-center gap-1.5 flex-wrap">
                {ao.client_name && <span className="inline-flex items-center gap-1"><Building2 size={9} /> {ao.client_name}</span>}
                {ao.ao_reference && <span className="text-slate-400 normal-case">· réf. {ao.ao_reference}</span>}
                {ao.ao_archived && <span className="badge bg-amber-500/10 text-amber-400 border border-amber-500/20 text-[9px]">Archivé</span>}
              </div>
              <h3 className="text-sm font-semibold text-white line-clamp-1">{ao.ao_title}</h3>
            </div>
            <ArrowRight size={13} className="text-slate-700 shrink-0 mt-1" />
          </div>
          <div className="space-y-1.5">
            {(ao.candidates || []).map((c, i) => {
              const m = OUTCOME_META[c.outcome] || OUTCOME_META.soumis
              return (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <Users size={12} className="text-slate-500 shrink-0" />
                  <span className="text-slate-300 truncate flex-1">{c.consultant_name || 'Consultant'}</span>
                  {typeof c.score === 'number' && <span className="text-slate-500 tabular shrink-0">{Math.round(c.score)}/100</span>}
                  <span className="badge text-[10px] shrink-0" style={{ background: m.bg, color: m.c, border: 'none' }}>{m.l}</span>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

// Vue Pipeline (équipe UTI) : kanban des AO actifs par étape d'avancement.
// Mini-bilan déclenché en glissant une carte sur une colonne terminale du pipeline.
function PipelineOutcomeModal({ ao, stage, onClose, onSaved }) {
  const [outcome, setOutcome] = useState(stage === 'gagne' ? 'pourvu' : 'non_pourvu')
  const [winner, setWinner] = useState('')
  const [note, setNote] = useState('')
  const [partners, setPartners] = useState([])
  const [saving, setSaving] = useState(false)
  // Gagnant : partenaires ayant répondu à CET AO (chargé à la demande).
  useEffect(() => {
    if (stage !== 'gagne') return
    let ignore = false
    api.get(`/submissions/ao/${ao.id}`).then(r => {
      if (ignore) return
      const m = new Map()
      ;(r.data || []).forEach(s => { const p = s.submitter; if (p?.id) m.set(p.id, p.name || p.email || 'Partenaire') })
      setPartners(Array.from(m, ([id, name]) => ({ id, name })))
    }).catch(() => {})
    return () => { ignore = true }
  }, [ao.id, stage])
  const save = async () => {
    setSaving(true)
    try {
      await api.patch(`/aos/${ao.id}/outcome`, {
        ao_outcome: outcome,
        winning_partner_id: outcome === 'pourvu' ? (winner || null) : null,
        outcome_note: note || null,
      })
      await onSaved()
    } catch (e) {
      alert(e.response?.data?.detail || 'Enregistrement impossible')
      setSaving(false)
    }
  }
  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-5 w-full max-w-md" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-sm font-semibold text-white flex items-center gap-2"><Check size={14} className="text-brand-400" /> Clôturer l'AO</h3>
          <button onClick={onClose} className="btn-ghost p-1.5"><X size={14} /></button>
        </div>
        <p className="text-xs text-slate-500 mb-3 line-clamp-1">{ao.title}</p>
        {stage === 'perdu' && (
          <div className="flex gap-1.5 mb-3">
            {[['non_pourvu', 'Non pourvu'], ['sans_suite', 'Sans suite']].map(([k, l]) => (
              <button key={k} onClick={() => setOutcome(k)}
                className="px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors"
                style={outcome === k
                  ? { background: 'var(--accent-soft)', color: 'var(--accent-text)', borderColor: 'var(--accent)' }
                  : { background: 'var(--surface-2)', color: 'var(--text-muted)', borderColor: 'var(--border)' }}>
                {l}
              </button>
            ))}
          </div>
        )}
        {outcome === 'pourvu' && (
          <div className="mb-3">
            <label className="label">Partenaire gagnant</label>
            <div className="relative">
              <select className="input appearance-none pr-9" value={winner} onChange={e => setWinner(e.target.value)}>
                <option value="" className="bg-navy-900">— (pourvu hors plateforme / à préciser)</option>
                {partners.map(p => <option key={p.id} value={p.id} className="bg-navy-900">{p.name}</option>)}
              </select>
              <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
            </div>
          </div>
        )}
        <div className="mb-4">
          <label className="label">Note (optionnel)</label>
          <textarea className="input min-h-[52px] resize-y" value={note} onChange={e => setNote(e.target.value)} placeholder="Contexte de la clôture…" />
        </div>
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="btn-ghost text-xs px-3">Annuler</button>
          <button onClick={save} disabled={saving} className="btn-primary text-xs px-4 flex items-center gap-1.5">
            {saving ? <Loader2 size={13} className="animate-spin" /> : 'Clôturer'}
          </button>
        </div>
      </div>
    </div>
  )
}

const PIPELINE_TERMINAL = { gagne: true, perdu: true }

// Vue Pipeline (équipe UTI) : kanban des AO actifs par étape. Glisser une carte
// sur Gagné / Perdu-Sans suite ouvre un mini-bilan ; la reglisser vers une
// colonne active rouvre l'AO (efface l'issue).
function PipelineBoard({ navigate }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [dragId, setDragId] = useState(null)
  const [over, setOver] = useState(null)
  const [modal, setModal] = useState(null)   // { ao, stage }
  useEffect(() => {
    let ignore = false
    setLoading(true); setError('')
    api.get('/aos/pipeline')
      .then(r => { if (!ignore) setData(r.data) })
      .catch(e => { if (!ignore) setError(e.response?.data?.detail || 'Erreur de chargement') })
      .finally(() => { if (!ignore) setLoading(false) })
    return () => { ignore = true }
  }, [])
  const reload = async () => {
    try { const r = await api.get('/aos/pipeline'); setData(r.data) } catch { /* garde l'état courant */ }
  }
  // Seules les colonnes terminales (Gagné / Perdu) sont des cibles : y déposer une
  // carte ouvre le mini-bilan. Rouvrir un AO clôturé se fait depuis sa fiche
  // (effacer le bilan + désarchiver au besoin) — un simple drop ferait « vanish »
  // un AO archivé dont on efface l'issue.
  const onDrop = (stageKey) => {
    setOver(null)
    const id = dragId; setDragId(null)
    if (!id || !PIPELINE_TERMINAL[stageKey]) return
    const ao = (data?.aos || []).find(a => a.id === id)
    if (ao && ao.stage !== stageKey) setModal({ ao, stage: stageKey })
  }
  if (loading) return <div className="text-center py-16 text-slate-500 text-sm">Chargement…</div>
  if (error) return <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">{error}</div>
  const stages = data?.stages || []
  const byStage = {}
  stages.forEach(s => { byStage[s.key] = [] })
  ;(data?.aos || []).forEach(a => { (byStage[a.stage] || (byStage[a.stage] = [])).push(a) })
  return (
    <>
      <p className="text-[11px] text-slate-500 mb-2">Glissez une carte sur <span className="text-slate-300">Gagné</span> ou <span className="text-slate-300">Perdu / Sans suite</span> pour clôturer l'AO.</p>
      <div className="overflow-x-auto pb-2">
        <div className="flex gap-3 min-w-max">
          {stages.map(s => {
            const items = byStage[s.key] || []
            const droppable = !!PIPELINE_TERMINAL[s.key]
            const isDropTarget = droppable && dragId && over === s.key
            return (
              <div key={s.key} className="w-64 shrink-0">
                <div className="flex items-center justify-between mb-2 px-1">
                  <span className="text-xs font-semibold text-white">{s.label}</span>
                  <span className="text-[11px] text-slate-500 tabular">{items.length}</span>
                </div>
                <div
                  {...(droppable ? {
                    onDragOver: e => { e.preventDefault(); if (over !== s.key) setOver(s.key) },
                    onDragLeave: e => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(o => (o === s.key ? null : o)) },
                    onDrop: e => { e.preventDefault(); onDrop(s.key) },
                  } : {})}
                  className="space-y-2 rounded-lg p-1.5 min-h-[60px] transition-colors"
                  style={{
                    background: isDropTarget ? 'var(--accent-soft)' : 'var(--surface-2)',
                    outline: isDropTarget ? '1px dashed var(--accent)' : 'none',
                  }}>
                  {items.map(a => {
                    const dl = deadlineMeta(a.deadline)
                    return (
                      <div key={a.id} draggable
                        onDragStart={e => { e.dataTransfer.setData('text/plain', a.id); e.dataTransfer.effectAllowed = 'move'; setDragId(a.id) }}
                        onDragEnd={() => { setDragId(null); setOver(null) }}
                        onClick={() => navigate(`/aos/${a.id}`)}
                        className="card p-2.5 cursor-grab active:cursor-grabbing hover:border-white/10 transition-colors"
                        style={{ opacity: dragId === a.id ? 0.5 : 1 }}>
                        <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5 truncate">{a.clients?.name || '—'}</div>
                        <div className="text-[12.5px] font-medium text-white line-clamp-2 mb-1.5 leading-snug">{a.title}</div>
                        <div className="flex items-center gap-2 flex-wrap">
                          {dl && <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold" style={DEADLINE_TONE[dl.tone]}><CalendarClock size={9} />{dl.rel}</span>}
                          {a.submission_count > 0 && <span className="text-[10px] text-brand-300 inline-flex items-center gap-0.5"><Users size={9} />{a.submission_count}</span>}
                          {a.relance_count > 0 && <span className="text-[9px] text-slate-500">· {a.relance_count} relance{a.relance_count > 1 ? 's' : ''}</span>}
                        </div>
                        {a.winner_name && (
                          <div className="mt-1 inline-flex items-center gap-1 text-[10px] text-emerald-400 truncate max-w-full">
                            <Award size={9} className="shrink-0" /> {a.winner_name}
                          </div>
                        )}
                      </div>
                    )
                  })}
                  {items.length === 0 && <div className="text-[11px] text-slate-600 text-center py-3">—</div>}
                </div>
              </div>
            )
          })}
        </div>
      </div>
      {modal && (
        <PipelineOutcomeModal ao={modal.ao} stage={modal.stage}
          onClose={() => setModal(null)}
          onSaved={async () => { setModal(null); await reload() }} />
      )}
    </>
  )
}

// ── Panneau de filtres : section + puce sélectionnable ────────────────────────
const FSection = ({ label, children }) => (
  <div className="mb-3">
    <p className="text-[10px] font-semibold uppercase tracking-wider mb-1.5" style={{ color: 'var(--text-faint)' }}>{label}</p>
    {children}
  </div>
)
const FChip = ({ active, onClick, children }) => (
  <button
    type="button"
    onClick={onClick}
    className="px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors border max-w-full truncate"
    style={active
      ? { background: 'var(--accent-soft)', color: 'var(--accent-text)', borderColor: 'var(--accent)' }
      : { background: 'var(--surface-2)', color: 'var(--text-muted)', borderColor: 'var(--border)' }}
  >
    {children}
  </button>
)

export default function AOSPage() {
  const { isStaff, isAdmin, isCommerce } = useAuth()
  const confirm = useConfirm()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const matchedOnly = searchParams.get('matched') === '1'
  // Sous-onglets par rôle (liste par périmètre). 'active' = actifs ; 'mine' =
  // partenaire -> AO répondus (historique) / staff -> mes AO ; 'archived' =
  // archivés (partenaire : jamais).
  const [tab, setTab] = useState('active')
  const roleTabs = isAdmin
    ? [{ k: 'active', l: 'Tous' }, { k: 'draft', l: 'Brouillons' }, { k: 'archived', l: 'Archivés' }]
    : isCommerce
      ? [{ k: 'active', l: 'Tous' }, { k: 'mine', l: 'Mes AOs' }, { k: 'draft', l: 'Brouillons' }, { k: 'archived', l: 'Mes archivés' }]
      : [{ k: 'active', l: 'Accessibles' }, { k: 'mine', l: 'Mes réponses' }]
  const archivedView = tab === 'archived'
  const draftView = tab === 'draft'
  const partnerMine = tab === 'mine' && !isStaff   // « Mes réponses » enrichi (partenaire)
  const [matchedIds, setMatchedIds] = useState(null) // Set des ao_id ayant un profil potentiel
  const [aos, setAos] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')          // statut : 'all' | 'open' | 'closed'
  const [view, setView] = useState('client') // 'client' (cartes groupées) | 'cards' (cartes) | 'table' (liste dense)
  const [sortBy, setSortBy] = useState('created')  // 'created' (émission) | 'deadline' (échéance)
  // ── Filtres avancés (panneau unifié) ──────────────────────────────────────
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [fClients, setFClients] = useState(() => new Set())   // client_id retenus
  const [fTypes, setFTypes] = useState(() => new Set())       // ao_type retenus
  const [fDeadline, setFDeadline] = useState(null)            // clé DEADLINE_FILTERS | null
  const [fTjmMin, setFTjmMin] = useState('')                  // budget_max min (€/j)
  const [fTjmMax, setFTjmMax] = useState('')                  // budget_max max (€/j)
  const [fCandidates, setFCandidates] = useState(null)        // 'yes' | 'no' | null (staff)
  const filtersRef = useRef(null)
  const triggerRef = useRef(null)
  const [panelStyle, setPanelStyle] = useState(null)          // position calculée (bornée à la fenêtre)
  const [editAo, setEditAo] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [selected, setSelected] = useState(() => new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)

  const fetchAos = () =>
    api.get('/aos', { params: { view: tab } })
      .then(r => setAos(r.data))
      .catch(e => setError(e.response?.data?.detail || e.message || 'Erreur de chargement'))

  // Archive / désarchive (équipe UTI) : l'AO disparaît de la vue courante.
  const handleArchive = async (ao, toArchived) => {
    try {
      await api.post(`/aos/${ao.id}/${toArchived ? 'archive' : 'unarchive'}`)
      setAos(p => p.filter(a => a.id !== ao.id))
      setSelected(p => { const n = new Set(p); n.delete(ao.id); return n })
    } catch (e) {
      alert(e.response?.data?.detail || "Action impossible")
    }
  }

  // Publier un brouillon (équipe UTI) : il quitte l'onglet Brouillons et devient
  // visible des partenaires habilités (+ matching déclenché côté serveur).
  const handlePublish = async (ao) => {
    try {
      await api.post(`/aos/${ao.id}/publish`)
      setAos(p => p.filter(a => a.id !== ao.id))
      setSelected(p => { const n = new Set(p); n.delete(ao.id); return n })
    } catch (e) {
      alert(e.response?.data?.detail || "Publication impossible")
    }
  }

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

  // (Re)chargement à chaque changement d'onglet. Garde « dernière requête gagne »
  // (ignore) : un basculement rapide d'onglet ne doit pas laisser une réponse en
  // retard écraser la vue courante. La bannière d'erreur est aussi remise à zéro.
  useEffect(() => {
    setError(''); setSelected(new Set())
    // « Mes réponses » (partenaire) est une vue auto-portante (MyResponses fait
    // son propre fetch) : inutile de charger /aos?view=mine, et surtout de ne pas
    // laisser une erreur de CE fetch afficher une bannière au-dessus.
    if (tab === 'mine' && !isStaff) { setLoading(false); return }
    let ignore = false
    setLoading(true)
    api.get('/aos', { params: { view: tab } })
      .then(r => { if (!ignore) setAos(r.data) })
      .catch(e => { if (!ignore) setError(e.response?.data?.detail || e.message || 'Erreur de chargement') })
      .finally(() => { if (!ignore) setLoading(false) })
    return () => { ignore = true }
  }, [tab, isStaff])

  // Filtre « AOs avec profil potentiel » (venant du tableau de bord) :
  // on récupère la liste des ao_id ayant au moins un consultant potentiel.
  useEffect(() => {
    if (!matchedOnly || !isStaff) { setMatchedIds(null); return }
    api.get('/matching/stats')
      .then(r => setMatchedIds(new Set(r.data.matched_ao_ids || [])))
      .catch(() => setMatchedIds(new Set()))
  }, [matchedOnly, isStaff])

  // Options dérivées des AO chargés (le panneau ne propose que ce qui existe).
  const clientOptions = useMemo(() => {
    const m = new Map()
    aos.forEach(a => { if (a.clients?.id) m.set(a.clients.id, a.clients.name || 'Sans nom') })
    return Array.from(m, ([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name, 'fr'))
  }, [aos])
  const typeOptions = useMemo(
    () => Array.from(new Set(aos.map(a => a.ao_type).filter(Boolean))).sort((a, b) => a.localeCompare(b, 'fr')),
    [aos]
  )

  const filtered = useMemo(() => aos.filter(ao => {
    const q = search.toLowerCase()
    const matchSearch = !search ||
      ao.title.toLowerCase().includes(q) ||
      ao.skills_required?.toLowerCase().includes(q) ||
      ao.reference?.toLowerCase().includes(q) ||
      ao.location?.toLowerCase().includes(q) ||
      ao.clients?.name?.toLowerCase().includes(q)
    const matchStatus = filter === 'all' || ao.status === filter
    const matchClient = fClients.size === 0 || (ao.clients?.id && fClients.has(ao.clients.id))
    const matchType = fTypes.size === 0 || (ao.ao_type && fTypes.has(ao.ao_type))
    const matchDeadline = !fDeadline || matchesDeadline(ao, fDeadline)
    const tjm = ao.budget_max
    const matchTjm =
      (!fTjmMin || (tjm != null && tjm >= Number(fTjmMin))) &&
      (!fTjmMax || (tjm != null && tjm <= Number(fTjmMax)))
    const cnt = ao.submission_count ?? 0
    const matchCand = !fCandidates || (fCandidates === 'yes' ? cnt > 0 : cnt === 0)
    const matchPotential = !matchedOnly || (matchedIds && matchedIds.has(ao.id))
    return matchSearch && matchStatus && matchClient && matchType &&
      matchDeadline && matchTjm && matchCand && matchPotential
  }), [aos, search, filter, fClients, fTypes, fDeadline, fTjmMin, fTjmMax, fCandidates, matchedOnly, matchedIds])

  // Nombre de dimensions de filtre actives (badge + rangée de puces).
  const activeCount =
    (filter !== 'all' ? 1 : 0) + (fClients.size ? 1 : 0) + (fTypes.size ? 1 : 0) +
    (fDeadline ? 1 : 0) + (fTjmMin || fTjmMax ? 1 : 0) + (fCandidates ? 1 : 0)

  const toggleIn = (setter) => (val) => setter(prev => {
    const n = new Set(prev)
    n.has(val) ? n.delete(val) : n.add(val)
    return n
  })
  const clearFilters = () => {
    setFilter('all'); setFClients(new Set()); setFTypes(new Set())
    setFDeadline(null); setFTjmMin(''); setFTjmMax(''); setFCandidates(null)
  }

  // Changement d'onglet : on repart d'une vue propre. Les options de filtre
  // (clients/types) sont désormais dérivées de l'onglet courant — garder des
  // filtres d'un autre onglet afficherait une puce « fantôme » et viderait la liste.
  const changeTab = (k) => {
    if (k === tab) return
    setTab(k); setSearch(''); clearFilters(); setFiltersOpen(false)
  }

  // Fermeture du panneau au clic extérieur.
  useEffect(() => {
    if (!filtersOpen) return
    const onDown = (e) => { if (filtersRef.current && !filtersRef.current.contains(e.target)) setFiltersOpen(false) }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [filtersOpen])

  // Positionne le panneau (fixed) sous le bouton, borné à la fenêtre : jamais
  // clippé hors écran quand le bouton passe à gauche sur mobile, et le bas reste
  // au-dessus de la barre de navigation mobile. Recalculé à l'ouverture + resize.
  useLayoutEffect(() => {
    if (!filtersOpen) { setPanelStyle(null); return }
    const place = () => {
      const btn = triggerRef.current?.getBoundingClientRect()
      if (!btn) return
      const width = Math.min(352, window.innerWidth - 16)
      const left = Math.max(8, Math.min(btn.right - width, window.innerWidth - width - 8))
      const top = Math.round(btn.bottom + 8)
      const maxHeight = Math.max(220, window.innerHeight - top - 72)
      setPanelStyle({ position: 'fixed', top, left: Math.round(left), width, maxHeight, overflowY: 'auto' })
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [filtersOpen])

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

  // Puces des filtres actifs (retrait individuel) — sous la barre d'outils.
  const activeChips = []
  if (filter !== 'all') activeChips.push({ key: 'status', label: filter === 'open' ? 'Ouverts' : 'Fermés', clear: () => setFilter('all') })
  fClients.forEach(id => activeChips.push({ key: `c-${id}`, label: clientOptions.find(c => c.id === id)?.name || 'Client', clear: () => toggleIn(setFClients)(id) }))
  fTypes.forEach(t => activeChips.push({ key: `t-${t}`, label: t, clear: () => toggleIn(setFTypes)(t) }))
  if (fDeadline) activeChips.push({ key: 'dl', label: `Échéance : ${DEADLINE_FILTERS.find(d => d.k === fDeadline)?.l}`, clear: () => setFDeadline(null) })
  if (fTjmMin || fTjmMax) activeChips.push({ key: 'tjm', label: `TJM ${fTjmMin || '0'}–${fTjmMax || '∞'} €/j`, clear: () => { setFTjmMin(''); setFTjmMax('') } })
  if (fCandidates) activeChips.push({ key: 'cand', label: fCandidates === 'yes' ? 'Avec CV' : 'Sans CV', clear: () => setFCandidates(null) })

  const pipelineView = isStaff && view === 'pipeline'   // kanban (auto-portant)

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
            <Link to="/carte?only=aos" className="btn-ghost" title="Voir les appels d'offres sur la carte">
              <MapPin size={15} />
              Carte
            </Link>
          )}
          {isStaff && (
            <Link to="/aos/new" className="btn-primary">
              <Plus size={15} />
              Nouvel AO
            </Link>
          )}
        </div>
      </div>

      {/* Sous-onglets par périmètre (masqués sur la vue « profils potentiels » et
          en mode Pipeline, qui est une vue transverse de tous les AO actifs). */}
      {!matchedOnly && !pipelineView && roleTabs.length > 1 && (
        <div role="tablist" className="flex gap-1 mb-4 border-b overflow-x-auto" style={{ borderColor: 'var(--border)' }}>
          {roleTabs.map(t => (
            <button
              key={t.k}
              role="tab"
              aria-selected={tab === t.k}
              onClick={() => changeTab(t.k)}
              className="px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors -mb-px"
              style={{
                color: tab === t.k ? 'var(--text)' : 'var(--text-muted)',
                borderBottom: `2px solid ${tab === t.k ? 'var(--accent)' : 'transparent'}`,
              }}
            >
              {t.l}
            </button>
          ))}
        </div>
      )}

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

      {!partnerMine && (
      <div className="flex gap-3 mb-5 flex-wrap">
        {!pipelineView && (
        <div className="relative flex-1 min-w-[240px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text" className="input pl-9"
            placeholder="Rechercher par titre, client, compétence, référence..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        )}
        {/* Filtres avancés — un seul bouton regroupe statut, client, type, échéance, TJM et CV reçus. */}
        {!pipelineView && (
        <div className="relative" ref={filtersRef}>
          <button
            ref={triggerRef}
            onClick={() => setFiltersOpen(o => !o)}
            className="inline-flex items-center gap-2 h-full min-h-[34px] px-3 rounded-lg text-xs font-medium transition-colors border"
            style={{
              background: activeCount > 0 ? 'var(--accent-soft)' : 'var(--surface-2)',
              borderColor: activeCount > 0 ? 'var(--accent)' : 'var(--border)',
              color: activeCount > 0 ? 'var(--accent-text)' : 'var(--text-muted)',
            }}
            title="Filtres avancés"
          >
            <SlidersHorizontal size={13} />
            Filtres
            {activeCount > 0 && (
              <span className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full text-[10px] font-bold"
                style={{ background: 'var(--accent)', color: '#fff' }}>{activeCount}</span>
            )}
          </button>

          {filtersOpen && (
            <div
              className="z-40 p-3 shadow-xl rounded-xl"
              style={{ ...(panelStyle || { position: 'fixed', top: -9999, left: -9999, overflowY: 'auto' }),
                background: 'var(--surface)', border: '1px solid var(--border)' }}>
              <FSection label="Statut">
                <div className="flex gap-1">
                  {[['all', 'Tous'], ['open', 'Ouverts'], ['closed', 'Fermés']].map(([k, l]) => (
                    <FChip key={k} active={filter === k} onClick={() => setFilter(k)}>{l}</FChip>
                  ))}
                </div>
              </FSection>

              {clientOptions.length > 1 && (
                <FSection label={`Client${fClients.size ? ` · ${fClients.size}` : ''}`}>
                  <div className="flex flex-wrap gap-1">
                    {clientOptions.map(c => (
                      <FChip key={c.id} active={fClients.has(c.id)} onClick={() => toggleIn(setFClients)(c.id)}>{c.name}</FChip>
                    ))}
                  </div>
                </FSection>
              )}

              {typeOptions.length > 0 && (
                <FSection label={`Type d'AO${fTypes.size ? ` · ${fTypes.size}` : ''}`}>
                  <div className="flex flex-wrap gap-1">
                    {typeOptions.map(t => (
                      <FChip key={t} active={fTypes.has(t)} onClick={() => toggleIn(setFTypes)(t)}>{t}</FChip>
                    ))}
                  </div>
                </FSection>
              )}

              <FSection label="Échéance">
                <div className="flex flex-wrap gap-1">
                  {DEADLINE_FILTERS.map(o => (
                    <FChip key={o.k} active={fDeadline === o.k} onClick={() => setFDeadline(p => (p === o.k ? null : o.k))}>{o.l}</FChip>
                  ))}
                </div>
              </FSection>

              <FSection label="TJM (€/j)">
                <div className="flex items-center gap-2">
                  <input type="number" min="0" inputMode="numeric" placeholder="min" value={fTjmMin}
                    onChange={e => setFTjmMin(e.target.value)} className="input !h-8 !py-1 text-xs w-full" />
                  <span className="text-slate-500 text-xs shrink-0">–</span>
                  <input type="number" min="0" inputMode="numeric" placeholder="max" value={fTjmMax}
                    onChange={e => setFTjmMax(e.target.value)} className="input !h-8 !py-1 text-xs w-full" />
                </div>
              </FSection>

              {isStaff && (
                <FSection label="CV reçus">
                  <div className="flex gap-1">
                    {[['yes', 'Avec CV'], ['no', 'Sans CV']].map(([k, l]) => (
                      <FChip key={k} active={fCandidates === k} onClick={() => setFCandidates(p => (p === k ? null : k))}>{l}</FChip>
                    ))}
                  </div>
                </FSection>
              )}

              <div className="flex items-center justify-between pt-2 mt-1" style={{ borderTop: '1px solid var(--border)' }}>
                <button onClick={clearFilters} disabled={activeCount === 0}
                  className="text-xs font-medium disabled:opacity-40" style={{ color: 'var(--text-muted)' }}>
                  Tout effacer
                </button>
                <button onClick={() => setFiltersOpen(false)} className="btn-primary text-xs px-3 py-1.5">
                  Voir {filtered.length} résultat{filtered.length > 1 ? 's' : ''}
                </button>
              </div>
            </div>
          )}
        </div>
        )}

        <div className="flex gap-1 bg-white/5 rounded-lg p-1">
          {[
            { k: 'client', l: 'Par client' },
            { k: 'cards', l: 'Cartes' },
            { k: 'table', l: 'Tableau' },
            { k: 'calendar', l: 'Calendrier' },
            ...(isStaff ? [{ k: 'pipeline', l: 'Pipeline' }] : []),
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
        {!pipelineView && (
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
        )}
      </div>
      )}

      {/* Puces des filtres actifs — retrait individuel ou en un clic. */}
      {!partnerMine && !pipelineView && activeChips.length > 0 && (
        <div className="flex items-center flex-wrap gap-1.5 -mt-2 mb-4">
          {activeChips.map(c => (
            <span key={c.key} className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-md text-[11px] font-medium"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)', border: '1px solid var(--border)' }}>
              <span className="truncate max-w-[180px]">{c.label}</span>
              <button onClick={c.clear} className="p-0.5 rounded hover:bg-black/10" title="Retirer ce filtre"><X size={11} /></button>
            </span>
          ))}
          <button onClick={clearFilters} className="text-[11px] font-medium underline underline-offset-2 ml-1"
            style={{ color: 'var(--text-muted)' }}>
            Tout effacer
          </button>
        </div>
      )}

      {!partnerMine && !pipelineView && error && (
        <div className="mb-4 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      {partnerMine ? (
        <MyResponses navigate={navigate} />
      ) : pipelineView ? (
        <PipelineBoard navigate={navigate} />
      ) : loading ? (
        <div className="text-center py-16 text-slate-500 text-sm">Chargement...</div>
      ) : sorted.length === 0 ? (
        <EmptyState
          icon={FileText}
          message={matchedOnly
            ? "Aucun AO n'a encore trouvé de consultant potentiel."
            : (search || activeCount > 0)
              ? 'Aucun résultat'
              : draftView
                ? 'Aucun brouillon'
                : archivedView
                ? 'Aucun AO archivé'
                : tab === 'mine'
                  ? (isStaff ? "Vous n'avez encore créé aucun AO" : "Vous n'avez encore répondu à aucun AO")
                  : 'Aucun appel d\'offres accessible pour le moment'}
          action={<>
            {/* « Créer » seulement sur l'onglet actif, vraiment vide (ni recherche ni filtre). */}
            {isStaff && tab === 'active' && !search && activeCount === 0 && !matchedOnly && (
              <Link to="/aos/new" className="btn-primary mt-4 mx-auto">
                <Plus size={14} /> Créer le premier AO
              </Link>
            )}
            {activeCount > 0 && (
              <button onClick={clearFilters} className="btn-ghost mt-4 mx-auto">
                Réinitialiser les filtres
              </button>
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
          onArchive={handleArchive}
          onPublish={handlePublish}
          archivedView={archivedView}
          draftView={draftView}
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
                    onArchive={handleArchive}
                    onPublish={handlePublish}
                    archivedView={archivedView}
                    draftView={draftView}
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
              onArchive={handleArchive}
              onPublish={handlePublish}
              archivedView={archivedView}
              draftView={draftView}
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
