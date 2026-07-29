import { useEffect, useState, useCallback } from 'react'
import api from '../lib/api'
import {
  ShieldCheck, ShieldAlert, Loader2, Upload, BadgeCheck, FileText, Clock,
} from 'lucide-react'

// Conformité partenaire — obligation de vigilance (art. L.8222-1 c. trav.).
//
// Mode ALERTE et non blocage : l'obligation se rattache au CONTRAT de prestation
// (≥ 5 000 € HT par opération), pas à la présentation d'une candidature.
//
// Le libellé d'état importe autant que la donnée. « Non vérifiée » n'est pas un
// détail cosmétique : détenir l'attestation ne purge pas l'obligation, il faut en
// contrôler l'authenticité auprès de l'URSSAF. Un écran qui afficherait « OK » dès
// le dépôt du PDF donnerait une fausse assurance — c'est exactement le piège.

const STATE_META = {
  missing: { label: 'Manquante', tone: 'var(--danger)', Icon: ShieldAlert },
  expired: { label: 'Périmée', tone: 'var(--danger)', Icon: ShieldAlert },
  unverified: { label: 'Non vérifiée', tone: 'var(--warning, #d97706)', Icon: Clock },
  expiring: { label: 'Bientôt échue', tone: 'var(--warning, #d97706)', Icon: Clock },
  valid: { label: 'À jour', tone: 'var(--success)', Icon: BadgeCheck },
}

const ORDER = ['vigilance', 'immatriculation', 'salaries_etrangers']

const fmt = (iso) => (iso ? new Date(iso).toLocaleDateString('fr-FR') : '—')

export default function PartnerCompliance({ partnerId }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    api.get(`/partners/${partnerId}/compliance`)
      .then(r => setData(r.data))
      .catch(() => setData(false))
  }, [partnerId])

  useEffect(() => { load() }, [load])

  if (data === false || data === null) return null

  const upload = async (docType, file, issuedAt) => {
    setBusy(docType); setErr('')
    try {
      const fd = new FormData()
      fd.append('doc_type', docType)
      fd.append('issued_at', issuedAt)
      if (file) fd.append('file', file)
      await api.post(`/partners/${partnerId}/compliance`, fd)
      load()
    } catch (e) {
      setErr(e.response?.data?.detail || 'Dépôt impossible.')
    } finally { setBusy('') }
  }

  const verify = async (docId) => {
    setBusy(docId); setErr('')
    try {
      const ref = window.prompt(
        'Code de sécurité relevé sur l’attestation (facultatif).\n\n' +
        'Confirmez uniquement après avoir contrôlé l’authenticité sur le site de l’URSSAF.'
      )
      if (ref === null) return
      await api.post(`/partners/${partnerId}/compliance/${docId}/verify`, { authenticity_ref: ref || null })
      load()
    } catch (e) {
      setErr(e.response?.data?.detail || 'Enregistrement impossible.')
    } finally { setBusy('') }
  }

  const overall = STATE_META[data.overall] || STATE_META.missing

  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest flex items-center gap-2">
          <ShieldCheck size={13} className="text-brand-400" /> Conformité — obligation de vigilance
        </h2>
        <span className="badge text-[10.5px]"
              style={{ background: 'var(--surface-2)', color: overall.tone, border: '1px solid var(--border)' }}>
          {overall.label}
        </span>
      </div>

      <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-faint)' }}>
        Art. L.8222-1 du code du travail : pour toute opération d’au moins 5 000 € HT, les pièces
        doivent être vérifiées à la conclusion du contrat puis <strong>tous les six mois</strong>.
        Un manquement engage la solidarité financière. Ces alertes ne bloquent aucune action.
      </p>

      {err && <p className="text-[12px]" style={{ color: 'var(--danger)' }}>{err}</p>}

      <div className="space-y-3">
        {ORDER.map((type) => {
          const t = data.by_type?.[type]
          if (!t) return null
          const meta = STATE_META[t.state] || STATE_META.missing
          const doc = t.doc
          return (
            <div key={type} className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="min-w-0">
                  <p className="text-[13px] flex items-center gap-1.5" style={{ color: 'var(--text)' }}>
                    <meta.Icon size={13} style={{ color: meta.tone }} />
                    {t.label}
                    {!t.required && (
                      <span className="text-[9px] font-semibold px-1 py-px rounded border"
                            style={{ color: 'var(--text-faint)', borderColor: 'var(--border)' }}
                            title="Exigible à la conclusion, et seulement si le partenaire emploie des salariés soumis à autorisation de travail">
                        SI CONCERNÉ
                      </span>
                    )}
                  </p>
                  <p className="text-[10.5px] mt-0.5" style={{ color: 'var(--text-faint)' }}>{t.legal}</p>
                  {doc && (
                    <p className="text-[11px] mt-1" style={{ color: 'var(--text-muted)' }}>
                      Émise le {fmt(doc.issued_at)}
                      {t.expires_at && <> · échéance le {fmt(t.expires_at)}</>}
                      {t.advisory && <span style={{ color: 'var(--text-faint)' }}> (usage, non légal)</span>}
                    </p>
                  )}
                </div>
                <span className="badge text-[10.5px] shrink-0"
                      style={{ background: 'var(--surface-2)', color: meta.tone, border: '1px solid var(--border)' }}>
                  {meta.label}
                </span>
              </div>

              <div className="flex items-center gap-2 mt-2.5 flex-wrap">
                <label className="btn-ghost text-[11px] px-2 py-1 cursor-pointer inline-flex items-center gap-1.5">
                  {busy === type ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />}
                  {doc ? 'Remplacer' : 'Déposer'}
                  <input type="file" className="hidden" accept=".pdf,.jpg,.jpeg,.png"
                         onChange={(e) => {
                           const f = e.target.files?.[0]
                           if (!f) return
                           // La date d'ÉMISSION fait courir la validité, pas la date
                           // de dépôt : on la demande explicitement.
                           const d = window.prompt('Date d’émission de la pièce (AAAA-MM-JJ)',
                                                   new Date().toISOString().slice(0, 10))
                           e.target.value = ''
                           if (d) upload(type, f, d)
                         }} />
                </label>

                {doc?.file_url && (
                  <a href={`/api/partners/${partnerId}/compliance/${doc.id}/file`}
                     target="_blank" rel="noreferrer"
                     className="btn-ghost text-[11px] px-2 py-1 inline-flex items-center gap-1.5">
                    <FileText size={11} /> Voir
                  </a>
                )}

                {doc && t.state === 'unverified' && (
                  <button onClick={() => verify(doc.id)} disabled={busy === doc.id}
                          className="btn-primary text-[11px] px-2.5 py-1 inline-flex items-center gap-1.5">
                    {busy === doc.id ? <Loader2 size={11} className="animate-spin" /> : <BadgeCheck size={11} />}
                    Vérifier l’authenticité
                  </button>
                )}
                {doc?.authenticity_checked_at && (
                  <span className="text-[10.5px]" style={{ color: 'var(--text-faint)' }}>
                    Authenticité vérifiée le {fmt(doc.authenticity_checked_at)}
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
