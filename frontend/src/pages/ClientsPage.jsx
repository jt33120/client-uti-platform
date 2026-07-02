import { useEffect, useState, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useConfirm } from '../contexts/ConfirmContext'
import { Building2, Plus, Pencil, Trash2, Search, Briefcase, UserCircle2, Mail, ArrowRight, AlertTriangle, ChevronRight, Layers } from 'lucide-react'
import clsx from 'clsx'
import { findSimilarClients } from '../lib/similarity'
import { TierBadge } from '../components/badges'

// Puce périmètre (AMOA, SAD, SI, ...)
function PerimetreBadge({ perimetre }) {
  if (!perimetre) return null
  return (
    <span className="badge text-[10px] bg-white/5 text-slate-300 border border-white/10">
      {perimetre}
    </span>
  )
}

// Ligne cliquable pour un client « feuille » (autonome ou enfant d'une organisation)
function ClientRow({ client, isAdmin, onEdit, onDelete, navigate, asChild = false, labelOverride }) {
  return (
    <div
      className={clsx(
        'card flex items-center gap-4 hover:border-white/10 transition-all duration-150 group cursor-pointer',
        asChild ? 'p-3 bg-white/[0.02]' : 'p-4'
      )}
      onClick={() => navigate(`/clients/${client.id}`)}
    >
      {!asChild && (
        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-brand-500/30 to-emerald-500/30 border border-white/10 flex items-center justify-center text-sm font-bold text-white shrink-0">
          {client.name.charAt(0).toUpperCase()}
        </div>
      )}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <div className={clsx('font-semibold text-white group-hover:text-brand-300 transition-colors', asChild ? 'text-[13px]' : 'text-sm')}>
            {labelOverride || client.name}
          </div>
          <PerimetreBadge perimetre={asChild ? null : client.perimetre} />
          <TierBadge tier={client.tier} />
          {!asChild && client.sector && (
            <span className="text-[10px] text-slate-500 inline-flex items-center gap-1">
              <Briefcase size={10} /> {client.sector}
            </span>
          )}
        </div>
        {client.contact_name && (
          <p className="text-xs text-slate-500 mt-0.5 flex items-center gap-1">
            <UserCircle2 size={10} /> {client.contact_name}
            {client.contact_email && <span className="text-slate-600">· {client.contact_email}</span>}
          </p>
        )}
        {!client.contact_name && client.description && (
          <p className="text-xs text-slate-500 mt-0.5 line-clamp-1">{client.description}</p>
        )}
      </div>
      <div className="flex items-center gap-1">
        {isAdmin && (
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button onClick={e => { e.stopPropagation(); onEdit(client) }} className="btn-ghost p-2">
              <Pencil size={13} />
            </button>
            <button onClick={e => { e.stopPropagation(); onDelete(client.id) }} className="btn-danger p-2">
              <Trash2 size={13} />
            </button>
          </div>
        )}
        <ArrowRight size={13} className="text-slate-700 group-hover:text-brand-400 transition-colors ml-1" />
      </div>
    </div>
  )
}

// Organisation parente : en-tête repliable + périmètres enfants
function ClientGroup({ parent, children, isAdmin, onEdit, onDelete, navigate, forceOpen }) {
  const [open, setOpen] = useState(false)
  const expanded = forceOpen || open

  return (
    <div className="card overflow-hidden">
      <div
        className="p-4 flex items-center gap-4 hover:bg-white/[0.02] transition-colors cursor-pointer group"
        onClick={() => setOpen(o => !o)}
      >
        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-brand-500/30 to-emerald-500/30 border border-white/10 flex items-center justify-center text-sm font-bold text-white shrink-0">
          {parent.name.charAt(0).toUpperCase()}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm font-semibold text-white">{parent.name}</div>
            <span className="badge text-[10px] bg-brand-500/10 text-brand-300 border border-brand-500/20 inline-flex items-center gap-1">
              <Layers size={9} /> {children.length} périmètre{children.length > 1 ? 's' : ''}
            </span>
            {parent.sector && (
              <span className="text-[10px] text-slate-500 inline-flex items-center gap-1">
                <Briefcase size={10} /> {parent.sector}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {isAdmin && (
            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <button onClick={e => { e.stopPropagation(); onEdit(parent) }} className="btn-ghost p-2" title="Modifier l'organisation">
                <Pencil size={13} />
              </button>
              <button onClick={e => { e.stopPropagation(); onDelete(parent.id) }} className="btn-danger p-2" title="Supprimer l'organisation">
                <Trash2 size={13} />
              </button>
            </div>
          )}
          <ChevronRight size={16} className={clsx('text-slate-500 transition-transform', expanded && 'rotate-90')} />
        </div>
      </div>
      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-white/5 pt-3">
          {children.map(c => (
            <ClientRow key={c.id} client={c} isAdmin={isAdmin} asChild
              labelOverride={c.perimetre || c.name}
              onEdit={onEdit} onDelete={onDelete} navigate={navigate} />
          ))}
        </div>
      )}
    </div>
  )
}

function ClientModal({ client, onClose, onSaved, existingClients = [] }) {
  const isEdit = !!client?.id
  const [form, setForm] = useState({
    name: client?.name || '',
    description: client?.description || '',
    sector: client?.sector || '',
    contact_name: client?.contact_name || '',
    contact_email: client?.contact_email || '',
    perimetre: client?.perimetre || '',
    parent_client_id: client?.parent_client_id || '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Organisations parentes possibles : les clients « racine » (sans parent), hors soi-même
  const parentOptions = existingClients.filter(
    c => c.id !== client?.id && !c.parent_client_id
  )

  // Garde-fou anti-frappe : noms ressemblants (Groupama ↔ Groupma)
  const similar = useMemo(
    () => findSimilarClients(form.name, existingClients, { excludeId: client?.id }),
    [form.name, existingClients, client?.id]
  )
  const exactMatch = existingClients.find(
    c => c.id !== client?.id && c.name.toLowerCase() === form.name.trim().toLowerCase()
  )

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const payload = { ...form, parent_client_id: form.parent_client_id || null }
      if (isEdit) {
        await api.patch(`/clients/${client.id}`, payload)
      } else {
        await api.post('/clients', payload)
      }
      onSaved()
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-6 w-full max-w-md max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-white mb-4">
          {isEdit ? 'Modifier le client' : 'Nouveau client'}
        </h2>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="label">Nom *</label>
            <input className="input" required value={form.name}
              onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
              autoComplete="off"
            />
            {exactMatch ? (
              <div className="mt-2 flex items-center gap-2 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
                <AlertTriangle size={13} className="shrink-0" />
                Un client nommé « {exactMatch.name} » existe déjà.
              </div>
            ) : similar.length > 0 && (
              <div className="mt-2 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
                <div className="flex items-center gap-2 mb-1">
                  <AlertTriangle size={13} className="shrink-0" />
                  Noms ressemblants — évitez un doublon :
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {similar.slice(0, 4).map(c => (
                    <button key={c.id} type="button"
                      onClick={() => setForm(p => ({ ...p, parent_client_id: c.parent_client_id ? c.parent_client_id : c.id }))}
                      className="px-2 py-0.5 rounded bg-white/5 border border-white/10 text-slate-200 hover:border-brand-400/40">
                      {c.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label flex items-center gap-1"><Layers size={11} /> Organisation parente</label>
              <select className="input" value={form.parent_client_id}
                onChange={e => setForm(p => ({ ...p, parent_client_id: e.target.value }))}>
                <option value="">— Aucune (racine) —</option>
                {parentOptions.map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Périmètre</label>
              <input className="input" placeholder="AMOA, SAD, SI..." value={form.perimetre}
                onChange={e => setForm(p => ({ ...p, perimetre: e.target.value }))} />
            </div>
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
              {loading ? 'Enregistrement...' : isEdit ? 'Enregistrer' : 'Créer'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function ClientsPage() {
  const { isAdmin } = useAuth()
  const confirm = useConfirm()
  const [clients, setClients] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [modal, setModal] = useState(null) // null | client (edit only)
  const navigate = useNavigate()

  const [error, setError] = useState('')

  const fetchAll = async () => {
    setLoading(true)
    setError('')
    try {
      const { data } = await api.get('/clients')
      setClients(data)
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Erreur de chargement')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchAll() }, [])

  const handleDelete = async (id) => {
    if (!(await confirm({
      title: 'Supprimer ce client ?',
      message: 'Les AOs liés deviendront orphelins. Cette action est irréversible.',
      confirmLabel: 'Supprimer',
    }))) return
    try {
      await api.delete(`/clients/${id}`)
      setClients(p => p.filter(c => c.id !== id))
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors de la suppression')
    }
  }

  // Construction de l'arbre : organisations parentes + périmètres enfants
  const { roots, childrenOf } = useMemo(() => {
    const byId = Object.fromEntries(clients.map(c => [c.id, c]))
    const childrenOf = {}
    clients.forEach(c => {
      if (c.parent_client_id && byId[c.parent_client_id]) {
        (childrenOf[c.parent_client_id] ||= []).push(c)
      }
    })
    Object.values(childrenOf).forEach(arr => arr.sort((a, b) => (a.perimetre || a.name).localeCompare(b.perimetre || b.name)))
    // Racine = client sans parent OU dont le parent n'est pas visible (partenaire)
    const roots = clients
      .filter(c => !c.parent_client_id || !byId[c.parent_client_id])
      .sort((a, b) => a.name.localeCompare(b.name))
    return { roots, childrenOf }
  }, [clients])

  const q = search.toLowerCase()
  const matches = (c) => !q ||
    c.name.toLowerCase().includes(q) ||
    c.sector?.toLowerCase().includes(q) ||
    c.perimetre?.toLowerCase().includes(q)

  const filteredRoots = roots.filter(r => matches(r) || (childrenOf[r.id] || []).some(matches))

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="section-title flex items-center gap-2">
            <Building2 size={20} className="text-brand-400" />
            Clients
            <span className="text-sm font-normal text-slate-500">({roots.length})</span>
          </h1>
          {!isAdmin && (
            <p className="text-sm text-slate-500 mt-0.5">Vos clients accessibles</p>
          )}
        </div>
        {isAdmin && (
          <div className="flex gap-2">
            <Link to="/partners-access" className="btn-ghost">Accès partenaires</Link>
            <Link to="/clients/new" className="btn-primary">
              <Plus size={15} /> Nouveau client
            </Link>
          </div>
        )}
      </div>

      <div className="relative mb-5">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <input type="text" className="input pl-9" placeholder="Rechercher par nom, secteur, périmètre..."
          value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      {error && (
        <div className="mb-4 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-center py-16 text-slate-500 text-sm">Chargement...</div>
      ) : filteredRoots.length === 0 ? (
        <div className="text-center py-16">
          <Building2 size={32} className="mx-auto text-slate-700 mb-3" />
          <p className="text-slate-500 text-sm">
            {search ? 'Aucun résultat' : isAdmin ? 'Aucun client pour le moment' : 'Aucun client accessible'}
          </p>
          {isAdmin && !search && (
            <Link to="/clients/new" className="btn-primary mt-4 mx-auto">
              <Plus size={14} /> Créer le premier client
            </Link>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {filteredRoots.map(r => {
            const kids = childrenOf[r.id] || []
            if (kids.length > 0) {
              return (
                <ClientGroup key={r.id} parent={r} children={kids} isAdmin={isAdmin}
                  forceOpen={!!search}
                  onEdit={(c) => setModal(c)} onDelete={handleDelete} navigate={navigate} />
              )
            }
            return (
              <ClientRow key={r.id} client={r} isAdmin={isAdmin}
                onEdit={(c) => setModal(c)} onDelete={handleDelete} navigate={navigate} />
            )
          })}
        </div>
      )}

      {modal && <ClientModal client={modal} onClose={() => setModal(null)}
        onSaved={() => { setModal(null); fetchAll() }} existingClients={clients} />}

    </div>
  )
}
