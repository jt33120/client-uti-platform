import { useState, useEffect } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { Eye, EyeOff, AlertCircle, MailQuestion } from 'lucide-react'
import api from '../lib/api'

// Account creation is invitation-only: without a valid invite token there is
// no form at all — the role always comes from the invitation server-side.
export default function RegisterPage() {
  const { register, loading, user, logout } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const inviteToken = searchParams.get('invite')

  const [form, setForm] = useState({ email: '', password: '', name: '', role: '' })
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [inviteStatus, setInviteStatus] = useState(inviteToken ? 'loading' : 'missing')

  useEffect(() => {
    if (!inviteToken) return
    api.get(`/invitations/validate/${inviteToken}`)
      .then(res => {
        setForm(f => ({ ...f, email: res.data.email, name: res.data.name || '', role: res.data.role || 'ao' }))
        setInviteStatus('valid')
      })
      .catch(err => {
        setInviteStatus('invalid')
        setError(err.response?.data?.detail || "Lien d'invitation invalide ou expiré.")
      })
  }, [inviteToken])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (form.password.length < 8) { setError('Mot de passe trop court (min 8 caractères)'); return }
    setError('')
    try {
      await register(form.email, form.password, form.name, form.role, inviteToken)
      // L'inscription n'ouvre pas de session : on renvoie vers /login, où la
      // double authentification obligatoire s'applique à la 1re connexion.
      navigate(`/login?registered=1&email=${encodeURIComponent(form.email)}`)
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur lors de l\'inscription')
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-[380px]">
        <div className="flex items-center gap-2.5 mb-8">
          <img src="/logo.png" alt="Groupement-IT" className="h-8 w-8 object-contain" />
          <div className="leading-tight">
            <div className="text-[14px] font-semibold tracking-tightest text-[var(--text)]">Groupement-IT</div>
            <div className="text-[11px] text-[var(--text-faint)]">Plateforme Partenaires</div>
          </div>
        </div>

        {user ? (
          // Session déjà active : un lien d'invitation ne redirige plus en
          // silence (bug #3). On explique et on propose de se déconnecter.
          <div className="text-center py-6">
            <MailQuestion size={28} className="mx-auto mb-3" style={{ color: 'var(--text-faint)' }} />
            <h1 className="text-[18px] font-semibold tracking-tightest text-[var(--text)] mb-1.5">
              Vous êtes déjà connecté
            </h1>
            <p className="text-[13px] text-[var(--text-muted)] leading-relaxed mb-5">
              Vous êtes connecté en tant que <span className="font-medium text-[var(--text)]">{user.email}</span>.
              Pour créer le compte lié à cette invitation, déconnectez-vous d'abord.
            </p>
            <button onClick={() => logout()} className="btn-primary w-full justify-center !h-10">
              Se déconnecter et continuer l'inscription
            </button>
            <button onClick={() => navigate('/dashboard')} className="btn-ghost w-full justify-center !h-9 mt-2 text-[13px]">
              Revenir à mon espace
            </button>
          </div>
        ) : inviteStatus === 'missing' ? (
          <div className="text-center py-6">
            <MailQuestion size={28} className="mx-auto mb-3" style={{ color: 'var(--text-faint)' }} />
            <h1 className="text-[18px] font-semibold tracking-tightest text-[var(--text)] mb-1.5">
              Accès sur invitation
            </h1>
            <p className="text-[13px] text-[var(--text-muted)] leading-relaxed">
              La création de compte se fait uniquement via un lien d'invitation
              envoyé par l'équipe Groupement-IT. Vérifiez votre boîte mail, ou
              contactez votre interlocuteur Groupement-IT.
            </p>
          </div>
        ) : (
          <>
            <h1 className="text-[22px] font-semibold tracking-tightest text-[var(--text)] mb-1">Créer un compte</h1>
            <p className="text-[13px] text-[var(--text-muted)] mb-6">
              Vous avez été invité à rejoindre la plateforme
            </p>

            {inviteStatus === 'loading' && (
              <div className="flex items-center justify-center py-8 text-[13px] gap-2 text-[var(--text-muted)]">
                <span className="w-3.5 h-3.5 border-2 rounded-full animate-spin"
                      style={{ borderColor: 'var(--border)', borderTopColor: 'var(--text)' }} />
                Vérification de l'invitation...
              </div>
            )}

            {inviteStatus === 'invalid' && (
              <div
                className="flex items-center gap-2 rounded-md px-3 py-2.5 mb-4 text-[13px]"
                style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}
              >
                <AlertCircle size={14} className="shrink-0" />
                {error}
              </div>
            )}

            {inviteStatus === 'valid' && (
              <form onSubmit={handleSubmit} className="space-y-3.5">
                <div>
                  <label className="label">Nom complet</label>
                  <input
                    type="text"
                    className="input"
                    value={form.name}
                    onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
                    readOnly
                    required
                  />
                  <p className="text-[11px] text-[var(--text-faint)] mt-1">
                    Nom défini par l'administrateur de la plateforme.
                  </p>
                </div>

                <div>
                  <label className="label">Email</label>
                  <input
                    type="email"
                    className="input"
                    value={form.email}
                    readOnly
                    required
                  />
                </div>

                <div>
                  <label className="label">Mot de passe</label>
                  <div className="relative">
                    <input
                      type={showPassword ? 'text' : 'password'}
                      className="input pr-9"
                      placeholder="Min. 8 caractères"
                      value={form.password}
                      onChange={e => setForm(p => ({ ...p, password: e.target.value }))}
                      required
                      autoFocus
                    />
                    <button
                      type="button"
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--text-faint)] hover:text-[var(--text)]"
                      onClick={() => setShowPassword(p => !p)}
                    >
                      {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>

                {error && (
                  <div
                    className="text-[13px] rounded-md px-3 py-2"
                    style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}
                  >
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={loading}
                  className="btn-primary w-full justify-center !h-10"
                >
                  {loading ? 'Création...' : 'Créer le compte'}
                </button>
              </form>
            )}
          </>
        )}

        <p className="text-center text-[13px] text-[var(--text-muted)] mt-6">
          Déjà un compte ?{' '}
          <Link to="/login" className="font-medium text-[var(--text)] hover:underline underline-offset-2">
            Se connecter
          </Link>
        </p>
      </div>
    </div>
  )
}
