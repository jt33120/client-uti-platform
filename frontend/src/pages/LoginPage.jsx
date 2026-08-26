import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { Eye, EyeOff, ArrowRight, ShieldCheck, ArrowLeft, Loader2, Clock, CheckCircle, KeyRound, Mail } from 'lucide-react'
import AuthBrand from '../components/AuthBrand'

function CodeInput({ value, onChange, autoFocus }) {
  return (
    <input
      inputMode="numeric"
      autoComplete="one-time-code"
      maxLength={6}
      className="input text-center tracking-[0.5em] text-lg font-semibold"
      placeholder="••••••"
      value={value}
      onChange={e => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))}
      autoFocus={autoFocus}
      required
    />
  )
}

// Étape 2 : enrôlement (QR à scanner) ou vérification (code à saisir).
function MfaStep({ mfa, onSubmit, onBack, error }) {
  const [code, setCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const enroll = mfa.mode === 'enroll'

  const submit = async (e) => {
    e.preventDefault()
    setSubmitting(true)
    try { await onSubmit(code) } finally { setSubmitting(false) }
  }

  return (
    <>
      <div className="flex items-center gap-2 mb-1">
        <ShieldCheck size={18} style={{ color: 'var(--accent-text)' }} />
        <h1 className="text-[26px] font-semibold tracking-tightest text-[var(--text)]">
          {enroll ? 'Sécurisez votre compte' : 'Vérification en deux étapes'}
        </h1>
      </div>
      <p className="text-[13px] text-[var(--text-muted)] mb-5">
        {enroll
          ? 'Scannez ce QR code avec une application d\'authentification (Google Authenticator, Authy, Microsoft Authenticator), puis saisissez le code à 6 chiffres affiché.'
          : 'Saisissez le code à 6 chiffres affiché par votre application d\'authentification.'}
      </p>

      {enroll && (
        <div className="mb-5">
          <div className="flex justify-center mb-3">
            <div className="p-3 rounded-lg bg-white">
              <img src={mfa.qr} alt="QR code MFA" width={176} height={176} />
            </div>
          </div>
          <p className="text-[11px] text-center mb-1" style={{ color: 'var(--text-faint)' }}>
            Impossible de scanner ? Saisissez cette clé manuellement :
          </p>
          <p className="text-[12px] text-center font-mono break-all px-3" style={{ color: 'var(--text-muted)' }}>
            {mfa.secret}
          </p>
        </div>
      )}

      <form onSubmit={submit} className="space-y-3.5">
        <div>
          <label className="label">Code de vérification</label>
          <CodeInput value={code} onChange={setCode} autoFocus />
        </div>

        {error && (
          <div className="text-[13px] rounded-md px-3 py-2"
               style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}>
            {error}
          </div>
        )}

        <button type="submit" disabled={submitting || code.length < 6}
                className="btn-primary w-full justify-center !h-10">
          {submitting
            ? <Loader2 size={15} className="animate-spin" />
            : <span className="flex items-center gap-1.5">{enroll ? 'Activer et se connecter' : 'Vérifier'} <ArrowRight size={14} strokeWidth={2} /></span>}
        </button>
      </form>

      <button onClick={onBack}
              className="mt-5 mx-auto flex items-center gap-1.5 text-[12px] text-[var(--text-faint)] hover:text-[var(--text)] transition-colors">
        <ArrowLeft size={13} /> Revenir à la connexion
      </button>
    </>
  )
}

export default function LoginPage() {
  const { login, verifyMfa, enrollMfa, loading } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const sessionExpired = searchParams.get('reason') === 'expired'
  // Retour depuis l'inscription : compte créé, on invite à se connecter (la
  // double authentification s'enclenche à cette 1re connexion).
  const justRegistered = searchParams.get('registered') === '1'
  // Retour depuis /reset-password : le mot de passe vient d'être posé. Sans ce
  // relais, la confirmation mourait avec la page qu'on quitte.
  const justReset = searchParams.get('reset') === '1'
  const [form, setForm] = useState({ email: searchParams.get('email') || '', password: '' })
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  // Migration Supabase → VPS : affiché après un échec d'identifiants.
  const [heritage, setHeritage] = useState(false)
  const [mfa, setMfa] = useState(null) // { mode, challenge, qr, secret }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    try {
      const res = await login(form.email, form.password)
      if (res.mfa) {
        setError('')
        setMfa({ mode: res.mfa, challenge: res.challenge, qr: res.qr, secret: res.secret })
      } else {
        navigate('/dashboard')
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'Identifiants incorrects')
      // Un échec d'identifiants est, pendant la migration, bien plus souvent un
      // compte hérité qu'une faute de frappe : les mots de passe vivaient chez
      // Supabase et n'ont pas été repris. On explique donc AU MOMENT où la
      // personne est bloquée, plutôt que de compter sur un e-mail qu'elle n'a
      // peut-être pas lu.
      //
      // POURQUOI PAS UNE FENÊTRE APRÈS CONNEXION : elle ne s'afficherait que
      // pour les gens qui arrivent à se connecter, c'est-à-dire exactement ceux
      // qui n'ont pas le problème.
      //
      // Affiché seulement après un 401, jamais en permanence : le message
      // disparaît de lui-même quand tout le monde a repris la main, sans qu'il
      // faille penser à l'enlever. Et il ne révèle rien — il est identique que
      // l'adresse existe ou non.
      if (err.response?.status === 401) setHeritage(true)
    }
  }

  const submitCode = async (code) => {
    setError('')
    try {
      if (mfa.mode === 'enroll') await enrollMfa(mfa.challenge, code)
      else await verifyMfa(mfa.challenge, code)
      navigate('/dashboard')
    } catch (err) {
      setError(err.response?.data?.detail || 'Code invalide')
    }
  }

  const backToLogin = () => { setMfa(null); setError(''); setForm(p => ({ ...p, password: '' })) }

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-[360px]">
        <AuthBrand />

        {mfa ? (
          <MfaStep mfa={mfa} onSubmit={submitCode} onBack={backToLogin} error={error} />
        ) : (
          <>
            {sessionExpired && (
              <div className="mb-5 flex items-start gap-2.5 rounded-lg px-3.5 py-3 text-[13px]"
                style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)', border: '1px solid var(--border)' }}>
                <Clock size={15} className="shrink-0 mt-0.5" />
                <span>
                  <strong className="font-semibold">Vous avez été déconnecté.</strong><br />
                  Votre session a expiré après 3 heures (pour des raisons de sécurité). Reconnectez-vous pour continuer.
                </span>
              </div>
            )}
            {justReset && (
              <div className="mb-5 flex items-start gap-2.5 rounded-lg px-3.5 py-3 text-[13px]"
                style={{ background: 'var(--success-soft, rgba(16,163,74,0.10))', color: 'var(--success, #16a34a)', border: '1px solid var(--border)' }}>
                <CheckCircle size={15} className="shrink-0 mt-0.5" />
                <span>
                  <strong className="font-semibold">Mot de passe enregistré.</strong><br />
                  Connectez-vous avec celui que vous venez de choisir.
                </span>
              </div>
            )}
            {justRegistered && (
              <div className="mb-5 flex items-start gap-2.5 rounded-lg px-3.5 py-3 text-[13px]"
                style={{ background: 'var(--success-soft, rgba(16,163,74,0.10))', color: 'var(--success, #16a34a)', border: '1px solid var(--border)' }}>
                <CheckCircle size={15} className="shrink-0 mt-0.5" />
                <span>
                  <strong className="font-semibold">Compte créé.</strong><br />
                  Connectez-vous pour activer la double authentification et accéder à votre espace.
                </span>
              </div>
            )}
            <h1 className="text-[26px] font-semibold tracking-tightest text-[var(--text)] mb-1">Se connecter</h1>
            <p className="text-[13px] text-[var(--text-muted)] mb-6">
              Accédez à votre espace partenaire
            </p>

            <form onSubmit={handleSubmit} className="space-y-3.5" autoComplete="on">
              <div>
                <label className="label">Email</label>
                <input
                  type="email"
                  name="email"
                  autoComplete="email"
                  className="input"
                  placeholder="vous@example.com"
                  value={form.email}
                  onChange={e => setForm(p => ({ ...p, email: e.target.value }))}
                  required
                  autoFocus
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="label !mb-0">Mot de passe</label>
                  <Link
                    to="/forgot-password"
                    className="text-[12px] text-[var(--text-faint)] hover:text-[var(--text)] transition-colors"
                  >
                    Mot de passe oublié ?
                  </Link>
                </div>
                <div className="relative">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    name="password"
                    autoComplete="current-password"
                    className="input pr-9"
                    placeholder="••••••••"
                    value={form.password}
                    onChange={e => setForm(p => ({ ...p, password: e.target.value }))}
                    required
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

              {/* L'EXPLICATION PASSE DEVANT L'ERREUR, et ce n'est pas cosmétique.
                  Un utilisateur hérité n'a pas « fait une faute de frappe » : il
                  subit une migration. Laisser « Email ou mot de passe incorrect »
                  dominer l'écran l'invite à conclure qu'il s'est trompé et à
                  réessayer le même mot de passe — indéfiniment. */}
              {heritage && (
                <div
                  className="rounded-lg px-3.5 py-3 text-[13px]"
                  style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)', border: '1px solid var(--border)' }}
                >
                  <div className="flex items-start gap-2.5">
                    <KeyRound size={15} className="shrink-0 mt-0.5" />
                    <span>
                      {/* « Changer de main » signifie PASSER À QUELQU'UN D'AUTRE.
                          La phrase disait donc littéralement l'inverse de ce
                          qu'elle voulait rassurer, en première ligne.

                          « SI VOUS AVIEZ UN COMPTE » N'EST PAS UNE PRÉCAUTION DE
                          STYLE. Ce bandeau s'affiche sur TOUT 401, délibérément :
                          l'afficher seulement aux comptes connus révélerait qui a
                          un compte. Mais l'ancienne rédaction AFFIRMAIT « votre
                          compte et vos données sont intacts » — un fait, adressé
                          à quelqu'un qui n'a peut-être aucun compte, et faux pour
                          qui a déjà repris la main et vient juste de mal taper son
                          mot de passe.

                          Constaté le 26 août : le fondateur a saisi une adresse
                          sans compte, lu cette phrase, et en a conclu qu'un compte
                          avait été SUPPRIMÉ. Un message conçu pour rassurer a
                          fabriqué la croyance d'une perte de données.

                          La condition ne bouge pas — c'est la formulation qui
                          cesse d'affirmer. Le texte reste rigoureusement identique
                          que l'adresse existe ou non : on ne divulgue toujours
                          rien. */}
                      <strong className="font-semibold">
                        Les mots de passe n'ont pas été conservés lors de notre changement de serveur.
                      </strong><br />
                      <strong className="font-semibold">Si vous aviez un compte, il est intact</strong> —
                      ses données aussi. Il suffit d'en choisir un nouveau. Si la double
                      authentification était déjà active, votre application
                      d'authentification continue de fonctionner.
                    </span>
                  </div>
                  {/* Un lien noyé dans cinq lignes de texte ne se voit pas. */}
                  <Link
                    to={`/forgot-password${form.email ? `?email=${encodeURIComponent(form.email)}` : ''}`}
                    className="btn-primary w-full justify-center !h-9 mt-3 flex items-center gap-1.5"
                  >
                    <Mail size={14} /> Recevoir un lien par e-mail
                  </Link>
                </div>
              )}

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
                {loading ? 'Connexion...' : (
                  <span className="flex items-center gap-1.5">
                    Se connecter <ArrowRight size={14} strokeWidth={2} />
                  </span>
                )}
              </button>
            </form>

            <p className="text-center text-[12px] text-[var(--text-faint)] mt-6">
              L'accès à la plateforme se fait uniquement sur invitation.
            </p>
            <p className="text-center text-[12px] text-[var(--text-muted)] mt-2">
              Vous souhaitez devenir partenaire ?{' '}
              <Link to="/contact" className="font-medium" style={{ color: 'var(--accent-text)' }}>Nous contacter</Link>
            </p>
          </>
        )}
      </div>
    </div>
  )
}
