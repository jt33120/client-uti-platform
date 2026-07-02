import { useEffect, useState, useCallback, useMemo } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useConfirm } from '../contexts/ConfirmContext'
import {
  ArrowLeft, Loader2, UserCircle2, Mail, Building2, Search,
  Star, ListChecks, Ban, AlertCircle, ChevronDown, CheckCircle2,
  Package, Sparkles,
} from 'lucide-react'
import clsx from 'clsx'
import { TierBadge } from '../components/badges'

const TIER_OPTIONS = [
  { value: '', label: 'Aucun accès', color: 'text-slate-400' },
  { value: 'list_1', label: 'Liste 1 (prioritaire)', color: 'text-emerald-400' },
  { value: 'list_2', label: 'Liste 2', color: 'text-brand-300' },
  { value: 'suspended', label: 'Suspendu', color: 'text-red-400' },
]

function ApplyPacModal({ partnerId, partnerName, onClose, onApplied }) {
  const confirm = useConfirm()
  const [pacs, setPacs] = useState([])
  const [loading, setLoading] = useState(true)
  const [applying, setApplying] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/pacs')
      .then(r => setPacs(r.data))
      .catch(e => setError(e.response?.data?.detail || 'Erreur'))
      .finally(() => setLoading(false))
  }, [])

  const apply = async (pacId) => {
    if (!(await confirm({
      title: 'Appliquer ce PAC ?',
      message: `Les affectations existantes de ${partnerName} seront écrasées pour les clients du PAC.`,
      confirmLabel: 'Appliquer',
      danger: false,
    }))) return
    setApplying(pacId)
    try {
      await api.post(`/partners/${partnerId}/apply-pac/${pacId}`)
      onApplied()
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur')
    } finally {
      setApplying(null)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-5 w-full max-w-md max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-4">
          <Package size={16} className="text-brand-400" />
          <h2 className="text-sm font-semibold text-white">Appliquer un PAC</h2>
        </div>
        <p className="text-[11px] text-slate-500 mb-4">
          Affecte en bloc une liste de clients pré-configurée à ce partenaire.
        </p>

        {loading ? (
          <div className="text-center py-8"><Loader2 size={16} className="animate-spin text-brand-400 mx-auto" /></div>
        ) : error ? (
          <p className="text-xs text-red-400">{error}</p>
        ) : pacs.length === 0 ? (
          <div className="text-center py-8">
            <Package size={20} className="mx-auto text-slate-700 mb-2" />
            <p className="text-slate-500 text-xs">Aucun PAC créé pour l'instant.</p>
            <Link to="/pacs" className="text-[11px] text-brand-400 hover:underline mt-2 inline-block">
              Créer un PAC →
            </Link>
          </div>
        ) : (
          <div className="space-y-2">
            {pacs.map(pac => (
              <button
                key={pac.id}
                onClick={() => apply(pac.id)}
                disabled={applying === pac.id}
                className="w-full text-left p-3 rounded-lg bg-white/3 border border-white/5 hover:border-brand-500/30 hover:bg-white/5 transition-all disabled:opacity-50"
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-semibold text-white truncate">{pac.name}</div>
                    {pac.description && (
                      <div className="text-[10px] text-slate-500 truncate mt-0.5">{pac.description}</div>
                    )}
                    <div className="text-[10px] text-slate-500 mt-1">
                      {pac.client_count || 0} client{(pac.client_count || 0) > 1 ? 's' : ''}
                    </div>
                  </div>
                  {applying === pac.id ? (
                    <Loader2 size={13} className="animate-spin text-brand-400 shrink-0 ml-2" />
                  ) : (
                    <Sparkles size={13} className="text-brand-400 shrink-0 ml-2" />
                  )}
                </div>
              </button>
            ))}
          </div>
        )}

        <div className="flex justify-end pt-4">
          <button onClick={onClose} className="btn-ghost text-xs">Fermer</button>
        </div>
      </div>
    </div>
  )
}

export default function PartnerDetailPage() {
  const { isAdmin } = useAuth() // commerce : même vue, lecture seule
  const { id } = useParams()
  const navigate = useNavigate()

  const [partner, setPartner] = useState(null)
  const [clients, setClients] = useState([])
  const [loading, setLoading] = useState(true)
  const [savingClient, setSavingClient] = useState(null)
  const [search, setSearch] = useState('')
  const [showPac, setShowPac] = useState(false)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await api.get(`/partners/${id}/clients`)
      setPartner(data.partner)
      setClients(data.clients)
    } catch {
      navigate('/partners')
    } finally {
      setLoading(false)
    }
  }, [id, navigate])

  useEffect(() => { fetchAll() }, [fetchAll])

  const handleTierChange = async (clientId, newTier) => {
    setSavingClient(clientId)
    const prev = clients
    setClients(cs => cs.map(c => c.id === clientId ? { ...c, tier: newTier || null } : c))
    try {
      if (!newTier) {
        await api.delete('/partners/access', { params: { partner_id: id, client_id: clientId } })
      } else {
        await api.put('/partners/access', { partner_id: id, client_id: clientId, tier: newTier })
      }
    } catch (e) {
      setClients(prev)
      alert(e.response?.data?.detail || 'Erreur lors de la mise à jour')
    } finally {
      setSavingClient(null)
    }
  }

  const filtered = useMemo(() =>
    clients.filter(c =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      (c.sector || '').toLowerCase().includes(search.toLowerCase())
    ), [clients, search])

  const summary = useMemo(() => ({
    list_1: clients.filter(c => c.tier === 'list_1').length,
    list_2: clients.filter(c => c.tier === 'list_2').length,
    suspended: clients.filter(c => c.tier === 'suspended').length,
    none: clients.filter(c => !c.tier).length,
  }), [clients])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-brand-400" />
      </div>
    )
  }

  if (!partner) return null

  return (
    <div className="animate-slide-up">
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => navigate('/partners')} className="btn-ghost p-2">
          <ArrowLeft size={16} />
        </button>
        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-brand-500/40 to-emerald-500/40 border border-white/10 flex items-center justify-center text-sm font-bold text-white shrink-0">
          {partner.name?.charAt(0).toUpperCase() || '?'}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-bold text-white">{partner.name}</h1>
            {partner.status && partner.status !== 'active' && (
              <span className="badge bg-red-500/15 text-red-400 border border-red-500/30 text-[10px] flex items-center gap-1"
                title="Le compte est bloqué : ce partenaire ne peut pas se connecter, quels que soient ses accès clients (à gérer dans Admin → Comptes).">
                <AlertCircle size={10} /> {partner.status === 'disabled' ? 'Compte désactivé' : 'Compte suspendu'}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-0.5 flex items-center gap-1.5">
            <Mail size={11} /> {partner.email}
          </p>
        </div>
        {isAdmin && (
          <button
            onClick={() => setShowPac(true)}
            className="btn-primary text-xs flex items-center gap-1.5"
          >
            <Package size={13} /> Appliquer un PAC
          </button>
        )}
      </div>

      {/* Stat pills */}
      <div className="flex gap-3 mb-5 flex-wrap">
        <div className="card px-4 py-2.5 flex items-center gap-2">
          <Star size={13} className="text-emerald-400" />
          <span className="text-xs text-slate-300">{summary.list_1} Liste 1</span>
        </div>
        <div className="card px-4 py-2.5 flex items-center gap-2">
          <ListChecks size={13} className="text-brand-300" />
          <span className="text-xs text-slate-300">{summary.list_2} Liste 2</span>
        </div>
        {summary.suspended > 0 && (
          <div className="card px-4 py-2.5 flex items-center gap-2 border-red-500/20">
            <AlertCircle size={13} className="text-red-400" />
            <span className="text-xs text-red-400">{summary.suspended} suspendu{summary.suspended > 1 ? 's' : ''}</span>
          </div>
        )}
        <div className="card px-4 py-2.5 flex items-center gap-2">
          <Ban size={13} className="text-slate-500" />
          <span className="text-xs text-slate-400">{summary.none} sans accès</span>
        </div>
      </div>

      {/* Search */}
      <div className="relative mb-4">
        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
        <input
          className="input pl-8 text-sm"
          placeholder="Rechercher un client par nom ou secteur…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {/* Clients list */}
      <div className="card p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
            <Building2 size={13} className="text-brand-400" /> Affectation par client
            <span className="text-slate-600 font-normal">({clients.length})</span>
          </h2>
          <Link to="/partners-access" className="text-[11px] text-brand-400 hover:text-brand-300">
            Vue Kanban →
          </Link>
        </div>
        <p className="text-[11px] text-slate-600">
          {isAdmin
            ? `Définissez pour chaque client le niveau d'accès de ${partner.name}.`
            : `Niveaux d'accès de ${partner.name} par client (lecture seule).`}
        </p>

        {filtered.length === 0 ? (
          <div className="text-center py-8">
            <Building2 size={20} className="mx-auto text-slate-700 mb-2" />
            <p className="text-slate-500 text-xs">
              {search ? 'Aucun résultat.' : 'Aucun client.'}
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map(c => (
              <div key={c.id} className="p-3 rounded-lg bg-white/3 border border-white/5 flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-500/30 to-emerald-500/30 border border-white/10 flex items-center justify-center text-xs font-bold text-white shrink-0">
                  {c.name.charAt(0).toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Link to={`/clients/${c.id}`} className="text-xs font-semibold text-white hover:text-brand-300 truncate">
                      {c.name}
                    </Link>
                    <TierBadge tier={c.tier} />
                  </div>
                  {c.sector && (
                    <div className="text-[10px] text-slate-500 mt-0.5">{c.sector}</div>
                  )}
                </div>
                {savingClient === c.id && (
                  <Loader2 size={12} className="animate-spin text-brand-400 shrink-0" />
                )}
                {savingClient !== c.id && c.tier && c.tier !== 'suspended' && (
                  <CheckCircle2 size={12} className="text-emerald-500/60 shrink-0" />
                )}
                {isAdmin ? (
                  <div className="relative w-44 shrink-0">
                    <select
                      value={c.tier || ''}
                      onChange={e => handleTierChange(c.id, e.target.value)}
                      disabled={savingClient === c.id}
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
                  <div className="w-44 shrink-0 text-[11px] px-2 py-1.5 rounded text-center"
                       style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}>
                    {TIER_OPTIONS.find(o => o.value === (c.tier || ''))?.label || 'Aucun accès'}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="pt-3 border-t border-white/5 space-y-1.5 text-[10px] text-slate-600">
          <div className="flex items-center gap-1.5"><Star size={9} className="text-emerald-400" /> Liste 1 : accès prioritaire</div>
          <div className="flex items-center gap-1.5"><ListChecks size={9} className="text-brand-300" /> Liste 2 : accès standard</div>
          <div className="flex items-center gap-1.5"><Ban size={9} className="text-slate-500" /> Aucun accès : non visible pour ce client</div>
        </div>
      </div>

      {showPac && (
        <ApplyPacModal
          partnerId={id}
          partnerName={partner.name}
          onClose={() => setShowPac(false)}
          onApplied={() => { setShowPac(false); fetchAll() }}
        />
      )}
    </div>
  )
}
