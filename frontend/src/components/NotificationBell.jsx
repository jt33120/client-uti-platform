import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../lib/api'
import { Bell, AlertTriangle, Clock, Mail, X, PencilLine, CheckCircle2 } from 'lucide-react'

const SEV = {
  urgent:  { color: '#ef4444', ring: 'rgba(239,68,68,0.15)' },
  warning: { color: '#f59e0b', ring: 'rgba(245,158,11,0.15)' },
  info:    { color: '#3b82f6', ring: 'rgba(59,130,246,0.12)' },
}
const KIND_ICON = { ao_urgent: Clock, email: Mail, missing_info: PencilLine, status: CheckCircle2 }

// Cloche de notifications (staff) : AO urgents (échéance ≤ 3 j) + miroir des
// e-mails partenaires. Le badge rouge compte les AO urgents (le signal qui
// demande une action). Dégrade en silence si l'endpoint n'est pas déployé.
export default function NotificationBell() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)   // { items, urgent_count, count }
  const [loading, setLoading] = useState(false)
  const ref = useRef(null)

  const load = useCallback(() => {
    setLoading(true)
    api.get('/notifications/feed')
      .then(r => setData(r.data))
      .catch(() => setData({ items: [], urgent_count: 0, count: 0 }))
      .finally(() => setLoading(false))
  }, [])

  // Chargement initial + rafraîchissement doux toutes les 5 min.
  useEffect(() => {
    load()
    const t = setInterval(load, 5 * 60 * 1000)
    return () => clearInterval(t)
  }, [load])

  // Recharge à l'ouverture (données fraîches sans marteler l'API).
  useEffect(() => { if (open) load() }, [open, load])

  // Fermeture au clic extérieur.
  useEffect(() => {
    if (!open) return
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  const urgent = data?.urgent_count || 0
  const items = data?.items || []
  // Badge = éléments actionnables (AO urgents + infos à compléter). Rouge s'il y a
  // une vraie urgence d'échéance, ambre sinon (invitations à compléter).
  const badge = data?.action_count ?? urgent
  const badgeColor = urgent > 0 ? '#ef4444' : '#f59e0b'

  const go = (link) => { setOpen(false); if (link) navigate(link) }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="relative h-8 w-8 rounded-md flex items-center justify-center text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--surface-2)] transition-colors"
        title="Notifications"
        aria-label="Notifications"
      >
        <Bell size={15} strokeWidth={1.75} />
        {badge > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[15px] h-[15px] px-1 rounded-full text-[9px] font-bold text-white flex items-center justify-center"
                style={{ background: badgeColor }}>
            {badge > 9 ? '9+' : badge}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 mt-2 w-[330px] max-w-[calc(100vw-2rem)] rounded-xl overflow-hidden z-50 shadow-2xl"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
        >
          <div className="flex items-center justify-between px-3.5 py-2.5" style={{ borderBottom: '1px solid var(--border)' }}>
            <span className="text-[12px] font-semibold" style={{ color: 'var(--text)' }}>Notifications</span>
            <div className="flex items-center gap-2">
              {urgent > 0 && (
                <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full"
                      style={{ background: 'rgba(239,68,68,0.12)', color: '#ef4444' }}>
                  {urgent} urgent{urgent > 1 ? 's' : ''}
                </span>
              )}
              <button onClick={() => setOpen(false)} className="text-[var(--text-faint)] hover:text-[var(--text)]"><X size={14} /></button>
            </div>
          </div>

          <div className="max-h-[60vh] overflow-y-auto">
            {loading && !data ? (
              <p className="px-3.5 py-6 text-[12px] text-center" style={{ color: 'var(--text-faint)' }}>Chargement…</p>
            ) : items.length === 0 ? (
              <p className="px-3.5 py-8 text-[12px] text-center" style={{ color: 'var(--text-faint)' }}>Rien à signaler pour l'instant.</p>
            ) : (
              items.map(it => {
                const sev = SEV[it.severity] || SEV.info
                const Icon = KIND_ICON[it.kind] || AlertTriangle
                return (
                  <button key={it.id} onClick={() => go(it.link)}
                    className="w-full text-left flex items-start gap-2.5 px-3.5 py-2.5 hover:bg-[var(--surface-2)] transition-colors"
                    style={{ borderBottom: '1px solid var(--border)' }}>
                    <span className="mt-0.5 h-6 w-6 rounded-full flex items-center justify-center shrink-0"
                          style={{ background: sev.ring, color: sev.color }}>
                      <Icon size={12} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[12.5px] font-medium truncate" style={{ color: 'var(--text)' }}>{it.title}</span>
                      {it.subtitle && <span className="block text-[11px] truncate" style={{ color: 'var(--text-muted)' }}>{it.subtitle}</span>}
                    </span>
                  </button>
                )
              })
            )}
          </div>
        </div>
      )}
    </div>
  )
}
