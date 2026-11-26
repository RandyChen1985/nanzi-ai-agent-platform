import api from '@/utils/axios'
import { ref } from 'vue'
import {
  DEFAULT_BRANDING,
  DEFAULT_REPO_URL,
  type PublicBranding,
} from '@/constants/branding'

/**
 * 公开品牌信息的本机快照（首帧防闪）。
 *
 * ## 问题
 *
 * `branding` 的初值是 `DEFAULT_BRANDING`（`/favicon.svg` + 默认产品名），真正的配置要等
 * `GET /api/portal/auth/branding` 返回才知道。于是每次刷新都会先按默认值画一次 logo，
 * 几十毫秒后跳成管理员配置的 logo——肉眼就是「logo 先闪一下默认图标」。
 *
 * ## 做法
 *
 * 1. 首帧读上次拿到的配置做快照，刷新时直接画正确品牌；
 * 2. 请求回来后**无条件覆盖**：管理员改了 logo、或把品牌功能关掉，下一次渲染就纠正；
 * 3. 没有快照时（首次访问 / 清了缓存）用 `brandingReady` 判据：组件在拿到真配置之前
 *    不画 logo，宁可用同尺寸占位，也不要「先画错再改对」；
 * 4. 请求失败保留快照，不把已经画对的品牌改回默认值（一次接口抖动不该让整站换 logo）。
 */
const BRANDING_CACHE_KEY = 'nanzi_public_branding'

function readCachedBranding(): PublicBranding | null {
  if (typeof localStorage === 'undefined') return null
  try {
    const raw = localStorage.getItem(BRANDING_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PublicBranding> | null
    if (!parsed || typeof parsed !== 'object') return null
    // 只接受形状对得上的快照：手改过、或来自旧版本字段的数据，宁可退回默认值
    if (typeof parsed.icon_url !== 'string' || typeof parsed.product_name !== 'string') {
      return null
    }
    return { ...DEFAULT_BRANDING, ...parsed }
  } catch {
    return null
  }
}

function writeCachedBranding(value: PublicBranding) {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(BRANDING_CACHE_KEY, JSON.stringify(value))
  } catch {
    // 隐私模式 / 配额满：缓存只是首帧优化，写不进去不影响功能
  }
}

const cachedBranding = readCachedBranding()
const branding = ref<PublicBranding>({ ...(cachedBranding ?? DEFAULT_BRANDING) })
/**
 * 是否已经可以画品牌：有快照时首帧即可画；没有快照则等 `loadBranding()` 落地。
 * 组件用它决定「先渲染 logo」还是「先渲染同尺寸占位」，避免默认 logo 闪一下。
 */
const brandingReady = ref<boolean>(cachedBranding !== null)
let loadPromise: Promise<PublicBranding> | null = null

function applyFavicon(iconUrl: string) {
  if (typeof document === 'undefined') return
  const href = iconUrl || DEFAULT_BRANDING.icon_url
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (!link) {
    link = document.createElement('link')
    link.rel = 'icon'
    document.head.appendChild(link)
  }
  link.href = href
}

export function applyDocumentTitle(pageTitle?: string) {
  const name = branding.value.product_name || DEFAULT_BRANDING.product_name
  document.title = pageTitle ? `${pageTitle} - ${name}` : name
}

export function resolveRepoUrl(): string {
  if (branding.value.hide_version_link) return ''
  return DEFAULT_REPO_URL
}

// 有快照时立刻设好 tab 图标：否则浏览器会先用 index.html 里的默认 favicon，
// 等接口回来才换（和页面里的 logo 是同一类闪烁，只是发生在标签页上）。
if (cachedBranding) applyFavicon(cachedBranding.icon_url)

export async function loadBranding(force = false): Promise<PublicBranding> {
  if (!force && loadPromise) return loadPromise

  loadPromise = (async () => {
    try {
      const res = await api.get('/api/portal/auth/branding')
      const data = res.data?.data ?? res.data
      branding.value = { ...DEFAULT_BRANDING, ...data }
      writeCachedBranding(branding.value)
    } catch {
      // 接口抖动：保留本机快照（没有快照时 branding 本就是 DEFAULT_BRANDING），
      // 允许下次调用重试，但不要把已经画对的品牌改回默认值
      loadPromise = null
    } finally {
      // 无论成功还是失败，都已经有了「本次会话能确定的值」，可以画 logo 了
      brandingReady.value = true
      applyFavicon(branding.value.icon_url)
      applyDocumentTitle()
    }
    return branding.value
  })()

  return loadPromise
}

export function useBranding() {
  return {
    branding,
    brandingReady,
    loadBranding,
    applyDocumentTitle,
    applyFavicon,
    resolveRepoUrl,
  }
}
