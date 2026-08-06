import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import api from '../lib/api'
import { ArrowLeft, Mail } from 'lucide-react'
import AuthBrand from '../components/AuthBrand'

export default function ForgotPasswordPage() {
  // Adresse reportée depuis l'écran de connexion : la personne vient de la
  // saisir, la lui redemander n'apporte rien qu'une occasion de se tromper.
  const [searchParams] = useSearchParams()
  const [email, setEmail] = useState(searchParams.get('email') || '')
  const [loading, setLoading] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await api.post('/auth/forgot-password', { email })
      setSent(true)
    } catch (err) {
      setError(err.response?.data?.detail || 'Une erreur est survenue.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-[360px]">
        <AuthBrand />

        <h1 className="text-[26px] font-semibold tracking-tightest text-[var(--text)] mb-1">
          Mot de passe oublié
        </h1>
        <p className="text-[13px] text-[var(--text-muted)] mb-6">
          Saisissez votre email pour recevoir un lien de réinitialisation.
        </p>

        {sent ? (
          <div className="space-y-4">
            <div
              className="flex items-start gap-3 rounded-md px-4 py-3 text-[13px]"
              style={{ background: 'var(--success-soft)', color: 'var(--success)' }}
            >
              <Mail size={15} className="shrink-0 mt-0.5" />
              <span>
                Si un compte existe pour <strong>{email}</strong>, un lien de réinitialisation vient d'être envoyé.
                Vérifiez aussi vos spams.
              </span>
            </div>
            <Link to="/login" className="btn-ghost w-full justify-center flex items-center gap-1.5">
              <ArrowLeft size={14} /> Retour à la connexion
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3.5">
            <div>
              <label className="label">Email</label>
              <input
                type="email"
                className="input"
                placeholder="vous@example.com"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                autoFocus
              />
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
              {loading ? 'Envoi...' : 'Envoyer le lien'}
            </button>

            <Link
              to="/login"
              className="flex items-center justify-center gap-1.5 text-[13px] text-[var(--text-muted)] hover:text-[var(--text)] transition-colors mt-2"
            >
              <ArrowLeft size={13} /> Retour à la connexion
            </Link>
          </form>
        )}
      </div>
    </div>
  )
}
