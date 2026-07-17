import { useEffect, useState, useCallback } from 'react'
import api from '../lib/api'
import { trackAiRegenerate } from '../lib/rum'
import { useAuth } from '../contexts/AuthContext'
import AiFeedback from './AiFeedback'
import { X, Loader2, Printer, RefreshCw, FileText } from 'lucide-react'

// Rendu du CV harmonisé (format Groupement-IT) en HTML autonome — utilisé pour
// l'aperçu ET pour l'impression (Enregistrer en PDF). Anonymisé : pas de nom.
function cvToHtml(cv, lang) {
  const esc = (s) => String(s ?? '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
  const bullets = (arr) => (arr || []).map(x => `<li>${esc(x)}</li>`).join('')
  const T = lang === 'en'
    ? { synth: 'SKILLS SUMMARY', exp: 'EXPERIENCE', comp: 'SKILLS', metier: 'BUSINESS', fonc: 'FUNCTIONAL', soft: 'SOFT SKILLS', tech: 'TECHNICAL', lang: 'LANGUAGES', form: 'EDUCATION', env: 'Technical environment', ctx: 'Context' }
    : { synth: 'SYNTHÈSE DES COMPÉTENCES', exp: 'EXPÉRIENCES', comp: 'COMPÉTENCES', metier: 'MÉTIER', fonc: 'FONCTIONNELLES', soft: 'SOFT SKILLS', tech: 'TECHNIQUES', lang: 'LANGUES', form: 'FORMATION', env: 'Environnement technique', ctx: 'Contexte' }
  const c = cv.competences || {}
  const exp = (cv.experiences || []).map(e => `
    <div class="exp">
      <div class="exp-h">${esc(e.company)}${e.role ? ' — ' + esc(e.role) : ''}</div>
      ${e.period ? `<div class="period">${esc(e.period)}</div>` : ''}
      ${e.context ? `<div class="ctx"><em>${esc(T.ctx)} : ${esc(e.context)}</em></div>` : ''}
      ${e.missions?.length ? `<ul>${bullets(e.missions)}</ul>` : ''}
      ${e.environment ? `<div class="env"><strong>${esc(T.env)} :</strong> ${esc(e.environment)}</div>` : ''}
    </div>`).join('')
  const compBlock = (label, arr) => arr?.length ? `<div class="cg"><div class="cg-h">${esc(label)}</div><ul>${bullets(arr)}</ul></div>` : ''
  return `
  <div class="cv">
    <div class="title">${esc(cv.title || 'CV')}</div>
    ${cv.synthese?.length ? `<div class="sec"><div class="sec-h">${esc(T.synth)}</div><ul>${bullets(cv.synthese)}</ul></div>` : ''}
    ${exp ? `<div class="sec"><div class="sec-h">${esc(T.exp)}</div>${exp}</div>` : ''}
    <div class="sec"><div class="sec-h">${esc(T.comp)}</div>
      ${compBlock(T.metier, c.metier)}${compBlock(T.fonc, c.fonctionnelles)}${compBlock(T.soft, c.soft_skills)}${compBlock(T.tech, c.techniques)}
    </div>
    ${cv.langues?.length ? `<div class="sec"><div class="sec-h">${esc(T.lang)}</div><ul>${bullets(cv.langues)}</ul></div>` : ''}
    ${cv.formation?.length ? `<div class="sec"><div class="sec-h">${esc(T.form)}</div><ul>${bullets(cv.formation)}</ul></div>` : ''}
  </div>`
}

const PRINT_CSS = `
  * { box-sizing: border-box; }
  body { font-family: 'Segoe UI', Arial, sans-serif; color: #1d1d1f; margin: 0; padding: 32px; background: #fff; }
  .cv { max-width: 800px; margin: 0 auto; }
  .title { font-size: 20px; font-weight: 800; letter-spacing: .3px; color: #111; margin-bottom: 18px; text-transform: uppercase; }
  .sec { margin-bottom: 18px; }
  .sec-h { font-size: 13px; font-weight: 800; color: #4338ca; letter-spacing: .6px; border-bottom: 2px solid #e5e7eb; padding-bottom: 3px; margin-bottom: 8px; }
  ul { margin: 4px 0 4px 18px; padding: 0; }
  li { font-size: 13px; line-height: 1.5; margin-bottom: 2px; }
  .exp { margin-bottom: 12px; }
  .exp-h { font-size: 14px; font-weight: 700; }
  .period { font-size: 12px; color: #6b7280; margin-bottom: 2px; }
  .ctx { font-size: 12.5px; color: #374151; margin-bottom: 3px; }
  .env { font-size: 12px; color: #374151; margin-top: 3px; }
  .cg { margin-bottom: 6px; }
  .cg-h { font-size: 12px; font-weight: 700; color: #111; }
`

export default function HarmonizedCvModal({ submissionId, consultantId, name, onClose }) {
  const { user } = useAuth()
  // Langue initiale = préférence de l'utilisateur (fallback 'fr').
  const [lang, setLang] = useState(user?.preferred_language === 'en' ? 'en' : 'fr')
  const [cv, setCv] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async (l) => {
    setLoading(true); setError(''); setCv(null)
    try {
      const { data } = await api.post('/cv/harmonize', { submission_id: submissionId, consultant_id: consultantId, lang: l })
      setCv(data.cv)
    } catch (e) {
      setError(e.response?.data?.detail || 'Échec de la génération')
    } finally {
      setLoading(false)
    }
  }, [submissionId, consultantId])

  useEffect(() => { load(lang) }, [lang, load])

  const print = () => {
    if (!cv) return
    const w = window.open('', '_blank')
    if (!w) return
    w.document.write(`<!doctype html><html lang="${lang}"><head><meta charset="utf-8"><title>CV Groupement-IT</title><style>${PRINT_CSS}</style></head><body>${cvToHtml(cv, lang)}<script>window.onload=()=>setTimeout(()=>window.print(),300)<\/script></body></html>`)
    w.document.close()
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
      <div className="card p-0 w-full max-w-3xl max-h-[92vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 p-4 border-b" style={{ borderColor: 'var(--border)' }}>
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <FileText size={15} className="text-brand-400" /> CV au format Groupement-IT{name ? ` — ${name}` : ''}
          </h2>
          <div className="flex items-center gap-2">
            <div className="flex bg-white/5 rounded-lg p-0.5">
              {['fr', 'en'].map(l => (
                <button key={l} onClick={() => setLang(l)}
                  className={`px-2.5 py-1 text-xs rounded-md font-medium ${lang === l ? 'seg-active' : 'text-slate-400 hover:text-slate-200'}`}>
                  {l.toUpperCase()}
                </button>
              ))}
            </div>
            <button onClick={() => { trackAiRegenerate('harmonize', 'cv/harmonize'); load(lang) }} disabled={loading} className="btn-ghost p-2" title="Régénérer">
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            </button>
            <button onClick={print} disabled={!cv} className="btn-primary text-xs"><Printer size={13} /> PDF</button>
            <button onClick={onClose} className="btn-ghost p-2"><X size={15} /></button>
          </div>
        </div>

        <div className="overflow-y-auto p-5" style={{ background: '#fff' }}>
          {loading ? (
            <div className="py-16 text-center text-slate-500 text-sm flex items-center justify-center gap-2">
              <Loader2 size={16} className="animate-spin" /> Génération du CV {lang.toUpperCase()}…
            </div>
          ) : error ? (
            <div className="py-10 text-center">
              <p className="text-sm text-red-500">{error}</p>
              <button onClick={() => load(lang)} className="btn-ghost mt-3 text-xs mx-auto">Réessayer</button>
            </div>
          ) : cv ? (
            <div dangerouslySetInnerHTML={{ __html: `<style>${PRINT_CSS}</style>${cvToHtml(cv, lang)}` }} />
          ) : null}
        </div>

        {cv && !loading && (
          <div className="flex justify-end px-5 py-2.5 border-t" style={{ borderColor: 'var(--border)' }}>
            <AiFeedback operation="harmonize" route="cv/harmonize" label="Ce CV harmonisé vous convient ?" />
          </div>
        )}
      </div>
    </div>
  )
}
