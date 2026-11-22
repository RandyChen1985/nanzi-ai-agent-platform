/**
 * 版本检测：当前版本与上游最新 tag 的比对。
 *
 * 判定逻辑**全部在后端**（`app/services/update_check_service.py`），前端只负责展示。
 * 两边各写一套比较器，迟早会演变成「后端说有新版、前端说已最新」——而四段版本
 * 与三段 tag 的补零比较正是最容易写歪的地方。
 *
 * 三处数据落点：
 * - 检查结果：后端 Redis 缓存 24h；GET 读缓存，POST 强制回源（受 60 秒节流）
 * - 「稍后」：sessionStorage，本次会话不再出现，下次打开还会提示
 * - 「忽略此版本」：账号偏好 `agent:portal_prefs:{user_id}.ignored_update_version`，
 *   上游出现更大的 tag 时自然失配，提示自动恢复
 */
import { computed, ref } from "vue"
import axios from "@/utils/axios"

export type UpdateCheckState =
  | "idle"
  | "checking"
  | "latest"
  | "outdated"
  | "ignored"
  | "failed"

export interface UpdateCheckData {
  current: string
  latest: string
  latest_normalized: string
  has_update: boolean
  reason?: string
  published_at: string
  release_url: string
  notes: string[]
  /** 发行说明正文（markdown）。老缓存里可能没有这个字段，所以可选 */
  body?: string
  /** 同一版本在 GitHub 镜像上的发布页（内网里常有只有一个能通的情况） */
  github_release_url?: string
  /** 本次请求被 60 秒节流挡下：结论仍是上次那份，只是没刷新 */
  throttled?: boolean
  has_migrations: boolean
  checked_at: string
  source: string
  error: string | null
  ignored_version?: string
  ignored?: boolean
  should_prompt?: boolean
}

const DEFERRED_KEY = "nanzi_update_deferred"
const CHECK_URL = "/api/portal/system/update-check"

const data = ref<UpdateCheckData | null>(null)
const loading = ref(false)
const failure = ref("")
const deferredVersion = ref(readDeferred())

let inflight: Promise<void> | null = null
let loadedForVersion = ""

function readDeferred(): string {
  try {
    const raw = sessionStorage.getItem(DEFERRED_KEY)
    if (!raw) return ""
    const parsed = JSON.parse(raw)
    return typeof parsed?.version === "string" ? parsed.version : ""
  } catch {
    return ""
  }
}

function writeDeferred(version: string): void {
  deferredVersion.value = version
  try {
    if (version) sessionStorage.setItem(DEFERRED_KEY, JSON.stringify({ version }))
    else sessionStorage.removeItem(DEFERRED_KEY)
  } catch {
    // 隐私模式下 sessionStorage 可能不可写：退化成「本次不记住」，不影响主流程
  }
}

/** 版本号统一剥掉 v 前缀，用于和接口回来的三段 tag 对齐比较。 */
export function normalizeVersion(raw: string): string {
  const text = String(raw || "").trim().replace(/^[vV]/, "")
  if (!text || /^dev/i.test(text)) return ""
  const core = text.split(/[-+]/)[0] ?? ""
  if (!core) return ""
  const parts = core.split(".")
  if (!parts.length || !parts.every((part) => /^\d+$/.test(part))) return ""
  return parts.slice(0, 3).join(".")
}

/** 当前状态：把接口结果、用户「忽略」「稍后」折算成界面要展示的一种。 */
const state = computed<UpdateCheckState>(() => {
  if (loading.value) return "checking"
  if (failure.value) return "failed"
  if (!data.value) return "idle"
  // 节流不是失败：老后端可能仍把提示塞在 error 里，判定时以 throttled 为准
  if (data.value.error && !data.value.throttled) return "failed"
  if (!data.value.has_update) return "latest"
  if (data.value.ignored) return "ignored"
  if (deferredVersion.value && deferredVersion.value === data.value.latest_normalized) {
    return "ignored"
  }
  return "outdated"
})

/** 是否值得在菜单上打点 / 变色：只有「有新版且没被忽略没被稍后」才算。 */
const hasPendingUpdate = computed(() => state.value === "outdated")

/** 发布日期只到天：完整时间戳在 300px 宽的面板里既挤又没用。 */
const publishedDate = computed(() => {
  const raw = data.value?.published_at || ""
  const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})/)
  return match ? `${match[1]}-${match[2]}-${match[3]}` : ""
})

const notes = computed(() => data.value?.notes ?? [])

/** 节流时只给一句灰字说明，不占用「检查失败」的红色位置 */
const throttleHint = computed(() =>
  data.value?.throttled ? "刚刚检查过，请 1 分钟后再试" : "",
)

async function load(currentVersion: string, force: boolean): Promise<void> {
  loading.value = true
  failure.value = ""
  try {
    const params = currentVersion ? { current: currentVersion } : undefined
    const res = force
      ? await axios.post(CHECK_URL, { current: currentVersion })
      : await axios.get(CHECK_URL, { params })
    const payload = (res?.data?.data || null) as UpdateCheckData | null
    if (!payload) {
      failure.value = "检查失败，请稍后重试"
      return
    }
    data.value = payload
    // 节流 / 上游失败的提示语由后端给出（它知道是超时、限流还是离线环境）
    if (payload.error) failure.value = payload.error
  } catch (err: unknown) {
    const detail =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    failure.value = typeof detail === "string" && detail ? detail : "检查失败，请稍后重试"
  } finally {
    loading.value = false
  }
}

/**
 * 打开「关于」时调用：读缓存，不主动打外部接口。
 * 同一版本只自动拉一次，避免来回展开收起菜单反复请求。
 */
export function ensureUpdateChecked(currentVersion: string): Promise<void> {
  if (loadedForVersion === currentVersion && (data.value || failure.value)) {
    return inflight ?? Promise.resolve()
  }
  loadedForVersion = currentVersion
  inflight = load(currentVersion, false)
  return inflight
}

/** 用户点「检查更新」：绕过缓存，受后端 60 秒节流。 */
export function checkForUpdate(currentVersion: string): Promise<void> {
  inflight = load(currentVersion, true)
  return inflight
}

/** 忽略当前这个新版本：写账号偏好，接口失败时把错误抛给调用方提示。 */
export async function ignoreUpdateVersion(version: string): Promise<boolean> {
  const target = normalizeVersion(version)
  if (!target) return false
  try {
    await axios.put("/api/portal/portal-prefs/ignored-update-version", { version: target })
    if (data.value) {
      data.value = {
        ...data.value,
        ignored: true,
        ignored_version: target,
        should_prompt: false,
      }
    }
    return true
  } catch {
    return false
  }
}

/** 「稍后」：本次会话内不再显示，不写后端。 */
export function deferUpdateVersion(version: string): void {
  writeDeferred(normalizeVersion(version))
}

/** 恢复提示（用户点了「恢复提示」时用），清掉会话内忽略与账号偏好。 */
export async function restoreUpdatePrompt(): Promise<void> {
  writeDeferred("")
  const version = data.value?.ignored_version || ""
  if (!version) return
  try {
    await axios.put("/api/portal/portal-prefs/ignored-update-version", { version: "" })
    if (data.value) {
      data.value = { ...data.value, ignored: false, ignored_version: "", should_prompt: true }
    }
  } catch {
    // 恢复失败就保持原状，用户下次点「检查更新」还会重新提示
  }
}

/** 换号 / 退出登录时重置内存态，避免上一个账号的检查结果串到新账号。 */
export function resetUpdateCheck(): void {
  data.value = null
  failure.value = ""
  loading.value = false
  inflight = null
  loadedForVersion = ""
  // 换号时把会话内的「稍后」也清掉：同一标签页换账号不该继承上一个人的忽略态
  writeDeferred("")
}

export function useUpdateCheck() {
  return {
    data,
    state,
    notes,
    loading,
    failure,
    throttleHint,
    hasPendingUpdate,
    publishedDate,
    deferredVersion,
  }
}
