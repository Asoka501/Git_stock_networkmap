import type { NetLink, NetNode, NetworkData } from './types'

export function linkEndId(end: string | NetNode): string {
  return typeof end === 'object' ? String(end.id) : String(end)
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * seedIds から指定ホップ数（1〜2）以内のノードと、その間を結ぶエッジを抽出する。
 * 全上場3,800社データでも保有銘柄や注目銘柄の周辺だけを軽快に描画するために使用。
 */
export function extractHopSubgraph(
  data: NetworkData,
  seedIds: Set<string>,
  hops: 1 | 2,
  extraNodeIds: Set<string> = new Set(),
): NetworkData {
  const adj = new Map<string, Set<string>>()
  for (const link of data.links) {
    const s = linkEndId(link.source)
    const t = linkEndId(link.target)
    if (!adj.has(s)) adj.set(s, new Set())
    if (!adj.has(t)) adj.set(t, new Set())
    adj.get(s)!.add(t)
    adj.get(t)!.add(s)
  }

  const visited = new Set<string>()
  let frontier = new Set<string>()
  for (const id of seedIds) {
    visited.add(id)
    frontier.add(id)
  }

  for (let step = 0; step < hops; step++) {
    const next = new Set<string>()
    for (const id of frontier) {
      const neighbors = adj.get(id)
      if (!neighbors) continue
      for (const n of neighbors) {
        if (!visited.has(n)) {
          visited.add(n)
          next.add(n)
        }
      }
    }
    frontier = next
    if (frontier.size === 0) break
  }

  // ユーザーが「さらに1ホップ広げる」で追加展開したノードとその隣接も取り込む
  for (const id of extraNodeIds) {
    visited.add(id)
    const neighbors = adj.get(id)
    if (neighbors) {
      for (const n of neighbors) visited.add(n)
    }
  }

  const nodes = data.nodes.filter((n) => visited.has(n.id))
  const valid = new Set(nodes.map((n) => n.id))
  const links = data.links.filter(
    (l) => valid.has(linkEndId(l.source)) && valid.has(linkEndId(l.target)),
  )
  return { nodes, links }
}

/**
 * 大規模グラフ時に主要ハブ（次数×時価総額上位）を抽出する。
 * ノード数が maxNodes 以下であればそのまま全件返す。
 */
export function extractCoreSubgraph(data: NetworkData, maxNodes = 200): NetworkData {
  if (data.nodes.length <= maxNodes) return data

  const degree = new Map<string, number>()
  for (const l of data.links) {
    const s = linkEndId(l.source)
    const t = linkEndId(l.target)
    degree.set(s, (degree.get(s) ?? 0) + 1)
    degree.set(t, (degree.get(t) ?? 0) + 1)
  }

  const ranked = [...data.nodes].sort((a, b) => {
    const scoreA = (degree.get(a.id) ?? 0) * 10 + Math.log10((a.marketCap || 0.01) * 100 + 1) * 15
    const scoreB = (degree.get(b.id) ?? 0) * 10 + Math.log10((b.marketCap || 0.01) * 100 + 1) * 15
    return scoreB - scoreA
  })

  const kept = new Set(ranked.slice(0, maxNodes).map((n) => n.id))
  const nodes = data.nodes.filter((n) => kept.has(n.id))
  const links = data.links.filter(
    (l) => kept.has(linkEndId(l.source)) && kept.has(linkEndId(l.target)),
  )
  return { nodes, links }
}
