import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { publicApi } from '../lib/api'
import { FileText, Loader2, Check, Clock, AlertCircle } from 'lucide-react'

// Décisions client — libellés FR côté client, valeurs alignées sur le backend
// (ao_consultant_state.client_decision : 'interesse' | 'a_revoir' | 'refuse').
const DECISIONS = [
  { value: 'interesse', label: 'Intéressé', emoji: '👍', color: 'var(--success)', soft: 'var(--success-soft)' },
  { value: 'a_revoir',  label: 'À revoir',  emoji: '🤔', color: 'var(--warning)', soft: 'var(--warning-soft)' },
  { value: 'refuse',    label: 'Pas retenu', emoji: '👎', color: 'var(--danger)',  soft: 'var(--danger-soft)' },
]

// Page PUBLIQUE — vitrine Groupement-IT vue par le CLIENT final. Aucune auth :
// tout passe par publicApi (pas d'Authorization, pas de redirection /login).
// Le scope (AO + profils présentés) est dérivé du token côté backend.
export default function ClientReviewPage() {
  const { token } = useParams()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)   // 'not_found' | 'generic' | null
  const [data, setData] = useState(null)      // { ao, profiles, expired }
  // État local par consultant : décision courante, note éditable, en-cours, confirmation.
  const [decisions, setDecisions] = useState({})   // { [cid]: 'interesse'|... }
  const [notes, setNotes] = useState({})           // { [cid]: string }
  const [saving, setSaving] = useState({})         // { [cid]: bool }
  const [saved, setSaved] = useState({})           // { [cid]: bool }
  const [rowError, setRowError] = useState({})     // { [cid]: string }

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const { data: payload } = await publicApi.get(`/client-review/${token}`)
      setData(payload)
      const d = {}, n = {}
      for (const p of (payload?.profiles || [])) {
        if (p.decision) d[p.consultant_id] = p.decision
        n[p.consultant_id] = p.note || ''
      }
      setDecisions(d); setNotes(n)
    } catch (err) {
      // 404 = token inconnu / expiré / révoqué → message courtois, jamais /login.
      setError(err?.response?.status === 404 ? 'not_found' : 'generic')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => { load() }, [load])

  const respond = async (cid, decision) => {
    setSaving(s => ({ ...s, [cid]: true }))
    setRowError(e => ({ ...e, [cid]: '' }))
    try {
      await publicApi.post(`/client-review/${token}/respond`, {
        consultant_id: cid,
        decision,
        note: (notes[cid] || '').trim() || null,
      })
      setDecisions(d => ({ ...d, [cid]: decision }))
      setSaved(s => ({ ...s, [cid]: true }))
    } catch (err) {
      setRowError(e => ({
        ...e,
        [cid]: err?.response?.status === 404
          ? "Ce lien n'est plus valide."
          : "Enregistrement impossible. Réessayez dans un instant.",
      }))
    } finally {
      setSaving(s => ({ ...s, [cid]: false }))
    }
  }

  // ── États de page ────────────────────────────────────────────────
  if (loading) {
    return (
      <Shell>
        <div className="flex items-center justify-center py-20 text-[13px]" style={{ color: 'var(--text-muted)' }}>
          <Loader2 size={18} className="animate-spin mr-2" /> Chargement…
        </div>
      </Shell>
    )
  }

  if (error === 'not_found' || data?.expired) {
    return (
      <Shell>
        <div className="card p-8 text-center">
          <Clock size={30} className="mx-auto mb-3" style={{ color: 'var(--text-faint)' }} />
          <h1 className="text-lg font-semibold mb-1" style={{ color: 'var(--text)' }}>Lien indisponible</h1>
          <p className="text-[13px]" style={{ color: 'var(--text-muted)' }}>
            Ce lien de consultation n'est plus valide ou a expiré. Rapprochez-vous de votre
            interlocuteur Groupement-IT pour recevoir un nouvel accès.
          </p>
        </div>
      </Shell>
    )
  }

  if (error === 'generic') {
    return (
      <Shell>
        <div className="card p-8 text-center">
          <AlertCircle size={30} className="mx-auto mb-3" style={{ color: 'var(--danger)' }} />
          <h1 className="text-lg font-semibold mb-1" style={{ color: 'var(--text)' }}>Une erreur est survenue</h1>
          <p className="text-[13px] mb-4" style={{ color: 'var(--text-muted)' }}>
            Impossible de charger cette page pour le moment.
          </p>
          <button onClick={load} className="btn-ghost mx-auto">Réessayer</button>
        </div>
      </Shell>
    )
  }

  const ao = data?.ao || {}
  const profiles = data?.profiles || []

  return (
    <Shell>
      {/* En-tête de l'AO */}
      <div className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight mb-1" style={{ color: 'var(--text)' }}>
          {ao.title || 'Profils proposés'}
        </h1>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px]" style={{ color: 'var(--text-muted)' }}>
          {ao.client_name && <span>{ao.client_name}</span>}
          {ao.client_name && ao.reference && <span style={{ color: 'var(--text-faint)' }}>·</span>}
          {ao.reference && <span style={{ color: 'var(--text-faint)' }}>Réf. {ao.reference}</span>}
        </div>
        <p className="text-[13px] mt-3" style={{ color: 'var(--text-muted)' }}>
          Voici les profils que nous vous proposons. Consultez chaque CV et indiquez-nous votre retour
          pour chacun d'eux — vous pouvez revenir modifier vos réponses à tout moment.
        </p>
      </div>

      {profiles.length === 0 ? (
        <div className="card p-8 text-center text-[13px]" style={{ color: 'var(--text-muted)' }}>
          Aucun profil n'est disponible à la consultation pour le moment.
        </div>
      ) : (
        <div className="space-y-4">
          {profiles.map((p) => {
            const cid = p.consultant_id
            const current = decisions[cid] || null
            const isSaving = !!saving[cid]
            return (
              <div key={cid} className="card p-5">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                  <div className="min-w-0">
                    <div className="text-[15px] font-semibold truncate" style={{ color: 'var(--text)' }}>
                      {p.name || 'Consultant'}
                    </div>
                  </div>
                  {p.cv_url ? (
                    <a href={p.cv_url} target="_blank" rel="noopener noreferrer"
                       className="btn-ghost shrink-0">
                      <FileText size={14} /> Voir le CV
                    </a>
                  ) : (
                    <span className="text-[12px]" style={{ color: 'var(--text-faint)' }}>CV indisponible</span>
                  )}
                </div>

                {/* Boutons de décision */}
                <div className="grid grid-cols-3 gap-2">
                  {DECISIONS.map((opt) => {
                    const active = current === opt.value
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        disabled={isSaving}
                        onClick={() => respond(cid, opt.value)}
                        className="inline-flex flex-col items-center justify-center gap-1 rounded-md py-2.5 px-2 text-[13px] font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        style={{
                          border: `1px solid ${active ? opt.color : 'var(--border)'}`,
                          backgroundColor: active ? opt.soft : 'transparent',
                          color: active ? opt.color : 'var(--text)',
                        }}
                        aria-pressed={active}
                      >
                        <span className="text-[18px] leading-none">{opt.emoji}</span>
                        <span>{opt.label}</span>
                      </button>
                    )
                  })}
                </div>

                {/* Note optionnelle */}
                <div className="mt-3">
                  <label className="label" htmlFor={`note-${cid}`}>Commentaire (facultatif)</label>
                  <textarea
                    id={`note-${cid}`}
                    className="input h-20 resize-none"
                    value={notes[cid] || ''}
                    onChange={(e) => {
                      const v = e.target.value
                      setNotes(n => ({ ...n, [cid]: v }))
                      setSaved(s => ({ ...s, [cid]: false }))
                    }}
                    placeholder="Une précision sur ce profil…"
                  />
                </div>

                {/* Confirmation / erreur / en-cours */}
                <div className="mt-2 min-h-[18px] text-[12px]">
                  {isSaving && (
                    <span className="inline-flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }}>
                      <Loader2 size={12} className="animate-spin" /> Enregistrement…
                    </span>
                  )}
                  {!isSaving && rowError[cid] && (
                    <span style={{ color: 'var(--danger)' }}>{rowError[cid]}</span>
                  )}
                  {!isSaving && !rowError[cid] && saved[cid] && (
                    <span className="inline-flex items-center gap-1.5" style={{ color: 'var(--success)' }}>
                      <Check size={13} /> Merci, réponse enregistrée
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <p className="text-center text-[11px] mt-8" style={{ color: 'var(--text-faint)' }}>
        Groupement-IT · Consultation client
      </p>
    </Shell>
  )
}

// Cadre commun : fond, logo, centrage responsive.
function Shell({ children }) {
  return (
    <div className="min-h-screen px-4 py-8 sm:py-12" style={{ background: 'var(--bg)' }}>
      <div className="w-full max-w-[640px] mx-auto">
        <div className="flex items-center gap-2.5 mb-8">
          <img src="/logo.png" alt="Groupement-IT" className="h-8 w-8 object-contain" />
          <div className="leading-tight">
            <div className="text-[14px] font-semibold tracking-tight" style={{ color: 'var(--text)' }}>Groupement-IT</div>
            <div className="text-[11px]" style={{ color: 'var(--text-faint)' }}>Retour client</div>
          </div>
        </div>
        {children}
      </div>
    </div>
  )
}
