import { useEffect, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import api from '../lib/api'
import {
  ArrowLeft, FileText, Loader2, ChevronDown, Building2,
  Euro, MapPin, Clock, Zap, CheckCircle, CalendarClock,
  Sparkles, UploadCloud, X, Target
} from 'lucide-react'
import ScoringPriorities, { DEFAULT_STARS } from '../components/ScoringPriorities'
import { formatDateFR } from '../lib/date'

// Minuscule, sans accents, ponctuation (tirets, slashs…) ramenée à des espaces —
// pour rapprocher un nom de client (« AGIRC-ARRCO » ≈ « AGIRC ARRCO »).
const _norm = (s) => (s || '').toString()
  .normalize('NFKD').replace(/[̀-ͯ]/g, '')
  .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()

// Rapproche le nom de client détecté par l'IA d'un client existant, pour le
// pré-sélectionner (ex. « CGM » → « CMA CGM »). Renvoie l'id ou '' si rien de sûr.
function matchClientId(hint, clients) {
  const h = _norm(hint)
  if (!h || !clients?.length) return ''
  let best = null, bestScore = -1
  for (const c of clients) {
    const n = _norm(c.name)
    if (!n) continue
    let score = -1
    if (n === h) score = 100
    // Inclusion mutuelle, mais seulement si le plus court fait ≥ 3 caractères
    // (évite qu'un sigle « it »/« si » ne matche n'importe quel client).
    else if ((n.includes(h) || h.includes(n)) && Math.min(n.length, h.length) >= 3) {
      score = 50 - Math.abs(n.length - h.length)
    }
    if (score > bestScore) { bestScore = score; best = c }
  }
  return bestScore >= 0 ? best.id : ''
}

export default function NewAOPage() {
  const navigate = useNavigate()
  const { state } = useLocation()
  const [clients, setClients] = useState([])
  const AO_TYPES = ['Assurance', 'Banque / Finance', 'IT / Dev', 'Énergie', 'Retail', 'Public', 'Santé', 'Autre']

  const [form, setForm] = useState({
    client_id: '', title: '', description: '', skills_required: '', reference: '',
    budget_max: '', location: '', duration: '', context: '', ao_type: '', deadline: '', work_mode: '',
    langue_requise: '',
    ...(state?.assistantPrefill || {}),  // assistant may pre-fill (never submits)
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const errorRef = useRef(null)
  // Envoi des e-mails à la création : décoché par défaut (comportement sûr,
  // l'envoi reste un acte volontaire). Coché → la liste 1 est notifiée juste
  // après la création (la liste 2 suit selon le délai des réglages admin).
  const [notifyNow, setNotifyNow] = useState(false)

  // Priorités de matching (étoiles 1-5). Pré-remplies par défaut, ajustables ;
  // l'IA peut les suggérer depuis la source. On ne les envoie que si touchées.
  const [stars, setStars] = useState(DEFAULT_STARS)
  const [scoringTouched, setScoringTouched] = useState(false)
  const onStars = (s) => { setStars(s); setScoringTouched(true) }

  // AI draft step
  const [aiText, setAiText] = useState('')
  const [aiFiles, setAiFiles] = useState([])
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState('')
  const [aiDone, setAiDone] = useState(false)
  const [aiModel, setAiModel] = useState(null) // { model, fallback }
  const [dragOver, setDragOver] = useState(false)

  // Formats acceptés pour la source (cohérent avec l'input fichier et le backend).
  const ACCEPTED_EXT = ['.pdf', '.docx', '.xlsx', '.txt', '.csv']
  const addFiles = (fileList) => {
    const incoming = Array.from(fileList || []).filter(f =>
      ACCEPTED_EXT.some(ext => f.name.toLowerCase().endsWith(ext))
    )
    if (incoming.length === 0) {
      setAiError('Format non pris en charge. Acceptés : PDF, DOCX, XLSX, TXT, CSV.')
      return
    }
    setAiFiles(prev => [...prev, ...incoming])
  }
  const onDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files)
  }

  const generateFromSource = async () => {
    setAiError(''); setAiDone(false)
    if (!aiText.trim() && aiFiles.length === 0) {
      setAiError("Collez le texte d'un email ou ajoutez un fichier (PDF, DOCX, XLSX)."); return
    }
    setAiLoading(true)
    try {
      const fd = new FormData()
      fd.append('pasted_text', aiText)
      aiFiles.forEach(f => fd.append('files', f))
      const { data } = await api.post('/aos/draft', fd)
      // Client détecté par l'IA → pré-sélection dans la liste (Sullyvan : « s'il
      // y voit CGM il me le propose »). Ne remplace pas un client déjà choisi.
      const matchedClient = matchClientId(data.client_hint, clients)
      setForm(p => ({
        ...p,
        client_id: p.client_id || matchedClient || p.client_id,
        title: data.title || p.title,
        description: data.description || p.description,
        skills_required: data.skills_required || p.skills_required,
        reference: data.reference || p.reference,
        ao_type: data.ao_type || p.ao_type,
        budget_max: data.budget_max != null ? String(data.budget_max) : p.budget_max,
        location: data.location || p.location,
        work_mode: data.work_mode || p.work_mode,
        duration: data.duration || p.duration,
        deadline: data.deadline || p.deadline,
        context: data.context || p.context,
        langue_requise: data.langue_requise || p.langue_requise,
      }))
      if (data.scoring_stars && Object.keys(data.scoring_stars).length) {
        setStars(p => ({ ...p, ...data.scoring_stars }))
        setScoringTouched(true)
      }
      setAiModel(data._ai_model ? { model: data._ai_model, fallback: !!data._ai_fallback } : null)
      setAiDone(true)
    } catch (err) {
      setAiError(err.response?.data?.detail || 'Échec de la génération. Réessayez.')
    } finally {
      setAiLoading(false)
    }
  }

  useEffect(() => {
    api.get('/clients').then(r => {
      setClients(r.data)
      if (r.data.length === 1) setForm(p => ({ ...p, client_id: r.data[0].id }))
    }).catch(() => {
      // Sans ce catch, un échec affichait « Aucun client disponible : créez-en
      // un d'abord » — trompeur, le vrai problème étant réseau/serveur.
      setError('Impossible de charger la liste des clients. Rechargez la page ou réessayez plus tard.')
    })
  }, [])

  const set = (k) => (e) => setForm(p => ({ ...p, [k]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    // Explicit, visible validation (no reliance on the native browser tooltip,
    // which is English-only and invisible if the field is off-screen).
    const missing = []
    if (!form.client_id) missing.push('Client')
    if (!form.title.trim()) missing.push('Titre de la mission')
    if (!form.description.trim()) missing.push('Description')
    if (!form.skills_required.trim()) missing.push('Compétences requises')
    if (missing.length) {
      setError(`Champ${missing.length > 1 ? 's' : ''} obligatoire${missing.length > 1 ? 's' : ''} manquant${missing.length > 1 ? 's' : ''} : ${missing.join(', ')}.`)
      errorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      return
    }
    setLoading(true)
    try {
      const payload = { ...form }
      if (!payload.reference?.trim()) delete payload.reference
      if (!payload.budget_max) delete payload.budget_max
      else payload.budget_max = parseInt(payload.budget_max)
      if (!payload.deadline) delete payload.deadline
      if (!payload.work_mode) delete payload.work_mode
      if (!payload.langue_requise?.trim()) delete payload.langue_requise
      if (scoringTouched) payload.scoring_overrides = { stars }
      const { data } = await api.post('/aos', payload)
      // Persiste les pièces jointes d'origine pour les retrouver à l'édition.
      if (aiFiles.length) {
        try {
          const fd = new FormData()
          aiFiles.forEach(f => fd.append('files', f))
          await api.post(`/aos/${data.id}/sources`, fd)
        } catch { /* non bloquant */ }
      }
      // Envoi immédiat aux partenaires de la liste 1 si demandé (best-effort :
      // un échec d'envoi ne doit pas bloquer la création de l'AO).
      if (notifyNow) {
        try { await api.post(`/aos/${data.id}/notify`) } catch { /* non bloquant */ }
      }
      navigate(`/aos/${data.id}`)
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur lors de la création')
    } finally {
      setLoading(false)
    }
  }

  const selectedClient = clients.find(c => c.id === form.client_id)
  const skillsList = form.skills_required.split(',').map(s => s.trim()).filter(Boolean)

  return (
    <div className="animate-slide-up">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <button onClick={() => navigate(-1)} className="btn-ghost p-2">
          <ArrowLeft size={16} />
        </button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <FileText size={22} className="text-brand-400" />
            Nouvel Appel d'Offres
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">Les compétences renseignées alimentent directement le scoring IA</p>
        </div>
      </div>

      {/* AI generation step — paste an email / drop attachments → pre-fill the form */}
      <div
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={e => { e.preventDefault(); setDragOver(false) }}
        onDrop={onDrop}
        className={`card p-6 mb-6 border bg-gradient-to-br from-violet-500/[0.08] to-brand-500/[0.04] transition-colors ${dragOver ? 'border-violet-400 ring-2 ring-violet-400/40' : 'border-violet-500/20'}`}>
        <div className="flex items-start gap-3 mb-4">
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-violet-500/30 to-brand-500/30 border border-violet-400/20 flex items-center justify-center shrink-0">
            <Sparkles size={18} className="text-violet-300" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-white">Générer l'AO avec l'IA</h2>
            <p className="text-xs text-slate-400 mt-0.5 leading-relaxed">
              Collez l'email reçu et/ou ajoutez la pièce jointe (PDF, DOCX, XLSX). L'IA pré-remplit le formulaire ci-dessous,
              vous vérifiez et ajustez avant d'enregistrer.
            </p>
          </div>
        </div>

        <textarea
          className="input h-28 resize-none text-sm"
          placeholder="Collez ici le texte de l'email de l'appel d'offres…"
          value={aiText} onChange={e => setAiText(e.target.value)}
        />

        <div className="flex flex-wrap items-center gap-2 mt-3">
          <label className="btn-ghost cursor-pointer text-sm">
            <UploadCloud size={15} /> Ajouter un fichier
            <input
              type="file" multiple accept=".pdf,.docx,.xlsx,.txt,.csv" className="hidden"
              onChange={e => { addFiles(e.target.files); e.target.value = '' }}
            />
          </label>
          <span className="text-[11px] text-slate-500">
            ou glissez-déposez le fichier ici
          </span>
          {aiFiles.map((f, i) => (
            <span key={i} className="badge bg-white/5 border border-white/10 text-slate-300 text-xs">
              <FileText size={11} className="inline mr-1" />{f.name}
              <button
                type="button"
                onClick={() => setAiFiles(prev => prev.filter((_, j) => j !== i))}
                className="ml-1.5 text-slate-500 hover:text-red-400"
              >
                <X size={11} />
              </button>
            </span>
          ))}
          <button
            type="button" onClick={generateFromSource} disabled={aiLoading}
            className="btn-primary text-sm ml-auto"
          >
            {aiLoading
              ? <><Loader2 size={14} className="animate-spin" />Génération…</>
              : <><Sparkles size={14} />Générer l'AO</>}
          </button>
        </div>

        {aiError && <p className="text-xs text-red-400 mt-3">{aiError}</p>}
        {aiDone && !aiError && (
          <p className="text-xs text-emerald-400 mt-3 flex items-center gap-1.5">
            <CheckCircle size={12} /> Champs pré-remplis : vérifiez et ajustez ci-dessous avant d'enregistrer.
          </p>
        )}
        {aiDone && aiModel && (
          <p className="text-[10px] mt-1" style={{ color: aiModel.fallback ? '#f59e0b' : 'var(--text-faint)' }}>
            {aiModel.fallback ? '⚠️ ' : ''}Généré par {aiModel.model}{aiModel.fallback ? ' (modèle de repli)' : ''}
          </p>
        )}
      </div>

      <form onSubmit={handleSubmit} noValidate>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* Left — main fields */}
          <div className="lg:col-span-2 space-y-5">

            {/* Client & title */}
            <div className="card p-6 space-y-5">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                <Building2 size={13} className="text-brand-400" /> Client & Mission
              </h2>

              <div>
                <label className="label">Client *</label>
                <div className="relative">
                  <select
                    value={form.client_id} onChange={set('client_id')} required
                    className="input appearance-none pr-9 text-base"
                  >
                    <option value="" className="bg-navy-900">Sélectionner un client</option>
                    {clients.map(c => (
                      <option key={c.id} value={c.id} className="bg-navy-900">
                        {c.name}{c.sector ? ` · ${c.sector}` : ''}
                      </option>
                    ))}
                  </select>
                  <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
                </div>
                {clients.length === 0 && (
                  <p className="text-[11px] text-amber-400 mt-1.5">
                    Aucun client disponible : créez-en un d'abord depuis « Clients ».
                  </p>
                )}
              </div>

              <div>
                <label className="label">Type d'AO</label>
                <div className="relative">
                  <select
                    value={form.ao_type} onChange={set('ao_type')}
                    className="input appearance-none pr-9"
                  >
                    <option value="" className="bg-navy-900">Sélectionner un type</option>
                    {AO_TYPES.map(t => (
                      <option key={t} value={t} className="bg-navy-900">{t}</option>
                    ))}
                  </select>
                  <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
                </div>
              </div>

              <div>
                <label className="label">Référence client / consultation</label>
                <input
                  type="text" className="input"
                  placeholder="ex: Marché Spécifique n°23915SA230MS"
                  value={form.reference} onChange={set('reference')}
                />
                <p className="text-[11px] text-slate-600 mt-1">Référence du marché / de la consultation. Sert aussi à rechercher l'AO.</p>
              </div>

              <div>
                <label className="label">Titre de la mission *</label>
                <input
                  type="text" className="input text-base" required
                  placeholder="ex: Data Engineer Senior - Modernisation Data Platform"
                  value={form.title} onChange={set('title')}
                />
              </div>

              <div>
                <label className="label">Description *</label>
                <textarea
                  className="input h-36 resize-none"
                  placeholder="Contexte de la mission, responsabilités, équipe, environnement technique..."
                  value={form.description} onChange={set('description')} required
                />
              </div>
            </div>

            {/* Skills */}
            <div className="card p-6 space-y-4">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                <Zap size={13} className="text-brand-400" /> Compétences requises (IA)
              </h2>
              <div>
                <input
                  type="text" className="input text-base" required
                  placeholder="React, TypeScript, Node.js, PostgreSQL, Docker..."
                  value={form.skills_required} onChange={set('skills_required')}
                />
                <p className="text-[11px] text-slate-600 mt-1.5">Séparées par des virgules · utilisées pour le scoring IA</p>
              </div>
              {skillsList.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {skillsList.map((s, i) => (
                    <span key={i} className="badge bg-brand-600/10 text-brand-300 border border-brand-500/15 text-xs">
                      <CheckCircle size={10} className="inline mr-1" />{s}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Context */}
            <div className="card p-6 space-y-4">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                <FileText size={13} className="text-brand-400" /> Contexte additionnel (IA)
              </h2>
              <textarea
                className="input h-28 resize-none"
                placeholder="Secteur métier, culture d'équipe, points de vigilance, RGPD, stack tech spécifique, urgence..."
                value={form.context} onChange={set('context')}
              />
              <p className="text-[11px] text-slate-600">Ce texte est transmis tel quel à l'IA pour affiner le scoring</p>
            </div>

            {/* Priorités de matching — étoiles d'importance par critère */}
            <div className="card p-6 space-y-4">
              <div>
                <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
                  <Target size={13} className="text-brand-400" /> Priorités de matching
                </h2>
                <p className="text-[11px] text-slate-600 mt-1">
                  Notez l'importance de chaque critère : les poids s'équilibrent tout seuls.
                  {scoringTouched && aiDone ? ' Suggestion IA pré-remplie, ajustez si besoin.' : ''}
                </p>
              </div>
              <ScoringPriorities stars={stars} onStarsChange={onStars} />
            </div>
          </div>

          {/* Right — conditions + summary */}
          <div className="space-y-5">

            {/* Conditions */}
            <div className="card p-6 space-y-4">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Conditions</h2>

              <div>
                <label className="label flex items-center gap-1.5">
                  <CalendarClock size={12} style={{ color: 'var(--danger)' }} /> Date limite de réponse
                </label>
                <input type="date" className="input"
                  value={form.deadline} onChange={set('deadline')} />
              </div>

              <div>
                <label className="label flex items-center gap-1.5">
                  <Euro size={12} className="text-emerald-400" /> Budget max (€/jour)
                </label>
                <input type="number" className="input" placeholder="700"
                  value={form.budget_max} onChange={set('budget_max')} min="0" />
              </div>

              <div>
                <label className="label flex items-center gap-1.5">
                  <Clock size={12} className="text-amber-400" /> Durée
                </label>
                <input type="text" className="input" placeholder="3 mois renouvelable"
                  value={form.duration} onChange={set('duration')} />
              </div>

              <div>
                <label className="label flex items-center gap-1.5">
                  <MapPin size={12} className="text-brand-400" /> Localisation
                </label>
                <input type="text" className="input" placeholder="Paris 8e, Lyon, Nantes..."
                  value={form.location} onChange={set('location')} />
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
                <label className="label">Langue requise</label>
                <input type="text" className="input" placeholder="ex. Anglais courant (impératif)"
                  value={form.langue_requise} onChange={set('langue_requise')} />
                <p className="text-[10px] text-slate-500 mt-1">
                  Si le client l'exige. Le matching signale les CV où cette langue n'est pas détectée.
                </p>
              </div>
            </div>

            {/* Preview */}
            <div className="card p-6 space-y-3">
              <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Récapitulatif</h2>

              {selectedClient && (
                <div className="flex items-center gap-2 p-2.5 bg-white/3 border border-white/5 rounded-lg">
                  <div className="w-7 h-7 rounded-full bg-gradient-to-br from-brand-500/40 to-emerald-500/40 border border-white/10 flex items-center justify-center text-xs font-bold text-white shrink-0">
                    {selectedClient.name.charAt(0)}
                  </div>
                  <div>
                    <div className="text-xs font-semibold text-white">{selectedClient.name}</div>
                    {selectedClient.sector && <div className="text-[10px] text-slate-500">{selectedClient.sector}</div>}
                  </div>
                </div>
              )}

              <div className="space-y-2 text-xs">
                {form.deadline && (
                  <div className="flex items-center gap-2 font-medium" style={{ color: 'var(--danger)' }}>
                    <CalendarClock size={11} /> Échéance : {formatDateFR(form.deadline)}
                  </div>
                )}
                {form.ao_type && (
                  <div className="flex items-center gap-2 text-slate-400">
                    <FileText size={11} className="text-violet-400" /> {form.ao_type}
                  </div>
                )}
                {form.budget_max && (
                  <div className="flex items-center gap-2 text-slate-400">
                    <Euro size={11} className="text-emerald-400" /> {form.budget_max}€/j
                  </div>
                )}
                {form.duration && (
                  <div className="flex items-center gap-2 text-slate-400">
                    <Clock size={11} className="text-amber-400" /> {form.duration}
                  </div>
                )}
                {form.location && (
                  <div className="flex items-center gap-2 text-slate-400">
                    <MapPin size={11} className="text-brand-400" /> {form.location}
                  </div>
                )}
                {skillsList.length > 0 && (
                  <div className="flex items-center gap-2 text-slate-400">
                    <Zap size={11} className="text-brand-400" /> {skillsList.length} compétence{skillsList.length > 1 ? 's' : ''}
                  </div>
                )}
              </div>
            </div>

            {error && (
              <div ref={errorRef} className="text-sm rounded-lg px-4 py-3" style={{ background: 'var(--danger-soft)', color: 'var(--danger)', border: '1px solid var(--danger)' }}>
                {error}
              </div>
            )}

            <label className="flex items-start gap-2.5 px-1 py-1 cursor-pointer select-none"
                   style={{ color: 'var(--text-muted)' }}>
              <input type="checkbox" className="mt-0.5 accent-brand-500"
                     checked={notifyNow} onChange={e => setNotifyNow(e.target.checked)} />
              <span className="text-[13px] leading-snug">
                Notifier la <strong>liste 1</strong> par e-mail dès la création
                <span className="block text-[11px]" style={{ color: 'var(--text-faint)' }}>
                  Décoché : aucun e-mail n'est envoyé maintenant (vous pourrez le faire depuis l'onglet « Envoi des e-mails »).
                </span>
              </span>
            </label>

            <div className="flex flex-col gap-2">
              <button type="submit" disabled={loading} className="btn-primary w-full justify-center py-3">
                {loading
                  ? <><Loader2 size={15} className="animate-spin" />Création...</>
                  : <><FileText size={15} />Créer l'AO</>}
              </button>
              <button type="button" onClick={() => navigate(-1)} className="btn-ghost w-full justify-center py-2.5">
                Annuler
              </button>
            </div>
          </div>
        </div>
      </form>
    </div>
  )
}
