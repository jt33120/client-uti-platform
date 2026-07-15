import { useState, useEffect } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import ContactPartnerModal from '../components/ContactPartnerModal'
import {
  ArrowLeft, Loader2, Euro, MapPin, Clock, Map as MapIcon, Mail,
  UserCircle2, Briefcase, FileText, Star, CheckCircle2, Phone,
  Calendar, Download, Target, Award,
} from 'lucide-react'
import clsx from 'clsx'
import { availabilityLabel, availabilityTone, AVAILABILITY_OPTIONS } from '../lib/availability'

const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' }) : null
const employmentLabel = (t) => t === 'salarie' ? 'Salarié' : t === 'independant' ? 'Indépendant' : null

const CONTACT_LABEL = {
  contacted: { label: 'Contacté', cls: 'bg-amber-500/10 text-amber-400 border border-amber-500/20' },
  proposed: { label: 'Proposé au client', cls: 'bg-violet-500/10 text-violet-300 border border-violet-500/20' },
}

const AO_STATUS = {
  open: { label: 'Ouvert', cls: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' },
  closed: { label: 'Fermé', cls: 'bg-slate-500/10 text-slate-500 border border-slate-600/20' },
}

function Stat({ icon: Icon, value, label }) {
  return (
    <div className="card px-4 py-3 flex items-center gap-3">
      <Icon size={18} style={{ color: 'var(--accent-text)' }} />
      <div>
        <div className="text-lg font-bold leading-none" style={{ color: 'var(--text)' }}>{value}</div>
        <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-faint)' }}>{label}</div>
      </div>
    </div>
  )
}

function InfoRow({ icon: Icon, label, children }) {
  if (!children) return null
  return (
    <div className="flex items-start gap-2.5 text-sm">
      <Icon size={14} className="mt-0.5 shrink-0" style={{ color: 'var(--text-faint)' }} />
      <div className="min-w-0">
        <div className="text-[11px]" style={{ color: 'var(--text-faint)' }}>{label}</div>
        <div className="break-words" style={{ color: 'var(--text)' }}>{children}</div>
      </div>
    </div>
  )
}

// Une entrée d'historique (un AO auquel le consultant a participé / été retenu).
function HistoryEntry({ h }) {
  const status = AO_STATUS[h.ao_status]
  const contact = CONTACT_LABEL[h.contact_status]
  const score = h.score_hybride ?? h.score_total
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/aos/${h.ao_id}`} className="text-sm font-semibold hover:underline" style={{ color: 'var(--text)' }}>
            {h.ao_title}
          </Link>
          <div className="text-[11px] mt-0.5 flex items-center gap-1.5 flex-wrap" style={{ color: 'var(--text-faint)' }}>
            {h.client_name && <span>{h.client_name}</span>}
            {h.ao_reference && <span>· réf. {h.ao_reference}</span>}
            {h.submitted_at && <span>· soumis le {fmtDate(h.submitted_at)}</span>}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          {status && <span className={clsx('badge text-[10px]', status.cls)}>{status.label}</span>}
        </div>
      </div>

      <div className="flex items-center gap-1.5 flex-wrap mt-3">
        {h.retained && (
          <span className="badge text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <Award size={10} /> Retenu{h.human_rank ? ` · #${h.human_rank}` : ''}
          </span>
        )}
        {score != null && (
          <span className="badge text-[10px]" style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>
            <Target size={10} /> Score {score}{h.rank ? ` · rang ${h.rank}` : ''}
          </span>
        )}
        {contact && <span className={clsx('badge text-[10px]', contact.cls)}>{contact.label}</span>}
        {!h.submitted && !h.retained && (
          <span className="badge text-[10px] bg-white/5 text-slate-400">Recommandé (vivier)</span>
        )}
        {h.cv_url && (
          <a href={h.cv_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
             className="badge text-[10px] bg-white/5 text-slate-300 hover:text-white ml-auto">
            <Download size={10} /> CV{h.cv_filename ? ` · ${h.cv_filename}` : ''}
          </a>
        )}
      </div>
    </div>
  )
}

// Statut de disponibilité éditable en ligne (info clé). Sauvegarde immédiate
// (PATCH). Lecture seule si l'utilisateur n'a pas le droit d'éditer.
function AvailabilityEditor({ consultant, canEdit, onSaved }) {
  const [status, setStatus] = useState(consultant.availability_status || '')
  const [from, setFrom] = useState(consultant.available_from ? String(consultant.available_from).slice(0, 10) : '')
  const [saving, setSaving] = useState(false)

  const save = async (nextStatus, nextFrom) => {
    setSaving(true)
    try {
      const { data } = await api.patch(`/consultants/${consultant.id}`, {
        availability_status: nextStatus || null,
        available_from: (nextStatus === 'soon' || nextStatus === 'on_mission') ? (nextFrom || null) : null,
      })
      onSaved?.(data)
    } catch { /* best-effort : l'état local reste affiché */ }
    finally { setSaving(false) }
  }

  if (!canEdit) {
    return consultant.availability_status ? (
      <span className={clsx('text-[11px] px-2 py-0.5 rounded-full border inline-flex items-center gap-1', availabilityTone(consultant.availability_status))}>
        <Clock size={11} /> {availabilityLabel(consultant.availability_status, consultant.available_from)}
        {consultant.availability && <span className="opacity-70">· {consultant.availability}</span>}
      </span>
    ) : null
  }

  const needsDate = status === 'soon' || status === 'on_mission'
  return (
    <span className="inline-flex items-center gap-1.5">
      <select value={status} disabled={saving}
        onChange={e => { const v = e.target.value; const nf = (v === 'soon' || v === 'on_mission') ? from : ''; setStatus(v); setFrom(nf); save(v, nf) }}
        className="input h-7 py-0 text-[12px] w-auto" title="Statut de disponibilité">
        <option value="">Statut ?</option>
        {AVAILABILITY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      {needsDate && (
        <input type="date" value={from} disabled={saving}
          onChange={e => { setFrom(e.target.value); save(status, e.target.value) }}
          className="input h-7 py-0 text-[12px] w-auto" title="Échéance / date de disponibilité" />
      )}
      {saving && <Loader2 size={11} className="animate-spin" style={{ color: 'var(--text-faint)' }} />}
    </span>
  )
}

export default function ConsultantDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { isStaff, isAdmin, user } = useAuth()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [contactOpen, setContactOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.get(`/consultants/${id}/history`)
      .then(r => { if (!cancelled) setData(r.data) })
      .catch(e => { if (!cancelled) setError(e.response?.status === 403 ? "Vous n'avez pas accès à ce consultant." : (e.response?.data?.detail || 'Consultant introuvable')) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [id])

  if (loading) {
    return <div className="flex items-center justify-center h-64"><Loader2 size={22} className="animate-spin" style={{ color: 'var(--text-faint)' }} /></div>
  }
  if (error || !data) {
    return (
      <div>
        <button onClick={() => navigate('/consultants')} className="btn-ghost gap-1.5 text-sm mb-4"><ArrowLeft size={15} /> Retour au vivier</button>
        <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">{error || 'Consultant introuvable'}</div>
      </div>
    )
  }

  const c = data.consultant
  const owner = data.owner
  const stats = data.stats || {}
  const history = data.history || []
  const cvs = history.filter(h => h.cv_url)
  const ownerIsPartner = owner?.role === 'ao'
  const hasCoords = c.latitude != null && c.longitude != null
  // Édition du statut : admin, ou le propriétaire du profil (le backend restreint
  // pareil). Le commercial ne peut pas éditer un consultant qu'il ne porte pas.
  const canEditStatus = isAdmin || c.created_by === user?.id
  const mergeConsultant = (patch) => setData(d => ({ ...d, consultant: { ...d.consultant, ...patch } }))

  return (
    <div className="animate-slide-up">
      <button onClick={() => navigate('/consultants')} className="btn-ghost gap-1.5 text-sm mb-4">
        <ArrowLeft size={15} /> Retour au vivier
      </button>

      {/* En-tête */}
      <div className="card p-5 mb-4">
        <div className="flex items-start gap-4">
          <div className="w-14 h-14 rounded-full flex items-center justify-center text-xl font-bold shrink-0"
               style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>
            {c.name.charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-bold flex items-center gap-2 flex-wrap" style={{ color: 'var(--text)' }}>
              {c.name}
              {employmentLabel(c.employment_type) && (
                <span className="badge text-[10px]" style={{ background: 'var(--surface-2)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
                  {employmentLabel(c.employment_type)}
                </span>
              )}
            </h1>
            <div className="flex items-center gap-3 mt-1.5 text-sm flex-wrap" style={{ color: 'var(--text-muted)' }}>
              {c.tjm != null && <span className="flex items-center gap-1"><Euro size={12} /> {c.tjm} €/j</span>}
              {c.city && <span className="flex items-center gap-1"><MapPin size={12} /> {c.city}</span>}
              {c.experience_years != null && <span className="flex items-center gap-1"><Briefcase size={12} /> {c.experience_years} ans d'exp.</span>}
              <AvailabilityEditor consultant={c} canEdit={canEditStatus} onSaved={mergeConsultant} />
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {hasCoords && (
              <Link to={`/carte?focus=${c.id}`} className="btn-ghost gap-1.5 text-sm" title="Voir sur la carte">
                <MapIcon size={14} /> Carte
              </Link>
            )}
            {isStaff && (
              <button
                onClick={async () => {
                  try {
                    const res = await api.get(`/consultants/${c.id}/export`, { responseType: 'blob' })
                    const url = URL.createObjectURL(res.data)
                    const a = document.createElement('a')
                    a.href = url; a.download = `export-consultant-${c.id}.json`
                    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
                  } catch { alert('Export impossible') }
                }}
                className="btn-ghost gap-1.5 text-sm"
                title="Exporter toutes les données détenues sur ce consultant (RGPD, portabilité art. 20)">
                <Download size={14} /> Export RGPD
              </button>
            )}
            {isStaff && ownerIsPartner && (
              <button onClick={() => setContactOpen(true)} className="btn-primary gap-1.5 text-sm">
                <Mail size={14} /> Contacter le porteur
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Statistiques */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <Stat icon={FileText} value={stats.participations ?? 0} label="AO participés" />
        <Stat icon={Award} value={stats.retained ?? 0} label="Retenu" />
        <Stat icon={CheckCircle2} value={stats.contacted ?? 0} label="Contacté" />
        <Stat icon={Download} value={stats.cv_count ?? 0} label="CV" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Infos */}
        <div className="lg:col-span-1 space-y-4">
          <div className="card p-5 space-y-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--text-faint)' }}>Informations</h2>
            <InfoRow icon={Star} label="Compétences">
              <div className="flex flex-wrap gap-1.5 mt-1">
                {c.skills?.split(',').map(s => s.trim()).filter(Boolean).map((s, i) => (
                  <span key={i} className="badge bg-brand-600/10 text-brand-300 border border-brand-500/15 text-[10px]">{s}</span>
                ))}
              </div>
            </InfoRow>
            <InfoRow icon={Mail} label="Email">{c.email}</InfoRow>
            <InfoRow icon={Phone} label="Téléphone">{c.phone}</InfoRow>
            <InfoRow icon={CheckCircle2} label="Consentement RGPD">
              {c.consent_at
                ? <span className="text-emerald-400">Recueilli le {new Date(c.consent_at).toLocaleDateString('fr-FR')}</span>
                : <span style={{ color: 'var(--text-faint)' }}>Non renseigné</span>}
            </InfoRow>
            <InfoRow icon={UserCircle2} label="Partenaire porteur">
              {owner ? (
                ownerIsPartner
                  ? <Link to={`/partners/${owner.id}`} className="hover:underline" style={{ color: 'var(--accent-text)' }}>{owner.name}</Link>
                  : owner.name
              ) : '—'}
            </InfoRow>
            <InfoRow icon={Calendar} label="Ajouté au vivier">{fmtDate(c.created_at)}</InfoRow>
          </div>

          {/* Historique des CV */}
          {cvs.length > 0 && (
            <div className="card p-5">
              <h2 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: 'var(--text-faint)' }}>Historique des CV</h2>
              <ul className="space-y-2">
                {cvs.map((h, i) => (
                  <li key={i}>
                    <a href={h.cv_url} target="_blank" rel="noreferrer"
                       className="flex items-center gap-2 text-sm rounded-md px-2 py-1.5 hover:bg-[var(--surface-2)]" style={{ color: 'var(--text-muted)' }}>
                      <FileText size={13} className="shrink-0" />
                      <span className="truncate flex-1">{h.cv_filename || h.ao_title}</span>
                      <span className="text-[10px]" style={{ color: 'var(--text-faint)' }}>{fmtDate(h.submitted_at)}</span>
                      <Download size={12} className="shrink-0" />
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Historique des AO */}
        <div className="lg:col-span-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
            <Briefcase size={13} /> Historique des appels d'offres ({history.length})
          </h2>
          {history.length === 0 ? (
            <div className="card p-8 text-center text-sm" style={{ color: 'var(--text-faint)' }}>
              Ce consultant n'a encore participé à aucun appel d'offres.
            </div>
          ) : (
            <div className="space-y-3">
              {history.map((h) => <HistoryEntry key={h.ao_id} h={h} />)}
            </div>
          )}
        </div>
      </div>

      {contactOpen && owner && (
        <ContactPartnerModal consultant={{ ...c, owner }} onClose={() => setContactOpen(false)} />
      )}
    </div>
  )
}
