import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../lib/api'
import { ArrowLeft, Building2, Loader2, Briefcase, FileText, CheckCircle, UserCircle2, Mail, AlertTriangle, Layers } from 'lucide-react'
import { findSimilarClients } from '../lib/similarity'

export default function NewClientPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState({ name: '', sector: '', description: '', contact_name: '', contact_email: '', parent_client_id: '', perimetre: '', city: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [existingClients, setExistingClients] = useState([])
  const [showSuggestions, setShowSuggestions] = useState(false)

  useEffect(() => {
    api.get('/clients').then(({ data }) => setExistingClients(data)).catch(() => {})
  }, [])

  // Organisations parentes possibles : clients « racine » (sans parent)
  const parentOptions = existingClients.filter(c => !c.parent_client_id)

  // Garde-fou anti-frappe : noms ressemblants (Groupama ↔ Groupma)
  const nameSuggestions = useMemo(
    () => findSimilarClients(form.name, existingClients),
    [form.name, existingClients]
  )

  const handleNameChange = (e) => {
    const val = e.target.value
    setForm(p => ({ ...p, name: val }))
    setShowSuggestions(val.trim().length >= 2)
  }

  const exactMatch = existingClients.find(
    c => c.name.toLowerCase() === form.name.trim().toLowerCase()
  )

  const set = (k) => (e) => setForm(p => ({ ...p, [k]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await api.post('/clients', { ...form, parent_client_id: form.parent_client_id || null })
      navigate('/clients')
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur lors de la création')
    } finally {
      setLoading(false)
    }
  }

  const SECTORS = [
    'Banque & Finance', 'Assurance', 'Énergie & Utilities', 'Retail & Distribution',
    'Industrie & Manufacturing', 'Santé & Pharma', 'Télécoms & Média',
    'Transport & Logistique', 'Secteur Public', 'Immobilier', 'Tech & Startup',
  ]

  return (
    <div className="animate-slide-up">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <button onClick={() => navigate('/clients')} className="btn-ghost p-2">
          <ArrowLeft size={16} />
        </button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Building2 size={22} className="text-brand-400" />
            Nouveau client
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">Ajoutez un compte client pour lui associer des AOs et des partenaires</p>
        </div>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* Left — main fields */}
          <div className="lg:col-span-2 space-y-5">
            <div className="card p-6 space-y-5">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                <Building2 size={13} className="text-brand-400" /> Identité
              </h2>

              <div>
                <label className="label">Nom du client *</label>
                <div className="relative">
                  <input
                    type="text" className="input text-base" required
                    placeholder="ex: Groupama, BNP Paribas, Total Energies..."
                    value={form.name}
                    onChange={handleNameChange}
                    onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
                    onFocus={() => nameSuggestions.length > 0 && setShowSuggestions(true)}
                    autoComplete="off"
                  />
                  {showSuggestions && nameSuggestions.length > 0 && (
                    <div className="absolute top-full left-0 right-0 mt-1 z-50 bg-slate-800 border border-white/10 rounded-lg shadow-xl overflow-hidden">
                      <div className="px-3 py-1.5 text-[10px] text-slate-500 uppercase tracking-widest border-b border-white/5">
                        Clients existants similaires
                      </div>
                      {nameSuggestions.map(c => (
                        <button
                          key={c.id} type="button"
                          onMouseDown={() => navigate(`/clients/${c.id}`)}
                          className="w-full text-left px-4 py-2.5 text-sm text-slate-200 hover:bg-white/5 flex items-center gap-2 transition-colors"
                        >
                          <Building2 size={13} className="text-brand-400 shrink-0" />
                          <span>{c.name}</span>
                          {c.sector && <span className="text-slate-500 text-xs ml-auto">{c.sector}</span>}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {exactMatch && (
                  <div className="mt-2 flex items-center gap-2 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
                    <AlertTriangle size={13} className="shrink-0" />
                    Un client nommé « {exactMatch.name} » existe déjà.
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="label flex items-center gap-1.5">
                    <Layers size={12} className="text-slate-500" /> Organisation parente
                  </label>
                  <select className="input" value={form.parent_client_id} onChange={set('parent_client_id')}>
                    <option value="">— Aucune (organisation racine) —</option>
                    {parentOptions.map(c => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                  <p className="text-[11px] text-slate-500 mt-1">Rattachez ce périmètre à une organisation existante (ex. AGIRC ARRCO).</p>
                </div>
                <div>
                  <label className="label">Périmètre</label>
                  <input type="text" className="input" placeholder="ex: AMOA, SAD, SI, DATA + IA..."
                    value={form.perimetre} onChange={set('perimetre')} />
                  <p className="text-[11px] text-slate-500 mt-1">Type de référencement / prestation.</p>
                </div>
              </div>

              <div>
                <label className="label">Ville / siège</label>
                <input type="text" className="input" placeholder="ex: Paris, Lyon, Nantes…"
                  value={form.city} onChange={set('city')} />
                <p className="text-[11px] text-slate-500 mt-1">Géocodée pour positionner le client sur la carte.</p>
              </div>

              <div>
                <label className="label">Secteur d'activité</label>
                <div className="flex flex-wrap gap-2 mt-2">
                  {SECTORS.map(s => (
                    <button
                      key={s} type="button"
                      onClick={() => setForm(p => ({ ...p, sector: p.sector === s ? '' : s }))}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                        form.sector === s
                          ? 'bg-brand-600/20 border-brand-500/40 text-brand-300'
                          : 'bg-white/5 border-white/10 text-slate-400 hover:text-slate-200 hover:border-white/20'
                      }`}
                    >
                      {form.sector === s && <CheckCircle size={11} className="inline mr-1" />}
                      {s}
                    </button>
                  ))}
                </div>
                <input
                  type="text" className="input mt-3"
                  placeholder="Ou saisissez un secteur personnalisé..."
                  value={form.sector} onChange={set('sector')}
                />
              </div>

              <div>
                <label className="label">Description</label>
                <textarea
                  className="input h-32 resize-none"
                  placeholder="Contexte, taille, organisation, particularités du compte..."
                  value={form.description} onChange={set('description')}
                />
              </div>
            </div>

            <div className="card p-6 space-y-5">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                <UserCircle2 size={13} className="text-brand-400" /> Contact client
              </h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="label flex items-center gap-1.5">
                    <UserCircle2 size={12} className="text-slate-500" /> Nom du contact
                  </label>
                  <input type="text" className="input" placeholder="ex: Jean Dupont"
                    value={form.contact_name} onChange={set('contact_name')} />
                </div>
                <div>
                  <label className="label flex items-center gap-1.5">
                    <Mail size={12} className="text-slate-500" /> Email du contact
                  </label>
                  <input type="email" className="input" placeholder="jean.dupont@societe.fr"
                    value={form.contact_email} onChange={set('contact_email')} />
                </div>
              </div>
            </div>
          </div>

          {/* Right — summary + actions */}
          <div className="space-y-5">
            <div className="card p-6 space-y-4">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                <FileText size={13} className="text-brand-400" /> Récapitulatif
              </h2>

              <div className="space-y-3">
                <div className="flex items-start gap-2.5">
                  <Building2 size={14} className="text-slate-500 mt-0.5 shrink-0" />
                  <div>
                    <div className="text-[10px] text-slate-500 uppercase tracking-wide">Nom</div>
                    <div className="text-sm text-white font-medium">{form.name || '—'}</div>
                  </div>
                </div>
                <div className="flex items-start gap-2.5">
                  <Briefcase size={14} className="text-slate-500 mt-0.5 shrink-0" />
                  <div>
                    <div className="text-[10px] text-slate-500 uppercase tracking-wide">Secteur</div>
                    <div className="text-sm text-white font-medium">{form.sector || '—'}</div>
                  </div>
                </div>
              </div>
            </div>

            {error && (
              <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">
                {error}
              </div>
            )}

            <div className="flex flex-col gap-2">
              <button type="submit" disabled={loading || !form.name.trim()} className="btn-primary w-full justify-center py-3">
                {loading
                  ? <><Loader2 size={15} className="animate-spin" />Création...</>
                  : <><Building2 size={15} />Créer le client</>}
              </button>
              <button type="button" onClick={() => navigate('/clients')} className="btn-ghost w-full justify-center py-2.5">
                Annuler
              </button>
            </div>
          </div>
        </div>
      </form>
    </div>
  )
}
