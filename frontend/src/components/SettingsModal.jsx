import { useState, useRef, useEffect } from 'react'
import { Camera, Trash2, X, Loader2, Check, AlertCircle } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'

export default function SettingsModal({ onClose }) {
  const { user, updateProfile, uploadAvatar, deleteAvatar } = useAuth()

  const [name, setName] = useState(user?.name || '')
  const [email, setEmail] = useState(user?.email || '')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  const [avatarPreview, setAvatarPreview] = useState(user?.avatar_url || null)
  const [pendingFile, setPendingFile] = useState(null)
  const [removingAvatar, setRemovingAvatar] = useState(false)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const fileInputRef = useRef(null)

  useEffect(() => {
    const handleKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [onClose])

  const emailChanged = email.trim().toLowerCase() !== user?.email?.toLowerCase()
  const passwordChanged = newPassword.length > 0
  const needsCurrentPassword = emailChanged || passwordChanged

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
      } else if (removingAvatar && user?.avatar_url) {
        await deleteAvatar()
      }

      // Profile fields
      const payload = {}
      if (name.trim() && name.trim() !== user?.name) payload.name = name.trim()
      if (emailChanged) payload.email = email.trim()
      if (passwordChanged) payload.new_password = newPassword
      if (needsCurrentPassword) payload.current_password = currentPassword

      if (Object.keys(payload).length > 0) {
        await updateProfile(payload)
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

  const initial = user?.name?.charAt(0).toUpperCase()

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
          {/* Avatar */}
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
            {(avatarPreview || user?.avatar_url) && !removingAvatar && (
              <button
                onClick={handleRemoveAvatar}
                className="flex items-center gap-1.5 text-[12px] text-[var(--danger)] hover:underline"
              >
                <Trash2 size={12} strokeWidth={1.75} />
                Supprimer la photo
              </button>
            )}
          </div>

          {/* Name */}
          <div>
            <label className="label">Nom</label>
            <input
              className="input w-full mt-1"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Votre nom"
            />
          </div>

          {/* Email */}
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

          {/* Password change */}
          <div className="flex flex-col gap-3" style={{ borderTop: '1px solid var(--border)', paddingTop: '1.25rem' }}>
            <p className="text-[12px] font-semibold uppercase tracking-wider text-[var(--text-faint)]">Changer le mot de passe</p>
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
          </div>

          {/* Current password (shown when needed) */}
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
