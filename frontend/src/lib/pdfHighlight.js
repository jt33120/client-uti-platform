// Rendu du CV (PDF) + ancrage géométrique du surlignage.
//
// Objectif : afficher le VRAI PDF du candidat tel quel, puis poser par-dessus des
// zones de surlignage (façon marqueur humain) reliées aux commentaires de l'IA.
// L'IA cite le CV entre « … » dans ses justifications ; on retrouve ces extraits
// dans la couche texte du PDF et on calcule les rectangles exacts à surligner.
//
// Tout est fait côté client à partir des octets du PDF (servis par le backend,
// même chemin auth/CORS que le reste de l'API) — aucune donnée ne quitte la page.
import * as pdfjsLib from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl

// ── Rendu ────────────────────────────────────────────────────────────────────
// Rend chaque page en image (JPEG blob → object URL, léger en mémoire) et extrait
// la géométrie de chaque fragment de texte (boîte en px du rendu). Les positions
// servent ensuite à tracer les surlignages, en %, donc responsives.
export async function renderCvPdf(bytes, { targetWidth = 1240, maxPages = 12 } = {}) {
  const doc = await pdfjsLib.getDocument({
    data: bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes),
    disableAutoFetch: true,
    isEvalSupported: false,
  }).promise

  const pages = []
  const n = Math.min(doc.numPages, maxPages)
  for (let p = 1; p <= n; p++) {
    const page = await doc.getPage(p)
    const base = page.getViewport({ scale: 1 })
    // Rendu net (≈2× la largeur d'affichage) sans exploser la mémoire.
    const scale = Math.min(3, Math.max(1.2, targetWidth / base.width))
    const viewport = page.getViewport({ scale })

    const canvas = document.createElement('canvas')
    canvas.width = Math.ceil(viewport.width)
    canvas.height = Math.ceil(viewport.height)
    const ctx = canvas.getContext('2d', { alpha: false })
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    await page.render({ canvasContext: ctx, viewport }).promise

    const imgUrl = await new Promise((resolve) =>
      canvas.toBlob((b) => resolve(b ? URL.createObjectURL(b) : null), 'image/jpeg', 0.88)
    )

    const tc = await page.getTextContent()
    const items = []
    for (const it of tc.items) {
      if (typeof it.str !== 'string' || !it.str) continue
      // Position de la ligne de base dans l'espace du rendu.
      const tm = pdfjsLib.Util.transform(viewport.transform, it.transform)
      const fh = Math.hypot(tm[2], tm[3]) || (it.height || 0) * scale
      const asc = fh * 0.82   // au-dessus de la ligne de base (majuscules)
      const desc = fh * 0.24  // sous la ligne de base (jambages)
      items.push({
        str: it.str,
        hasEOL: !!it.hasEOL,
        l: tm[4],
        t: tm[5] - asc,
        w: (it.width || 0) * scale,
        h: asc + desc,
      })
    }

    pages.push({ pageNum: p, wpx: canvas.width, hpx: canvas.height, imgUrl, items })
    canvas.width = canvas.height = 0  // libère la mémoire du canvas
  }

  try { doc.destroy() } catch { /* noop */ }
  return pages
}

export function revokePages(pages) {
  ;(pages || []).forEach((pg) => { if (pg.imgUrl) { try { URL.revokeObjectURL(pg.imgUrl) } catch { /* noop */ } } })
}

// ── Normalisation & index de recherche ───────────────────────────────────────
// Minuscule + espaces compressés, en gardant une carte index_normalisé → index_brut
// pour retrouver la position réelle d'un extrait trouvé.
function normWithMap(raw) {
  let norm = ''
  const map = []
  let prevSpace = true // évite l'espace de tête
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i]
    if (/\s/.test(c)) {
      if (prevSpace) continue
      norm += ' '; map.push(i); prevSpace = true
    } else {
      norm += c.toLowerCase(); map.push(i); prevSpace = false
    }
  }
  while (norm.endsWith(' ')) { norm = norm.slice(0, -1); map.pop() }
  return { norm, map }
}

const normOne = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim()

// Concatène les fragments d'une page en un texte cherchable, en gardant l'intervalle
// [start,end) de chaque item (pour remonter des positions de caractères aux items).
function buildPageIndex(pg) {
  let text = ''
  const ranges = []
  pg.items.forEach((it, i) => {
    const s = it.str || ''
    if (!s) { if (it.hasEOL) text += '\n'; return }
    const start = text.length
    text += s
    ranges.push({ start, end: text.length, i })
    text += it.hasEOL ? '\n' : ' '
  })
  const { norm, map } = normWithMap(text)
  return { norm, map, ranges }
}

// Trouve un extrait normalisé dans une page → indices des items couverts.
function locate(idx, fragNorm) {
  const at = idx.norm.indexOf(fragNorm)
  if (at < 0) return null
  const rawStart = idx.map[at]
  const rawEnd = idx.map[at + fragNorm.length - 1] + 1
  const hits = idx.ranges.filter((r) => r.start < rawEnd && r.end > rawStart).map((r) => r.i)
  return hits.length ? hits : null
}

// Fusionne les boîtes des items en rectangles « par ligne » : un trait de marqueur
// par ligne visuelle, comme un humain qui surligne.
function itemsToRects(items, idxs) {
  const boxes = idxs.map((i) => items[i]).filter(Boolean)
  if (!boxes.length) return []
  boxes.sort((a, b) => (a.t - b.t) || (a.l - b.l))
  const lines = []
  for (const b of boxes) {
    const tol = Math.max(4, b.h * 0.6)
    const line = lines.find((L) => Math.abs((L.t + L.bot) / 2 - (b.t + b.h / 2)) <= tol)
    if (line) {
      line.l = Math.min(line.l, b.l)
      line.r = Math.max(line.r, b.l + b.w)
      line.t = Math.min(line.t, b.t)
      line.bot = Math.max(line.bot, b.t + b.h)
    } else {
      lines.push({ l: b.l, r: b.l + b.w, t: b.t, bot: b.t + b.h })
    }
  }
  return lines.map((L) => ({ l: L.l, t: L.t, w: L.r - L.l, h: L.bot - L.t }))
}

// ── Matching des annotations sur le PDF ───────────────────────────────────────
// Pour chaque critère (annotation) et chacune de ses citations, cherche l'extrait
// dans les pages et produit des rectangles de surlignage (en %, responsives).
// `annos` : [{ idx, color, quotes:[string] }]. Retourne des « marks ».
export function matchAnnotations(pages, annos) {
  if (!pages?.length) return []
  const idxs = pages.map(buildPageIndex)
  const marks = []
  const seen = new Set() // (page:l:t) → évite les surlignages en double

  for (const a of annos) {
    let anchored = false
    ;(a.quotes || []).forEach((rawq, qi) => {
      // Retire les jetons de pseudonymisation ([NOM], [EMAIL], …) absents du PDF réel.
      const cleaned = rawq.replace(/\[[^\]]{1,24}\]/g, ' ')
      const words = cleaned.split(/\s+/).filter(Boolean)
      if (words.length < 2) return
      const lens = [...new Set([words.length, 16, 12, 8, 5])]
        .filter((k) => k >= 3 && k <= words.length)
        .sort((x, y) => y - x)

      for (let pi = 0; pi < pages.length; pi++) {
        let placed = false
        for (const k of lens) {
          const frag = normOne(words.slice(0, k).join(' '))
          if (frag.length < 8) continue
          const hit = locate(idxs[pi], frag)
          if (!hit) continue
          const rects = itemsToRects(pages[pi].items, hit)
          if (!rects.length) continue
          const dedupe = `${pi}:${Math.round(rects[0].l)}:${Math.round(rects[0].t)}`
          if (seen.has(dedupe)) { placed = true; break }
          seen.add(dedupe)
          marks.push({
            annoIdx: a.idx,
            page: pi,
            color: a.color,
            key: `${a.idx}-${qi}`,
            anchor: !anchored,
            rects: rects.map((r) => ({
              lPct: (r.l / pages[pi].wpx) * 100,
              tPct: (r.t / pages[pi].hpx) * 100,
              wPct: (r.w / pages[pi].wpx) * 100,
              hPct: (r.h / pages[pi].hpx) * 100,
            })),
          })
          anchored = true
          placed = true
          break
        }
        if (placed) break
      }
    })
  }
  return marks
}
