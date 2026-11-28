<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '../../composables/useToast'
import { copyToClipboard } from '../../utils/clipboard'
import {
  ArrowPathIcon,
  ClipboardDocumentIcon,
  CubeIcon,
  ExclamationTriangleIcon,
  MagnifyingGlassIcon,
  ServerStackIcon,
  TrashIcon,
  XMarkIcon,
} from '@heroicons/vue/24/outline'

export type SandboxAdminBackend = 'docker' | 'k8s'
export type SandboxCategory = 'managed' | 'orphan' | 'zombie'

export interface SandboxInstanceItem {
  id: string
  short_id: string
  uid?: string
  name: string
  namespace?: string
  workspace_id: string
  user_key: string
  skill_token: string
  image: string
  runtime_state: string
  state_detail: string
  category: SandboxCategory
  in_cache: boolean
  refcount: number
  created_at: string | null
  age_seconds: number | null
  idle_seconds_left: number | null
  /** 基础空闲阈值（秒），来自 sandbox_idle_time；用于解释剩余时间是它的几倍 */
  idle_seconds?: number | null
  /** 空闲阈值放宽倍数：仍被会话引用时为 4，否则 1 */
  idle_multiplier?: number | null
  node_or_host: string
  mount_summary: string
  cpu_percent?: number | null
  cpu_millicores?: number | null
  cpu_limit_millicores?: number | null
  memory_usage_bytes?: number | null
  memory_limit_bytes?: number | null
  pids_current?: number | null
  pids_limit?: number | null
}

export interface SandboxAdminSummary {
  all: number
  managed: number
  orphan: number
  zombie: number
}

interface SandboxInstanceDetail {
  backend: SandboxAdminBackend
  id: string
  short_id: string
  uid?: string
  name: string
  namespace?: string
  workspace_id: string
  user_key: string
  skill_token: string
  image: string
  runtime_state: string
  oom_killed?: boolean
  exit_code?: number | null
  init_enabled?: boolean
  node_or_host?: string
  created_at?: string | null
  started_at?: string | null
  cpu_percent?: number | null
  cpu_millicores?: number | null
  memory_usage_bytes?: number | null
  memory_limit_bytes?: number | null
  pids_current?: number | null
  pids_limit?: number | null
  mounts: string[]
  labels: Record<string, string>
  logs: string
}

interface ConfirmDialogState {
  open: boolean
  title: string
  desc: string
  warn: string
  okLabel: string
  danger: boolean
  showOrphanCheckbox: boolean
  includeOrphans: boolean
  onConfirm: ((includeOrphans: boolean) => Promise<void>) | null
}

const props = defineProps<{
  show: boolean
  backend: SandboxAdminBackend
  canManage: boolean
  orphanReconcileEnabled?: boolean
}>()

const emit = defineEmits<{
  close: []
  'summary-change': [payload: { backend: SandboxAdminBackend; summary: SandboxAdminSummary; available: boolean; scopeLabel: string }]
}>()

const { showToast } = useToast()

const loading = ref(false)
const statsLoading = ref(false)
const operating = ref(false)
const available = ref(true)
const loadError = ref('')
const scopeLabel = ref('')
const runtimeDesc = ref('')
const lastRefreshedAt = ref('')
const items = ref<SandboxInstanceItem[]>([])
const summary = ref<SandboxAdminSummary>({ all: 0, managed: 0, orphan: 0, zombie: 0 })

const activeFilter = ref<'all' | SandboxCategory>('all')
const searchKeyword = ref('')
const selectedIds = ref<Set<string>>(new Set())
const autoRefresh = ref(false)

const detailOpen = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const detailData = ref<SandboxInstanceDetail | null>(null)
const detailTargetItem = ref<SandboxInstanceItem | null>(null)

const confirmState = ref<ConfirmDialogState>({
  open: false,
  title: '',
  desc: '',
  warn: '',
  okLabel: '确认执行',
  danger: true,
  showOrphanCheckbox: false,
  includeOrphans: true,
  onConfirm: null,
})

let fetchSeq = 0
let detailSeq = 0
let autoRefreshTimer: number | null = null

const isK8s = computed(() => props.backend === 'k8s')
const staleCount = computed(() => summary.value.orphan + summary.value.zombie)

const filteredItems = computed(() => {
  const q = searchKeyword.value.trim().toLowerCase()
  return items.value.filter(item => {
    if (activeFilter.value !== 'all' && item.category !== activeFilter.value) return false
    if (!q) return true
    return (
      item.user_key.toLowerCase().includes(q) ||
      item.name.toLowerCase().includes(q) ||
      item.id.toLowerCase().includes(q) ||
      item.skill_token.toLowerCase().includes(q) ||
      item.state_detail.toLowerCase().includes(q)
    )
  })
})

const allVisibleSelected = computed(() => {
  if (filteredItems.value.length === 0) return false
  return filteredItems.value.every(item => selectedIds.value.has(item.id))
})

const someVisibleSelected = computed(() => {
  if (filteredItems.value.length === 0) return false
  const hit = filteredItems.value.filter(item => selectedIds.value.has(item.id)).length
  return hit > 0 && hit < filteredItems.value.length
})

const selectedItems = computed(() =>
  items.value.filter(item => selectedIds.value.has(item.id)),
)

const extractError = (err: any, fallback: string) => {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
    return detail.message
  }
  return err?.response?.data?.message || err?.message || fallback
}

const formatDuration = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—'
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分钟`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时 ${m % 60} 分`
  const d = Math.floor(h / 24)
  return `${d} 天 ${h % 24} 小时`
}

const formatBytes = (bytes: number | null | undefined): string => {
  if (bytes === null || bytes === undefined || bytes < 0) return '—'
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KiB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(0)} MiB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GiB`
}

const formatAgeClass = (item: SandboxInstanceItem): string => {
  const age = item.age_seconds ?? 0
  if (item.category === 'zombie' || age >= 7 * 86400) return 'text-rose-600 font-semibold'
  if (item.category === 'orphan' && age >= 86400) return 'text-amber-600 font-semibold'
  return 'text-gray-800'
}

const formatIdleSubtext = (item: SandboxInstanceItem): string => {
  if (item.runtime_state === 'Terminating') return '正在终止回收中'
  if (item.category === 'zombie') return '异常终态（可立即清理）'
  if (item.category === 'orphan') return '脱离进程注册表（待对账）'
  if (item.idle_seconds_left === null || item.idle_seconds_left === undefined) {
    return '进程受管'
  }
  if (item.idle_seconds_left <= 60) return '即将空闲回收'
  return `${Math.ceil(item.idle_seconds_left / 60)} 分钟后空闲回收`
}

/**
 * 解释「剩余时间为什么不是配置里那个数」。
 *
 * 仍被会话持有引用（refcount > 0）的沙箱，回收器把空闲阈值按 4 倍放宽（心跳只覆盖工具
 * 执行期，长思考期间不刷新，按原阈值会误杀正在跑的会话）。放大是正确行为，但如果页面
 * 只显示放大后的分钟数，管理员拿 sandbox_idle_time=15 一对照就会以为配置没生效。
 */
const formatIdleQuotaNote = (item: SandboxInstanceItem): string => {
  const multiplier = item.idle_multiplier ?? 1
  const baseSeconds = item.idle_seconds ?? 0
  if (multiplier <= 1 || baseSeconds <= 0) return ''
  return `受管引用 · 阈值放宽 ${multiplier}×（基础 ${Math.round(baseSeconds / 60)} 分钟）`
}

const stateBadgeClass = (item: SandboxInstanceItem): string => {
  if (item.category === 'zombie') return 'border-rose-200 bg-rose-50 text-rose-700'
  const st = item.runtime_state.toLowerCase()
  if (st === 'terminating' || st === 'restarting' || st === 'created' || st === 'pending') {
    return 'border-amber-200 bg-amber-50 text-amber-700'
  }
  return 'border-emerald-200 bg-emerald-50 text-emerald-700'
}

const stateDotClass = (item: SandboxInstanceItem): string => {
  if (item.category === 'zombie') return 'bg-rose-500'
  const st = item.runtime_state.toLowerCase()
  if (st === 'terminating' || st === 'restarting' || st === 'created' || st === 'pending') {
    return 'bg-amber-500'
  }
  return 'bg-emerald-500'
}

const formatMemoryCell = (item: SandboxInstanceItem): string => {
  if (item.memory_usage_bytes === null || item.memory_usage_bytes === undefined) {
    return item.memory_limit_bytes ? `— / ${formatBytes(item.memory_limit_bytes)}` : '—'
  }
  if (item.memory_limit_bytes) {
    return `${formatBytes(item.memory_usage_bytes)} / ${formatBytes(item.memory_limit_bytes)}`
  }
  return formatBytes(item.memory_usage_bytes)
}

const formatResourceSub = (item: SandboxInstanceItem): string => {
  if (isK8s.value) {
    const cpuPart =
      item.cpu_millicores !== null && item.cpu_millicores !== undefined
        ? `CPU ${item.cpu_millicores}m`
        : 'CPU —'
    return `${cpuPart} · ${item.mount_summary || '卷挂载'}`
  }
  const cpuPart =
    item.cpu_percent !== null && item.cpu_percent !== undefined
      ? `CPU ${item.cpu_percent.toFixed(1)}%`
      : 'CPU —'
  const pidPart =
    item.pids_current !== null && item.pids_current !== undefined
      ? (item.pids_limit ? `PIDs ${item.pids_current}/${item.pids_limit}` : `PIDs ${item.pids_current}`)
      : 'PIDs —'
  return `${cpuPart} · ${pidPart}`
}

const updateTimeStamp = () => {
  const now = new Date()
  lastRefreshedAt.value = now.toTimeString().slice(0, 8)
}

const syncSelectionWithItems = () => {
  const valid = new Set(items.value.map(i => i.id))
  const next = new Set<string>()
  selectedIds.value.forEach(id => {
    if (valid.has(id)) next.add(id)
  })
  selectedIds.value = next
  if (detailTargetItem.value) {
    const latest = items.value.find(i => i.id === detailTargetItem.value?.id)
    if (latest) detailTargetItem.value = latest
  }
}

const fetchInstances = async (silent = false) => {
  if (!props.canManage) return
  const seq = ++fetchSeq
  statsLoading.value = false
  if (!silent) loading.value = true
  loadError.value = ''

  try {
    // 第一段：不带 stats 秒出列表
    const res = await axios.get('/api/v1/admin/sandbox/instances', {
      params: { backend: props.backend, include_stats: false },
    })
    if (seq !== fetchSeq) return
    const data = res.data?.data ?? res.data
    available.value = Boolean(data?.available)
    loadError.value = data?.error || ''
    scopeLabel.value = data?.scope_label || ''
    runtimeDesc.value = data?.runtime_desc || ''
    items.value = Array.isArray(data?.items) ? data.items : []
    summary.value = data?.summary ?? { all: 0, managed: 0, orphan: 0, zombie: 0 }
    syncSelectionWithItems()
    updateTimeStamp()
    emit('summary-change', {
      backend: props.backend,
      summary: summary.value,
      available: available.value,
      scopeLabel: scopeLabel.value,
    })
  } catch (err: any) {
    if (seq !== fetchSeq) return
    available.value = false
    loadError.value = extractError(err, '获取沙箱列表失败')
    if (!silent) showToast(loadError.value, 'error')
  } finally {
    if (seq === fetchSeq) loading.value = false
  }

  // 第二段：列表可用且存在运行中实例时，后台静默补拉资源 stats，不阻塞首屏
  const hasRunning = items.value.some(
    it => it.category === 'managed' || it.category === 'orphan',
  )
  if (!available.value || !hasRunning || seq !== fetchSeq) return
  statsLoading.value = true
  try {
    const statsRes = await axios.get('/api/v1/admin/sandbox/instances', {
      params: { backend: props.backend, include_stats: true },
    })
    if (seq !== fetchSeq) return
    const statsData = statsRes.data?.data ?? statsRes.data
    if (statsData?.available && Array.isArray(statsData.items)) {
      const byId = new Map<string, SandboxInstanceItem>()
      for (const it of statsData.items as SandboxInstanceItem[]) {
        byId.set(it.id, it)
      }
      items.value = items.value.map(existing => {
        const enriched = byId.get(existing.id)
        if (!enriched) return existing
        return {
          ...existing,
          cpu_percent: enriched.cpu_percent,
          cpu_millicores: enriched.cpu_millicores,
          memory_usage_bytes: enriched.memory_usage_bytes,
          memory_limit_bytes: enriched.memory_limit_bytes ?? existing.memory_limit_bytes,
          pids_current: enriched.pids_current,
          pids_limit: enriched.pids_limit ?? existing.pids_limit,
        }
      })
    }
  } catch {
    // 资源统计是尽力而为的增强字段，失败静默保持 '—'
  } finally {
    if (seq === fetchSeq) statsLoading.value = false
  }
}

const toggleSelectAll = () => {
  const next = new Set(selectedIds.value)
  if (allVisibleSelected.value) {
    filteredItems.value.forEach(i => next.delete(i.id))
  } else {
    filteredItems.value.forEach(i => next.add(i.id))
  }
  selectedIds.value = next
}

const toggleSelectOne = (id: string) => {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

const clearSelection = () => {
  selectedIds.value = new Set()
}

const copyName = async (text: string) => {
  try {
    await copyToClipboard(text)
    showToast(`已复制：${text}`, 'success')
  } catch {
    showToast('复制失败', 'error')
  }
}

const copyLogs = async () => {
  const logs = detailData.value?.logs || ''
  if (!logs.trim()) {
    showToast('当前暂无日志可复制', 'info')
    return
  }
  try {
    await copyToClipboard(logs)
    showToast('已复制最近日志到剪贴板', 'success')
  } catch {
    showToast('复制日志失败', 'error')
  }
}

const openDetail = async (item: SandboxInstanceItem) => {
  if (!props.canManage) return
  const seq = ++detailSeq
  detailTargetItem.value = item
  detailOpen.value = true
  detailLoading.value = true
  detailError.value = ''
  detailData.value = null
  try {
    const res = await axios.get(
      `/api/v1/admin/sandbox/instances/${encodeURIComponent(item.id)}/detail`,
      { params: { backend: props.backend, tail: 120 } },
    )
    if (seq !== detailSeq) return
    detailData.value = res.data?.data ?? res.data
  } catch (err: any) {
    if (seq !== detailSeq) return
    detailError.value = extractError(err, '读取沙箱详情与日志失败')
  } finally {
    if (seq === detailSeq) detailLoading.value = false
  }
}

const closeDetail = () => {
  ++detailSeq
  detailOpen.value = false
  detailLoading.value = false
  detailTargetItem.value = null
  detailData.value = null
  detailError.value = ''
}

const openConfirm = (
  cfg: Omit<ConfirmDialogState, 'open' | 'showOrphanCheckbox' | 'includeOrphans'> & {
    showOrphanCheckbox?: boolean
    includeOrphans?: boolean
  },
) => {
  confirmState.value = {
    showOrphanCheckbox: false,
    includeOrphans: true,
    ...cfg,
    open: true,
  }
}

const closeConfirm = () => {
  confirmState.value.open = false
  confirmState.value.onConfirm = null
}

const runConfirm = async () => {
  const fn = confirmState.value.onConfirm
  const includeOrphans = confirmState.value.includeOrphans
  closeConfirm()
  if (!fn) return
  operating.value = true
  try {
    await fn(includeOrphans)
  } finally {
    operating.value = false
  }
}

const executeKill = async (targetIds: string[]) => {
  if (!props.canManage || targetIds.length === 0) return
  try {
    const res = await axios.post('/api/v1/admin/sandbox/instances/kill', {
      backend: props.backend,
      target_ids: targetIds,
    })
    const data = res.data?.data ?? res.data
    const succeeded = Number(data?.succeeded ?? 0)
    const failed = Number(data?.failed ?? 0)
    if (failed > 0) {
      showToast(`销毁完成：成功 ${succeeded} 台，失败 ${failed} 台`, 'warning')
    } else {
      showToast(`已销毁 ${succeeded} 台沙箱实例`, 'success')
    }
    if (detailTargetItem.value && targetIds.includes(detailTargetItem.value.id)) {
      closeDetail()
    }
    clearSelection()
    await fetchInstances(true)
  } catch (err: any) {
    showToast(extractError(err, '销毁沙箱失败'), 'error')
  }
}

const executeRestart = async (targetIds: string[]) => {
  if (!props.canManage || targetIds.length === 0) return
  try {
    const res = await axios.post('/api/v1/admin/sandbox/instances/restart', {
      backend: props.backend,
      target_ids: targetIds,
    })
    const data = res.data?.data ?? res.data
    const succeeded = Number(data?.succeeded ?? 0)
    const failed = Number(data?.failed ?? 0)
    if (failed > 0) {
      showToast(`重置完成：成功 ${succeeded} 台，失败 ${failed} 台`, 'warning')
    } else {
      showToast(
        isK8s.value
          ? `已重置 ${succeeded} 台 Pod，用户下次使用时将自动拉起新 Pod`
          : `已重置 ${succeeded} 台沙箱容器，用户下次使用时将自动拉起全新健康容器`,
        'success',
      )
    }
    if (detailTargetItem.value && targetIds.includes(detailTargetItem.value.id)) {
      closeDetail()
    }
    clearSelection()
    await fetchInstances(true)
  } catch (err: any) {
    showToast(extractError(err, '重置沙箱失败'), 'error')
  }
}

const executeCleanupStale = async (includeRunningOrphans: boolean) => {
  if (!props.canManage) return
  try {
    const res = await axios.post('/api/v1/admin/sandbox/instances/cleanup-stale', {
      backend: props.backend,
      include_running_orphans: includeRunningOrphans,
    })
    const data = res.data?.data ?? res.data
    const succeeded = Number(data?.succeeded ?? 0)
    const failed = Number(data?.failed ?? 0)
    if (succeeded === 0 && failed === 0) {
      showToast('当前没有符合清理条件的沙箱实例', 'info')
    } else if (failed > 0) {
      showToast(`一键清理完成：回收 ${succeeded} 台，失败 ${failed} 台`, 'warning')
    } else {
      showToast(`一键清理完成，共回收 ${succeeded} 台沙箱`, 'success')
    }
    closeDetail()
    clearSelection()
    await fetchInstances(true)
  } catch (err: any) {
    showToast(extractError(err, '一键清理失败'), 'error')
  }
}

const confirmKillSingle = (item: SandboxInstanceItem) => {
  openConfirm({
    title: '强制 Kill 并销毁沙箱',
    desc: `确认销毁 ${item.user_key} 的沙箱 ${item.name}？将同步清理进程缓存与孤儿技能目录，用户工作区挂载文件完整保留。`,
    warn:
      item.category === 'managed'
        ? '注意：该沙箱当前处于「进程受管 · 运行中」，强制销毁会中断正在执行的命令，用户下次使用时会自动拉起新沙箱。'
        : '',
    okLabel: '确认 Kill 销毁',
    danger: true,
    onConfirm: () => executeKill([item.id]),
  })
}

const confirmRestartSingle = (item: SandboxInstanceItem) => {
  openConfirm({
    title: isK8s.value ? '重置沙箱 Pod' : '重置沙箱容器',
    desc: isK8s.value
      ? `将销毁旧 Pod ${item.name} 并清理进程缓存，用户下次使用时按最新配置自动拉起全新 Pod（工作区挂载文件保留）。`
      : `将销毁旧容器 ${item.name} 并清理进程缓存，用户下次使用时自动拉起带健康 Gateway 的全新容器（宿主机工作区文件完整保留）。`,
    warn:
      item.category === 'managed'
        ? '该沙箱内当前正在运行的命令将被中断。'
        : '',
    okLabel: '确认重置',
    danger: false,
    onConfirm: () => executeRestart([item.id]),
  })
}

const confirmBatchKill = () => {
  const targets = selectedItems.value
  if (targets.length === 0) return
  const managedHit = targets.filter(t => t.category === 'managed').length
  openConfirm({
    title: `批量 Kill 销毁 ${targets.length} 台沙箱`,
    desc: `确认强制销毁已勾选的 ${targets.length} 台沙箱实例？用户工作区挂载文件完整保留。`,
    warn:
      managedHit > 0
        ? `所选项中包含 ${managedHit} 台「进程受管 · 运行中」沙箱，销毁将中断正在执行的任务。`
        : '',
    okLabel: `批量销毁 (${targets.length})`,
    danger: true,
    onConfirm: () => executeKill(targets.map(t => t.id)),
  })
}

const confirmBatchRestart = () => {
  const targets = selectedItems.value
  if (targets.length === 0) return
  openConfirm({
    title: isK8s.value
      ? `批量重置 ${targets.length} 台沙箱 Pod`
      : `批量重置 ${targets.length} 台沙箱容器`,
    desc: `将销毁已勾选的 ${targets.length} 台沙箱并清理进程缓存，用户下次使用时自动拉起全新健康沙箱（工作区文件完整保留）。`,
    warn: '',
    okLabel: `批量重置 (${targets.length})`,
    danger: false,
    onConfirm: () => executeRestart(targets.map(t => t.id)),
  })
}

const confirmCleanupStale = () => {
  if (staleCount.value === 0) return
  const defaultIncludeOrphans =
    summary.value.orphan > 0 && (props.orphanReconcileEnabled ?? true)
  openConfirm({
    title: '一键清理异常 / 孤儿沙箱',
    desc: `当前检测到 ${summary.value.zombie} 台僵死/异常终态实例、${summary.value.orphan} 台脱离本进程注册表的运行中孤儿实例。所有「进程受管 · 运行中」的活跃沙箱不会被触碰。`,
    warn:
      summary.value.orphan > 0 && props.orphanReconcileEnabled === false
        ? '检测到已关闭 sandbox_orphan_reconcile（多副本模式）：默认仅清理僵死终态，避免误删其它平台副本正在使用的孤儿沙箱；确认单副本或无人使用后再勾选下方孤儿项。'
        : '清理后将同步回收对应孤儿技能目录，用户工作区文件完整保留。',
    okLabel: '确认清理',
    danger: true,
    showOrphanCheckbox: summary.value.orphan > 0,
    includeOrphans: defaultIncludeOrphans,
    onConfirm: includeOrphans => executeCleanupStale(includeOrphans),
  })
}

const stopAutoRefreshTimer = () => {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
}

watch(autoRefresh, enabled => {
  stopAutoRefreshTimer()
  if (enabled && props.show) {
    autoRefreshTimer = window.setInterval(() => {
      if (props.show && !operating.value) {
        fetchInstances(true)
      }
    }, 15000)
  }
})

const confirmOkLabel = computed(() => {
  if (!confirmState.value.showOrphanCheckbox) return confirmState.value.okLabel
  const count =
    summary.value.zombie + (confirmState.value.includeOrphans ? summary.value.orphan : 0)
  return count > 0 ? `确认清理 (${count} 台)` : '无可清理实例'
})

watch(
  () => [props.show, props.backend] as const,
  ([visible]) => {
    if (visible) {
      activeFilter.value = 'all'
      searchKeyword.value = ''
      clearSelection()
      closeDetail()
      fetchInstances(false)
      if (autoRefresh.value) {
        stopAutoRefreshTimer()
        autoRefreshTimer = window.setInterval(() => {
          if (props.show && !operating.value) fetchInstances(true)
        }, 15000)
      }
    } else {
      stopAutoRefreshTimer()
      closeDetail()
      closeConfirm()
    }
  },
  { immediate: true },
)

const handleKeydown = (e: KeyboardEvent) => {
  if (!props.show || e.key !== 'Escape') return
  if (confirmState.value.open) {
    closeConfirm()
  } else if (detailOpen.value) {
    closeDetail()
  } else {
    emit('close')
  }
}

watch(
  () => props.show,
  visible => {
    if (visible) window.addEventListener('keydown', handleKeydown)
    else window.removeEventListener('keydown', handleKeydown)
  },
  { immediate: true },
)

onUnmounted(() => {
  stopAutoRefreshTimer()
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="show"
      class="fixed inset-0 z-[110] flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-xs"
      role="dialog"
      aria-modal="true"
      @click.self="emit('close')"
    >
      <div class="relative flex h-[min(86vh,800px)] w-[min(1160px,95vw)] flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl">
        <!-- 头部 -->
        <div class="flex shrink-0 items-center justify-between gap-4 border-b border-gray-200 bg-white px-5 py-4">
          <div class="flex min-w-0 items-center gap-3">
            <div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-primary/20 bg-primary/10 text-primary">
              <ServerStackIcon v-if="isK8s" class="h-5 w-5" />
              <CubeIcon v-else class="h-5 w-5" />
            </div>
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-base font-bold text-gray-900">
                  {{ isK8s ? 'Kubernetes 沙箱 Pod 管理' : 'Docker 沙箱容器管理' }}
                </h3>
                <span
                  v-if="scopeLabel"
                  class="rounded-full border border-primary/20 bg-primary/5 px-2 py-0.5 font-mono text-[11px] font-medium text-primary"
                >{{ scopeLabel }}</span>
              </div>
              <div class="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-gray-500">
                <span>{{ runtimeDesc || '仅展示带 agentscope.workspace=true 标签的平台沙箱实例' }}</span>
                <span v-if="lastRefreshedAt">·</span>
                <span v-if="lastRefreshedAt">最后刷新：{{ lastRefreshedAt }}</span>
                <span v-if="statsLoading" class="text-primary">（资源指标补拉中…）</span>
              </div>
            </div>
          </div>

          <div class="flex shrink-0 items-center gap-2">
            <label class="mr-1 inline-flex cursor-pointer select-none items-center gap-1.5 text-xs text-gray-500">
              <input
                v-model="autoRefresh"
                type="checkbox"
                class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
              />
              <span>15s 自动刷新</span>
            </label>
            <button
              type="button"
              :disabled="loading || operating"
              class="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
              @click="fetchInstances(false)"
            >
              <ArrowPathIcon class="h-3.5 w-3.5" :class="loading ? 'animate-spin' : ''" />
              <span>刷新</span>
            </button>
            <button
              type="button"
              class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800"
              title="关闭 (ESC)"
              @click="emit('close')"
            >
              <XMarkIcon class="h-4 w-4" />
            </button>
          </div>
        </div>

        <!-- 一体化 KPI 状态条（点击联动筛选） -->
        <div class="grid shrink-0 grid-cols-2 divide-y divide-gray-200 border-b border-gray-200 bg-gray-50/70 sm:grid-cols-4 sm:divide-x sm:divide-y-0">
          <button
            type="button"
            class="relative px-4 py-3 text-left transition-colors hover:bg-white"
            :class="activeFilter === 'all' ? 'bg-white' : ''"
            @click="activeFilter = 'all'"
          >
            <div class="text-xs text-gray-500">全部沙箱实例</div>
            <div class="mt-0.5 flex items-baseline gap-2">
              <span class="text-xl font-bold tabular-nums text-gray-900">{{ summary.all }}</span>
              <span class="text-[11px] text-gray-400">总计</span>
            </div>
            <span v-if="activeFilter === 'all'" class="absolute inset-x-0 bottom-0 h-0.5 bg-primary"></span>
          </button>

          <button
            type="button"
            class="relative px-4 py-3 text-left transition-colors hover:bg-white"
            :class="activeFilter === 'managed' ? 'bg-white' : ''"
            @click="activeFilter = 'managed'"
          >
            <div class="flex items-center gap-1.5 text-xs text-gray-500">
              <span class="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
              <span>进程受管 · 运行中</span>
            </div>
            <div class="mt-0.5 flex items-baseline gap-2">
              <span class="text-xl font-bold tabular-nums text-emerald-600">{{ summary.managed }}</span>
              <span class="text-[11px] text-gray-400">注册表内活跃</span>
            </div>
            <span v-if="activeFilter === 'managed'" class="absolute inset-x-0 bottom-0 h-0.5 bg-primary"></span>
          </button>

          <button
            type="button"
            class="relative px-4 py-3 text-left transition-colors hover:bg-white"
            :class="activeFilter === 'orphan' ? 'bg-white' : ''"
            @click="activeFilter = 'orphan'"
          >
            <div class="flex items-center gap-1.5 text-xs text-gray-500">
              <span class="h-1.5 w-1.5 rounded-full bg-amber-500"></span>
              <span>孤儿残留 · 运行中</span>
            </div>
            <div class="mt-0.5 flex items-baseline gap-2">
              <span class="text-xl font-bold tabular-nums text-amber-600">{{ summary.orphan }}</span>
              <span class="text-[11px] text-gray-400">脱离注册表</span>
            </div>
            <span v-if="activeFilter === 'orphan'" class="absolute inset-x-0 bottom-0 h-0.5 bg-primary"></span>
          </button>

          <button
            type="button"
            class="relative px-4 py-3 text-left transition-colors hover:bg-white"
            :class="activeFilter === 'zombie' ? 'bg-white' : ''"
            @click="activeFilter = 'zombie'"
          >
            <div class="flex items-center gap-1.5 text-xs text-gray-500">
              <span class="h-1.5 w-1.5 rounded-full bg-rose-500"></span>
              <span>僵死 / 异常终态</span>
            </div>
            <div class="mt-0.5 flex items-baseline gap-2">
              <span class="text-xl font-bold tabular-nums text-rose-600">{{ summary.zombie }}</span>
              <span class="text-[11px] text-gray-400">{{ isK8s ? 'Failed / Evicted / Pending' : 'Exited / Dead / OOM' }}</span>
            </div>
            <span v-if="activeFilter === 'zombie'" class="absolute inset-x-0 bottom-0 h-0.5 bg-primary"></span>
          </button>
        </div>

        <!-- 搜索与一键清理工具条 -->
        <div class="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-gray-100 bg-white px-5 py-3">
          <div class="flex flex-wrap items-center gap-2">
            <div class="relative w-64">
              <MagnifyingGlassIcon class="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
              <input
                v-model="searchKeyword"
                type="text"
                placeholder="搜索用户名、容器/Pod 名、ID 或技能令牌..."
                class="h-8 w-full rounded-lg border border-gray-200 bg-white py-1 pl-8 pr-3 text-xs text-gray-800 placeholder-gray-400 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
            <div class="inline-flex rounded-lg bg-gray-100 p-0.5 text-xs">
              <button
                v-for="tab in ([
                  { key: 'all', label: '全部' },
                  { key: 'managed', label: '受管运行' },
                  { key: 'orphan', label: '孤儿残留' },
                  { key: 'zombie', label: '僵死异常' },
                ] as const)"
                :key="tab.key"
                type="button"
                class="rounded-md px-2.5 py-1 font-medium transition-colors"
                :class="activeFilter === tab.key ? 'bg-white text-gray-900 shadow-2xs' : 'text-gray-500 hover:text-gray-800'"
                @click="activeFilter = tab.key"
              >{{ tab.label }}</button>
            </div>
          </div>

          <button
            type="button"
            :disabled="staleCount === 0 || operating || !available"
            class="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-medium text-rose-700 transition-colors hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-45"
            @click="confirmCleanupStale"
          >
            <TrashIcon class="h-3.5 w-3.5" />
            <span>{{ staleCount > 0 ? `一键清理孤儿/僵死 (${staleCount})` : '无孤儿/僵死需清理' }}</span>
          </button>
        </div>

        <!-- 批量选择提示条 -->
        <div
          v-if="selectedIds.size > 0"
          class="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-primary/20 bg-primary/5 px-5 py-2 text-xs text-primary-dark"
        >
          <span>已勾选 <strong class="font-bold">{{ selectedIds.size }}</strong> 台沙箱实例（仅删除运行壳，用户工作区目录完整保留）</span>
          <div class="flex items-center gap-2">
            <button
              type="button"
              :disabled="operating"
              class="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              @click="confirmBatchRestart"
            >批量重置</button>
            <button
              type="button"
              :disabled="operating"
              class="rounded-md bg-rose-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
              @click="confirmBatchKill"
            >批量 Kill 销毁</button>
            <button
              type="button"
              class="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
              @click="clearSelection"
            >取消选择</button>
          </div>
        </div>

        <!-- 异常提示或表格主体 -->
        <div class="flex-1 overflow-auto bg-white">
          <div v-if="loadError && !available" class="m-5 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-900">
            <ExclamationTriangleIcon class="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
            <div class="space-y-1">
              <div class="font-semibold">暂时无法读取沙箱运行态</div>
              <div class="leading-relaxed">{{ loadError }}</div>
            </div>
          </div>

          <div v-else-if="loading && items.length === 0" class="flex h-56 items-center justify-center text-xs text-gray-500">
            <ArrowPathIcon class="mr-2 h-4 w-4 animate-spin text-primary" />
            <span>正在加载实时沙箱实例列表…</span>
          </div>

          <div v-else-if="filteredItems.length === 0" class="flex h-56 flex-col items-center justify-center gap-1 text-center text-xs text-gray-500">
            <div class="text-sm font-semibold text-gray-800">当前筛选条件下没有沙箱实例</div>
            <div>可切换上方状态分组，或点击右上角「刷新」重新拉取实时状态。</div>
          </div>

          <table v-else class="w-full border-collapse text-left text-xs">
            <thead class="sticky top-0 z-10 bg-gray-50 text-[11px] font-semibold text-gray-500">
              <tr class="border-b border-gray-200">
                <th class="w-9 px-3 py-2.5">
                  <input
                    type="checkbox"
                    :checked="allVisibleSelected"
                    :indeterminate="someVisibleSelected"
                    class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
                    @change="toggleSelectAll"
                  />
                </th>
                <th class="w-40 px-3 py-2.5">归属用户 / 技能令牌</th>
                <th class="px-3 py-2.5">{{ isK8s ? 'Pod 名称 / UID / 镜像' : '容器名 / ID / 镜像' }}</th>
                <th class="w-44 px-3 py-2.5">状态 &amp; 托管判据</th>
                <th class="w-36 px-3 py-2.5">存活时长 / 回收状态</th>
                <th class="w-36 px-3 py-2.5">资源占用</th>
                <th class="w-44 px-3 py-2.5 text-right">操作</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr
                v-for="item in filteredItems"
                :key="item.id"
                class="transition-colors hover:bg-gray-50/80"
                :class="selectedIds.has(item.id) ? 'bg-primary/5' : ''"
              >
                <td class="px-3 py-2.5">
                  <input
                    type="checkbox"
                    :checked="selectedIds.has(item.id)"
                    class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
                    @change="toggleSelectOne(item.id)"
                  />
                </td>
                <td class="px-3 py-2.5">
                  <div class="font-semibold text-gray-900">{{ item.user_key }}</div>
                  <div class="mt-0.5 font-mono text-[11px] text-gray-400">令牌: {{ item.skill_token }}</div>
                </td>
                <td class="max-w-[280px] px-3 py-2.5">
                  <div class="flex items-center gap-1.5">
                    <span class="truncate font-mono font-medium text-gray-900" :title="item.name">{{ item.name }}</span>
                    <button
                      type="button"
                      class="shrink-0 rounded p-0.5 text-gray-400 hover:bg-gray-100 hover:text-primary"
                      title="复制名称"
                      @click="copyName(item.name)"
                    >
                      <ClipboardDocumentIcon class="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <div class="mt-0.5 truncate font-mono text-[11px] text-gray-500" :title="`${item.short_id} · ${item.image}`">
                    {{ item.short_id }} · {{ item.image || '—' }}
                  </div>
                </td>
                <td class="px-3 py-2.5">
                  <div class="flex flex-col items-start gap-1">
                    <span
                      class="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold"
                      :class="stateBadgeClass(item)"
                    >
                      <span
                        class="h-1.5 w-1.5 rounded-full"
                        :class="stateDotClass(item)"
                      ></span>
                      <span>{{ item.state_detail }}</span>
                    </span>
                    <span
                      class="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium"
                      :class="item.category === 'managed'
                        ? 'border-primary/20 bg-primary/5 text-primary'
                        : item.category === 'orphan'
                          ? 'border-amber-200 bg-amber-50 text-amber-700'
                          : 'border-gray-200 bg-gray-100 text-gray-600'"
                    >
                      {{ item.category === 'managed' ? '进程受管' : item.category === 'orphan' ? '孤儿 · 脱离注册表' : '僵死终态' }}
                    </span>
                  </div>
                </td>
                <td class="px-3 py-2.5">
                  <div :class="formatAgeClass(item)">{{ formatDuration(item.age_seconds) }}</div>
                  <div class="mt-0.5 text-[11px] text-gray-500">{{ formatIdleSubtext(item) }}</div>
                  <div
                    v-if="formatIdleQuotaNote(item)"
                    class="text-[11px] text-gray-400"
                    title="仍被会话持有引用（进程受管）的沙箱，空闲阈值按倍数放宽；引用释放后回到 sandbox_idle_time 配置值"
                  >{{ formatIdleQuotaNote(item) }}</div>
                </td>
                <td class="px-3 py-2.5">
                  <div class="font-mono text-[11px] text-gray-800">{{ formatMemoryCell(item) }}</div>
                  <div class="mt-0.5 text-[11px] text-gray-400">{{ formatResourceSub(item) }}</div>
                </td>
                <td class="px-3 py-2.5 text-right">
                  <div class="inline-flex items-center justify-end gap-1.5">
                    <button
                      type="button"
                      class="rounded-md border border-gray-200 bg-white px-2 py-1 text-[11px] font-medium text-gray-700 hover:bg-gray-50"
                      @click="openDetail(item)"
                    >日志/详情</button>
                    <button
                      type="button"
                      :disabled="operating"
                      class="rounded-md border border-gray-200 bg-white px-2 py-1 text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-45"
                      @click="confirmRestartSingle(item)"
                    >重置</button>
                    <button
                      type="button"
                      :disabled="operating"
                      class="rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-45"
                      @click="confirmKillSingle(item)"
                    >Kill</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 底部栏 -->
        <div class="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-gray-200 bg-gray-50/70 px-5 py-2.5 text-xs text-gray-500">
          <span>显示 {{ filteredItems.length }} / {{ items.length }} 条 · 安全边界：仅管理平台沙箱资源，宿主 / PVC 用户工作区文件完整保留</span>
          <span>权限：<code class="rounded bg-gray-200/80 px-1.5 py-0.5 font-mono text-[11px] text-gray-700">element:system:config_save</code></span>
        </div>

        <!-- 右侧滑出：详情与实时日志抽屉 -->
        <div
          v-if="detailOpen"
          class="absolute inset-y-0 right-0 z-20 flex w-[min(520px,92%)] flex-col border-l border-gray-200 bg-white shadow-2xl"
        >
          <div class="flex shrink-0 items-center justify-between gap-3 border-b border-gray-200 px-4 py-3">
            <div class="min-w-0">
              <div class="truncate text-sm font-bold text-gray-900">
                {{ detailTargetItem?.user_key }} · {{ isK8s ? 'Pod' : '容器' }}详情
              </div>
              <div class="truncate font-mono text-[11px] text-gray-500">
                {{ detailTargetItem?.name }} ({{ detailTargetItem?.short_id }})
              </div>
            </div>
            <button
              type="button"
              class="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-100"
              @click="closeDetail"
            >
              <XMarkIcon class="h-4 w-4" />
            </button>
          </div>

          <div class="flex-1 space-y-4 overflow-auto p-4 text-xs">
            <div v-if="detailLoading" class="flex h-40 items-center justify-center text-gray-500">
              <ArrowPathIcon class="mr-2 h-4 w-4 animate-spin text-primary" />
              <span>正在拉取详情与日志…</span>
            </div>
            <div v-else-if="detailError" class="rounded-lg border border-rose-200 bg-rose-50 p-3 text-rose-700">
              {{ detailError }}
            </div>
            <template v-else-if="detailData">
              <div class="grid grid-cols-[100px_1fr] gap-x-3 gap-y-2 rounded-xl border border-gray-200 bg-gray-50/70 p-3">
                <span class="text-gray-500">运行状态</span>
                <span class="font-medium text-gray-900">
                  {{ detailData.runtime_state }}
                  <span v-if="detailData.oom_killed" class="ml-1 rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700">OOMKilled</span>
                </span>
                <span class="text-gray-500">归属用户</span>
                <span class="font-mono text-gray-900">{{ detailData.user_key }}</span>
                <span class="text-gray-500">技能令牌</span>
                <span class="font-mono text-gray-900">{{ detailData.skill_token }}</span>
                <span class="text-gray-500">镜像</span>
                <span class="break-all font-mono text-gray-900">{{ detailData.image || '—' }}</span>
                <template v-if="!isK8s">
                  <span class="text-gray-500">Init / PID 1</span>
                  <span class="font-mono text-gray-900">{{ detailData.init_enabled ? '已启用 tini (Init=True)' : '未启用（旧容器）' }}</span>
                </template>
                <template v-else>
                  <span class="text-gray-500">节点 / IP</span>
                  <span class="font-mono text-gray-900">{{ detailData.node_or_host || '—' }}</span>
                </template>
                <span class="text-gray-500">创建时间</span>
                <span class="font-mono text-gray-900">{{ detailData.created_at || '—' }}</span>
                <span class="text-gray-500">挂载目录</span>
                <div class="space-y-1 font-mono text-[11px] text-gray-800">
                  <div v-for="(m, idx) in detailData.mounts" :key="idx" class="break-all">{{ m }}</div>
                  <div v-if="!detailData.mounts || detailData.mounts.length === 0" class="text-gray-400">—</div>
                </div>
              </div>

              <div>
                <div class="mb-1.5 flex items-center justify-between">
                  <span class="font-semibold text-gray-800">最近日志（已脱敏，tail 120）</span>
                  <button
                    type="button"
                    :disabled="!detailData.logs || !detailData.logs.trim()"
                    class="rounded border border-gray-200 bg-white px-2 py-0.5 text-[11px] text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-45"
                    @click="copyLogs"
                  >复制日志</button>
                </div>
                <pre class="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ detailData.logs || '# 暂无日志输出' }}</pre>
              </div>
            </template>
          </div>

          <div
            v-if="detailTargetItem"
            class="flex shrink-0 items-center justify-end gap-2 border-t border-gray-200 bg-gray-50/70 px-4 py-2.5"
          >
            <button
              type="button"
              :disabled="operating"
              class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              @click="confirmRestartSingle(detailTargetItem)"
            >销毁重置</button>
            <button
              type="button"
              :disabled="operating"
              class="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
              @click="confirmKillSingle(detailTargetItem)"
            >强制 Kill 销毁</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 二次确认弹窗 -->
    <div
      v-if="confirmState.open"
      class="fixed inset-0 z-[130] flex items-center justify-center bg-slate-900/55 p-4"
      role="alertdialog"
      aria-modal="true"
      @click.self="closeConfirm"
    >
      <div class="w-[min(440px,94vw)] overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl">
        <div class="p-5">
          <h4 class="text-sm font-bold text-gray-900">{{ confirmState.title }}</h4>
          <p class="mt-2 text-xs leading-relaxed text-gray-600">{{ confirmState.desc }}</p>
          <div
            v-if="confirmState.warn"
            class="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-xs leading-relaxed text-amber-900"
          >
            {{ confirmState.warn }}
          </div>
          <label
            v-if="confirmState.showOrphanCheckbox"
            class="mt-3 flex cursor-pointer select-none items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-700"
          >
            <input
              v-model="confirmState.includeOrphans"
              type="checkbox"
              class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
            />
            <span>同时清理脱离本进程注册表的运行中孤儿（{{ summary.orphan }} 台）</span>
          </label>
        </div>
        <div class="flex items-center justify-end gap-2 border-t border-gray-100 bg-gray-50/70 px-5 py-3">
          <button
            type="button"
            class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
            @click="closeConfirm"
          >取消</button>
          <button
            type="button"
            :disabled="confirmState.showOrphanCheckbox && !confirmState.includeOrphans && summary.zombie === 0"
            class="rounded-lg px-3 py-1.5 text-xs font-semibold text-white transition-colors disabled:cursor-not-allowed disabled:opacity-45"
            :class="confirmState.danger ? 'bg-rose-600 hover:bg-rose-700' : 'bg-primary hover:bg-primary-dark'"
            @click="runConfirm"
          >{{ confirmOkLabel }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
