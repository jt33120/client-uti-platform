import { useState, useEffect } from 'react'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useConfirm } from '../contexts/ConfirmContext'
import {
  Gauge, Users, FileText, Sparkles, UserPlus, X, Loader2,
  Shield, Briefcase, BadgePercent, Coins, Pencil, PauseCircle, Ban, KeyRound,
  ShieldCheck, ShieldOff, GraduationCap,
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

// Registre de littératie IA — AI Act art. 4.
//
// L'obligation est de MOYENS : ce qui se démontre en contrôle, c'est la trace.
// D'où un registre EXHAUSTIF (tous les comptes actifs, y compris ceux qui n'ont
// jamais attesté) et non une simple liste d'attestations — une liste ne dit rien
// de ceux qui manquent, qui sont précisément le sujet.
//
// Autonome : charge son propre état, disparaît en silence s'il est indisponible
// (colonnes non migrées) plutôt que de casser la page Administration.
const LIT_STATE = {
  never: { label: 'Jamais fait', tone: 'var(--danger)' },
  outdated: { label: 'Contenu mis à jour', tone: 'var(--warning, #d97706)' },
  expired: { label: 'À renouveler', tone: 'var(--warning, #d97706)' },
  ok: { label: 'À jour', tone: 'var(--success)' },
}

function AiLiteracyRegister() {
  const [reg, setReg] = useState(null)
  useEffect(() => {
    api.get('/admin/ai-literacy').then(r => setReg(r.data)).catch(() => setReg(false))
  }, [])
  if (!reg) return null

  return (
    <div className="pt-7" style={{ borderTop: '1px solid var(--border)' }}>
      <h2 className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-3 flex items-center gap-1.5"
          style={{ color: 'var(--text-faint)' }}>
        <GraduationCap size={13} strokeWidth={2} /> Sensibilisation IA — registre (AI Act art. 4)
      </h2>

      <div className="card p-4 mb-3 flex items-center justify-between gap-4 flex-wrap">
        <div>
          <p className="text-[13px]" style={{ color: 'var(--text)' }}>
            <strong>{reg.done}/{reg.total}</strong> personne{reg.total > 1 ? 's' : ''} à jour
            {reg.coverage_pct != null && <span style={{ color: 'var(--text-muted)' }}> · {reg.coverage_pct} %</span>}
          </p>
          <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-faint)' }}>
            Contenu en version {reg.current_version} · attestation valable {reg.validity_days} jours
          </p>
        </div>
        {reg.pending > 0 && (
          <span className="badge text-[11px]"
                style={{ background: 'var(--danger-soft)', color: 'var(--danger)', border: 'none' }}>
            {reg.pending} à régulariser
          </span>
        )}
      </div>

      <div className="card overflow-hidden">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wide"
                style={{ color: 'var(--text-faint)', borderBottom: '1px solid var(--border)' }}>
              <th className="font-medium px-4 py-2.5">Nom</th>
              <th className="font-medium px-4 py-2.5 hidden md:table-cell">Email</th>
              <th className="font-medium px-4 py-2.5">État</th>
              <th className="font-medium px-4 py-2.5 hidden sm:table-cell">Attesté le</th>
            </tr>
          </thead>
          <tbody>
            {reg.people.map(p => {
              const st = LIT_STATE[p.state] || LIT_STATE.never
              return (
                <tr key={p.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td className="px-4 py-2.5" style={{ color: 'var(--text)' }}>{p.name || '—'}</td>
                  <td className="px-4 py-2.5 hidden md:table-cell" style={{ color: 'var(--text-muted)' }}>{p.email}</td>
                  <td className="px-4 py-2.5">
                    <span className="badge text-[10.5px]"
                          style={{ background: 'var(--surface-2)', color: st.tone, border: '1px solid var(--border)' }}>
                      {st.label}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 hidden sm:table-cell" style={{ color: 'var(--text-muted)' }}>
                    {p.ack_at ? fmtDate(p.ack_at) : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
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

      <AiLiteracyRegister />

      {/* Le journal d'erreurs + l'usage IA ont leur page dédiée : Supervision. */}

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
