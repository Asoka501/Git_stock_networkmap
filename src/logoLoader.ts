import type { NetNode } from './types'

export type LogoStatus = 'loading' | 'loaded' | 'failed'

export interface LogoEntry {
  img: HTMLImageElement
  status: LogoStatus
  resolvedUrl: string | null
  candidates: string[]
  attemptIndex: number
}

/**
 * ロゴURLの多段フォールバック候補リストを生成する。
 * 優先順:
 *   1) node.logo（既定: logoapi.sh）
 *   2) node.logoFallbacks（JSON側で明示指定された予備URL）
 *   3) LogoKit Stock API（https://img.logokit.com/ticker/{code}.T?token=...）
 *   4) LogoKit Domain API（https://img.logokit.com/{domain}?token=...）
 *   5) すべて失敗時は UI 側で略称イニシャルバッジを表示
 */
export function buildLogoCandidates(node: Pick<NetNode, 'code' | 'logo' | 'logoFallbacks' | 'domain'>, logokitToken: string): string[] {
  const urls: string[] = []
  const pushUnique = (u?: string) => {
    const trimmed = u?.trim()
    if (trimmed && !urls.includes(trimmed)) urls.push(trimmed)
  }

  pushUnique(node.logo)
  if (node.logoFallbacks) {
    for (const fb of node.logoFallbacks) pushUnique(fb)
  }

  const token = logokitToken.trim()
  if (token) {
    const encodedToken = encodeURIComponent(token)
    if (node.code) {
      const symbol = node.code.includes('.') ? node.code : `${node.code}.T`
      pushUnique(`https://img.logokit.com/ticker/${encodeURIComponent(symbol)}?token=${encodedToken}`)
    }
    if (node.domain) {
      const cleanDomain = node.domain.replace(/^https?:\/\//i, '').replace(/\/.*$/, '')
      if (cleanDomain) {
        pushUnique(`https://img.logokit.com/${encodeURIComponent(cleanDomain)}?token=${encodedToken}`)
      }
    }
  }

  return urls
}

export class LogoCache {
  private readonly cache = new Map<string, LogoEntry>()
  private readonly logokitToken: string

  constructor(logokitToken: string) {
    this.logokitToken = logokitToken
  }

  get(node: NetNode): LogoEntry {
    const key = node.code || node.id
    const existing = this.cache.get(key)
    if (existing) return existing

    const candidates = buildLogoCandidates(node, this.logokitToken)
    const img = new Image()
    img.crossOrigin = 'anonymous'

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
   * 詳細パネル用に独立した <img> 要素へ多段フォールバック付きでロゴを描画する。
   */
  renderInto(container: HTMLElement, node: NetNode): void {
    const fallbackText = node.logoText || node.code.slice(0, 2)
    container.innerHTML = ''

    const cached = this.get(node)
    if (cached.status === 'failed') {
      container.textContent = fallbackText
      return
    }

    const urls = cached.resolvedUrl ? [cached.resolvedUrl] : [...cached.candidates]
    let idx = 0
    const img = new Image()
    img.alt = fallbackText
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
