<script setup lang="ts">
/**
 * 系统诊断 →「实例」面板。
 *
 * 数据全部来自 `GET /api/portal/system/instances`：每个节点每 15 秒把自己的快照写进
 * 共享 Redis，后端聚合后返回。前端不做任何轮询之外的推导，只负责展示与别名编辑。
 *
 * 口径说明（页面上也会提示）：
 * - QPS / P95 / 5xx / 并发是**本节点**最近 60 秒的进程内指标；
 * - 在线用户数是共享 Redis 里的**全平台**值，同一个数出现在每个实例上。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import {
  ServerStackIcon,
  ServerIcon,
  PencilSquareIcon,
  ArrowPathIcon,
  MagnifyingGlassIcon,
  ExclamationTriangleIcon,
  XMarkIcon,
  CheckCircleIcon,
  CircleStackIcon,
  CpuChipIcon,
  ClockIcon,
  ChevronDownIcon,
  InformationCircleIcon
} from '@heroicons/vue/24/outline'

interface InstanceResources {
  cpu_percent: number
  cpu_count: number
  load: number[]
  mem_used_mb: number
  mem_total_mb: number
  disk_used_gb: number
  disk_total_gb: number
}

interface InstanceItem {
  host: string
  alias: string
  primary: boolean
  online: boolean
  heartbeat_age_seconds: number
  heartbeat_at: string
  uptime_seconds: number
  started_at: string
  version: { app: string; commit: string; image: string }
  runtime: {
    kind: string
    node_name: string
    pod_ip: string
    python_version: string
    pid: number
    threads: number
    open_files: number
  }
  resources: InstanceResources
  stack: { db_type: string; db_version: string; redis_version: string; redis_flavor: string }
  deps: { db_ok: boolean; db_latency_ms: number; redis_ok: boolean; redis_latency_ms: number }
  traffic: {
    requests_1m: number
    qps: number
    p95_ms: number
    p99_ms: number
    error_rate: number
    inflight: number
    chat_inflight: number
  }
  cluster: { online_users: number }
  series: { cpu: number[]; mem: number[]; qps: number[] }
}

interface InstanceAlert {
  level: 'critical' | 'warning' | 'info'
  code: string
  hosts: string[]
  message: string
}

interface InstanceOverview {
  instances: InstanceItem[]
  summary: { total: number; online: number; primary: number; offline: number }
  alerts: InstanceAlert[]
  intervals: {
    heartbeat_seconds: number
    offline_after_seconds: number
    series_seconds: number
    series_retention_seconds: number
  }
}

interface SeriesPoint {
  ts: number
  cpu: number
  mem_mb: number
  qps: number
  chat_inflight: number
  inflight: number
}

const { showToast } = useToast()

// 父级左栏要显示「在线 4/5」，这里只播报汇总，不把整个列表提上去
const emit = defineEmits<{ (event: 'summary', value: { online: number; total: number }): void }>()

const loading = ref(true)
const loadError = ref('')
const overview = ref<InstanceOverview>({
  instances: [],
  summary: { total: 0, online: 0, primary: 0, offline: 0 },
  alerts: [],
  intervals: {
    heartbeat_seconds: 15,
    offline_after_seconds: 45,
    series_seconds: 60,
    series_retention_seconds: 86400
  }
})

const viewMode = ref<'card' | 'table'>('card')
const keyword = ref('')
const primaryFilter = ref<'all' | 'primary' | 'secondary'>('all')
const autoRefresh = ref(true)
const contractOpen = ref(false)

// 相对时间：每秒钟推一次，但不重新拉接口
const nowTick = ref(Date.now())
const fetchedAt = ref(Date.now())

// 详情抽屉
const selectedHost = ref<string | null>(null)
const detailSeries = ref<SeriesPoint[]>([])
const detailRange = ref<24>(24)
const detailLoading = ref(false)

// 别名编辑
const aliasTarget = ref<InstanceItem | null>(null)
const aliasDraft = ref('')
const aliasSaving = ref(false)

let refreshTimer: number | null = null
let tickTimer: number | null = null

const runtimeLabel = (kind: string) => {
  if (kind === 'k8s') return 'Kubernetes Pod'
  if (kind === 'docker') return 'Docker 容器'
  if (kind === 'host') return '主机进程'
  return kind || '未知'
}

const fmtDuration = (seconds: number) => {
  if (!seconds || seconds <= 0) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days) return `${days}天 ${hours}小时`
  if (hours) return `${hours}小时 ${minutes}分`
  return `${minutes}分 ${seconds % 60}秒`
}

const fmtAgo = (seconds: number) => {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return `${Math.floor(seconds / 3600)} 小时前`
}

const heartbeatAge = (item: InstanceItem) => {
  const elapsed = (nowTick.value - fetchedAt.value) / 1000
  return Math.max(0, item.heartbeat_age_seconds + elapsed)
}

const percent = (used: number, total: number) => (total ? Math.round((used / total) * 1000) / 10 : 0)

const barClass = (value: number) => (value >= 85 ? 'bg-rose-500' : value >= 70 ? 'bg-amber-500' : 'bg-primary')

const displayName = (item: InstanceItem) => item.alias || item.host
const subLine = (item: InstanceItem) => (item.alias ? item.host : item.runtime.pod_ip || item.host)

/** 版本漂移的 host 集合：由后端告警给出，前端只用来给卡片打标 */
const driftHosts = computed(() => {
  const set = new Set<string>()
  overview.value.alerts
    .filter((alert) => alert.code === 'version_drift')
    .forEach((alert) => alert.hosts.forEach((host) => set.add(host)))
  return set
})

const statusOf = (item: InstanceItem) => {
  if (!item.online) return { label: '离线', cls: 'bg-gray-50 text-gray-500 border-gray-200', dot: 'bg-gray-400', pulse: false }
  if (driftHosts.value.has(item.host)) {
    return { label: '版本漂移', cls: 'bg-amber-50 text-amber-700 border-amber-200', dot: 'bg-amber-500', pulse: false }
  }
  if (percent(item.resources.mem_used_mb, item.resources.mem_total_mb) >= 85) {
    return { label: '内存偏高', cls: 'bg-amber-50 text-amber-700 border-amber-200', dot: 'bg-amber-500', pulse: false }
  }
  return { label: '在线', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200', dot: 'bg-emerald-500', pulse: true }
}

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return overview.value.instances.filter((item) => {
    if (primaryFilter.value === 'primary' && !item.primary) return false
    if (primaryFilter.value === 'secondary' && item.primary) return false
    if (!kw) return true
    return [
      item.alias,
      item.host,
      item.runtime.pod_ip,
      item.runtime.node_name,
      item.version.app,
      item.stack.db_type,
      item.stack.db_version,
      item.stack.redis_version
    ]
      .join(' ')
      .toLowerCase()
      .includes(kw)
  })
})

const selectedInstance = computed(
  () => overview.value.instances.find((item) => item.host === selectedHost.value) || null
)

/** 迷你趋势：把数值序列压成 SVG 路径，单序列自缩放 */
const spark = (values: number[]) => {
  const points = (values || []).filter((value) => typeof value === 'number')
  if (points.length < 2) return { line: '', area: '' }
  const width = 120
  const height = 26
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min || 1
  const coords = points.map((value, index) => {
    const x = (index / (points.length - 1)) * width
    const y = height - ((value - min) / span) * (height - 5) - 2.5
    return [Number(x.toFixed(1)), Number(y.toFixed(1))]
  })
  const line = coords.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x} ${y}`).join(' ')
  return { line, area: `${line} L${width} ${height} L0 ${height} Z` }
}

const detailSpark = (values: number[]) => {
  const points = (values || []).filter((value) => typeof value === 'number')
  if (points.length < 2) return { line: '', area: '' }
  const width = 520
  const height = 90
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min || 1
  const coords = points.map((value, index) => {
    const x = (index / (points.length - 1)) * width
    const y = height - ((value - min) / span) * (height - 8) - 4
    return [Number(x.toFixed(1)), Number(y.toFixed(1))]
  })
  const line = coords.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x} ${y}`).join(' ')
  return { line, area: `${line} L${width} ${height} L0 ${height} Z` }
}

async function loadOverview() {
  try {
    loadError.value = ''
    const response = await axios.get('/api/portal/system/instances')
    overview.value = response.data as InstanceOverview
    fetchedAt.value = Date.now()
    emit('summary', { online: overview.value.summary.online, total: overview.value.summary.total })
  } catch (error: any) {
    loadError.value = error?.response?.data?.detail || '实例列表加载失败'
  } finally {
    loading.value = false
  }
}

async function loadDetailSeries() {
  const host = selectedHost.value
  if (!host) return
  detailLoading.value = true
  try {
    const response = await axios.get(`/api/portal/system/instances/${encodeURIComponent(host)}/series`, {
      params: { hours: detailRange.value }
    })
    detailSeries.value = (response.data?.points || []) as SeriesPoint[]
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '读取实例趋势失败', 'error')
    detailSeries.value = []
  } finally {
    detailLoading.value = false
  }
}

function openDetail(item: InstanceItem) {
  selectedHost.value = item.host
  detailRange.value = 24
  detailSeries.value = []
  void loadDetailSeries()
}

function closeDetail() {
  selectedHost.value = null
  detailSeries.value = []
}

async function changeRange(hours: number) {
  detailRange.value = hours
  await loadDetailSeries()
}

function openAlias(item: InstanceItem, event?: Event) {
  event?.stopPropagation()
  aliasTarget.value = item
  aliasDraft.value = item.alias
}

function closeAlias() {
  aliasTarget.value = null
  aliasDraft.value = ''
}

async function saveAlias() {
  const target = aliasTarget.value
  if (!target) return
  aliasSaving.value = true
  try {
    await axios.put(`/api/portal/system/instances/${encodeURIComponent(target.host)}/alias`, {
      alias: aliasDraft.value.trim()
    })
    showToast(aliasDraft.value.trim() ? '别名已保存' : '别名已清除', 'success')
    closeAlias()
    await loadOverview()
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '保存别名失败', 'error')
  } finally {
    aliasSaving.value = false
  }
}

function toggleAutoRefresh() {
  autoRefresh.value = !autoRefresh.value
  setupTimer()
}

function setupTimer() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
  }
  if (autoRefresh.value) {
    refreshTimer = window.setInterval(() => {
      void loadOverview()
      if (selectedHost.value) void loadDetailSeries()
    }, 15000)
  }
}

onMounted(() => {
  void loadOverview()
  setupTimer()
  tickTimer = window.setInterval(() => {
    nowTick.value = Date.now()
  }, 1000)
})

onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
  if (tickTimer) window.clearInterval(tickTimer)
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 统计条 -->
    <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <div class="rounded-xl border border-gray-200/80 bg-white px-4 py-3 shadow-sm">
        <div class="text-xs text-gray-500">实例总数</div>
        <div class="mt-0.5 text-2xl font-semibold text-gray-900">{{ overview.summary.total }}</div>
        <div class="text-[11px] text-gray-400">同构部署 · 负载均衡</div>
      </div>
      <div class="rounded-xl border border-gray-200/80 bg-white px-4 py-3 shadow-sm">
        <div class="text-xs text-gray-500">在线</div>
        <div class="mt-0.5 text-2xl font-semibold text-emerald-600">{{ overview.summary.online }}</div>
        <div class="text-[11px] text-gray-400">心跳在 {{ overview.intervals.offline_after_seconds }} 秒内</div>
      </div>
      <div class="rounded-xl border border-gray-200/80 bg-white px-4 py-3 shadow-sm">
        <div class="text-xs text-gray-500">主节点</div>
        <div
          class="mt-0.5 text-2xl font-semibold"
          :class="overview.summary.primary === 1 ? 'text-amber-600' : 'text-rose-600'"
        >
          {{ overview.summary.primary }}
          <span class="text-xs font-medium text-gray-400">/ {{ overview.summary.total }}</span>
        </div>
        <div class="text-[11px] text-gray-400">TASK_SCHEDULER_ENABLED=true</div>
      </div>
      <div class="rounded-xl border border-gray-200/80 bg-white px-4 py-3 shadow-sm">
        <div class="text-xs text-gray-500">离线</div>
        <div class="mt-0.5 text-2xl font-semibold" :class="overview.summary.offline ? 'text-rose-600' : 'text-gray-900'">
          {{ overview.summary.offline }}
        </div>
        <div class="text-[11px] text-gray-400">
          {{ overview.summary.offline ? '心跳已过期，请检查该节点' : '全部节点心跳正常' }}
        </div>
      </div>
    </div>

    <!-- 告警 -->
    <div v-if="overview.alerts.length" class="flex flex-col gap-2">
      <div
        v-for="alert in overview.alerts"
        :key="alert.code + alert.hosts.join(',')"
        class="flex items-start gap-2 rounded-xl border px-3.5 py-2.5 text-xs leading-relaxed"
        :class="
          alert.level === 'critical'
            ? 'border-rose-200 bg-rose-50 text-rose-900'
            : 'border-amber-200 bg-amber-50 text-amber-900'
        "
      >
        <ExclamationTriangleIcon class="mt-0.5 h-4 w-4 shrink-0" />
        <div>{{ alert.message }}</div>
      </div>
    </div>

    <!-- 工具条 + 列表 -->
    <div class="rounded-xl border border-gray-200/80 bg-white shadow-sm">
      <div class="flex flex-wrap items-center gap-2 px-3.5 py-2.5">
        <div class="relative w-full sm:w-64">
          <MagnifyingGlassIcon class="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索别名 / hostname / IP / 版本…"
            class="w-full rounded-lg border border-gray-200 bg-gray-50/70 py-1.5 pl-8 pr-3 text-xs text-gray-700 placeholder-gray-400 outline-none transition focus:border-primary/50 focus:bg-white focus:ring-2 focus:ring-primary/15"
          />
        </div>
        <button
          v-for="option in [
            { key: 'all', label: '全部' },
            { key: 'primary', label: '主节点' },
            { key: 'secondary', label: '从节点' }
          ]"
          :key="option.key"
          type="button"
          class="rounded-full border px-2.5 py-1 text-[11px] font-medium transition"
          :class="
            primaryFilter === option.key
              ? 'border-primary/30 bg-primary/10 text-primary'
              : 'border-gray-200 bg-white text-gray-500 hover:text-gray-700'
          "
          @click="primaryFilter = option.key as 'all' | 'primary' | 'secondary'"
        >
          {{ option.label }}
        </button>
        <div class="ml-auto flex items-center gap-2">
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium transition"
            :class="autoRefresh ? 'border-gray-200 text-gray-600' : 'border-gray-200 text-gray-400'"
            @click="toggleAutoRefresh"
          >
            <ArrowPathIcon class="h-3.5 w-3.5" :class="autoRefresh ? 'animate-none' : ''" />
            自动刷新 15s：{{ autoRefresh ? '开' : '关' }}
          </button>
          <div class="inline-flex rounded-lg border border-gray-200/70 bg-gray-50 p-0.5">
            <button
              type="button"
              class="rounded-md px-2.5 py-1 text-[11px] transition"
              :class="viewMode === 'card' ? 'bg-white font-semibold text-gray-900 shadow-sm' : 'text-gray-500'"
              @click="viewMode = 'card'"
            >
              卡片
            </button>
            <button
              type="button"
              class="rounded-md px-2.5 py-1 text-[11px] transition"
              :class="viewMode === 'table' ? 'bg-white font-semibold text-gray-900 shadow-sm' : 'text-gray-500'"
              @click="viewMode = 'table'"
            >
              表格
            </button>
          </div>
        </div>
      </div>

      <!-- 加载 / 错误 / 空态 -->
      <div v-if="loading" class="flex items-center gap-2 px-4 py-10 text-xs text-gray-400">
        <ArrowPathIcon class="h-4 w-4 animate-spin" />
        正在读取各实例心跳…
      </div>
      <div v-else-if="loadError" class="px-4 py-8 text-xs text-rose-600">{{ loadError }}</div>
      <div v-else-if="!filtered.length" class="px-4 py-10 text-center text-xs text-gray-400">
        {{ overview.summary.total ? '没有匹配的实例' : '暂时没有实例上报心跳：请确认 Redis 可用且服务已启动' }}
      </div>

      <!-- 卡片视图：只有一张卡时铺满整行，多张才分两列（只有一个节点时右边不会留一大片空） -->
      <div
        v-else-if="viewMode === 'card'"
        class="grid grid-cols-1 gap-3 p-3"
        :class="filtered.length > 1 ? 'xl:grid-cols-2' : ''"
      >
        <div
          v-for="item in filtered"
          :key="item.host"
          class="group cursor-pointer rounded-xl border bg-white p-3.5 transition hover:-translate-y-px hover:border-primary/40 hover:shadow-md"
          :class="[
            item.online ? 'border-gray-200/80' : 'border-gray-200/60 bg-gray-50/60',
            item.online && statusOf(item).label !== '在线' ? 'border-l-[3px] border-l-amber-400' : ''
          ]"
          @click="openDetail(item)"
        >
          <div class="flex items-start gap-2.5">
            <div
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border"
              :class="
                !item.online
                  ? 'border-gray-200 bg-gray-100 text-gray-400'
                  : item.primary
                    ? 'border-amber-200 bg-amber-50 text-amber-600'
                    : 'border-primary/20 bg-primary/10 text-primary'
              "
            >
              <ServerStackIcon class="h-4 w-4" />
            </div>
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="max-w-[240px] truncate text-sm font-semibold text-gray-900" :title="displayName(item)">
                  {{ displayName(item) }}
                </span>
                <button
                  type="button"
                  class="text-gray-300 opacity-0 transition hover:text-primary group-hover:opacity-100"
                  title="编辑别名"
                  @click="openAlias(item, $event)"
                >
                  <PencilSquareIcon class="h-3.5 w-3.5" />
                </button>
                <span
                  class="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-semibold"
                  :class="item.primary ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 bg-gray-50 text-gray-500'"
                >
                  {{ item.primary ? '主节点' : '从节点' }}
                </span>
              </div>
              <div class="mt-0.5 truncate font-mono text-[10.5px] text-gray-400">{{ subLine(item) }}</div>
              <div class="mt-1 flex flex-wrap items-center gap-1.5">
                <span
                  class="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-semibold"
                  :class="statusOf(item).cls"
                >
                  <span class="h-1 w-1 rounded-full" :class="[statusOf(item).dot, statusOf(item).pulse ? 'animate-pulse' : '']" />
                  {{ statusOf(item).label }}
                </span>
                <span class="rounded-full border border-gray-200 bg-white px-1.5 py-0.5 text-[10px] text-gray-500">
                  {{ item.version.app || 'unknown' }}
                </span>
              </div>
            </div>
            <div class="ml-auto shrink-0 text-right">
              <div class="text-[10px] text-gray-400">最后心跳</div>
              <div class="text-xs font-semibold" :class="item.online ? 'text-emerald-600' : 'text-rose-600'">
                {{ fmtAgo(heartbeatAge(item)) }}
              </div>
            </div>
          </div>

          <div class="mt-2.5 grid grid-cols-3 gap-2">
            <div v-for="metric in [
              { label: 'CPU', value: item.online ? item.resources.cpu_percent : 0, text: item.online ? `${item.resources.cpu_percent}%` : '—' },
              { label: '内存', value: item.online ? percent(item.resources.mem_used_mb, item.resources.mem_total_mb) : 0, text: item.online ? `${percent(item.resources.mem_used_mb, item.resources.mem_total_mb)}%` : '—' },
              { label: '磁盘', value: percent(item.resources.disk_used_gb, item.resources.disk_total_gb), text: `${percent(item.resources.disk_used_gb, item.resources.disk_total_gb)}%` }
            ]" :key="metric.label" class="rounded-lg border border-gray-100 bg-gray-50/60 px-2 py-1.5">
              <div class="flex items-baseline justify-between text-[10px] text-gray-500">
                <span>{{ metric.label }}</span>
                <b class="font-semibold text-gray-700">{{ metric.text }}</b>
              </div>
              <div class="mt-1 h-1 overflow-hidden rounded-full bg-gray-200">
                <div class="h-full rounded-full transition-all" :class="barClass(metric.value)" :style="{ width: `${Math.min(100, metric.value)}%` }" />
              </div>
            </div>
          </div>

          <div class="mt-2 grid grid-cols-4 gap-1.5">
            <div class="rounded-lg border border-dashed border-gray-200 px-2 py-1">
              <div class="text-[10px] text-gray-400">QPS</div>
              <div class="text-xs font-semibold text-gray-800">{{ item.online ? item.traffic.qps : '—' }}</div>
            </div>
            <div class="rounded-lg border border-dashed border-gray-200 px-2 py-1">
              <div class="text-[10px] text-gray-400">P95</div>
              <div class="text-xs font-semibold text-gray-800">{{ item.online ? `${item.traffic.p95_ms}ms` : '—' }}</div>
            </div>
            <div class="rounded-lg border border-dashed border-gray-200 px-2 py-1">
              <div class="text-[10px] text-gray-400">对话中</div>
              <div class="text-xs font-semibold text-gray-800">{{ item.traffic.chat_inflight }}</div>
            </div>
            <div class="rounded-lg border border-dashed border-gray-200 px-2 py-1">
              <div class="text-[10px] text-gray-400">在线用户</div>
              <div class="text-xs font-semibold text-gray-800">{{ item.cluster.online_users }}</div>
            </div>
          </div>

          <div class="mt-2 flex flex-wrap items-center gap-1.5 text-[10.5px] text-gray-500">
            <span class="inline-flex items-center gap-1 rounded-md border border-gray-100 bg-gray-50 px-1.5 py-0.5">
              <CircleStackIcon class="h-3 w-3 text-gray-400" />
              <b class="font-semibold text-gray-600">{{ item.stack.db_type }} {{ item.stack.db_version }}</b>
            </span>
            <span class="inline-flex items-center gap-1 rounded-md border border-gray-100 bg-gray-50 px-1.5 py-0.5">
              <CpuChipIcon class="h-3 w-3 text-gray-400" />
              <b class="font-semibold text-gray-600">Redis {{ item.stack.redis_version }}</b>
            </span>
            <span class="rounded-md border border-gray-100 bg-gray-50 px-1.5 py-0.5">Python {{ item.runtime.python_version }}</span>
            <span class="rounded-md border border-gray-100 bg-gray-50 px-1.5 py-0.5">{{ runtimeLabel(item.runtime.kind) }}</span>
          </div>

          <div class="mt-2 flex items-center gap-2 border-t border-gray-100 pt-2 text-[10.5px] text-gray-500">
            <svg viewBox="0 0 120 26" class="h-6 w-[120px]" preserveAspectRatio="none">
              <path :d="spark(item.series.cpu).area" :fill="item.online ? '#2563eb' : '#94a3b8'" opacity="0.1" />
              <path
                :d="spark(item.series.cpu).line"
                fill="none"
                :stroke="item.online ? '#2563eb' : '#94a3b8'"
                stroke-width="1.6"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <span>{{ item.online ? `运行 ${fmtDuration(item.uptime_seconds)}` : '进程已退出' }}</span>
            <span class="ml-auto font-mono text-[10px] text-gray-400">{{ item.runtime.pod_ip }}</span>
            <button
              type="button"
              class="rounded-md bg-primary px-2 py-0.5 text-[11px] font-medium text-white hover:bg-primary/90"
              @click.stop="openDetail(item)"
            >
              详情
            </button>
          </div>
        </div>
      </div>

      <!-- 表格视图 -->
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[1120px] border-collapse text-xs">
          <thead>
            <tr class="bg-gray-50/80 text-[11px] text-gray-500">
              <th class="px-3 py-2 text-left font-semibold">实例（别名 / hostname）</th>
              <th class="px-3 py-2 text-left font-semibold">状态</th>
              <th class="px-3 py-2 text-left font-semibold">主从</th>
              <th class="px-3 py-2 text-right font-semibold">CPU</th>
              <th class="px-3 py-2 text-right font-semibold">内存</th>
              <th class="px-3 py-2 text-left font-semibold">数据库</th>
              <th class="px-3 py-2 text-left font-semibold">缓存</th>
              <th class="px-3 py-2 text-left font-semibold">版本</th>
              <th class="px-3 py-2 text-left font-semibold">运行环境</th>
              <th class="px-3 py-2 text-right font-semibold">QPS</th>
              <th class="px-3 py-2 text-right font-semibold">P95</th>
              <th class="px-3 py-2 text-right font-semibold">对话中</th>
              <th class="px-3 py-2 text-right font-semibold">运行时长</th>
              <th class="px-3 py-2 text-left font-semibold">最后心跳</th>
              <th class="px-3 py-2 text-left font-semibold">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in filtered"
              :key="item.host"
              class="cursor-pointer border-t border-gray-100 hover:bg-primary/5"
              @click="openDetail(item)"
            >
              <td class="px-3 py-2.5">
                <div class="flex items-center gap-1.5 font-semibold text-gray-800">
                  {{ displayName(item) }}
                  <button
                    type="button"
                    class="text-gray-300 hover:text-primary"
                    title="编辑别名"
                    @click="openAlias(item, $event)"
                  >
                    <PencilSquareIcon class="h-3 w-3" />
                  </button>
                </div>
                <div class="font-mono text-[10.5px] text-gray-400">{{ subLine(item) }}</div>
              </td>
              <td class="px-3 py-2.5">
                <span class="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-semibold" :class="statusOf(item).cls">
                  <span class="h-1 w-1 rounded-full" :class="statusOf(item).dot" />
                  {{ statusOf(item).label }}
                </span>
              </td>
              <td class="px-3 py-2.5">
                <span
                  class="inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-semibold"
                  :class="item.primary ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 bg-gray-50 text-gray-500'"
                >
                  {{ item.primary ? '主' : '从' }}
                </span>
              </td>
              <td class="px-3 py-2.5 text-right tabular-nums">{{ item.online ? `${item.resources.cpu_percent}%` : '—' }}</td>
              <td class="px-3 py-2.5 text-right tabular-nums">
                {{ item.online ? `${percent(item.resources.mem_used_mb, item.resources.mem_total_mb)}%` : '—' }}
              </td>
              <td class="px-3 py-2.5 text-gray-700">
                {{ item.stack.db_type }} <span class="text-gray-400">{{ item.stack.db_version }}</span>
              </td>
              <td class="px-3 py-2.5 text-gray-700">
                Redis <span class="text-gray-400">{{ item.stack.redis_version }}</span>
              </td>
              <td class="px-3 py-2.5 font-mono text-[11px]">{{ item.version.app || 'unknown' }}</td>
              <td class="px-3 py-2.5 text-gray-600">{{ runtimeLabel(item.runtime.kind) }}</td>
              <td class="px-3 py-2.5 text-right tabular-nums">{{ item.online ? item.traffic.qps : '—' }}</td>
              <td class="px-3 py-2.5 text-right tabular-nums">{{ item.online ? `${item.traffic.p95_ms} ms` : '—' }}</td>
              <td class="px-3 py-2.5 text-right tabular-nums">{{ item.traffic.chat_inflight }}</td>
              <td class="px-3 py-2.5 text-right tabular-nums">{{ item.online ? fmtDuration(item.uptime_seconds) : '已退出' }}</td>
              <td class="px-3 py-2.5" :class="item.online ? 'text-gray-500' : 'text-rose-600'">
                {{ fmtAgo(heartbeatAge(item)) }}
              </td>
              <td class="px-3 py-2.5">
                <button
                  type="button"
                  class="rounded-md border border-gray-200 px-2 py-0.5 text-[11px] text-gray-600 hover:border-primary/40 hover:text-primary"
                  @click.stop="openDetail(item)"
                >
                  详情
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 字段口径说明 -->
    <div class="rounded-xl border border-gray-200/80 bg-white shadow-sm">
      <button type="button" class="flex w-full items-center gap-2 px-3.5 py-2.5 text-left" @click="contractOpen = !contractOpen">
        <div>
          <div class="text-[13px] font-semibold text-gray-800">采集口径与 Redis 键</div>
          <div class="text-[11px] text-gray-500">这些数字从哪来、存在哪、保留多久</div>
        </div>
        <ChevronDownIcon class="ml-auto h-4 w-4 text-gray-400 transition" :class="contractOpen ? 'rotate-180' : ''" />
      </button>
      <div v-if="contractOpen" class="border-t border-gray-100 px-3.5 py-3 text-xs leading-relaxed text-gray-600">
        <div class="mb-2 flex items-start gap-1.5 rounded-lg bg-sky-50 px-2.5 py-2 text-[11.5px] text-sky-900">
          <InformationCircleIcon class="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <div>
            QPS / P95 / 5xx / 并发是<b>本节点</b>最近 60 秒的进程内指标；在线用户数是共享 Redis 里的<b>全平台</b>值，因此每个实例上显示的是同一个数。
          </div>
        </div>
        <div class="space-y-1.5 font-mono text-[11px]">
          <div class="rounded-md bg-gray-900 px-2.5 py-1.5 text-sky-300">
            nanzi:instance:{host} <span class="text-gray-400">HASH · TTL {{ overview.intervals.offline_after_seconds }}s（心跳 {{ overview.intervals.heartbeat_seconds }}s × 3）</span>
          </div>
          <div class="rounded-md bg-gray-900 px-2.5 py-1.5 text-sky-300">
            nanzi:instances:index <span class="text-gray-400">SET&lt;host&gt; · 启动 SADD，读取时按 TTL 剔除</span>
          </div>
          <div class="rounded-md bg-gray-900 px-2.5 py-1.5 text-sky-300">
            nanzi:instance:{host}:series <span class="text-gray-400">ZSET · 每 {{ overview.intervals.series_seconds }}s 一点，保留 {{ Math.round(overview.intervals.series_retention_seconds / 3600) }}h 后自动裁剪</span>
          </div>
          <div class="rounded-md bg-gray-900 px-2.5 py-1.5 text-sky-300">
            nanzi:instance:alias:{host} <span class="text-gray-400">STRING · 不设 TTL，心跳过期不影响别名</span>
          </div>
        </div>
        <div class="mt-2 text-[11px] text-gray-500">
          主从来自环境变量 TASK_SCHEDULER_ENABLED：true 表示该节点执行定时任务与调度。页面只校验「是否只有一个主节点」，不提供在线切换——那会造成页面与进程实际行为不一致。
        </div>
      </div>
    </div>

    <!-- 详情抽屉 -->
    <Teleport to="body">
      <div v-if="selectedInstance" class="fixed inset-0 z-40 bg-slate-900/30 backdrop-blur-[1px]" @click="closeDetail" />
      <aside
        v-if="selectedInstance"
        class="fixed inset-y-0 right-0 z-50 flex w-full max-w-[640px] flex-col bg-white shadow-2xl"
      >
        <div class="flex items-start gap-3 border-b border-gray-100 px-4 py-3.5">
          <div
            class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border"
            :class="
              !selectedInstance.online
                ? 'border-gray-200 bg-gray-100 text-gray-400'
                : selectedInstance.primary
                  ? 'border-amber-200 bg-amber-50 text-amber-600'
                  : 'border-primary/20 bg-primary/10 text-primary'
            "
          >
            <ServerIcon class="h-4 w-4" />
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="truncate text-sm font-semibold text-gray-900">{{ displayName(selectedInstance) }}</span>
              <button type="button" class="text-gray-300 hover:text-primary" @click="openAlias(selectedInstance)">
                <PencilSquareIcon class="h-3.5 w-3.5" />
              </button>
              <span class="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-semibold" :class="statusOf(selectedInstance).cls">
                <span class="h-1 w-1 rounded-full" :class="statusOf(selectedInstance).dot" />
                {{ statusOf(selectedInstance).label }}
              </span>
              <span
                class="inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-semibold"
                :class="selectedInstance.primary ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 bg-gray-50 text-gray-500'"
              >
                {{ selectedInstance.primary ? '主节点' : '从节点' }}
              </span>
            </div>
            <div class="mt-0.5 truncate font-mono text-[10.5px] text-gray-400">{{ selectedInstance.host }}</div>
            <div class="mt-1 flex flex-wrap gap-1.5 text-[10px] text-gray-500">
              <span class="rounded-full border border-gray-200 px-1.5 py-0.5">{{ runtimeLabel(selectedInstance.runtime.kind) }}</span>
              <span class="rounded-full border border-gray-200 px-1.5 py-0.5">{{ selectedInstance.version.app || 'unknown' }}</span>
            </div>
          </div>
          <button type="button" class="rounded-lg border border-gray-200 px-2 py-1 text-[11px] text-gray-600" @click="closeDetail">
            关闭
          </button>
        </div>

        <div class="flex-1 space-y-4 overflow-y-auto px-4 py-3.5 custom-scrollbar">
          <!-- 身份与拓扑 -->
          <section>
            <div class="mb-1.5 flex items-center gap-2 text-[11px] font-semibold text-gray-400">
              身份与拓扑
              <span class="h-px flex-1 bg-gray-100" />
            </div>
            <div class="grid grid-cols-1 overflow-hidden rounded-lg border border-gray-100 sm:grid-cols-2">
              <div
                v-for="field in [
                  { k: '别名', v: selectedInstance.alias || '—（未设置，显示 hostname）' },
                  { k: 'hostname（实例 ID）', v: selectedInstance.host, mono: true },
                  { k: '主 / 从', v: selectedInstance.primary ? '主节点（执行定时调度）' : '从节点（不执行调度）' },
                  { k: 'K8s Node', v: selectedInstance.runtime.node_name || '—' },
                  { k: '内网 IP', v: selectedInstance.runtime.pod_ip || '—', mono: true },
                  { k: '部署形态', v: runtimeLabel(selectedInstance.runtime.kind) },
                  { k: '版本 / commit', v: `${selectedInstance.version.app || 'unknown'}${selectedInstance.version.commit ? ' · ' + selectedInstance.version.commit : ''}` },
                  { k: '镜像', v: selectedInstance.version.image || '—', mono: true },
                  { k: '启动时间', v: selectedInstance.started_at || '—' },
                  { k: '运行时长', v: selectedInstance.online ? fmtDuration(selectedInstance.uptime_seconds) : '进程已退出' },
                  { k: '最后心跳', v: fmtAgo(heartbeatAge(selectedInstance)) },
                  { k: 'Python / PID', v: `${selectedInstance.runtime.python_version || '—'} · ${selectedInstance.runtime.pid}` }
                ]"
                :key="field.k"
                class="flex items-center justify-between gap-2 border-b border-gray-50 px-2.5 py-1.5 text-xs last:border-b-0 sm:border-r"
              >
                <span class="shrink-0 text-gray-500">{{ field.k }}</span>
                <span class="truncate text-right font-medium text-gray-800" :class="field.mono ? 'font-mono text-[11px]' : ''">{{ field.v }}</span>
              </div>
            </div>
          </section>

          <!-- 中间件与运行时 -->
          <section>
            <div class="mb-1.5 flex items-center gap-2 text-[11px] font-semibold text-gray-400">
              中间件与运行时
              <span class="h-px flex-1 bg-gray-100" />
            </div>
            <div class="grid grid-cols-1 overflow-hidden rounded-lg border border-gray-100 sm:grid-cols-2">
              <div
                v-for="field in [
                  { k: '数据库类型', v: `${selectedInstance.stack.db_type} ${selectedInstance.stack.db_version}` },
                  { k: 'Redis', v: `${selectedInstance.stack.redis_flavor} ${selectedInstance.stack.redis_version}` },
                  { k: '线程 / 打开文件', v: `${selectedInstance.runtime.threads} / ${selectedInstance.runtime.open_files}` },
                  { k: '负载 1/5/15', v: (selectedInstance.resources.load || []).join(' / ') }
                ]"
                :key="field.k"
                class="flex items-center justify-between gap-2 border-b border-gray-50 px-2.5 py-1.5 text-xs last:border-b-0 sm:border-r"
              >
                <span class="shrink-0 text-gray-500">{{ field.k }}</span>
                <span class="truncate text-right font-medium text-gray-800">{{ field.v }}</span>
              </div>
            </div>
          </section>

          <!-- 资源 -->
          <section>
            <div class="mb-1.5 flex items-center gap-2 text-[11px] font-semibold text-gray-400">
              资源占用
              <span class="h-px flex-1 bg-gray-100" />
            </div>
            <div class="grid grid-cols-3 gap-2">
              <div v-for="metric in [
                {
                  label: `CPU ${selectedInstance.resources.cpu_count} 核`,
                  value: selectedInstance.online ? selectedInstance.resources.cpu_percent : 0,
                  text: selectedInstance.online ? `${selectedInstance.resources.cpu_percent}%` : '—'
                },
                {
                  label: '内存',
                  value: selectedInstance.online ? percent(selectedInstance.resources.mem_used_mb, selectedInstance.resources.mem_total_mb) : 0,
                  text: selectedInstance.online ? `${percent(selectedInstance.resources.mem_used_mb, selectedInstance.resources.mem_total_mb)}%` : '—'
                },
                {
                  label: '磁盘',
                  value: percent(selectedInstance.resources.disk_used_gb, selectedInstance.resources.disk_total_gb),
                  text: `${percent(selectedInstance.resources.disk_used_gb, selectedInstance.resources.disk_total_gb)}%`
                }
              ]" :key="metric.label" class="rounded-lg border border-gray-100 bg-gray-50/60 px-2 py-1.5">
                <div class="flex items-baseline justify-between text-[10px] text-gray-500">
                  <span class="truncate">{{ metric.label }}</span>
                  <b class="font-semibold text-gray-700">{{ metric.text }}</b>
                </div>
                <div class="mt-1 h-1 overflow-hidden rounded-full bg-gray-200">
                  <div class="h-full rounded-full" :class="barClass(metric.value)" :style="{ width: `${Math.min(100, metric.value)}%` }" />
                </div>
              </div>
            </div>
            <div class="mt-1.5 grid grid-cols-1 overflow-hidden rounded-lg border border-gray-100 sm:grid-cols-2">
              <div class="flex items-center justify-between border-b border-gray-50 px-2.5 py-1.5 text-xs sm:border-r">
                <span class="text-gray-500">内存 RSS / 上限</span>
                <span class="font-medium text-gray-800">{{ selectedInstance.resources.mem_used_mb }} / {{ selectedInstance.resources.mem_total_mb }} MB</span>
              </div>
              <div class="flex items-center justify-between px-2.5 py-1.5 text-xs">
                <span class="text-gray-500">磁盘占用</span>
                <span class="font-medium text-gray-800">{{ selectedInstance.resources.disk_used_gb }} / {{ selectedInstance.resources.disk_total_gb }} GB</span>
              </div>
            </div>
          </section>

          <!-- 依赖 -->
          <section>
            <div class="mb-1.5 flex items-center gap-2 text-[11px] font-semibold text-gray-400">
              依赖与连接延迟
              <span class="h-px flex-1 bg-gray-100" />
            </div>
            <div class="space-y-1.5">
              <div
                v-for="dep in [
                  { label: `${selectedInstance.stack.db_type} 主库`, ok: selectedInstance.deps.db_ok, latency: selectedInstance.deps.db_latency_ms },
                  { label: `Redis ${selectedInstance.stack.redis_version}`, ok: selectedInstance.deps.redis_ok, latency: selectedInstance.deps.redis_latency_ms }
                ]"
                :key="dep.label"
                class="flex items-center gap-2 rounded-lg border border-gray-100 px-2.5 py-1.5 text-xs"
              >
                <span
                  class="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-semibold"
                  :class="dep.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-rose-200 bg-rose-50 text-rose-700'"
                >
                  <span class="h-1 w-1 rounded-full" :class="dep.ok ? 'bg-emerald-500' : 'bg-rose-500'" />
                  {{ dep.ok ? '正常' : '异常' }}
                </span>
                <span class="font-medium text-gray-700">{{ dep.label }}</span>
                <span class="ml-auto tabular-nums text-gray-500">{{ dep.ok ? `${dep.latency} ms` : '—' }}</span>
              </div>
            </div>
          </section>

          <!-- 流量 -->
          <section>
            <div class="mb-1.5 flex items-center gap-2 text-[11px] font-semibold text-gray-400">
              流量与运行态（本节点 · 最近 60 秒）
              <span class="h-px flex-1 bg-gray-100" />
            </div>
            <div class="grid grid-cols-3 gap-2">
              <div v-for="metric in [
                { k: '近 1 分钟请求', v: selectedInstance.traffic.requests_1m },
                { k: 'QPS', v: selectedInstance.traffic.qps },
                { k: 'P95 延迟', v: `${selectedInstance.traffic.p95_ms} ms` },
                { k: 'P99 延迟', v: `${selectedInstance.traffic.p99_ms} ms` },
                { k: '5xx 错误率', v: `${selectedInstance.traffic.error_rate}%` },
                { k: '处理中请求', v: selectedInstance.traffic.inflight },
                { k: '对话流进行中', v: selectedInstance.traffic.chat_inflight },
                { k: '在线用户（集群）', v: selectedInstance.cluster.online_users }
              ]" :key="metric.k" class="rounded-lg border border-dashed border-gray-200 px-2 py-1.5">
                <div class="truncate text-[10px] text-gray-400">{{ metric.k }}</div>
                <div class="text-sm font-semibold text-gray-800">{{ metric.v }}</div>
              </div>
            </div>
          </section>

          <!-- 趋势 -->
          <section>
            <div class="mb-1.5 flex items-center gap-2 text-[11px] font-semibold text-gray-400">
              趋势
              <span class="h-px flex-1 bg-gray-100" />
              <div class="inline-flex rounded-lg border border-gray-200/70 bg-gray-50 p-0.5">
                <button
                  v-for="hours in [1, 6, 24]"
                  :key="hours"
                  type="button"
                  class="rounded-md px-2 py-0.5 text-[10.5px] transition"
                  :class="detailRange === hours ? 'bg-white font-semibold text-gray-900 shadow-sm' : 'text-gray-500'"
                  @click="changeRange(hours)"
                >
                  {{ hours }}h
                </button>
              </div>
            </div>
            <div v-if="detailLoading" class="flex items-center gap-2 py-6 text-xs text-gray-400">
              <ArrowPathIcon class="h-3.5 w-3.5 animate-spin" />
              正在读取趋势…
            </div>
            <div v-else-if="!detailSeries.length" class="rounded-lg border border-dashed border-gray-200 px-3 py-6 text-center text-xs text-gray-400">
              还没有趋势数据：每分钟落一个点，最多保留 24 小时
            </div>
            <div v-else class="space-y-2">
              <div
                v-for="chart in [
                  { label: 'CPU %', values: detailSeries.map((point) => point.cpu), color: '#2563eb', last: `${detailSeries[detailSeries.length - 1]?.cpu ?? 0}%` },
                  { label: '内存 MB', values: detailSeries.map((point) => point.mem_mb), color: '#7c3aed', last: `${detailSeries[detailSeries.length - 1]?.mem_mb ?? 0} MB` },
                  { label: 'QPS', values: detailSeries.map((point) => point.qps), color: '#0e7490', last: `${detailSeries[detailSeries.length - 1]?.qps ?? 0}` }
                ]"
                :key="chart.label"
                class="rounded-lg border border-gray-100 bg-gray-50/60 px-2 py-1.5"
              >
                <div class="flex items-baseline justify-between text-[10.5px] text-gray-500">
                  <span>{{ chart.label }}</span>
                  <b class="font-semibold text-gray-700">{{ chart.last }}</b>
                </div>
                <svg viewBox="0 0 520 90" class="mt-1 h-16 w-full" preserveAspectRatio="none">
                  <path :d="detailSpark(chart.values).area" :fill="chart.color" opacity="0.1" />
                  <path :d="detailSpark(chart.values).line" fill="none" :stroke="chart.color" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" />
                </svg>
              </div>
              <div class="text-[10.5px] text-gray-400">
                共 {{ detailSeries.length }} 个采样点，每 {{ overview.intervals.series_seconds }} 秒一个；超过 24 小时的自动从 Redis 裁掉。
              </div>
            </div>
          </section>
        </div>
      </aside>

      <!-- 别名弹窗 -->
      <div v-if="aliasTarget" class="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/35 p-5" @click.self="closeAlias">
        <div class="w-full max-w-[420px] overflow-hidden rounded-xl bg-white shadow-2xl">
          <div class="flex items-center justify-between border-b border-gray-100 px-4 py-3">
            <div class="text-sm font-semibold text-gray-900">编辑实例别名</div>
            <button type="button" class="text-gray-400 hover:text-gray-600" @click="closeAlias">
              <XMarkIcon class="h-4 w-4" />
            </button>
          </div>
          <div class="space-y-2.5 px-4 py-3.5">
            <div>
              <div class="mb-1 text-[11px] text-gray-500">hostname（不可改）</div>
              <div class="truncate rounded-lg border border-gray-100 bg-gray-50 px-2.5 py-1.5 font-mono text-[11.5px] text-gray-500">
                {{ aliasTarget.host }}
              </div>
            </div>
            <div>
              <div class="mb-1 text-[11px] text-gray-500">显示别名</div>
              <input
                v-model="aliasDraft"
                type="text"
                maxlength="32"
                placeholder="例如：生产节点 A / 华东接入 01"
                class="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/15"
                @keydown.enter="saveAlias"
              />
            </div>
            <div class="text-[10.5px] leading-relaxed text-gray-400">
              别名写入 <span class="font-mono">nanzi:instance:alias:{host}</span>，不随心跳过期；留空则回退显示 hostname。
            </div>
          </div>
          <div class="flex justify-end gap-2 border-t border-gray-100 px-4 py-2.5">
            <button type="button" class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600" @click="closeAlias">
              取消
            </button>
            <button
              type="button"
              class="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90 disabled:opacity-60"
              :disabled="aliasSaving"
              @click="saveAlias"
            >
              <ArrowPathIcon v-if="aliasSaving" class="h-3.5 w-3.5 animate-spin" />
              <CheckCircleIcon v-else class="h-3.5 w-3.5" />
              保存
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
