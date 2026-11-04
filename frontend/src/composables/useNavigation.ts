/**
 * 导航树数据源。
 *
 * 改造前侧栏是 `Dashboard.vue` 里的 `menuGroups` 常量，权限过滤靠本地 `hasMenuPerm`。
 * 现在菜单全部入库，由后端 `GET /api/portal/menus/nav` 返回**已按当前用户权限过滤**的树，
 * 前端只负责渲染。这样做的两个理由：
 *
 * 1. 菜单表与权限码只有后端一个判断点，不会再出现"表改了、前端判断没跟上"的漂移；
 * 2. 无权限的菜单不再下发到客户端。
 *
 * 性能上做了两级缓存：内存 → `localStorage` 快照 → 带 `version` + `signature` 的请求。
 * 版本号未变时后端只回 `unchanged`，因此每次进后台只是一个几百字节的校验请求，不会重传整棵树。
 *
 * 快照属于**某个身份**，不是设备级缓存：写入时记录 `user_id`，读取时校验；
 * 条件请求带上服务端下发的 `signature`（版本 + 用户 + 权限集合的指纹）。这两道闸门
 * 解决的是「同一个浏览器换账号后仍看到上一个人的菜单，点进去又被路由守卫弹回工作台」：
 * 以前只比全局菜单版本，只要菜单表没改过，服务端就会一直说"没变"。
 */

import { computed, reactive } from 'vue'
import axios from '../utils/axios'
import { NAV_SNAPSHOT_STORAGE_KEY } from '../utils/sessionCleanup'

const STORAGE_KEY = NAV_SNAPSHOT_STORAGE_KEY

export type NavNodeType = 'group' | 'page' | 'external' | 'element'
export type NavOpenMode = 'new_tab' | 'embed'

export interface NavNode {
  code: string
  parent_code: string | null
  node_type: NavNodeType
  permission_code: string | null
  label: string
  icon: string | null
  path: string | null
  active_route_names: string[]
  url: string | null
  open_mode: NavOpenMode | null
  sort_order: number
  enabled: boolean
  desktop_only: boolean
  is_landing: boolean
  description?: string | null
  children?: NavNode[]
}

interface NavSnapshot {
  version: number
  signature: string
  /** 快照归属的登录身份；旧格式快照没有该字段，读取时直接丢弃 */
  user_id: string
  nodes: NavNode[]
  landing_path: string | null
  fetched_at: number
}

const state = reactive({
  version: 0,
  signature: '',
  userId: '',
  nodes: [] as NavNode[],
  landingPath: '',
  loaded: false,
  loading: false,
  /** 请求失败但仍有快照可用时为 true：侧栏继续渲染，同时给出"数据可能不是最新"的提示 */
  stale: false,
  error: '',
})

let inflight: Promise<void> | null = null
/**
 * 请求序号。登出 / 清快照时 +1，让在途请求的迟到响应作废：
 * 管理员退出前发出的静默校验若在新账号登录后才返回，不能把旧树写回 state 与快照。
 */
let requestSeq = 0

/** 从 user_info 原文解析登录身份；空串表示未登录 / 不可解析。 */
function parseUserId(raw: string | null): string {
  if (!raw) return ''
  try {
    const parsed = JSON.parse(raw) as Record<string, any>
    const id = parsed?.user_id ?? parsed?.id
    return id === undefined || id === null ? '' : String(id)
  } catch {
    return ''
  }
}

/** 当前登录身份（`user_info` 是唯一同步可读的身份来源）。 */
function currentUserId(): string {
  try {
    return parseUserId(localStorage.getItem('user_info'))
  } catch {
    return ''
  }
}

function readSnapshot(): NavSnapshot | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as NavSnapshot
    if (!parsed || !Array.isArray(parsed.nodes)) return null
    // 快照属于某个身份：没有 user_id（升级前的旧格式）或与当前身份不一致时一律丢弃。
    // 否则换账号后首屏会直接渲染上一个人的菜单，还会拿它的 version/signature 去条件请求。
    const ownerId = currentUserId()
    const snapshotOwner = parsed.user_id ? String(parsed.user_id) : ''
    if (!ownerId || !snapshotOwner || snapshotOwner !== ownerId) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    return parsed
  } catch {
    // 损坏的快照不必保留：每次都解析失败，不如直接删掉走全量
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 忽略
    }
    return null
  }
}

function writeSnapshot() {
  try {
    const snapshot: NavSnapshot = {
      version: state.version,
      signature: state.signature,
      user_id: currentUserId(),
      nodes: state.nodes,
      landing_path: state.landingPath || null,
      fetched_at: Date.now(),
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot))
  } catch {
    // 隐私模式/配额满：只影响下次首屏，不影响本次渲染
  }
}

function hydrateFromSnapshot(): boolean {
  const snapshot = readSnapshot()
  if (!snapshot) return false
  state.version = Number(snapshot.version || 0)
  state.signature = snapshot.signature || ''
  state.userId = currentUserId()
  state.nodes = snapshot.nodes
  state.landingPath = snapshot.landing_path || ''
  state.loaded = true
  return true
}

async function fetchNav(force: boolean): Promise<void> {
  const seq = ++requestSeq
  // 请求发出时的身份。响应回来时若 localStorage 里的登录身份已被别的标签页换掉，
  // 这份响应属于「上一个人」，既不能渲染也不能落盘（否则会给新用户贴上一份旧菜单，
  // 只能等下一次条件请求被服务端签名拒绝才自愈）。
  const requestOwner = currentUserId()
  state.loading = true
  try {
    const params: Record<string, string | number> = {}
    // 没有 signature 就不带 version：服务端会拒绝不完整的条件请求（回完整树），
    // 这里提前省掉一次无谓往返。
    if (!force && state.version > 0 && state.signature) {
      params.version = state.version
      params.signature = state.signature
    }
    const response = await axios.get('/api/portal/menus/nav', { params })
    // 登出 / 换号 / 已有更新的请求：迟到的响应一律丢弃
    if (seq !== requestSeq) return
    if (currentUserId() !== requestOwner) return
    const data = response.data || {}
    if (data.unchanged) {
      if (data.signature) state.signature = String(data.signature)
      state.stale = false
      state.error = ''
      return
    }
    state.version = Number(data.version || 0)
    state.signature = typeof data.signature === 'string' ? data.signature : ''
    state.userId = currentUserId()
    state.nodes = Array.isArray(data.nodes) ? data.nodes : []
    state.landingPath = data.landing_path || ''
    state.loaded = true
    state.stale = false
    state.error = ''
    writeSnapshot()
  } catch (error) {
    if (seq !== requestSeq) return
    state.stale = true
    state.error = state.loaded ? '菜单数据可能不是最新' : '菜单加载失败'
    console.error('[useNavigation] 导航加载失败', error)
  } finally {
    if (seq === requestSeq) state.loading = false
  }
}

/**
 * 确保导航可用。
 *
 * - 有快照时立即返回（首屏不等待网络），后台静默校验版本；
 * - 没有快照时等待请求结果，失败则 `state.error` 有值，由侧栏显示可重试的错误态。
 *
 * 静默校验同样登记 `inflight`：登出清理要能作废它，并发调用也不该重复打网。
 */
export function ensureNavigation(force = false): Promise<void> {
  if (!state.loaded) {
    hydrateFromSnapshot()
  }
  if (inflight) {
    return inflight
  }
  if (state.loaded && !force) {
    const background = fetchNav(false).finally(() => {
      if (inflight === background) inflight = null
    })
    inflight = background
    return Promise.resolve()
  }
  const current = fetchNav(force).finally(() => {
    if (inflight === current) inflight = null
  })
  inflight = current
  return current
}

/** 强制刷新（菜单管理保存后调用）。 */
export function refreshNavigation(): Promise<void> {
  return ensureNavigation(true)
}

/**
 * 登出 / 确认换号时清掉本机快照。
 *
 * 除了清存储与内存，还必须让在途请求作废——只清 state 的话，旧身份迟到的响应
 * 会立刻把 `nodes` 和 localStorage 一起写回去，等于白清。
 */
export function clearNavigationSnapshot(): void {
  requestSeq += 1
  inflight = null
  state.version = 0
  state.signature = ''
  state.userId = ''
  state.nodes = []
  state.landingPath = ''
  state.loaded = false
  state.loading = false
  state.stale = false
  state.error = ''
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 忽略
  }
}

/** 路由守卫用：同步读取落地页，不等待网络。 */
export function getSnapshotLandingPath(): string {
  // 内存里的 landing 只有在仍属于当前身份时才能用：同一标签页可能已被别的标签
  // 改成了另一个登录身份（localStorage 共享），此时不能拿旧 landing 当兜底。
  if (state.landingPath && state.userId && state.userId === currentUserId()) {
    return state.landingPath
  }
  const snapshot = readSnapshot()
  return snapshot?.landing_path || ''
}

// 另一个标签页换了登录身份：本标签页内存里的旧菜单要立刻作废。
// `storage` 事件只在**其他**标签页写入时触发，所以这里不会和本标签的登出清理打架。
// 只认「身份真的变了」：同一个人刷新 / `/auth/me` 回写 user_info 也会触发该事件，
// 那种情况清空重拉只会让侧栏白闪一下。
if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('storage', (event) => {
    if (event.key !== 'user_info') return
    if (parseUserId(event.oldValue) === parseUserId(event.newValue)) return
    const wasLoaded = state.loaded
    clearNavigationSnapshot()
    if (wasLoaded && window.location.pathname.startsWith('/dashboard')) {
      void ensureNavigation()
    }
  })
}

export function useNavigation() {
  const groups = computed(() => state.nodes)
  const isReady = computed(() => state.loaded)
  const isLoading = computed(() => state.loading)
  const isStale = computed(() => state.stale)
  const error = computed(() => state.error)
  const version = computed(() => state.version)
  const landingPath = computed(() => state.landingPath)

  const findNode = (code: string): NavNode | null => {
    const stack: NavNode[] = [...state.nodes]
    while (stack.length) {
      const node = stack.shift() as NavNode
      if (node.code === code) return node
      if (node.children?.length) stack.push(...node.children)
    }
    return null
  }

  return {
    groups,
    isReady,
    isLoading,
    isStale,
    error,
    version,
    landingPath,
    findNode,
    ensureNavigation,
    refreshNavigation,
  }
}
