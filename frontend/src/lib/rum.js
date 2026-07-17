// Petit pont vers le SDK MIP RUM chargé dans index.html (global `MIPRum`).
// Best-effort : si le script CDN est bloqué/indisponible, ces appels sont des
// no-op silencieux (l'app fonctionne sans télémétrie). AUCUN contenu personnel
// ne doit transiter — seulement operation/route et un signal court.
//
// track('ai_regenerate', { operation, route })   → alimente regen_rate (MIP)
// track('ai_feedback',   { operation, route, thumb: 'up'|'down' }) → thumbs_down_rate
export function trackRum(event, props) {
  try {
    if (typeof window !== 'undefined' && window.MIPRum && typeof window.MIPRum.track === 'function') {
      window.MIPRum.track(event, props || {})
    }
  } catch { /* no-op : la télémétrie ne doit jamais casser l'UI */ }
}

// Régénération d'une sortie IA (l'utilisateur relance la même fonction). Corrélé
// à la fonction par operation + route (DOIVENT matcher les appels rum_ai backend).
export function trackAiRegenerate(operation, route) {
  trackRum('ai_regenerate', { operation, route })
}

// Avis 👍/👎 sur une sortie IA. thumb ∈ 'up' | 'down'.
export function trackAiFeedback(operation, route, thumb) {
  trackRum('ai_feedback', { operation, route, thumb })
}
