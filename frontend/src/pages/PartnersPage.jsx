import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../lib/api'
import {
  Users, Search, Pencil, X, Loader2, AlertCircle,
  ShieldOff, Star, ListChecks, UserPlus, Ban, CalendarDays,
  Settings2, SlidersHorizontal, ChevronRight, RotateCcw,
} from 'lucide-react'
import clsx from 'clsx'
import { useAuth } from '../contexts/AuthContext'
import { useConfirm } from '../contexts/ConfirmContext'
import InviteModal from '../components/InviteModal'

// ── Edit modal ────────────────────────────────────────────────────────────────
function PartnerModal({ partner, onClose, onSaved }) {
  const [name, setName] = useState(partner.name)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      await api.patch(`/partners/${partner.id}`, { name: name.trim() })
      onSaved()
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur lors de la mise à jour')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div className="card p-6 w-full max-w-sm" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-sm font-semibold text-white">Modifier le partenaire</h2>
          <button onClick={onClose} className="btn-ghost p-1.5"><X size={14} /></button>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="label">Nom affiché</label>
            <input
              className="input"
              value={name}
              onChange={e => setName(e.target.value)}
              required
              minLength={2}
              autoFocus
            />
          </div>
          <div>
            <label className="label">Email</label>
            <input
              className="input opacity-50 cursor-not-allowed"
              value={partner.email}
              disabled
            />
            <p className="text-[10px] text-slate-500 mt-1">
              L'adresse email ne peut pas être modifiée ici.
            </p>
          </div>

          {error && <p className="text-xs text-red-400">{error}</p>}

          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose} className="btn-ghost text-xs px-3">
              Annuler
            </button>
            <button type="submit" disabled={loading} className="btn-primary text-xs px-4 flex items-center gap-1.5">
              {loading ? <Loader2 size={13} className="animate-spin" /> : 'Enregistrer'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Tier summary badges ───────────────────────────────────────────────────────
function AccessSummary({ summary }) {
  if (summary.total === 0) {
    return <span className="text-[10px] text-slate-600 flex items-center gap-1"><Ban size={9} /> Aucun accès</span>
  }
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {summary.list_1 > 0 && (
        <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400">
          <Star size={9} /> {summary.list_1} Liste 1
        </span>
      )}
      {summary.list_2 > 0 && (
        <span className="inline-flex items-center gap-1 text-[10px] text-brand-300">
          <ListChecks size={9} /> {summary.list_2} Liste 2
        </span>
      )}
      {summary.suspended > 0 && (
        <span className="inline-flex items-center gap-1 text-[10px] text-red-400">
          <ShieldOff size={9} /> {summary.suspended} suspendu{summary.suspended > 1 ? 's' : ''}
        </span>
      )}
    </div>
  )
}

const SORTS = [
  { k: 'name', l: 'Alphabétique' },
  { k: 'recent', l: 'Plus récents' },
]

const EMPTY_FILTERS = { access: 'all', account: 'all' }

// ── Main page ─────────────────────────────────────────────────────────────────
export default function PartnersPage() {
  const { isAdmin } = useAuth() // commerce: même vue, lecture seule
  const confirm = useConfirm()
  const navigate = useNavigate()
  const [partners, setPartners] = useState([])
  const [access, setAccess] = useState([])
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState(null)
  const [search, setSearch] = useState('')
  const [editPartner, setEditPartner] = useState(null)
  const [inviteOpen, setInviteOpen] = useState(false)
  const [deleting, setDeleting] = useState(null)
  const [suspending, setSuspending] = useState(null)
  const [advanced, setAdvanced] = useState(false)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [sort, setSort] = useState('name')

  const fetchAll = async () => {
    setLoading(true)
    setFetchError(null)
    try {
      const [p, a] = await Promise.all([
        api.get('/partners'),
        api.get('/partners/access'),
      ])
      setPartners(p.data)
      setAccess(a.data)
    } catch (e) {
      setFetchError(e.response?.data?.detail || e.message || 'Erreur de chargement')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchAll() }, [])

  const getAccessSummary = (partnerId) => {
    const rows = access.filter(r => r.partner_id === partnerId)
    return {
      total: rows.length,
      list_1: rows.filter(r => r.tier === 'list_1').length,
      list_2: rows.filter(r => r.tier === 'list_2').length,
      suspended: rows.filter(r => r.tier === 'suspended').length,
    }
  }

  const isAccountBlocked = (p) => p.status && p.status !== 'active'

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    const out = partners.filter(p => {
      if (q && !(p.name.toLowerCase().includes(q) || p.email.toLowerCase().includes(q))) return false
      if (filters.account !== 'all') {
        const blocked = isAccountBlocked(p)
        if (filters.account === 'blocked' && !blocked) return false
        if (filters.account === 'active' && blocked) return false
      }
      if (filters.access !== 'all') {
        const s = getAccessSummary(p.id)
        if (filters.access === 'none' && s.total !== 0) return false
        if (filters.access === 'list_1' && s.list_1 === 0) return false
        if (filters.access === 'list_2' && s.list_2 === 0) return false
        if (filters.access === 'suspended' && s.suspended === 0) return false
      }
      return true
    })
    out.sort((a, b) => {
      if (sort === 'recent') return new Date(b.created_at) - new Date(a.created_at)
      return a.name.localeCompare(b.name)
    })
    return out
  }, [partners, access, search, filters, sort])

  const activeFilterCount = (filters.access !== 'all' ? 1 : 0) + (filters.account !== 'all' ? 1 : 0)

  const handleDelete = async (partner) => {
    if (!(await confirm({
      title: 'Supprimer ce partenaire ?',
      message: `« ${partner.name} » : le compte et tous ses accès seront supprimés. Cette action est irréversible.`,
      confirmLabel: 'Supprimer',
    }))) return
    setDeleting(partner.id)
    try {
      await api.delete(`/partners/${partner.id}`)
      await fetchAll()
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors de la suppression')
    } finally {
      setDeleting(null)
    }
  }

  const handleSuspend = async (partner) => {
    if (!(await confirm({
      title: "Suspendre l'accès de ce partenaire ?",
      message: `L'accès de « ${partner.name} » sera suspendu sur tous les clients qui lui sont attribués. (Ceci ne bloque pas la connexion : pour cela, suspendez le compte depuis Admin → Comptes.)`,
      confirmLabel: "Suspendre l'accès",
    }))) return
    setSuspending(partner.id)
    try {
      await api.post(`/partners/${partner.id}/suspend`)
      await fetchAll()
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors de la suspension')
    } finally {
      setSuspending(null)
    }
  }

  // ── Stat counters ─────────────────────────────────────────────────────────
  const totalSuspended = partners.filter(p => {
    if (isAccountBlocked(p)) return true               // compte bloqué
    const s = getAccessSummary(p.id)
    return s.total > 0 && s.suspended === s.total       // ou accès suspendu partout
  }).length
  const totalActive = partners.length - totalSuspended

  // ── Render ────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-brand-400" />
      </div>
    )
  }

  if (fetchError) {
    return (
      <div className="card p-6 flex items-start gap-3 border border-red-500/30 bg-red-500/5">
        <AlertCircle size={18} className="text-red-400 mt-0.5 shrink-0" />
        <div>
          <p className="text-sm font-semibold text-red-300">Erreur de chargement</p>
          <p className="text-xs text-red-400 mt-1">{fetchError}</p>
          <button onClick={fetchAll} className="mt-3 text-xs text-brand-300 hover:underline">
            Réessayer
          </button>
        </div>
      </div>
    )
  }

  const setF = (k) => (e) => setFilters(p => ({ ...p, [k]: e.target.value }))

  return (
    <div className="animate-slide-up">
      {/* Header */}
      <div className="page-header">
        <div>
          <h1 className="section-title flex items-center gap-2">
            <Users size={20} className="text-brand-400" />
            Partenaires
            <span className="text-sm font-normal text-slate-500">({partners.length})</span>
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            {partners.length} inscrit{partners.length !== 1 ? 's' : ''}
            {totalSuspended > 0 && (
              <span className="ml-2 text-red-400">· {totalSuspended} suspendu{totalSuspended > 1 ? 's' : ''}</span>
            )}
          </p>
        </div>
        {isAdmin && (
          <button
            onClick={() => setInviteOpen(true)}
            className="btn-primary text-xs flex items-center gap-1.5"
          >
            <UserPlus size={13} /> Inviter un partenaire
          </button>
        )}
      </div>

      {/* Stat pills */}
      {partners.length > 0 && (
        <div className="flex gap-3 mb-5 flex-wrap">
          <div className="card px-4 py-2.5 flex items-center gap-2">
            <Users size={13} className="text-brand-400" />
            <span className="text-xs text-slate-300">{partners.length} partenaire{partners.length !== 1 ? 's' : ''}</span>
          </div>
          <div className="card px-4 py-2.5 flex items-center gap-2">
            <Star size={13} className="text-emerald-400" />
            <span className="text-xs text-slate-300">{totalActive} actif{totalActive !== 1 ? 's' : ''}</span>
          </div>
          {totalSuspended > 0 && (
            <div className="card px-4 py-2.5 flex items-center gap-2 border-red-500/20">
              <ShieldOff size={13} className="text-red-400" />
              <span className="text-xs text-red-400">{totalSuspended} suspendu{totalSuspended !== 1 ? 's' : ''}</span>
            </div>
          )}
        </div>
      )}

      {/* Barre d'outils : recherche + recherche avancée + tri (mêmes codes que le Vivier) */}
      {partners.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
            <input
              className="input pl-9"
              placeholder="Rechercher par nom ou email…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
          <button
            onClick={() => setAdvanced(v => !v)}
            className={clsx('btn-ghost gap-1.5 text-sm', advanced && 'text-[var(--accent-text)]')}
            style={advanced ? { background: 'var(--accent-soft)' } : undefined}
            title="Filtres avancés"
          >
            <SlidersHorizontal size={14} /> Recherche avancée
            {activeFilterCount > 0 && (
              <span className="ml-0.5 inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full text-[10px] font-semibold"
                    style={{ background: 'var(--accent)', color: '#fff' }}>
                {activeFilterCount}
              </span>
            )}
          </button>
          <div className="relative">
            <select
              value={sort}
              onChange={e => setSort(e.target.value)}
              className="input appearance-none pr-8 text-sm h-9 py-0"
              title="Trier"
            >
              {SORTS.map(s => <option key={s.k} value={s.k} className="bg-navy-900">{s.l}</option>)}
            </select>
          </div>
        </div>
      )}

      <div className="flex gap-5 items-start">
        {/* Filtres avancés (panneau gauche) */}
        {advanced && partners.length > 0 && (
          <aside className="w-60 shrink-0 card p-4 space-y-4 hidden md:block">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wide flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
                <SlidersHorizontal size={13} /> Filtres
              </h2>
              {activeFilterCount > 0 && (
                <button onClick={() => setFilters(EMPTY_FILTERS)}
                        className="text-[11px] inline-flex items-center gap-1 text-slate-500 hover:text-slate-300">
                  <RotateCcw size={11} /> Réinitialiser
                </button>
              )}
            </div>

            <div>
              <label className="label">Accès clients</label>
              <div className="flex flex-col gap-1">
                {[
                  { v: 'all', l: 'Tous' },
                  { v: 'list_1', l: 'Liste 1' },
                  { v: 'list_2', l: 'Liste 2' },
                  { v: 'suspended', l: 'Accès suspendu' },
                  { v: 'none', l: 'Sans accès' },
                ].map(o => (
                  <button key={o.v} onClick={() => setFilters(p => ({ ...p, access: o.v }))}
                    className={clsx('text-left px-2.5 py-1.5 rounded-md text-xs font-medium transition-all',
                      filters.access === o.v ? 'seg-active' : 'text-slate-400 hover:text-slate-200 bg-white/5')}>
                    {o.l}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="label">Statut du compte</label>
              <div className="flex flex-col gap-1">
                {[
                  { v: 'all', l: 'Tous' },
                  { v: 'active', l: 'Actif' },
                  { v: 'blocked', l: 'Bloqué' },
                ].map(o => (
                  <button key={o.v} onClick={() => setFilters(p => ({ ...p, account: o.v }))}
                    className={clsx('text-left px-2.5 py-1.5 rounded-md text-xs font-medium transition-all',
                      filters.account === o.v ? 'seg-active' : 'text-slate-400 hover:text-slate-200 bg-white/5')}>
                    {o.l}
                  </button>
                ))}
              </div>
            </div>
          </aside>
        )}

        {/* Liste */}
        <div className="flex-1 min-w-0">
          {filtered.length === 0 ? (
            <div className="card p-10 text-center">
              <Users size={32} className="mx-auto text-slate-700 mb-3" />
              <p className="text-slate-400 text-sm">
                {search || activeFilterCount
                  ? 'Aucun résultat pour cette recherche.'
                  : 'Aucun partenaire inscrit. Utilisez le bouton « Inviter » pour en ajouter.'}
              </p>
            </div>
          ) : (
            <>
              <div className="text-[11px] mb-2 px-1" style={{ color: 'var(--text-faint)' }}>
                {filtered.length} partenaire{filtered.length > 1 ? 's' : ''}
              </div>
              <div className="space-y-2">
                {filtered.map(partner => {
                  const summary = getAccessSummary(partner.id)
                  const isSuspendedEverywhere = summary.total > 0 && summary.suspended === summary.total
                  // Statut du COMPTE (bloque la connexion) : prioritaire sur l'accès par client.
                  const accountBlocked = isAccountBlocked(partner)
                  const accountLabel = partner.status === 'disabled' ? 'Compte désactivé' : 'Compte suspendu'

                  return (
                    <div
                      key={partner.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => navigate(`/partners/${partner.id}`)}
                      onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/partners/${partner.id}`) }}
                      className={clsx(
                        'card group flex items-center gap-3 sm:gap-4 px-3 sm:px-4 py-3 cursor-pointer hover:border-white/10 transition-all',
                        (isSuspendedEverywhere || accountBlocked) && 'border-red-500/20 opacity-80'
                      )}
                    >
                      {/* Avatar (style unifié avec le Vivier) */}
                      <div className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold shrink-0"
                           style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>
                        {partner.name?.charAt(0).toUpperCase() || '?'}
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-semibold text-white">{partner.name}</span>
                          {accountBlocked && (
                            <span className="badge bg-red-500/15 text-red-400 border border-red-500/30 text-[10px] flex items-center gap-1" title="Le compte est bloqué : ce partenaire ne peut pas se connecter, quels que soient ses accès clients.">
                              <ShieldOff size={9} /> {accountLabel}
                            </span>
                          )}
                          {isSuspendedEverywhere && (
                            <span className="badge bg-red-500/10 text-red-400 border border-red-500/20 text-[10px] flex items-center gap-1" title="Accès suspendu sur tous les clients qui lui sont attribués.">
                              <ShieldOff size={9} /> Accès suspendu{summary.total ? ` (${summary.total})` : ''}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-slate-500 mt-0.5 truncate">{partner.email}</p>
                        <div className="mt-1.5">
                          <AccessSummary summary={summary} />
                        </div>
                      </div>

                      {/* Date */}
                      <div className="hidden sm:flex items-center gap-1 text-[10px] text-slate-600 shrink-0">
                        <CalendarDays size={10} />
                        {new Date(partner.created_at).toLocaleDateString('fr-FR')}
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-0.5 shrink-0" onClick={e => e.stopPropagation()}>
                        <button
                          onClick={() => navigate(`/partners/${partner.id}`)}
                          className="p-1.5 rounded text-[var(--text-faint)] hover:text-[var(--accent-text)] transition-colors"
                          title={isAdmin ? 'Gérer les clients de ce partenaire' : 'Voir les clients de ce partenaire'}
                        >
                          <Settings2 size={15} strokeWidth={1.75} />
                        </button>

                        {/* Write actions : admin only ; commerce garde la même vue en lecture seule */}
                        {isAdmin && summary.total > 0 && !isSuspendedEverywhere && (
                          <button
                            onClick={() => handleSuspend(partner)}
                            disabled={suspending === partner.id}
                            className="p-1.5 rounded text-[var(--text-faint)] hover:text-red-400 transition-colors"
                            title="Suspendre l'accès à tous ses clients (ne bloque pas la connexion)"
                          >
                            {suspending === partner.id
                              ? <Loader2 size={15} className="animate-spin" />
                              : <ShieldOff size={15} strokeWidth={1.75} />}
                          </button>
                        )}

                        {isAdmin && (
                          <button
                            onClick={() => setEditPartner(partner)}
                            className="p-1.5 rounded text-[var(--text-faint)] hover:text-[var(--accent-text)] transition-colors"
                            title="Modifier"
                          >
                            <Pencil size={15} strokeWidth={1.75} />
                          </button>
                        )}

                        {isAdmin && (
                          <button
                            onClick={() => handleDelete(partner)}
                            disabled={deleting === partner.id}
                            className="p-1.5 rounded text-[var(--text-faint)] hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                            title="Supprimer définitivement"
                          >
                            {deleting === partner.id
                              ? <Loader2 size={15} className="animate-spin" />
                              : <X size={15} />}
                          </button>
                        )}

                        <ChevronRight size={16} className="text-[var(--text-faint)] group-hover:text-[var(--accent-text)] transition-colors" />
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Modals */}
      {editPartner && (
        <PartnerModal
          partner={editPartner}
          onClose={() => setEditPartner(null)}
          onSaved={() => { setEditPartner(null); fetchAll() }}
        />
      )}
      {inviteOpen && <InviteModal onClose={() => setInviteOpen(false)} />}
    </div>
  )
}
