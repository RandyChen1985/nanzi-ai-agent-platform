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
 * 性能上做了两级缓存：内存 → `localStorage` 快照 → 带版本号请求。版本号未变时后端只回
 * `unchanged`，因此每次进后台只是一个几百字节的校验请求，不会重传整棵树。
 */

import { computed, reactive } from 'vue'
import axios from '../utils/axios'

const STORAGE_KEY = 'nav_snapshot_v1'

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
  nodes: NavNode[]
  landing_path: string | null
  fetched_at: number
}

const state = reactive({
  version: 0,
  nodes: [] as NavNode[],
  landingPath: '',
  loaded: false,
  loading: false,
  /** 请求失败但仍有快照可用时为 true：侧栏继续渲染，同时给出"数据可能不是最新"的提示 */
  stale: false,
  error: '',
})

let inflight: Promise<void> | null = null

function readSnapshot(): NavSnapshot | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as NavSnapshot
    if (!parsed || !Array.isArray(parsed.nodes)) return null
    return parsed
  } catch {
    return null
  }
}

function writeSnapshot() {
  try {
    const snapshot: NavSnapshot = {
      version: state.version,
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
  state.nodes = snapshot.nodes
  state.landingPath = snapshot.landing_path || ''
  state.loaded = true
  return true
}

async function fetchNav(force: boolean): Promise<void> {
  state.loading = true
  try {
    const params = !force && state.version > 0 ? { version: state.version } : {}
    const response = await axios.get('/api/portal/menus/nav', { params })
    const data = response.data || {}
    if (data.unchanged) {
      state.stale = false
      state.error = ''
      return
    }
    state.version = Number(data.version || 0)
    state.nodes = Array.isArray(data.nodes) ? data.nodes : []
    state.landingPath = data.landing_path || ''
    state.loaded = true
    state.stale = false
    state.error = ''
    writeSnapshot()
  } catch (error) {
    state.stale = true
    state.error = state.loaded ? '菜单数据可能不是最新' : '菜单加载失败'
    console.error('[useNavigation] 导航加载失败', error)
  } finally {
    state.loading = false
  }
}

/**
 * 确保导航可用。
 *
 * - 有快照时立即返回（首屏不等待网络），后台静默校验版本；
 * - 没有快照时等待请求结果，失败则 `state.error` 有值，由侧栏显示可重试的错误态。
 */
export function ensureNavigation(force = false): Promise<void> {
  if (!state.loaded) {
    hydrateFromSnapshot()
  }
  if (inflight) {
    return inflight
  }
  if (state.loaded && !force) {
    void fetchNav(false)
    return Promise.resolve()
  }
  inflight = fetchNav(force).finally(() => {
    inflight = null
  })
  return inflight
}

/** 强制刷新（菜单管理保存后调用）。 */
export function refreshNavigation(): Promise<void> {
  return ensureNavigation(true)
}

/** 登出时清掉本机快照，避免换账号后短暂看到上一个人的菜单。 */
export function clearNavigationSnapshot(): void {
  state.version = 0
  state.nodes = []
  state.landingPath = ''
  state.loaded = false
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
  if (state.landingPath) return state.landingPath
  const snapshot = readSnapshot()
  return snapshot?.landing_path || ''
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
