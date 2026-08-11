import { useState, useEffect } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { Eye, EyeOff, CheckCircle, ArrowLeft, AlertCircle, RefreshCw } from 'lucide-react'
import AuthBrand from '../components/AuthBrand'

export default function ResetPasswordPage() {
  const navigate = useNavigate()
  const { logout } = useAuth()
  const [searchParams] = useSearchParams()
  const [token, setToken] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')

  // Le lien envoyé par email porte un jeton OPAQUE en query string :
  //   /reset-password?token=XXXX
  // Auparavant Supabase le posait dans le FRAGMENT (#access_token=…&type=recovery)
  // et cette page le décodait en base64 pour en extraire l'email. Le backend ne
  // voyait alors jamais le jeton : il ne pouvait ni le limiter à un seul usage,
  // ni le révoquer. Le nouveau jeton ne se décode pas — c'est 256 bits de hasard
  // dont seule l'empreinte SHA-256 existe en base.
  useEffect(() => {
    const recu = searchParams.get('token')
    if (!recu) {
      setError('Lien de réinitialisation invalide. Veuillez refaire une demande.')
      return
    }
    setToken(recu)
    // Le jeton sort de la barre d'adresse dès qu'il est en mémoire : sans ça il
    // resterait dans l'historique du navigateur et partirait en en-tête Referer
    // vers toute ressource tierce chargée par la page.
    window.history.replaceState({}, '', '/reset-password')
    // L'email ne peut plus être lu dans le jeton : on le demande au serveur.
    // Il ne sert qu'à alimenter le champ « username » masqué, pour que le
    // trousseau du navigateur rattache le nouveau mot de passe au bon compte.
    api.post('/auth/reset-password/verify', { token: recu })
      .then(res => {
        setEmail(res.data.email || '')
        // La session en cours n'est fermée QU'UNE FOIS le lien reconnu valide.
        //
        // Elle doit l'être : sans cela, quelqu'un déjà connecté qui ouvre le
        // lien d'un autre compte poserait un mot de passe sur ce compte-là tout
        // en restant connecté sur le sien — deux identités mêlées sur un écran
        // qui parle de mot de passe.
        //
        // Mais le faire AVANT de vérifier déconnectait sur un lien mort : ouvrir
        // par curiosité un lien déjà consommé, dans un onglet, faisait perdre sa
        // session à quelqu'un qui n'avait rien demandé et ne pouvait rien
        // réinitialiser. On paie le coût là où il achète quelque chose.
        logout()
      })
      .catch(err => {
        setToken('')
        setError(err.response?.data?.detail || 'Lien de réinitialisation invalide ou expiré.')
      })
  }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (password !== confirmPassword) {
      setError('Les mots de passe ne correspondent pas.')
      return
    }
    setError('')
    setLoading(true)
    try {
      await api.post('/auth/reset-password', { token, new_password: password })
      setDone(true)
      // La confirmation SURVIT à la redirection. Affichée seulement ici, elle
      // disparaissait avec la page : on validait, on se retrouvait sur l'écran
      // de connexion, et rien ne disait si ça avait marché — on l'apprenait en
      // essayant. Le paramètre reprend le motif déjà en place pour l'inscription
      // (`?registered=1`).
      setTimeout(() => { logout(); navigate('/login?reset=1') }, 3000)
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
          Nouveau mot de passe
        </h1>
        <p className="text-[13px] text-[var(--text-muted)] mb-6">
          Choisissez un nouveau mot de passe pour votre compte.
        </p>

        {done ? (
          <div className="space-y-4">
            <div
              className="flex items-start gap-3 rounded-md px-4 py-3 text-[13px]"
              style={{ background: 'var(--success-soft)', color: 'var(--success)' }}
            >
              <CheckCircle size={15} className="shrink-0 mt-0.5" />
              <span>Mot de passe mis à jour. Redirection vers la connexion...</span>
            </div>
            <Link to="/login" className="btn-ghost w-full justify-center flex items-center gap-1.5">
              <ArrowLeft size={14} /> Se connecter
            </Link>
          </div>
        ) : (!token && error) ? (
          // Lien mort : jeton absent, trafiqué, expiré ou déjà consommé.
          //
          // Avant, le formulaire restait affiché — champs saisissables, seul le
          // bouton grisé — et la seule issue proposée était « retour à la
          // connexion ». C'est-à-dire : on laissait la personne remplir deux
          // champs qui ne servaient à rien, puis on la renvoyait exactement là
          // où elle vient d'échouer. L'action utile à cet instant précis est
          // d'en redemander un ; c'est donc la seule qu'on propose.
          <div className="space-y-4">
            <div
              className="flex items-start gap-3 rounded-md px-4 py-3 text-[13px]"
              style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}
            >
              <AlertCircle size={15} className="shrink-0 mt-0.5" />
              <span>
                {error}<br />
                <span style={{ opacity: 0.85 }}>
                  Ces liens ne servent qu'une fois et expirent. En demander un
                  nouveau prend quelques secondes.
                </span>
              </span>
            </div>
            <Link to="/forgot-password" className="btn-primary w-full justify-center !h-10 flex items-center gap-1.5">
              <RefreshCw size={14} /> Demander un nouveau lien
            </Link>
            <Link
              to="/login"
              className="flex items-center justify-center gap-1.5 text-[13px] text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
            >
              <ArrowLeft size={13} /> Retour à la connexion
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3.5" autoComplete="on">
            {/* Hidden username field lets macOS Keychain associate the new password with this account */}
            <input type="hidden" name="username" autoComplete="username" value={email} readOnly />

            <div>
              <label className="label">Nouveau mot de passe</label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  name="new-password"
                  autoComplete="new-password"
                  className="input pr-9"
                  placeholder="Min. 8 caractères"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required
                  minLength={8}
                  autoFocus
                  disabled={!token}
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

            <div>
              <label className="label">Confirmer le mot de passe</label>
              <div className="relative">
                <input
                  type={showConfirm ? 'text' : 'password'}
                  name="confirm-password"
                  autoComplete="new-password"
                  className="input pr-9"
                  placeholder="Répétez le mot de passe"
                  value={confirmPassword}
                  onChange={e => setConfirmPassword(e.target.value)}
                  required
                  minLength={8}
                  disabled={!token}
                />
                <button
                  type="button"
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--text-faint)] hover:text-[var(--text)]"
                  onClick={() => setShowConfirm(p => !p)}
                >
                  {showConfirm ? <EyeOff size={14} /> : <Eye size={14} />}
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
              disabled={loading || !token}
              className="btn-primary w-full justify-center !h-10"
            >
              {loading ? 'Enregistrement...' : 'Mettre à jour le mot de passe'}
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
