import { Routes, Route, Navigate } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ConfirmProvider } from './contexts/ConfirmContext'
import ErrorBoundary from './components/ErrorBoundary'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import DashboardPage from './pages/DashboardPage'
import TasksPage from './pages/TasksPage'
import ConsultantsPage from './pages/ConsultantsPage'
import ConsultantDetailPage from './pages/ConsultantDetailPage'
import AOSPage from './pages/AOSPage'
import AODetailPage from './pages/AODetailPage'
import NewConsultantPage from './pages/NewConsultantPage'
import NewAOPage from './pages/NewAOPage'
import NewClientPage from './pages/NewClientPage'
import ClientsPage from './pages/ClientsPage'
import ClientDetailPage from './pages/ClientDetailPage'
import PartnersAccessHub from './pages/PartnersAccessHub'
import PartnersPage from './pages/PartnersPage'
import PartnerDetailPage from './pages/PartnerDetailPage'
import CookieBanner from './components/CookieBanner'
import ContactPage from './pages/ContactPage'
import ClientReviewPage from './pages/ClientReviewPage'
import { MentionsLegales, Confidentialite, CGU } from './pages/LegalPages'

// Auto-récupération des « chunks » lazy : après un nouveau déploiement, un onglet
// déjà ouvert référence des fichiers hashés qui n'existent plus → l'import échoue
// et la page devient blanche. On recharge alors UNE fois (garde sessionStorage
// anti-boucle) pour récupérer la version fraîche ; en cas d'échec réel (hors-ligne,
// 500 persistant), on laisse l'ErrorBoundary afficher un message + bouton Recharger.
const CHUNK_RELOAD_KEY = 'chunk-reload-once'
function lazyWithReload(factory) {
  return lazy(() =>
    factory()
      .then((mod) => {
        try { sessionStorage.removeItem(CHUNK_RELOAD_KEY) } catch { /* ignore */ }
        return mod
      })
      .catch((err) => {
        let already = false
        try { already = sessionStorage.getItem(CHUNK_RELOAD_KEY) === '1' } catch { /* ignore */ }
        if (!already) {
          try { sessionStorage.setItem(CHUNK_RELOAD_KEY, '1') } catch { /* ignore */ }
          window.location.reload()
          return new Promise(() => {})   // ne rend rien avant le rechargement
        }
        throw err                        // échec réel → ErrorBoundary
      })
  )
}

// Lazy — keeps the graph library out of the main bundle
const GraphPage = lazyWithReload(() => import('./pages/GraphPage'))
const CartePage = lazyWithReload(() => import('./pages/CartePage'))
const AdminPage = lazyWithReload(() => import('./pages/AdminPage'))
const SupervisionPage = lazyWithReload(() => import('./pages/SupervisionPage'))
const TicketsPage = lazyWithReload(() => import('./pages/TicketsPage'))
const ScoringSettingsPage = lazyWithReload(() => import('./pages/ScoringSettingsPage'))
const EmailsPage = lazyWithReload(() => import('./pages/EmailsPage'))

// roles: array of allowed roles; omitted = any authenticated user.
function ProtectedRoute({ children, roles = null }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  if (roles && !roles.includes(user.role)) return <Navigate to="/dashboard" replace />
  return children
}

const STAFF = ['admin', 'commerce']
const ADMIN = ['admin']

function GuestRoute({ children }) {
  const { user } = useAuth()
  // Exception : un lien d'invitation (/register?invite=…) ouvert alors qu'une
  // session est active doit s'AFFICHER (RegisterPage propose de se déconnecter)
  // au lieu de rediriger silencieusement vers le dashboard.
  const hasInvite = typeof window !== 'undefined'
    && window.location.pathname.startsWith('/register')
    && new URLSearchParams(window.location.search).has('invite')
  if (user && !hasInvite) return <Navigate to="/dashboard" replace />
  return children
}

export default function App() {
  return (
    <AuthProvider>
      <ConfirmProvider>
        <ErrorBoundary>
        <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/login" element={<GuestRoute><LoginPage /></GuestRoute>} />
        <Route path="/register" element={<GuestRoute><RegisterPage /></GuestRoute>} />
        <Route path="/forgot-password" element={<GuestRoute><ForgotPasswordPage /></GuestRoute>} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />

        {/* Contact public — futurs partenaires (accessible avant connexion) */}
        <Route path="/contact" element={<ContactPage />} />

        {/* Pages légales — publiques (lisibles avant connexion) */}
        <Route path="/legal/mentions" element={<MentionsLegales />} />
        <Route path="/legal/confidentialite" element={<Confidentialite />} />
        <Route path="/legal/cgu" element={<CGU />} />

        {/* Retour client — page PUBLIQUE (lien tokenisé, sans auth ni Layout) */}
        <Route path="/client-review/:token" element={<ClientReviewPage />} />

        <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/a-traiter" element={<ProtectedRoute roles={STAFF}><TasksPage /></ProtectedRoute>} />
          <Route path="/clients" element={<ClientsPage />} />
          <Route path="/clients/:id" element={<ClientDetailPage />} />
          <Route path="/consultants" element={<ConsultantsPage />} />
          <Route path="/consultants/new" element={<NewConsultantPage />} />
          <Route path="/consultants/:id" element={<ConsultantDetailPage />} />
          <Route path="/aos" element={<AOSPage />} />
          <Route path="/aos/new" element={<ProtectedRoute roles={STAFF}><NewAOPage /></ProtectedRoute>} />
          <Route path="/clients/new" element={<ProtectedRoute roles={ADMIN}><NewClientPage /></ProtectedRoute>} />
          <Route path="/aos/:id" element={<AODetailPage />} />
          <Route path="/partners" element={<ProtectedRoute roles={STAFF}><PartnersPage /></ProtectedRoute>} />
          <Route path="/partners/:id" element={<ProtectedRoute roles={STAFF}><PartnerDetailPage /></ProtectedRoute>} />
          <Route path="/partners-access" element={<ProtectedRoute roles={STAFF}><PartnersAccessHub /></ProtectedRoute>} />
          <Route path="/graph" element={
            <ProtectedRoute roles={ADMIN}>
              <Suspense fallback={<div className="p-10 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement de la cartographie…</div>}>
                <GraphPage />
              </Suspense>
            </ProtectedRoute>
          } />
          <Route path="/carte" element={
            <ProtectedRoute roles={STAFF}>
              <Suspense fallback={<div className="p-10 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement de la carte…</div>}>
                <CartePage />
              </Suspense>
            </ProtectedRoute>
          } />
          {/* Ancien lien PACs → onglet Modèles de la page Habilitations */}
          <Route path="/pacs" element={<Navigate to="/partners-access?tab=pacs" replace />} />
          <Route path="/emails" element={
            <ProtectedRoute roles={STAFF}>
              <Suspense fallback={<div className="p-10 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement…</div>}>
                <EmailsPage />
              </Suspense>
            </ProtectedRoute>
          } />
          {/* Anciennes routes -> page Emails unifiée (préserve favoris & liens) */}
          <Route path="/notifications" element={<Navigate to="/emails?tab=journal" replace />} />
          <Route path="/admin/scoring" element={
            <ProtectedRoute roles={ADMIN}>
              <Suspense fallback={<div className="p-10 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement…</div>}>
                <ScoringSettingsPage />
              </Suspense>
            </ProtectedRoute>
          } />
          <Route path="/admin/email-templates" element={<Navigate to="/emails?tab=modeles" replace />} />
          <Route path="/admin" element={
            <ProtectedRoute roles={ADMIN}>
              <Suspense fallback={<div className="p-10 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement…</div>}>
                <AdminPage />
              </Suspense>
            </ProtectedRoute>
          } />
          <Route path="/supervision" element={
            <ProtectedRoute roles={ADMIN}>
              <Suspense fallback={<div className="p-10 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement…</div>}>
                <SupervisionPage />
              </Suspense>
            </ProtectedRoute>
          } />
          <Route path="/tickets" element={
            <ProtectedRoute roles={ADMIN}>
              <Suspense fallback={<div className="p-10 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement…</div>}>
                <TicketsPage />
              </Suspense>
            </ProtectedRoute>
          } />
        </Route>
        </Routes>
        </ErrorBoundary>
        <CookieBanner />
      </ConfirmProvider>
    </AuthProvider>
  )
}
