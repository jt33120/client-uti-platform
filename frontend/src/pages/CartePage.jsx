import { useEffect, useMemo, useState, useCallback } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { MapContainer, TileLayer, CircleMarker, Circle, Popup, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import api from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { availabilityLabel } from '../lib/availability'
import {
  Map as MapIcon, Loader2, Wifi, MapPinOff, LocateFixed, Search, X,
  Crosshair, Target, Building2, Users, Briefcase,
} from 'lucide-react'

const FRANCE_CENTER = [46.6, 2.45]
const FRANCE_ZOOM = 6

const COLORS = {
  consultant: '#6366f1', // indigo
  onsite: '#10b981',     // emerald (AO sur site)
  hybrid: '#f59e0b',     // amber  (AO hybride)
  client: '#a855f7',     // violet
  center: '#ef4444',     // rouge (centre de recherche)
}

const WORK_MODE_LABEL = { onsite: 'Sur site', hybrid: 'Hybride', remote: 'Remote' }
const LAYERS = ['aos', 'clients', 'consultants']

function LegendDot({ color }) {
  return <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: color }} />
}

// Distance à vol d'oiseau (km) — filtre par périmètre.
function haversineKm(a, b) {
  const R = 6371, toRad = d => (d * Math.PI) / 180
  const dLat = toRad(b[0] - a[0]), dLon = toRad(b[1] - a[1])
  const lat1 = toRad(a[0]), lat2 = toRad(b[0])
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(x))
}

// Recentre/zoome sur un point ciblé (deep-link ?focus= ou centre de périmètre).
function FlyTo({ point, zoom = 11 }) {
  const map = useMap()
  useEffect(() => {
    if (point && point.latitude != null && point.longitude != null) {
      map.flyTo([point.latitude, point.longitude], zoom, { duration: 1.1 })
    }
  }, [point, zoom, map])
  return null
}

// Mode « pointer sur la carte » : un clic pose le centre de recherche.
function ClickToSetCenter({ enabled, onSet }) {
  useMapEvents({
    click(e) {
      if (enabled) onSet({ latitude: e.latlng.lat, longitude: e.latlng.lng, label: 'Point sélectionné' })
    },
  })
  return null
}

export default function CartePage() {
  const { isAdmin } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const focusId = searchParams.get('focus')
  const rawOnly = searchParams.get('only')  // aos | clients | consultants
  const only = LAYERS.includes(rawOnly) ? rawOnly : null  // ignore une valeur inconnue

  const [data, setData] = useState({ consultants: [], aos: [], clients: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [backfilling, setBackfilling] = useState(false)

  // Couches : ?only= ouvre la carte filtrée sur une seule entité.
  const [show, setShow] = useState(() => ({
    aos: !only || only === 'aos',
    clients: !only || only === 'clients',
    consultants: !only || only === 'consultants',
  }))

  // Recherche par périmètre.
  const [placeQuery, setPlaceQuery] = useState('')
  const [center, setCenter] = useState(null)          // { latitude, longitude, label }
  const [radiusKm, setRadiusKm] = useState(50)
  const [centerZoom, setCenterZoom] = useState(9)     // figé à la pose du centre (pas au slider)
  const [geoLoading, setGeoLoading] = useState(false)
  const [geoError, setGeoError] = useState('')
  const [pickMode, setPickMode] = useState(false)
  const [onlyInside, setOnlyInside] = useState(false) // masquer ce qui est hors périmètre

  const load = useCallback(() => {
    setLoading(true)
    return api.get('/map/points')
      .then(r => setData({ consultants: [], aos: [], clients: [], ...r.data }))
      .catch(e => setError(e.response?.data?.detail || 'Erreur de chargement de la carte'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const focusedConsultant = useMemo(
    () => focusId ? (data.consultants || []).find(c => String(c.id) === String(focusId)) : null,
    [focusId, data.consultants]
  )

  const placedAos = useMemo(
    () => (data.aos || []).filter(a => a.latitude != null && a.longitude != null),
    [data.aos]
  )
  const offMapAos = useMemo(
    () => (data.aos || []).filter(a => a.latitude == null || a.longitude == null),
    [data.aos]
  )
  const geocodableCount = useMemo(
    () => offMapAos.filter(a => a.work_mode !== 'remote' && a.location).length,
    [offMapAos]
  )

  // Distance au centre pour un point (ou null si pas de centre).
  const distOf = useCallback((lat, lon) => {
    if (!center || lat == null || lon == null) return null
    return haversineKm([center.latitude, center.longitude], [lat, lon])
  }, [center])
  const inside = useCallback((lat, lon) => {
    const d = distOf(lat, lon)
    return d == null ? true : d <= radiusKm
  }, [distOf, radiusKm])

  // Points visibles par couche (respecte le toggle + éventuellement le périmètre).
  const visibleConsultants = useMemo(
    () => (show.consultants ? (data.consultants || []) : []).filter(c => !onlyInside || inside(c.latitude, c.longitude)),
    [show.consultants, data.consultants, onlyInside, inside]
  )
  const visibleAos = useMemo(
    () => (show.aos ? placedAos : []).filter(a => !onlyInside || inside(a.latitude, a.longitude)),
    [show.aos, placedAos, onlyInside, inside]
  )
  const visibleClients = useMemo(
    () => (show.clients ? (data.clients || []) : []).filter(c => !onlyInside || inside(c.latitude, c.longitude)),
    [show.clients, data.clients, onlyInside, inside]
  )

  // Décompte dans le périmètre (indépendant du toggle onlyInside).
  const insideCounts = useMemo(() => {
    if (!center) return null
    const inC = (data.consultants || []).filter(c => inside(c.latitude, c.longitude))
    const inA = placedAos.filter(a => inside(a.latitude, a.longitude))
    const inCl = (data.clients || []).filter(c => inside(c.latitude, c.longitude))
    const withDist = (arr) => arr
      .map(x => ({ ...x, _dist: distOf(x.latitude, x.longitude) }))
      .sort((a, b) => (a._dist ?? 0) - (b._dist ?? 0))
    return { consultants: withDist(inC), aos: withDist(inA), clients: withDist(inCl) }
  }, [center, data.consultants, data.clients, placedAos, inside, distOf])

  // Pose un centre de recherche + fige le zoom d'arrivée (le slider de rayon ne
  // doit pas re-déclencher un vol de caméra à chaque cran).
  const focusCenter = (c) => { setCenterZoom(Math.max(8, 12 - Math.floor(radiusKm / 40))); setCenter(c) }

  const searchPlace = async (e) => {
    e?.preventDefault?.()
    const q = placeQuery.trim()
    if (!q) return
    setGeoLoading(true); setGeoError('')
    try {
      const { data: g } = await api.get('/map/geocode', { params: { q } })
      focusCenter({ latitude: g.latitude, longitude: g.longitude, label: g.label || q })
      setPickMode(false)
    } catch (e2) {
      setGeoError(e2.response?.status === 404 ? 'Lieu introuvable.' : 'Géocodage indisponible.')
    } finally {
      setGeoLoading(false)
    }
  }
  const clearPerimeter = () => { setCenter(null); setOnlyInside(false); setPickMode(false); setGeoError('') }

  const toggleLayer = (k) => {
    setShow(s => ({ ...s, [k]: !s[k] }))
    // Sortir du mode « une seule couche » dès qu'on ajuste manuellement.
    if (only) setSearchParams(p => { p.delete('only'); return p }, { replace: true })
  }

  const runBackfill = async () => {
    setBackfilling(true); setError('')
    try { await api.post('/map/backfill'); await load() }
    catch (e) { setError(e.response?.data?.detail || 'Échec du géocodage des fiches manquantes') }
    finally { setBackfilling(false) }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64"><Loader2 size={22} className="animate-spin" style={{ color: 'var(--text-faint)' }} /></div>
  }

  const layerMeta = {
    aos: { label: 'Appels d\'offres', color: COLORS.onsite, count: placedAos.length, icon: Briefcase },
    clients: { label: 'Clients', color: COLORS.client, count: (data.clients || []).length, icon: Building2 },
    consultants: { label: 'Consultants', color: COLORS.consultant, count: (data.consultants || []).length, icon: Users },
  }

  return (
    <div className="animate-slide-up">
      <div className="flex items-center gap-2.5 mb-1">
        <MapIcon size={18} style={{ color: 'var(--accent-text)' }} />
        <h1 className="text-xl font-bold" style={{ color: 'var(--text)' }}>Carte géographique</h1>
      </div>
      <p className="text-sm mb-4" style={{ color: 'var(--text-muted)' }}>
        Appels d'offres, clients et consultants sur le territoire — recherche par périmètre autour d'un lieu.
      </p>

      {/* Barre d'outils : couches + recherche périmètre */}
      <div className="card p-3 mb-3 flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          {LAYERS.map(k => {
            const m = layerMeta[k]
            return (
              <button key={k} onClick={() => toggleLayer(k)}
                className="badge inline-flex items-center gap-1.5"
                style={{ opacity: show[k] ? 1 : 0.4, background: 'var(--surface-2)', color: 'var(--text)' }}>
                <LegendDot color={m.color} /> {m.label} ({m.count})
              </button>
            )
          })}
          <span className="flex items-center gap-1.5 ml-1" style={{ color: 'var(--text-faint)' }}>
            <LegendDot color={COLORS.hybrid} /> AO hybride
          </span>
        </div>

        {/* Recherche par périmètre */}
        <form onSubmit={searchPlace} className="flex flex-wrap items-end gap-2">
          <div className="flex-1 min-w-[220px]">
            <label className="block text-[11px] mb-1" style={{ color: 'var(--text-faint)' }}>
              Rechercher dans un rayon autour d'un lieu
            </label>
            <div className="flex items-center gap-1.5">
              <div className="relative flex-1">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-faint)' }} />
                <input value={placeQuery} onChange={e => setPlaceQuery(e.target.value)}
                  placeholder="Ville, adresse… (ex. Nantes)"
                  className="input h-9 pl-8 text-sm w-full" />
              </div>
              <button type="submit" disabled={geoLoading} className="btn-primary h-9 text-[12px] gap-1.5">
                {geoLoading ? <Loader2 size={13} className="animate-spin" /> : <Target size={13} />} Cibler
              </button>
            </div>
          </div>
          <div className="min-w-[160px]">
            <label className="block text-[11px] mb-1" style={{ color: 'var(--text-faint)' }}>Rayon : {radiusKm} km</label>
            <input type="range" min={5} max={300} step={5} value={radiusKm}
              onChange={e => setRadiusKm(Number(e.target.value))} className="w-full accent-[var(--accent-text)]" />
          </div>
          <button type="button" onClick={() => setPickMode(p => !p)}
            className="btn-ghost h-9 text-[12px] gap-1.5" title="Cliquer sur la carte pour poser le centre"
            style={{ color: pickMode ? 'var(--accent-text)' : 'var(--text-muted)' }}>
            <Crosshair size={13} /> {pickMode ? 'Cliquez sur la carte…' : 'Pointer'}
          </button>
          {center && (
            <button type="button" onClick={clearPerimeter} className="btn-ghost h-9 text-[12px] gap-1.5">
              <X size={13} /> Effacer
            </button>
          )}
        </form>
        {geoError && <p className="text-[12px]" style={{ color: 'var(--danger)' }}>{geoError}</p>}
        {center && (
          <div className="flex items-center gap-3 flex-wrap text-[12px]" style={{ color: 'var(--text-muted)' }}>
            <span className="inline-flex items-center gap-1.5">
              <LegendDot color={COLORS.center} /> {center.label} · {radiusKm} km
            </span>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={onlyInside} onChange={e => setOnlyInside(e.target.checked)} />
              N'afficher que le périmètre
            </label>
          </div>
        )}
      </div>

      {error && <p className="text-sm mb-3" style={{ color: 'var(--danger)' }}>{error}</p>}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <div className="lg:col-span-3 card overflow-hidden" style={{ height: '72vh', padding: 0, cursor: pickMode ? 'crosshair' : undefined }}>
          <MapContainer center={FRANCE_CENTER} zoom={FRANCE_ZOOM} style={{ height: '100%', width: '100%' }} scrollWheelZoom>
            <TileLayer attribution='&copy; OpenStreetMap' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            <FlyTo point={focusedConsultant} />
            <FlyTo point={center} zoom={centerZoom} />
            <ClickToSetCenter enabled={pickMode} onSet={(c) => { focusCenter(c); setPickMode(false) }} />

            {/* Périmètre de recherche */}
            {center && (
              <>
                <Circle center={[center.latitude, center.longitude]} radius={radiusKm * 1000}
                  pathOptions={{ color: COLORS.center, fillColor: COLORS.center, fillOpacity: 0.06, weight: 1.5 }} />
                <CircleMarker center={[center.latitude, center.longitude]} radius={6}
                  pathOptions={{ color: '#fff', fillColor: COLORS.center, fillOpacity: 1, weight: 2 }}>
                  <Popup><div className="text-[12px] font-semibold">{center.label}</div><div className="text-[11px] text-slate-500">Rayon {radiusKm} km</div></Popup>
                </CircleMarker>
              </>
            )}

            {/* Clients */}
            {visibleClients.map(c => {
              const dim = center && !inside(c.latitude, c.longitude)
              return (
                <CircleMarker key={`cl-${c.id}`} center={[c.latitude, c.longitude]} radius={8}
                  pathOptions={{ color: COLORS.client, fillColor: COLORS.client, fillOpacity: dim ? 0.15 : 0.7, weight: 1, opacity: dim ? 0.3 : 1 }}>
                  <Popup>
                    <div className="text-[13px] font-semibold">{c.name}</div>
                    {c.sector && <div className="text-[12px] text-slate-500">{c.sector}</div>}
                    <div className="text-[11px] text-slate-500 mt-0.5">
                      {c.positioned_by === 'aos'
                        ? `Position approximée (${c.ao_count} AO)`
                        : c.city || '—'}
                    </div>
                    <Link to={`/clients/${c.id}`} className="text-[12px] text-indigo-600 underline">Ouvrir le client →</Link>
                  </Popup>
                </CircleMarker>
              )
            })}

            {/* Consultants */}
            {visibleConsultants.map(c => {
              const focused = focusedConsultant && String(c.id) === String(focusedConsultant.id)
              const dim = center && !inside(c.latitude, c.longitude)
              return (
                <CircleMarker key={`c-${c.id}`} center={[c.latitude, c.longitude]} radius={focused ? 11 : 7}
                  pathOptions={{
                    color: focused ? '#1d1d1f' : COLORS.consultant, fillColor: COLORS.consultant,
                    fillOpacity: dim ? 0.15 : (focused ? 0.95 : 0.7), weight: focused ? 3 : 1, opacity: dim ? 0.3 : 1,
                  }}>
                  <Popup>
                    <div className="text-[13px] font-semibold">{c.name}</div>
                    {c.city && <div className="text-[12px] text-slate-500">{c.city}</div>}
                    {c.availability_status && <div className="text-[11px] text-emerald-600">{availabilityLabel(c.availability_status)}</div>}
                    {c.skills && <div className="text-[11px] text-slate-500 mt-1">{c.skills}</div>}
                    {c.tjm && <div className="text-[11px] text-slate-500">TJM {c.tjm} €/j</div>}
                    <Link to={`/consultants/${c.id}`} className="text-[12px] text-indigo-600 underline">Ouvrir la fiche →</Link>
                  </Popup>
                </CircleMarker>
              )
            })}

            {/* AO */}
            {visibleAos.map(a => {
              const color = a.work_mode === 'hybrid' ? COLORS.hybrid : COLORS.onsite
              const dim = center && !inside(a.latitude, a.longitude)
              return (
                <CircleMarker key={`a-${a.id}`} center={[a.latitude, a.longitude]} radius={8}
                  pathOptions={{ color, fillColor: color, fillOpacity: dim ? 0.12 : 0.65, weight: a.work_mode === 'hybrid' ? 3 : 1, opacity: dim ? 0.3 : 1 }}>
                  <Popup>
                    <div className="text-[13px] font-semibold">{a.title}</div>
                    {a.clients?.name && <div className="text-[12px] text-slate-500">{a.clients.name}</div>}
                    {a.location && <div className="text-[11px] text-slate-500 mt-1">{a.location}</div>}
                    <div className="text-[11px] text-slate-500">{WORK_MODE_LABEL[a.work_mode] || '—'}</div>
                    <Link to={`/aos/${a.id}`} className="text-[12px] text-indigo-600 underline">Ouvrir l'AO →</Link>
                  </Popup>
                </CircleMarker>
              )
            })}
          </MapContainer>
        </div>

        {/* Panneau latéral : périmètre OU hors-carte */}
        <div className="card p-4">
          {center && insideCounts ? (
            <PerimeterPanel center={center} radiusKm={radiusKm} counts={insideCounts} show={show} />
          ) : (
            <>
              <h2 className="text-xs font-semibold uppercase tracking-wide mb-3 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
                <Wifi size={13} /> AO hors carte ({offMapAos.length})
              </h2>
              {isAdmin && geocodableCount > 0 && (
                <button onClick={runBackfill} disabled={backfilling}
                  className="w-full mb-3 flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-[12px] font-medium transition-colors"
                  style={{ border: '1px solid var(--border)', color: 'var(--text-muted)' }}
                  title="Géocoder les fiches (AO, consultants, clients) qui ont une localisation mais pas de point">
                  {backfilling ? <><Loader2 size={13} className="animate-spin" /> Géolocalisation…</>
                    : <><LocateFixed size={13} /> Géolocaliser {geocodableCount} AO</>}
                </button>
              )}
              {offMapAos.length === 0 ? (
                <p className="text-[12px]" style={{ color: 'var(--text-faint)' }}>Aucun.</p>
              ) : (
                <ul className="space-y-2">
                  {offMapAos.map(a => {
                    const remote = a.work_mode === 'remote'
                    return (
                      <li key={a.id}>
                        <Link to={`/aos/${a.id}`} className="block rounded-md px-2 py-1.5 hover:bg-[var(--surface-2)]">
                          <div className="text-[13px] font-medium truncate" style={{ color: 'var(--text)' }}>{a.title}</div>
                          <div className="text-[11px] flex items-center gap-1" style={{ color: 'var(--text-faint)' }}>
                            {a.clients?.name ? `${a.clients.name} · ` : ''}
                            {remote ? <><Wifi size={10} /> Remote</> : <><MapPinOff size={10} /> Non géolocalisé{a.location ? ` · ${a.location}` : ''}</>}
                          </div>
                        </Link>
                      </li>
                    )
                  })}
                </ul>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// Une section de la liste « Dans le périmètre » (hoistée : identité stable).
function PerimeterSection({ icon: Icon, color, label, items, to, render }) {
  if (!items.length) return null
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wide mb-1.5 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
        <Icon size={12} style={{ color }} /> {label} ({items.length})
      </p>
      <ul className="space-y-1 mb-3">
        {items.slice(0, 20).map(x => (
          <li key={x.id}>
            <Link to={to(x)} className="flex items-center justify-between gap-2 rounded-md px-2 py-1 hover:bg-[var(--surface-2)]">
              <span className="text-[12px] truncate" style={{ color: 'var(--text)' }}>{render(x)}</span>
              <span className="text-[11px] tabular shrink-0" style={{ color: 'var(--text-faint)' }}>{Math.round(x._dist)} km</span>
            </Link>
          </li>
        ))}
        {items.length > 20 && <li className="text-[11px] px-2" style={{ color: 'var(--text-faint)' }}>+{items.length - 20} autres</li>}
      </ul>
    </div>
  )
}

// Panneau « Dans le périmètre » : comptes + listes triées par distance.
function PerimeterPanel({ center, radiusKm, counts, show }) {
  const total = counts.aos.length + counts.clients.length + counts.consultants.length
  return (
    <div>
      <h2 className="text-xs font-semibold uppercase tracking-wide mb-1 flex items-center gap-1.5" style={{ color: 'var(--text-faint)' }}>
        <Target size={13} /> Dans le périmètre
      </h2>
      <p className="text-[12px] mb-3" style={{ color: 'var(--text-muted)' }}>
        <strong>{total}</strong> résultat{total > 1 ? 's' : ''} à ≤ {radiusKm} km de {center.label}.
      </p>
      {total === 0 && <p className="text-[12px]" style={{ color: 'var(--text-faint)' }}>Rien dans ce rayon — élargissez-le.</p>}
      {show.aos && <PerimeterSection icon={Briefcase} color={COLORS.onsite} label="Appels d'offres" items={counts.aos}
        to={x => `/aos/${x.id}`} render={x => x.title} />}
      {show.clients && <PerimeterSection icon={Building2} color={COLORS.client} label="Clients" items={counts.clients}
        to={x => `/clients/${x.id}`} render={x => x.name} />}
      {show.consultants && <PerimeterSection icon={Users} color={COLORS.consultant} label="Consultants" items={counts.consultants}
        to={x => `/consultants/${x.id}`} render={x => x.name} />}
    </div>
  )
}
