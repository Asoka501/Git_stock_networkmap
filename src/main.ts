import ForceGraph3D, { type ForceGraph3DInstance } from '3d-force-graph'
import {
  Code2,
  createIcons,
  FileJson,
  LocateFixed,
  Maximize2,
  Network,
  RotateCw,
  Search,
  SlidersHorizontal,
} from 'lucide'
import './style.css'
import {
  buildIndexFromNetwork,
  fetchCompanyDetail,
  fetchFullNetwork,
  getSampleDataset,
  loadInitialDataset,
  normaliseData,
} from './dataLoader'
import { buildIframeSnippet, parseEmbedConfig } from './embedConfig'
import { escapeHtml, extractCoreSubgraph, extractHopSubgraph, linkEndId } from './graphUtils'
import { LogoCache } from './logoLoader'
import { getNodeSectorColor, getSectorStyle, sortSectorsByOrder } from './sectorPalette'
import type {
  DataMeta,
  IndexEntry,
  NetLink,
  NetNode,
  NetworkData,
  SizeMetric,
  SourceFilter,
  ViewScope,
} from './types'

export type { NetNode, NetLink, NetworkData }

// ===== URL埋め込み設定 =====
const config = parseEmbedConfig()
if (config.embed) {
  document.body.classList.add('is-embed')
}

// ===== 状態 =====
const initialSample = getSampleDataset()
const state = {
  meta: initialSample.meta as DataMeta,
  index: initialSample.index as IndexEntry[],
  coreData: initialSample.coreData as NetworkData,
  fullData: initialSample.fullData as NetworkData | null,
  isSplitLive: false,

  scope: config.mode as ViewScope,
  hops: config.hops as 1 | 2,
  focusScopeId: config.focus as string | null,
  holdings: new Set<string>(config.holdings),
  expandedSeeds: new Set<string>(),

  sizeMetric: config.metric as SizeMetric,
  minValue: config.minValue,
  availableSectors: [] as string[],
  activeSectors: new Set<string>(),
  availableKinds: [] as string[],
  activeKinds: new Set<string>(),
  hasSourceTypes: false,
  sourceFilter: config.sourceFilter as SourceFilter,

  selectedNodeId: null as string | null,
  hoverNodeId: null as string | null,
  autoRotate: config.rotate,
  rotateAngle: 0,
  zoomSubSectorMode: false,
  highlightNodes: new Set<string>(),
  highlightLinks: new Set<NetLink>(),
  visibleNodeCount: 0,
}

const container = document.getElementById('graph-container') as HTMLElement
const overlayCanvas = document.getElementById('logo-overlay') as HTMLCanvasElement
const ctx = overlayCanvas.getContext('2d') as CanvasRenderingContext2D
const tooltipEl = document.getElementById('hover-tooltip') as HTMLElement
const loadingBadge = document.getElementById('loading-badge') as HTMLElement
const loadingText = document.getElementById('loading-text') as HTMLElement
const zoomSubSectorBadge = document.getElementById('zoom-subsector-badge') as HTMLElement | null

let viewportWidth = container.clientWidth || window.innerWidth
let viewportHeight = container.clientHeight || window.innerHeight

function resizeOverlay(w: number, h: number) {
  viewportWidth = Math.max(1, w)
  viewportHeight = Math.max(1, h)
  const dpr = window.devicePixelRatio || 1
  overlayCanvas.width = viewportWidth * dpr
  overlayCanvas.height = viewportHeight * dpr
  overlayCanvas.style.width = viewportWidth + 'px'
  overlayCanvas.style.height = viewportHeight + 'px'
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
}
resizeOverlay(viewportWidth, viewportHeight)

function setLoading(show: boolean, message = 'データを読み込み中...') {
  loadingText.textContent = message
  loadingBadge.classList.toggle('hidden', !show)
}

function getNodeVal(node: NetNode): number {
  if (state.sizeMetric === 'marketCap') {
    return Math.max(2.5, Math.pow(node.marketCap || 1, 0.72) * 2.2)
  }
  return Math.max(2.5, Math.pow((node.capital || 0.1) * 18, 0.78) * 2.4)
}

function getNodeRadius(node: NetNode): number {
  return Math.cbrt(getNodeVal(node)) * 4.4
}

function isHolding(id: string): boolean {
  return state.holdings.has(id)
}

function getNodeColor(node: NetNode): string {
  const base = getNodeSectorColor(node.sector, node.subSector, state.zoomSubSectorMode)
  if (state.selectedNodeId) {
    if (node.id === state.selectedNodeId) return '#ffffff'
    if (state.highlightNodes.has(node.id)) return base
    if (isHolding(node.id)) return '#fbbf24'
    return '#334155'
  }
  return base
}

function getLinkWidth(link: NetLink): number {
  const base = Math.max(0.6, (link.value / 100) * 5.2)
  if (state.selectedNodeId) return state.highlightLinks.has(link) ? base * 1.35 : 0.3
  return base
}

function getLinkColor(link: NetLink): string {
  if (state.selectedNodeId) {
    return state.highlightLinks.has(link) ? '#38bdf8' : 'rgba(100,116,139,0.1)'
  }
  if (link.sourceType === 'llm') {
    return link.value >= 75 ? 'rgba(167, 139, 250, 0.56)' : 'rgba(167, 139, 250, 0.25)'
  }
  return link.value >= 80 ? 'rgba(56, 189, 248, 0.58)' : 'rgba(148, 163, 184, 0.28)'
}

function getLinkParticles(link: NetLink): number {
  if (state.selectedNodeId) return state.highlightLinks.has(link) ? 4 : 0
  if (state.visibleNodeCount > 300) return link.value >= 88 ? 1 : 0
  return link.value >= 75 ? 2 : 0
}

function formatMetricValue(val: number): string {
  if (!val || val <= 0) return '—'
  if (val >= 1) return `${val.toFixed(val >= 10 ? 1 : 2)} 兆円`
  return `${Math.round(val * 10000).toLocaleString()} 億円`
}

function formatNodeTooltip(node: NetNode): string {
  const suffix = state.meta.dataType === 'sample' ? '(サンプル)' : ''
  const valText =
    state.sizeMetric === 'marketCap'
      ? `時価総額${suffix}: ${formatMetricValue(node.marketCap)}`
      : `資本金${suffix}: ${formatMetricValue(node.capital)}`
  const holdTag = isHolding(node.id)
    ? '<span style="margin-left:6px;padding:1px 5px;border-radius:4px;background:rgba(245,158,11,0.22);color:#fcd34d;font-size:10px;">★ 保有</span>'
    : ''
  const sectorColor = getNodeSectorColor(node.sector, node.subSector, state.zoomSubSectorMode)
  const sectorLabel =
    node.subSector && node.subSector !== node.sector
      ? `${node.sector} › ${node.subSector}`
      : node.sector
  return (
    `<div style="font-weight:700;">${escapeHtml(node.name)} <span style="color:#94a3b8;font-weight:400;">(${escapeHtml(node.code)})</span>${holdTag}</div>` +
    `<div style="color:${sectorColor};font-size:11px;">${escapeHtml(sectorLabel)}</div>` +
    `<div style="color:#cbd5e1;font-size:11px;margin-top:2px;">${escapeHtml(valText)}</div>`
  )
}

function formatLinkTooltip(link: NetLink): string {
  const nodes = graph.graphData().nodes as NetNode[]
  const nameOf = (id: string) => nodes.find((n) => n.id === id)?.name ?? id
  const srcTag =
    link.sourceType === 'llm'
      ? ' <span style="color:#c4b5fd;font-size:10px;">[LLM抽出]</span>'
      : link.sourceType === 'table'
        ? ' <span style="color:#7dd3fc;font-size:10px;">[定型表]</span>'
        : ''
  return (
    `<div style="font-weight:700;color:#7dd3fc;">${escapeHtml(nameOf(linkEndId(link.source)))} ⇄ ${escapeHtml(nameOf(linkEndId(link.target)))}</div>` +
    `<div style="margin-top:2px;">種別: ${escapeHtml(link.kind)}${srcTag} / 関係値: <b>${link.value}</b></div>` +
    (link.detail ? `<div style="color:#cbd5e1;margin-top:2px;">${escapeHtml(link.detail)}</div>` : '')
  )
}

// ===== 3Dグラフ =====
const graph = (new ForceGraph3D(container) as unknown as ForceGraph3DInstance<NetNode, NetLink>)
  .width(viewportWidth)
  .height(viewportHeight)
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
  .linkDirectionalParticleColor((l) => ((l as NetLink).sourceType === 'llm' ? '#c4b5fd' : '#7dd3fc'))
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

// ===== 遅延ロード（全上場 network.json） =====
async function ensureFullData(): Promise<NetworkData> {
  if (state.fullData) return state.fullData
  if (!state.isSplitLive) return state.coreData
  setLoading(true, '全上場ネットワークデータを読み込み中...')
  try {
    const loaded = await fetchFullNetwork()
    if (loaded) {
      state.fullData = loaded
      syncCatalogFromData(loaded, false)
      return loaded
    }
    return state.coreData
  } finally {
    setLoading(false)
  }
}

function syncCatalogFromData(data: NetworkData, resetSelections = true) {
  const sectorSet = new Set<string>()
  const kindSet = new Set<string>()
  let hasSource = false

  for (const n of data.nodes) {
    getSectorStyle(n.sector)
    sectorSet.add(n.sector)
  }
  for (const l of data.links) {
    kindSet.add(l.kind)
    if (l.sourceType) hasSource = true
  }

  state.availableSectors = sortSectorsByOrder(sectorSet)
  state.availableKinds = Array.from(kindSet)
  state.hasSourceTypes = hasSource

  if (resetSelections || state.activeSectors.size === 0) {
    state.activeSectors = new Set(state.availableSectors)
  } else {
    for (const s of state.availableSectors) {
      if (!state.activeSectors.has(s)) state.activeSectors.add(s)
    }
  }

  if (resetSelections || state.activeKinds.size === 0) {
    if (config.kinds.size > 0 && resetSelections) {
      const matched = state.availableKinds.filter((k) =>
        Array.from(config.kinds).some((ck) => k.includes(ck)),
      )
      state.activeKinds = new Set(matched.length > 0 ? matched : state.availableKinds)
    } else {
      state.activeKinds = new Set(state.availableKinds)
    }
  } else {
    for (const k of state.availableKinds) {
      if (!state.activeKinds.has(k)) state.activeKinds.add(k)
    }
  }

  document.getElementById('source-filter-section')?.classList.toggle('hidden', !state.hasSourceTypes)
  renderSectorLegend()
  renderKindFilters()
}

// ===== スコープ＆フィルタ適用 =====
function getScopedBaseData(): NetworkData {
  const source = state.fullData ?? state.coreData

  if (state.scope === 'holdings' && state.holdings.size > 0) {
    const seeds = new Set(state.holdings)
    if (state.focusScopeId) seeds.add(state.focusScopeId)
    return extractHopSubgraph(source, seeds, state.hops, state.expandedSeeds)
  }

  if (state.scope === 'focus' && state.focusScopeId) {
    const seeds = new Set([state.focusScopeId])
    return extractHopSubgraph(source, seeds, state.hops, state.expandedSeeds)
  }

  if (state.scope === 'core') {
    if (state.expandedSeeds.size > 0 && state.fullData) {
      const baseIds = new Set(state.coreData.nodes.map((n) => n.id))
      return extractHopSubgraph(state.fullData, baseIds, 1, state.expandedSeeds)
    }
    return state.isSplitLive ? state.coreData : extractCoreSubgraph(source, 200)
  }

  return source
}

function getFilteredData(): NetworkData {
  const scoped = getScopedBaseData()
  const nodes = scoped.nodes
    .filter((n) => state.activeSectors.has(n.sector))
    .map((n) => ({ ...n }))
  const validIds = new Set(nodes.map((n) => n.id))
  const links = scoped.links
    .filter((l) => {
      if (!validIds.has(linkEndId(l.source)) || !validIds.has(linkEndId(l.target))) return false
      if (l.value < state.minValue) return false
      if (state.activeKinds.size > 0 && !state.activeKinds.has(l.kind)) return false
      if (state.sourceFilter === 'table' && l.sourceType === 'llm') return false
      return true
    })
    .map((l) => ({ ...l, source: linkEndId(l.source), target: linkEndId(l.target) }))
  return { nodes, links }
}

function updateFilterBadge() {
  let count = 0
  if (state.minValue > 0) count++
  if (state.activeSectors.size > 0 && state.activeSectors.size < state.availableSectors.length) count++
  if (state.activeKinds.size > 0 && state.activeKinds.size < state.availableKinds.length) count++
  if (state.sourceFilter === 'table') count++
  if (state.scope === 'focus') count++

  const badge = document.getElementById('filter-badge') as HTMLElement
  if (count > 0) {
    badge.textContent = String(count)
    badge.classList.remove('hidden')
  } else {
    badge.classList.add('hidden')
  }
}

function updateScopeUI() {
  const holdingsBtn = document.getElementById('scope-btn-holdings') as HTMLElement
  const holdingsPill = document.getElementById('holdings-count-pill') as HTMLElement
  const hopsRow = document.getElementById('hops-row') as HTMLElement

  if (state.holdings.size > 0) {
    holdingsBtn.classList.remove('hidden')
    holdingsPill.textContent = `保有 ${state.holdings.size}銘柄`
    holdingsPill.classList.remove('hidden')
  } else {
    holdingsBtn.classList.add('hidden')
    holdingsPill.classList.add('hidden')
  }

  const showHops = state.scope === 'holdings' || state.scope === 'focus'
  hopsRow.classList.toggle('hidden', !showHops)

  document.querySelectorAll<HTMLButtonElement>('.scope-btn').forEach((btn) => {
    const active = btn.dataset.scope === state.scope
    const isHoldingsBtn = btn.dataset.scope === 'holdings'
    btn.className = `scope-btn py-1.5 rounded-lg text-[11px] font-medium transition ${
      isHoldingsBtn ? 'col-span-2 ' : ''
    }${
      active
        ? isHoldingsBtn
          ? 'bg-amber-500 text-slate-950 font-semibold'
          : 'bg-sky-500 text-slate-950 font-semibold'
        : isHoldingsBtn
          ? 'text-amber-300 hover:text-amber-200'
          : 'text-slate-300 hover:text-white'
    }`
    if (isHoldingsBtn && state.holdings.size === 0) btn.classList.add('hidden')
  })

  document.querySelectorAll<HTMLButtonElement>('.hops-btn').forEach((btn) => {
    const active = Number(btn.dataset.hops) === state.hops
    btn.className = `hops-btn px-2 py-0.5 rounded-md text-[10px] font-medium transition ${
      active ? 'bg-sky-500 text-slate-950' : 'text-slate-300 hover:text-white'
    }`
  })
}

function updateDataStatusUI() {
  const badge = document.getElementById('data-status-badge') as HTMLElement
  const metaNote = document.getElementById('stat-meta-note') as HTMLElement
  const mcapLabel = document.getElementById('ins-mcap-label') as HTMLElement
  const capLabel = document.getElementById('ins-cap-label') as HTMLElement
  const excerptLabel = document.getElementById('ins-excerpt-label') as HTMLElement

  if (state.meta.dataType === 'live') {
    badge.textContent = state.meta.fiscalYear ? `実データ (${state.meta.fiscalYear})` : '実データ'
    badge.className =
      'text-[10px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-400/30'
    metaNote.textContent = state.meta.updatedAt
      ? `全${state.index.length.toLocaleString()}社収録 / 更新: ${state.meta.updatedAt.slice(0, 10)}`
      : `全${state.index.length.toLocaleString()}社収録`
    mcapLabel.textContent = '時価総額'
    capLabel.textContent = '資本金'
    excerptLabel.textContent = '有報テキスト抽出'
  } else {
    badge.textContent = 'サンプルデータ'
    badge.className =
      'text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-400/30'
    metaNote.textContent = `サンプル ${state.index.length}社を表示中`
    mcapLabel.textContent = '時価総額（サンプル値）'
    capLabel.textContent = '資本金（サンプル値）'
    excerptLabel.textContent = '有報テキスト抽出イメージ（サンプル）'
  }
}

function updateGraphData() {
  const data = getFilteredData()
  state.visibleNodeCount = data.nodes.length
  // 大規模グラフ時のWebGL負荷軽減LOD
  graph.nodeResolution(data.nodes.length > 300 ? 10 : 20)
  graph.graphData(data)
  ;(document.getElementById('stat-nodes') as HTMLElement).textContent = data.nodes.length.toLocaleString()
  ;(document.getElementById('stat-links') as HTMLElement).textContent = data.links.length.toLocaleString()
  updateFilterBadge()
  updateScopeUI()

  if (state.selectedNodeId && !data.nodes.some((n) => n.id === state.selectedNodeId)) {
    clearSelection()
  } else if (state.selectedNodeId) {
    const node = data.nodes.find((n) => n.id === state.selectedNodeId)
    if (node) {
      recomputeHighlight(node)
      refreshGraphStyles()
      void renderInspector(node)
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

function focusCameraOnNode(node: NetNode) {
  if (typeof node.x !== 'number') return
  const dist = 145
  const hypot = Math.hypot(node.x, node.y ?? 0, node.z ?? 0) || 1
  const ratio = 1 + dist / hypot
  graph.cameraPosition(
    { x: node.x * ratio, y: (node.y ?? 0) * ratio + 15, z: (node.z ?? 0) * ratio },
    { x: node.x, y: node.y ?? 0, z: node.z ?? 0 },
    850,
  )
}

function selectNode(node: NetNode, moveCamera = true) {
  if (!node) return
  state.selectedNodeId = node.id
  setAutoRotate(false)
  recomputeHighlight(node)
  refreshGraphStyles()
  void renderInspector(node)
  if (moveCamera) {
    if (typeof node.x === 'number') {
      focusCameraOnNode(node)
    } else {
      window.setTimeout(() => {
        if (state.selectedNodeId !== node.id) return
        const { nodes } = graph.graphData() as unknown as NetworkData
        const live = nodes.find((n) => n.id === node.id)
        if (live) focusCameraOnNode(live)
      }, 600)
    }
  }
}

function clearSelection() {
  state.selectedNodeId = null
  state.highlightNodes.clear()
  state.highlightLinks.clear()
  document.getElementById('inspector-panel')?.classList.add('hidden')
  refreshGraphStyles()
}

async function renderInspector(node: NetNode) {
  const panel = document.getElementById('inspector-panel') as HTMLElement
  const style = getSectorStyle(node.sector)
  const accentColor = getNodeSectorColor(node.sector, node.subSector, true)

  const logoEl = document.getElementById('ins-logo') as HTMLElement
  logoEl.style.background = `linear-gradient(135deg, ${accentColor}, #e2e8f0)`
  logoCache.renderInto(logoEl, node)

  ;(document.getElementById('ins-code') as HTMLElement).textContent = node.code
  document.getElementById('ins-holding-badge')?.classList.toggle('hidden', !isHolding(node.id))

  const marketEl = document.getElementById('ins-market') as HTMLElement
  if (node.market) {
    marketEl.textContent = node.market
    marketEl.classList.remove('hidden')
  } else {
    marketEl.classList.add('hidden')
  }

  const secEl = document.getElementById('ins-sector') as HTMLElement
  secEl.textContent =
    node.subSector && node.subSector !== node.sector ? `${node.sector} › ${node.subSector}` : node.sector
  secEl.style.backgroundColor = style.bg
  secEl.style.color = style.text
  ;(document.getElementById('ins-name') as HTMLElement).textContent = node.name
  ;(document.getElementById('ins-mcap') as HTMLElement).textContent = formatMetricValue(node.marketCap)
  ;(document.getElementById('ins-cap') as HTMLElement).textContent = formatMetricValue(node.capital)

  const excerptEl = document.getElementById('ins-excerpt') as HTMLElement
  excerptEl.textContent = node.excerpt || (state.isSplitLive ? '有報詳細を読み込み中...' : '有価証券報告書の関係情報')

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
      item.className =
        'w-full text-left bg-slate-900/75 hover:bg-slate-800/90 border border-slate-800 rounded-xl p-2.5 transition group'
      const srcBadge =
        link.sourceType === 'llm'
          ? '<span class="px-1 py-0.2 rounded bg-violet-500/20 text-violet-300 text-[9px]">LLM</span>'
          : link.sourceType === 'table'
            ? '<span class="px-1 py-0.2 rounded bg-sky-500/15 text-sky-300 text-[9px]">定型表</span>'
            : ''
      const holdStar = isHolding(peer.id) ? '<span class="text-amber-300 text-[10px]">★</span>' : ''
      item.innerHTML = `
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 min-w-0">
            <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background:${pStyle.color}"></span>
            <span class="text-xs font-semibold text-slate-100 truncate group-hover:text-sky-300">${holdStar}${escapeHtml(peer.name)}</span>
          </div>
          <span class="text-[11px] font-mono font-bold text-sky-300 shrink-0">${link.value}</span>
        </div>
        <div class="mt-1 flex items-center justify-between gap-1 text-[10px] text-slate-400">
          <span class="inline-flex items-center gap-1">${escapeHtml(link.kind)} ${srcBadge}</span>
          <span class="truncate max-w-[130px]">${escapeHtml(link.detail || '')}</span>
        </div>
        <div class="mt-1.5 h-1 w-full bg-slate-800 rounded-full overflow-hidden">
          <div class="h-full rounded-full" style="width:${Math.min(100, link.value)}%;background:${pStyle.color};"></div>
        </div>`
      item.addEventListener('click', () => selectNode(peer, true))
      listEl.appendChild(item)
    })
  }
  panel.classList.remove('hidden')

  // 分割ライブデータ時のみ詳細バケット（details/{0-9}.json）を遅延取得
  if (!node.excerpt && state.isSplitLive) {
    const detail = await fetchCompanyDetail(node.code, state.isSplitLive)
    if (state.selectedNodeId === node.id) {
      if (detail?.excerpt) {
        node.excerpt = detail.excerpt
        excerptEl.textContent = detail.excerpt
      } else {
        excerptEl.textContent = 'この銘柄の有報テキスト抜粋はまだ登録されていません。'
      }
    }
  }
}

// ===== セクター凡例 =====
function renderSectorLegend() {
  const listEl = document.getElementById('sector-list') as HTMLElement
  listEl.innerHTML = ''
  state.availableSectors.forEach((sector) => {
    const style = getSectorStyle(sector)
    const active = state.activeSectors.has(sector)
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = `w-full flex items-center justify-between px-2.5 py-1.5 rounded-xl text-xs transition border ${
      active
        ? 'bg-slate-900/90 border-slate-700/80 text-slate-100'
        : 'bg-slate-950/40 border-transparent text-slate-500 opacity-60'
    }`
    btn.innerHTML = `
      <span class="flex items-center gap-2 min-w-0">
        <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background:${style.color};box-shadow:${active ? '0 0 8px ' + style.color : 'none'}"></span>
        <span class="truncate">${escapeHtml(sector)}</span>
      </span>
      <span class="text-[10px] font-mono shrink-0">${active ? 'ON' : 'OFF'}</span>`
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

// ===== 関係種別チップフィルタ =====
function renderKindFilters() {
  const listEl = document.getElementById('kind-list') as HTMLElement
  listEl.innerHTML = ''
  state.availableKinds.forEach((kind) => {
    const active = state.activeKinds.has(kind)
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = `px-2 py-1 rounded-lg text-[10px] font-medium border transition ${
      active
        ? 'bg-sky-500/20 text-sky-200 border-sky-400/40'
        : 'bg-slate-950/50 text-slate-500 border-slate-800/80 hover:text-slate-300'
    }`
    btn.textContent = kind
    btn.addEventListener('click', () => {
      if (state.activeKinds.has(kind)) {
        if (state.activeKinds.size > 1) state.activeKinds.delete(kind)
      } else {
        state.activeKinds.add(kind)
      }
      renderKindFilters()
      updateGraphData()
    })
    listEl.appendChild(btn)
  })
}

// ===== ロゴ/ラベル オーバーレイ（ズーム連動LOD＋LogoKit多段フォールバック＋描画上限キャップ） =====
const logoCache = new LogoCache(config.logokitToken)

function getLogoImage(node: NetNode) {
  return logoCache.get(node)
}

function drawRoundRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
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

const MAX_OVERLAY_LABELS = 48

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

  ctx.clearRect(0, 0, viewportWidth, viewportHeight)
  const camera = graph.camera()
  if (!camera) return
  const { nodes } = graph.graphData() as unknown as NetworkData
  const camPos = camera.position

  // ズームイン連動：一定距離より近づくと大分類11色→中分類（33業種）近似色へ自動切替（ヒステリシス付き）
  const controls = graph.controls() as unknown as { target?: { x: number; y: number; z: number } } | undefined
  const target = controls?.target ?? { x: 0, y: 0, z: 0 }
  const camDist = Math.hypot(camPos.x - target.x, camPos.y - target.y, camPos.z - target.z)
  const nextSubMode = state.zoomSubSectorMode ? camDist < 285 : camDist < 245
  if (nextSubMode !== state.zoomSubSectorMode) {
    state.zoomSubSectorMode = nextSubMode
    zoomSubSectorBadge?.classList.toggle('hidden', !nextSubMode)
    graph.nodeColor((n) => getNodeColor(n as NetNode))
  }

  interface Candidate {
    node: NetNode
    dist: number
    screen: { x: number; y: number }
    isSelected: boolean
    isNeighbor: boolean
    isHovered: boolean
    isHold: boolean
    showLogo: boolean
    priority: number
  }

  const candidates: Candidate[] = []
  for (const n of nodes) {
    if (typeof n.x !== 'number') continue
    const dx = camPos.x - n.x
    const dy = camPos.y - (n.y ?? 0)
    const dz = camPos.z - (n.z ?? 0)
    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz)

    const isSelected = n.id === state.selectedNodeId
    const isNeighbor = !!state.selectedNodeId && state.highlightNodes.has(n.id)
    const isHovered = n.id === state.hoverNodeId
    const isHold = isHolding(n.id)
    const isMajor = (n.marketCap || 0) >= 13
    const showLogo = dist < 310 || isSelected || isHovered || isNeighbor || isHold
    const showLabel = dist < 480 || isSelected || isHovered || isNeighbor || isMajor || isHold
    if (!showLogo && !showLabel) continue

    const screen = graph.graph2ScreenCoords(n.x, n.y ?? 0, n.z ?? 0)
    if (
      !screen ||
      screen.x < -60 ||
      screen.x > viewportWidth + 60 ||
      screen.y < -60 ||
      screen.y > viewportHeight + 60
    ) {
      continue
    }

    const priority =
      (isSelected ? 10000 : 0) +
      (isHovered ? 8000 : 0) +
      (isHold ? 6000 : 0) +
      (isNeighbor ? 4000 : 0) +
      (isMajor ? 1000 : 0) -
      dist

    candidates.push({
      node: n,
      dist,
      screen,
      isSelected,
      isNeighbor,
      isHovered,
      isHold,
      showLogo,
      priority,
    })
  }

  if (candidates.length > MAX_OVERLAY_LABELS) {
    candidates.sort((a, b) => b.priority - a.priority)
    candidates.length = MAX_OVERLAY_LABELS
  }

  for (const c of candidates) {
    const { node: n, dist, screen, isSelected, isNeighbor, isHovered, isHold, showLogo } = c
    const nodeRingColor = getNodeSectorColor(n.sector, n.subSector, state.zoomSubSectorMode || dist < 245)
    const dimmed = !!state.selectedNodeId && !isSelected && !isNeighbor && !isHold
    const baseAlpha = dimmed ? 0.16 : 0.95

    if (showLogo) {
      const zoomFactor = Math.max(0, Math.min(1, (320 - dist) / 130))
      const alpha =
        isSelected || isHovered || isHold
          ? 1
          : Math.min(baseAlpha, Math.max(isNeighbor ? 0.88 : 0, zoomFactor))
      if (alpha > 0.08) {
        const radius = isSelected ? 16 : isHold ? 15 : dist < 200 ? 15 : 12
        ctx.save()
        ctx.globalAlpha = alpha

        // 保有銘柄のゴールド二重リング
        if (isHold) {
          ctx.beginPath()
          ctx.arc(screen.x, screen.y, radius + 4.5, 0, Math.PI * 2)
          ctx.strokeStyle = 'rgba(251, 191, 36, 0.85)'
          ctx.lineWidth = 2
          ctx.stroke()
        }

        ctx.beginPath()
        ctx.arc(screen.x, screen.y, radius, 0, Math.PI * 2)
        ctx.fillStyle = '#0b1222'
        ctx.fill()
        ctx.lineWidth = isSelected ? 2.5 : isHold ? 2.2 : 1.8
        ctx.strokeStyle = isSelected ? '#ffffff' : isHold ? '#fbbf24' : nodeRingColor
        ctx.stroke()

        const logoEntry = getLogoImage(n)
        if (logoEntry.status === 'loaded') {
          ctx.save()
          ctx.beginPath()
          ctx.arc(screen.x, screen.y, radius - 2, 0, Math.PI * 2)
          ctx.clip()
          ctx.drawImage(
            logoEntry.img,
            screen.x - (radius - 2),
            screen.y - (radius - 2),
            (radius - 2) * 2,
            (radius - 2) * 2,
          )
          ctx.restore()
        } else {
          ctx.fillStyle = '#f8fafc'
          ctx.font = `700 ${radius >= 15 ? 10 : 9}px Inter, sans-serif`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'middle'
          ctx.fillText(n.logoText || n.code.slice(0, 2), screen.x, screen.y + 0.5)
        }

        const labelText = isHold ? `★ ${n.name}` : n.name
        ctx.font = `${isSelected || isHold ? 700 : 500} 11px "Noto Sans JP", Inter, sans-serif`
        const textWidth = ctx.measureText(labelText).width
        const px = 6
        const bx = screen.x - textWidth / 2 - px
        const by = screen.y + radius + (isHold ? 6 : 4)
        ctx.fillStyle = isHold ? 'rgba(30, 24, 8, 0.88)' : 'rgba(6, 10, 20, 0.84)'
        drawRoundRect(ctx, bx, by, textWidth + px * 2, 18, 6)
        ctx.fill()
        if (isHold) {
          ctx.strokeStyle = 'rgba(251, 191, 36, 0.45)'
          ctx.lineWidth = 1
          ctx.stroke()
        }
        ctx.fillStyle = isSelected ? '#ffffff' : isHold ? '#fde68a' : '#e2e8f0'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillText(labelText, screen.x, by + 9.5)
        ctx.restore()
      }
    } else {
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
  const label = document.getElementById('btn-autorotate-label') as HTMLElement
  label.textContent = enabled ? 'ON' : 'OFF'
  btn.className = enabled
    ? 'px-2.5 py-1.5 rounded-xl text-xs font-medium bg-sky-500/20 text-sky-300 border border-sky-400/30 hover:bg-sky-500/30 transition inline-flex items-center gap-1'
    : 'px-2.5 py-1.5 rounded-xl text-xs font-medium bg-slate-800/85 text-slate-300 border border-slate-700/70 hover:bg-slate-700/85 transition inline-flex items-center gap-1'
}

container.addEventListener('pointerdown', () => {
  if (state.autoRotate) setAutoRotate(false)
})

document.getElementById('btn-autorotate')?.addEventListener('click', () => setAutoRotate(!state.autoRotate))
document.getElementById('btn-reset')?.addEventListener('click', () => {
  clearSelection()
  state.expandedSeeds.clear()
  if (state.scope === 'focus') {
    state.scope = state.holdings.size > 0 ? 'holdings' : 'core'
    state.focusScopeId = null
    updateGraphData()
  }
  graph.zoomToFit(700, 50)
})
document.getElementById('btn-close-inspector')?.addEventListener('click', clearSelection)

// 全画面ボタン（iframeで許可されていない場合は非表示）
const btnFullscreen = document.getElementById('btn-fullscreen') as HTMLElement
if (!document.fullscreenEnabled) {
  btnFullscreen.classList.add('hidden')
} else {
  btnFullscreen.addEventListener('click', () => {
    if (document.fullscreenElement) {
      void document.exitFullscreen()
    } else {
      void document.documentElement.requestFullscreen()
    }
  })
}

// フィルタドロワー開閉
const leftPanel = document.getElementById('left-panel') as HTMLElement
if (!config.embed && window.innerWidth >= 768) {
  leftPanel.classList.remove('hidden')
}
document.getElementById('btn-toggle-controls')?.addEventListener('click', () => {
  leftPanel.classList.toggle('hidden')
})
document.getElementById('btn-close-controls')?.addEventListener('click', () => {
  leftPanel.classList.add('hidden')
})

// 表示スコープ切替
document.querySelectorAll<HTMLButtonElement>('.scope-btn').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const next = btn.dataset.scope as ViewScope | undefined
    if (!next) return
    if (next === 'all' || next === 'holdings') {
      await ensureFullData()
    }
    state.scope = next
    state.expandedSeeds.clear()
    updateGraphData()
    graph.zoomToFit(650, 50)
  })
})

// ホップ数切替
document.querySelectorAll<HTMLButtonElement>('.hops-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    const h = Number(btn.dataset.hops) === 2 ? 2 : 1
    state.hops = h
    updateGraphData()
    graph.zoomToFit(650, 50)
  })
})

// インスペクタ探索ボタン（この銘柄周辺に絞る / さらに1ホップ広げる）
document.getElementById('btn-focus-subgraph')?.addEventListener('click', async () => {
  if (!state.selectedNodeId) return
  await ensureFullData()
  state.focusScopeId = state.selectedNodeId
  state.scope = 'focus'
  state.expandedSeeds.clear()
  updateGraphData()
  graph.zoomToFit(650, 60)
})

document.getElementById('btn-expand-hop')?.addEventListener('click', async () => {
  if (!state.selectedNodeId) return
  await ensureFullData()
  state.expandedSeeds.add(state.selectedNodeId)
  if (state.scope === 'all') {
    // 全表示中はそのまま該当ノードの隣接が既に出ているのでフォーカスのみ
    const { nodes } = graph.graphData() as unknown as NetworkData
    const target = nodes.find((n) => n.id === state.selectedNodeId)
    if (target) focusCameraOnNode(target)
    return
  }
  updateGraphData()
})

// ノードサイズ切替
const metricOn = 'py-1.5 rounded-lg text-xs font-medium bg-sky-500 text-slate-950 transition'
const metricOff = 'py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white transition'
function applyMetricUI(metric: SizeMetric) {
  state.sizeMetric = metric
  ;(document.getElementById('metric-mcap') as HTMLElement).className = metric === 'marketCap' ? metricOn : metricOff
  ;(document.getElementById('metric-cap') as HTMLElement).className = metric === 'capital' ? metricOn : metricOff
}
applyMetricUI(state.sizeMetric)

document.getElementById('metric-mcap')?.addEventListener('click', () => {
  applyMetricUI('marketCap')
  refreshGraphStyles()
})
document.getElementById('metric-cap')?.addEventListener('click', () => {
  applyMetricUI('capital')
  refreshGraphStyles()
})

// 関係値スライダー
const thresholdSlider = document.getElementById('threshold-slider') as HTMLInputElement
const thresholdValue = document.getElementById('threshold-value') as HTMLElement
thresholdSlider.value = String(state.minValue)
thresholdValue.textContent = `${state.minValue} 以上`
thresholdSlider.addEventListener('input', () => {
  state.minValue = Number(thresholdSlider.value)
  thresholdValue.textContent = `${state.minValue} 以上`
  updateGraphData()
})

// 出典フィルタ
const sourceOn = 'py-1 rounded-lg text-[11px] font-medium bg-sky-500 text-slate-950 transition'
const sourceOff = 'py-1 rounded-lg text-[11px] font-medium text-slate-300 hover:text-white transition'
function applySourceUI(filter: SourceFilter) {
  state.sourceFilter = filter
  ;(document.getElementById('source-all') as HTMLElement).className = filter === 'all' ? sourceOn : sourceOff
  ;(document.getElementById('source-table') as HTMLElement).className = filter === 'table' ? sourceOn : sourceOff
}
applySourceUI(state.sourceFilter)
document.getElementById('source-all')?.addEventListener('click', () => {
  applySourceUI('all')
  updateGraphData()
})
document.getElementById('source-table')?.addEventListener('click', () => {
  applySourceUI('table')
  updateGraphData()
})

document.getElementById('btn-all-sectors')?.addEventListener('click', () => {
  state.activeSectors = new Set(state.availableSectors)
  renderSectorLegend()
  updateGraphData()
})
document.getElementById('btn-all-kinds')?.addEventListener('click', () => {
  state.activeKinds = new Set(state.availableKinds)
  renderKindFilters()
  updateGraphData()
})

// ===== 全社横断検索（index.json 対応） =====
const searchInput = document.getElementById('search-input') as HTMLInputElement
const searchResults = document.getElementById('search-results') as HTMLElement

async function jumpToCompany(entry: IndexEntry) {
  const currentNodes = (graph.graphData() as unknown as NetworkData).nodes
  let target = currentNodes.find((n) => n.id === entry.id || n.code === entry.code)

  if (!target) {
    // 現在の表示スコープ外にいる銘柄の場合は全データを取り込んで表示対象に追加する
    await ensureFullData()
    state.activeSectors.add(entry.sector)
    state.expandedSeeds.add(entry.id)
    renderSectorLegend()
    updateGraphData()
    const updated = (graph.graphData() as unknown as NetworkData).nodes
    target = updated.find((n) => n.id === entry.id || n.code === entry.code)
  }

  if (target) {
    selectNode(target, true)
  }
}

searchInput.addEventListener('input', () => {
  const q = searchInput.value.trim().toLowerCase()
  if (!q) {
    searchResults.classList.add('hidden')
    return
  }
  const matches = state.index.filter(
    (n) =>
      n.name.toLowerCase().includes(q) ||
      n.code.toLowerCase().includes(q) ||
      n.sector.toLowerCase().includes(q),
  )
  searchResults.innerHTML = ''
  if (matches.length === 0) {
    searchResults.innerHTML = '<div class="px-3 py-2 text-xs text-slate-400">一致する銘柄がありません</div>'
  } else {
    matches.slice(0, 10).forEach((entry) => {
      const item = document.createElement('button')
      item.type = 'button'
      item.className =
        'w-full text-left px-3 py-2 hover:bg-slate-800/90 flex items-center justify-between gap-2 text-xs border-b border-slate-800/60 last:border-0'
      const holdStar = isHolding(entry.id) ? '<span class="text-amber-300">★</span>' : ''
      item.innerHTML = `
        <span class="flex items-center gap-2 min-w-0">
          <span class="w-2 h-2 rounded-full shrink-0" style="background:${getSectorStyle(entry.sector).color}"></span>
          <span class="text-slate-100 font-medium truncate">${holdStar}${escapeHtml(entry.name)}</span>
        </span>
        <span class="text-[10px] font-mono text-slate-400 shrink-0">${escapeHtml(entry.code)}</span>`
      item.addEventListener('click', () => {
        searchInput.value = ''
        searchResults.classList.add('hidden')
        void jumpToCompany(entry)
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

// ===== Muse向け iframe 埋め込みコード生成モーダル =====
const embedModal = document.getElementById('embed-modal') as HTMLElement
const embedPreview = document.getElementById('embed-code-preview') as HTMLElement
const optHoldings = document.getElementById('opt-embed-holdings') as HTMLInputElement
const optFocus = document.getElementById('opt-embed-focus') as HTMLInputElement
const btnPreviewEmbed = document.getElementById('btn-preview-embed') as HTMLAnchorElement
const btnCopyEmbed = document.getElementById('btn-copy-embed') as HTMLButtonElement

function getCanonicalBaseUrl(): string {
  if (window.location.protocol === 'https:') {
    return `${window.location.origin}${window.location.pathname}`
  }
  return 'https://asoka501.github.io/Git_stock_networkmap/'
}

function refreshEmbedSnippet() {
  const holdings = optHoldings.checked
    ? state.holdings.size > 0
      ? Array.from(state.holdings)
      : ['7203', '6758', '8035']
    : []
  const focus = optFocus.checked ? state.selectedNodeId || '7203' : null
  const code = buildIframeSnippet(getCanonicalBaseUrl(), { holdings, focus })
  embedPreview.textContent = code

  const previewUrl = new URL(window.location.href)
  previewUrl.search = ''
  previewUrl.searchParams.set('embed', '1')
  if (holdings.length > 0) previewUrl.searchParams.set('holdings', holdings.join(','))
  if (focus) previewUrl.searchParams.set('focus', focus)
  btnPreviewEmbed.href = previewUrl.toString()
}

function closeEmbedModal() {
  embedModal.classList.add('hidden')
  embedModal.classList.remove('flex')
}

document.getElementById('btn-embed-code')?.addEventListener('click', () => {
  refreshEmbedSnippet()
  embedModal.classList.remove('hidden')
  embedModal.classList.add('flex')
})
document.getElementById('btn-close-embed-modal')?.addEventListener('click', closeEmbedModal)
optHoldings.addEventListener('change', refreshEmbedSnippet)
optFocus.addEventListener('change', refreshEmbedSnippet)
btnCopyEmbed.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(embedPreview.textContent || '')
    btnCopyEmbed.textContent = 'コピーしました！'
    window.setTimeout(() => {
      btnCopyEmbed.textContent = 'コードをコピー'
    }, 1800)
  } catch {
    btnCopyEmbed.textContent = '手動でコピーしてください'
  }
})

// ===== JSON読込モーダル =====
const jsonModal = document.getElementById('json-modal') as HTMLElement
const jsonError = document.getElementById('json-error') as HTMLElement
function closeJsonModal() {
  jsonModal.classList.add('hidden')
  jsonModal.classList.remove('flex')
}
document.getElementById('btn-json-modal')?.addEventListener('click', () => {
  jsonError.classList.add('hidden')
  tooltipEl.style.display = 'none'
  jsonModal.classList.remove('hidden')
  jsonModal.classList.add('flex')
})
document.getElementById('btn-close-modal')?.addEventListener('click', closeJsonModal)
document.getElementById('btn-restore-sample')?.addEventListener('click', () => {
  const sample = getSampleDataset()
  state.meta = sample.meta
  state.index = sample.index
  state.coreData = sample.coreData
  state.fullData = sample.fullData
  state.isSplitLive = false
  state.expandedSeeds.clear()
  clearSelection()
  syncCatalogFromData(sample.coreData, true)
  updateDataStatusUI()
  updateGraphData()
  closeJsonModal()
  graph.zoomToFit(700, 50)
})
document.getElementById('btn-apply-json')?.addEventListener('click', () => {
  const raw = (document.getElementById('json-input') as HTMLTextAreaElement).value.trim()
  try {
    const parsed = JSON.parse(raw)
    const data = normaliseData(parsed)
    state.meta = {
      dataType: 'live',
      nodeCount: data.nodes.length,
      linkCount: data.links.length,
    }
    state.index = buildIndexFromNetwork(data)
    state.coreData = data
    state.fullData = data
    state.isSplitLive = false
    state.expandedSeeds.clear()
    clearSelection()
    syncCatalogFromData(data, true)
    updateDataStatusUI()
    updateGraphData()
    closeJsonModal()
    graph.zoomToFit(700, 50)
  } catch (err) {
    jsonError.textContent = 'JSON形式エラー: ' + (err as Error).message
    jsonError.classList.remove('hidden')
  }
})

// ===== キーボードショートカット =====
document.addEventListener('keydown', (e) => {
  const tag = (e.target as HTMLElement | null)?.tagName
  const isTyping = tag === 'INPUT' || tag === 'TEXTAREA'

  if (e.key === 'Escape') {
    closeJsonModal()
    closeEmbedModal()
    searchResults.classList.add('hidden')
    if (state.selectedNodeId) clearSelection()
    else if (config.embed && !leftPanel.classList.contains('hidden')) leftPanel.classList.add('hidden')
    return
  }

  if (isTyping) return
  if (e.key === '/') {
    e.preventDefault()
    searchInput.focus()
    searchInput.select()
  } else if (e.key === 'r' || e.key === 'R') {
    clearSelection()
    graph.zoomToFit(700, 50)
  }
})

// ===== ResizeObserver（iframeのサイズ変化に即座に追従） =====
const resizeObserver = new ResizeObserver((entries) => {
  const rect = entries[0]?.contentRect
  if (!rect) return
  const w = Math.max(1, Math.floor(rect.width))
  const h = Math.max(1, Math.floor(rect.height))
  if (w !== viewportWidth || h !== viewportHeight) {
    graph.width(w).height(h)
    resizeOverlay(w, h)
  }
})
resizeObserver.observe(container)
window.addEventListener('resize', () => {
  const w = container.clientWidth || window.innerWidth
  const h = container.clientHeight || window.innerHeight
  graph.width(w).height(h)
  resizeOverlay(w, h)
})

// ===== 起動シーケンス =====
async function bootstrap() {
  createIcons({
    icons: {
      Code2,
      FileJson,
      LocateFixed,
      Maximize2,
      Network,
      RotateCw,
      Search,
      SlidersHorizontal,
    },
  })
  setAutoRotate(state.autoRotate)

  const dataset = await loadInitialDataset(config)
  state.meta = dataset.meta
  state.index = dataset.index
  state.coreData = dataset.coreData
  state.fullData = dataset.fullData
  state.isSplitLive = dataset.isSplitLive

  // URL指定が holdings / focus / all で分割配信中の場合は全ネットワークを取得
  if (state.isSplitLive && (state.scope === 'holdings' || state.scope === 'all' || config.focus)) {
    await ensureFullData()
  }

  syncCatalogFromData(state.fullData ?? state.coreData, true)
  updateDataStatusUI()
  updateGraphData()
  animate()

  // ?focus=7203 指定時の初期フォーカス
  if (config.focus) {
    const entry = state.index.find((i) => i.code === config.focus || i.id === config.focus)
    if (entry) {
      await jumpToCompany(entry)
    }
  }
}

void bootstrap()

// getNodeRadius は将来のカスタムノード描画用に保持（現行は nodeVal ベース）
export { getNodeRadius }
