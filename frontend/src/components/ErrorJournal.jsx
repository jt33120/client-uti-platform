import { useState, useEffect } from 'react'
import api from '../lib/api'
import { Activity, RefreshCw, AlertTriangle } from 'lucide-react'

const fmtDateTime = (iso) => iso
  ? new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(iso))
  : '—'

// Journal d'erreurs / dégradations du backend (ring buffer serveur, GET /admin/errors).
// `embedded` = rendu dans un onglet (sans le séparateur/haut de section).
export default function ErrorJournal({ embedded = false }) {
  const [events, setEvents] = useState(null) // null = chargement, false = erreur
  const [levelFilter, setLevelFilter] = useState('all')

  const load = () => {
    setEvents(null)
    api.get('/admin/errors', { params: { limit: 100 } })
      .then(r => setEvents(r.data.events || []))
      .catch(() => setEvents(false))
  }
  useEffect(load, [])

  const shown = Array.isArray(events)
    ? events.filter(e => levelFilter === 'all' || e.level === levelFilter)
    : []
  const errCount = Array.isArray(events) ? events.filter(e => e.level === 'error').length : 0

  return (
    <div className={embedded ? '' : 'pt-7 mt-8'} style={embedded ? undefined : { borderTop: '1px solid var(--border)' }}>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-[11px] uppercase tracking-[0.08em] font-semibold flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
          <Activity size={13} strokeWidth={2} /> Santé du backend — journal d'erreurs
          {errCount > 0 && (
            <span className="badge !text-[10px]" style={{ background: 'color-mix(in srgb, var(--danger) 12%, transparent)', color: 'var(--danger)' }}>
              {errCount} erreur{errCount > 1 ? 's' : ''}
            </span>
          )}
        </h2>
        <div className="flex items-center gap-1.5">
          <div className="flex gap-1 rounded-lg p-0.5" style={{ background: 'var(--surface-2)' }}>
            {[{ k: 'all', l: 'Tout' }, { k: 'error', l: 'Erreurs' }, { k: 'warning', l: 'Dégradations' }].map(o => (
              <button key={o.k} onClick={() => setLevelFilter(o.k)}
                className={levelFilter === o.k ? 'seg-active px-2.5 py-1 text-[11px] rounded-md font-medium' : 'px-2.5 py-1 text-[11px] rounded-md font-medium text-[var(--text-muted)] hover:text-[var(--text)]'}>
                {o.l}
              </button>
            ))}
          </div>
          <button onClick={load} className="btn-ghost !h-7 !px-2 text-[11px]" title="Rafraîchir">
            <RefreshCw size={12} />
          </button>
        </div>
      </div>
      <p className="text-[11.5px] mb-3" style={{ color: 'var(--text-faint)' }}>
        Pannes et dégradations récentes (échecs IA, e-mails, planificateur, erreurs 500). Vidé au redémarrage du
        backend — l'historique complet est dans les logs serveur (RUNBOOK).
      </p>
      {events === null ? (
        <div className="text-[12.5px] py-4" style={{ color: 'var(--text-faint)' }}>Chargement…</div>
      ) : events === false ? (
        <div className="text-[12.5px] py-4" style={{ color: 'var(--text-muted)' }}>
          Journal inaccessible (backend injoignable ou version antérieure).
          <button onClick={load} className="btn-ghost !h-7 !px-2.5 text-[11px] ml-2">Réessayer</button>
        </div>
      ) : shown.length === 0 ? (
        <div className="text-[12.5px] py-4" style={{ color: 'var(--text-faint)' }}>
          ✅ Rien à signaler {levelFilter !== 'all' ? 'pour ce filtre ' : ''}depuis le dernier redémarrage.
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="w-full text-[12px]">
            <tbody>
              {shown.map((e, i) => (
                <tr key={i} style={{ borderTop: i ? '1px solid var(--border)' : 'none' }}>
                  <td className="px-3 py-2 whitespace-nowrap align-top" style={{ color: 'var(--text-faint)' }}>
                    {fmtDateTime(e.ts)}
                  </td>
                  <td className="px-2 py-2 align-top">
                    {e.level === 'error'
                      ? <AlertTriangle size={12} style={{ color: 'var(--danger)' }} title="Erreur" />
                      : <AlertTriangle size={12} style={{ color: 'var(--warning, #b45309)' }} title="Dégradation" />}
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap align-top font-medium" style={{ color: 'var(--text-muted)' }}>
                    {e.source}
                  </td>
                  <td className="px-3 py-2 align-top break-words" style={{ color: 'var(--text)' }}>
                    {e.message}
                    {e.path && <span style={{ color: 'var(--text-faint)' }}> · {e.path}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
