import { useState, useEffect, useLayoutEffect, useCallback, useMemo } from 'react'
import { X, ArrowRight, ArrowLeft, Sparkles } from 'lucide-react'

const TW = 320 // tooltip width
const TH = 190 // approx tooltip height (used only for placement clamping)
const PAD = 6  // spotlight padding around the target

/**
 * Lightweight, dependency-free guided tour.
 * - Highlights elements found via `step.selector` (a [data-tour="…"] attribute).
 * - Steps whose target is absent are skipped automatically.
 * - "Passer" / Échap dismiss it; the caller persists completion so it won't
 *   reappear on every login.
 */
export default function OnboardingTour({ steps, onClose }) {
  // Keep only steps whose target currently exists in the DOM.
  const liveSteps = useMemo(
    () => steps.filter(s => !s.selector || document.querySelector(s.selector)),
    [steps]
  )
  const [i, setI] = useState(0)
  const [rect, setRect] = useState(null)

  const step = liveSteps[i]
  const last = i >= liveSteps.length - 1

  // Ne fait QUE relire la position (aucun scroll) — sûr à brancher sur scroll/resize.
  const updateRect = useCallback(() => {
    if (!step?.selector) { setRect(null); return }
    const el = document.querySelector(step.selector)
    setRect(el ? el.getBoundingClientRect() : null)
  }, [step])

  // Amène la cible au centre UNE SEULE FOIS par étape. Le surlignage suit ensuite
  // le défilement via updateRect (sur l'écouteur scroll) — sans relancer de scroll,
  // ce qui évitait la boucle scroll→scrollIntoView→scroll (effet de « vibration »).
  useLayoutEffect(() => {
    if (!step?.selector) { setRect(null); return }
    const el = document.querySelector(step.selector)
    if (!el) { setRect(null); return }
    // 'center' (et non 'nearest') : la cible et sa bulle restent dans la fenêtre,
    // même pour un élément bas de page (sinon on devait scroller à la main).
    el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    updateRect()
  }, [step, updateRect])

  useEffect(() => {
    updateRect()
    window.addEventListener('resize', updateRect)
    window.addEventListener('scroll', updateRect, true)
    return () => {
      window.removeEventListener('resize', updateRect)
      window.removeEventListener('scroll', updateRect, true)
    }
  }, [updateRect])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight' || e.key === 'Enter') next()
      else if (e.key === 'ArrowLeft') setI(p => Math.max(0, p - 1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [i, liveSteps.length])

  if (!step) { return null }

  const next = () => { if (last) onClose(); else setI(p => p + 1) }
  const prev = () => setI(p => Math.max(0, p - 1))

  // Tooltip placement: prefer to the right of left-rail targets, else below, else above.
  let tip
  if (!rect) {
    tip = { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }
  } else if (rect.left < 300 && window.innerWidth - rect.right > TW + 28) {
    tip = { left: rect.right + 14, top: clamp(rect.top, 12, window.innerHeight - TH - 12) }
  } else if (rect.bottom + TH + 20 < window.innerHeight) {
    tip = { left: clamp(rect.left, 12, window.innerWidth - TW - 12), top: rect.bottom + 14 }
  } else {
    tip = { left: clamp(rect.left, 12, window.innerWidth - TW - 12), top: Math.max(12, rect.top - TH - 14) }
  }

  return (
    <>
      {/* Click catcher — blocks interaction with the page during the tour */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 88 }} />

      {/* Spotlight (or full dim when no target) */}
      {rect ? (
        <div
          style={{
            position: 'fixed',
            top: rect.top - PAD, left: rect.left - PAD,
            width: rect.width + PAD * 2, height: rect.height + PAD * 2,
            borderRadius: 10,
            boxShadow: '0 0 0 9999px rgba(0,0,0,0.55)',
            outline: '2px solid var(--accent)',
            outlineOffset: 2,
            zIndex: 90, pointerEvents: 'none',
            transition: 'top .2s ease, left .2s ease, width .2s ease, height .2s ease',
          }}
        />
      ) : (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 90 }} />
      )}

      {/* Tooltip card */}
      <div
        className="fixed rounded-xl overflow-hidden"
        style={{
          width: TW, ...tip, zIndex: 95,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          boxShadow: '0 12px 32px rgba(0,0,0,0.28)',
        }}
      >
        <div className="px-4 pt-4 pb-3">
          <div className="flex items-start gap-2.5">
            <div
              className="w-7 h-7 rounded-md flex items-center justify-center shrink-0 mt-0.5"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
            >
              <Sparkles size={15} strokeWidth={2} />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-[14px] font-semibold text-[var(--text)] leading-snug">{step.title}</h3>
            </div>
            <button
              onClick={onClose}
              aria-label="Fermer"
              className="p-1 -mr-1 rounded text-[var(--text-faint)] hover:text-[var(--text)] hover:bg-[var(--surface-2)] transition-colors shrink-0"
            >
              <X size={15} strokeWidth={2} />
            </button>
          </div>
          <p className="text-[13px] text-[var(--text-muted)] leading-relaxed mt-2">{step.text}</p>
        </div>

        <div
          className="flex items-center justify-between gap-2 px-4 py-2.5"
          style={{ borderTop: '1px solid var(--border)' }}
        >
          <div className="flex items-center gap-1.5">
            {liveSteps.map((_, idx) => (
              <span
                key={idx}
                className="rounded-full transition-all"
                style={{
                  width: idx === i ? 16 : 6, height: 6,
                  background: idx === i ? 'var(--accent)' : 'var(--border-strong)',
                }}
              />
            ))}
          </div>
          <div className="flex items-center gap-1.5">
            <button onClick={onClose} className="btn-ghost h-8 px-2.5 text-[12px]">Passer</button>
            {i > 0 && (
              <button onClick={prev} className="btn-ghost h-8 px-2.5 text-[12px]">
                <ArrowLeft size={13} />
              </button>
            )}
            <button onClick={next} className="btn-primary h-8 px-3 text-[12px]">
              {last ? "C'est parti" : 'Suivant'}
              {!last && <ArrowRight size={13} />}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

function clamp(v, lo, hi) { return Math.min(Math.max(v, lo), hi) }
