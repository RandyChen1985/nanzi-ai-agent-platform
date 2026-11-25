<script setup lang="ts">
/**
 * 「定义变更」页签：跨服务的定义变更待办与历史。
 *
 * 与服务内抽屉的分工：
 * - 这个页签回答「这周到底变了什么」，平铺 + 筛选，适合扫一遍；
 * - 抽屉回答「这个服务改哪了」，带服务上下文与单条动作。
 *
 * 数据口径：待确认数量只有一个来源（``counts.pending``），左栏卡片徽标读的是
 * ``/servers`` 里的 ``pending_change_count``，两者都由后端按同一张表算，前端不重算。
 */
import { computed, onMounted, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { ArrowPathIcon, ChevronDownIcon, ChevronRightIcon, ClockIcon } from '@heroicons/vue/24/outline'
import McpDefinitionChangeDetail from './McpDefinitionChangeDetail.vue'

interface DefinitionChange {
  id: string
  batch_id: string
  server_id: string
  server_name?: string | null
  tool_name: string
  tool_short_name: string
  change_kind: string
  severity: 'safe' | 'notice' | 'breaking'
  before_value: Record<string, any> | null
  after_value: Record<string, any> | null
  summary: string
  detected_at?: string | null
  detected_by?: string | null
  status: 'pending' | 'applied' | 'dismissed'
  handled_at?: string | null
  handled_by?: number | null
}

const emit = defineEmits<{
  /** 变更状态变了，父级可能需要刷新左栏角标 */
  (e: 'changed'): void
}>()

const { showToast } = useToast()
const { confirm } = useConfirm()

const loading = ref(false)
const acting = ref(false)
const items = ref<DefinitionChange[]>([])
const counts = ref({ pending: 0, applied: 0, dismissed: 0, breaking_pending: 0 })
const total = ref(0)
const expanded = ref<Set<string>>(new Set())
const picked = ref<Set<string>>(new Set())
const servers = ref<any[]>([])
/** 服务筛选下拉的选项。
 *
 * 光用 `/servers?scope=global` 不够：管理员在「定义变更」里看到的记录还包括他自己的
 * 个人服务（接口按归属裁剪，不按 scope），那些服务不在 global 列表里，下拉就选不到。
 * 于是把「本页出现过的服务」并进来——名字由 items 里的 server_name 提供。
 */
const serverOptions = computed(() => {
  const merged = new Map<string, string>()
  for (const server of servers.value) {
    if (server?.id) merged.set(String(server.id), server.server_name || String(server.id))
  }
  for (const item of items.value) {
    if (item?.server_id && !merged.has(String(item.server_id))) {
      merged.set(String(item.server_id), item.server_name || String(item.server_id))
    }
  }
  return Array.from(merged, ([id, name]) => ({ id, name }))
})

type StatusFilter = 'pending' | 'applied' | 'dismissed' | 'all'
const statusFilter = ref<StatusFilter>('pending')
const serverFilter = ref('')
const kindFilter = ref('')
const days = ref(7)

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

const KIND_GROUPS: Record<string, string[]> = {
  description: ['description_changed'],
  param: [
    'param_description', 'param_added_optional', 'param_removed',
    'param_required', 'param_type', 'param_enum',
  ],
  tool: ['tool_added', 'tool_removed', 'annotations_changed'],
}

const SEVERITY_META: Record<string, { label: string, cls: string }> = {
  breaking: { label: '破坏性', cls: 'border-red-200 bg-red-50 text-red-700' },
  notice: { label: '需留意', cls: 'border-amber-200 bg-amber-50 text-amber-700' },
  safe: { label: '安全', cls: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
}

const STATUS_LABELS: Record<string, string> = {
  pending: '待确认',
  applied: '已应用',
  dismissed: '已忽略',
}

const apiMessage = (error: any, fallback: string) => {
  const data = error?.response?.data
  const candidates = [data?.message, data?.detail, data?.data?.message, data?.data?.detail]
  return candidates.find((v) => typeof v === 'string' && v.trim()) || fallback
}

const pendingCount = computed(() => counts.value.pending)
// 必须用后端算的全局数，不能用 items 过滤：
// ① items 带 limit（默认 200），待办一多就少算；
// ② 切到「已应用 / 已忽略」分段时 items 里没有 pending 行，卡片会变成 0，
//    而标题写的是「破坏性待确认」——同排第一张卡是全局口径，两张卡对不上。
const breakingPending = computed(() => Number(counts.value.breaking_pending || 0))

const pickedBreaking = computed(() =>
  items.value.filter((i) => picked.value.has(i.id) && i.severity === 'breaking'))

/**
 * 探测覆盖：能自动盯住的服务 / 总数。
 * 「盯不住」比「有没有变更」更需要被人看到——一个 needs_user 的服务改了什么，
 * 这个页签永远不会告诉你。
 */
const probeCoverage = computed(() => {
  const list = servers.value.filter((s: any) => Number(s.enabled_status) === 1)
  const covered = list.filter((s: any) =>
    ['ok', 'changed'].includes(String(s.last_probe_status || ''))).length
  const blind = list.filter((s: any) =>
    ['needs_user', 'unreachable'].includes(String(s.last_probe_status || ''))).length
  return { covered, total: list.length, blind }
})

const fetchServers = async () => {
  try {
    const res = await axios.get('/api/portal/mcp/servers', { params: { scope: 'global' } })
    servers.value = Array.isArray(res.data) ? res.data : []
  } catch {
    // 服务列表只用于下拉与覆盖统计，失败不阻断变更列表
    servers.value = []
  }
}

const fetchChanges = async () => {
  loading.value = true
  try {
    const params: Record<string, any> = {
      status: statusFilter.value,
      days: days.value,
      limit: 200,
    }
    if (serverFilter.value) params.server_id = serverFilter.value
    if (kindFilter.value) params.kind = KIND_GROUPS[kindFilter.value]
    // 数组参数必须序列化成 `kind=a&kind=b`：axios 默认给数组加方括号（`kind[]=a`），
    // 而后端是 `kind: List[str] = Query(None)`——它只认重复同名键，带方括号的那个
    // 会被静默忽略，于是「按变更类型筛选」看起来点了没反应，永远返回全部类型。
    const res = await axios.get('/api/portal/mcp/changes', {
      params,
      paramsSerializer: { indexes: null },
    })
    items.value = Array.isArray(res.data?.items) ? res.data.items : []
    counts.value = {
      pending: Number(res.data?.counts?.pending || 0),
      applied: Number(res.data?.counts?.applied || 0),
      dismissed: Number(res.data?.counts?.dismissed || 0),
      breaking_pending: Number(res.data?.counts?.breaking_pending || 0),
    }
    total.value = Number(res.data?.total || 0)
    picked.value = new Set()
  } catch (error) {
    showToast(apiMessage(error, '加载定义变更失败'), 'error')
  } finally {
    loading.value = false
  }
}

const refreshAll = async () => {
  await Promise.all([fetchChanges(), fetchServers()])
}

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

const pickAll = () => {
  picked.value = new Set(items.value.filter((i) => i.status === 'pending').map((i) => i.id))
}

const applyChange = async (ids: string[]) => {
  if (!ids.length || acting.value) return
  const breakingCount = items.value.filter(
    (i) => ids.includes(i.id) && i.severity === 'breaking').length
  const ok = await confirm({
    title: '应用变更',
    message: breakingCount
      ? `选中的变更里有 ${breakingCount} 项破坏性变更，应用后已绑定这些工具的智能体可能立即调用失败。\n\n应用按服务整批同步远端（同服务的其他差异也会一起生效），确认继续？`
      : '应用按服务整批同步远端（同服务的其他差异也会一起生效），确认继续？',
    confirmText: '应用',
    danger: breakingCount > 0,
  })
  if (!ok) return
  acting.value = true
  try {
    const res = await axios.post('/api/portal/mcp/changes/apply', { change_ids: ids })
    const applied = Number(res.data?.applied || 0)
    const errors = Array.isArray(res.data?.errors) ? res.data.errors : []
    if (errors.length) {
      showToast(`部分服务应用失败：${errors[0]?.error || '未知原因'}`, 'error')
    } else {
      showToast(`已应用 ${applied || ids.length} 项变更`, 'success')
    }
    await refreshAll()
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
    showToast(`已忽略 ${Number(res.data?.dismissed || ids.length)} 项变更`, 'success')
    await refreshAll()
    emit('changed')
  } catch (error) {
    showToast(apiMessage(error, '忽略变更失败'), 'error')
  } finally {
    acting.value = false
  }
}

const formatTime = (value?: string | null) => {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

watch([statusFilter, serverFilter, kindFilter, days], fetchChanges)
onMounted(refreshAll)

defineExpose({ refresh: refreshAll })
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col gap-3">
    <!-- 四张统计卡：第一张回答「要不要现在处理」，最后一张回答「有没有服务根本没被盯着」 -->
    <div class="grid shrink-0 grid-cols-2 gap-2.5 lg:grid-cols-4">
      <div class="rounded-lg border border-gray-200 bg-white px-3.5 py-2.5 shadow-xs">
        <div class="flex items-center gap-1.5 text-[10px] text-gray-500">
          <ClockIcon class="h-3 w-3 text-violet-500" />待确认变更
        </div>
        <div class="mt-0.5 text-xl font-extrabold tracking-tight text-violet-700">{{ pendingCount }}</div>
        <!-- 副标题只放与主数字同源（counts）的指标：拿列表项去数「涉及几个服务」，
             切到「已应用」分段时列表里没有 pending，就会显示「待确认 5 · 来自 0 个服务」。 -->
        <div class="text-[10px] text-gray-400">近 {{ days }} 天 · 已忽略 {{ counts.dismissed }}</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white px-3.5 py-2.5 shadow-xs">
        <div class="text-[10px] text-gray-500">破坏性待确认</div>
        <div class="mt-0.5 text-xl font-extrabold tracking-tight text-red-600">{{ breakingPending }}</div>
        <div class="text-[10px] text-gray-400">删参数／改类型／可选改变必填</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white px-3.5 py-2.5 shadow-xs">
        <div class="text-[10px] text-gray-500">已应用</div>
        <div class="mt-0.5 text-xl font-extrabold tracking-tight text-slate-700">{{ counts.applied }}</div>
        <div class="text-[10px] text-gray-400">同步或应用后自动结清</div>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white px-3.5 py-2.5 shadow-xs">
        <div class="text-[10px] text-gray-500">探测覆盖</div>
        <div class="mt-0.5 text-xl font-extrabold tracking-tight text-slate-700">
          {{ probeCoverage.covered }}<span class="text-sm font-semibold text-gray-400">/{{ probeCoverage.total }}</span>
        </div>
        <div class="text-[10px]" :class="probeCoverage.blind ? 'text-amber-600' : 'text-gray-400'">
          {{ probeCoverage.blind ? `${probeCoverage.blind} 个服务盯不住` : '启用的服务都能自动探测' }}
        </div>
      </div>
    </div>

    <!-- 筛选条 -->
    <div class="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 shadow-xs">
      <div class="inline-flex rounded-md border border-gray-200 bg-gray-100/80 p-0.5">
        <button
          v-for="option in [
            { key: 'pending', label: '待确认' },
            { key: 'applied', label: '已应用' },
            { key: 'dismissed', label: '已忽略' },
            { key: 'all', label: '全部' },
          ]"
          :key="option.key"
          type="button"
          class="rounded px-2.5 py-1 text-[11.5px] font-semibold transition-colors"
          :class="statusFilter === option.key
            ? (option.key === 'pending' ? 'bg-violet-600 text-white shadow-xs' : 'bg-white text-slate-800 shadow-xs')
            : 'text-gray-500 hover:text-slate-800'"
          @click="statusFilter = option.key as StatusFilter"
        >{{ option.label }}<template v-if="option.key === 'pending' && pendingCount"> {{ pendingCount }}</template></button>
      </div>

      <select
        v-model="serverFilter"
        class="rounded-md border border-gray-300 bg-white py-1 pl-2 pr-6 text-[11.5px] text-slate-700 focus:border-violet-400 focus:outline-none focus:ring-1 focus:ring-violet-300"
        aria-label="按服务筛选"
      >
        <option value="">全部服务</option>
        <option v-for="server in serverOptions" :key="server.id" :value="server.id">{{ server.name }}</option>
      </select>

      <select
        v-model="kindFilter"
        class="rounded-md border border-gray-300 bg-white py-1 pl-2 pr-6 text-[11.5px] text-slate-700 focus:border-violet-400 focus:outline-none focus:ring-1 focus:ring-violet-300"
        aria-label="按变更类型筛选"
      >
        <option value="">全部类型</option>
        <option value="description">描述变更</option>
        <option value="param">参数变更</option>
        <option value="tool">工具增删与标注</option>
      </select>

      <select
        v-model.number="days"
        class="rounded-md border border-gray-300 bg-white py-1 pl-2 pr-6 text-[11.5px] text-slate-700 focus:border-violet-400 focus:outline-none focus:ring-1 focus:ring-violet-300"
        aria-label="时间范围"
      >
        <option :value="7">近 7 天</option>
        <option :value="30">近 30 天</option>
        <option :value="90">近 90 天</option>
        <option :value="365">近一年</option>
      </select>

      <button
        type="button"
        class="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-[11.5px] text-slate-600 transition-colors hover:bg-gray-50 disabled:cursor-wait disabled:opacity-60"
        :disabled="loading"
        @click="refreshAll"
      >
        <ArrowPathIcon class="h-3.5 w-3.5" :class="loading ? 'animate-spin' : ''" />刷新
      </button>

      <span class="ml-auto text-[11px] text-gray-400">{{ total }} 条记录</span>
    </div>

    <!-- 表格 -->
    <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xs">
      <div
        class="grid shrink-0 items-center gap-2.5 border-b border-gray-200 bg-gray-50 px-3.5 py-2 text-[10px] font-bold uppercase tracking-wider text-gray-500"
        style="grid-template-columns: 22px 170px 230px minmax(150px, 1fr) 90px 80px 92px"
      >
        <span></span>
        <span>服务</span>
        <span>工具 / 类型</span>
        <span>变更摘要</span>
        <span>严重度</span>
        <span>状态</span>
        <span>探测时间</span>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto">
        <div v-if="loading && !items.length" class="py-12 text-center text-xs text-gray-400">加载中…</div>
        <div v-else-if="!items.length" class="py-12 text-center text-xs text-gray-400">
          该筛选下没有记录。夜间巡检每天 04:30 比对一次远端 tools/list；也可以在服务内抽屉里点「立即探测」。
        </div>

        <div v-for="item in items" :key="item.id" class="border-b border-gray-100 last:border-b-0">
          <div
            class="grid cursor-pointer items-center gap-2.5 px-3.5 py-2.5 text-[12px] transition-colors hover:bg-gray-50"
            :class="item.status === 'pending' ? 'bg-violet-50/30' : ''"
            style="grid-template-columns: 22px 170px 230px minmax(150px, 1fr) 90px 80px 92px"
            @click="toggleExpand(item.id)"
          >
            <span class="flex items-center justify-center">
              <input
                v-if="item.status === 'pending'"
                type="checkbox"
                class="h-3.5 w-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-400"
                :checked="picked.has(item.id)"
                :aria-label="`选择 ${item.tool_short_name}`"
                @click.stop
                @change="togglePick(item.id)"
              />
            </span>
            <span class="truncate font-mono text-[11px] text-slate-600" :title="item.server_name || item.server_id">
              {{ item.server_name || item.server_id }}
            </span>
            <span class="flex min-w-0 items-center gap-1.5">
              <component
                :is="expanded.has(item.id) ? ChevronDownIcon : ChevronRightIcon"
                class="h-3 w-3 shrink-0 text-gray-400"
              />
              <span class="truncate font-mono text-[11.5px] font-bold text-slate-800">{{ item.tool_short_name }}</span>
              <span class="shrink-0 rounded border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[9px] font-semibold text-violet-700">
                {{ KIND_LABELS[item.change_kind] || item.change_kind }}
              </span>
            </span>
            <span class="truncate text-[11px] text-slate-600" :title="item.summary">{{ item.summary }}</span>
            <span class="flex items-center gap-1">
              <span class="rounded border px-1.5 py-0.5 text-[9px] font-bold" :class="SEVERITY_META[item.severity]?.cls">
                {{ SEVERITY_META[item.severity]?.label || item.severity }}
              </span>
            </span>
            <span>
              <span
                class="rounded px-1.5 py-0.5 text-[9px] font-semibold"
                :class="item.status === 'pending'
                  ? 'bg-violet-100 text-violet-700'
                  : (item.status === 'applied' ? 'bg-emerald-50 text-emerald-600' : 'bg-gray-100 text-gray-500')"
              >{{ STATUS_LABELS[item.status] }}</span>
            </span>
            <span class="text-[10.5px] tabular-nums text-gray-400">{{ formatTime(item.detected_at) }}</span>
          </div>

          <div v-if="expanded.has(item.id)" class="border-b border-gray-100 bg-gray-50/60 px-3.5 py-3 pl-12">
            <McpDefinitionChangeDetail :change="item" />
            <div v-if="item.status === 'pending'" class="mt-2 flex gap-2">
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
            <div v-else class="mt-1.5 text-[10px] text-gray-400">
              {{ STATUS_LABELS[item.status] }}于 {{ formatTime(item.handled_at) }}
            </div>
          </div>
        </div>
      </div>

      <div class="flex shrink-0 items-center gap-2 border-t border-gray-200 bg-gray-50 px-3.5 py-2 text-[11.5px] text-gray-600">
        <span>
          已选 <b class="text-slate-800">{{ picked.size }}</b> 项<template v-if="pickedBreaking.length">（含 <span class="text-red-600">{{ pickedBreaking.length }} 项破坏性</span>）</template>
        </span>
        <button v-if="statusFilter === 'pending'" type="button" class="text-violet-600 hover:underline" @click="pickAll">全选本页</button>
        <button v-if="picked.size" type="button" class="text-gray-400 hover:underline" @click="picked = new Set()">清空</button>
        <div class="ml-auto flex gap-2">
          <button
            type="button"
            class="rounded border border-gray-300 bg-white px-2.5 py-1 text-[11.5px] text-slate-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!picked.size || acting"
            @click="dismissChange(Array.from(picked))"
          >忽略所选</button>
          <button
            type="button"
            class="rounded border border-violet-300 bg-violet-600 px-2.5 py-1 text-[11.5px] font-semibold text-white transition-colors hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!picked.size || acting"
            @click="applyChange(Array.from(picked))"
          >应用所选</button>
        </div>
      </div>
    </div>
  </div>
</template>
