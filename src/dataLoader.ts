import sampleData from './data/sample.json'
import type {
  DataMeta,
  DetailEntry,
  EmbedConfig,
  IndexEntry,
  NetLink,
  NetNode,
  NetworkData,
  SourceType,
} from './types'
import { linkEndId } from './graphUtils'

// ===== 入力データの正規化（契約スキーマ＋拡張スキーマ＋試作スキーマを受理） =====
export function normaliseData(raw: unknown): NetworkData {
  const parsed = raw as { nodes?: Record<string, unknown>[]; links?: Record<string, unknown>[] }
  if (!parsed || !Array.isArray(parsed.nodes) || !Array.isArray(parsed.links)) {
    throw new Error('nodes配列とlinks配列の両方が必要です。')
  }
  const nodes: NetNode[] = parsed.nodes.map((n) => {
    const code = String(n.code ?? n.id ?? '').trim()
    return {
      id: String(n.id ?? code).trim(),
      code,
      name: String(n.name ?? code),
      sector: String(n.sector ?? 'その他'),
      marketCap: Number(n.marketCap ?? 0),
      capital: Number(n.capital ?? 0),
      logo: String(n.logo ?? `https://logoapi.sh/ticker/${code}.svg`),
      logoFallbacks: Array.isArray(n.logoFallbacks)
        ? n.logoFallbacks.map((u) => String(u).trim()).filter(Boolean)
        : undefined,
      domain: n.domain ? String(n.domain).trim() : undefined,
      logoText: n.logoText ? String(n.logoText) : undefined,
      market: n.market ? String(n.market) : undefined,
      edinetCode: n.edinetCode ? String(n.edinetCode) : undefined,
      fiscalYear: n.fiscalYear ? String(n.fiscalYear) : undefined,
      degree: typeof n.degree === 'number' ? n.degree : undefined,
      excerpt: n.excerpt ? String(n.excerpt) : undefined,
    }
  })

  const links: NetLink[] = parsed.links.map((l) => {
    const st = l.sourceType === 'table' || l.sourceType === 'llm' ? (l.sourceType as SourceType) : undefined
    return {
      source: String((l.source as { id?: string })?.id ?? l.source).trim(),
      target: String((l.target as { id?: string })?.id ?? l.target).trim(),
      value: Number(l.value ?? l.strength ?? 50),
      kind: String(l.kind ?? l.relation ?? '関連'),
      sourceType: st,
      detail: l.detail ? String(l.detail) : undefined,
      amountMillionYen: typeof l.amountMillionYen === 'number' ? l.amountMillionYen : undefined,
      ratioPercent: typeof l.ratioPercent === 'number' ? l.ratioPercent : undefined,
      docId: l.docId ? String(l.docId) : undefined,
    }
  })

  return { nodes, links }
}

export function buildIndexFromNetwork(data: NetworkData): IndexEntry[] {
  const degree = new Map<string, number>()
  for (const l of data.links) {
    const s = linkEndId(l.source)
    const t = linkEndId(l.target)
    degree.set(s, (degree.get(s) ?? 0) + 1)
    degree.set(t, (degree.get(t) ?? 0) + 1)
  }
  return data.nodes.map((n) => ({
    id: n.id,
    code: n.code,
    name: n.name,
    sector: n.sector,
    market: n.market,
    marketCap: n.marketCap,
    capital: n.capital,
    degree: n.degree ?? degree.get(n.id) ?? 0,
    logo: n.logo,
    logoText: n.logoText,
  }))
}

async function fetchJsonOrNull<T>(url: string): Promise<T | null> {
  try {
    const resp = await fetch(url, { cache: 'default' })
    if (!resp.ok) return null
    return (await resp.json()) as T
  } catch {
    return null
  }
}

export interface LoadedDataset {
  meta: DataMeta
  index: IndexEntry[]
  coreData: NetworkData
  /** 全件データが既にロード済みなら格納（サンプル時や ?data= 時） */
  fullData: NetworkData | null
  /** 分割配信（public/data/）が有効か */
  isSplitLive: boolean
}

export function getSampleDataset(): LoadedDataset {
  const data = normaliseData(sampleData)
  return {
    meta: {
      dataType: 'sample',
      nodeCount: data.nodes.length,
      linkCount: data.links.length,
    },
    index: buildIndexFromNetwork(data),
    coreData: data,
    fullData: data,
    isSplitLive: false,
  }
}

/**
 * 起動時のデータ読込。
 * 1) ?data= 指定があればそのJSONを取得
 * 2) ./data/core.json（Phase 3以降で生成される分割JSON）があれば優先読込
 * 3) 未生成時は同梱の sample.json へ自動フォールバック
 */
export async function loadInitialDataset(config: EmbedConfig): Promise<LoadedDataset> {
  if (config.dataUrl) {
    const customRaw = await fetchJsonOrNull<unknown>(config.dataUrl)
    if (customRaw) {
      try {
        const data = normaliseData(customRaw)
        return {
          meta: {
            dataType: 'live',
            nodeCount: data.nodes.length,
            linkCount: data.links.length,
          },
          index: buildIndexFromNetwork(data),
          coreData: data,
          fullData: data,
          isSplitLive: false,
        }
      } catch (err) {
        console.warn('外部JSONの解析に失敗したためデフォルトデータを使用します:', err)
      }
    }
  }

  const [metaRaw, indexRaw, coreRaw] = await Promise.all([
    fetchJsonOrNull<DataMeta>('./data/meta.json'),
    fetchJsonOrNull<IndexEntry[]>('./data/index.json'),
    fetchJsonOrNull<unknown>('./data/core.json'),
  ])

  if (coreRaw) {
    try {
      const coreData = normaliseData(coreRaw)
      const index = Array.isArray(indexRaw) && indexRaw.length > 0 ? indexRaw : buildIndexFromNetwork(coreData)
      const meta: DataMeta = metaRaw ?? {
        dataType: 'live',
        nodeCount: index.length,
        linkCount: coreData.links.length,
      }
      return {
        meta,
        index,
        coreData,
        fullData: null,
        isSplitLive: true,
      }
    } catch (err) {
      console.warn('分割JSONの解析に失敗したためサンプルデータを使用します:', err)
    }
  }

  return getSampleDataset()
}

let fullNetworkPromise: Promise<NetworkData | null> | null = null

export async function fetchFullNetwork(): Promise<NetworkData | null> {
  if (!fullNetworkPromise) {
    fullNetworkPromise = fetchJsonOrNull<unknown>('./data/network.json').then((raw) => {
      if (!raw) return null
      try {
        return normaliseData(raw)
      } catch {
        return null
      }
    })
  }
  return fullNetworkPromise
}

const detailBucketCache = new Map<string, Promise<Record<string, DetailEntry> | null>>()

export async function fetchCompanyDetail(code: string, isSplitLive: boolean): Promise<DetailEntry | null> {
  if (!isSplitLive || !code) return null
  const bucket = /^[0-9]/.test(code) ? code[0] : 'other'
  let bucketPromise = detailBucketCache.get(bucket)
  if (!bucketPromise) {
    bucketPromise = fetchJsonOrNull<Record<string, DetailEntry>>(`./data/details/${bucket}.json`)
    detailBucketCache.set(bucket, bucketPromise)
  }
  const map = await bucketPromise
  return map?.[code] ?? null
}
