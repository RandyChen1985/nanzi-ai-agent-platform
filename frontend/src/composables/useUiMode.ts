/**
 * 界面模式：用户模式 / 专业模式。
 *
 * 账号级偏好存在 Redis（`agent:portal_prefs:{user_id}` 的 `ui_mode` 字段），
 * 后端读取时会按角色补默认值（管理员 pro、其他 user），所以这里拿到的一定是
 * 两个合法值之一。localStorage 只做「接口回来前先用上次的值渲染」的本机缓存，
 * 且按 user_id 分键——换号后不会串用上一个账号的模式（和菜单缓存同一个坑）。
 */
import { computed, ref } from "vue"
import axios from "@/utils/axios"
import { useAppTheme } from "./useAppTheme"

export type UiMode = "user" | "pro"

const CACHE_PREFIX = "nanzi_ui_mode"
const THEME_CACHE_PREFIX = "nanzi_ui_mode_theme"
const VALID_MODES: readonly UiMode[] = ["user", "pro"]

function readCurrentUser(): { id: string; role: string } {
  try {
    const raw = localStorage.getItem("user_info")
    const parsed = raw ? JSON.parse(raw) : null
    return {
      id: String(parsed?.user_id ?? parsed?.id ?? ""),
      role: String(parsed?.role ?? ""),
    }
  } catch {
    return { id: "", role: "" }
  }
}

function cacheKey(userId: string): string {
  return `${CACHE_PREFIX}:${userId || "anon"}`
}

function readCachedMode(): UiMode | null {
  try {
    const raw = localStorage.getItem(cacheKey(readCurrentUser().id))
    return raw === "user" || raw === "pro" ? raw : null
  } catch {
    return null
  }
}

function persistCachedMode(mode: UiMode): void {
  try {
    localStorage.setItem(cacheKey(readCurrentUser().id), mode)
  } catch {
    /* 隐私模式等写不进去，忽略：只是少一层首屏缓存 */
  }
}

/**
 * 两种模式各自的默认主题。
 *
 * - 用户模式 → 亮色：简化后的侧栏与内容是「白底 + 灰底」的排版，深色下层级会糊在一起；
 *   普通用户一年也未必打开一次设置，默认值比可配置更重要。
 * - 专业模式 → 深色：这是平台原本的视觉，信息密度高、以深色侧栏为主。
 *
 * 只在**进入**对应模式时应用一次；用户之后手动切主题不会被反复拽回来。
 * 每个模式还会记住自己上一次的主题：在用户模式里手动切了深色，去专业模式再切回来
 * 仍然是深色，而不是被「默认亮色」覆盖。
 */
function themeCacheKey(mode: UiMode, userId: string): string {
  return `${THEME_CACHE_PREFIX}:${userId || "anon"}:${mode}`
}

function readModeTheme(mode: UiMode): "light" | "dark" | null {
  try {
    const raw = localStorage.getItem(themeCacheKey(mode, readCurrentUser().id))
    return raw === "light" || raw === "dark" ? raw : null
  } catch {
    return null
  }
}

function persistModeTheme(mode: UiMode, theme: "light" | "dark"): void {
  try {
    localStorage.setItem(themeCacheKey(mode, readCurrentUser().id), theme)
  } catch {
    /* 隐私模式等写不进去，忽略：只是少一层「记住上次主题」 */
  }
}

/** 把当前主题记到「即将离开的模式」名下，供下次切回来时恢复。 */
export function rememberThemeForMode(mode: UiMode): void {
  persistModeTheme(mode, useAppTheme().theme.value)
}

/** 进入某个模式时该用的主题：优先用该模式上一次记住的，其次用该模式的默认值。 */
export function resolveModeTheme(mode: UiMode): "light" | "dark" {
  return readModeTheme(mode) ?? (mode === "user" ? "light" : "dark")
}

export function applyModeTheme(mode: UiMode): void {
  const { setTheme } = useAppTheme()
  setTheme(resolveModeTheme(mode))
}

export function applyUserModeDefaultTheme(): void {
  applyModeTheme("user")
}

export function applyProModeDefaultTheme(): void {
  applyModeTheme("pro")
}

/** 角色默认值，与后端 `_resolve_ui_mode` 保持一致 */
export function defaultUiMode(role = readCurrentUser().role): UiMode {
  return role.trim().toLowerCase() === "admin" ? "pro" : "user"
}

/**
 * 同步读取本机缓存的模式。
 *
 * 给「还没拿到接口结果就要做决定」的地方用：登录落地、路由守卫兜底。
 * 返回 null 表示这个人还没选过，调用方按角色给默认。
 */
export function getCachedUiMode(): UiMode | null {
  return readCachedMode()
}

/**
 * 登录瞬间用的模式判断：先看本机缓存（同一个人上次选过什么），否则按角色给默认。
 *
 * 登录页还没拉 `/portal-prefs`，等接口回来再决定落地页会闪一下，
 * 所以这里只做同步判断；真正的权威值随后由 `ensureUiMode()` 覆盖。
 */
export function resolveInitialUiMode(role?: string): UiMode {
  return readCachedMode() || defaultUiMode(role ?? readCurrentUser().role)
}

// 单例：侧栏、个人中心、路由守卫看到的是同一份状态
const uiMode = ref<UiMode>(readCachedMode() || defaultUiMode())
const ready = ref(false)
let loadedForUserId = ""
let inflight: Promise<void> | null = null

async function loadFromServer(): Promise<void> {
  try {
    const res = await axios.get("/api/portal/portal-prefs")
    const server = res?.data?.data?.ui_mode
    if (server === "user" || server === "pro") {
      uiMode.value = server
      persistCachedMode(server)
    }
  } catch {
    // 接口失败就用缓存 / 角色默认值，不阻断页面渲染
  } finally {
    ready.value = true
  }
}

/**
 * 进入管理台时调用。同一账号只拉一次；换号会重新拉，
 * 避免上一个账号的偏好留在内存里。
 */
export function ensureUiMode(): Promise<void> {
  const { id } = readCurrentUser()
  if (loadedForUserId !== id) {
    loadedForUserId = id
    ready.value = false
    uiMode.value = readCachedMode() || defaultUiMode()
    inflight = loadFromServer()
  }
  return inflight ?? Promise.resolve()
}

/** 切换模式。返回是否保存成功——失败时由调用方提示，并把状态回滚。 */
export async function setUiMode(mode: UiMode): Promise<boolean> {
  if (!VALID_MODES.includes(mode)) return false
  const previous = uiMode.value
  // 已经是这个模式：不重复写接口，也不要把当前主题重置成该模式的默认值
  if (previous === mode) return true
  uiMode.value = mode
  persistCachedMode(mode)
  try {
    await axios.put("/api/portal/portal-prefs/ui-mode", { ui_mode: mode })
    // 先记住离开的那个模式当前用的主题，再进入新模式的「上次主题 / 默认主题」
    rememberThemeForMode(previous)
    if (mode === "user") applyUserModeDefaultTheme()
    else applyProModeDefaultTheme()
    ready.value = true
    return true
  } catch {
    uiMode.value = previous
    persistCachedMode(previous)
    return false
  }
}

export function useUiMode() {
  const isUserMode = computed(() => uiMode.value === "user")
  const isProMode = computed(() => uiMode.value === "pro")
  return {
    uiMode,
    ready,
    isUserMode,
    isProMode,
    ensureUiMode,
    setUiMode,
  }
}

/** 供测试或退出登录时清理内存状态 */
export function resetUiModeState(): void {
  loadedForUserId = ""
  inflight = null
  ready.value = false
  uiMode.value = defaultUiMode()
}
