import { useState, useEffect, useMemo } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import api from '../lib/api'
import { Inbox, ArrowLeft, ArrowRight, CheckCircle2 } from 'lucide-react'
import { TASK_KINDS, TASK_ICON, TASK_FALLBACK_ICON, KIND_LABEL } from '../lib/staffTasks'
import UTILoader from '../components/UTILoader'

// Page dédiée « À traiter » — vue complète de la file staff (le dashboard n'en
// montre qu'un aperçu). Regroupe les éléments actionnables par catégorie.
export default function TasksPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(false)
    api.get('/notifications/feed')
      .then(r => { if (!cancelled) setItems((r.data?.items || []).filter(it => TASK_KINDS.includes(it.kind))) })
      .catch(() => { if (!cancelled) setError(true) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  // Regroupé par catégorie, dans l'ordre de TASK_KINDS.
  const groups = useMemo(() => {
    return TASK_KINDS
      .map(kind => ({ kind, label: KIND_LABEL[kind] || kind, items: items.filter(it => it.kind === kind) }))
      .filter(g => g.items.length > 0)
  }, [items])

  return (
    <div>
      <div className="mb-6">
        <Link to="/dashboard" className="inline-flex items-center gap-1.5 text-[12px] mb-3 hover:underline" style={{ color: 'var(--text-muted)' }}>
          <ArrowLeft size={13} strokeWidth={2} /> Retour au tableau de bord
        </Link>
        <div className="flex items-center gap-2">
          <Inbox size={18} className="text-amber-400 shrink-0" />
          <h1 className="text-[22px] font-semibold tracking-tightest" style={{ color: 'var(--text)' }}>À traiter</h1>
          {!loading && !error && (
            <span className="text-[13px]" style={{ color: 'var(--text-muted)' }}>
              · {items.length} élément{items.length > 1 ? 's' : ''}
            </span>
          )}
        </div>
        <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
          Les dossiers qui attendent une action de votre part.
        </p>
      </div>

      {loading ? (
        <div className="py-20 flex justify-center"><UTILoader /></div>
      ) : error ? (
        <div className="px-4 py-3 rounded-lg text-[13px] flex items-center justify-between gap-3"
          style={{ background: 'var(--surface-2)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
          <span>Impossible de charger la file « à traiter ».</span>
          <button onClick={() => window.location.reload()} className="btn-ghost !h-7 !px-2.5 text-[12px] shrink-0">Réessayer</button>
        </div>
      ) : items.length === 0 ? (
        <div className="py-20 flex flex-col items-center gap-3 text-center">
          <CheckCircle2 size={32} className="text-emerald-400" strokeWidth={1.5} />
          <p className="text-[14px] font-medium" style={{ color: 'var(--text)' }}>Rien à traiter pour le moment.</p>
          <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>Tout est à jour — revenez plus tard.</p>
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          {groups.map(group => {
            const Icon = TASK_ICON[group.kind] || TASK_FALLBACK_ICON
            return (
              <div key={group.kind} className="rounded-xl overflow-hidden"
                   style={{ border: '1px solid var(--border)' }}>
                <div className="flex items-center gap-2 px-4 py-2.5" style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface-2)' }}>
                  <Icon size={14} className="text-amber-400 shrink-0" />
                  <span className="text-[12px] uppercase tracking-[0.06em] font-semibold" style={{ color: 'var(--text-muted)' }}>
                    {group.label}
                  </span>
                  <span className="text-[12px]" style={{ color: 'var(--text-faint)' }}>· {group.items.length}</span>
                </div>
                <ul className="divide-y" style={{ borderColor: 'var(--border)' }}>
                  {group.items.map(it => (
                    <li key={it.id}>
                      <button
                        onClick={() => { if (it.link) navigate(it.link) }}
                        className="w-full text-left flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--surface-2)] transition-colors">
                        <Icon size={14} className="text-amber-400 shrink-0" />
                        <span className="min-w-0 flex-1">
                          <span className="block text-[13px] font-medium truncate" style={{ color: 'var(--text)' }}>{it.title}</span>
                          {it.subtitle && <span className="block text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>{it.subtitle}</span>}
                        </span>
                        <ArrowRight size={13} className="text-slate-600 shrink-0" />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
