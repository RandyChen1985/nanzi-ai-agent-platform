/**
 * 浏览器类型与版本检测工具 (Browser Detect Utility)
 * 用于识别用户浏览器内核及版本，检测是否属于低版本或不受支持的旧浏览器，并提供升级引导依据。
 */

export interface BrowserDetectResult {
  name: string
  version: string
  majorVersion: number
  isSupported: boolean
  isLowVersion: boolean
  recommendation: string
  downloadUrl: string
}

export const RECOMMENDED_CHROME_URL = 'https://www.google.cn/chrome/'
export const RECOMMENDED_EDGE_URL = 'https://www.microsoft.com/edge'

const SESSION_DISMISS_KEY = 'nanzi_dismiss_browser_upgrade'

// 最低推荐主版本号基线
export const MIN_RECOMMENDED_VERSIONS: Record<string, number> = {
  Chrome: 90,
  Edge: 90,
  Firefox: 90,
  Safari: 15,
  Opera: 76,
}

/**
 * 解析用户代理字符串，提取浏览器名称和版本
 */
export function parseBrowserInfo(userAgent: string): { name: string; version: string; majorVersion: number } {
  const ua = userAgent || ''

  // 1. Internet Explorer / Trident
  if (/Trident\/|MSIE\s/i.test(ua)) {
    let ver = '11.0'
    const msieMatch = ua.match(/MSIE\s([0-9.]+)/i)
    const rvMatch = ua.match(/rv:([0-9.]+)/i)
    if (msieMatch) ver = msieMatch[1]
    else if (rvMatch) ver = rvMatch[1]
    const major = parseInt(ver, 10) || 11
    return { name: 'Internet Explorer', version: ver, majorVersion: major }
  }

  // 2. Microsoft Edge (Edg/ or Edge/)
  const edgeMatch = ua.match(/Edg(?:e)?\/([0-9.]+)/i)
  if (edgeMatch) {
    const ver = edgeMatch[1]
    const major = parseInt(ver, 10) || 0
    return { name: 'Edge', version: ver, majorVersion: major }
  }

  // 3. Opera (OPR/)
  const operaMatch = ua.match(/OPR\/([0-9.]+)/i)
  if (operaMatch) {
    const ver = operaMatch[1]
    const major = parseInt(ver, 10) || 0
    return { name: 'Opera', version: ver, majorVersion: major }
  }

  // 4. Firefox
  const firefoxMatch = ua.match(/(?:Firefox|FxiOS)\/([0-9.]+)/i)
  if (firefoxMatch) {
    const ver = firefoxMatch[1]
    const major = parseInt(ver, 10) || 0
    return { name: 'Firefox', version: ver, majorVersion: major }
  }

  // 5. Chrome / Chromium (需在 Edge, Opera 等之后判定)
  const chromeMatch = ua.match(/(?:Chrome|CriOS)\/([0-9.]+)/i)
  if (chromeMatch) {
    const ver = chromeMatch[1]
    const major = parseInt(ver, 10) || 0
    return { name: 'Chrome', version: ver, majorVersion: major }
  }

  // 6. Safari (排除 Chrome/CriOS/Android 伪装)
  const safariMatch = ua.match(/Version\/([0-9.]+).*Safari/i)
  if (safariMatch && !/Chrome|Android|CriOS/i.test(ua)) {
    const ver = safariMatch[1]
    const major = parseInt(ver, 10) || 0
    return { name: 'Safari', version: ver, majorVersion: major }
  }

  return { name: 'Unknown', version: 'Unknown', majorVersion: 0 }
}

/**
 * 现代核心特性能力探测（作为兜底保底）
 */
export function probeModernFeatures(): boolean {
  if (typeof window === 'undefined') return true
  try {
    const hasPromiseAllSettled = typeof Promise !== 'undefined' && typeof (Promise as any).allSettled === 'function'
    const hasResizeObserver = typeof window.ResizeObserver !== 'undefined'
    const hasCrypto = typeof window.crypto !== 'undefined'
    return hasPromiseAllSettled && hasResizeObserver && hasCrypto
  } catch {
    return false
  }
}

/**
 * 检测当前浏览器版本及兼容性
 */
export function detectBrowser(customUa?: string): BrowserDetectResult {
  const ua = customUa !== undefined ? customUa : (typeof navigator !== 'undefined' ? navigator.userAgent : '')
  const { name, version, majorVersion } = parseBrowserInfo(ua)
  const modernFeaturesSupported = probeModernFeatures()

  // 1. IE 判定：完全不支持
  if (name === 'Internet Explorer') {
    return {
      name,
      version,
      majorVersion,
      isSupported: false,
      isLowVersion: true,
      recommendation: '平台已全面停止对 Internet Explorer 的支持，请升级至 Google Chrome 或 Microsoft Edge 浏览器。',
      downloadUrl: RECOMMENDED_CHROME_URL
    }
  }

  // 2. 主流浏览器最低版本判断
  const minRequired = MIN_RECOMMENDED_VERSIONS[name]
  let isLow = false
  if (minRequired && majorVersion > 0 && majorVersion < minRequired) {
    isLow = true
  }

  // 3. 特性缺失兜底
  if (!modernFeaturesSupported && majorVersion > 0) {
    isLow = true
  }

  const isSupported = !isLow

  return {
    name: name === 'Unknown' ? '浏览器' : name,
    version: version === 'Unknown' ? '' : version,
    majorVersion,
    isSupported,
    isLowVersion: isLow,
    recommendation: isLow 
      ? `检测到您正在使用较旧版本的 ${name} (${version || '旧版本'})，建议升级或使用最新版 Google Chrome 获得最佳体验。`
      : '当前浏览器版本符合平台运行要求。',
    downloadUrl: RECOMMENDED_CHROME_URL
  }
}

/**
 * 检查当前会话是否已经关闭/忽略过升级引导
 */
export function isBrowserUpgradeDismissed(): boolean {
  if (typeof window === 'undefined' || !window.sessionStorage) return false
  try {
    return window.sessionStorage.getItem(SESSION_DISMISS_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * 记录会话内忽略升级提示
 */
export function dismissBrowserUpgrade(): void {
  if (typeof window === 'undefined' || !window.sessionStorage) return
  try {
    window.sessionStorage.setItem(SESSION_DISMISS_KEY, '1')
  } catch {
    // 容错处理
  }
}
