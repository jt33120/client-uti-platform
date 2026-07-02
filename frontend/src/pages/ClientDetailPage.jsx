import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import {
  ArrowLeft, Building2, Briefcase, UserCircle2, Mail, Pencil, Loader2,
  FileText, Plus, Euro, MapPin, Clock, Star, ListChecks, Ban,
  ChevronDown, Users, CheckCircle2
} from 'lucide-react'
import clsx from 'clsx'

const TIER_OPTIONS = [
  { value: '', label: 'Aucun accès', color: 'text-slate-400' },
  { value: 'list_1', label: 'Liste 1 (prioritaire)', color: 'text-emerald-400' },
  { value: 'list_2', label: 'Liste 2', color: 'text-brand-300' },
  { value: 'suspended', label: 'Suspendu', color: 'text-red-400' },
]

function ClientEditModal({ client, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: client.name || '',
    description: client.description || '',
    sector: client.sector || '',
    contact_name: client.contact_name || '',
    contact_email: client.contact_email || '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const { data } = await api.patch(`/clients/${client.id}`, form)
      onSaved(data)
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur lors de la mise à jour')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-md" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-white mb-4">Modifier le client</h2>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="label">Nom *</label>
            <input className="input" required value={form.name}
              onChange={e => setForm(p => ({ ...p, name: e.target.value }))} />
          </div>
          <div>
            <label className="label">Secteur</label>
            <input className="input" placeholder="Banque, Énergie, Retail..." value={form.sector}
              onChange={e => setForm(p => ({ ...p, sector: e.target.value }))} />
          </div>
          <div>
            <label className="label">Description</label>
            <textarea className="input h-24 resize-none" value={form.description}
              onChange={e => setForm(p => ({ ...p, description: e.target.value }))} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label flex items-center gap-1"><UserCircle2 size={11} /> Contact</label>
              <input className="input" placeholder="Nom du contact" value={form.contact_name}
                onChange={e => setForm(p => ({ ...p, contact_name: e.target.value }))} />
            </div>
            <div>
              <label className="label flex items-center gap-1"><Mail size={11} /> Email</label>
              <input className="input" type="email" placeholder="email@..." value={form.contact_email}
                onChange={e => setForm(p => ({ ...p, contact_email: e.target.value }))} />
            </div>
          </div>
          {error && <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</div>}
          <div className="flex gap-2 justify-end pt-2">
            <button type="button" onClick={onClose} className="btn-ghost">Annuler</button>
            <button type="submit" disabled={loading} className="btn-primary">
              {loading ? 'Enregistrement...' : 'Enregistrer'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function ClientDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { isAdmin, isStaff } = useAuth()

  const [client, setClient] = useState(null)
  const [aos, setAos] = useState([])
  const [partners, setPartners] = useState([]) // { id, name, email, tier }
  const [loadingClient, setLoadingClient] = useState(true)
  const [loadingAos, setLoadingAos] = useState(true)
  const [loadingPartners, setLoadingPartners] = useState(true)
  const [showEdit, setShowEdit] = useState(false)
  const [savingPartner, setSavingPartner] = useState(null) // partner id being updated

  const fetchClient = useCallback(async () => {
    try {
      const { data } = await api.get(`/clients/${id}`)
      setClient(data)
    } catch {
      navigate('/clients')
    } finally {
      setLoadingClient(false)
    }
  }, [id, navigate])

  const fetchAos = useCallback(async () => {
    try {
      const { data } = await api.get('/aos')
      setAos(data.filter(ao => ao.client_id === id))
    } catch {
      // ignore
    } finally {
      setLoadingAos(false)
    }
  }, [id])

  const fetchPartners = useCallback(async () => {
    // Staff only — commerce gets the same view in read-only
    if (!isStaff) { setLoadingPartners(false); return }
    try {
      const { data } = await api.get(`/clients/${id}/partners`)
      setPartners(data)
    } catch {
      // ignore
    } finally {
      setLoadingPartners(false)
    }
  }, [id, isStaff])

  useEffect(() => {
    fetchClient()
    fetchAos()
    fetchPartners()
  }, [fetchClient, fetchAos, fetchPartners])

  const handleTierChange = async (partnerId, newTier) => {
    setSavingPartner(partnerId)
    const prev = partners
    // Optimistic update
    setPartners(ps => ps.map(p => p.id === partnerId ? { ...p, tier: newTier || null } : p))
    try {
      if (!newTier) {
        await api.delete('/partners/access', { params: { partner_id: partnerId, client_id: id } })
      } else {
        await api.put('/partners/access', { partner_id: partnerId, client_id: id, tier: newTier })
      }
    } catch (e) {
      setPartners(prev) // rollback
      alert(e.response?.data?.detail || 'Erreur lors de la mise à jour')
    } finally {
      setSavingPartner(null)
    }
  }

  if (loadingClient) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-brand-400" />
      </div>
    )
  }

  if (!client) return null

  const openAos = aos.filter(a => a.status === 'open')
  const closedAos = aos.filter(a => a.status !== 'open')

  return (
    <div className="animate-slide-up">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <button onClick={() => navigate('/clients')} className="btn-ghost p-2">
          <ArrowLeft size={16} />
        </button>
        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-brand-500/30 to-emerald-500/30 border border-white/10 flex items-center justify-center text-sm font-bold text-white shrink-0">
          {client.name.charAt(0).toUpperCase()}
        </div>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-white">{client.name}</h1>
          {client.sector && (
            <p className="text-sm text-slate-500 mt-0.5 flex items-center gap-1.5">
              <Briefcase size={12} /> {client.sector}
            </p>
          )}
        </div>
        {isAdmin && (
          <button onClick={() => setShowEdit(true)} className="btn-ghost flex items-center gap-1.5">
            <Pencil size={14} /> Modifier
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* Left: Client info + AOs */}
        <div className="lg:col-span-2 space-y-5">

          {/* Client info */}
          <div className="card p-5 space-y-4">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
              <Building2 size={13} className="text-brand-400" /> Informations
            </h2>
            {client.description && (
              <p className="text-sm text-slate-400 leading-relaxed">{client.description}</p>
            )}
            {(client.contact_name || client.contact_email) && (
              <div className="flex items-center gap-3 pt-1">
                {client.contact_name && (
                  <span className="text-xs text-slate-400 flex items-center gap-1.5">
                    <UserCircle2 size={13} className="text-slate-500" /> {client.contact_name}
                  </span>
                )}
                {client.contact_email && (
                  <a href={`mailto:${client.contact_email}`}
                    className="text-xs text-brand-400 hover:text-brand-300 flex items-center gap-1.5">
                    <Mail size={13} /> {client.contact_email}
                  </a>
                )}
              </div>
            )}
          </div>

          {/* AOs */}
          <div className="card p-5 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                <FileText size={13} className="text-brand-400" /> Appels d'Offres
                <span className="text-slate-600 font-normal">({aos.length})</span>
              </h2>
              {isStaff && (
                <Link to="/aos/new" className="btn-ghost text-xs py-1 px-2.5 flex items-center gap-1">
                  <Plus size={12} /> Nouvel AO
                </Link>
              )}
            </div>

            {loadingAos ? (
              <div className="text-center py-6 text-slate-500 text-sm">Chargement...</div>
            ) : aos.length === 0 ? (
              <div className="text-center py-8">
                <FileText size={24} className="mx-auto text-slate-700 mb-2" />
                <p className="text-slate-500 text-sm">Aucun AO pour ce client</p>
              </div>
            ) : (
              <div className="space-y-2">
                {openAos.map(ao => <AORow key={ao.id} ao={ao} />)}
                {closedAos.map(ao => <AORow key={ao.id} ao={ao} />)}
              </div>
            )}
          </div>
        </div>

        {/* Right: Partner access — admin edits, commerce reads */}
        {isStaff && (
          <div className="space-y-5">
            <div className="card p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                  <Users size={13} className="text-brand-400" /> Partenaires
                </h2>
                <Link to="/partners-access" className="text-[11px] text-brand-400 hover:text-brand-300">
                  Vue globale →
                </Link>
              </div>
              <p className="text-[11px] text-slate-600">
                {isAdmin
                  ? "Définissez le niveau d'accès de chaque partenaire pour ce client."
                  : "Niveaux d'accès des partenaires pour ce client (lecture seule)."}
              </p>

              {loadingPartners ? (
                <div className="text-center py-6">
                  <Loader2 size={16} className="animate-spin text-brand-400 mx-auto" />
                </div>
              ) : partners.length === 0 ? (
                <div className="text-center py-6">
                  <UserCircle2 size={20} className="mx-auto text-slate-700 mb-2" />
                  <p className="text-slate-500 text-xs">Aucun partenaire inscrit</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {partners.map(p => (
                    <div key={p.id} className="p-2.5 rounded-lg bg-white/3 border border-white/5 space-y-2">
                      <div className="flex items-center gap-2">
                        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-brand-500/40 to-emerald-500/40 border border-white/10 flex items-center justify-center text-[11px] font-bold text-white shrink-0">
                          {p.name?.charAt(0).toUpperCase() || '?'}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-semibold text-white truncate">{p.name}</div>
                          <div className="text-[10px] text-slate-500 truncate">{p.email}</div>
                        </div>
                        {savingPartner === p.id && (
                          <Loader2 size={12} className="animate-spin text-brand-400 shrink-0" />
                        )}
                        {savingPartner !== p.id && p.tier && (
                          <CheckCircle2 size={12} className="text-emerald-500/60 shrink-0" />
                        )}
                      </div>
                      {isAdmin ? (
                        <div className="relative">
                          <select
                            value={p.tier || ''}
                            onChange={e => handleTierChange(p.id, e.target.value)}
                            disabled={savingPartner === p.id}
                            className="input appearance-none pr-7 text-[11px] py-1.5 w-full"
                          >
                            {TIER_OPTIONS.map(o => (
                              <option key={o.value} value={o.value} className="bg-navy-900">
                                {o.label}
                              </option>
                            ))}
                          </select>
                          <ChevronDown size={11} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
                        </div>
                      ) : (
                        <div className="text-[11px] px-2 py-1 rounded"
                             style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}>
                          {TIER_OPTIONS.find(o => o.value === (p.tier || ''))?.label || 'Aucun accès'}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="pt-2 border-t border-white/5 space-y-1.5 text-[10px] text-slate-600">
                <div className="flex items-center gap-1.5"><Star size={9} className="text-emerald-400" /> Liste 1 : accès prioritaire</div>
                <div className="flex items-center gap-1.5"><ListChecks size={9} className="text-brand-300" /> Liste 2 : accès standard</div>
                <div className="flex items-center gap-1.5"><Ban size={9} className="text-slate-500" /> Aucun accès : non visible pour ce client</div>
              </div>
            </div>
          </div>
        )}
      </div>

      {showEdit && (
        <ClientEditModal
          client={client}
          onClose={() => setShowEdit(false)}
          onSaved={(updated) => { setClient(updated); setShowEdit(false) }}
        />
      )}
    </div>
  )
}

function AORow({ ao }) {
  const isOpen = ao.status === 'open'
  return (
    <Link
      to={`/aos/${ao.id}`}
      className="flex items-center gap-3 p-3 rounded-lg bg-white/3 border border-white/5 hover:border-white/12 hover:bg-white/5 transition-all duration-150 group"
    >
      <div className={clsx(
        'w-2 h-2 rounded-full shrink-0',
        isOpen ? 'bg-emerald-500' : 'bg-slate-600'
      )} />
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-white group-hover:text-brand-300 transition-colors truncate">
          {ao.title}
        </div>
        <div className="flex items-center gap-3 mt-0.5 text-[10px] text-slate-500">
          {ao.budget_max && <span className="flex items-center gap-1"><Euro size={9} />{ao.budget_max}€/j</span>}
          {ao.location && <span className="flex items-center gap-1"><MapPin size={9} />{ao.location}</span>}
          {ao.duration && <span className="flex items-center gap-1"><Clock size={9} />{ao.duration}</span>}
        </div>
      </div>
      <span className={clsx(
        'badge text-[10px]',
        isOpen
          ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
          : 'bg-slate-500/10 text-slate-500 border border-slate-600/20'
      )}>
        {isOpen ? 'Ouvert' : 'Fermé'}
      </span>
    </Link>
  )
}
