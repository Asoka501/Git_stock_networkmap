import type { NetNode } from './types'

export type LogoStatus = 'loading' | 'loaded' | 'failed'

export interface LogoEntry {
  img: HTMLImageElement
  status: LogoStatus
  resolvedUrl: string | null
  candidates: string[]
  attemptIndex: number
}

function normalizeDomain(domain?: string): string {
  if (!domain) return ''
  return domain.trim().replace(/^https?:\/\//i, '').replace(/\/.*$/, '').toLowerCase()
}

/**
 * ロゴURLの多段フォールバック候補リストを生成する。
 * 日本株ではティッカー指定より企業ドメイン指定の方が圧倒的にヒット率が高いため、
 * ドメイン優先フォールバックを組み込む。
 *
 * 優先順:
 *   1) node.logo（既定: https://logoapi.sh/ticker/{code}.svg）
 *   2) https://logoapi.sh/{domain}（日本株大手でも高ヒット率・無料）
 *   3) node.logoFallbacks（JSON側で明示指定された予備URL）
 *   4) LogoKit Domain API（https://img.logokit.com/{domain}?token=...）
 *   5) LogoKit Stock API（https://img.logokit.com/ticker/{code}.T?token=...）
 *   6) すべて失敗時は UI 側で略称イニシャルバッジを表示
 */
export function buildLogoCandidates(
  node: Pick<NetNode, 'code' | 'logo' | 'logoFallbacks' | 'domain'>,
  logokitToken: string,
): string[] {
  const urls: string[] = []
  const pushUnique = (u?: string) => {
    const trimmed = u?.trim()
    if (trimmed && !urls.includes(trimmed)) urls.push(trimmed)
  }

  const cleanDomain = normalizeDomain(node.domain)

  pushUnique(node.logo)
  if (cleanDomain) {
    pushUnique(`https://logoapi.sh/${encodeURIComponent(cleanDomain)}`)
  }
  if (node.logoFallbacks) {
    for (const fb of node.logoFallbacks) pushUnique(fb)
  }

  const token = logokitToken.trim()
  if (token) {
    const encodedToken = encodeURIComponent(token)
    if (cleanDomain) {
      pushUnique(`https://img.logokit.com/${encodeURIComponent(cleanDomain)}?token=${encodedToken}`)
    }
    if (node.code) {
      const symbol = node.code.includes('.') ? node.code : `${node.code}.T`
      pushUnique(`https://img.logokit.com/ticker/${encodeURIComponent(symbol)}?token=${encodedToken}`)
    }
  }

  return urls
}

export class LogoCache {
  private readonly cache = new Map<string, LogoEntry>()
  private readonly logokitToken: string
  public revision = 0

  constructor(logokitToken: string) {
    this.logokitToken = logokitToken
  }

  get(node: NetNode): LogoEntry {
    const key = node.code || node.id
    const existing = this.cache.get(key)
    if (existing) return existing

    const candidates = buildLogoCandidates(node, this.logokitToken)
    const img = new Image()
    // 2Dキャンバスへの drawImage のみを行いピクセル読出し（getImageData/toDataURL）は行わないため、
    // crossOrigin を設定せず LogoKit 等 CORS ヘッダ非付与CDNの画像も正常表示させる
    img.referrerPolicy = 'strict-origin-when-cross-origin'

    const entry: LogoEntry = {
      img,
      status: candidates.length > 0 ? 'loading' : 'failed',
      resolvedUrl: null,
      candidates,
      attemptIndex: 0,
    }
    this.cache.set(key, entry)

    if (candidates.length === 0) return entry

    const tryNext = () => {
      if (entry.attemptIndex >= entry.candidates.length) {
        entry.status = 'failed'
        return
      }
      const nextUrl = entry.candidates[entry.attemptIndex++]
      img.src = nextUrl
    }

    img.onload = () => {
      if (img.naturalWidth > 0 && img.naturalHeight > 0) {
        entry.status = 'loaded'
        entry.resolvedUrl = img.currentSrc || img.src
        this.revision += 1
      } else {
        tryNext()
      }
    }
    img.onerror = () => {
      tryNext()
    }

    tryNext()
    return entry
  }

  /**
   * 詳細パネルおよび関係企業一覧用に独立した <img> 要素へ多段フォールバック付きでロゴを描画する。
   * lazy=true のときは未キャッシュ銘柄の先行読み込みを避け、スクロール表示行のみ遅延取得する。
   */
  renderInto(container: HTMLElement, node: NetNode, options: { lazy?: boolean } = {}): void {
    const fallbackText = node.logoText || node.code.slice(0, 2)
    container.innerHTML = ''

    const key = node.code || node.id
    const cached = options.lazy ? this.cache.get(key) : this.get(node)
    if (cached?.status === 'failed') {
      container.textContent = fallbackText
      return
    }

    const urls = cached?.resolvedUrl
      ? [cached.resolvedUrl]
      : cached
        ? [...cached.candidates]
        : buildLogoCandidates(node, this.logokitToken)

    if (urls.length === 0) {
      container.textContent = fallbackText
      return
    }

    let idx = 0
    const img = new Image()
    img.alt = fallbackText
    img.referrerPolicy = 'strict-origin-when-cross-origin'
    if (options.lazy) {
      img.loading = 'lazy'
      img.decoding = 'async'
    }
    img.className = 'w-full h-full object-contain bg-white'

    const tryNext = () => {
      if (idx >= urls.length) {
        img.remove()
        container.textContent = fallbackText
        return
      }
      img.src = urls[idx++]
    }

    img.onerror = () => tryNext()
    container.appendChild(img)
    tryNext()
  }
}
