import { useState, useRef, useEffect } from 'react'
import {
  Camera, Trash2, X, Loader2, Check, AlertCircle,
  ShieldCheck, ShieldAlert, Phone, Globe, Bell, Clock, KeyRound,
} from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import api from '../lib/api'

const ROLE_LABEL = { admin: 'Administrateur', commerce: 'Commercial', ao: 'Partenaire' }
const ORG_LABEL = { 'groupement-it': 'Groupement-IT', uti: 'UTI' }

const fmtDateTime = (iso) => {
  if (!iso) return null
  try {
    return new Intl.DateTimeFormat('fr-FR', {
      day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    }).format(new Date(iso))
  } catch { return null }
}
const fmtMonth = (iso) => {
  if (!iso) return null
  try {
    return new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric' }).format(new Date(iso))
  } catch { return null }
}

export default function SettingsModal({ onClose }) {
  const { user, updateProfile, uploadAvatar, deleteAvatar, startMfa, confirmMfa, disableMfa } = useAuth()

  // Profil complet (chargé via /auth/me) — porte les champs non présents dans le
  // `user` du contexte (dernière connexion, 2FA, fonction, téléphone, prefs…).
  const [profile, setProfile] = useState(null)

  const [name, setName] = useState(user?.name || '')
  const [email, setEmail] = useState(user?.email || '')
  const [title, setTitle] = useState('')
  const [phone, setPhone] = useState('')
  const [language, setLanguage] = useState('fr')
  const [notifDeadline, setNotifDeadline] = useState(true)
  const [notifMissing, setNotifMissing] = useState(true)

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  const [avatarPreview, setAvatarPreview] = useState(user?.avatar_url || null)
  const [pendingFile, setPendingFile] = useState(null)
  const [removingAvatar, setRemovingAvatar] = useState(false)

  // 2FA self-service
  const [mfaEnabled, setMfaEnabled] = useState(false)
  const [mfaRequired, setMfaRequired] = useState(true)
  const [mfaFlow, setMfaFlow] = useState(null) // null | 'enroll' | 'disable'
  const [mfaQr, setMfaQr] = useState(null)
  const [mfaSecret, setMfaSecret] = useState('')
  const [mfaChallenge, setMfaChallenge] = useState('')
  const [mfaCode, setMfaCode] = useState('')
  const [mfaPassword, setMfaPassword] = useState('')
  const [mfaBusy, setMfaBusy] = useState(false)
  const [mfaError, setMfaError] = useState('')

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const fileInputRef = useRef(null)

  useEffect(() => {
    const handleKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [onClose])

  // Hydrate depuis /auth/me au montage (dégrade en silence : les champs
  // historiques restent éditables depuis le `user` du contexte).
  useEffect(() => {
    let alive = true
    api.get('/auth/me').then(({ data }) => {
      if (!alive) return
      setProfile(data)
      setName(data.name || '')
      setEmail(data.email || '')
      setTitle(data.title || '')
      setPhone(data.phone || '')
      setLanguage(data.preferred_language || 'fr')
      setNotifDeadline(data.notif_deadline_alerts !== false)
      setNotifMissing(data.notif_missing_info !== false)
      setMfaEnabled(!!data.mfa_enabled)
      setMfaRequired(data.mfa_required !== false)
      if (data.avatar_url && !pendingFile && !removingAvatar) setAvatarPreview(data.avatar_url)
    }).catch(() => {})
    return () => { alive = false }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const emailChanged = email.trim().toLowerCase() !== (profile?.email || user?.email || '').toLowerCase()
  const passwordChanged = newPassword.length > 0
  const needsCurrentPassword = emailChanged || passwordChanged

  const role = profile?.role || user?.role
  const roleLabel = ROLE_LABEL[role] || role || '—'
  const orgLabel = profile?.org ? (ORG_LABEL[profile.org] || profile.org) : null
  const memberSince = fmtMonth(profile?.created_at)
  const lastLogin = fmtDateTime(profile?.last_login_at)

  const handleAvatarPick = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setPendingFile(file)
    setRemovingAvatar(false)
    setAvatarPreview(URL.createObjectURL(file))
  }

  const handleRemoveAvatar = () => {
    setPendingFile(null)
    setRemovingAvatar(true)
    setAvatarPreview(null)
  }

  // ── 2FA ────────────────────────────────────────────────────────────────
  const beginEnroll = async () => {
    setMfaError(''); setSuccess(''); setMfaBusy(true)
    try {
      const d = await startMfa()
      setMfaQr(d.qr); setMfaSecret(d.secret); setMfaChallenge(d.challenge_token)
      setMfaCode(''); setMfaFlow('enroll')
    } catch (e) {
      setMfaError(e?.response?.data?.detail || "Impossible de démarrer l'activation.")
    } finally { setMfaBusy(false) }
  }

  const submitEnroll = async () => {
    setMfaError(''); setMfaBusy(true)
    try {
      await confirmMfa(mfaChallenge, mfaCode)
      setMfaEnabled(true); cancelMfaFlow()
      setSuccess('Double authentification activée.')
    } catch (e) {
      setMfaError(e?.response?.data?.detail || 'Code invalide.')
    } finally { setMfaBusy(false) }
  }

  const submitDisable = async () => {
    setMfaError(''); setMfaBusy(true)
    try {
      await disableMfa(mfaPassword)
      setMfaEnabled(false); cancelMfaFlow()
      setSuccess('Double authentification désactivée.')
    } catch (e) {
      setMfaError(e?.response?.data?.detail || 'Impossible de désactiver.')
    } finally { setMfaBusy(false) }
  }

  const cancelMfaFlow = () => {
    setMfaFlow(null); setMfaError('')
    setMfaQr(null); setMfaSecret(''); setMfaChallenge(''); setMfaCode(''); setMfaPassword('')
  }

  // ── Enregistrement du profil ─────────────────────────────────────────────
  const handleSave = async () => {
    setError('')
    setSuccess('')

    if (newPassword && newPassword !== confirmPassword) {
      setError('Les nouveaux mots de passe ne correspondent pas.')
      return
    }
    if (newPassword && newPassword.length < 8) {
      setError('Le nouveau mot de passe doit contenir au moins 8 caractères.')
      return
    }
    if (needsCurrentPassword && !currentPassword) {
      setError('Mot de passe actuel requis pour changer l\'email ou le mot de passe.')
      return
    }

    setSaving(true)
    try {
      // Avatar upload/delete
      if (pendingFile) {
        await uploadAvatar(pendingFile)
      } else if (removingAvatar && (profile?.avatar_url || user?.avatar_url)) {
        await deleteAvatar()
      }

      // Champs profil — on n'envoie que ce qui a changé.
      const base = profile || user || {}
      const payload = {}
      if (name.trim() && name.trim() !== base.name) payload.name = name.trim()
      if (emailChanged) payload.email = email.trim()
      if (passwordChanged) payload.new_password = newPassword
      if (needsCurrentPassword) payload.current_password = currentPassword
      if (title.trim() !== (base.title || '')) payload.title = title.trim()
      if (phone.trim() !== (base.phone || '')) payload.phone = phone.trim()
      if (language !== (base.preferred_language || 'fr')) payload.preferred_language = language
      if (notifDeadline !== (base.notif_deadline_alerts !== false)) payload.notif_deadline_alerts = notifDeadline
      if (notifMissing !== (base.notif_missing_info !== false)) payload.notif_missing_info = notifMissing

      if (Object.keys(payload).length > 0) {
        const updated = await updateProfile(payload)
        setProfile((prev) => ({ ...(prev || {}), ...updated }))
      }

      setSuccess('Profil mis à jour avec succès.')
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setPendingFile(null)
      setRemovingAvatar(false)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Une erreur est survenue.')
    } finally {
      setSaving(false)
    }
  }

  const initial = (name || user?.name || '?').charAt(0).toUpperCase()

  const sectionHead = 'text-[12px] font-semibold uppercase tracking-wider text-[var(--text-faint)]'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.5)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="card w-full max-w-md mx-4" style={{ maxHeight: '90vh', overflowY: 'auto' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
          <h2 className="text-[15px] font-semibold text-[var(--text)]">Paramètres du profil</h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded text-[var(--text-faint)] hover:text-[var(--text)] hover:bg-[var(--surface-2)] transition-colors"
          >
            <X size={15} strokeWidth={1.75} />
          </button>
        </div>

        <div className="px-5 py-5 flex flex-col gap-5">
          {/* Avatar + identité résumée */}
          <div className="flex flex-col items-center gap-3">
            <div className="relative">
              {avatarPreview ? (
                <img
                  src={avatarPreview}
                  alt="Avatar"
                  className="w-20 h-20 rounded-full object-cover"
                  style={{ border: '2px solid var(--border)' }}
                />
              ) : (
                <div
                  className="w-20 h-20 rounded-full flex items-center justify-center text-2xl font-semibold"
                  style={{ background: 'var(--surface-2)', color: 'var(--text)' }}
                >
                  {initial}
                </div>
              )}
              <button
                onClick={() => fileInputRef.current?.click()}
                className="absolute bottom-0 right-0 w-7 h-7 rounded-full flex items-center justify-center transition-colors"
                style={{ background: 'var(--accent)', color: '#fff' }}
                title="Changer la photo"
              >
                <Camera size={13} strokeWidth={2} />
              </button>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={handleAvatarPick}
            />
            {(avatarPreview || profile?.avatar_url || user?.avatar_url) && !removingAvatar && (
              <button
                onClick={handleRemoveAvatar}
                className="flex items-center gap-1.5 text-[12px] text-[var(--danger)] hover:underline"
              >
                <Trash2 size={12} strokeWidth={1.75} />
                Supprimer la photo
              </button>
            )}
            {/* Badge rôle · organisation · ancienneté */}
            <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-[12px]">
              <span className="badge" style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>{roleLabel}</span>
              {orgLabel && <span style={{ color: 'var(--text-faint)' }}>· {orgLabel}</span>}
              {memberSince && <span style={{ color: 'var(--text-faint)' }}>· Membre depuis {memberSince}</span>}
            </div>
          </div>

          {/* Identité */}
          <div className="flex flex-col gap-3">
            <div>
              <label className="label">Nom</label>
              <input
                className="input w-full mt-1"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Votre nom"
              />
            </div>
            <div>
              <label className="label">Fonction</label>
              <input
                className="input w-full mt-1"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Ex. Responsable recrutement"
              />
            </div>
          </div>

          {/* Coordonnées */}
          <div className="flex flex-col gap-3" style={{ borderTop: '1px solid var(--border)', paddingTop: '1.25rem' }}>
            <p className={sectionHead}>Coordonnées</p>
            <div>
              <label className="label">Email</label>
              <input
                className="input w-full mt-1"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="votre@email.com"
              />
            </div>
            <div>
              <label className="label flex items-center gap-1.5"><Phone size={12} strokeWidth={1.75} /> Téléphone</label>
              <input
                className="input w-full mt-1"
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="Ex. +33 6 12 34 56 78"
              />
            </div>
          </div>

          {/* Sécurité */}
          <div className="flex flex-col gap-3" style={{ borderTop: '1px solid var(--border)', paddingTop: '1.25rem' }}>
            <p className={sectionHead}>Sécurité</p>

            {/* Dernière connexion */}
            {lastLogin && (
              <div className="flex items-start gap-2 text-[12px]" style={{ color: 'var(--text-faint)' }}>
                <Clock size={13} strokeWidth={1.75} className="mt-0.5 shrink-0" />
                <span>
                  Dernière connexion : {lastLogin}
                  {profile?.last_login_ip ? <> · IP {profile.last_login_ip}</> : null}
                </span>
              </div>
            )}

            {/* Double authentification */}
            <div className="rounded-lg p-3" style={{ background: 'var(--surface-2)' }}>
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  {mfaEnabled
                    ? <ShieldCheck size={16} strokeWidth={1.75} style={{ color: 'var(--success)' }} />
                    : <ShieldAlert size={16} strokeWidth={1.75} style={{ color: 'var(--text-faint)' }} />}
                  <div>
                    <p className="text-[13px] font-medium text-[var(--text)]">Double authentification</p>
                    <p className="text-[12px]" style={{ color: mfaEnabled ? 'var(--success)' : 'var(--text-faint)' }}>
                      {mfaEnabled ? 'Activée' : 'Désactivée'}
                      {mfaRequired && <span style={{ color: 'var(--text-faint)' }}> · obligatoire sur votre compte</span>}
                    </p>
                  </div>
                </div>
                {mfaFlow === null && (
                  mfaEnabled
                    ? (!mfaRequired && (
                        <button className="btn-ghost text-[12px] h-7 px-2.5" onClick={() => { setMfaFlow('disable'); setMfaError(''); setMfaPassword('') }}>
                          Désactiver
                        </button>
                      ))
                    : (
                        <button className="btn-primary text-[12px] h-7 px-3 flex items-center gap-1.5" onClick={beginEnroll} disabled={mfaBusy}>
                          {mfaBusy ? <Loader2 size={12} className="animate-spin" /> : null} Activer
                        </button>
                      )
                )}
              </div>

              {/* Flux d'activation : QR + secret + code */}
              {mfaFlow === 'enroll' && (
                <div className="mt-3 flex flex-col gap-2.5">
                  <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
                    Scannez ce QR code avec votre application d'authentification (Google Authenticator, Authy…), puis saisissez le code à 6 chiffres.
                  </p>
                  {mfaQr && (
                    <div className="flex items-center gap-3">
                      <img src={mfaQr} alt="QR 2FA" className="w-28 h-28 rounded bg-white p-1" />
                      <div className="min-w-0">
                        <p className="text-[11px]" style={{ color: 'var(--text-faint)' }}>Ou saisie manuelle :</p>
                        <code className="text-[11px] break-all" style={{ color: 'var(--text-muted)' }}>{mfaSecret}</code>
                      </div>
                    </div>
                  )}
                  <input
                    className="input w-full"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    value={mfaCode}
                    onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, ''))}
                    placeholder="Code à 6 chiffres"
                  />
                  <div className="flex justify-end gap-2">
                    <button className="btn-ghost text-[12px] h-7 px-2.5" onClick={cancelMfaFlow}>Annuler</button>
                    <button className="btn-primary text-[12px] h-7 px-3 flex items-center gap-1.5" onClick={submitEnroll} disabled={mfaBusy || mfaCode.length < 6}>
                      {mfaBusy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} strokeWidth={2} />} Confirmer
                    </button>
                  </div>
                </div>
              )}

              {/* Flux de désactivation : mot de passe requis */}
              {mfaFlow === 'disable' && (
                <div className="mt-3 flex flex-col gap-2.5">
                  <p className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
                    Confirmez avec votre mot de passe actuel pour désactiver la double authentification.
                  </p>
                  <input
                    className="input w-full"
                    type="password"
                    autoComplete="current-password"
                    value={mfaPassword}
                    onChange={(e) => setMfaPassword(e.target.value)}
                    placeholder="Mot de passe actuel"
                  />
                  <div className="flex justify-end gap-2">
                    <button className="btn-ghost text-[12px] h-7 px-2.5" onClick={cancelMfaFlow}>Annuler</button>
                    <button className="btn-primary text-[12px] h-7 px-3 flex items-center gap-1.5" onClick={submitDisable} disabled={mfaBusy || !mfaPassword}>
                      {mfaBusy ? <Loader2 size={12} className="animate-spin" /> : null} Désactiver
                    </button>
                  </div>
                </div>
              )}

              {mfaError && (
                <div className="mt-2 flex items-start gap-1.5 text-[12px] text-[var(--danger)]">
                  <AlertCircle size={13} strokeWidth={1.75} className="mt-0.5 shrink-0" />
                  {mfaError}
                </div>
              )}
            </div>

            {/* Mot de passe */}
            <div className="flex flex-col gap-3">
              <p className="text-[12px] font-medium flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }}>
                <KeyRound size={12} strokeWidth={1.75} /> Changer le mot de passe
              </p>
              <div>
                <label className="label">Nouveau mot de passe</label>
                <input
                  className="input w-full mt-1"
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="Laisser vide pour ne pas modifier"
                  autoComplete="new-password"
                />
              </div>
              {newPassword && (
                <div>
                  <label className="label">Confirmer le nouveau mot de passe</label>
                  <input
                    className="input w-full mt-1"
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Répétez le nouveau mot de passe"
                    autoComplete="new-password"
                  />
                </div>
              )}
              {needsCurrentPassword && (
                <div>
                  <label className="label">Mot de passe actuel <span className="text-[var(--danger)]">*</span></label>
                  <input
                    className="input w-full mt-1"
                    type="password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    placeholder="Requis pour changer l'email ou le mot de passe"
                    autoComplete="current-password"
                  />
                </div>
              )}
            </div>
          </div>

          {/* Préférences */}
          <div className="flex flex-col gap-3" style={{ borderTop: '1px solid var(--border)', paddingTop: '1.25rem' }}>
            <p className={sectionHead}>Préférences</p>
            <div>
              <label className="label flex items-center gap-1.5"><Globe size={12} strokeWidth={1.75} /> Langue par défaut</label>
              <select className="input w-full mt-1" value={language} onChange={(e) => setLanguage(e.target.value)}>
                <option value="fr">Français</option>
                <option value="en">English</option>
              </select>
              <p className="text-[11px] mt-1" style={{ color: 'var(--text-faint)' }}>
                Langue pré-sélectionnée pour les documents générés (CV harmonisé).
              </p>
            </div>
            <div>
              <label className="label flex items-center gap-1.5"><Bell size={12} strokeWidth={1.75} /> Notifications (cloche)</label>
              <div className="flex flex-col gap-2 mt-1.5">
                <label className="flex items-center gap-2 text-[13px] text-[var(--text)] cursor-pointer">
                  <input type="checkbox" checked={notifDeadline} onChange={(e) => setNotifDeadline(e.target.checked)} />
                  Alertes d'échéance <span style={{ color: 'var(--text-faint)' }}>(AO à échéance proche)</span>
                </label>
                <label className="flex items-center gap-2 text-[13px] text-[var(--text)] cursor-pointer">
                  <input type="checkbox" checked={notifMissing} onChange={(e) => setNotifMissing(e.target.checked)} />
                  Rappels d'infos manquantes
                </label>
              </div>
            </div>
          </div>

          {/* Feedback */}
          {error && (
            <div className="flex items-start gap-2 text-[13px] text-[var(--danger)]">
              <AlertCircle size={14} strokeWidth={1.75} className="mt-0.5 shrink-0" />
              {error}
            </div>
          )}
          {success && (
            <div className="flex items-start gap-2 text-[13px] text-[var(--success)]">
              <Check size={14} strokeWidth={2} className="mt-0.5 shrink-0" />
              {success}
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-1">
            <button className="btn-ghost text-[13px] h-8 px-3" onClick={onClose}>Annuler</button>
            <button
              className="btn-primary text-[13px] h-8 px-4 flex items-center gap-1.5"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? <Loader2 size={13} className="animate-spin" /> : null}
              Enregistrer
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
