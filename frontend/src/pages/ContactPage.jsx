import { useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../lib/api'
import { ArrowLeft, Send, Loader2, CheckCircle } from 'lucide-react'
import AuthBrand from '../components/AuthBrand'

const EMPTY = { nom: '', prenom: '', email: '', phone: '', company: '', siret: '', message: '' }

export default function ContactPage() {
  const [form, setForm] = useState(EMPTY)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [sent, setSent] = useState(false)
  const set = (k) => (e) => setForm(p => ({ ...p, [k]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    setError(''); setLoading(true)
    try {
      await api.post('/support/partner-request', form)
      setSent(true)
    } catch (err) {
      setError(err.response?.data?.detail || "Échec de l'envoi. Réessayez dans un instant.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-[460px]">
        <AuthBrand subtitle="Devenir partenaire" />

        {sent ? (
          <div className="card p-8 text-center">
            <CheckCircle size={32} className="mx-auto mb-3" style={{ color: 'var(--accent-text)' }} />
            <h1 className="text-lg font-semibold text-[var(--text)] mb-1">Demande envoyée</h1>
            <p className="text-[13px] text-[var(--text-muted)]">
              Merci ! Votre demande a bien été transmise à notre équipe. Nous vous recontacterons rapidement.
            </p>
            <Link to="/login" className="btn-ghost mt-5 inline-flex"><ArrowLeft size={14} /> Retour à la connexion</Link>
          </div>
        ) : (
          <>
            <h1 className="text-[22px] font-semibold tracking-tightest text-[var(--text)] mb-1">Nous contacter</h1>
            <p className="text-[13px] text-[var(--text-muted)] mb-6">
              Vous souhaitez devenir partenaire du Groupement-IT ? Laissez-nous vos coordonnées.
            </p>

            <form onSubmit={submit} className="space-y-3.5">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="label">Prénom *</label>
                  <input className="input" required value={form.prenom} onChange={set('prenom')} autoComplete="given-name" />
                </div>
                <div>
                  <label className="label">Nom *</label>
                  <input className="input" required value={form.nom} onChange={set('nom')} autoComplete="family-name" />
                </div>
              </div>
              <div>
                <label className="label">Adresse e-mail *</label>
                <input className="input" type="email" required value={form.email} onChange={set('email')} autoComplete="email" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="label">Téléphone</label>
                  <input className="input" value={form.phone} onChange={set('phone')} autoComplete="tel" />
                </div>
                <div>
                  <label className="label">SIRET</label>
                  <input className="input" value={form.siret} onChange={set('siret')} placeholder="Siège social" />
                </div>
              </div>
              <div>
                <label className="label">Société *</label>
                <input className="input" required value={form.company} onChange={set('company')} autoComplete="organization" />
              </div>
              <div>
                <label className="label">Commentaire</label>
                <textarea className="input h-24 resize-none" value={form.message} onChange={set('message')}
                  placeholder="Votre activité, votre besoin…" />
              </div>

              {error && (
                <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</div>
              )}

              <button type="submit" disabled={loading} className="btn-primary w-full justify-center py-2.5">
                {loading ? <><Loader2 size={15} className="animate-spin" /> Envoi…</> : <><Send size={15} /> Envoyer ma demande</>}
              </button>
              <Link to="/login" className="btn-ghost w-full justify-center py-2"><ArrowLeft size={14} /> Retour à la connexion</Link>
            </form>
          </>
        )}
      </div>
    </div>
  )
}
