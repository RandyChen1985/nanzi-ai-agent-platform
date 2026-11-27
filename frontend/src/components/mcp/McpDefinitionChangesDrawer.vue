<script setup lang="ts">
/**
 * 工具定义变更抽屉（单服务）。
 *
 * 这个组件只做「看差异 + 决定」两件事，不自己算差异——差异由后端在探测时算好落库，
 * 前端拿到的 before/after 是已经拆到参数级的片段。前端重算会有两个口径（比如
 * 「整块 schema 变了」vs「某个可选参数变必填」），那正是这个功能要消除的问题。
 *
 * 一个刻意的不对称：「应用」是**按服务整批同步远端**，不是只写勾选的那几条。
 * sync_tools() 本来就是整批覆盖语义，硬做局部应用会多出一条写路径、最终和凭据槽
 * 清理对不上。所以有破坏性变更时，确认框里必须说清「未勾选的项也会一起同步」。
 */
import { computed, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useUser } from '@/composables/useUser'
import {
  XMarkIcon,
  ArrowPathIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
} from '@heroicons/vue/24/outline'
import McpDefinitionChangeDetail from './McpDefinitionChangeDetail.vue'

type Severity = 'safe' | 'notice' | 'breaking'
type ChangeStatus = 'pending' | 'applied' | 'dismissed'

interface DefinitionChange {
  id: string
  batch_id: string
  server_id: string
  server_name?: string | null
  tool_name: string
  tool_short_name: string
  change_kind: string
  severity: Severity
  before_value: Record<string, any> | null
  after_value: Record<string, any> | null
  summary: string
  detected_at?: string | null
  detected_by?: string | null
  status: ChangeStatus
  handled_at?: string | null
  handled_by?: number | null
}

const props = withDefaults(defineProps<{
  visible: boolean
  serverId: string
  serverName?: string
}>(), {
  serverName: '',
})

const emit = defineEmits<{
  (e: 'close'): void
  /** 应用/忽略之后，列表卡片上的角标需要跟着变 */
  (e: 'changed'): void
}>()

const { showToast } = useToast()
const { confirm } = useConfirm()
const { hasPermission } = useUser()

/** 探测与应用都是写操作，统一按 sync 码校验（后端三个接口都挂了这个权限）。 */
const canManage = computed(() => hasPermission('element:mcp_management:sync'))

const loading = ref(false)
const acting = ref(false)
const items = ref<DefinitionChange[]>([])
const counts = ref({ pending: 0, applied: 0, dismissed: 0 })
const expanded = ref<Set<string>>(new Set())
const picked = ref<Set<string>>(new Set())
const loadError = ref('')

const apiMessage = (error: any, fallback: string) => {
  const data = error?.response?.data
  const candidates = [data?.message, data?.detail, data?.data?.message, data?.data?.detail]
  return candidates.find((v) => typeof v === 'string' && v.trim()) || fallback
}

const KIND_LABELS: Record<string, string> = {
  description_changed: '描述变更',
  param_description: '参数描述',
  param_added_optional: '新增可选参数',
  param_removed: '删除参数',
  param_required: '必填变化',
  param_type: '类型变更',
  param_enum: '枚举变更',
  tool_added: '新增工具',
  tool_removed: '工具删除',
  annotations_changed: '标注变更',
}

const SOURCE_LABELS: Record<string, string> = {
  nightly: '夜间巡检',
  page: '进页面探测',
  hot_call: '活跃会话热刷新',
  manual: '手动探测',
}

const SEVERITY_META: Record<Severity, { label: string, cls: string }> = {
  breaking: { label: '破坏性', cls: 'border-red-200 bg-red-50 text-red-700' },
  notice: { label: '需留意', cls: 'border-amber-200 bg-amber-50 text-amber-700' },
  safe: { label: '安全', cls: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
}

const STATUS_LABELS: Record<ChangeStatus, string> = {
  pending: '待确认',
  applied: '已应用',
  dismissed: '已忽略',
}

/** 按探测批次分组：一次巡检一批，人看的是「那天凌晨发现了什么」 */
const batches = computed(() => {
  const map = new Map<string, DefinitionChange[]>()
  for (const item of items.value) {
    const key = item.batch_id || 'unknown'
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(item)
  }
  return Array.from(map.entries()).map(([batchId, list]) => ({
    batchId,
    detectedAt: list[0]?.detected_at || '',
    detectedBy: list[0]?.detected_by || 'manual',
    pendingCount: list.filter((i) => i.status === 'pending').length,
    items: list,
  }))
})

const pendingIds = computed(() => items.value.filter((i) => i.status === 'pending').map((i) => i.id))
const pickedBreaking = computed(() =>
  items.value.filter((i) => picked.value.has(i.id) && i.severity === 'breaking'))

const toggleExpand = (id: string) => {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

const togglePick = (id: string) => {
  const next = new Set(picked.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  picked.value = next
}

const pickAllPending = () => {
  picked.value = new Set(pendingIds.value)
}

const clearPicked = () => {
  picked.value = new Set()
}

const formatTime = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

let fetchSeq = 0

const fetchChanges = async () => {
  const targetServerId = props.serverId
  if (!targetServerId) return
  const seq = ++fetchSeq
  loading.value = true
  loadError.value = ''
  try {
    const res = await axios.get('/api/portal/mcp/changes', {
      params: { server_id: targetServerId, status: 'all', days: 90, limit: 200 },
    })
    if (seq !== fetchSeq || props.serverId !== targetServerId) return
    items.value = Array.isArray(res.data?.items) ? res.data.items : []
    const backendCounts = res.data?.counts
    counts.value = backendCounts
      ? {
          pending: Number(backendCounts.pending || 0),
          applied: Number(backendCounts.applied || 0),
          dismissed: Number(backendCounts.dismissed || 0),
        }
      : {
          pending: items.value.filter((i) => i.status === 'pending').length,
          applied: items.value.filter((i) => i.status === 'applied').length,
          dismissed: items.value.filter((i) => i.status === 'dismissed').length,
        }
    picked.value = new Set()
    // 打开就能看到差异，不用先点一下展开——默认收起会让抽屉看起来是空的。
    const firstPending = items.value.find((i) => i.status === 'pending')
    expanded.value = new Set(firstPending ? [firstPending.id] : [])
  } catch (error) {
    if (seq !== fetchSeq || props.serverId !== targetServerId) return
    loadError.value = apiMessage(error, '加载定义变更记录失败')
  } finally {
    if (seq === fetchSeq) loading.value = false
  }
}

/**
 * 立即探测：不等凌晨那一趟。
 *
 * 单独给这个入口是因为「重新同步」会直接改本地定义，而人只想先看清楚远端现在长什么样——
 * 如果唯一的检查手段是同步，那检查就等于应用。
 */
const probingServerId = ref('')
const probing = computed(() => Boolean(props.serverId && probingServerId.value === props.serverId))

const PROBE_MESSAGES: Record<string, string> = {
  ok: '远端定义与本地一致',
  unreachable: '连不上远端，本次跳过差异比对',
  needs_user: '该服务需要用户签名，后台链路无法探测',
  skipped_disabled: '服务已禁用，跳过探测',
}

const probeNow = async () => {
  const targetServerId = props.serverId
  if (probing.value || !targetServerId) return
  // 探测期间可能切到别的服务，名字要在发起时抓住（切走后 props 已经是别人的）
  const targetName = props.serverName || targetServerId
  probingServerId.value = targetServerId
  try {
    const res = await axios.post(`/api/portal/mcp/servers/${targetServerId}/probe`)
    const status = String(res.data?.status || '')
    const newChanges = Number(res.data?.new_changes || 0)
    const baselineTools = Number(res.data?.baseline_tools || 0)
    const detailError = String(res.data?.error || '').trim()
    // 探测期间切到别的服务：结论只属于发起时那个服务，挂到当前服务上会指错对象。
    const stillOnTarget = props.serverId === targetServerId
    const prefix = stillOnTarget ? '' : `${targetName}：`
    if (status === 'changed') {
      showToast(
        newChanges > 0 ? `${prefix}发现 ${newChanges} 处新变更` : `${prefix}远端定义与本地已不一致（差异已在列表中）`,
        'warning',
      )
    } else if (status === 'unreachable') {
      showToast(prefix + (detailError || PROBE_MESSAGES.unreachable), 'error')
    } else if (status === 'needs_user' || status === 'skipped_disabled') {
      showToast(prefix + (detailError || PROBE_MESSAGES[status]), 'warning')
    } else if (status === 'ok' && baselineTools > 0) {
      // 首次探测不算「一致」，而是「刚建立基线」——说成一致会让人以为比对过一轮。
      showToast(`${prefix}已为 ${baselineTools} 个工具建立基线，之后的变化才会被报出来`, 'success')
    } else {
      showToast(prefix + (PROBE_MESSAGES[status] || '探测完成'), 'success')
    }
    if (stillOnTarget) {
      await fetchChanges()
      emit('changed')
    }
  } catch (error) {
    showToast(apiMessage(error, '探测失败'), 'error')
  } finally {
    if (probingServerId.value === targetServerId) probingServerId.value = ''
  }
}

const applyChange = async (ids: string[]) => {
  if (!ids.length || acting.value) return
  const breakingCount = items.value.filter(
    (i) => ids.includes(i.id) && i.severity === 'breaking').length
  const ok = await confirm({
    title: '应用变更',
    message: breakingCount
      ? `选中的变更里有 ${breakingCount} 项破坏性变更，应用后已绑定这些工具的智能体可能立即调用失败。\n\n应用会按远端当前状态对该服务的工具定义做一次整批同步（未勾选的其他差异也会一起生效），确认继续？`
      : '应用会按远端当前状态对该服务的工具定义做一次整批同步（未勾选的其他差异也会一起生效），确认继续？',
    confirmText: '应用',
    danger: breakingCount > 0,
  })
  if (!ok) return

  acting.value = true
  try {
    const res = await axios.post('/api/portal/mcp/changes/apply', { change_ids: ids })
    const applied = Number(res.data?.applied ?? 0)
    const errors = Array.isArray(res.data?.errors) ? res.data.errors : []
    if (errors.length) {
      // 后端是「逐服务循环」：可能一部分服务已应用、另一部分失败。只说「失败」会让
      // 用户去重试已经生效的那部分，所以有 applied 时必须把成功条数一起说清。
      const reason = errors[0]?.error || '未知原因'
      showToast(
        applied > 0 ? `已应用 ${applied} 项，另有服务同步失败：${reason}` : `应用变更失败：${reason}`,
        applied > 0 ? 'warning' : 'error',
      )
    } else if (applied > 0) {
      showToast(`已应用 ${applied} 项变更`, 'success')
    } else {
      showToast('没有可应用的待确认变更（可能已被他人处理）', 'warning')
    }
    await fetchChanges()
    emit('changed')
  } catch (error) {
    showToast(apiMessage(error, '应用变更失败'), 'error')
  } finally {
    acting.value = false
  }
}

const dismissChange = async (ids: string[]) => {
  if (!ids.length || acting.value) return
  acting.value = true
  try {
    const res = await axios.post('/api/portal/mcp/changes/dismiss', { change_ids: ids })
    const dismissed = Number(res.data?.dismissed ?? 0)
    if (dismissed > 0) {
      showToast(`已忽略 ${dismissed} 项变更`, 'success')
    } else {
      showToast('没有可忽略的待确认变更（可能已被他人处理）', 'warning')
    }
    await fetchChanges()
    emit('changed')
  } catch (error) {
    showToast(apiMessage(error, '忽略变更失败'), 'error')
  } finally {
    acting.value = false
  }
}

watch(
  () => [props.visible, props.serverId] as const,
  ([visible, serverId], prev) => {
    if (serverId !== prev?.[1]) {
      fetchSeq += 1
      items.value = []
      counts.value = { pending: 0, applied: 0, dismissed: 0 }
      picked.value = new Set()
      expanded.value = new Set()
      loadError.value = ''
    }
    if (visible && serverId) fetchChanges()
  },
  { immediate: true },
)
</script>

<template>
  <div v-if="visible" class="fixed inset-0 z-[70] flex justify-end" role="dialog" aria-modal="true" aria-labelledby="mcp-definition-changes-title">
    <div class="absolute inset-0 bg-gray-900/30 backdrop-blur-sm" @click="emit('close')"></div>

    <div class="relative flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl animate-slide-in-right">
      <div class="flex items-start justify-between gap-3 border-b border-gray-100 bg-gray-50/60 p-4">
        <div class="min-w-0">
          <h3 id="mcp-definition-changes-title" class="flex items-center gap-2 text-sm font-bold text-slate-800">
            <ClockIcon class="h-4 w-4 text-violet-500" />
            工具定义变更
          </h3>
          <p class="mt-1 truncate text-[11px] text-slate-400">
            服务 <span class="font-mono text-slate-500">{{ serverName || serverId }}</span> · 自动探测远端 tools/list 与本地定义的差异
          </p>
        </div>
        <div class="flex shrink-0 items-center gap-1">
          <button
            v-if="canManage"
            type="button"
            class="inline-flex items-center gap-1 rounded-md border border-violet-200 bg-violet-50 px-2 py-1 text-[11px] font-semibold text-violet-700 transition-colors hover:bg-violet-100 disabled:cursor-wait disabled:opacity-60"
            :disabled="probing"
            v-tooltip="'现在就比一次远端 tools/list 与本地定义（只读，不会改动任何工具）'"
            @click="probeNow"
          >
            <ArrowPathIcon class="h-3.5 w-3.5" :class="probing ? 'animate-spin' : ''" />
            立即探测
          </button>
          <button
            type="button"
            class="rounded-md p-1 text-gray-400 transition-colors hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-200"
            :disabled="loading"
            aria-label="刷新变更记录"
            @click="fetchChanges"
          >
            <ArrowPathIcon class="h-4 w-4" :class="loading ? 'animate-spin' : ''" />
          </button>
          <button
            type="button"
            class="rounded-md p-1 text-gray-400 transition-colors hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-200"
            aria-label="关闭变更抽屉"
            @click="emit('close')"
          >
            <XMarkIcon class="h-5 w-5" />
          </button>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-3 border-b border-gray-100 px-4 py-2.5 text-[11px]">
        <span class="text-violet-700">待确认 <b class="font-bold">{{ counts.pending }}</b> 项</span>
        <span class="text-slate-400">已应用 {{ counts.applied }}</span>
        <span class="text-slate-400">已忽略 {{ counts.dismissed }}</span>
        <span class="ml-auto text-slate-400">最近探测 {{ formatTime(items[0]?.detected_at) || '—' }}</span>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <div v-if="loadError" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {{ loadError }}
        </div>

        <div v-else-if="!loading && !items.length" class="mt-10 text-center text-xs text-slate-400">
          还没有发现定义差异。夜间巡检每天 04:30 会比对一次远端 tools/list。
        </div>

        <div v-for="batch in batches" :key="batch.batchId" class="mb-4">
          <div class="mb-2 flex items-center gap-2 text-[11px]">
            <span class="font-mono font-semibold text-slate-600">{{ formatTime(batch.detectedAt) }}</span>
            <span class="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">
              {{ SOURCE_LABELS[batch.detectedBy] || batch.detectedBy }}
            </span>
            <span v-if="batch.pendingCount" class="text-violet-600">{{ batch.pendingCount }} 项待确认</span>
            <span class="ml-auto text-slate-300">{{ batch.items.length }} 项</span>
          </div>

          <div class="divide-y divide-gray-100 overflow-hidden rounded-lg border border-gray-200">
            <div v-for="item in batch.items" :key="item.id" :class="item.status === 'pending' ? 'bg-violet-50/30' : 'bg-white'">
              <div class="flex items-start gap-2 px-3 py-2.5">
                <input
                  v-if="item.status === 'pending' && canManage"
                  type="checkbox"
                  class="mt-1 h-3.5 w-3.5 shrink-0 rounded border-gray-300 text-violet-600 focus:ring-violet-400"
                  :checked="picked.has(item.id)"
                  :aria-label="`选择 ${item.tool_short_name} 的变更`"
                  @change="togglePick(item.id)"
                />
                <span v-else class="mt-1 h-3.5 w-3.5 shrink-0"></span>

                <button type="button" class="min-w-0 flex-1 text-left" @click="toggleExpand(item.id)">
                  <div class="flex flex-wrap items-center gap-1.5">
                    <ChevronRightIcon v-if="!expanded.has(item.id)" class="h-3 w-3 shrink-0 text-slate-400" />
                    <ChevronDownIcon v-else class="h-3 w-3 shrink-0 text-slate-400" />
                    <span class="font-mono text-[12px] font-bold text-slate-800 break-all">{{ item.tool_short_name }}</span>
                    <span class="rounded border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[9px] font-semibold text-violet-700">
                      {{ KIND_LABELS[item.change_kind] || item.change_kind }}
                    </span>
                    <span class="text-[11px] text-slate-500">{{ item.summary }}</span>
                    <span
                      class="ml-auto rounded border px-1.5 py-0.5 text-[9px] font-bold"
                      :class="SEVERITY_META[item.severity]?.cls"
                    >{{ SEVERITY_META[item.severity]?.label || item.severity }}</span>
                    <span
                      class="rounded px-1.5 py-0.5 text-[9px] font-semibold"
                      :class="item.status === 'pending'
                        ? 'bg-violet-100 text-violet-700'
                        : (item.status === 'applied' ? 'bg-emerald-50 text-emerald-600' : 'bg-gray-100 text-gray-500')"
                    >{{ STATUS_LABELS[item.status] }}</span>
                  </div>
                </button>
              </div>

              <div v-if="expanded.has(item.id)" class="border-t border-gray-100 bg-gray-50/60 px-3 py-3 pl-9">
                <McpDefinitionChangeDetail :change="item" />

                <div v-if="item.status === 'pending' && canManage" class="mt-2 flex gap-2">
                  <button
                    type="button"
                    class="rounded border border-violet-300 bg-violet-600 px-2 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-violet-700 disabled:cursor-wait disabled:opacity-60"
                    :disabled="acting"
                    @click="applyChange([item.id])"
                  >应用这一项</button>
                  <button
                    type="button"
                    class="rounded border border-gray-300 bg-white px-2 py-1 text-[11px] text-slate-600 transition-colors hover:bg-gray-50 disabled:cursor-wait disabled:opacity-60"
                    :disabled="acting"
                    @click="dismissChange([item.id])"
                  >忽略</button>
                </div>
                <div v-else-if="item.status !== 'pending'" class="mt-2 text-[10px] text-slate-400">
                  {{ STATUS_LABELS[item.status] }}{{ formatTime(item.handled_at) ? `于 ${formatTime(item.handled_at)}` : '' }}
                </div>
                <div v-else class="mt-2 text-[10px] text-slate-400">
                  待确认（需要同步权限方可应用或忽略）
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-2 border-t border-gray-100 bg-gray-50/60 px-4 py-2.5 text-[11px]">
        <template v-if="canManage">
          <span class="text-slate-500">
            已选 <b class="text-slate-700">{{ picked.size }}</b> 项<template v-if="pickedBreaking.length">（含 <span class="text-red-600">{{ pickedBreaking.length }} 项破坏性</span>）</template>
          </span>
          <button type="button" class="text-violet-600 hover:underline" @click="pickAllPending">全选待确认</button>
          <button v-if="picked.size" type="button" class="text-slate-400 hover:underline" @click="clearPicked">清空</button>
        </template>
        <div v-if="canManage" class="ml-auto flex gap-2">
          <button
            type="button"
            class="rounded border border-gray-300 bg-white px-2.5 py-1 text-[11px] text-slate-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!picked.size || acting"
            @click="dismissChange(Array.from(picked))"
          >忽略所选</button>
          <button
            type="button"
            class="rounded border border-violet-300 bg-violet-600 px-2.5 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!picked.size || acting"
            @click="applyChange(Array.from(picked))"
          >应用所选</button>
        </div>
        <span v-else class="ml-auto text-[10px] text-slate-400">只读：你没有 MCP 同步权限</span>
      </div>
    </div>
  </div>
</template>
