import { Star } from 'lucide-react'

// Notation d'importance 0-5. `onChange` absent => lecture seule.
// Re-cliquer l'étoile active la décrémente ; cliquer la 1re quand elle est
// seule active met la note à 0 (critère exclu du score).
export default function StarRating({ value = 0, onChange, size = 18 }) {
  const readOnly = !onChange
  return (
    <div className="flex items-center gap-0.5" role="radiogroup" aria-label="Importance">
      {[1, 2, 3, 4, 5].map((n) => {
        const active = n <= value
        return (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={n === value}
            aria-label={`${n} étoile${n > 1 ? 's' : ''}`}
            disabled={readOnly}
            onClick={() => onChange?.(n === value ? n - 1 : n)}
            className={readOnly ? 'cursor-default' : 'cursor-pointer transition-transform hover:scale-110'}
            style={{ lineHeight: 0 }}
          >
            <Star
              size={size}
              strokeWidth={1.75}
              style={{
                color: active ? 'var(--accent-text)' : 'var(--text-faint)',
                fill: active ? 'var(--accent-text)' : 'transparent',
              }}
            />
          </button>
        )
      })}
    </div>
  )
}
