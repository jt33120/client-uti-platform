import { useState, useRef, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import api from '../lib/api'
import UTILoader from './UTILoader'
import { Sparkles, X, Minus, Send, Loader2, ArrowRight, Eye } from 'lucide-react'

// Role-aware starter suggestions — showcase the capabilities: data Q&A, chart, highlight.
const SUGGESTIONS = {
  admin: [
    "Combien d'AOs ouverts ?",
    'TJM moyen du vivier ?',
    'Montre les clients par secteur',
    "Où créer un appel d'offres ?",
  ],
  commerce: [
    "Combien d'AOs ouverts ?",
    'Montre les AOs par type',
    'TJM moyen du vivier ?',
    "Où créer un appel d'offres ?",
  ],
  ao: [
    'Combien de consultants dans mon vivier ?',
    'Quels AOs sont ouverts ?',
    'Où ajouter un consultant ?',
  ],
}

function MiniChart({ title, data }) {
  const max = Math.max(...data.map(d => d.value), 1)
  return (
    <div className="mt-1.5 p-3 rounded-lg" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
      {title && <div className="text-[11px] font-semibold mb-2" style={{ color: 'var(--text)' }}>{title}</div>}
      <div className="space-y-1.5">
        {data.map((d, i) => (
          <div key={i} className="flex items-center gap-2">
            <div className="w-20 text-[10px] truncate" style={{ color: 'var(--text-muted)' }}>{d.name}</div>
            <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: 'var(--surface)' }}>
              {/* one brand hue; opacity tracks magnitude, so colour carries info */}
              <div className="h-full rounded-full" style={{ width: `${Math.max((d.value / max) * 100, 4)}%`, background: 'var(--accent)', opacity: 0.45 + 0.55 * (d.value / max) }} />
            </div>
            <div className="w-7 text-right text-[10px] tabular" style={{ color: 'var(--text)' }}>
              {Number.isInteger(d.value) ? d.value : d.value.toFixed(1)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function Bubble({ m, onNavigate, onHighlight }) {
  const isUser = m.role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className="max-w-[85%]">
        <div
          className="px-3 py-2 rounded-lg text-[13px] leading-relaxed whitespace-pre-wrap"
          style={isUser ? { background: 'var(--accent)', color: '#fff' } : { background: 'var(--surface-2)', color: 'var(--text)' }}
        >
          {m.content}
        </div>
        {(m.actions || []).map((a, i) => {
          if (a.type === 'navigate') return (
            <button
              key={i} onClick={() => onNavigate(a)}
              className="mt-1.5 w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-[12px] font-medium transition-colors"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)', border: '1px solid var(--accent)' }}
            >
              <span className="truncate">{a.cta || 'Ouvrir la page'}</span>
              <ArrowRight size={13} className="shrink-0" />
            </button>
          )
          if (a.type === 'highlight') return (
            <button
              key={i} onClick={() => onHighlight(a)}
              className="mt-1.5 w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-[12px] font-medium transition-colors"
              style={{ background: 'var(--surface-2)', color: 'var(--text)', border: '1px solid var(--border)' }}
            >
              <span className="truncate">{a.cta || 'Me montrer'}</span>
              <Eye size={13} className="shrink-0" />
            </button>
          )
          if (a.type === 'chart' && a.data?.length) return <MiniChart key={i} title={a.title} data={a.data} />
          return null
        })}
      </div>
    </div>
  )
}

export default function AssistantWidget() {
  const { user, isAdmin } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([]) // { role, content, action? }
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const scrollRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, loading, open])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  if (!user) return null

  const role = user?.role || 'ao'

  const send = async (text) => {
    const content = (text ?? input).trim()
    if (!content || loading) return
    setInput('')
    const next = [...messages, { role: 'user', content }]
    setMessages(next)
    setLoading(true)
    try {
      const { data } = await api.post('/assistant/chat', {
        messages: next.map(m => ({ role: m.role, content: m.content })),
        page: location.pathname,
      })
      setMessages(m => [...m, { role: 'assistant', content: data.reply, actions: data.actions || [] }])
    } catch {
      setMessages(m => [...m, {
        role: 'assistant',
        content: "Désolé, je n'ai pas pu traiter votre demande. Veuillez réessayer.",
        actions: [],
      }])
    } finally {
      setLoading(false)
    }
  }

  // Navigate + pre-fill. Never submits the target form — the user always confirms.
  const runNavigate = (a) => {
    if (!a?.path) return
    navigate(a.path, { state: { assistantPrefill: a.prefill || undefined } })
    setOpen(false)
  }

  // Pulse the matching sidebar menu entry to show *where* a feature lives.
  const runHighlight = (a) => {
    if (!a?.path) return
    const el = document.querySelector(`a[href="${a.path}"]`)
    if (!el) { navigate(a.path); setOpen(false); return }
    el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    try {
      el.animate(
        [
          { boxShadow: '0 0 0 0 rgba(99,102,241,0)' },
          { boxShadow: '0 0 0 4px rgba(99,102,241,0.55)' },
          { boxShadow: '0 0 0 0 rgba(99,102,241,0)' },
        ],
        { duration: 850, iterations: 3, easing: 'ease-out' },
      )
    } catch { /* Web Animations API unavailable */ }
  }

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <>
      {/* Launcher */}
      {/* Coin bas-droit partagé : le widget d'avis MIP (CSAT) se pose en
          position:fixed bottom:20px right:20px avec un z-index maximal — on ne
          peut ni le déplacer ni passer devant. On garde donc NOTRE bouton à
          bottom-20 (80px) sur toutes les tailles d'écran : au-dessus du widget
          MIP (qui occupe ~20→60px) sur desktop, et au-dessus de la barre de
          navigation mobile. Sans ça, les deux pastilles se chevauchent. */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="Ouvrir l'assistant"
          data-tour="assistant"
          className="fixed bottom-20 right-5 md:right-6 z-40 flex items-center gap-2 h-12 px-4 rounded-full transition-transform hover:scale-[1.03]"
          style={{
            backgroundColor: 'var(--action-bg)',
            backgroundImage: 'var(--btn-sheen)',
            color: 'var(--action-text)',
            boxShadow: 'var(--btn-inset), 0 4px 14px rgba(10,10,10,0.25)',
          }}
        >
          <Sparkles size={18} strokeWidth={2} />
          {/* AI Act art. 50(1) : la personne doit savoir qu'elle s'adresse à une IA,
              dès la première interaction. « Assistant » seul ne le dit pas. */}
          <span className="text-[13px] font-semibold">Assistant IA</span>
        </button>
      )}

      {/* Panel */}
      {open && (
        <div
          className="fixed bottom-20 right-4 md:right-6 z-40 flex flex-col rounded-xl overflow-hidden shadow-2xl"
          style={{
            width: 'min(380px, calc(100vw - 2rem))',
            height: 'min(600px, calc(100vh - 7rem))',
            background: 'var(--surface)',
            border: '1px solid var(--border)',
          }}
        >
          {/* Header */}
          <div
            className="flex items-center gap-2 px-4 h-12 shrink-0"
            style={{ borderBottom: '1px solid var(--border)' }}
          >
            <div
              className="w-7 h-7 rounded-md flex items-center justify-center"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
            >
              <Sparkles size={15} strokeWidth={2} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-semibold text-[var(--text)] leading-tight">Assistant IA</div>
              <div className="text-[10px] text-[var(--text-faint)] leading-tight">Réponses générées · ne valide jamais à votre place</div>
            </div>
            <button
              onClick={() => setOpen(false)}
              aria-label="Réduire"
              className="p-1.5 rounded text-[var(--text-faint)] hover:text-[var(--text)] hover:bg-[var(--surface-2)] transition-colors"
            >
              <Minus size={15} strokeWidth={2} />
            </button>
            <button
              onClick={() => { setOpen(false); setMessages([]) }}
              aria-label="Fermer"
              className="p-1.5 rounded text-[var(--text-faint)] hover:text-[var(--text)] hover:bg-[var(--surface-2)] transition-colors"
            >
              <X size={15} strokeWidth={2} />
            </button>
          </div>

          {/* Messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
            {messages.length === 0 && (
              <div className="space-y-3">
                <div
                  className="px-3 py-2.5 rounded-lg text-[13px] leading-relaxed"
                  style={{ background: 'var(--surface-2)', color: 'var(--text)' }}
                >
                  Bonjour {user.name?.split(' ')[0]} 👋<br />
                  Posez-moi une question sur vos données, demandez un graphique, ou dites-moi où aller :
                  je vous guide et pré-remplis, mais je ne valide jamais à votre place.
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(SUGGESTIONS[role] || []).map((s) => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      className="px-2.5 py-1.5 rounded-full text-[12px] transition-colors"
                      style={{ background: 'var(--surface-2)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => <Bubble key={i} m={m} onNavigate={runNavigate} onHighlight={runHighlight} />)}

            {loading && (
              <div className="flex justify-start">
                <div
                  className="px-3 py-2 rounded-lg flex items-center gap-2.5 text-[12px]"
                  style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}
                >
                  <UTILoader size={18} /> Analyse de vos données…
                </div>
              </div>
            )}
          </div>

          {/* Input */}
          <div className="p-3 shrink-0" style={{ borderTop: '1px solid var(--border)' }}>
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                rows={1}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Demandez-moi de vous guider…"
                className="input resize-none py-2 max-h-28"
                style={{ minHeight: '38px' }}
              />
              <button
                onClick={() => send()}
                disabled={loading || !input.trim()}
                aria-label="Envoyer"
                className="btn-primary h-[38px] px-3 shrink-0"
              >
                {loading ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
