
import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  // 120s — aligné sur le proxy nginx (proxy_read_timeout 120s). Un matching IA
  // peut durer plus d'une minute : on ne coupe pas la requête côté navigateur
  // avant le serveur, sinon l'utilisateur voit une erreur alors que le
  // traitement aboutit côté backend.
  timeout: 120000,
})

// Instance PUBLIQUE — sans intercepteurs : aucune attache d'Authorization et
// aucune redirection sur 401. À utiliser pour les pages non authentifiées
// (ex : /client-review/:token), afin de ne jamais fuiter le jeton du staff
// vers une route publique ni renvoyer un visiteur anonyme vers /login.
export const publicApi = axios.create({ baseURL: '/api' })

// Attach JWT token automatically
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Global error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Ne pas rebondir l'utilisateur déjà sur une page d'auth publique.
      // Ex : la page /reset-password sonde le backend avec un token de
      // récupération Supabase qui 401 légitimement — sans ce garde-fou,
      // l'utilisateur serait renvoyé vers /login avant de pouvoir changer
      // son mot de passe.
      const authPaths = ['/login', '/reset-password', '/forgot-password', '/register', '/client-review']
      const onAuthPage = authPaths.some((p) => window.location.pathname.startsWith(p))
      if (!onAuthPage) {
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        localStorage.removeItem('session_expires')
        // Session expirée / jeton invalide : on l'indique à la page de login
        // pour afficher un message plutôt qu'un atterrissage brutal.
        window.location.href = '/login?reason=expired'
      }
    }
    return Promise.reject(error)
  }
)

export default api
