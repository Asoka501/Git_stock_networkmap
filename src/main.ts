import ForceGraph3D, { type ForceGraph3DInstance, type LinkObject, type NodeObject } from '3d-force-graph'
import { createIcons, FileJson, Maximize2, Network, RotateCw, Search, Settings2, X } from 'lucide'
import './style.css'
import sampleData from './data/sample.json'

// ===== 型定義（ネットワークJSONスキーマ契約。README参照） =====
export interface NetNode extends NodeObject {
  id: string
  code: string
  name: string
  sector: string
  /** 時価総額（兆円） */
  marketCap: number
  /** 資本金（兆円） */
  capital: number
  /** ロゴ画像URL（https://logoapi.sh/ticker/{code}.svg） */
  logo: string
  logoText?: string
  excerpt?: string
}

export interface NetLink extends LinkObject<NetNode> {
  source: string | NetNode
  target: string | NetNode
  /** 関係値 0-100（線の太さ・粒子量の基準） */
  value: number
  /** 関係種別（主要顧客・供給 / 資本・業務提携 / 金融・融資 / 共同開発・DX など） */
  kind: string
  detail?: string
}

export interface NetworkData {
  nodes: NetNode[]
  links: NetLink[]
}

// ===== セクターパレット =====
interface SectorStyle { color: string; bg: string; text: string }
const SECTORS: Record<string, SectorStyle> = {
  '自動車・モビリティ': { color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.16)', text: '#7dd3fc' },
  '半導体・電機': { color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.16)', text: '#c4b5fd' },
  '商社・エネルギー': { color: '#fbbf24', bg: 'rgba(251, 191, 36, 0.16)', text: '#fcd34d' },
  '通信・IT': { color: '#34d399', bg: 'rgba(52, 211, 153, 0.16)', text: '#6ee7b7' },
  '金融': { color: '#f472b6', bg: 'rgba(244, 114, 182, 0.16)', text: '#f9a8d4' },
  '素材・重工': { color: '#fb923c', bg: 'rgba(251, 146, 60, 0.16)', text: '#fdba74' },
}
const EXTRA_PALETTE = ['#22d3ee', '#c084fc', '#facc15', '#4ade80', '#fb7185', '#f97316']
let extraIdx = 0
function getSectorStyle(sector: string): SectorStyle {
  if (!SECTORS[sector]) {
    const c = EXTRA_PALETTE[extraIdx++ % EXTRA_PALETTE.length]
    SECTORS[sector] = { color: c, bg: 'rgba(148,163,184,0.16)', text: c }
  }
  return SECTORS[sector]
}

// ===== 入力データの正規化（契約スキーマ＋試作スキーマの双方を受ける） =====
function normaliseData(raw: unknown): NetworkData {
  const parsed = raw as { nodes?: Record<string, unknown>[]; links?: Record<string, unknown>[] }
  if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.links)) {
    throw new Error('nodes配列とlinks配列の両方が必要です。')
  }
  const nodes: NetNode[] = parsed.nodes.map((n) => {
    const code = String(n.code ?? n.id ?? '')
    return {
      id: String(n.id ?? code),
      code,
      name: String(n.name ?? code),
      sector: String(n.sector ?? 'その他'),
      marketCap: Number(n.marketCap ?? 1),
      capital: Number(n.capital ?? 0.1),
      logo: String(n.logo ?? `https://logoapi.sh/ticker/${code}.svg`),
      logoText: n.logoText ? String(n.logoText) : undefined,
      excerpt: n.excerpt ? String(n.excerpt) : undefined,
    }
  })
  const links: NetLink[] = parsed.links.map((l) => ({
    source: String((l.source as { id?: string })?.id ?? l.source),
    target: String((l.target as { id?: string })?.id ?? l.target),
    value: Number(l.value ?? l.strength ?? 50),
    kind: String(l.kind ?? l.relation ?? '関連'),
    detail: l.detail ? String(l.detail) : undefined,
  }))
  return { nodes, links }
}

// ===== 状態 =====
const state = {
  rawData: normaliseData(sampleData),
  sizeMetric: 'marketCap' as 'marketCap' | 'capital',
  minValue: 0,
  activeSectors: new Set(Object.keys(SECTORS)),
  selectedNodeId: null as string | null,
  hoverNodeId: null as string | null,
  autoRotate: true,
  rotateAngle: 0,
  highlightNodes: new Set<string>(),
  highlightLinks: new Set<NetLink>(),
}

const container = document.getElementById('graph-container') as HTMLElement
const overlayCanvas = document.getElementById('logo-overlay') as HTMLCanvasElement
const ctx = overlayCanvas.getContext('2d') as CanvasRenderingContext2D
const tooltipEl = document.getElementById('hover-tooltip') as HTMLElement

function resizeOverlay() {
  const dpr = window.devicePixelRatio || 1
  overlayCanvas.width = window.innerWidth * dpr
  overlayCanvas.height = window.innerHeight * dpr
  overlayCanvas.style.width = window.innerWidth + 'px'
  overlayCanvas.style.height = window.innerHeight + 'px'
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
}
resizeOverlay()

function getNodeVal(node: NetNode): number {
  if (state.sizeMetric === 'marketCap') return Math.max(2.5, Math.pow(node.marketCap || 1, 0.72) * 2.2)
  return Math.max(2.5, Math.pow((node.capital || 0.1) * 18, 0.78) * 2.4)
}
function getNodeRadius(node: NetNode): number {
  return Math.cbrt(getNodeVal(node)) * 4.4
}
function getNodeColor(node: NetNode): string {
  const base = getSectorStyle(node.sector).color
  if (state.selectedNodeId) {
    if (node.id === state.selectedNodeId) return '#ffffff'
    if (state.highlightNodes.has(node.id)) return base
    return '#334155'
  }
  return base
}
function linkEndId(end: string | NetNode): string {
  return typeof end === 'object' ? String(end.id) : String(end)
}
function getLinkWidth(link: NetLink): number {
  const base = Math.max(0.6, (link.value / 100) * 5.2)
  if (state.selectedNodeId) return state.highlightLinks.has(link) ? base * 1.35 : 0.35
  return base
}
function getLinkColor(link: NetLink): string {
  if (state.selectedNodeId) {
    return state.highlightLinks.has(link) ? '#38bdf8' : 'rgba(100,116,139,0.12)'
  }
  return link.value >= 80 ? 'rgba(56, 189, 248, 0.58)' : 'rgba(148, 163, 184, 0.28)'
}
function getLinkParticles(link: NetLink): number {
  if (state.selectedNodeId) return state.highlightLinks.has(link) ? 4 : 0
  return link.value >= 75 ? 2 : 0
}
function formatNodeTooltip(node: NetNode): string {
  const valText = state.sizeMetric === 'marketCap'
    ? `時価総額(サンプル): ${node.marketCap}兆円`
    : `資本金(サンプル): ${node.capital}兆円`
  return `<div style="font-weight:700;">${node.name} <span style="color:#94a3b8;font-weight:400;">(${node.code})</span></div>` +
    `<div style="color:${getSectorStyle(node.sector).color};font-size:11px;">${node.sector}</div>` +
    `<div style="color:#cbd5e1;font-size:11px;margin-top:2px;">${valText}</div>`
}
function formatLinkTooltip(link: NetLink): string {
  const nodes = graph.graphData().nodes as NetNode[]
  const nameOf = (id: string) => nodes.find((n) => n.id === id)?.name ?? id
  return `<div style="font-weight:700;color:#7dd3fc;">${nameOf(linkEndId(link.source))} ⇄ ${nameOf(linkEndId(link.target))}</div>` +
    `<div style="margin-top:2px;">種別: ${link.kind} / 関係値: <b>${link.value}</b></div>` +
    (link.detail ? `<div style="color:#cbd5e1;margin-top:2px;">${link.detail}</div>` : '')
}

// ===== 3Dグラフ =====
const graph = (new ForceGraph3D(container) as unknown as ForceGraph3DInstance<NetNode, NetLink>)
  .backgroundColor('#060a14')
  .showNavInfo(false)
  .nodeResolution(20)
  .nodeVal((n) => getNodeVal(n as NetNode))
  .nodeColor((n) => getNodeColor(n as NetNode))
  .nodeOpacity(0.92)
  .nodeLabel((n) => formatNodeTooltip(n as NetNode))
  .linkWidth((l) => getLinkWidth(l as NetLink))
  .linkColor((l) => getLinkColor(l as NetLink))
  .linkOpacity(0.85)
  .linkDirectionalParticles((l) => getLinkParticles(l as NetLink))
  .linkDirectionalParticleWidth((l) => Math.max(1.5, ((l as NetLink).value / 100) * 3.2))
  .linkDirectionalParticleSpeed((l) => 0.003 + ((l as NetLink).value / 100) * 0.006)
  .linkDirectionalParticleColor(() => '#7dd3fc')
  .linkLabel((l) => formatLinkTooltip(l as NetLink))
  .onNodeClick((n) => selectNode(n as NetNode, true))
  .onNodeHover((n) => {
    container.style.cursor = n ? 'pointer' : 'grab'
    state.hoverNodeId = n ? String((n as NetNode).id) : null
  })
  .onBackgroundClick(() => clearSelection())

graph.d3Force('charge')?.strength(-220)
const linkForce = graph.d3Force('link') as unknown as { distance: (fn: (l: NetLink) => number) => void } | undefined
linkForce?.distance((l) => 135 - (l.value || 50) * 0.65)

function refreshGraphStyles() {
  graph
    .nodeVal((n) => getNodeVal(n as NetNode))
    .nodeColor((n) => getNodeColor(n as NetNode))
    .linkWidth((l) => getLinkWidth(l as NetLink))
    .linkColor((l) => getLinkColor(l as NetLink))
    .linkDirectionalParticles((l) => getLinkParticles(l as NetLink))
}

// ===== フィルタ・選択 =====
function getFilteredData(): NetworkData {
  const nodes = state.rawData.nodes
    .filter((n) => state.activeSectors.has(n.sector))
    .map((n) => ({ ...n }))
  const validIds = new Set(nodes.map((n) => n.id))
  const links = state.rawData.links
    .filter((l) => validIds.has(linkEndId(l.source)) && validIds.has(linkEndId(l.target)) && l.value >= state.minValue)
    .map((l) => ({ ...l, source: linkEndId(l.source), target: linkEndId(l.target) }))
  return { nodes, links }
}

function updateGraphData() {
  const data = getFilteredData()
  graph.graphData(data)
  ;(document.getElementById('stat-nodes') as HTMLElement).textContent = String(data.nodes.length)
  ;(document.getElementById('stat-links') as HTMLElement).textContent = String(data.links.length)
  if (state.selectedNodeId && !data.nodes.some((n) => n.id === state.selectedNodeId)) {
    clearSelection()
  } else if (state.selectedNodeId) {
    const node = data.nodes.find((n) => n.id === state.selectedNodeId)
    if (node) {
      recomputeHighlight(node)
      refreshGraphStyles()
    }
  }
}

function recomputeHighlight(node: NetNode) {
  state.highlightNodes.clear()
  state.highlightLinks.clear()
  if (!node) return
  state.highlightNodes.add(node.id)
  const { links } = graph.graphData() as unknown as NetworkData
  links.forEach((link) => {
    const sId = linkEndId(link.source)
    const tId = linkEndId(link.target)
    if (sId === node.id || tId === node.id) {
      state.highlightLinks.add(link)
      state.highlightNodes.add(sId)
      state.highlightNodes.add(tId)
    }
  })
}

function selectNode(node: NetNode, moveCamera = true) {
  if (!node) return
  state.selectedNodeId = node.id
  setAutoRotate(false)
  recomputeHighlight(node)
  refreshGraphStyles()
  renderInspector(node)
  if (moveCamera && typeof node.x === 'number') {
    const dist = 145
    const hypot = Math.hypot(node.x, node.y ?? 0, node.z ?? 0) || 1
    const ratio = 1 + dist / hypot
    graph.cameraPosition(
      { x: node.x * ratio, y: (node.y ?? 0) * ratio + 15, z: (node.z ?? 0) * ratio },
      { x: node.x, y: node.y ?? 0, z: node.z ?? 0 },
      850,
    )
  }
}

function clearSelection() {
  state.selectedNodeId = null
  state.highlightNodes.clear()
  state.highlightLinks.clear()
  document.getElementById('inspector-panel')?.classList.add('hidden')
  refreshGraphStyles()
}

function renderInspector(node: NetNode) {
  const panel = document.getElementById('inspector-panel') as HTMLElement
  const style = getSectorStyle(node.sector)

  const logoEl = document.getElementById('ins-logo') as HTMLElement
  logoEl.style.background = `linear-gradient(135deg, ${style.color}, #e2e8f0)`
  logoEl.innerHTML = ''
  const img = new Image()
  img.src = node.logo
  img.alt = node.logoText ?? node.code
  img.className = 'w-full h-full object-contain bg-white'
  img.onerror = () => {
    img.remove()
    logoEl.textContent = node.logoText || node.code.slice(0, 2)
  }
  logoEl.appendChild(img)

  ;(document.getElementById('ins-code') as HTMLElement).textContent = node.code
  const secEl = document.getElementById('ins-sector') as HTMLElement
  secEl.textContent = node.sector
  secEl.style.backgroundColor = style.bg
  secEl.style.color = style.text
  ;(document.getElementById('ins-name') as HTMLElement).textContent = node.name
  ;(document.getElementById('ins-mcap') as HTMLElement).textContent = `${node.marketCap} 兆円`
  ;(document.getElementById('ins-cap') as HTMLElement).textContent = `${node.capital} 兆円`
  ;(document.getElementById('ins-excerpt') as HTMLElement).textContent =
    node.excerpt || '有価証券報告書の関係情報（サンプル）'

  const { nodes, links } = graph.graphData() as unknown as NetworkData
  const nodeMap = new Map(nodes.map((n) => [n.id, n]))
  const connected: { peer: NetNode; link: NetLink }[] = []
  links.forEach((l) => {
    const sId = linkEndId(l.source)
    const tId = linkEndId(l.target)
    if (sId === node.id && nodeMap.has(tId)) connected.push({ peer: nodeMap.get(tId) as NetNode, link: l })
    else if (tId === node.id && nodeMap.has(sId)) connected.push({ peer: nodeMap.get(sId) as NetNode, link: l })
  })
  connected.sort((a, b) => b.link.value - a.link.value)

  ;(document.getElementById('ins-conn-count') as HTMLElement).textContent = `${connected.length}社`
  const listEl = document.getElementById('ins-connections') as HTMLElement
  listEl.innerHTML = ''
  if (connected.length === 0) {
    listEl.innerHTML = '<div class="text-xs text-slate-500 py-2">現在のフィルタ条件に一致する接続はありません。</div>'
  } else {
    connected.forEach(({ peer, link }) => {
      const pStyle = getSectorStyle(peer.sector)
      const item = document.createElement('button')
      item.type = 'button'
      item.className = 'w-full text-left bg-slate-900/75 hover:bg-slate-800/90 border border-slate-800 rounded-xl p-2.5 transition group'
      item.innerHTML = `
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 min-w-0">
            <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background:${pStyle.color}"></span>
            <span class="text-xs font-semibold text-slate-100 truncate group-hover:text-sky-300">${peer.name}</span>
          </div>
          <span class="text-[11px] font-mono font-bold text-sky-300 shrink-0">${link.value}</span>
        </div>
        <div class="mt-1 flex items-center justify-between text-[10px] text-slate-400">
          <span>${link.kind}</span>
          <span class="truncate max-w-[140px]">${link.detail || ''}</span>
        </div>
        <div class="mt-1.5 h-1 w-full bg-slate-800 rounded-full overflow-hidden">
          <div class="h-full rounded-full" style="width:${Math.min(100, link.value)}%;background:${pStyle.color};"></div>
        </div>`
      item.addEventListener('click', () => selectNode(peer, true))
      listEl.appendChild(item)
    })
  }
  panel.classList.remove('hidden')
}

// ===== セクター凡例 =====
function renderSectorLegend() {
  const listEl = document.getElementById('sector-list') as HTMLElement
  listEl.innerHTML = ''
  Object.entries(SECTORS).forEach(([sector, style]) => {
    const active = state.activeSectors.has(sector)
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = `w-full flex items-center justify-between px-2.5 py-1.5 rounded-xl text-xs transition border ${
      active
        ? 'bg-slate-900/90 border-slate-700/80 text-slate-100'
        : 'bg-slate-950/40 border-transparent text-slate-500 opacity-60'
    }`
    btn.innerHTML = `
      <span class="flex items-center gap-2">
        <span class="w-2.5 h-2.5 rounded-full" style="background:${style.color};box-shadow:${active ? '0 0 8px ' + style.color : 'none'}"></span>
        <span>${sector}</span>
      </span>
      <span class="text-[10px] font-mono">${active ? 'ON' : 'OFF'}</span>`
    btn.addEventListener('click', () => {
      if (state.activeSectors.has(sector)) {
        if (state.activeSectors.size > 1) state.activeSectors.delete(sector)
      } else {
        state.activeSectors.add(sector)
      }
      renderSectorLegend()
      updateGraphData()
    })
    listEl.appendChild(btn)
  })
}

// ===== ロゴ/ラベル オーバーレイ（ズーム連動LOD） =====
const logoImageCache = new Map<string, { img: HTMLImageElement; loaded: boolean }>()
function getLogoImage(node: NetNode): { img: HTMLImageElement; loaded: boolean } {
  let entry = logoImageCache.get(node.code)
  if (!entry) {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    entry = { img, loaded: false }
    img.onload = () => { if (entry) entry.loaded = true }
    img.src = node.logo
    logoImageCache.set(node.code, entry)
  }
  return entry
}

function drawRoundRect(context: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  context.beginPath()
  context.moveTo(x + r, y)
  context.lineTo(x + w - r, y)
  context.quadraticCurveTo(x + w, y, x + w, y + r)
  context.lineTo(x + w, y + h - r)
  context.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  context.lineTo(x + r, y + h)
  context.quadraticCurveTo(x, y + h, x, y + h - r)
  context.lineTo(x, y + r)
  context.quadraticCurveTo(x, y, x + r, y)
  context.closePath()
}

function animate() {
  requestAnimationFrame(animate)

  if (state.autoRotate) {
    state.rotateAngle += 0.0022
    const dist = 410
    graph.cameraPosition(
      { x: dist * Math.sin(state.rotateAngle), y: 70, z: dist * Math.cos(state.rotateAngle) },
      { x: 0, y: 0, z: 0 },
      0,
    )
  }

  ctx.clearRect(0, 0, window.innerWidth, window.innerHeight)
  const camera = graph.camera()
  if (!camera) return
  const { nodes } = graph.graphData() as unknown as NetworkData
  const camPos = camera.position

  for (const n of nodes) {
    if (typeof n.x !== 'number') continue
    const dx = camPos.x - n.x
    const dy = camPos.y - (n.y ?? 0)
    const dz = camPos.z - (n.z ?? 0)
    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz)

    const isSelected = n.id === state.selectedNodeId
    const isNeighbor = !!state.selectedNodeId && state.highlightNodes.has(n.id)
    const isHovered = n.id === state.hoverNodeId
    const isMajor = (n.marketCap || 0) >= 13
    const showLogo = dist < 310 || isSelected || isHovered || isNeighbor
    const showLabel = dist < 480 || isSelected || isHovered || isNeighbor || isMajor
    if (!showLogo && !showLabel) continue

    const screen = graph.graph2ScreenCoords(n.x, n.y ?? 0, n.z ?? 0)
    if (!screen || screen.x < -60 || screen.x > window.innerWidth + 60 || screen.y < -60 || screen.y > window.innerHeight + 60) continue

    const style = getSectorStyle(n.sector)
    const dimmed = !!state.selectedNodeId && !isSelected && !isNeighbor
    const baseAlpha = dimmed ? 0.16 : 0.95

    if (showLogo) {
      const zoomFactor = Math.max(0, Math.min(1, (320 - dist) / 130))
      const alpha = isSelected || isHovered ? 1 : Math.min(baseAlpha, Math.max(isNeighbor ? 0.88 : 0, zoomFactor))
      if (alpha > 0.08) {
        const radius = isSelected ? 16 : dist < 200 ? 15 : 12
        ctx.save()
        ctx.globalAlpha = alpha
        ctx.beginPath()
        ctx.arc(screen.x, screen.y, radius, 0, Math.PI * 2)
        ctx.fillStyle = '#0b1222'
        ctx.fill()
        ctx.lineWidth = isSelected ? 2.5 : 1.8
        ctx.strokeStyle = isSelected ? '#ffffff' : style.color
        ctx.stroke()

        const logoEntry = getLogoImage(n)
        if (logoEntry.loaded) {
          ctx.save()
          ctx.beginPath()
          ctx.arc(screen.x, screen.y, radius - 2, 0, Math.PI * 2)
          ctx.clip()
          ctx.drawImage(logoEntry.img, screen.x - (radius - 2), screen.y - (radius - 2), (radius - 2) * 2, (radius - 2) * 2)
          ctx.restore()
        } else {
          ctx.fillStyle = '#f8fafc'
          ctx.font = `700 ${radius >= 15 ? 10 : 9}px Inter, sans-serif`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'middle'
          ctx.fillText(n.logoText || n.code.slice(0, 2), screen.x, screen.y + 0.5)
        }

        ctx.font = `${isSelected ? 700 : 500} 11px "Noto Sans JP", Inter, sans-serif`
        const textWidth = ctx.measureText(n.name).width
        const px = 6
        const bx = screen.x - textWidth / 2 - px
        const by = screen.y + radius + 4
        ctx.fillStyle = 'rgba(6, 10, 20, 0.84)'
        drawRoundRect(ctx, bx, by, textWidth + px * 2, 18, 6)
        ctx.fill()
        ctx.fillStyle = isSelected ? '#ffffff' : '#e2e8f0'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(n.name, screen.x, by + 9.5)
        ctx.restore()
      }
    } else if (showLabel) {
      ctx.save()
      ctx.globalAlpha = dimmed ? 0.14 : 0.82
      ctx.font = '500 10.5px "Noto Sans JP", Inter, sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillStyle = '#cbd5e1'
      ctx.fillText(n.name, screen.x, screen.y + 11)
      ctx.restore()
    }
  }
}

// ===== コントロール =====
function setAutoRotate(enabled: boolean) {
  state.autoRotate = enabled
  const btn = document.getElementById('btn-autorotate') as HTMLElement
  btn.textContent = enabled ? '回転: ON' : '回転: OFF'
  btn.className = enabled
    ? 'px-2.5 py-1.5 rounded-xl text-xs font-medium bg-sky-500/20 text-sky-300 border border-sky-400/30 hover:bg-sky-500/30 transition'
    : 'px-2.5 py-1.5 rounded-xl text-xs font-medium bg-slate-800/80 text-slate-300 border border-slate-700/70 hover:bg-slate-700/80 transition'
}

container.addEventListener('pointerdown', () => {
  if (state.autoRotate) setAutoRotate(false)
})
document.getElementById('btn-autorotate')?.addEventListener('click', () => setAutoRotate(!state.autoRotate))
document.getElementById('btn-reset')?.addEventListener('click', () => {
  clearSelection()
  graph.zoomToFit(700, 50)
})
document.getElementById('btn-close-inspector')?.addEventListener('click', clearSelection)

const metricOn = 'py-1.5 rounded-lg text-xs font-medium bg-sky-500 text-slate-950 transition'
const metricOff = 'py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white transition'
document.getElementById('metric-mcap')?.addEventListener('click', () => {
  state.sizeMetric = 'marketCap'
  ;(document.getElementById('metric-mcap') as HTMLElement).className = metricOn
  ;(document.getElementById('metric-cap') as HTMLElement).className = metricOff
  refreshGraphStyles()
})
document.getElementById('metric-cap')?.addEventListener('click', () => {
  state.sizeMetric = 'capital'
  ;(document.getElementById('metric-cap') as HTMLElement).className = metricOn
  ;(document.getElementById('metric-mcap') as HTMLElement).className = metricOff
  refreshGraphStyles()
})

document.getElementById('threshold-slider')?.addEventListener('input', (e) => {
  state.minValue = Number((e.target as HTMLInputElement).value)
  ;(document.getElementById('threshold-value') as HTMLElement).textContent = `${state.minValue} 以上`
  updateGraphData()
})
document.getElementById('btn-all-sectors')?.addEventListener('click', () => {
  state.activeSectors = new Set(Object.keys(SECTORS))
  renderSectorLegend()
  updateGraphData()
})
document.getElementById('btn-toggle-controls')?.addEventListener('click', () => {
  document.getElementById('left-panel')?.classList.toggle('hidden')
})

// 検索
const searchInput = document.getElementById('search-input') as HTMLInputElement
const searchResults = document.getElementById('search-results') as HTMLElement
searchInput.addEventListener('input', () => {
  const q = searchInput.value.trim().toLowerCase()
  if (!q) {
    searchResults.classList.add('hidden')
    return
  }
  const { nodes } = graph.graphData() as unknown as NetworkData
  const matches = nodes.filter(
    (n) => n.name.toLowerCase().includes(q) || n.code.toLowerCase().includes(q) || n.sector.toLowerCase().includes(q),
  )
  searchResults.innerHTML = ''
  if (matches.length === 0) {
    searchResults.innerHTML = '<div class="px-3 py-2 text-xs text-slate-400">一致する銘柄がありません</div>'
  } else {
    matches.slice(0, 8).forEach((n) => {
      const item = document.createElement('button')
      item.type = 'button'
      item.className = 'w-full text-left px-3 py-2 hover:bg-slate-800/90 flex items-center justify-between text-xs border-b border-slate-800/60 last:border-0'
      item.innerHTML = `
        <span class="flex items-center gap-2">
          <span class="w-2 h-2 rounded-full" style="background:${getSectorStyle(n.sector).color}"></span>
          <span class="text-slate-100 font-medium">${n.name}</span>
        </span>
        <span class="text-[10px] font-mono text-slate-400">${n.code}</span>`
      item.addEventListener('click', () => {
        selectNode(n, true)
        searchInput.value = ''
        searchResults.classList.add('hidden')
      })
      searchResults.appendChild(item)
    })
  }
  searchResults.classList.remove('hidden')
})
document.addEventListener('click', (e) => {
  if (!searchInput.contains(e.target as Node) && !searchResults.contains(e.target as Node)) {
    searchResults.classList.add('hidden')
  }
})

// JSON読込モーダル
const jsonModal = document.getElementById('json-modal') as HTMLElement
const jsonError = document.getElementById('json-error') as HTMLElement
function closeModal() {
  jsonModal.classList.add('hidden')
  jsonModal.classList.remove('flex')
}
document.getElementById('btn-json-modal')?.addEventListener('click', () => {
  jsonError.classList.add('hidden')
  tooltipEl.style.display = 'none'
  jsonModal.classList.remove('hidden')
  jsonModal.classList.add('flex')
})
document.getElementById('btn-close-modal')?.addEventListener('click', closeModal)
document.getElementById('btn-restore-sample')?.addEventListener('click', () => {
  state.rawData = normaliseData(sampleData)
  state.activeSectors = new Set(Object.keys(SECTORS))
  renderSectorLegend()
  updateGraphData()
  closeModal()
  graph.zoomToFit(700, 50)
})
document.getElementById('btn-apply-json')?.addEventListener('click', () => {
  const raw = (document.getElementById('json-input') as HTMLTextAreaElement).value.trim()
  try {
    const parsed = JSON.parse(raw)
    const data = normaliseData(parsed)
    data.nodes.forEach((n) => {
      getSectorStyle(n.sector)
      state.activeSectors.add(n.sector)
    })
    state.rawData = data
    clearSelection()
    renderSectorLegend()
    updateGraphData()
    closeModal()
    graph.zoomToFit(700, 50)
  } catch (err) {
    jsonError.textContent = 'JSON形式エラー: ' + (err as Error).message
    jsonError.classList.remove('hidden')
  }
})

window.addEventListener('resize', () => {
  graph.width(window.innerWidth).height(window.innerHeight)
  resizeOverlay()
})

// ===== 起動 =====
createIcons({ icons: { Network, Search, RotateCw, Maximize2, FileJson, X, Settings2 } })
renderSectorLegend()
updateGraphData()
animate()

// getNodeRadius は将来のカスタムノード描画用に保持（現行は nodeVal ベース）
export { getNodeRadius }
