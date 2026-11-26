/**
 * 登录页视觉风格偏好（8 套主题，默认 f1）。
 *
 * - 权威源：后端 Redis（`agent:portal_prefs:{user_id}` 的 `login_theme` 字段），跨设备同步；
 * - 本机镜像：`localStorage['nanzi_login_theme']`，登出时不清除，保证未登录访问 `/login`
 *   时首帧同步渲染用户上次选好的风格、零闪屏；全新浏览器则回落默认 `f1`。
 */
import { ref } from 'vue'
import axios from '@/utils/axios'
import {
  DEFAULT_LOGIN_THEME,
  LOGIN_THEME_STORAGE_KEY,
  normalizeLoginTheme,
  type LoginThemeId,
} from '@/constants/loginThemes'

export type SaveLoginThemeStatus = 'saved' | 'local-only' | 'failed'

function readCurrentUserId(): string {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return ''
    const raw = window.localStorage.getItem('user_info')
    const parsed = raw ? JSON.parse(raw) : null
    return String(parsed?.user_id ?? parsed?.id ?? '')
  } catch {
    return ''
  }
}

export function readCachedLoginTheme(): LoginThemeId {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return DEFAULT_LOGIN_THEME
    return normalizeLoginTheme(window.localStorage.getItem(LOGIN_THEME_STORAGE_KEY))
  } catch {
    return DEFAULT_LOGIN_THEME
  }
}

export function persistCachedLoginTheme(theme: LoginThemeId): void {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(LOGIN_THEME_STORAGE_KEY, normalizeLoginTheme(theme))
  } catch {
    /* 隐私模式写不进去时静默忽略，仅少一层本机首帧镜像 */
  }
}

const loginTheme = ref<LoginThemeId>(readCachedLoginTheme())
let loadedForUserId: string | null = null
let inflight: Promise<void> | null = null
let mutationVersion = 0
let storageListenerBound = false

/** 供外部发起 `/portal-prefs` 请求前记录快照版本，避免慢响应倒灌覆盖最新选择。 */
export function getLoginThemeMutationVersion(): number {
  return mutationVersion
}

function ensureStorageListener(): void {
  if (storageListenerBound || typeof window === 'undefined') return
  storageListenerBound = true
  window.addEventListener('storage', (event: StorageEvent) => {
    if (event.key !== LOGIN_THEME_STORAGE_KEY) return
    mutationVersion += 1
    loginTheme.value = normalizeLoginTheme(event.newValue)
  })
}

/** 收到 `/api/portal/portal-prefs` 响应时顺带同步，避免多发一次请求。 */
export function syncLoginThemeFromPrefs(rawTheme: unknown, expectedVersion?: number): void {
  if (rawTheme === undefined || rawTheme === null) return
  if (expectedVersion !== undefined && expectedVersion !== mutationVersion) return
  const normalized = normalizeLoginTheme(rawTheme)
  loginTheme.value = normalized
  persistCachedLoginTheme(normalized)
  loadedForUserId = readCurrentUserId()
}

/** 个人中心等处按需拉取一次当前账号的服务端权威值（换号后自动重拉，防慢响应倒灌）。 */
export function ensureLoginTheme(force = false): Promise<void> {
  const currentUserId = readCurrentUserId()
  if (!force && loadedForUserId !== null && loadedForUserId === currentUserId) {
    return Promise.resolve()
  }
  if (inflight) return inflight
  const startVersion = mutationVersion
  inflight = (async () => {
    try {
      const res = await axios.get('/api/portal/portal-prefs')
      if (mutationVersion !== startVersion) return
      const serverTheme = res?.data?.data?.login_theme
      if (serverTheme !== undefined) {
        syncLoginThemeFromPrefs(serverTheme, startVersion)
      } else {
        loadedForUserId = currentUserId
      }
    } catch {
      /* 读失败时保持本机缓存或默认 f1，不阻断页面 */
    } finally {
      inflight = null
    }
  })()
  return inflight
}

export async function setLoginTheme(
  next: LoginThemeId,
  options?: { allowLocalFallback?: boolean },
): Promise<SaveLoginThemeStatus> {
  const target = normalizeLoginTheme(next)
  const previous = loginTheme.value
  const reqVersion = ++mutationVersion
  loginTheme.value = target
  persistCachedLoginTheme(target)
  try {
    await axios.put(
      '/api/portal/portal-prefs/login-theme',
      { login_theme: target },
      options?.allowLocalFallback ? { headers: { 'X-Ignore-Auth-Redirect': '1' } } : undefined,
    )
    loadedForUserId = readCurrentUserId()
    return 'saved'
  } catch (err: any) {
    // 预览模式下用户尚未登录（401）：允许保留本机镜像生效，不回滚也不触发跳转
    if (options?.allowLocalFallback && err?.response?.status === 401) {
      return 'local-only'
    }
    // 仅在没有更新的并发切换覆盖它时才回滚，避免旧失败请求回滚掉用户最新选择
    if (mutationVersion === reqVersion) {
      loginTheme.value = previous
      persistCachedLoginTheme(previous)
    }
    return 'failed'
  }
}

export function useLoginTheme() {
  ensureStorageListener()
  return {
    loginTheme,
    ensureLoginTheme,
    setLoginTheme,
  }
}
