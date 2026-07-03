import { useState, useEffect } from 'react'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useConfirm } from '../contexts/ConfirmContext'
import {
  Gauge, Users, FileText, Sparkles, UserPlus, X, Loader2,
  Shield, Briefcase, BadgePercent, Coins, Pencil, PauseCircle, Ban, KeyRound,
  ShieldCheck, ShieldOff, Activity, RefreshCw, AlertTriangle,
} from 'lucide-react'
import InviteModal from '../components/InviteModal'
import AccountEditModal from '../components/AccountEditModal'

const ROLE_META = {
  admin: { label: 'Administrateur', icon: Shield },
  commerce: { label: 'Commercial UTI', icon: BadgePercent },
  ao: { label: 'Partenaire', icon: Briefcase },
}

// Role label that accounts for the commercial entity (UTI vs Groupement-IT).
const roleLabel = (item) =>
  item.role === 'commerce'
    ? (item.org === 'groupement-it' ? 'Commercial Groupement-IT' : 'Commercial UTI')
    : (ROLE_META[item.role]?.label || item.role)

const STATUS_META = {
  suspended: { label: 'Suspendu', icon: PauseCircle, color: 'var(--warning, #b45309)' },
  disabled: { label: 'Désactivé', icon: Ban, color: 'var(--danger)' },
}

const fmtDate = (iso) => iso
  ? new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(iso))
  : '—'
const fmtDateTime = (iso) => iso
  ? new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(iso))
  : 'Jamais'

// Journal des erreurs/dégradations backend (GET /admin/errors). Autonome :
// charge son propre état, rafraîchissable, ne casse jamais la page admin.
function ErrorJournal() {
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
    <div className="pt-7 mt-8" style={{ borderTop: '1px solid var(--border)' }}>
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

function Kpi({ icon: Icon, label, value, sub }) {
  return (
    <div className="flex flex-col gap-1.5 lg:px-5 lg:border-l lg:first:border-l-0 lg:first:pl-0 border-[color:var(--border)]">
      <div className="flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
        <Icon size={14} strokeWidth={2} />
        <span className="text-[11px] uppercase tracking-[0.07em] font-semibold">{label}</span>
      </div>
      <div className="text-[30px] font-semibold tabular leading-none" style={{ color: 'var(--text)' }}>{value ?? '—'}</div>
      {sub && <div className="text-[11px]" style={{ color: 'var(--text-faint)' }}>{sub}</div>}
    </div>
  )
}

function RoleBadge({ item }) {
  const meta = ROLE_META[item.role] || { label: item.role, icon: Users }
  const Icon = meta.icon
  return (
    <span className="badge" style={{
      background: item.role === 'ao' ? 'var(--surface-2)' : 'var(--accent-soft)',
      color: item.role === 'ao' ? 'var(--text-muted)' : 'var(--accent-text)',
    }}>
      <Icon size={10} strokeWidth={2} /> {roleLabel(item)}
    </span>
  )
}

function StatusBadge({ status }) {
  const meta = STATUS_META[status]
  if (!meta) return null
  const Icon = meta.icon
  return (
    <span className="badge ml-1.5" style={{ background: 'var(--surface-2)', color: meta.color }}>
      <Icon size={10} strokeWidth={2} /> {meta.label}
    </span>
  )
}

export default function AdminPage() {
  const { user } = useAuth()
  const confirm = useConfirm()
  const [overview, setOverview] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [pending, setPending] = useState([])
  const [loading, setLoading] = useState(true)
  const [inviteOpen, setInviteOpen] = useState(false)
  const [deletingId, setDeletingId] = useState(null)
  const [mfaResetId, setMfaResetId] = useState(null)
  const [mfaToggleId, setMfaToggleId] = useState(null)
  const [editing, setEditing] = useState(null)
  const [loadError, setLoadError] = useState(false)
  const [mfaResetOk, setMfaResetOk] = useState(null)

  const load = async () => {
    setLoadError(false)
    const settle = (p) => p.then(r => ({ ok: true, data: r.data })).catch(() => ({ ok: false }))
    const [o, a] = await Promise.all([
      settle(api.get('/admin/overview')),
      settle(api.get('/admin/accounts')),
    ])
    if (o.ok) setOverview(o.data)
    if (a.ok) { setAccounts(a.data.accounts || []); setPending(a.data.pending_invitations || []) }
    // Un tableau des comptes vide sans message = panne invisible pour l'admin.
    if (!o.ok || !a.ok) setLoadError(true)
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const resetMfa = async (acc) => {
    if (!(await confirm({
      title: 'Réinitialiser la MFA ?',
      message: `« ${acc.name} » devra reconfigurer son application d'authentification (QR code) à sa prochaine connexion. À utiliser en cas de perte de téléphone.`,
      confirmLabel: 'Réinitialiser',
    }))) return
    setMfaResetId(acc.id)
    try {
      await api.post(`/auth/mfa/reset/${acc.id}`)
      // Confirmation visuelle : sans elle, l'admin ne sait pas si ça a marché.
      setMfaResetOk(acc.id)
      setTimeout(() => setMfaResetOk(null), 4000)
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors de la réinitialisation MFA')
    } finally {
      setMfaResetId(null)
    }
  }

  // MFA active par défaut ; un admin peut l'exonérer pour un compte précis.
  const toggleMfaRequired = async (acc) => {
    const required = acc.mfa_required !== false
    const next = !required
    if (!(await confirm({
      title: next ? 'Réactiver la MFA ?' : 'Désactiver la MFA ?',
      message: next
        ? `« ${acc.name} » devra de nouveau utiliser la double authentification à sa prochaine connexion.`
        : `« ${acc.name} » pourra se connecter sans double authentification. À n'utiliser qu'en cas de nécessité — la MFA reste recommandée.`,
      confirmLabel: next ? 'Réactiver' : 'Désactiver',
    }))) return
    setMfaToggleId(acc.id)
    try {
      await api.post(`/auth/mfa/require/${acc.id}`, { required: next })
      setAccounts(p => p.map(a => a.id === acc.id ? { ...a, mfa_required: next } : a))
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors du changement MFA')
    } finally {
      setMfaToggleId(null)
    }
  }

  const deleteAccount = async (acc) => {
    if (!(await confirm({
      title: 'Supprimer ce compte ?',
      message: `Le compte « ${acc.name} » (${acc.email}) sera supprimé définitivement. Cette action est irréversible.`,
      confirmLabel: 'Supprimer',
    }))) return
    setDeletingId(acc.id)
    try {
      await api.delete(`/admin/accounts/${acc.id}`)
      setAccounts(p => p.filter(a => a.id !== acc.id))
      // Keep the KPI tiles in sync without a full reload
      setOverview(o => o ? {
        ...o,
        accounts_total: Math.max(0, (o.accounts_total || 1) - 1),
        accounts_by_role: { ...o.accounts_by_role, [acc.role]: Math.max(0, (o.accounts_by_role?.[acc.role] || 1) - 1) },
      } : o)
    } catch (e) {
      alert(e.response?.data?.detail || 'Erreur lors de la suppression')
    } finally {
      setDeletingId(null)
    }
  }

  const onAccountSaved = (updated) => {
    setAccounts(p => p.map(a => (a.id === updated.id ? { ...a, ...updated } : a)))
    setEditing(null)
  }

  const hairline = { borderTop: '1px solid var(--border)' }

  if (loading) {
    return <div className="py-20 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement des comptes…</div>
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="section-title flex items-center gap-2">
            <Gauge size={20} strokeWidth={1.75} style={{ color: 'var(--accent-text)' }} />
            Comptes & utilisateurs
          </h1>
          <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
            Comptes, accès et activité de la plateforme.
          </p>
        </div>
        <button onClick={() => setInviteOpen(true)} className="btn-primary">
          <UserPlus size={15} strokeWidth={1.75} /> Inviter un compte
        </button>
      </div>

      {loadError && (
        <div className="mb-6 px-4 py-3 rounded-lg text-[13px] flex items-center justify-between gap-3"
          style={{ background: 'var(--surface-2)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
          <span>Le chargement a partiellement échoué — comptes ou indicateurs peut-être incomplets.</span>
          <button onClick={() => { setLoading(true); load() }} className="btn-ghost !h-7 !px-2.5 text-[12px] shrink-0">Réessayer</button>
        </div>
      )}
      {overview?.degraded && (
        <div className="mb-6 px-4 py-2.5 rounded-lg text-[12.5px]"
          style={{ background: 'var(--surface-2)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
          Indicateurs indisponibles (lecture en échec) : {overview.degraded.join(', ')} — les valeurs « — » ne signifient pas zéro.
        </div>
      )}

      {/* KPIs */}
      {overview && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-y-6 pb-8">
          <Kpi icon={Users} label="Comptes" value={overview.accounts_total}
            sub={Object.entries(overview.accounts_by_role || {})
              .map(([r, n]) => `${n} ${ROLE_META[r]?.label?.toLowerCase() || r}${n > 1 ? 's' : ''}`)
              .join(' · ') || null} />
          <Kpi icon={Users} label="Actifs (30 j)" value={overview.active_accounts_30d}
            sub="connexions sur 30 jours" />
          <Kpi icon={FileText} label="AOs" value={overview.aos_total}
            sub={`${overview.aos_open ?? 0} ouverts · ${overview.aos_30d ?? 0} créés / 30 j`} />
          <Kpi icon={Sparkles} label="Activité 30 j" value={overview.submissions_30d}
            sub={`CVs soumis · ${overview.matchings_30d ?? 0} matchings`} />
          <Kpi icon={Coins} label="Coût IA" value={overview.matching_cost_usd != null ? `$${overview.matching_cost_usd}` : '—'}
            sub={`cumulé · ${overview.matchings_total ?? 0} matchings`} />
        </div>
      )}

      {/* Comptes */}
      <div className="pt-7" style={hairline}>
        <h2 className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
          <Users size={13} strokeWidth={2} /> Comptes ({accounts.length})
        </h2>
        <div className="card overflow-hidden mb-4">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide" style={{ color: 'var(--text-faint)', borderBottom: '1px solid var(--border)' }}>
                <th className="font-medium px-4 py-2.5">Nom</th>
                <th className="font-medium px-4 py-2.5 hidden md:table-cell">Email</th>
                <th className="font-medium px-4 py-2.5">Rôle</th>
                <th className="font-medium px-4 py-2.5 hidden md:table-cell">Dernière connexion</th>
                <th className="font-medium px-4 py-2.5 hidden xl:table-cell">Créé le</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {accounts.map(acc => (
                <tr key={acc.id} className="hover:bg-[var(--surface-2)] transition-colors" style={{ borderBottom: '1px solid var(--border)' }}>
                  <td className="px-4 py-2.5 font-medium" style={{ color: 'var(--text)' }}>
                    {acc.name}
                    {acc.id === user?.id && <span className="ml-1.5 text-[10px]" style={{ color: 'var(--text-faint)' }}>(vous)</span>}
                    <StatusBadge status={acc.status} />
                  </td>
                  <td className="px-4 py-2.5 hidden md:table-cell" style={{ color: 'var(--text-muted)' }}>{acc.email}</td>
                  <td className="px-4 py-2.5"><RoleBadge item={acc} /></td>
                  <td className="px-4 py-2.5 hidden md:table-cell" style={{ color: 'var(--text-muted)' }}>
                    <div className="tabular">{fmtDateTime(acc.last_login_at)}</div>
                    {acc.last_login_ip && (
                      <div className="text-[11px] tabular" style={{ color: 'var(--text-faint)' }} title="IP de la dernière connexion">
                        {acc.last_login_ip}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5 hidden xl:table-cell tabular" style={{ color: 'var(--text-faint)' }}>{fmtDate(acc.created_at)}</td>
                  <td className="px-4 py-2.5 text-right whitespace-nowrap">
                    <button
                      onClick={() => setEditing(acc)}
                      className="p-1 rounded transition-colors text-[var(--text-faint)] hover:text-[var(--text)]"
                      title="Modifier le compte"
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      onClick={() => toggleMfaRequired(acc)}
                      disabled={mfaToggleId === acc.id}
                      className={`p-1 rounded transition-colors ml-0.5 ${acc.mfa_required === false ? 'text-[var(--warning,#b45309)] hover:opacity-80' : 'text-[var(--text-faint)] hover:text-[var(--text)]'}`}
                      title={acc.mfa_required === false ? 'MFA désactivée — cliquer pour réactiver' : 'MFA active — cliquer pour désactiver'}
                    >
                      {mfaToggleId === acc.id
                        ? <Loader2 size={13} className="animate-spin" />
                        : (acc.mfa_required === false ? <ShieldOff size={14} /> : <ShieldCheck size={14} />)}
                    </button>
                    <button
                      onClick={() => resetMfa(acc)}
                      disabled={mfaResetId === acc.id}
                      className="p-1 rounded transition-colors ml-0.5"
                      style={mfaResetOk === acc.id ? { color: 'var(--success, #16a34a)' } : undefined}
                      title={mfaResetOk === acc.id ? 'MFA réinitialisée ✓' : 'Réinitialiser la double authentification (perte de téléphone)'}
                    >
                      {mfaResetId === acc.id
                        ? <Loader2 size={13} className="animate-spin" />
                        : mfaResetOk === acc.id
                          ? <ShieldCheck size={14} />
                          : <KeyRound size={14} className="text-[var(--text-faint)] hover:text-[var(--text)]" />}
                    </button>
                    {acc.id !== user?.id && (
                      <button
                        onClick={() => deleteAccount(acc)}
                        disabled={deletingId === acc.id}
                        className="p-1 rounded transition-colors text-[var(--text-faint)] hover:text-[var(--danger)] ml-0.5"
                        title="Supprimer le compte"
                      >
                        {deletingId === acc.id ? <Loader2 size={13} className="animate-spin" /> : <X size={14} />}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {pending.length > 0 && (
          <div className="mb-2">
            <h3 className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2" style={{ color: 'var(--text-faint)' }}>
              Invitations en attente ({pending.length})
            </h3>
            <div className="flex flex-wrap gap-2">
              {pending.map(inv => (
                <span key={inv.id} className="badge" style={{ background: 'var(--surface-2)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
                  {inv.name || inv.email} · {roleLabel(inv)} · expire le {fmtDate(inv.expires_at)}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Journal d'erreurs backend — visibilité sur les pannes/dégradations
          (échecs LLM, SMTP, scheduler, 500). Ring buffer serveur : se vide au
          redémarrage ; l'historique complet est dans journald (RUNBOOK §3). */}
      <ErrorJournal />

      {inviteOpen && <InviteModal onClose={() => { setInviteOpen(false); load() }} />}
      {editing && (
        <AccountEditModal
          account={editing}
          isSelf={editing.id === user?.id}
          onClose={() => setEditing(null)}
          onSaved={onAccountSaved}
        />
      )}
    </div>
  )
}
