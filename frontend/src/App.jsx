import { Routes, Route, Navigate } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ConfirmProvider } from './contexts/ConfirmContext'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import DashboardPage from './pages/DashboardPage'
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
import { MentionsLegales, Confidentialite, CGU } from './pages/LegalPages'

// Lazy — keeps the graph library out of the main bundle
const GraphPage = lazy(() => import('./pages/GraphPage'))
const CartePage = lazy(() => import('./pages/CartePage'))
const AdminPage = lazy(() => import('./pages/AdminPage'))
const TicketsPage = lazy(() => import('./pages/TicketsPage'))
const ScoringSettingsPage = lazy(() => import('./pages/ScoringSettingsPage'))
const EmailsPage = lazy(() => import('./pages/EmailsPage'))

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

        <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
          <Route path="/dashboard" element={<DashboardPage />} />
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
          <Route path="/tickets" element={
            <ProtectedRoute roles={ADMIN}>
              <Suspense fallback={<div className="p-10 text-center text-sm" style={{ color: 'var(--text-faint)' }}>Chargement…</div>}>
                <TicketsPage />
              </Suspense>
            </ProtectedRoute>
          } />
        </Route>
        </Routes>
        <CookieBanner />
      </ConfirmProvider>
    </AuthProvider>
  )
}
