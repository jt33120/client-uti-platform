import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import api from '../lib/api'
import ErrorJournal from '../components/ErrorJournal'
import {
  Activity, Coins, Radio, RefreshCw, Cpu, TrendingUp,
} from 'lucide-react'

const TABS = [
  { k: 'logs', label: "Journal d'erreurs", icon: Activity },
  { k: 'ia', label: 'Usage & coûts IA', icon: Coins },
  { k: 'rum', label: 'RUM (activité)', icon: Radio },
]

const fmtUsd = (v) => (v == null ? '—' : `$${Number(v).toFixed(v < 1 ? 4 : 2)}`)
const fmtDay = (iso) => {
  try { return new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short' }).format(new Date(iso)) }
  catch { return iso }
}

// ── Onglet Usage & coûts IA ──────────────────────────────────────
function AiUsageTab() {
  const [data, setData] = useState(null) // null=chargement, false=erreur
  const load = () => {
    setData(null)
    api.get('/admin/ai-usage').then(r => setData(r.data)).catch(() => setData(false))
  }
  useEffect(load, [])

  if (data === null) return <div className="text-[13px] py-8" style={{ color: 'var(--text-faint)' }}>Chargement…</div>
  if (data === false) return (
    <div className="text-[13px] py-8" style={{ color: 'var(--text-muted)' }}>
      Usage IA indisponible (backend injoignable ou version antérieure).
      <button onClick={load} className="btn-ghost !h-7 !px-2.5 text-[12px] ml-2">Réessayer</button>
    </div>
  )

  const maxCost = Math.max(0.0001, ...(data.series_30d || []).map(d => d.cost))
  const models = data.models || {}

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
          Coût des appels IA (extraction CV + 2ᵉ avis de scoring). Le coût est compté <strong>par run</strong> de matching.
        </p>
        <button onClick={load} className="btn-ghost !h-7 !px-2 text-[11px]" title="Rafraîchir"><RefreshCw size={12} /></button>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-6">
        {[
          { label: 'Coût IA cumulé', value: fmtUsd(data.total_cost_usd), sub: 'depuis le début' },
          { label: 'Runs de matching', value: data.total_runs ?? '—', sub: `${data.scored_profiles ?? 0} profils scorés` },
          { label: 'Coût moyen / run', value: fmtUsd(data.avg_cost_per_run), sub: 'extraction + scoring' },
          { label: 'Sur 30 jours', value: fmtUsd((data.series_30d || []).reduce((s, d) => s + d.cost, 0)), sub: `${(data.series_30d || []).reduce((s, d) => s + d.runs, 0)} runs` },
        ].map((k, i) => (
          <div key={i} className="flex flex-col gap-1.5 lg:px-5 lg:border-l lg:first:border-l-0 lg:first:pl-0 border-[color:var(--border)]">
            <span className="text-[11px] uppercase tracking-[0.07em] font-semibold" style={{ color: 'var(--text-faint)' }}>{k.label}</span>
            <span className="text-[26px] font-semibold tabular leading-none" style={{ color: 'var(--text)' }}>{k.value}</span>
            <span className="text-[11px]" style={{ color: 'var(--text-faint)' }}>{k.sub}</span>
          </div>
        ))}
      </div>

      {data.degraded && (
        <div className="text-[12px] px-3 py-2 rounded-lg" style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}>
          Lecture partielle ({data.degraded.join(', ')}) — les chiffres peuvent être incomplets.
        </div>
      )}

      {/* Série journalière (30 j) */}
      <div>
        <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
          <TrendingUp size={13} /> Coût par jour (30 derniers jours)
        </p>
        {(data.series_30d || []).length === 0 ? (
          <p className="text-[12.5px] py-3" style={{ color: 'var(--text-faint)' }}>Aucun run sur la période.</p>
        ) : (
          <div className="flex items-end gap-1 h-32">
            {data.series_30d.map((d) => (
              <div key={d.date} className="flex-1 flex flex-col items-center justify-end group" title={`${fmtDay(d.date)} · ${fmtUsd(d.cost)} · ${d.runs} run(s)`}>
                <div className="w-full rounded-t" style={{ height: `${Math.max(3, (d.cost / maxCost) * 100)}%`, background: 'var(--accent)' }} />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Modèles configurés */}
      <div>
        <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
          <Cpu size={13} /> Modèles IA configurés
        </p>
        <div className="card overflow-hidden">
          <table className="w-full text-[12.5px]">
            <tbody>
              {[
                ['Extraction CV', models.extraction],
                ['Scoring (2ᵉ avis)', models.scoring],
                ['Génération d\'AO', models.draft],
                ['Assistant', models.assistant],
              ].map(([label, m], i) => (
                <tr key={label} style={{ borderTop: i ? '1px solid var(--border)' : 'none' }}>
                  <td className="px-3 py-2 whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>{label}</td>
                  <td className="px-3 py-2 font-mono text-[12px]" style={{ color: 'var(--text)' }}>{m || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] mt-2" style={{ color: 'var(--text-faint)' }}>
          Réglables par variables d'environnement (EXTRACTION_MODEL / SCORING_MODEL / DRAFT_MODEL) sans redéploiement de code.
        </p>
      </div>
    </div>
  )
}

// ── Onglet RUM (Real User Monitoring) ────────────────────────────
function RumTab() {
  const [data, setData] = useState(null) // null=chargement, false=erreur réseau
  const load = () => {
    setData(null)
    api.get('/admin/rum').then(r => setData(r.data)).catch(() => setData(false))
  }
  useEffect(load, [])

  if (data === null) return <div className="text-[13px] py-8" style={{ color: 'var(--text-faint)' }}>Chargement…</div>

  const notReady = data === false || !data?.configured || !data?.ok

  if (notReady) {
    return (
      <div className="space-y-4">
        <div className="card p-5">
          <p className="text-sm font-semibold flex items-center gap-2" style={{ color: 'var(--text)' }}>
            <Radio size={15} className="text-brand-400" /> Real User Monitoring — en attente de l'API MIP RUM
          </p>
          <p className="text-[13px] mt-2 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            La télémétrie d'usage (sessions, parcours, performances, signaux de frustration) et le tracing
            front→back sont bien <strong>collectés</strong> par MIP RUM pour l'app <code>gip-plateforme</code>.
            Pour les afficher ici, MIP RUM doit exposer son <strong>API de lecture propriétaire</strong> ; le
            backend UTI l'interroge alors avec un token d'accès (gardé côté serveur).
          </p>
          <p className="text-[12.5px] mt-3" style={{ color: 'var(--text-faint)' }}>
            {data && data.message ? `État : ${data.message}` : 'État : API MIP RUM non configurée.'} Une fois
            l'API disponible, renseigner <code>MIP_RUM_READ_URL</code> et <code>MIP_RUM_READ_TOKEN</code> côté
            serveur — cet onglet s'activera automatiquement, sans changement de code.
          </p>
        </div>
      </div>
    )
  }

  // API disponible → on affiche le JSON MIP tel quel (mise en forme minimale ;
  // à enrichir en graphes une fois le schéma MIP figé).
  const d = data.data || {}
  const cards = Object.entries(d).filter(([, v]) => typeof v === 'number' || typeof v === 'string')
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <p className="text-[12.5px]" style={{ color: 'var(--text-muted)' }}>Activité utilisateurs (MIP RUM · 30 jours).</p>
        <button onClick={load} className="btn-ghost !h-7 !px-2 text-[11px]" title="Rafraîchir"><RefreshCw size={12} /></button>
      </div>
      {cards.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-6">
          {cards.map(([k, v]) => (
            <div key={k} className="flex flex-col gap-1.5 lg:px-5 lg:border-l lg:first:border-l-0 lg:first:pl-0 border-[color:var(--border)]">
              <span className="text-[11px] uppercase tracking-[0.07em] font-semibold" style={{ color: 'var(--text-faint)' }}>{k}</span>
              <span className="text-[26px] font-semibold tabular leading-none" style={{ color: 'var(--text)' }}>{String(v)}</span>
            </div>
          ))}
        </div>
      )}
      <details className="text-[12px]">
        <summary className="cursor-pointer" style={{ color: 'var(--text-faint)' }}>Données brutes MIP RUM</summary>
        <pre className="mt-2 p-3 rounded-lg overflow-auto text-[11px]" style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}>
          {JSON.stringify(d, null, 2)}
        </pre>
      </details>
    </div>
  )
}

export default function SupervisionPage() {
  const [params, setParams] = useSearchParams()
  const tab = TABS.some(t => t.k === params.get('tab')) ? params.get('tab') : 'logs'
  const setTab = (k) => setParams(k === 'logs' ? {} : { tab: k }, { replace: true })

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="section-title flex items-center gap-2">
            <Activity size={20} strokeWidth={1.75} style={{ color: 'var(--accent-text)' }} />
            Supervision
          </h1>
          <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
            Santé du backend, usage & coûts de l'IA, activité des utilisateurs.
          </p>
        </div>
      </div>

      {/* Sous-onglets */}
      <div className="flex gap-1 rounded-lg p-1 mb-6 w-fit" style={{ background: 'var(--surface-2)' }}>
        {TABS.map(t => (
          <button key={t.k} onClick={() => setTab(t.k)}
            className={tab === t.k
              ? 'seg-active px-3 py-1.5 text-[12.5px] rounded-md font-medium inline-flex items-center gap-1.5'
              : 'px-3 py-1.5 text-[12.5px] rounded-md font-medium text-[var(--text-muted)] hover:text-[var(--text)] inline-flex items-center gap-1.5'}>
            <t.icon size={13} /> {t.label}
          </button>
        ))}
      </div>

      {tab === 'logs' && <ErrorJournal embedded />}
      {tab === 'ia' && <AiUsageTab />}
      {tab === 'rum' && <RumTab />}
    </div>
  )
}
