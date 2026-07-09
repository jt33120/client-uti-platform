import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import api from '../lib/api'
import ErrorJournal from '../components/ErrorJournal'
import UTILoader, { ChartLoader } from '../components/UTILoader'
import {
  Activity, Coins, Radio, RefreshCw, Cpu, TrendingUp,
  Wallet, ShieldCheck, Layers, FileText, Users, AlertTriangle,
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

// ── Onglet Usage & coûts IA — miroir OpenRouter + traçabilité ────
function AiUsageTab() {
  const [win, setWin] = useState('30d')
  const [data, setData] = useState(null)   // registre interne (attribution)
  const [orr, setOrr] = useState(undefined) // undefined=chargement, null=indispo
  const load = (w = win) => {
    setData(null); setOrr(undefined)
    api.get('/admin/ai-usage', { params: { window: w } }).then(r => setData(r.data)).catch(() => setData(false))
    api.get('/admin/ai-openrouter', { params: { window: w } }).then(r => setOrr(r.data)).catch(() => setOrr(null))
  }
  useEffect(() => { load(win) }, [win])

  const winLabel = (AI_WINDOWS.find(w => w.k === win) || {}).l || win
  const orLoading = orr === undefined            // valeurs en cours de recherche
  const orConfigured = orr && orr.configured
  const hasProv = orr && orr.has_provisioning
  const orT = (orr && orr.totals) || {}
  const orSeries = (orr && orr.series) || []
  const orModels = (orr && orr.by_model) || []
  const orKeys = (orr && orr.keys) || []
  const maxOr = Math.max(0.0001, ...orSeries.map(d => d.cost))
  // Coût des seules clés plateforme (par fenêtre) — la dépense « à toi », hors autres apps du compte.
  const PC_MAP = { '24h': 'daily', '7d': 'weekly', '30d': 'monthly', '90d': 'total' }
  const pc = (orr && orr.platform_cost) || null
  const platCost = pc ? pc[PC_MAP[win] || 'monthly'] : null

  // Attribution interne (ce qu'OpenRouter/MIP ne savent pas : quel AO / quel compte).
  const topAos = (data && data.top_aos) || []
  const topUsers = (data && data.top_users) || []
  const models = (data && data.models) || {}

  const kpis = [
    { label: 'Dépense', value: (hasProv && platCost != null) ? fmtUsd(platCost) : (hasProv ? fmtUsd(orT.cost) : '—'), sub: `Plateforme · ${winLabel}` },
    { label: 'Requêtes', value: hasProv ? fmtInt(orT.requests) : '—', sub: 'appels facturés' },
    { label: 'Tokens', value: hasProv ? fmtTok(orT.tokens) : '—', sub: hasProv ? `${fmtTok(orT.prompt_tokens)} in · ${fmtTok(orT.completion_tokens)} out` : '' },
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
            <span className="text-[26px] font-semibold tabular leading-none flex items-center" style={{ color: 'var(--text)', minHeight: 26 }}>
              {orLoading ? <UTILoader size={22} /> : k.value}
            </span>
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

      {/* Coût par jour (OpenRouter) */}
      {hasProv && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
            <TrendingUp size={13} /> Dépense par jour ({winLabel})
          </p>
          {orSeries.length === 0 ? (
            <p className="text-[12.5px] py-3" style={{ color: 'var(--text-faint)' }}>Aucune dépense sur la période.</p>
          ) : (
            <div className="flex items-end gap-1 h-32">
              {orSeries.map((d) => (
                <div key={d.date} className="flex-1 flex flex-col items-center justify-end group" title={`${fmtDay(d.date)} · ${fmtUsd(d.cost)} · ${fmtInt(d.requests)} req · ${fmtTok(d.tokens)} tokens`}>
                  <div className="w-full rounded-t" style={{ height: `${Math.max(3, (d.cost / maxOr) * 100)}%`, background: 'var(--accent)' }} />
                </div>
              ))}
            </div>
          )}
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

      {/* Performance IA (MIP) — à venir via l'API de lecture */}
      <div className="card p-4">
        <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-1.5 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
          <Radio size={13} /> Performance IA (MIP) — latence, gouvernance PII, lien session
        </p>
        <p className="text-[12px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          La couche observabilité (latence p75 par usage, gouvernance des données / PII, corrélation à la session RUM)
          est collectée par MIP à partir des spans <code>gen_ai</code> du backend. Elle sera recopiée ici dès que l'API de
          lecture MIP expose l'endpoint <code>/ai/summary</code> — comme l'onglet RUM.
        </p>
      </div>

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
          <p className="text-[11px] uppercase tracking-[0.08em] font-semibold mb-2" style={{ color: 'var(--text-faint)' }}>Erreurs front les plus fréquentes</p>
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
