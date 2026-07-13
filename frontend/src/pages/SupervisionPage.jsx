import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import api from '../lib/api'
import ErrorJournal from '../components/ErrorJournal'
import UTILoader, { ChartLoader } from '../components/UTILoader'
import {
  Activity, Coins, Radio, RefreshCw, Cpu, TrendingUp,
  Wallet, ShieldCheck, Layers, FileText, Users, AlertTriangle, Loader2,
} from 'lucide-react'

const TABS = [
  { k: 'logs', label: "Journal d'erreurs", icon: Activity },
  { k: 'ia', label: 'Usage & coûts IA', icon: Coins },
  { k: 'rum', label: 'RUM (activité)', icon: Radio },
]

const fmtUsd = (v) => (v == null ? '—' : `$${Number(v).toFixed(Math.abs(v) < 1 ? 4 : 2)}`)
const fmtInt = (v) => (v == null ? '—' : new Intl.NumberFormat('fr-FR').format(v))
const fmtTok = (v) => {
  if (v == null) return '—'
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)} M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)} k`
  return fmtInt(v)
}
const fmtDay = (iso) => {
  try { return new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short' }).format(new Date(iso)) }
  catch { return iso }
}

const OP_LABELS = {
  extraction: 'Extraction CV', scoring: 'Scoring (2ᵉ avis)', draft: "Génération d'AO",
  summary: "Résumé d'AO", assistant: 'Assistant', harmonize: 'Harmonisation CV',
}
const AI_WINDOWS = [{ k: '24h', l: '24 h' }, { k: '7d', l: '7 j' }, { k: '30d', l: '30 j' }, { k: '90d', l: '90 j' }]

// Palette catégorielle data-viz (tokens --viz-* dans index.css, validés CVD).
// Assignée par rang de coût du modèle ; au-delà du 6ᵉ → « Autres » (gris).
const VIZ = ['var(--viz-1)', 'var(--viz-2)', 'var(--viz-3)', 'var(--viz-4)', 'var(--viz-5)', 'var(--viz-6)']
const VIZ_OTHER = 'var(--text-faint)'
const shortModel = (m) => (m || '—').replace(/^[^/]+\//, '')

// Mini-courbe d'évolution (façon tuiles OpenRouter). Aire + trait + point final.
function Sparkline({ values, color = 'var(--accent)', width = 104, height = 30 }) {
  const vals = Array.isArray(values) ? values.filter(v => Number.isFinite(v)) : []
  if (vals.length < 2) return null
  const max = Math.max(...vals), min = Math.min(...vals)
  const span = max - min || 1
  const n = vals.length
  const pts = vals.map((v, i) => [
    (i / (n - 1)) * width,
    height - 2 - ((v - min) / span) * (height - 4),
  ])
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area = `${line} L${width.toFixed(1)},${height} L0,${height} Z`
  const [lx, ly] = pts[n - 1]
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" className="shrink-0">
      <path d={area} fill={color} opacity="0.12" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="2.1" fill={color} />
    </svg>
  )
}

// Jauge « dépense vs budget » d'une période, avec limite éditable + marqueur 80 %.
function BudgetRow({ label, periodHint, spend, limitDraft, onLimit }) {
  const limit = Number(limitDraft) || 0
  const pct = (limit > 0 && spend != null) ? (spend / limit) * 100 : null
  const tone = pct == null ? 'var(--text-faint)' : pct >= 100 ? 'var(--danger)' : pct >= 80 ? 'var(--warning)' : 'var(--success)'
  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-1.5">
        <span className="text-[12.5px] font-medium" style={{ color: 'var(--text)' }}>
          {label} <span style={{ color: 'var(--text-faint)' }}>· {periodHint}</span>
        </span>
        <div className="flex items-center gap-1">
          <span className="text-[12px]" style={{ color: 'var(--text-faint)' }}>$</span>
          <input type="number" min="0" step="1" value={limitDraft}
            onChange={(e) => onLimit(e.target.value)}
            className="input !h-8 w-24 text-right text-[13px]" placeholder="0" />
        </div>
      </div>
      <div className="relative h-2 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
        {pct != null && <div className="h-full rounded-full" style={{ width: `${Math.min(100, pct)}%`, background: tone }} />}
        {limit > 0 && <div className="absolute top-[-2px] bottom-[-2px] w-px" style={{ left: '80%', background: 'var(--border-strong)' }} title="Seuil d'alerte 80 %" />}
      </div>
      <div className="flex items-center justify-between mt-1 text-[11px]">
        <span style={{ color: 'var(--text-faint)' }} className="tabular">
          {spend != null ? `Dépensé : $${spend.toFixed(2)}` : 'Dépense indisponible'}
        </span>
        <span style={{ color: pct != null ? tone : 'var(--text-faint)' }} className="tabular font-medium">
          {limit > 0 ? (pct != null ? `${Math.round(pct)} % du budget` : `budget $${limit}`) : 'aucune limite'}
        </span>
      </div>
    </div>
  )
}

// ── Onglet Usage & coûts IA — miroir OpenRouter + traçabilité ────
function AiUsageTab() {
  const [win, setWin] = useState('30d')
  const [data, setData] = useState(null)   // registre interne (attribution)
  const [orr, setOrr] = useState(undefined) // undefined=chargement, null=indispo
  const [rum, setRum] = useState(undefined) // télémétrie IA via MIP (latence / erreur)
  const load = (w = win) => {
    setData(null); setOrr(undefined); setRum(undefined)
    api.get('/admin/ai-usage', { params: { window: w } }).then(r => setData(r.data)).catch(() => setData(false))
    api.get('/admin/ai-openrouter', { params: { window: w } }).then(r => setOrr(r.data)).catch(() => setOrr(null))
    // MIP /rum/summary porte aussi la perf IA (latence p75, taux d'erreur) que
    // la facturation OpenRouter n'a pas. Fenêtre MIP : 30 j max (90 j → 30 j).
    api.get('/admin/rum', { params: { window: w === '90d' ? '30d' : w } }).then(r => setRum(r.data)).catch(() => setRum(null))
  }
  useEffect(() => { load(win) }, [win])

  // Budget IA (indépendant de la fenêtre) — chargé une fois.
  const [budget, setBudget] = useState(null)
  const [budgetDraft, setBudgetDraft] = useState(null)
  const [savingBudget, setSavingBudget] = useState(false)
  useEffect(() => {
    api.get('/admin/settings')
      .then(r => { setBudget(r.data.ai_budget); setBudgetDraft(r.data.ai_budget) })
      .catch(() => {})
  }, [])
  const budgetDirty = budget && budgetDraft && (
    !!budget.enabled !== !!budgetDraft.enabled ||
    Number(budget.weekly_usd) !== Number(budgetDraft.weekly_usd) ||
    Number(budget.monthly_usd) !== Number(budgetDraft.monthly_usd)
  )
  const saveBudget = async () => {
    if (!budgetDraft) return
    setSavingBudget(true)
    try {
      const r = await api.put('/admin/settings/ai-budget', {
        enabled: !!budgetDraft.enabled,
        weekly_usd: Number(budgetDraft.weekly_usd) || 0,
        monthly_usd: Number(budgetDraft.monthly_usd) || 0,
      })
      setBudget(r.data.ai_budget); setBudgetDraft(r.data.ai_budget)
    } catch (e) {
      alert(e.response?.data?.detail || "Erreur lors de l'enregistrement du budget")
    } finally {
      setSavingBudget(false)
    }
  }

  const [testingAlert, setTestingAlert] = useState(false)
  const [testResult, setTestResult] = useState(null) // { ok, msg }
  const testAlert = async () => {
    setTestingAlert(true); setTestResult(null)
    try {
      const r = await api.post('/admin/ai-budget/test')
      setTestResult({ ok: true, msg: `Email envoyé à ${r.data.to} · ${r.data.admin_count} admin(s) alerté(s) en condition réelle.` })
    } catch (e) {
      setTestResult({ ok: false, msg: e.response?.data?.detail || "Échec de l'envoi de l'email de test." })
    } finally {
      setTestingAlert(false)
    }
  }

  const winLabel = (AI_WINDOWS.find(w => w.k === win) || {}).l || win
  const orLoading = orr === undefined            // valeurs en cours de recherche
  const orConfigured = orr && orr.configured
  const hasProv = orr && orr.has_provisioning
  const orT = (orr && orr.totals) || {}
  const orSeries = (orr && orr.series) || []
  const orModels = (orr && orr.by_model) || []
  const orKeys = (orr && orr.keys) || []
  const maxOr = Math.max(0.0001, ...orSeries.map(d => d.cost))
  const maxReq = Math.max(1, ...orSeries.map(d => d.requests || 0))
  // Coût des seules clés plateforme (par fenêtre) — la dépense « à toi », hors autres apps du compte.
  const PC_MAP = { '24h': 'daily', '7d': 'weekly', '30d': 'monthly', '90d': 'total' }
  const pc = (orr && orr.platform_cost) || null
  const platCost = pc ? pc[PC_MAP[win] || 'monthly'] : null

  // Attribution interne (ce qu'OpenRouter/MIP ne savent pas : quel AO / quel compte).
  const topAos = (data && data.top_aos) || []
  const topUsers = (data && data.top_users) || []
  const models = (data && data.models) || {}

  // Séries par jour pour les mini-courbes des tuiles (données réelles OpenRouter).
  const costSpark = orSeries.map(d => d.cost)
  const reqSpark = orSeries.map(d => d.requests)
  const tokSpark = orSeries.map(d => d.tokens)

  // Rang → couleur pour l'empilement par modèle (l'entité, pas le rang, garde
  // sa couleur au sein d'un même rendu). Top 6 colorés, le reste → « Autres ».
  const modelRank = new Map(orModels.map((m, i) => [m.model, i]))
  const colorOf = (m) => (modelRank.get(m) < 6 ? VIZ[modelRank.get(m)] : VIZ_OTHER)
  const topModels = orModels.slice(0, 6)
  const hasOther = orModels.length > 6

  // Segments empilés d'une journée, ordonnés par rang (rang 0 en bas).
  const daySegments = (d) => {
    const models = d.models || {}
    const segs = []
    let other = 0
    for (const [m, c] of Object.entries(models)) {
      if (modelRank.get(m) < 6) segs.push({ model: m, cost: c, color: colorOf(m) })
      else other += c
    }
    segs.sort((a, b) => modelRank.get(a.model) - modelRank.get(b.model))
    if (other > 0) segs.push({ model: 'Autres', cost: other, color: VIZ_OTHER })
    // Rétrocompat : si le backend n'expose pas encore le détail par modèle,
    // on retombe sur une barre unique (couleur accent) = coût total du jour.
    if (segs.length === 0 && d.cost > 0) segs.push({ model: '—', cost: d.cost, color: 'var(--accent)' })
    return segs
  }

  const kpis = [
    { label: 'Dépense', value: (hasProv && platCost != null) ? fmtUsd(platCost) : (hasProv ? fmtUsd(orT.cost) : '—'), sub: `Plateforme · ${winLabel}`, spark: hasProv ? costSpark : null, sparkColor: 'var(--accent)' },
    { label: 'Requêtes', value: hasProv ? fmtInt(orT.requests) : '—', sub: 'appels facturés', spark: hasProv ? reqSpark : null, sparkColor: 'var(--viz-2)' },
    { label: 'Tokens', value: hasProv ? fmtTok(orT.tokens) : '—', sub: hasProv ? `${fmtTok(orT.prompt_tokens)} in · ${fmtTok(orT.completion_tokens)} out` : '', spark: hasProv ? tokSpark : null, sparkColor: 'var(--viz-1)' },
    { label: 'Solde', value: orConfigured ? fmtUsd(orr.balance) : '—', sub: orConfigured ? `${fmtUsd(orr.usage)} / ${fmtUsd(orr.total_credits)} consommé` : '' },
  ]

  return (
    <div className="space-y-7">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <p className="text-[12.5px]" style={{ color: 'var(--text-muted)' }}>
          Facturation <strong>réelle</strong> du compte OpenRouter UTI (miroir de l'API), + attribution interne par AO et par compte.
        </p>
        <div className="flex items-center gap-1.5">
          <div className="flex gap-1 rounded-lg p-0.5" style={{ background: 'var(--surface-2)' }}>
            {AI_WINDOWS.map(w => (
              <button key={w.k} onClick={() => setWin(w.k)}
                className={win === w.k ? 'seg-active px-2.5 py-1 text-[11px] rounded-md font-medium' : 'px-2.5 py-1 text-[11px] rounded-md font-medium text-[var(--text-muted)] hover:text-[var(--text)]'}>
                {w.l}
              </button>
            ))}
          </div>
          <button onClick={() => load(win)} className="btn-ghost !h-7 !px-2 text-[11px]" title="Rafraîchir"><RefreshCw size={12} /></button>
        </div>
      </div>

      {orr !== undefined && !orConfigured && (
        <div className="flex items-start gap-2 text-[12px] px-3 py-2.5 rounded-lg" style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}>
          <AlertTriangle size={14} className="mt-0.5 shrink-0" style={{ color: 'var(--warning, #b78103)' }} />
          <span>Compte OpenRouter non joignable{orr && orr.message ? ` — ${orr.message}` : ''}. Vérifier <code>OPENROUTER_KEY</code> côté serveur.</span>
        </div>
      )}

      {/* KPIs — miroir du compte OpenRouter UTI */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-6">
        {kpis.map((k, i) => (
          <div key={i} className="flex flex-col gap-1.5 lg:px-5 lg:border-l lg:first:border-l-0 lg:first:pl-0 border-[color:var(--border)]">
            <span className="text-[11px] uppercase tracking-[0.07em] font-semibold" style={{ color: 'var(--text-faint)' }}>{k.label}</span>
            <div className="flex items-end justify-between gap-2">
              <span className="text-[26px] font-semibold tabular leading-none flex items-center" style={{ color: 'var(--text)', minHeight: 26 }}>
                {orLoading ? <UTILoader size={22} /> : k.value}
              </span>
              {!orLoading && k.spark && k.spark.length >= 2 && (
                <Sparkline values={k.spark} color={k.sparkColor} width={96} height={28} />
              )}
            </div>
            <span className="text-[11px]" style={{ color: 'var(--text-faint)' }}>{orLoading ? '' : k.sub}</span>
          </div>
        ))}
      </div>

      {orLoading && <ChartLoader height={150} label="Recherche des données OpenRouter…" />}

      {orConfigured && !hasProv && (
        <div className="flex items-start gap-2 text-[12px] px-3 py-2.5 rounded-lg" style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}>
          <AlertTriangle size={14} className="mt-0.5 shrink-0" style={{ color: 'var(--warning, #b78103)' }} />
          <span>
            Seul le solde du compte est visible. Ajouter la <strong>clé de provisioning</strong> (<code>OPENROUTER_PROVISIONING_KEY</code>)
            côté serveur pour le détail par modèle, par jour et par clé — comme le dashboard OpenRouter.
          </span>
        </div>
      )}

      {/* Budget IA — limites hebdo/mensuelle + alerte email aux admins (80 % / 100 %) */}
      {budgetDraft && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2 flex items-center gap-1.5 flex-wrap" style={{ color: 'var(--text-faint)' }}>
            <Wallet size={13} /> Budget IA
            <span className="normal-case tracking-normal font-normal" style={{ color: 'var(--text-faint)' }}>
              · alerte email aux admins à 80 % puis 100 % · sans coupure de l'IA
            </span>
          </p>
          <div className="card p-4 space-y-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <label className="flex items-center gap-2 text-[12.5px] cursor-pointer" style={{ color: 'var(--text-muted)' }}>
                <input type="checkbox" checked={!!budgetDraft.enabled}
                  onChange={(e) => setBudgetDraft(d => ({ ...d, enabled: e.target.checked }))} />
                Surveillance active
              </label>
              <div className="flex items-center gap-2">
                <button onClick={testAlert} disabled={testingAlert}
                  className="btn-ghost !h-8 text-xs px-3 flex items-center gap-1.5 disabled:opacity-40"
                  title="Envoie un email d'alerte d'exemple à votre adresse">
                  {testingAlert ? <Loader2 size={13} className="animate-spin" /> : <Radio size={13} />}
                  Tester l'alerte
                </button>
                <button onClick={saveBudget} disabled={!budgetDirty || savingBudget}
                  className="btn-primary !h-8 text-xs px-4 flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-default">
                  {savingBudget ? <Loader2 size={13} className="animate-spin" /> : 'Enregistrer'}
                </button>
              </div>
            </div>
            {testResult && (
              <p className="text-[12px] -mt-1" style={{ color: testResult.ok ? 'var(--success)' : 'var(--danger)' }}>
                {testResult.msg}
              </p>
            )}
            <div className="grid sm:grid-cols-2 gap-x-8 gap-y-5">
              <BudgetRow label="Hebdomadaire" periodHint="7 j" spend={pc ? pc.weekly : null}
                limitDraft={budgetDraft.weekly_usd}
                onLimit={(v) => setBudgetDraft(d => ({ ...d, weekly_usd: v }))} />
              <BudgetRow label="Mensuel" periodHint="mois en cours" spend={pc ? pc.monthly : null}
                limitDraft={budgetDraft.monthly_usd}
                onLimit={(v) => setBudgetDraft(d => ({ ...d, monthly_usd: v }))} />
            </div>
            <p className="text-[11px]" style={{ color: 'var(--text-faint)' }}>
              Dépense réelle OpenRouter (clés plateforme). Une limite à <strong>0</strong> ne surveille pas la période.
              Le contrôle tourne chaque heure ; une alerte au plus par palier et par période.
              {!hasProv && ' La dépense en direct nécessite la clé de provisioning OpenRouter côté serveur.'}
            </p>
          </div>
        </div>
      )}

      {/* Dépense par modèle par jour (empilé, façon dashboard OpenRouter) */}
      {hasProv && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
            <TrendingUp size={13} /> Dépense par modèle par jour ({winLabel})
          </p>
          {orSeries.length === 0 ? (
            <p className="text-[12.5px] py-3" style={{ color: 'var(--text-faint)' }}>Aucune dépense sur la période.</p>
          ) : (
            <>
              <div className="flex items-end gap-1 h-40">
                {orSeries.map((d) => {
                  const segs = daySegments(d)
                  return (
                    <div key={d.date} className="flex-1 h-full flex flex-col justify-end"
                         title={`${fmtDay(d.date)} · ${fmtUsd(d.cost)} · ${fmtInt(d.requests)} req · ${fmtTok(d.tokens)} tokens`}>
                      {/* rendu haut → bas : on inverse l'ordre (rang 0 reste en bas) */}
                      {[...segs].reverse().map((s, i) => (
                        <div key={s.model}
                          style={{
                            height: `${Math.max(1.5, (s.cost / maxOr) * 100)}%`,
                            background: s.color,
                            marginTop: i ? 2 : 0,
                            borderRadius: i === 0 ? '3px 3px 0 0' : 0,
                          }}
                          title={`${shortModel(s.model)} · ${fmtDay(d.date)} · ${fmtUsd(s.cost)}`}
                        />
                      ))}
                    </div>
                  )
                })}
              </div>
              {/* Légende — l'identité ne repose jamais sur la couleur seule */}
              {topModels.length > 0 && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-3">
                  {topModels.map((m, i) => (
                    <span key={m.model} className="inline-flex items-center gap-1.5 text-[11px]" style={{ color: 'var(--text-muted)' }}>
                      <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: VIZ[i] }} />
                      <span className="font-mono text-[11px]">{shortModel(m.model)}</span>
                      <span className="tabular" style={{ color: 'var(--text-faint)' }}>{fmtUsd(m.cost)}</span>
                    </span>
                  ))}
                  {hasOther && (
                    <span className="inline-flex items-center gap-1.5 text-[11px]" style={{ color: 'var(--text-muted)' }}>
                      <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: VIZ_OTHER }} /> Autres
                    </span>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Volume — requêtes facturées par jour (complément « coût + volume ») */}
      {hasProv && orSeries.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
            <Cpu size={13} /> Volume — requêtes par jour ({winLabel})
          </p>
          <div className="flex items-end gap-1 h-24">
            {orSeries.map((d) => (
              <div key={d.date} className="flex-1 flex flex-col justify-end"
                   title={`${fmtDay(d.date)} · ${fmtInt(d.requests)} req · ${fmtTok(d.tokens)} tokens`}>
                <div className="w-full" style={{ height: `${Math.max(2, (d.requests / maxReq) * 100)}%`, background: 'var(--viz-2)', borderRadius: '3px 3px 0 0' }} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Usage par modèle + Clés API (comme le dashboard OpenRouter) */}
      {hasProv && (
        <div className="grid lg:grid-cols-2 gap-6">
          <BreakdownTable icon={Cpu} title="Usage par modèle (OpenRouter)"
            rows={orModels.map(m => ({ key: m.model, cost: m.cost, calls: m.requests, tokens: m.tokens }))}
            total={orT.cost} labelOf={(r) => r.key} mono />
          <div>
            <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
              <Coins size={13} /> Clés API de la plateforme
              {orr && orr.keys_filtered === false && (
                <span className="normal-case tracking-normal font-normal ml-1" style={{ color: 'var(--text-faint)' }}>
                  · toutes les clés du compte (nommage non reconnu)
                </span>
              )}
            </p>
            <div className="card overflow-hidden">
              {orKeys.length === 0 ? (
                <p className="text-[12px] px-3 py-4" style={{ color: 'var(--text-faint)' }}>Aucune clé listée.</p>
              ) : (
                <table className="w-full text-[12.5px]">
                  <thead>
                    <tr className="text-left text-[10.5px] uppercase tracking-wide" style={{ color: 'var(--text-faint)', borderBottom: '1px solid var(--border)' }}>
                      <th className="font-medium px-3 py-2">Clé</th>
                      <th className="font-medium px-3 py-2 text-right">7 j</th>
                      <th className="font-medium px-3 py-2 text-right">30 j</th>
                      <th className="font-medium px-3 py-2 text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orKeys.map((k, i) => (
                      <tr key={i} style={{ borderTop: i ? '1px solid var(--border)' : 'none', opacity: k.disabled ? 0.5 : 1 }}>
                        <td className="px-3 py-2" style={{ color: 'var(--text)' }}>
                          {k.name || '—'}
                          <span className="ml-1.5 font-mono text-[10.5px]" style={{ color: 'var(--text-faint)' }}>{k.label}</span>
                        </td>
                        <td className="px-3 py-2 text-right tabular" style={{ color: 'var(--text-muted)' }}>{fmtUsd(k.usage_weekly)}</td>
                        <td className="px-3 py-2 text-right tabular" style={{ color: 'var(--text-muted)' }}>{fmtUsd(k.usage_monthly)}</td>
                        <td className="px-3 py-2 text-right tabular font-medium" style={{ color: 'var(--text)' }}>{fmtUsd(k.usage)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Attribution interne — ce qu'OpenRouter ne sait pas : quel AO / quel compte */}
      {(topAos.length > 0 || topUsers.length > 0) && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
            <ShieldCheck size={13} /> Attribution interne UTI <span className="normal-case tracking-normal font-normal" style={{ color: 'var(--text-faint)' }}>· qui a consommé (registre <code>ai_usage</code>)</span>
          </p>
          <div className="grid lg:grid-cols-2 gap-6">
            <div>
              <p className="text-[11px] mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}><FileText size={12} /> AOs les plus consommateurs</p>
              <ConsumerTable rows={topAos} nameOf={(r) => r.title || r.ao_id} />
            </div>
            <div>
              <p className="text-[11px] mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}><Users size={12} /> Comptes les plus consommateurs</p>
              <ConsumerTable rows={topUsers} nameOf={(r) => r.name || r.email || '—'} />
            </div>
          </div>
        </div>
      )}

      {/* Performance IA (MIP) — latence & fiabilité, via /rum/summary (ce qu'OpenRouter n'a pas) */}
      {(() => {
        const rumLoading = rum === undefined
        const rd = (rum && rum.ok && rum.data) ? rum.data : null
        const hasAi = rd && (rd.ai_calls != null || rd.ai_p75_latency_ms != null || rd.ai_cost_usd != null)
        const mipWinLabel = win === '90d' ? '30 j' : winLabel
        return (
          <div>
            <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2 flex items-center gap-1.5 flex-wrap" style={{ color: 'var(--text-faint)' }}>
              <Radio size={13} /> Performance IA (MIP)
              <span className="normal-case tracking-normal font-normal" style={{ color: 'var(--text-faint)' }}>
                · latence &amp; fiabilité · app <code>gip-plateforme</code> · {mipWinLabel}
              </span>
              <MipBadge />
            </p>
            {rumLoading ? (
              <ChartLoader height={90} label="Lecture de la télémétrie MIP…" />
            ) : !hasAi ? (
              <div className="card p-4">
                <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                  {rum && rum.configured === false
                    ? <>API MIP RUM non configurée côté serveur (<code>MIP_RUM_READ_URL</code> / <code>MIP_RUM_READ_TOKEN</code>). La latence p75 et le taux d'erreur des appels IA — que la facturation OpenRouter n'expose pas — apparaîtront ici une fois branchés.</>
                    : <>Aucun appel IA rattaché à cette app sur la période (ou télémétrie MIP momentanément indisponible).</>}
                </p>
              </div>
            ) : (
              <>
                <div className="card p-4 grid grid-cols-2 lg:grid-cols-4 gap-y-5">
                  {[
                    { label: 'Latence p75', value: fmtMs(rd.ai_p75_latency_ms), sub: 'appels LLM backend' },
                    { label: "Taux d'erreur IA", value: fmtPct(rd.ai_error_rate), sub: 'appels en échec' },
                    { label: 'Appels IA', value: fmtInt(rd.ai_calls), sub: `${fmtTok(rd.ai_tokens)} tokens` },
                    { label: 'Coût IA (MIP)', value: fmtUsd(rd.ai_cost_usd), sub: 'attribué à cette app' },
                  ].map((k, i) => (
                    <div key={i} className="flex flex-col gap-1.5 lg:px-5 lg:border-l lg:first:border-l-0 lg:first:pl-0 border-[color:var(--border)]">
                      <span className="text-[11px] uppercase tracking-[0.07em] font-semibold" style={{ color: 'var(--text-faint)' }}>{k.label}</span>
                      <span className="text-[22px] font-semibold tabular leading-none" style={{ color: 'var(--text)' }}>{k.value}</span>
                      <span className="text-[11px]" style={{ color: 'var(--text-faint)' }}>{k.sub}</span>
                    </div>
                  ))}
                </div>
                {Array.isArray(rd.ai_by_model) && rd.ai_by_model.length > 0 && (
                  <div className="mt-4">
                    <p className="text-[11px] mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
                      <Cpu size={12} /> Coût IA par modèle <span style={{ color: 'var(--text-faint)' }}>· attribution MIP (cette app)</span>
                    </p>
                    <HBars
                      items={rd.ai_by_model.slice(0, 8).map(m => ({ label: shortModel(m.model), value: m.cost_usd, tone: 'var(--viz-1)' }))}
                      fmt={fmtUsd}
                    />
                  </div>
                )}
              </>
            )}
          </div>
        )
      })()}

      {/* Modèles configurés */}
      <div>
        <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
          <Layers size={13} /> Modèles IA configurés
        </p>
        <div className="card overflow-hidden">
          <table className="w-full text-[12.5px]">
            <tbody>
              {[
                ['Extraction CV', models.extraction],
                ['Scoring (2ᵉ avis)', models.scoring],
                ['Résumé d\'AO', models.summary],
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

// Tableau de répartition (part de coût avec mini-barre).
function BreakdownTable({ icon: Icon, title, rows, total, labelOf, mono }) {
  const list = Array.isArray(rows) ? rows : []
  const max = Math.max(0.0001, ...list.map(r => r.cost))
  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
        <Icon size={13} /> {title}
      </p>
      <div className="card overflow-hidden">
        {list.length === 0 ? (
          <p className="text-[12px] px-3 py-4" style={{ color: 'var(--text-faint)' }}>Aucune donnée sur la période.</p>
        ) : (
          <table className="w-full text-[12.5px]">
            <tbody>
              {list.map((r, i) => (
                <tr key={r.key || i} style={{ borderTop: i ? '1px solid var(--border)' : 'none' }}>
                  <td className={`px-3 py-2 ${mono ? 'font-mono text-[11.5px]' : ''}`} style={{ color: 'var(--text)' }}>
                    <div className="flex items-center justify-between gap-3">
                      <span className="truncate">{labelOf(r)}</span>
                      <span className="tabular shrink-0" style={{ color: 'var(--text-muted)' }}>{fmtUsd(r.cost)}</span>
                    </div>
                    <div className="mt-1.5 h-1 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                      <div className="h-full rounded-full" style={{ width: `${Math.max(2, (r.cost / max) * 100)}%`, background: 'var(--accent)' }} />
                    </div>
                    <div className="mt-1 text-[10.5px]" style={{ color: 'var(--text-faint)' }}>
                      {fmtInt(r.calls)} appel(s) · {fmtTok(r.tokens)} tokens
                      {total > 0 ? ` · ${Math.round((r.cost / total) * 100)} %` : ''}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function ConsumerTable({ rows, nameOf }) {
  const list = Array.isArray(rows) ? rows : []
  return (
    <div className="card overflow-hidden">
      {list.length === 0 ? (
        <p className="text-[12px] px-3 py-4" style={{ color: 'var(--text-faint)' }}>Aucune donnée sur la période.</p>
      ) : (
        <table className="w-full text-[12.5px]">
          <tbody>
            {list.map((r, i) => (
              <tr key={i} style={{ borderTop: i ? '1px solid var(--border)' : 'none' }}>
                <td className="px-3 py-2 truncate max-w-[220px]" style={{ color: 'var(--text)' }} title={nameOf(r)}>{nameOf(r)}</td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap" style={{ color: 'var(--text-faint)' }}>{fmtInt(r.calls)} appel(s)</td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap font-medium" style={{ color: 'var(--text-muted)' }}>{fmtUsd(r.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── Onglet RUM (Real User Monitoring) ────────────────────────────
const RUM_WINDOWS = [{ k: '24h', l: '24 h' }, { k: '7d', l: '7 j' }, { k: '30d', l: '30 j' }]
// null/undefined → « — » ; jamais afficher 0 pour une métrique absente.
const fmtNum = (v) => (v == null ? '—' : new Intl.NumberFormat('fr-FR').format(v))
const fmtMs = (v) => (v == null ? '—' : `${new Intl.NumberFormat('fr-FR').format(Math.round(v))} ms`)
const fmtPct = (v) => (v == null ? '—' : `${(v * 100).toFixed(v < 0.1 ? 2 : 1)} %`)

// Attribution de la source : la télémétrie RUM provient de MIP.
function MipBadge() {
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide px-2 py-0.5 rounded-full shrink-0"
      style={{ background: 'var(--surface-2)', color: 'var(--text-faint)', border: '1px solid var(--border)' }}
      title="Données de télémétrie fournies par MIP RUM">
      <Radio size={9} /> powered by MIP
    </span>
  )
}

// Core Web Vitals + leurs seuils Google (bon / à améliorer / médiocre).
const VITALS = [
  { key: 'p75_lcp_ms', label: 'LCP', good: 2500, ni: 4000, cap: 6000, fmt: fmtMs },
  { key: 'p75_inp_ms', label: 'INP', good: 200, ni: 500, cap: 800, fmt: fmtMs },
  { key: 'cls', label: 'CLS', good: 0.1, ni: 0.25, cap: 0.5, fmt: (v) => (v == null ? '—' : Number(v).toFixed(2)) },
]

// Barre d'un Web Vital positionné sur ses zones de seuil (statut par couleur + libellé).
function VitalBar({ label, value, good, ni, cap, fmt }) {
  const status = value == null ? null : value <= good ? 'good' : value <= ni ? 'warn' : 'bad'
  const tone = { good: 'var(--success)', warn: 'var(--warning)', bad: 'var(--danger)' }[status] || 'var(--text-faint)'
  const statusLabel = { good: 'Bon', warn: 'À améliorer', bad: 'Médiocre' }[status] || '—'
  const goodPct = (good / cap) * 100
  const niPct = (ni / cap) * 100
  const pct = value == null ? null : Math.min(100, (value / cap) * 100)
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-[12px] font-medium" style={{ color: 'var(--text)' }}>
          {label} <span style={{ color: 'var(--text-faint)' }}>p75</span>
        </span>
        <span className="text-[12px] tabular font-semibold" style={{ color: tone }}>
          {fmt(value)}
          {status && <span className="ml-1.5 text-[10px] font-medium" style={{ color: 'var(--text-faint)' }}>{statusLabel}</span>}
        </span>
      </div>
      <div className="relative h-2 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
        <div className="absolute inset-y-0 left-0" style={{ width: `${goodPct}%`, background: 'var(--success-soft)' }} />
        <div className="absolute inset-y-0" style={{ left: `${goodPct}%`, width: `${niPct - goodPct}%`, background: 'var(--warning-soft)' }} />
        <div className="absolute inset-y-0" style={{ left: `${niPct}%`, right: 0, background: 'var(--danger-soft)' }} />
        {pct != null && (
          <div className="absolute top-[-2px] bottom-[-2px] w-[2px] rounded" style={{ left: `calc(${pct}% - 1px)`, background: tone }} title={`${fmt(value)}`} />
        )}
      </div>
      <div className="mt-1 text-[9.5px]" style={{ color: 'var(--text-faint)' }}>
        Bon ≤ {fmt(good)} · à améliorer ≤ {fmt(ni)}
      </div>
    </div>
  )
}

// Barres horizontales classées (routes lentes, erreurs…). Identité = libellé, pas la couleur.
function HBars({ items, fmt, tone = 'var(--accent)' }) {
  const list = Array.isArray(items) ? items : []
  const max = Math.max(1, ...list.map(i => i.value || 0))
  return (
    <div className="card p-3.5 space-y-2.5">
      {list.map((it, i) => (
        <div key={i}>
          <div className="flex items-center justify-between gap-3 mb-1">
            <span className="text-[12px] truncate font-mono" style={{ color: 'var(--text)' }} title={it.label}>{it.label}</span>
            <span className="text-[11.5px] tabular shrink-0" style={{ color: 'var(--text-muted)' }}>{fmt(it.value)}</span>
          </div>
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
            <div className="h-full rounded-full" style={{ width: `${Math.max(2, (it.value / max) * 100)}%`, background: it.tone || tone }} />
          </div>
        </div>
      ))}
    </div>
  )
}

// Sévérité d'un temps de chargement (ms) → couleur de statut.
const toneForMs = (v) => (v == null ? 'var(--text-faint)' : v <= 1000 ? 'var(--success)' : v <= 3000 ? 'var(--warning)' : 'var(--danger)')

function RumTab() {
  const [win, setWin] = useState('30d')
  const [data, setData] = useState(null) // null=chargement, false=erreur réseau
  const load = (w = win) => {
    setData(null)
    api.get('/admin/rum', { params: { window: w } }).then(r => setData(r.data)).catch(() => setData(false))
  }
  useEffect(() => { load(win) }, [win])

  if (data === null) return <div className="py-10 flex justify-center"><UTILoader size={40} label="Chargement…" /></div>

  const notReady = data === false || !data?.configured || !data?.ok
  if (notReady) {
    return (
      <div className="card p-5">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-sm font-semibold flex items-center gap-2" style={{ color: 'var(--text)' }}>
            <Radio size={15} className="text-brand-400" /> Real User Monitoring — en attente de l'API MIP RUM
          </p>
          <MipBadge />
        </div>
        <p className="text-[13px] mt-2 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          La télémétrie d'usage (sessions, parcours, performances, signaux de frustration) est <strong>collectée</strong>
          par MIP RUM pour l'app <code>gip-plateforme</code>. Le backend UTI interroge l'API de lecture MIP avec un
          token gardé côté serveur.
        </p>
        <p className="text-[12.5px] mt-3" style={{ color: 'var(--text-faint)' }}>
          {data && data.message ? `État : ${data.message}` : 'État : API MIP RUM non configurée.'} Renseigner
          <code> MIP_RUM_READ_URL</code> et <code>MIP_RUM_READ_TOKEN</code> côté serveur, puis redémarrer.
          <button onClick={() => load(win)} className="btn-ghost !h-7 !px-2.5 text-[11px] ml-2">Réessayer</button>
        </p>
      </div>
    )
  }

  const d = data.data || {}
  const series = Array.isArray(d.series) ? d.series : []
  const maxSess = Math.max(1, ...series.map(s => s.sessions || 0))
  const kpis = [
    { label: 'Sessions', value: fmtNum(d.sessions) },
    { label: 'Utilisateurs', value: fmtNum(d.users) },
    { label: 'Pages vues', value: fmtNum(d.page_views) },
    { label: 'Frustration', value: fmtNum(d.frustration_signals), sub: 'rage / dead clicks' },
    { label: 'Chargement moy.', value: fmtMs(d.avg_load_ms), sub: 'FCP moyen' },
    { label: 'LCP (p75)', value: fmtMs(d.p75_lcp_ms), sub: 'Core Web Vital' },
    { label: 'INP (p75)', value: fmtMs(d.p75_inp_ms), sub: 'Core Web Vital' },
    { label: 'Taux d\'erreur', value: fmtPct(d.error_rate) },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-[12.5px]" style={{ color: 'var(--text-muted)' }}>Activité des utilisateurs.</p>
          <MipBadge />
        </div>
        <div className="flex items-center gap-1.5">
          <div className="flex gap-1 rounded-lg p-0.5" style={{ background: 'var(--surface-2)' }}>
            {RUM_WINDOWS.map(w => (
              <button key={w.k} onClick={() => setWin(w.k)}
                className={win === w.k ? 'seg-active px-2.5 py-1 text-[11px] rounded-md font-medium' : 'px-2.5 py-1 text-[11px] rounded-md font-medium text-[var(--text-muted)] hover:text-[var(--text)]'}>
                {w.l}
              </button>
            ))}
          </div>
          <button onClick={() => load(win)} className="btn-ghost !h-7 !px-2 text-[11px]" title="Rafraîchir"><RefreshCw size={12} /></button>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-y-6">
        {kpis.map((k) => (
          <div key={k.label} className="flex flex-col gap-1.5 lg:px-5 lg:border-l lg:first:border-l-0 lg:first:pl-0 border-[color:var(--border)]">
            <span className="text-[11px] uppercase tracking-[0.07em] font-semibold" style={{ color: 'var(--text-faint)' }}>{k.label}</span>
            <span className="text-[24px] font-semibold tabular leading-none" style={{ color: 'var(--text)' }}>{k.value}</span>
            {k.sub && <span className="text-[11px]" style={{ color: 'var(--text-faint)' }}>{k.sub}</span>}
          </div>
        ))}
      </div>

      {/* Core Web Vitals vs seuils Google (l'un des plots MIP « perf / vue d'ensemble ») */}
      {VITALS.some(v => d[v.key] != null) && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
            <Activity size={13} /> Core Web Vitals vs seuils
          </p>
          <div className="card p-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {VITALS.filter(v => d[v.key] != null).map(v => (
              <VitalBar key={v.key} label={v.label} value={d[v.key]} good={v.good} ni={v.ni} cap={v.cap} fmt={v.fmt} />
            ))}
          </div>
        </div>
      )}

      {series.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
            <TrendingUp size={13} /> Sessions par jour
          </p>
          <div className="flex items-end gap-1 h-28">
            {series.map((s) => (
              <div key={s.date} className="flex-1 flex flex-col items-center justify-end"
                   title={`${fmtDay(s.date)} · ${fmtNum(s.sessions)} sessions · ${fmtNum(s.page_views)} vues · ${fmtNum(s.errors)} err.`}>
                <div className="w-full rounded-t" style={{ height: `${Math.max(3, ((s.sessions || 0) / maxSess) * 100)}%`, background: 'var(--accent)' }} />
              </div>
            ))}
          </div>
        </div>
      )}

      {Array.isArray(d.top_routes) && d.top_routes.some(r => r.avg_ms != null) && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
            <Activity size={13} /> Pages les plus lentes <span className="normal-case tracking-normal font-normal" style={{ color: 'var(--text-faint)' }}>· temps moyen</span>
          </p>
          <HBars
            items={[...d.top_routes].filter(r => r.avg_ms != null).sort((a, b) => (b.avg_ms || 0) - (a.avg_ms || 0)).slice(0, 8)
              .map(r => ({ label: r.route, value: r.avg_ms, tone: toneForMs(r.avg_ms) }))}
            fmt={fmtMs}
          />
        </div>
      )}

      {Array.isArray(d.top_routes) && d.top_routes.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2" style={{ color: 'var(--text-faint)' }}>Pages les plus vues</p>
          <div className="card overflow-hidden">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-left text-[10.5px] uppercase tracking-wide" style={{ color: 'var(--text-faint)', borderBottom: '1px solid var(--border)' }}>
                  <th className="font-medium px-3 py-2">Route</th><th className="font-medium px-3 py-2 text-right">Vues</th>
                  <th className="font-medium px-3 py-2 text-right">Temps moy.</th><th className="font-medium px-3 py-2 text-right">Erreurs</th>
                </tr>
              </thead>
              <tbody>
                {d.top_routes.map((r, i) => (
                  <tr key={i} style={{ borderTop: i ? '1px solid var(--border)' : 'none' }}>
                    <td className="px-3 py-2 font-mono text-[12px]" style={{ color: 'var(--text)' }}>{r.route}</td>
                    <td className="px-3 py-2 text-right tabular" style={{ color: 'var(--text-muted)' }}>{fmtNum(r.views)}</td>
                    <td className="px-3 py-2 text-right tabular" style={{ color: 'var(--text-muted)' }}>{fmtMs(r.avg_ms)}</td>
                    <td className="px-3 py-2 text-right tabular" style={{ color: r.errors ? 'var(--danger)' : 'var(--text-faint)' }}>{fmtNum(r.errors)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {Array.isArray(d.top_errors) && d.top_errors.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
            <AlertTriangle size={13} /> Erreurs front <span className="normal-case tracking-normal font-normal" style={{ color: 'var(--text-faint)' }}>· par occurrences</span>
          </p>
          <HBars
            items={[...d.top_errors].sort((a, b) => (b.count || 0) - (a.count || 0)).slice(0, 8)
              .map(e => ({ label: e.message, value: e.count, tone: 'var(--danger)' }))}
            fmt={fmtNum}
          />
        </div>
      )}

      {Array.isArray(d.top_errors) && d.top_errors.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2" style={{ color: 'var(--text-faint)' }}>Détail des erreurs</p>
          <div className="card overflow-hidden">
            <table className="w-full text-[12px]">
              <tbody>
                {d.top_errors.map((e, i) => (
                  <tr key={i} style={{ borderTop: i ? '1px solid var(--border)' : 'none' }}>
                    <td className="px-3 py-2 text-right tabular align-top" style={{ color: 'var(--danger)' }}>{fmtNum(e.count)}</td>
                    <td className="px-3 py-2 break-words align-top" style={{ color: 'var(--text)' }}>{e.message}</td>
                    <td className="px-3 py-2 whitespace-nowrap align-top" style={{ color: 'var(--text-faint)' }}>{e.last_seen ? fmtDay(e.last_seen) : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
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
