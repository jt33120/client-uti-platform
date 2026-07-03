import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import api from '../lib/api'

const AuthContext = createContext(null)

// Déconnexion automatique après 3 h d'utilisation (durée absolue depuis la
// connexion). Le backend applique la même limite via l'expiration du jeton ;
// ce minuteur garantit la coupure même si l'utilisateur reste inactif.
const SESSION_MAX_MS = 3 * 60 * 60 * 1000

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try {
      const stored = localStorage.getItem('user')
      return stored ? JSON.parse(stored) : null
    } catch {
      return null
    }
  })
  const [loading, setLoading] = useState(false)

  const startSession = (data) => {
    localStorage.setItem('token', data.token)
    localStorage.setItem('user', JSON.stringify(data.user))
    localStorage.setItem('session_expires', String(Date.now() + SESSION_MAX_MS))
    setUser(data.user)
  }

  // Renvoie soit { mfa: 'verify'|'enroll', ... } (second facteur requis), soit
  // { mfa: null, user } quand la session est ouverte directement.
  const login = async (email, password) => {
    setLoading(true)
    try {
      const { data } = await api.post('/auth/login', { email, password })
      if (data.mfa) {
        return { mfa: data.mfa, challenge: data.challenge_token, qr: data.qr, secret: data.secret }
      }
      startSession(data)
      return { mfa: null, user: data.user }
    } finally {
      setLoading(false)
    }
  }

  // Étape 2 — valide le code TOTP (compte enrôlé) puis ouvre la session.
  const verifyMfa = async (challenge, code) => {
    const { data } = await api.post('/auth/mfa/verify', { challenge_token: challenge, code })
    startSession(data)
    return data.user
  }

  // Premier enrôlement — confirme le QR scanné puis ouvre la session.
  const enrollMfa = async (challenge, code) => {
    const { data } = await api.post('/auth/mfa/enroll', { challenge_token: challenge, code })
    startSession(data)
    return data.user
  }

  // L'inscription NE connecte PLUS directement : le backend ne renvoie pas de
  // jeton (sinon la MFA obligatoire serait contournée). On renvoie l'accusé
  // { registered, email } ; l'appelant redirige vers /login.
  const register = async (email, password, name, role, inviteToken = null) => {
    setLoading(true)
    try {
      const { data } = await api.post('/auth/register', { email, password, name, role, invite_token: inviteToken || undefined })
      return data
    } finally {
      setLoading(false)
    }
  }

  const logout = useCallback(() => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    localStorage.removeItem('session_expires')
    setUser(null)
  }, [])

  // Coupure automatique à l'échéance des 3 h (vérif au montage, périodique et au
  // retour de focus, car un onglet en arrière-plan peut suspendre les timers).
  useEffect(() => {
    if (!user) return
    let expires = parseInt(localStorage.getItem('session_expires') || '', 10)
    if (!expires || Number.isNaN(expires)) {
      expires = Date.now() + SESSION_MAX_MS
      localStorage.setItem('session_expires', String(expires))
    }
    const check = () => {
      const exp = parseInt(localStorage.getItem('session_expires') || '0', 10)
      if (Date.now() >= exp) {
        logout()
        if (!window.location.pathname.startsWith('/login')) window.location.href = '/login?reason=expired'
      }
    }
    check()
    const iv = setInterval(check, 30000)
    window.addEventListener('focus', check)
    return () => { clearInterval(iv); window.removeEventListener('focus', check) }
  }, [user, logout])

  const updateProfile = async (data) => {
    const { data: updated } = await api.patch('/auth/me', data)
    const newUser = { ...user, ...updated }
    localStorage.setItem('user', JSON.stringify(newUser))
    setUser(newUser)
    return newUser
  }

  const uploadAvatar = async (file) => {
    const form = new FormData()
    form.append('file', file)
    const { data } = await api.post('/auth/me/avatar', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    const newUser = { ...user, avatar_url: data.avatar_url }
    localStorage.setItem('user', JSON.stringify(newUser))
    setUser(newUser)
    return data.avatar_url
  }

  const deleteAvatar = async () => {
    await api.delete('/auth/me/avatar')
    const newUser = { ...user, avatar_url: null }
    localStorage.setItem('user', JSON.stringify(newUser))
    setUser(newUser)
  }

  const isAdmin = user?.role === 'admin'
  const isCommerce = user?.role === 'commerce'
  const isStaff = isAdmin || isCommerce // équipe UTI (admin + commercial)
  const isAO = user?.role === 'ao'

  return (
    <AuthContext.Provider value={{ user, loading, login, verifyMfa, enrollMfa, register, logout, isAdmin, isCommerce, isStaff, isAO, updateProfile, uploadAvatar, deleteAvatar }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
