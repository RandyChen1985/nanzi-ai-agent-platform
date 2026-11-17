const CHUNK_RELOAD_STORAGE_KEY = 'nanzi:chunk_reload_at'
const CHUNK_RELOAD_COOLDOWN_MS = 15_000
const DEFAULT_FALLBACK_MESSAGE =
  '系统刚刚发布新版本，当前页面资源已更新，请刷新浏览器后继续操作。'

let memoryLastReloadAt = 0

export interface ChunkFallbackNotice {
  targetUrl?: string
  message: string
}

type ChunkFallbackListener = (notice: ChunkFallbackNotice) => void

const fallbackListeners = new Set<ChunkFallbackListener>()
let pendingFallbackNotice: ChunkFallbackNotice | null = null

export function notifyChunkRecoveryFallback(
  targetUrl?: string,
  message: string = DEFAULT_FALLBACK_MESSAGE,
): void {
  const notice: ChunkFallbackNotice = { targetUrl, message }
  pendingFallbackNotice = notice
  for (const listener of fallbackListeners) {
    listener(notice)
  }
}

export function clearChunkRecoveryFallback(): void {
  pendingFallbackNotice = null
}

/**
 * 订阅 chunk 失效降级提示（用于冷却期内不再自动刷新时展示可见提示，避免点击无响应）。
 * 若订阅前已有待展示提示，会立即回放一次当前状态。
 */
export function onChunkRecoveryFallback(listener: ChunkFallbackListener): () => void {
  fallbackListeners.add(listener)
  if (pendingFallbackNotice) {
    listener(pendingFallbackNotice)
  }
  return () => {
    fallbackListeners.delete(listener)
  }
}

/**
 * 识别动态导入 / 静态 Chunk 失效错误（典型于发版覆盖前端产物后，旧页面仍请求旧 hash 资源）。
 */
export function isDynamicImportChunkError(error: unknown): boolean {
  if (!error) return false
  const message =
    typeof error === 'string'
      ? error
      : error instanceof Error
        ? `${error.name || ''} ${error.message || ''}`
        : String((error as any).message || error)
  const lower = message.toLowerCase()
  return (
    lower.includes('failed to fetch dynamically imported module') ||
    lower.includes('importing a module script failed') ||
    lower.includes('error loading dynamically imported module') ||
    lower.includes('loading chunk') ||
    lower.includes('chunkloaderror') ||
    lower.includes('unable to preload css') ||
    lower.includes('loading css chunk')
  )
}

export interface ChunkRecoveryDeps {
  now?: () => number
  getStorage?: () => Storage | null
  reload?: () => void
  assign?: (url: string) => void
  currentLocation?: () => string
}

/**
 * 尝试自动刷新以加载最新的前端资源清单，并带有 15s 冷却守卫防死循环。
 * 返回 true 表示已发起导航或刷新；返回 false 表示在冷却期内已停止自动刷新，应降级提示用户。
 */
export function recoverFromChunkError(targetUrl?: string, deps: ChunkRecoveryDeps = {}): boolean {
  const now = (deps.now ?? (() => Date.now()))()
  let lastReloadAt = memoryLastReloadAt

  const storage =
    deps.getStorage !== undefined
      ? deps.getStorage()
      : (() => {
          try {
            return typeof window !== 'undefined' ? window.sessionStorage : null
          } catch {
            return null
          }
        })()

  if (storage) {
    try {
      const stored = Number(storage.getItem(CHUNK_RELOAD_STORAGE_KEY) || 0)
      if (Number.isFinite(stored) && stored > lastReloadAt) {
        lastReloadAt = stored
      }
    } catch {
      // 禁用 storage 时退回内存时间戳防抖
    }
  }

  if (lastReloadAt > 0 && now - lastReloadAt < CHUNK_RELOAD_COOLDOWN_MS) {
    return false
  }

  memoryLastReloadAt = now
  if (storage) {
    try {
      storage.setItem(CHUNK_RELOAD_STORAGE_KEY, String(now))
    } catch {
      // ignore storage quota errors
    }
  }

  const currentUrl =
    deps.currentLocation !== undefined
      ? deps.currentLocation()
      : typeof window !== 'undefined'
        ? `${window.location.pathname}${window.location.search}`
        : ''
  const targetBase = targetUrl ? targetUrl.split('#')[0] : ''

  if (targetUrl && targetBase && targetBase !== currentUrl) {
    if (deps.assign) {
      deps.assign(targetUrl)
    } else if (typeof window !== 'undefined') {
      window.location.assign(targetUrl)
    }
  } else if (deps.reload) {
    deps.reload()
  } else if (typeof window !== 'undefined') {
    window.location.reload()
  }
  return true
}

export function _resetChunkRecoveryMemoryForTests(): void {
  memoryLastReloadAt = 0
  pendingFallbackNotice = null
  fallbackListeners.clear()
}
