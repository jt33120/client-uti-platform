import { useState, useEffect } from 'react'
import api from '../lib/api'
import { Bell, Loader2 } from 'lucide-react'

function Toggle({ checked, onChange, label, hint }) {
  return (
    <label className="flex items-start justify-between gap-3 cursor-pointer">
      <span>
        <span className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>{label}</span>
        {hint && <span className="block text-[11px]" style={{ color: 'var(--text-faint)' }}>{hint}</span>}
      </span>
      <button type="button" onClick={() => onChange(!checked)}
        className="shrink-0 mt-0.5 w-9 h-5 rounded-full transition-colors relative"
        style={{ background: checked ? 'var(--accent-text)' : 'var(--surface-2)' }}>
        <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all"
          style={{ left: checked ? '18px' : '2px' }} />
      </button>
    </label>
  )
}

function NumberField({ label, value, onChange, min = 0, suffix }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>{label}</span>
      <span className="flex items-center gap-1.5">
        <input type="number" min={min} value={value}
          onChange={e => onChange(e.target.value === '' ? '' : Math.max(min, parseInt(e.target.value) || 0))}
          className="input w-20 text-right" />
        {suffix && <span className="text-[12px]" style={{ color: 'var(--text-faint)' }}>{suffix}</span>}
      </span>
    </div>
  )
}

// Réglages des notifications partenaires + relances (admin). Autonome :
// charge et enregistre son propre état.
export default function NotificationSettings() {
  const [cfg, setCfg] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState('')

  const load = () => {
    setCfg(null)
    api.get('/admin/settings').then(r => setCfg(r.data.notifications)).catch(() => setCfg(false))
  }
  useEffect(load, [])

  // cfg === false = échec de chargement : on l'AFFICHE (avant : return null →
  // onglet totalement blanc, sans explication).
  if (cfg === false) {
    return (
      <div className="card p-4 max-w-xl text-[13px]" style={{ color: 'var(--text-muted)' }}>
        Impossible de charger les réglages d'envoi.
        <button onClick={load} className="btn-ghost !h-7 !px-2.5 text-[12px] ml-2">Réessayer</button>
      </div>
    )
  }
  if (cfg === null) {
    return <div className="text-[13px] py-6" style={{ color: 'var(--text-faint)' }}>Chargement des réglages…</div>
  }
  const upd = (k, v) => { setCfg(p => ({ ...p, [k]: v })); setSaved(false) }
  const save = async () => {
    setSaving(true); setErr('')
    try {
      const { data } = await api.put('/admin/settings/notifications', {
        ...cfg,
        list2_delay_days: cfg.list2_delay_days === '' ? 0 : cfg.list2_delay_days,
        relance_interval_days: cfg.relance_interval_days === '' ? 1 : cfg.relance_interval_days,
        relance_max: cfg.relance_max === '' ? 0 : cfg.relance_max,
      })
      setCfg(data.notifications); setSaved(true)
    } catch (e) {
      setErr(e.response?.data?.detail || "Erreur lors de l'enregistrement")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h2 className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
        <Bell size={13} strokeWidth={2} /> Réglages des notifications & relances
      </h2>
      <div className="card p-4 space-y-4 max-w-xl">
        <Toggle label="Notifications activées" hint="Envoi des emails aux partenaires (liste 1 / liste 2) et relances."
          checked={cfg.enabled} onChange={v => upd('enabled', v)} />
        <div className="h-px" style={{ background: 'var(--border)' }} />
        <NumberField label="Délai liste 1 → liste 2" suffix="jours"
          value={cfg.list2_delay_days} onChange={v => upd('list2_delay_days', v)} />
        <div className="h-px" style={{ background: 'var(--border)' }} />
        <Toggle label="Relance automatique" hint="Relance les partenaires sans réponse à la fréquence choisie."
          checked={cfg.relance_auto_enabled} onChange={v => upd('relance_auto_enabled', v)} />
        <NumberField label="Fréquence des relances" suffix="jours" min={1}
          value={cfg.relance_interval_days} onChange={v => upd('relance_interval_days', v)} />
        <NumberField label="Nombre maximum de relances" suffix="relances"
          value={cfg.relance_max} onChange={v => upd('relance_max', v)} />
        {err && <p className="text-[12px]" style={{ color: 'var(--danger)' }}>{err}</p>}
        <div className="flex items-center justify-end gap-3">
          {saved && <span className="text-[12px]" style={{ color: 'var(--success)' }}>Enregistré ✓</span>}
          <button onClick={save} disabled={saving} className="btn-primary">
            {saving ? <><Loader2 size={14} className="animate-spin" /> Enregistrement…</> : 'Enregistrer'}
          </button>
        </div>
      </div>
    </div>
  )
}
