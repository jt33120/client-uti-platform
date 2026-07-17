import { useState } from 'react'
import { ThumbsUp, ThumbsDown } from 'lucide-react'
import { trackAiFeedback } from '../lib/rum'

// Avis 👍/👎 discret sur une sortie IA. Émet UN event MIP (ai_feedback) par clic,
// corrélé à la fonction via operation + route (DOIVENT matcher les appels backend
// rum_ai). Purement télémétrie : aucun état serveur, aucune donnée personnelle —
// juste le signal de satisfaction (alimente thumbs_down_rate dans la Supervision IA).
export default function AiFeedback({
  operation,
  route,
  label = 'Cette proposition IA vous aide ?',
  className = '',
}) {
  const [sent, setSent] = useState(null) // 'up' | 'down' | null

  const click = (thumb) => {
    if (sent) return // un seul avis par sortie affichée
    setSent(thumb)
    trackAiFeedback(operation, route, thumb)
  }

  if (sent) {
    return (
      <div className={`inline-flex items-center gap-1.5 text-[11px] ${className}`} style={{ color: 'var(--text-faint)' }}>
        {sent === 'up' ? <ThumbsUp size={12} /> : <ThumbsDown size={12} />} Merci pour votre retour.
      </div>
    )
  }

  return (
    <div className={`inline-flex items-center gap-1.5 text-[11px] ${className}`} style={{ color: 'var(--text-faint)' }}>
      {label && <span>{label}</span>}
      <button type="button" onClick={() => click('up')} title="Utile" aria-label="Proposition utile"
        className="btn-ghost !h-6 !w-6 !p-0 inline-flex items-center justify-center hover:text-emerald-400">
        <ThumbsUp size={13} />
      </button>
      <button type="button" onClick={() => click('down')} title="Peu utile" aria-label="Proposition peu utile"
        className="btn-ghost !h-6 !w-6 !p-0 inline-flex items-center justify-center hover:text-red-400">
        <ThumbsDown size={13} />
      </button>
    </div>
  )
}
