import type { EmbedConfig, SizeMetric, SourceFilter, ViewScope } from './types'

function parseBoolParam(raw: string | null): boolean | null {
  if (raw === null) return null
  const v = raw.trim().toLowerCase()
  if (v === '1' || v === 'true' || v === 'yes' || v === 'on') return true
  if (v === '0' || v === 'false' || v === 'no' || v === 'off') return false
  return null
}

function parseCodeList(raw: string | null): Set<string> {
  const result = new Set<string>()
  if (!raw) return result
  for (const part of raw.split(',')) {
    const cleaned = part.trim().toUpperCase()
    if (cleaned) result.add(cleaned)
  }
  return result
}

function parseSafeDataUrl(raw: string | null): string | null {
  if (!raw) return null
  const trimmed = raw.trim()
  if (!trimmed) return null
  if (trimmed.startsWith('./') || trimmed.startsWith('/')) return trimmed
  try {
    const u = new URL(trimmed)
    if (u.protocol === 'https:') return u.toString()
  } catch {
    return null
  }
  return null
}

export function parseEmbedConfig(search: string = window.location.search): EmbedConfig {
  const params = new URLSearchParams(search)

  let inIframe = false
  try {
    inIframe = window.self !== window.top
  } catch {
    inIframe = true
  }

  const embedFlag = parseBoolParam(params.get('embed'))
  const embed = embedFlag ?? false

  const focusRaw = params.get('focus')?.trim().toUpperCase() || null
  const holdings = parseCodeList(params.get('holdings'))

  const hopsNum = Number(params.get('hops'))
  const hops: 1 | 2 = hopsNum === 2 ? 2 : 1

  const modeRaw = params.get('mode')?.trim().toLowerCase()
  let mode: ViewScope
  if (modeRaw === 'all' || modeRaw === 'core' || modeRaw === 'holdings' || modeRaw === 'focus') {
    mode = modeRaw
  } else if (holdings.size > 0) {
    mode = 'holdings'
  } else {
    mode = 'core'
  }

  const metricRaw = params.get('metric')?.trim()
  const metric: SizeMetric = metricRaw === 'capital' ? 'capital' : 'marketCap'

  const minValRaw = params.get('minValue')
  const parsedMin = minValRaw !== null ? Number(minValRaw) : 0
  const minValue = Number.isFinite(parsedMin) ? Math.max(0, Math.min(85, Math.round(parsedMin / 5) * 5)) : 0

  const kinds = new Set<string>()
  const kindRaw = params.get('kind')
  if (kindRaw) {
    for (const k of kindRaw.split(',')) {
      const t = k.trim()
      if (t) kinds.add(t)
    }
  }

  const sourceRaw = params.get('source')?.trim().toLowerCase()
  const sourceFilter: SourceFilter = sourceRaw === 'table' ? 'table' : 'all'

  const rotateFlag = parseBoolParam(params.get('rotate'))
  const rotate = rotateFlag !== null ? rotateFlag : !focusRaw

  const dataUrl = parseSafeDataUrl(params.get('data'))

  const envToken = (
    (import.meta as unknown as { env?: Record<string, string | undefined> }).env?.VITE_LOGOKIT_TOKEN ?? ''
  ).trim()
  const logokitToken = (params.get('logokitToken')?.trim() || envToken).trim()

  return {
    embed,
    inIframe,
    focus: focusRaw,
    holdings,
    hops,
    mode,
    metric,
    minValue,
    kinds,
    sourceFilter,
    rotate,
    dataUrl,
    logokitToken,
  }
}

export function buildIframeSnippet(baseUrl: string, options: {
  holdings?: string[]
  focus?: string | null
  hops?: 1 | 2
} = {}): string {
  const url = new URL(baseUrl)
  url.search = ''
  url.searchParams.set('embed', '1')
  if (options.holdings && options.holdings.length > 0) {
    url.searchParams.set('holdings', options.holdings.join(','))
  }
  if (options.focus) {
    url.searchParams.set('focus', options.focus)
  }
  if (options.hops && options.hops > 1) {
    url.searchParams.set('hops', String(options.hops))
  }
  const href = url.toString().replace(/%2C/gi, ',')
  return [
    '<iframe',
    `  src="${href}"`,
    '  width="100%"',
    '  height="600"',
    '  style="border:0;border-radius:12px;"',
    '  loading="lazy"',
    '  allow="fullscreen"',
    '  title="日本株 企業関係 3Dネットワーク">',
    '</iframe>',
  ].join('\n')
}
