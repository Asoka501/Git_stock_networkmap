export interface SectorStyle {
  color: string
  bg: string
  text: string
}

/** 11カテゴリ表示順（人間が識別しやすい10〜12色上限に基づく設計） */
export const SECTOR_ORDER = [
  '金融',
  '素材・重工',
  '半導体・電機',
  '自動車・モビリティ',
  '消費・医薬・食品',
  '不動産・建設',
  '商社・エネルギー',
  '通信・IT',
  '倉庫・運輸',
  'サービス業',
  'その他',
] as const

const LEGACY_SECTOR_MAP: Record<string, string> = {
  '消費・医薬': '消費・医薬・食品',
}

export function normalizeSectorName(sector: string): string {
  const trimmed = (sector || '').trim()
  if (!trimmed) return 'その他'
  return LEGACY_SECTOR_MAP[trimmed] ?? trimmed
}

/** 11カテゴリ基本パレット（ズームアウト時・凡例表示） */
export const SECTORS: Record<string, SectorStyle> = {
  '金融': { color: '#ef4444', bg: 'rgba(239, 68, 68, 0.18)', text: '#fca5a5' },
  '素材・重工': { color: '#b4734a', bg: 'rgba(180, 115, 74, 0.22)', text: '#e3b393' },
  '半導体・電機': { color: '#facc15', bg: 'rgba(250, 204, 21, 0.18)', text: '#fde047' },
  '自動車・モビリティ': { color: '#3b82f6', bg: 'rgba(59, 130, 246, 0.18)', text: '#93c5fd' },
  '消費・医薬・食品': { color: '#22c55e', bg: 'rgba(34, 197, 94, 0.18)', text: '#86efac' },
  '不動産・建設': { color: '#a855f7', bg: 'rgba(168, 85, 247, 0.18)', text: '#d8b4fe' },
  '商社・エネルギー': { color: '#f97316', bg: 'rgba(249, 115, 22, 0.18)', text: '#fdba74' },
  '通信・IT': { color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.18)', text: '#7dd3fc' },
  '倉庫・運輸': { color: '#fef08a', bg: 'rgba(254, 240, 138, 0.18)', text: '#fef9c3' },
  'サービス業': { color: '#e2e8f0', bg: 'rgba(226, 232, 240, 0.18)', text: '#f8fafc' },
  'その他': { color: '#64748b', bg: 'rgba(100, 116, 139, 0.22)', text: '#cbd5e1' },
}

/** ズームイン時の中分類（JPX 33業種）近似色パレット */
export const SUB_SECTOR_COLORS: Record<string, string> = {
  // 1. 金融（赤系）
  '銀行業': '#ef4444',
  '証券、商品先物取引業': '#fb7185',
  '保険業': '#dc2626',
  'その他金融業': '#f87171',
  // 2. 素材・重工（茶・テラコッタ系）
  '鉱業': '#8c4a27',
  '化学': '#c87d4f',
  '鉄鋼': '#9e5a38',
  '非鉄金属': '#de935b',
  '金属製品': '#b4734a',
  '石油・石炭製品': '#7d4628',
  'ゴム製品': '#a86740',
  'ガラス・土石製品': '#cf8f66',
  'パルプ・紙': '#dfa880',
  // 3. 半導体・電機（黄系）
  '電気機器': '#facc15',
  '精密機器': '#eab308',
  // 4. 自動車・モビリティ（青系）
  '輸送用機器': '#3b82f6',
  '機械': '#60a5fa',
  // 5. 消費・医薬・食品（緑系）
  '食料品': '#22c55e',
  '繊維製品': '#4ade80',
  '医薬品': '#10b981',
  'その他製品': '#86efac',
  '水産・農林業': '#16a34a',
  // 6. 不動産・建設（紫系）
  '建設業': '#a855f7',
  '不動産業': '#c084fc',
  // 7. 商社・エネルギー（オレンジ系）
  '電気・ガス業': '#fb923c',
  '卸売業': '#f97316',
  '小売業': '#fdba74',
  // 8. 通信・IT（薄青系）
  '情報・通信業': '#38bdf8',
  // 9. 倉庫・運輸（薄黄系）
  '陸運業': '#fef08a',
  '海運業': '#fde047',
  '空運業': '#fef9c3',
  '倉庫・運輸関連業': '#fde68a',
  // 10. サービス業（白・ライトグレー系）
  'サービス業': '#e2e8f0',
}

export function getSectorStyle(sector: string): SectorStyle {
  const key = normalizeSectorName(sector)
  return SECTORS[key] ?? SECTORS['その他']
}

export function getNodeSectorColor(
  sector: string,
  subSector: string | undefined,
  useSubSectorShade: boolean,
): string {
  if (useSubSectorShade && subSector && SUB_SECTOR_COLORS[subSector]) {
    return SUB_SECTOR_COLORS[subSector]
  }
  return getSectorStyle(sector).color
}

export function sortSectorsByOrder(sectors: Iterable<string>): string[] {
  const rank = new Map<string, number>(SECTOR_ORDER.map((s, idx) => [s, idx]))
  return Array.from(sectors).sort((a, b) => {
    const ra = rank.get(a) ?? 999
    const rb = rank.get(b) ?? 999
    return ra !== rb ? ra - rb : a.localeCompare(b, 'ja')
  })
}
