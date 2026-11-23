import type { LinkObject, NodeObject } from '3d-force-graph'

export type SizeMetric = 'marketCap' | 'capital'
export type SourceType = 'table' | 'llm'
export type SourceFilter = 'all' | 'table'
export type ViewScope = 'all' | 'core' | 'holdings' | 'focus'

// ===== ネットワークJSONスキーマ契約（docs/DATA_SCHEMA_AND_PIPELINE.md 参照） =====
export interface NetNode extends NodeObject {
  id: string
  code: string
  name: string
  sector: string
  /** 中分類（JPX 33業種。ズームイン時の近似色・詳細表示に使用） */
  subSector?: string
  /** 時価総額（兆円） */
  marketCap: number
  /** 資本金（兆円） */
  capital: number
  /** プライマリロゴ画像URL（取得不可時はLogoKit→略称バッジへ多段フォールバック） */
  logo: string
  /** 取得失敗時に順に試す予備ロゴURL配列 */
  logoFallbacks?: string[]
  /** 企業公式サイトドメイン（例: toyota.co.jp。LogoKitドメイン検索用） */
  domain?: string
  logoText?: string
  /** 東証市場区分（プライム / スタンダード / グロース 等） */
  market?: string
  /** EDINETコード（例: E02144） */
  edinetCode?: string
  /** 対象決算期（例: 2025-03） */
  fiscalYear?: string
  /** 接続次数（index.json 向け） */
  degree?: number
  /** 詳細パネルに表示する有報テキスト抜粋 */
  excerpt?: string
}

export interface NetLink extends LinkObject<NetNode> {
  source: string | NetNode
  target: string | NetNode
  /** 関係値 0-100（線の太さ・粒子量の基準） */
  value: number
  /** 関係種別（主要顧客・供給 / 政策保有株式 / 大株主 / 資本・業務提携 など） */
  kind: string
  /** 抽出元の種別（定型表ルールベース or LLM本文抽出） */
  sourceType?: SourceType
  /** 関係の補足説明 */
  detail?: string
  /** 金額（百万円） */
  amountMillionYen?: number
  /** 割合（%） */
  ratioPercent?: number
  /** 出典EDINET書類ID */
  docId?: string
}

export interface NetworkData {
  nodes: NetNode[]
  links: NetLink[]
}

export interface DataMeta {
  dataType: 'sample' | 'live'
  updatedAt?: string
  fiscalYear?: string
  nodeCount: number
  linkCount: number
}

export interface IndexEntry {
  id: string
  code: string
  name: string
  sector: string
  subSector?: string
  market?: string
  marketCap: number
  capital: number
  degree?: number
  logo?: string
  logoText?: string
}

export interface DetailEntry {
  excerpt?: string
  docId?: string
  fiscalYear?: string
}

export interface EmbedConfig {
  /** ?embed=1 でスリムUIモードを有効化 */
  embed: boolean
  /** iframe内で描画されているか */
  inIframe: boolean
  /** 初期フォーカス銘柄コード */
  focus: string | null
  /** 保有銘柄コード一覧（強調表示・保有周辺スコープ用） */
  holdings: Set<string>
  /** focus / holdings 展開時のホップ数（1 or 2） */
  hops: 1 | 2
  /** 初期表示スコープ */
  mode: ViewScope
  /** 初期サイズ指標 */
  metric: SizeMetric
  /** 関係値スライダー初期値 */
  minValue: number
  /** 関係種別の初期フィルタ（空の場合は全選択） */
  kinds: Set<string>
  /** 出典フィルタ */
  sourceFilter: SourceFilter
  /** 自動回転の初期ON/OFF */
  rotate: boolean
  /** 外部JSON URL差し替え用（https:// または相対パスのみ許可） */
  dataUrl: string | null
  /** LogoKit公開トークン（VITE_LOGOKIT_TOKEN または ?logokitToken=） */
  logokitToken: string
}
