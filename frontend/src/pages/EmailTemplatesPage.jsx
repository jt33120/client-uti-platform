import { useState, useEffect } from 'react'
import api from '../lib/api'
import RichTextEditor from '../components/RichTextEditor'
import { useAuth } from '../contexts/AuthContext'
import { useConfirm } from '../contexts/ConfirmContext'
import { Mail, Loader2, Save, RotateCcw, CheckCircle, Eye, Send, AlertCircle, Megaphone } from 'lucide-react'

const VAR_LABELS = {
  title: "Titre de l'AO",
  client: 'Client',
  reference: 'Référence',
  location: 'Localisation',
  deadline: 'Date limite',
  link: 'Lien / bouton',
  name: 'Prénom',
  role: 'Rôle invité',
}

const escapeHtml = (s) =>
  (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

const looksHtml = (s) => /<[a-zA-Z!/][^>]*>/.test(s || '')

// Corps texte (legacy) → HTML, pour pré-remplir l'éditeur visuel.
const plainToHtml = (text) =>
  escapeHtml(text)
    .split(/\n{2,}/)
    .map((para) => `<p>${para.replace(/\n/g, '<br>')}</p>`)
    .join('')

const toEditorHtml = (body) => (looksHtml(body) ? body : plainToHtml(body))

async function uploadImage(file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await api.post('/email-templates/upload-image', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return r.data.url
}

// Aperçu 100 % fidèle : on demande au backend le rendu RÉEL (même fonction que
// l'envoi) et on l'affiche dans une iframe — zéro écart avec le mail reçu.
function EmailPreview({ tplKey, subject, body }) {
  const [html, setHtml] = useState('')
  const [shownSubject, setShownSubject] = useState(subject)
  const [status, setStatus] = useState('loading') // loading | ok | error

  useEffect(() => {
    let cancelled = false
    setStatus('loading')
    const t = setTimeout(() => {
      api.post('/email-templates/preview', { key: tplKey, subject, body })
        .then((r) => {
          if (cancelled) return
          setHtml(r.data.html); setShownSubject(r.data.subject); setStatus('ok')
        })
        .catch(() => { if (!cancelled) setStatus('error') })
    }, 450)
    return () => { cancelled = true; clearTimeout(t) }
  }, [tplKey, subject, body])

  return (
    <div className="rounded-lg overflow-hidden border border-white/10" style={{ background: '#f4f4f7' }}>
      <div className="px-3 py-2 text-[11px] flex items-center gap-2" style={{ background: '#e9e9ec', color: '#57606a' }}>
        <span className="font-semibold" style={{ color: '#1d1d1f' }}>Objet :</span>
        <span className="truncate">{shownSubject}</span>
        {status === 'loading' && <Loader2 size={11} className="animate-spin ml-auto shrink-0" />}
      </div>
      {status === 'error' ? (
        <div className="p-4 text-xs text-amber-700 bg-amber-50">
          Aperçu indisponible : le backend doit être redéployé pour activer le rendu fidèle.
        </div>
      ) : (
        <iframe
          title="Aperçu de l'email"
          srcDoc={html}
          sandbox=""
          className="w-full block bg-white"
          style={{ height: 560, border: 'none' }}
        />
      )}
    </div>
  )
}

function TemplateCard({ tpl, onSaved }) {
  const { user } = useAuth()
  const confirm = useConfirm()
  const [subject, setSubject] = useState(tpl.subject)
  const [body, setBody] = useState(() => toEditorHtml(tpl.body))
  const [version, setVersion] = useState(0) // force la ré-init de l'éditeur
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [testEmail, setTestEmail] = useState(user?.email || '')
  const [testing, setTesting] = useState(false)
  const [testMsg, setTestMsg] = useState(null) // { ok, text }
  // Diffusion groupée (modèles d'annonce uniquement).
  const [clients, setClients] = useState([])
  const [bcClient, setBcClient] = useState('')
  const [bcBusy, setBcBusy] = useState(false)
  const [bcMsg, setBcMsg] = useState(null) // { ok, text }

  useEffect(() => {
    if (!tpl.broadcast) return
    api.get('/clients').then(r => setClients(r.data || [])).catch(() => {})
  }, [tpl.broadcast])

  const sendTest = async () => {
    setTesting(true); setTestMsg(null)
    try {
      const r = await api.post('/email-templates/send-test', { key: tpl.key, to: testEmail, subject, body })
      setTestMsg({ ok: true, text: r.data.message })
    } catch (e) {
      setTestMsg({ ok: false, text: e.response?.data?.detail || "Échec de l'envoi du test" })
    } finally {
      setTesting(false)
    }
  }

  const broadcast = async () => {
    const client = clients.find(c => c.id === bcClient)
    if (!client) return
    if (!(await confirm({
      title: 'Diffuser aux partenaires ?',
      message: `L'e-mail (tel qu'affiché) sera envoyé à TOUS les partenaires habilités (Liste 1/2) sur le client « ${client.name} ». Cette action envoie de vrais e-mails.`,
      confirmLabel: 'Diffuser',
    }))) return
    setBcBusy(true); setBcMsg(null)
    try {
      const r = await api.post('/email-templates/broadcast', { key: tpl.key, client_id: bcClient, subject, body })
      const { sent = 0, recipients = 0, failed = 0 } = r.data
      setBcMsg({
        ok: failed === 0,
        text: recipients === 0
          ? 'Aucun partenaire habilité sur ce client.'
          : `${sent}/${recipients} e-mail(s) envoyé(s)${failed ? ` · ${failed} échec(s)` : ''}.`,
      })
    } catch (e) {
      setBcMsg({ ok: false, text: e.response?.data?.detail || 'Échec de la diffusion' })
    } finally {
      setBcBusy(false)
    }
  }

  const initialBody = toEditorHtml(tpl.body)
  const dirty = subject !== tpl.subject || body !== initialBody
  const touch = () => setSaved(false)

  const save = async () => {
    setSaving(true); setError(''); setSaved(false)
    try {
      await api.put(`/email-templates/${tpl.key}`, { subject, body, format: 'html' })
      setSaved(true)
      onSaved?.()
    } catch (e) {
      setError(e.response?.data?.detail || "Erreur lors de l'enregistrement")
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    setSaving(true); setError(''); setSaved(false)
    try {
      await api.delete(`/email-templates/${tpl.key}`)
      setSubject(tpl.default_subject)
      setBody(toEditorHtml(tpl.default_body))
      setVersion((v) => v + 1)
      setSaved(true)
      onSaved?.()
    } catch (e) {
      setError(e.response?.data?.detail || 'Erreur lors de la réinitialisation')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h2 className="text-sm font-semibold text-white flex items-center gap-2">
          <Mail size={15} className="text-brand-400" /> {tpl.label}
        </h2>
        {tpl.is_custom
          ? <span className="badge bg-violet-500/10 text-violet-300 border border-violet-500/20 text-[10px]">Personnalisé</span>
          : <span className="badge bg-white/5 text-slate-400 text-[10px]">Par défaut</span>}
      </div>

      <div>
        <label className="label">Objet de l'email</label>
        <input className="input" value={subject}
               onChange={(e) => { setSubject(e.target.value); touch() }} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div>
          <label className="label">Corps du message</label>
          <RichTextEditor
            value={body}
            resetKey={`${tpl.key}-${version}`}
            onChange={(html) => { setBody(html); touch() }}
            placeholders={tpl.placeholders || []}
            varLabels={VAR_LABELS}
            uploadImage={uploadImage}
            minHeight={300}
          />
        </div>

        {/* Aperçu fidèle */}
        <div>
          <p className="label flex items-center gap-1">
            <Eye size={12} /> Aperçu (valeurs d'exemple, remplacées à l'envoi)
          </p>
          <EmailPreview tplKey={tpl.key} subject={subject} body={body} />
        </div>
      </div>

      {/* Envoi d'un email de test — discret, une ligne. */}
      <div className="flex items-center gap-2 pt-1">
        <Send size={13} className="text-slate-500 shrink-0" />
        <input
          type="email"
          className="input h-8 py-1 text-xs flex-1 min-w-[140px] max-w-xs"
          placeholder="Envoyer un test à…"
          title="Reçois le rendu réel (valeurs d'exemple) dans ta boîte"
          value={testEmail}
          onChange={(e) => { setTestEmail(e.target.value); setTestMsg(null) }}
        />
        <button
          onClick={sendTest}
          disabled={testing || !testEmail}
          className="btn-ghost text-xs h-8 px-2.5 gap-1 whitespace-nowrap"
        >
          {testing ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />} Test
        </button>
        {testMsg && (
          <span className={`text-[11px] inline-flex items-center gap-1 ${testMsg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
            {testMsg.ok ? <CheckCircle size={12} /> : <AlertCircle size={12} />} {testMsg.text}
          </span>
        )}
      </div>

      {/* Diffusion groupée — modèles d'annonce uniquement. Envoie l'e-mail
          (tel qu'affiché) aux partenaires habilités sur le client choisi. */}
      {tpl.broadcast && (
        <div className="pt-3 border-t border-white/5">
          <p className="label flex items-center gap-1.5">
            <Megaphone size={13} className="text-amber-400" /> Diffuser aux partenaires d'un client
          </p>
          <div className="flex items-center gap-2 flex-wrap">
            <select
              className="input h-8 py-1 text-xs flex-1 min-w-[160px] max-w-xs"
              value={bcClient}
              onChange={(e) => { setBcClient(e.target.value); setBcMsg(null) }}
            >
              <option value="">Choisir un client…</option>
              {clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <button
              onClick={broadcast}
              disabled={bcBusy || !bcClient}
              className="btn-primary text-xs h-8 px-3 gap-1 whitespace-nowrap"
            >
              {bcBusy ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />} Diffuser
            </button>
            {bcMsg && (
              <span className={`text-[11px] inline-flex items-center gap-1 ${bcMsg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                {bcMsg.ok ? <CheckCircle size={12} /> : <AlertCircle size={12} />} {bcMsg.text}
              </span>
            )}
          </div>
          <p className="text-[11px] text-slate-500 mt-1">
            Envoie à tous les partenaires habilités (Liste 1 / Liste 2) sur ce client. Pense à
            enregistrer tes modifications d'abord — c'est le contenu affiché qui part.
          </p>
        </div>
      )}

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="flex items-center justify-end gap-2">
        {saved && !dirty && (
          <span className="text-xs text-emerald-400 inline-flex items-center gap-1 mr-auto">
            <CheckCircle size={13} /> Enregistré
          </span>
        )}
        <button onClick={reset} disabled={saving}
                className="btn-ghost text-sm gap-1.5" title="Revenir au texte par défaut">
          <RotateCcw size={14} /> Réinitialiser
        </button>
        <button onClick={save} disabled={saving || !dirty} className="btn-primary text-sm gap-1.5">
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Enregistrer
        </button>
      </div>
    </div>
  )
}

// Panneau « Modèles » — réutilisé par la page Emails (onglet Modèles).
export function EmailTemplatesPanel() {
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    api.get('/email-templates')
      .then((r) => setTemplates(r.data.templates || []))
      .catch((e) => setError(e.response?.data?.detail || 'Erreur de chargement'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  return (
    <div className="w-full">
      <p className="text-sm text-slate-400 mb-5">
        Éditeur visuel complet : mise en forme, couleurs, images, boutons…
        Les variables entre accolades sont remplacées automatiquement à l'envoi.
      </p>

      {loading ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 size={22} className="animate-spin text-brand-400" />
        </div>
      ) : error ? (
        <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</div>
      ) : (
        <div className="space-y-5">
          {templates.map((tpl) => (
            <TemplateCard key={tpl.key} tpl={tpl} onSaved={load} />
          ))}
        </div>
      )}
    </div>
  )
}
