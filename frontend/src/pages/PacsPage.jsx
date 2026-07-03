import { useEffect, useState, useMemo, useCallback } from 'react'
import api from '../lib/api'
import { useConfirm } from '../contexts/ConfirmContext'
import {
  Package, Plus, Loader2, Trash2, Pencil, X, Search,
  Building2, ChevronDown,
  Save, Sparkles,
} from 'lucide-react'
import clsx from 'clsx'
import { TierBadge } from '../components/badges'
import { EmptyState } from '../components/EmptyState'

const TIER_OPTIONS = [
  { value: 'list_1', label: 'Liste 1 (prioritaire)' },
  { value: 'list_2', label: 'Liste 2' },
  { value: 'suspended', label: 'Suspendu' },
]

// ── Modal: create PAC ────────────────────────────────────────────────────────
function CreatePacModal({ onClose, onCreated }) {
  const [form, setForm] = useState({ name: '', description: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const { data } = await api.post('/pacs', { ...form, clients: [] })
      onCreated(data)
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-md" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <Package size={14} className="text-brand-400" /> Nouveau PAC
          </h2>
          <button onClick={onClose} className="btn-ghost p-1.5"><X size={14} /></button>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="label">Nom *</label>
            <input
              className="input"
              required
              minLength={2}
              autoFocus
              value={form.name}
              placeholder="ex: PAC Banque/Finance"
              onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
            />
          </div>
          <div>
            <label className="label">Description</label>
            <textarea
              className="input h-20 resize-none"
              value={form.description}
              placeholder="Ce que ce PAC représente (optionnel)"
              onChange={e => setForm(p => ({ ...p, description: e.target.value }))}
            />
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose} className="btn-ghost text-xs px-3">Annuler</button>
            <button type="submit" disabled={loading} className="btn-primary text-xs px-4 flex items-center gap-1.5">
              {loading ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} Créer
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Modal: edit PAC + manage its clients ─────────────────────────────────────
function PacEditor({ pacId, onClose, onChanged }) {
  const [pac, setPac] = useState(null)
  const [allClients, setAllClients] = useState([])
  const [loading, setLoading] = useState(true)
  const [savingMeta, setSavingMeta] = useState(false)
  const [savingClient, setSavingClient] = useState(null)
  const [search, setSearch] = useState('')
  const [meta, setMeta] = useState({ name: '', description: '' })

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [p, cs] = await Promise.all([
        api.get(`/pacs/${pacId}`),
        api.get('/clients'),
      ])
      setPac(p.data)
      setMeta({ name: p.data.name, description: p.data.description || '' })
      setAllClients(cs.data)
    } catch {
      onClose()
    } finally {
      setLoading(false)
    }
  }, [pacId, onClose])

  useEffect(() => { fetchAll() }, [fetchAll])

  const tiersByClient = useMemo(() => {
    if (!pac) return {}
    return Object.fromEntries(pac.clients.map(c => [c.id, c.tier]))
  }, [pac])

  const handleTierChange = async (clientId, newTier) => {
    setSavingClient(clientId)
    try {
      if (newTier === '') {
        await api.delete(`/pacs/${pacId}/clients/${clientId}`)
        setPac(p => ({ ...p, clients: p.clients.filter(c => c.id !== clientId) }))
      } else {
        await api.put(`/pacs/${pacId}/clients`, { client_id: clientId, tier: newTier })
        setPac(p => {
          const existing = p.clients.find(c => c.id === clientId)
          if (existing) {
            return { ...p, clients: p.clients.map(c => c.id === clientId ? { ...c, tier: newTier } : c) }
          }
          const clientData = allClients.find(c => c.id === clientId)
          return { ...p, clients: [...p.clients, { ...clientData, tier: newTier }] }
        })
      }
      onChanged?.()
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur')
    } finally {
      setSavingClient(null)
    }
  }

  const saveMeta = async () => {
    setSavingMeta(true)
    try {
      const { data } = await api.patch(`/pacs/${pacId}`, meta)
      setPac(p => ({ ...p, ...data }))
      onChanged?.()
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur')
    } finally {
      setSavingMeta(false)
    }
  }

  const filtered = useMemo(() =>
    allClients.filter(c =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      (c.sector || '').toLowerCase().includes(search.toLowerCase())
    ), [allClients, search])

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-2xl max-h-[90vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-5 shrink-0">
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <Package size={14} className="text-brand-400" /> Édition du PAC
          </h2>
          <button onClick={onClose} className="btn-ghost p-1.5"><X size={14} /></button>
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <Loader2 size={20} className="animate-spin text-brand-400" />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto pr-1 space-y-5">
            {/* Meta */}
            <div className="space-y-3">
              <div>
                <label className="label">Nom</label>
                <input
                  className="input"
                  value={meta.name}
                  onChange={e => setMeta(m => ({ ...m, name: e.target.value }))}
                />
              </div>
              <div>
                <label className="label">Description</label>
                <textarea
                  className="input h-16 resize-none"
                  value={meta.description}
                  onChange={e => setMeta(m => ({ ...m, description: e.target.value }))}
                />
              </div>
              <div className="flex justify-end">
                <button onClick={saveMeta} disabled={savingMeta} className="btn-ghost text-xs flex items-center gap-1.5">
                  {savingMeta ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
                  Enregistrer
                </button>
              </div>
            </div>

            {/* Clients */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                  <Building2 size={12} className="text-brand-400" /> Clients du PAC
                  <span className="text-slate-600 font-normal">({pac.clients.length})</span>
                </h3>
              </div>
              <div className="relative mb-3">
                <Search size={12} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
                <input
                  className="input pl-8 text-xs py-1.5"
                  placeholder="Rechercher…"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                />
              </div>

              <div className="space-y-1.5 max-h-[40vh] overflow-y-auto">
                {filtered.map(c => {
                  const tier = tiersByClient[c.id] || ''
                  return (
                    <div key={c.id} className="p-2 rounded-lg bg-white/3 border border-white/5 flex items-center gap-2">
                      <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-brand-500/30 to-emerald-500/30 border border-white/10 flex items-center justify-center text-[11px] font-bold text-white shrink-0">
                        {c.name.charAt(0).toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-medium text-white truncate">{c.name}</div>
                        {c.sector && <div className="text-[10px] text-slate-500 truncate">{c.sector}</div>}
                      </div>
                      {tier && <TierBadge tier={tier} />}
                      {savingClient === c.id && <Loader2 size={11} className="animate-spin text-brand-400" />}
                      <div className="relative w-32 shrink-0">
                        <select
                          value={tier}
                          onChange={e => handleTierChange(c.id, e.target.value)}
                          disabled={savingClient === c.id}
                          className="input appearance-none pr-6 text-[11px] py-1 w-full"
                        >
                          <option value="" className="bg-navy-900">Pas dans le PAC</option>
                          {TIER_OPTIONS.map(o => (
                            <option key={o.value} value={o.value} className="bg-navy-900">{o.label}</option>
                          ))}
                        </select>
                        <ChevronDown size={10} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
                      </div>
                    </div>
                  )
                })}
                {filtered.length === 0 && (
                  <p className="text-xs text-slate-500 text-center py-4">Aucun client.</p>
                )}
              </div>
            </div>
          </div>
        )}

        <div className="flex justify-end pt-4 shrink-0">
          <button onClick={onClose} className="btn-primary text-xs">Fermer</button>
        </div>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function PacsPage({ embedded = false }) {
  const confirm = useConfirm()
  const [pacs, setPacs] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [editingPacId, setEditingPacId] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [fetchError, setFetchError] = useState(null)

  const fetchAll = async () => {
    setLoading(true)
    setFetchError(null)
    try {
      const { data } = await api.get('/pacs')
      setPacs(data)
    } catch (e) {
      // Sans catch, un échec affichait « Aucun PAC créé » (faux état vide).
      setFetchError(e.response?.data?.detail || 'Impossible de charger les modèles.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchAll() }, [])

  const handleDelete = async (pac) => {
    if (!(await confirm({
      title: 'Supprimer ce PAC ?',
      message: `« ${pac.name} » sera supprimé. Cela n'affecte pas les partenaires déjà liés à ces clients.`,
      confirmLabel: 'Supprimer',
    }))) return
    setDeleting(pac.id)
    try {
      await api.delete(`/pacs/${pac.id}`)
      await fetchAll()
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur')
    } finally {
      setDeleting(null)
    }
  }

  const filtered = useMemo(() =>
    pacs.filter(p => p.name.toLowerCase().includes(search.toLowerCase())),
    [pacs, search])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-brand-400" />
      </div>
    )
  }

  if (fetchError) {
    return (
      <div className="text-center py-16 text-sm" style={{ color: 'var(--text-muted)' }}>
        <p className="mb-3">{fetchError}</p>
        <button onClick={fetchAll} className="btn-ghost !h-8 !px-3 text-[12px]">Réessayer</button>
      </div>
    )
  }

  return (
    <div className={embedded ? '' : 'animate-slide-up'}>
      {embedded ? (
        <div className="flex items-center justify-between gap-3 mb-4">
          <p className="text-sm text-slate-500">
            Modèles d'habilitation : listes prédéfinies de clients (avec leur priorité) à appliquer à un partenaire en un clic.
          </p>
          <button onClick={() => setShowCreate(true)} className="btn-primary text-xs flex items-center gap-1.5 shrink-0">
            <Plus size={13} /> Nouveau modèle
          </button>
        </div>
      ) : (
        <div className="page-header">
          <div>
            <h1 className="section-title flex items-center gap-2">
              <Package size={20} className="text-brand-400" /> Modèles d'habilitation
            </h1>
            <p className="text-sm text-slate-500 mt-0.5">
              Listes prédéfinies de clients à appliquer à un partenaire en un clic.
            </p>
          </div>
          <button onClick={() => setShowCreate(true)} className="btn-primary text-xs flex items-center gap-1.5">
            <Plus size={13} /> Nouveau modèle
          </button>
        </div>
      )}

      {pacs.length > 0 && (
        <div className="relative mb-4">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
          <input
            className="input pl-8 text-sm"
            placeholder="Rechercher un PAC…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      )}

      {filtered.length === 0 ? (
        <EmptyState
          icon={Package}
          message={search ? 'Aucun résultat.' : 'Aucun PAC créé.'}
          action={!search && (
            <button onClick={() => setShowCreate(true)} className="mt-4 btn-ghost text-xs inline-flex items-center gap-1.5">
              <Sparkles size={12} /> Créer votre premier PAC
            </button>
          )}
        />
      ) : (
        <div className="space-y-2">
          {filtered.map(pac => (
            <div
              key={pac.id}
              onClick={() => setEditingPacId(pac.id)}
              className="card p-4 flex items-center gap-4 hover:border-white/10 transition-all duration-150 cursor-pointer"
            >
              <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-brand-500/30 to-emerald-500/30 border border-white/10 flex items-center justify-center shrink-0">
                <Package size={16} className="text-white" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold text-white truncate">{pac.name}</div>
                {pac.description && (
                  <div className="text-[11px] text-slate-500 truncate mt-0.5">{pac.description}</div>
                )}
                <div className="text-[10px] text-slate-500 mt-1">
                  {pac.client_count} client{pac.client_count > 1 ? 's' : ''}
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
                <button
                  onClick={() => setEditingPacId(pac.id)}
                  className="btn-ghost p-2"
                  title="Modifier"
                >
                  <Pencil size={13} />
                </button>
                <button
                  onClick={() => handleDelete(pac)}
                  disabled={deleting === pac.id}
                  className="btn-ghost p-2 hover:text-red-400 transition-colors"
                  title="Supprimer"
                >
                  {deleting === pac.id
                    ? <Loader2 size={13} className="animate-spin" />
                    : <Trash2 size={13} />}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreate && (
        <CreatePacModal
          onClose={() => setShowCreate(false)}
          onCreated={(pac) => { setShowCreate(false); setEditingPacId(pac.id); fetchAll() }}
        />
      )}
      {editingPacId && (
        <PacEditor
          pacId={editingPacId}
          onClose={() => setEditingPacId(null)}
          onChanged={fetchAll}
        />
      )}
    </div>
  )
}
