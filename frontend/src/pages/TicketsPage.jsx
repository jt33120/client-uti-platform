import { useState, useEffect } from 'react'
import api from '../lib/api'
import {
  Ticket, Loader2, Check, RotateCcw, Inbox,
} from 'lucide-react'

const fmtDateTime = (iso) => iso
  ? new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(iso))
  : '—'

export default function TicketsPage() {
  const [tickets, setTickets] = useState([])
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState(null)
  const [ticketBusy, setTicketBusy] = useState(null)
  const [filter, setFilter] = useState('open')

  const load = () => {
    setLoading(true)
    setFetchError(null)
    api.get('/admin/tickets')
      .then(r => setTickets(r.data))
      // Sans ce catch, un échec affichait « Aucun ticket 🎉 » — un faux état
      // vide qui masquait la panne.
      .catch(e => setFetchError(e.response?.data?.detail || 'Impossible de charger les tickets.'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const setTicketStatus = async (ticket, status) => {
    setTicketBusy(ticket.id)
    try {
      await api.patch(`/admin/tickets/${ticket.id}`, { status })
      setTickets(p => p.map(t => (t.id === ticket.id ? { ...t, status } : t)))
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur de mise à jour du ticket')
    } finally {
      setTicketBusy(null)
    }
  }

  const shown = tickets.filter(t => (filter === 'all' ? true : (t.status || 'open') === filter))
  const openCount = tickets.filter(t => (t.status || 'open') === 'open').length

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="section-title flex items-center gap-2">
            <Ticket size={20} strokeWidth={1.75} style={{ color: 'var(--accent-text)' }} />
            Tickets support
            <span className="text-sm font-normal text-slate-500">({openCount} ouvert{openCount > 1 ? 's' : ''})</span>
          </h1>
          <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
            Messages envoyés via le formulaire de contact / signalement.
          </p>
        </div>
        <div className="flex gap-1 rounded-lg p-1" style={{ background: 'var(--surface-2)' }}>
          {[{ k: 'open', l: 'Ouverts' }, { k: 'resolved', l: 'Traités' }, { k: 'all', l: 'Tous' }].map(o => (
            <button key={o.k} onClick={() => setFilter(o.k)}
              className={filter === o.k ? 'seg-active px-3 py-1 text-xs rounded-md font-medium' : 'px-3 py-1 text-xs rounded-md font-medium text-[var(--text-muted)] hover:text-[var(--text)]'}>
              {o.l}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="py-20 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement des tickets…</div>
      ) : fetchError ? (
        <div className="py-16 text-center text-[13px]" style={{ color: 'var(--text-muted)' }}>
          <p className="mb-3">{fetchError}</p>
          <button onClick={load} className="btn-ghost !h-8 !px-3 text-[12px]">Réessayer</button>
        </div>
      ) : shown.length === 0 ? (
        <div className="py-16 text-center text-[13px]" style={{ color: 'var(--text-faint)' }}>
          <Inbox size={28} className="mx-auto mb-2 opacity-50" />
          {filter === 'open' ? 'Aucun ticket ouvert. 🎉' : 'Aucun ticket.'}
        </div>
      ) : (
        <div className="space-y-2.5">
          {shown.map(t => {
            const resolved = (t.status || 'open') === 'resolved'
            return (
              <div key={t.id} className="card p-4" style={resolved ? { opacity: 0.65 } : undefined}>
                <div className="flex items-start justify-between gap-3 mb-1.5">
                  <div className="min-w-0">
                    <div className="text-[13px] font-semibold truncate" style={{ color: 'var(--text)' }}>{t.subject}</div>
                    <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-faint)' }}>
                      {t.from_name} ({t.from_email}) · {t.type} · {fmtDateTime(t.created_at)}
                    </div>
                  </div>
                  <button
                    onClick={() => setTicketStatus(t, resolved ? 'open' : 'resolved')}
                    disabled={ticketBusy === t.id}
                    className="btn-ghost shrink-0 !h-7 !px-2.5 text-[11px]"
                    title={resolved ? 'Rouvrir' : 'Marquer comme traité'}
                  >
                    {ticketBusy === t.id
                      ? <Loader2 size={12} className="animate-spin" />
                      : resolved
                        ? <><RotateCcw size={11} strokeWidth={2} /> Rouvrir</>
                        : <><Check size={12} strokeWidth={2} /> Traité</>}
                  </button>
                </div>
                <p className="text-[12.5px] whitespace-pre-wrap leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                  {t.message}
                </p>
                <a
                  href={`mailto:${t.from_email}?subject=${encodeURIComponent(`Re: ${t.subject}`)}`}
                  className="inline-flex items-center gap-1 mt-2 text-[11.5px] font-medium hover:underline"
                  style={{ color: 'var(--accent-text)' }}
                >
                  Répondre par email
                </a>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
