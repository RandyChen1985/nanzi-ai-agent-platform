<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import {
  agentApi,
  type AgentExecutionHistory,
  type AIAgent,
  type ContextCompactionRecord,
} from '../api/agent'
import {
  ChatBubbleLeftRightIcon,
  FunnelIcon,
  ArrowPathIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronDownIcon,
  MagnifyingGlassIcon,
  ClockIcon,
  ExclamationCircleIcon,
  SparklesIcon,
  ArrowDownTrayIcon,
  DocumentDuplicateIcon,
  Bars3BottomLeftIcon,
  WrenchScrewdriverIcon,
  XMarkIcon,
} from '@heroicons/vue/24/outline'
import { format } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import { renderMarkdown } from '@/utils/markdown'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import { parsePageInput } from '../utils/pagination'
import {
  buildChatSessionMarkdown,
  buildSessionExportFilename,
  downloadMarkdownFile,
  type ChatTraceDetail,
} from '@/utils/chatSessionExport'
import { copyToClipboard } from '@/utils/clipboard'
import ContextCompactionTimeline from '@/components/chat/ContextCompactionTimeline.vue'
import UserQuestionCard from '@/components/UserQuestionCard.vue'
// AI 回复必须与聊天页走同一个渲染器，否则 ```chart / ```mermaid / <sql_plan> 会退化成
// 裸代码块（日志里只剩一坨 JSON）。只读模式：不接引用面板、产物下载按钮与画布。
import MessageRenderer from '@/components/MessageRenderer.vue'
// 该渲染器的排版样式一直由宿主提供，抽成一份共用，避免日志页再手抄一遍。
import '@/styles/messageMarkdownHost.css'
import {
  applyUserQuestionReceipts,
  parseUserQuestionReceipt,
  userQuestionStatesFromTimeline,
  userQuestionTextFromTimeline,
  type UserQuestionReceipt,
  type UserQuestionState,
} from '@/utils/userQuestion'
import {
  formatSubagentTraceSummary,
  normalizeSubagentTraceMeta,
  subagentDisplayName,
} from '@/utils/subagentTrace'
import { imageUsageChips, imageUsageFromStep } from '@/utils/traceUsage'
import {
  compactSingleLine,
  countTurnToolSteps,
  formatDurationMs,
  getChatLogStatusMeta,
  getTraceEventMeta,
  parseChatLogQuery,
  summarizeTraceStep,
} from '@/utils/chatLogPresentation'

const { showToast } = useToast()
const { hasPermission } = useUser()

const canExportLogs = computed(() => hasPermission('element:chat_logs:export'))

const cachedUser = localStorage.getItem('user_info')
const userInfo = ref(cachedUser ? JSON.parse(cachedUser) : null)
const isAdmin = computed(() => userInfo.value?.role === 'admin')

/**
 * 聊天日志审计页的两套数据源。
 *
 * - admin：走 `/api/v1/chat/admin/*` 审计端点，可跨用户查看（这是本页存在的意义）；
 * - 其他用户（拥有 `menu:chat_logs` 时）：退化为只看自己的会话。
 *
 * 后端把两者做成不同端点，是为了让聊天界面（EmbedChat）不可能误用跨用户视图：
 * 常规 `/chat/history`、`/chat/logs/*` 始终按当前用户过滤，任何角色都一样。
 */
const historyApi = computed(() =>
  isAdmin.value ? agentApi.getAdminChatHistory : agentApi.getChatHistory,
)
const traceApi = computed(() =>
  isAdmin.value ? agentApi.getAdminChatTrace : agentApi.getChatTrace,
)
const compactionsApi = computed(() =>
  isAdmin.value ? agentApi.getAdminContextCompactions : agentApi.getContextCompactions,
)

const viewMode = ref<'conversation' | 'turn'>('conversation')
const logs = ref<AgentExecutionHistory[]>([])
const agents = ref<AIAgent[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(15)

// 会话常达数百上千条，只靠上一页/下一页要翻几十次；这里支持直接输入页码跳转
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const pageInput = ref('')
watch(page, (value) => { pageInput.value = String(value) }, { immediate: true })

const applyPageInput = () => {
  const target = parsePageInput(pageInput.value, page.value, totalPages.value)
  pageInput.value = String(target)
  if (target !== page.value) page.value = target // watch([page, pageSize]) 负责重新拉取
}

const loading = ref(false)
const showFilters = ref(false)
const isListCollapsed = ref(false)

const filters = ref({
  agent_id: '',
  username: '',
  keyword: '',
  status: '',
  start_date: '',
  end_date: '',
})

const quickStatusOptions = [
  { value: '', label: '全部', dotClass: '' },
  { value: 'success', label: '成功', dotClass: 'bg-emerald-500' },
  { value: 'error', label: '异常', dotClass: 'bg-red-500' },
  { value: 'cancelled', label: '已取消', dotClass: 'bg-slate-400' },
] as const

const selectedId = ref<number | string | null>(null)
const activeDetailTab = ref<'conversation' | 'trace' | 'context'>('conversation')
const replyViewMode = ref<'render' | 'source'>('render')
const turnReplyModes = ref<Record<string | number, 'render' | 'source'>>({})
const expandedAutoRuleTurns = ref<Record<string | number, boolean>>({})
const expandedTraceSteps = ref<Record<string, boolean>>({})
const traceDetail = ref<any>(null)
const traceLoading = ref(false)
const contextCompactions = ref<ContextCompactionRecord[]>([])
const contextLoading = ref(false)
const contextError = ref(false)
const contextLoadedFor = ref('')
let contextRequestVersion = 0
const exporting = ref(false)

// 多轮对话状态
/** 日志轮次：附带从 process_timeline 快照重建的提问卡，用于只读回放 AI 的主动提问。 */
type ConversationTurn = AgentExecutionHistory & {
  userQuestion?: UserQuestionState
  /** 修复前落库的轮次没有卡片快照，退化为只回放问题文本 */
  userQuestionText?: string
}
const conversationTurns = ref<ConversationTurn[]>([])
/** 该会话的总轮数（后端 total）。详情一次只取最近 100 轮，需要据此提示用户。 */
const turnsTotal = ref(0)
const turnsLoading = ref(false)
const selectedTraceId = ref<string | null>(null)
let turnsRequestVersion = 0

const selectedLog = computed(
  () => logs.value.find((l) => l.id === selectedId.value) || null,
)

const activeTurn = computed(() => {
  if (selectedTraceId.value) {
    const found = conversationTurns.value.find((t) => t.trace_id === selectedTraceId.value)
    if (found) return found
  }
  return selectedLog.value
})

const activeTurnIndex = computed(() => {
  if (!conversationTurns.value.length) return -1
  if (selectedTraceId.value) {
    const idx = conversationTurns.value.findIndex((t) => t.trace_id === selectedTraceId.value)
    if (idx !== -1) return idx
  }
  return conversationTurns.value.length - 1
})

const selectedSessionParsedQuery = computed(() => {
  const firstTurn = conversationTurns.value[0]
  return parseChatLogQuery(firstTurn?.query || selectedLog.value?.query)
})

const selectedStatusMeta = computed(() =>
  getChatLogStatusMeta(activeTurn.value?.status || selectedLog.value?.status),
)

const getTurnReplyMode = (id: string | number) => turnReplyModes.value[id] || 'render'
const setTurnReplyMode = (id: string | number, mode: 'render' | 'source') => {
  turnReplyModes.value[id] = mode
}

const isAutoRuleExpanded = (id: string | number) => !!expandedAutoRuleTurns.value[id]
const toggleAutoRuleExpanded = (id: string | number) => {
  expandedAutoRuleTurns.value[id] = !expandedAutoRuleTurns.value[id]
}

const traceSteps = computed<any[]>(() => traceDetail.value?.steps || [])

const traceTotalStepMs = computed(() =>
  traceSteps.value.reduce((sum, step) => sum + Math.max(0, Number(step?.execution_time_ms || 0)), 0),
)

const traceToolCallCount = computed(() =>
  traceSteps.value.filter((step) => step?.event_type === 'tool_call' || !!step?.tool_name).length,
)

const getTraceStepKey = (idx: number) => `${selectedTraceId.value || 'trace'}-${idx}`

const isTraceStepExpanded = (step: any, idx: number) => {
  const key = getTraceStepKey(idx)
  if (expandedTraceSteps.value[key] !== undefined) {
    return expandedTraceSteps.value[key]
  }
  // 异常步、子代理委派、图像用量或少步链路默认展开，降低排查点击次数
  return (
    step?.status !== 'success' ||
    step?.event_type === 'error' ||
    !!step?.error_message ||
    !!getSubagentMeta(step) ||
    !!imageUsageFromStep(step) ||
    traceSteps.value.length <= 2
  )
}

const toggleTraceStep = (step: any, idx: number) => {
  const key = getTraceStepKey(idx)
  expandedTraceSteps.value[key] = !isTraceStepExpanded(step, idx)
}

const setAllTraceStepsExpanded = (expanded: boolean) => {
  traceSteps.value.forEach((_, idx) => {
    expandedTraceSteps.value[getTraceStepKey(idx)] = expanded
  })
}

const stepDurationPercent = (step: any) => {
  const totalMs = traceTotalStepMs.value
  const ms = Math.max(0, Number(step?.execution_time_ms || 0))
  if (!totalMs || !ms) return 0
  return Math.max(4, Math.min(100, Math.round((ms / totalMs) * 100)))
}

/** 无回复轮次的说明文案。
 *
 * 原先只显示「(无响应内容)」，用户看不出这是被取消、执行失败，还是模型确实没返回——
 * 这三种情况的处置完全不同（取消无需追查、失败要看轨迹、静默无返回才可能是异常）。
 */
const emptyReplyView = (turn: ConversationTurn) => {
  const status = (turn.status || '').trim().toLowerCase()
  if (status === 'cancelled') {
    return {
      title: '该轮已取消，未产生回复',
      detail: '在模型返回前被中断或取消，因此没有回复内容。',
    }
  }
  if (status === 'interrupted') {
    return {
      title: '该轮已中断，未产生回复',
      detail: '执行在中途被中断，因此没有产生完整回复，可在「轨迹」页签中查看中断前步骤。',
    }
  }
  if (status === 'error') {
    return {
      title: '该轮执行失败，未产生回复',
      detail: '可在「轨迹」页签中查看具体的错误信息。',
    }
  }
  if (status === 'awaiting_permission') {
    return {
      title: '该轮正在等待工具授权，尚未产生回复',
      detail: '模型请求工具授权后挂起等待确认，可在「轨迹」页签中查看待确认工具。',
    }
  }
  if (status === 'awaiting_external_execution') {
    return {
      title: '该轮正在等待外部执行结果，尚未产生回复',
      detail: '工具已交给外部环境执行，尚未收到回传结果。',
    }
  }
  if (status === 'awaiting_user') {
    return {
      title: '该轮正在等待用户交互，尚未产生回复',
      detail: '模型或子代理正在等待用户确认或授权，可在「轨迹」页签中查看详情。',
    }
  }
  if (status === 'rejected') {
    return {
      title: '该轮工具授权已被拒绝，未产生回复',
      detail: '用户拒绝了工具执行请求，因此未继续生成最终回复。',
    }
  }
  return {
    title: '该轮未产生回复',
    detail: '模型没有返回任何内容，可在「轨迹」页签中确认执行情况。',
  }
}

const hasAdvancedFilters = computed(() =>
  Boolean(
    filters.value.agent_id ||
      filters.value.username ||
      filters.value.start_date ||
      filters.value.end_date,
  ),
)

const hasActiveFilters = computed(() =>
  Object.values(filters.value).some((v) => v !== ''),
)

const fetchAgents = async () => {
  try {
    // 审计页要能筛选历史日志，因此连同已停用智能体一起取；
    // /agents/ 需要「智能体管理」菜单权限，仅有 menu:chat_logs 时会 403 导致筛选为空。
    const res = await agentApi.listAllowedAgents({ includeDisabled: true })
    agents.value = res.data
  } catch (e) {
    console.error('Failed to fetch agents', e)
  }
}

let logsRequestVersion = 0

const fetchLogs = async () => {
  // 与 loadContextCompactions / loadConversationTurns 保持同一套竞态保护：
  // 连续切换筛选或翻页时，先发的请求可能后到，不加版本号就会用旧结果覆盖新列表。
  const requestVersion = ++logsRequestVersion
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize.value,
      group_by_conversation: viewMode.value === 'conversation',
      ...filters.value,
    }
    Object.keys(params).forEach((key) => {
      if (params[key] === '') delete params[key]
    })

    const res = await historyApi.value(params)
    if (requestVersion !== logsRequestVersion) return
    logs.value = res.data.data.items || []
    total.value = res.data.data.total

    if (
      selectedId.value == null ||
      !logs.value.some((l) => l.id === selectedId.value)
    ) {
      selectedId.value = logs.value[0]?.id ?? null
    }
  } catch (e) {
    if (requestVersion !== logsRequestVersion) return
    console.error('Failed to fetch logs', e)
    showToast('获取聊天日志失败', 'error')
  } finally {
    if (requestVersion === logsRequestVersion) loading.value = false
  }
}

const changeViewMode = (mode: 'conversation' | 'turn') => {
  if (viewMode.value === mode) return
  viewMode.value = mode
  page.value = 1
  selectedId.value = null
  fetchLogs()
}

const applyQuickStatus = (status: string) => {
  if (filters.value.status === status) return
  filters.value.status = status
  applyFilters()
}

const clearKeyword = () => {
  if (!filters.value.keyword) return
  filters.value.keyword = ''
  applyFilters()
}

const resetFilters = () => {
  filters.value = {
    agent_id: '',
    username: '',
    keyword: '',
    status: '',
    start_date: '',
    end_date: '',
  }
  page.value = 1
  selectedId.value = null
  fetchLogs()
}

const applyFilters = () => {
  page.value = 1
  selectedId.value = null
  fetchLogs()
}

const selectLog = (log: AgentExecutionHistory) => {
  selectedId.value = log.id
}

let traceRequestVersion = 0

const loadTrace = async (traceId?: string) => {
  const requestVersion = ++traceRequestVersion
  if (!traceId) {
    traceDetail.value = null
    return
  }
  traceLoading.value = true
  traceDetail.value = null
  try {
    const res = await traceApi.value(traceId)
    // 快速连点列表项时，上一条的链路响应可能后到；丢了它，否则右侧会显示与左侧选中项
    // 不一致的链路（高亮 B、详情却是 A）。
    if (requestVersion !== traceRequestVersion) return
    traceDetail.value = res.data.data
  } catch (e) {
    if (requestVersion !== traceRequestVersion) return
    console.error('Failed to fetch trace', e)
    showToast('获取执行链路失败', 'error')
  } finally {
    if (requestVersion === traceRequestVersion) traceLoading.value = false
  }
}

const loadContextCompactions = async (conversationId?: string, force = false) => {
  const requestVersion = ++contextRequestVersion
  const id = conversationId?.trim() || ''
  if (!id) {
    contextCompactions.value = []
    contextLoadedFor.value = ''
    contextError.value = false
    contextLoading.value = false
    return
  }
  if (!force && contextLoadedFor.value === id && !contextError.value) return

  contextLoading.value = true
  contextError.value = false
  try {
    const res = await compactionsApi.value(id)
    if (requestVersion !== contextRequestVersion) return
    contextCompactions.value = res.data.data.records || []
    contextLoadedFor.value = id
  } catch (e) {
    if (requestVersion !== contextRequestVersion) return
    console.error('Failed to fetch context compactions', e)
    contextCompactions.value = []
    contextLoadedFor.value = ''
    contextError.value = true
  } finally {
    if (requestVersion === contextRequestVersion) contextLoading.value = false
  }
}

const formatDate = (dateStr: string) =>
  format(new Date(dateStr), 'yyyy-MM-dd HH:mm:ss', { locale: zhCN })

const formatListTime = (iso?: string) => {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '-'
  const diffMs = Date.now() - date.getTime()
  const dayMs = 24 * 60 * 60 * 1000
  if (diffMs >= 0 && diffMs < dayMs) {
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  }
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const getAgentName = (agentId: string) => {
  const agent = agents.value.find((a) => a.id === agentId)
  return agent ? agent.display_name : agentId
}

const copyText = async (text: string, label = '内容') => {
  if (!text) {
    showToast('内容为空', 'warning')
    return
  }
  const ok = await copyToClipboard(text)
  if (ok) {
    showToast(`${label}已复制`, 'success')
  } else {
    showToast('复制失败，请手动复制', 'error')
  }
}

const stepViewModes = ref<Record<string, 'json' | 'render'>>({})

const toggleStepViewMode = (key: string, mode: 'json' | 'render') => {
  stepViewModes.value[key] = mode
}

const getStepViewMode = (key: string, defaultMode: 'json' | 'render' = 'json') => {
  return stepViewModes.value[key] || defaultMode
}

const escapeHtml = (unsafe: unknown): string => {
  return String(unsafe ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

const parsePayloadObject = (value: unknown): { isObject: boolean; obj: any; textContent: string | null; hasMarkdown: boolean } => {
  if (value == null) return { isObject: false, obj: null, textContent: null, hasMarkdown: false }
  let target = value
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        target = JSON.parse(trimmed)
      } catch {
        // keep as string
      }
    }
  }

  if (typeof target === 'object' && target !== null) {
    const candidate = (target as any).content || (target as any).thought || (target as any).answer || (target as any).summary || (target as any).response || (target as any).query
    const textContent = typeof candidate === 'string' && candidate.length > 20 ? candidate : null
    const hasMarkdown = !!textContent && (textContent.includes('\n') || textContent.includes('#') || textContent.includes('*') || textContent.includes('`'))
    return { isObject: true, obj: target, textContent, hasMarkdown }
  }

  const text = String(target)
  const hasMarkdown = text.length > 20 && (text.includes('\n') || text.includes('#') || text.includes('*'))
  return { isObject: false, obj: target, textContent: hasMarkdown ? text : null, hasMarkdown }
}

const formatStepPayload = (value: unknown) => {
  if (value == null) return ''
  let obj = value
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        obj = JSON.parse(trimmed)
      } catch {
        return value
      }
    } else {
      return value
    }
  }
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(value)
  }
}

const highlightJsonHtml = (val: unknown): string => {
  if (val == null) return '<span class="text-slate-400">null</span>'
  let obj = val
  if (typeof val === 'string') {
    const trimmed = val.trim()
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        obj = JSON.parse(trimmed)
      } catch {
        return escapeHtml(val)
      }
    } else {
      return escapeHtml(val)
    }
  }

  const jsonStr = JSON.stringify(obj, null, 2)
  const escaped = escapeHtml(jsonStr)
  return escaped.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      let cls = 'text-amber-300' // number
      if (/^&quot;/.test(match) || /^"/.test(match)) {
        if (/:$/.test(match) || /&quot;:$/.test(match)) {
          cls = 'text-cyan-300 font-semibold' // key
        } else {
          cls = 'text-emerald-300' // string
        }
      } else if (/true|false/.test(match)) {
        cls = 'text-violet-300 font-semibold' // boolean
      } else if (/null/.test(match)) {
        cls = 'text-slate-500 italic' // null
      }
      return `<span class="${cls}">${match}</span>`
    },
  )
}

const renderPayloadMarkdown = (text: string) => {
  try {
    return renderMarkdown(text)
  } catch {
    return escapeHtml(text)
  }
}

/** 详情面板里 payload 的解析结果缓存。
 *
 * 每个 step 在模板中要多次用到解析结果（判空、hasMarkdown、两种渲染），而 Vue 模板里的
 * 函数调用**每次重渲染都会重新执行**：原先同一字段在一次渲染里被 parsePayloadObject
 * 解析 3~4 次，再各自套一次 JSON 高亮 / Markdown 渲染——一个 100+ 步、payload 几十 KB 的
 * 链路，切换页签、hover、resize 都会引发全量重算，卡顿明显。
 *
 * 这里按「原始值」缓存整份视图结果：字符串 payload 是不可变的，用 Map（限容防长会话吃内存）；
 * 对象 payload 走 WeakMap，元素被回收时缓存自动释放。
 */
type PayloadView = {
  isObject: boolean
  textContent: string | null
  hasMarkdown: boolean
  jsonHtml: string
  markdownHtml: string
}

const payloadObjectCache = new WeakMap<object, PayloadView>()
const payloadTextCache = new Map<string, PayloadView>()
const PAYLOAD_TEXT_CACHE_MAX = 500

const payloadView = (value: unknown): PayloadView => {
  const isObject = value !== null && typeof value === 'object'
  if (isObject) {
    const hit = payloadObjectCache.get(value as object)
    if (hit) return hit
  } else if (typeof value === 'string') {
    const hit = payloadTextCache.get(value)
    if (hit) return hit
  }

  const parsed = parsePayloadObject(value)
  const view: PayloadView = {
    isObject: parsed.isObject,
    textContent: parsed.textContent,
    hasMarkdown: parsed.hasMarkdown,
    jsonHtml: highlightJsonHtml(value),
    markdownHtml: parsed.textContent ? renderPayloadMarkdown(parsed.textContent) : '',
  }

  if (isObject) {
    payloadObjectCache.set(value as object, view)
  } else if (typeof value === 'string') {
    if (payloadTextCache.size >= PAYLOAD_TEXT_CACHE_MAX) payloadTextCache.clear()
    payloadTextCache.set(value, view)
  }
  return view
}

const getSubagentMeta = (step: { meta_info?: Record<string, unknown> | null }) =>
  normalizeSubagentTraceMeta(step.meta_info?.subagent)

const formatStepTime = (iso?: string) => {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

const fetchAllTurnsForExport = async (log: AgentExecutionHistory) => {
  const conversationId = log.conversation_id?.trim()
  if (!conversationId) {
    return [log]
  }
  const batchPageSize = 100
  let batchPage = 1
  const collected: AgentExecutionHistory[] = []
  let batchTotal = 0
  do {
    const res = await historyApi.value({
      conversation_id: conversationId,
      page: batchPage,
      page_size: batchPageSize,
    })
    const batch = res.data.data.items || []
    batchTotal = res.data.data.total ?? batch.length
    collected.push(...batch)
    if (collected.length >= batchTotal || batch.length < batchPageSize) break
    batchPage += 1
  } while (batchPage <= 50)

  collected.sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  )
  return collected.length ? collected : [log]
}

const fetchTracesForTurns = async (turns: AgentExecutionHistory[]) => {
  const tracesByTraceId: Record<string, ChatTraceDetail | undefined> = {}
  await Promise.all(
    turns.map(async (turn) => {
      if (!turn.trace_id) return
      try {
        const res = await traceApi.value(turn.trace_id)
        tracesByTraceId[turn.trace_id] = res.data.data as ChatTraceDetail
      } catch (e) {
        console.error('Failed to fetch trace for export', turn.trace_id, e)
        tracesByTraceId[turn.trace_id] = { trace_id: turn.trace_id, steps: [] }
      }
    }),
  )
  return tracesByTraceId
}

const exportSession = async (log: AgentExecutionHistory) => {
  if (!canExportLogs.value) {
    showToast('无导出权限', 'warning')
    return
  }
  if (exporting.value) return
  exporting.value = true
  try {
    const turns = await fetchAllTurnsForExport(log)
    const tracesByTraceId = await fetchTracesForTurns(turns)
    const agentLabel = getAgentName(log.agent_id)
    const markdown = buildChatSessionMarkdown(turns, tracesByTraceId, {
      agentLabel,
      username: log.username,
      conversationId: log.conversation_id,
      exportedAt: new Date(),
    })
    const filename = buildSessionExportFilename(turns, log.conversation_id)
    downloadMarkdownFile(filename, markdown)
    showToast(
      log.conversation_id
        ? `已导出会话（${turns.length} 轮）`
        : '已导出当前轮次',
      'success',
    )
  } catch (e) {
    console.error('Export session failed', e)
    showToast('导出失败', 'error')
  } finally {
    exporting.value = false
  }
}

/**
 * 为日志轮次附加从 process_timeline 快照重建的提问卡，并按后续轮的「用户回答」回执回填状态。
 *
 * 主动提问轮的 summary 为空（模型只提了问题、没有正文），若不重建，聊天日志
 * 只会显示「(无响应内容)」，看不到 AI 究竟问了什么、用户选了什么。
 */
const setConversationTurns = (turns: ConversationTurn[]) => {
  for (const turn of turns) {
    const states = userQuestionStatesFromTimeline(turn.process_timeline)
    if (states.length > 0) {
      turn.userQuestion = states[0]
    } else {
      // 修复前落库的轮次没有卡片快照，退化为回放问题文本
      const legacyText = userQuestionTextFromTimeline(turn.process_timeline)
      if (legacyText) turn.userQuestionText = legacyText
    }
  }
  const receipts = turns
    .map((turn) => parseUserQuestionReceipt(turn.query))
    .filter((receipt): receipt is UserQuestionReceipt => receipt !== null)
  applyUserQuestionReceipts(turns, receipts)
  conversationTurns.value = turns
}

const loadConversationTurns = async (log: AgentExecutionHistory | null) => {
  const reqVer = ++turnsRequestVersion
  if (!log) {
    conversationTurns.value = []
    selectedTraceId.value = null
    traceDetail.value = null
    return
  }

  // 单调用模式或无 conversation_id，直接作为单轮展示
  if (viewMode.value === 'turn' || !log.conversation_id?.trim()) {
    setConversationTurns([log])
    selectedTraceId.value = log.trace_id
    void loadTrace(log.trace_id)
    return
  }

  turnsLoading.value = true
  try {
    const res = await historyApi.value({
      conversation_id: log.conversation_id.trim(),
      page: 1,
      page_size: 100,
    })
    if (reqVer !== turnsRequestVersion) return
    const items = res.data.data.items || []
    turnsTotal.value = res.data.data.total ?? items.length
    if (items.length > 0) {
      items.sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      )
      setConversationTurns(items)
      // 默认聚焦最新的一轮
      const last = items[items.length - 1]
      if (last) {
        selectedTraceId.value = last.trace_id
        void loadTrace(last.trace_id)
      } else {
        selectedTraceId.value = log.trace_id
        void loadTrace(log.trace_id)
      }
    } else {
      setConversationTurns([log])
      selectedTraceId.value = log.trace_id
      void loadTrace(log.trace_id)
    }
  } catch (e) {
    if (reqVer !== turnsRequestVersion) return
    console.error('Failed to load conversation turns', e)
    setConversationTurns([log])
    selectedTraceId.value = log.trace_id
    void loadTrace(log.trace_id)
  } finally {
    if (reqVer === turnsRequestVersion) {
      turnsLoading.value = false
    }
  }
}

const scrollToTurn = (turn: AgentExecutionHistory, idx: number) => {
  if (turn.trace_id && selectedTraceId.value !== turn.trace_id) {
    selectedTraceId.value = turn.trace_id
    void loadTrace(turn.trace_id)
  }
  const el = document.getElementById(`chat-log-turn-${idx}`)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

const viewTurnTrace = (turn: AgentExecutionHistory) => {
  selectedTraceId.value = turn.trace_id
  activeDetailTab.value = 'trace'
  void loadTrace(turn.trace_id)
}

const switchTraceTurn = (turn: AgentExecutionHistory) => {
  if (selectedTraceId.value === turn.trace_id) return
  selectedTraceId.value = turn.trace_id
  void loadTrace(turn.trace_id)
}

watch(selectedLog, (log) => {
  replyViewMode.value = 'render'
  expandedAutoRuleTurns.value = {}
  expandedTraceSteps.value = {}
  void loadConversationTurns(log)
  contextCompactions.value = []
  contextLoadedFor.value = ''
  contextError.value = false
  if (activeDetailTab.value === 'context') {
    void loadContextCompactions(log?.conversation_id || undefined)
  }
})

watch(activeDetailTab, (tab) => {
  if (tab === 'context') {
    void loadContextCompactions(selectedLog.value?.conversation_id || undefined)
  }
})

watch([page, pageSize], () => {
  fetchLogs()
})

onMounted(() => {
  fetchAgents()
  fetchLogs()
})
</script>

<template>
  <!-- 高度用 h-full 而不是 calc(100vh-7.5rem)：<main> 自身没有内边距，硬编码 7.5rem
       会少算 Dashboard 头部的高度，整块内容因此贴不到底、下方露出一条空白。 -->
  <div class="flex flex-col h-full min-h-[560px] max-w-[1640px] mx-auto gap-2.5">
    <!-- 单行融合工具条：合并原双层页面头 + 独立搜索白卡，释放首屏阅读高度 -->
    <section class="shrink-0 bg-white rounded-xl border border-gray-200/80 shadow-2xs overflow-hidden">
      <div class="px-3 py-2 flex flex-wrap items-center gap-2.5">
        <div class="flex items-center gap-2 pr-2.5 border-r border-gray-200/80 shrink-0">
          <div class="w-7 h-7 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0">
            <ChatBubbleLeftRightIcon class="w-4 h-4" />
          </div>
          <h1 class="text-sm font-bold text-slate-900 tracking-tight">聊天日志</h1>
          <span class="px-2 py-0.5 rounded-full bg-slate-100 text-slate-500 text-[11px] font-semibold font-mono tabular-nums">
            {{ total }} {{ viewMode === 'conversation' ? '会话' : '条' }}
          </span>
        </div>

        <!-- 搜索框 -->
        <div class="relative flex-1 min-w-[200px] max-w-[440px]">
          <MagnifyingGlassIcon class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
          <input
            v-model="filters.keyword"
            type="text"
            data-search-no-clear
            placeholder="搜索问题、回复或智能体名称，回车查询…"
            class="w-full h-8 pl-8 pr-7 text-xs border border-gray-200 rounded-lg bg-slate-50/80 text-slate-800 placeholder:text-slate-400 focus:bg-white focus:ring-2 focus:ring-primary/15 focus:border-primary outline-none transition-all"
            @keyup.enter="applyFilters"
          />
          <button
            v-if="filters.keyword"
            type="button"
            class="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 rounded-full bg-slate-200/80 text-slate-500 hover:bg-slate-300 hover:text-slate-700 flex items-center justify-center"
            title="清空关键词"
            @click="clearKeyword"
          >
            <XMarkIcon class="w-3 h-3" />
          </button>
        </div>

        <!-- 快捷状态筛选（直接走后端 status 过滤，含已取消） -->
        <div
          class="inline-flex items-center p-0.5 rounded-lg bg-slate-100 border border-slate-200/70 shrink-0"
          role="group"
          aria-label="状态快捷筛选"
        >
          <button
            v-for="opt in quickStatusOptions"
            :key="opt.value || 'all'"
            type="button"
            class="px-2.5 py-1 rounded-md text-xs font-medium inline-flex items-center gap-1.5 transition-all whitespace-nowrap"
            :class="filters.status === opt.value
              ? 'bg-white text-slate-900 font-semibold shadow-2xs'
              : 'text-slate-500 hover:text-slate-800'"
            @click="applyQuickStatus(opt.value)"
          >
            <span v-if="opt.dotClass" class="w-1.5 h-1.5 rounded-full shrink-0" :class="opt.dotClass" />
            {{ opt.label }}
          </button>
        </div>

        <div class="ml-auto flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            class="h-8 px-2.5 inline-flex items-center gap-1.5 text-xs font-medium rounded-lg border transition-colors"
            :class="showFilters || hasAdvancedFilters
              ? 'border-primary/30 bg-primary/5 text-primary font-semibold'
              : 'border-gray-200 bg-white text-slate-600 hover:bg-slate-50'"
            @click="showFilters = !showFilters"
          >
            <FunnelIcon class="w-3.5 h-3.5" />
            高级筛选
            <span
              v-if="hasAdvancedFilters"
              class="w-1.5 h-1.5 rounded-full bg-primary"
            />
          </button>
          <button
            type="button"
            class="h-8 w-8 inline-flex items-center justify-center rounded-lg border border-gray-200 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-900 transition-colors"
            title="刷新列表"
            @click="fetchLogs"
          >
            <ArrowPathIcon class="w-3.5 h-3.5" :class="{ 'animate-spin': loading }" />
          </button>
        </div>
      </div>

      <!-- 折叠式高级筛选面板 -->
      <div v-show="showFilters" class="px-3 pb-3 pt-2.5 border-t border-gray-100 bg-slate-50/40">
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-2.5 items-end">
          <div class="space-y-1">
            <label class="text-[11px] font-semibold text-slate-500">智能体</label>
            <select
              v-model="filters.agent_id"
              class="w-full h-8 text-xs border border-gray-200 rounded-lg bg-white px-2 outline-none focus:ring-2 focus:ring-primary/15 focus:border-primary"
            >
              <option value="">全部智能体</option>
              <option v-for="a in agents" :key="a.id" :value="a.id">{{ a.display_name }}</option>
            </select>
          </div>
          <div v-if="isAdmin" class="space-y-1">
            <label class="text-[11px] font-semibold text-slate-500">用户</label>
            <input
              v-model="filters.username"
              type="text"
              placeholder="输入用户名"
              class="w-full h-8 text-xs border border-gray-200 rounded-lg bg-white px-2.5 outline-none focus:ring-2 focus:ring-primary/15 focus:border-primary"
              @keyup.enter="applyFilters"
            />
          </div>
          <div class="space-y-1">
            <label class="text-[11px] font-semibold text-slate-500">状态</label>
            <select
              v-model="filters.status"
              class="w-full h-8 text-xs border border-gray-200 rounded-lg bg-white px-2 outline-none focus:ring-2 focus:ring-primary/15 focus:border-primary"
            >
              <option value="">全部状态</option>
              <option value="success">成功</option>
              <option value="error">异常</option>
              <option value="cancelled">已取消</option>
              <option value="awaiting_user">待回答</option>
              <option value="awaiting_permission">待审批</option>
            </select>
          </div>
          <div class="space-y-1">
            <label class="text-[11px] font-semibold text-slate-500">开始日期</label>
            <input
              v-model="filters.start_date"
              type="date"
              class="w-full h-8 text-xs border border-gray-200 rounded-lg bg-white px-2 outline-none focus:ring-2 focus:ring-primary/15 focus:border-primary"
            />
          </div>
          <div class="space-y-1">
            <label class="text-[11px] font-semibold text-slate-500">结束日期</label>
            <input
              v-model="filters.end_date"
              type="date"
              class="w-full h-8 text-xs border border-gray-200 rounded-lg bg-white px-2 outline-none focus:ring-2 focus:ring-primary/15 focus:border-primary"
            />
          </div>
          <div class="flex items-center justify-end gap-1.5">
            <button
              v-if="hasActiveFilters"
              type="button"
              class="h-8 px-2.5 text-xs font-medium text-slate-500 hover:text-slate-800 rounded-lg border border-gray-200 bg-white hover:bg-slate-50"
              @click="resetFilters"
            >
              重置
            </button>
            <button
              type="button"
              class="h-8 px-3.5 bg-primary text-white text-xs font-semibold rounded-lg hover:bg-primary-dark transition-colors"
              @click="applyFilters"
            >
              应用筛选
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- 主分栏容器 -->
    <div class="flex-1 min-h-0 bg-white rounded-xl border border-gray-200/80 shadow-2xs overflow-hidden flex flex-col sm:flex-row">
      <!-- 左侧会话列表（支持折叠给宽表格/长回复留全宽） -->
      <aside
        v-show="!isListCollapsed"
        class="sm:w-[336px] sm:max-w-[38%] shrink-0 border-b sm:border-b-0 sm:border-r border-gray-200/80 bg-slate-50/50 flex flex-col min-h-0 max-h-[40%] sm:max-h-none"
      >
        <div class="px-3 py-2 border-b border-gray-100 flex items-center justify-between shrink-0 gap-2 bg-white">
          <div class="inline-flex p-0.5 rounded-lg bg-slate-100 border border-slate-200/60 text-[11px] font-medium">
            <button
              type="button"
              class="px-2.5 py-0.5 rounded-md transition-all"
              :class="viewMode === 'conversation'
                ? 'bg-white text-slate-900 shadow-2xs font-semibold'
                : 'text-slate-500 hover:text-slate-800'"
              @click="changeViewMode('conversation')"
            >
              按会话
            </button>
            <button
              type="button"
              class="px-2.5 py-0.5 rounded-md transition-all"
              :class="viewMode === 'turn'
                ? 'bg-white text-slate-900 shadow-2xs font-semibold'
                : 'text-slate-500 hover:text-slate-800'"
              @click="changeViewMode('turn')"
            >
              按调用
            </button>
          </div>
          <span class="font-mono text-[11px] text-slate-400 tabular-nums">
            共 {{ total }} {{ viewMode === 'conversation' ? '个会话' : '条' }}
          </span>
        </div>

        <div class="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-1.5 space-y-1">
          <div v-if="loading && logs.length === 0" class="py-16 text-center text-xs text-slate-400">
            加载中...
          </div>
          <div v-else-if="!loading && logs.length === 0" class="py-16 px-6 text-center">
            <ChatBubbleLeftRightIcon class="w-9 h-9 text-slate-200 mx-auto mb-2.5" />
            <p class="text-xs font-semibold text-slate-500">暂无聊天记录</p>
            <p class="text-[11px] text-slate-400 mt-1">试试切换状态或调整筛选条件</p>
          </div>
          <div
            v-for="log in logs"
            :key="log.id"
            role="button"
            tabindex="0"
            class="group relative w-full text-left px-3 py-2.5 rounded-lg border transition-all cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
            :class="selectedId === log.id
              ? 'bg-white border-primary/35 shadow-xs'
              : 'bg-transparent border-transparent hover:bg-white hover:border-gray-200/70'"
            @click="selectLog(log)"
            @keyup.enter="selectLog(log)"
          >
            <!-- 选中态左侧 3px 主色指示条 -->
            <span
              class="absolute left-0 top-2 bottom-2 w-[3px] rounded-r-full transition-colors"
              :class="selectedId === log.id ? 'bg-primary' : 'bg-transparent'"
            />

            <div class="flex items-center justify-between gap-2 mb-1">
              <div class="flex items-center gap-1.5 min-w-0 text-[11px]">
                <template v-if="isAdmin">
                  <span class="font-semibold text-slate-700 truncate max-w-[84px]">
                    {{ log.username || '匿名' }}
                  </span>
                  <span class="text-slate-300">·</span>
                </template>
                <span class="text-primary font-medium truncate">
                  {{ getAgentName(log.agent_id) }}
                </span>
              </div>
              <span class="text-[10.5px] font-mono text-slate-400 whitespace-nowrap shrink-0 tabular-nums">
                {{ formatListTime(log.created_at) }}
              </span>
            </div>

            <p
              class="text-[13px] leading-snug line-clamp-2 break-words"
              :class="selectedId === log.id ? 'text-slate-900 font-semibold' : 'text-slate-700'"
            >
              <span
                v-if="parseChatLogQuery(log.query).isAutomation"
                class="inline-flex items-center px-1.5 py-0 mr-1 rounded bg-sky-50 text-sky-700 border border-sky-200/80 text-[10px] font-semibold align-[1px]"
              >
                定时任务{{ parseChatLogQuery(log.query).taskId ? ` #${parseChatLogQuery(log.query).taskId}` : '' }}
              </span>
              {{ compactSingleLine(parseChatLogQuery(log.query).displayText, 76) }}
            </p>

            <div class="mt-1.5 flex items-center justify-between gap-1.5">
              <div class="flex items-center gap-2 min-w-0 text-[11px]">
                <span
                  class="inline-flex items-center gap-1 font-semibold"
                  :class="getChatLogStatusMeta(log.status).textClass"
                >
                  <span
                    class="w-1.5 h-1.5 rounded-full shrink-0"
                    :class="getChatLogStatusMeta(log.status).dotClass"
                  />
                  {{ getChatLogStatusMeta(log.status).label }}
                </span>
                <span
                  v-if="log.agent_version"
                  class="font-mono text-[10.5px] text-slate-400"
                >
                  {{ log.agent_version }}
                </span>
                <span
                  v-if="viewMode === 'conversation' && log.turn_count"
                  class="px-1.5 py-0.5 rounded text-[10px] font-semibold leading-none tabular-nums"
                  :class="selectedId === log.id ? 'bg-primary/10 text-primary' : 'bg-slate-200/70 text-slate-600'"
                >
                  {{ log.turn_count }} 轮
                </span>
                <span
                  v-else-if="log.execution_time_ms"
                  class="text-[10.5px] font-mono text-slate-400 tabular-nums"
                >
                  {{ formatDurationMs(log.execution_time_ms) }}
                </span>
              </div>
              <button
                v-if="canExportLogs"
                type="button"
                class="shrink-0 p-1 rounded-md text-slate-400 hover:text-primary hover:bg-primary/10 transition-all disabled:opacity-40"
                :class="selectedId === log.id ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'"
                :disabled="exporting"
                title="导出会话为 Markdown"
                @click.stop="exportSession(log)"
              >
                <ArrowDownTrayIcon class="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>

        <!-- 底部分页 -->
        <div class="shrink-0 px-2.5 py-2 border-t border-gray-100 bg-white flex items-center justify-between gap-2">
          <div class="flex items-center gap-1.5 min-w-0">
            <span class="text-[10.5px] text-slate-400 font-mono truncate tabular-nums">
              {{ total === 0 ? '0' : `${(page - 1) * pageSize + 1}-${Math.min(page * pageSize, total)}` }} / {{ total }}
            </span>
            <!-- 会话量常达数百条，15 条/页要翻几十页。换页大小后必须回到第 1 页：watch 只负责
                 重新拉取，不会改页码，否则会停在一个很深的页码上，看起来像"跳过了数据"。 -->
            <select
              v-model.number="pageSize"
              class="text-[10.5px] py-0.5 pl-1 pr-0.5 border border-gray-200 rounded-md bg-white text-slate-500 hover:border-gray-300 focus:outline-none focus:ring-1 focus:ring-primary/30"
              title="每页显示条数"
              @change="page = 1"
            >
              <option :value="15">15/页</option>
              <option :value="30">30/页</option>
              <option :value="50">50/页</option>
            </select>
          </div>
          <div class="flex items-center gap-1">
            <button
              type="button"
              class="p-1 border border-gray-200 rounded-md text-slate-600 hover:bg-slate-50 disabled:opacity-30"
              :disabled="page === 1 || loading"
              @click="page--"
            >
              <ChevronLeftIcon class="w-3.5 h-3.5" />
            </button>
            <input
              v-model="pageInput"
              type="text"
              inputmode="numeric"
              :title="`共 ${totalPages} 页，输入页码后回车跳转`"
              class="w-10 text-center text-[11px] font-semibold text-slate-700 px-1 py-0.5 border border-gray-200 rounded-md bg-white focus:outline-none focus:ring-1 focus:ring-primary/30 focus:border-primary/40 tabular-nums"
              @keyup.enter="applyPageInput"
              @blur="applyPageInput"
            />
            <span class="text-[10.5px] text-slate-400 tabular-nums">/ {{ totalPages }}</span>
            <button
              type="button"
              class="p-1 border border-gray-200 rounded-md text-slate-600 hover:bg-slate-50 disabled:opacity-30"
              :disabled="page * pageSize >= total || loading"
              @click="page++"
            >
              <ChevronRightIcon class="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </aside>

      <!-- 右侧详情工作区 -->
      <section class="flex-1 min-w-0 min-h-0 flex flex-col bg-white">
        <template v-if="selectedLog">
          <!-- 精炼双层头部：1. 会话主题 + 状态 + 操作区；2. Tab 导航 + 轻量化元信息条 -->
          <div class="shrink-0 px-5 pt-2.5 border-b border-gray-200/80 bg-white">
            <div class="flex items-center justify-between gap-3 pb-2">
              <div class="flex items-center gap-2 min-w-0 flex-1">
                <button
                  type="button"
                  class="hidden sm:inline-flex items-center justify-center w-7 h-7 rounded-lg border transition-colors shrink-0"
                  :class="isListCollapsed
                    ? 'border-primary/30 bg-primary/5 text-primary'
                    : 'border-gray-200 text-slate-500 hover:bg-slate-50 hover:text-slate-800'"
                  :title="isListCollapsed ? '展开左侧会话列表' : '折叠左侧会话列表，宽屏阅读'"
                  @click="isListCollapsed = !isListCollapsed"
                >
                  <Bars3BottomLeftIcon class="w-4 h-4" />
                </button>
                <span
                  v-if="selectedSessionParsedQuery.isAutomation"
                  class="shrink-0 inline-flex items-center px-1.5 py-0.5 rounded bg-sky-50 text-sky-700 border border-sky-200/80 text-[10.5px] font-semibold"
                >
                  定时任务{{ selectedSessionParsedQuery.taskId ? ` #${selectedSessionParsedQuery.taskId}` : '' }}
                </span>
                <h2
                  class="text-[14.5px] font-bold text-slate-900 truncate tracking-tight"
                  :title="selectedSessionParsedQuery.displayText"
                >
                  {{ compactSingleLine(selectedSessionParsedQuery.displayText, 96) }}
                </h2>
                <span
                  class="shrink-0 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-semibold border"
                  :class="selectedStatusMeta.pillClass"
                >
                  <span class="w-1.5 h-1.5 rounded-full" :class="selectedStatusMeta.dotClass" />
                  {{ selectedStatusMeta.label }}
                </span>
              </div>

              <div class="flex items-center gap-1.5 shrink-0">
                <button
                  v-if="activeTurn?.trace_id || selectedLog.trace_id"
                  type="button"
                  class="h-7 px-2 inline-flex items-center gap-1.5 rounded-md bg-slate-50 border border-gray-200 text-[11px] font-mono text-slate-500 hover:text-primary hover:border-primary/30 hover:bg-primary/5 transition-colors"
                  :title="`点击复制完整 Trace ID：${activeTurn?.trace_id || selectedLog.trace_id}`"
                  @click="copyText((activeTurn?.trace_id || selectedLog.trace_id)!, 'Trace ID')"
                >
                  <span class="font-sans text-[10.5px] text-slate-400">Trace</span>
                  <span>{{ (activeTurn?.trace_id || selectedLog.trace_id)!.slice(0, 8) }}</span>
                  <DocumentDuplicateIcon class="w-3 h-3 opacity-70" />
                </button>
                <button
                  v-if="canExportLogs"
                  type="button"
                  class="h-7 px-2.5 inline-flex items-center gap-1.5 text-xs font-semibold rounded-md border border-gray-200 bg-white text-slate-600 hover:text-primary hover:border-primary/30 hover:bg-primary/5 transition-colors disabled:opacity-40"
                  :disabled="exporting"
                  @click="exportSession(selectedLog)"
                >
                  <ArrowDownTrayIcon class="w-3.5 h-3.5" />
                  {{ exporting ? '导出中…' : '导出 Markdown' }}
                </button>
              </div>
            </div>

            <!-- 第二行：Tab 与轻量元信息同行对齐 -->
            <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
              <div class="flex items-center gap-5">
                <button
                  type="button"
                  class="py-2 text-xs font-semibold border-b-2 transition-all flex items-center gap-1.5"
                  :class="activeDetailTab === 'conversation'
                    ? 'border-primary text-primary'
                    : 'border-transparent text-slate-500 hover:text-slate-900'"
                  @click="activeDetailTab = 'conversation'"
                >
                  <ChatBubbleLeftRightIcon class="w-3.5 h-3.5" />
                  对话
                  <span
                    v-if="conversationTurns.length"
                    class="px-1.5 py-0.5 rounded-full text-[10px] font-mono leading-none tabular-nums"
                    :class="activeDetailTab === 'conversation' ? 'bg-primary/10 text-primary' : 'bg-slate-100 text-slate-500'"
                  >
                    {{ conversationTurns.length }}
                  </span>
                </button>
                <button
                  type="button"
                  class="py-2 text-xs font-semibold border-b-2 transition-all flex items-center gap-1.5"
                  :class="activeDetailTab === 'trace'
                    ? 'border-primary text-primary'
                    : 'border-transparent text-slate-500 hover:text-slate-900'"
                  @click="activeDetailTab = 'trace'"
                >
                  <ClockIcon class="w-3.5 h-3.5" />
                  轨迹
                  <span
                    v-if="traceDetail?.steps?.length"
                    class="px-1.5 py-0.5 rounded-full text-[10px] font-mono leading-none tabular-nums"
                    :class="activeDetailTab === 'trace' ? 'bg-primary/10 text-primary' : 'bg-slate-100 text-slate-500'"
                  >
                    {{ traceDetail.steps.length }}
                  </span>
                </button>
                <button
                  type="button"
                  class="py-2 text-xs font-semibold border-b-2 transition-all flex items-center gap-1.5"
                  :class="activeDetailTab === 'context'
                    ? 'border-primary text-primary'
                    : 'border-transparent text-slate-500 hover:text-slate-900'"
                  @click="activeDetailTab = 'context'"
                >
                  <ExclamationCircleIcon class="w-3.5 h-3.5" />
                  上下文
                  <span
                    v-if="contextCompactions.length"
                    class="px-1.5 py-0.5 rounded-full text-[10px] font-mono leading-none tabular-nums"
                    :class="activeDetailTab === 'context' ? 'bg-primary/10 text-primary' : 'bg-slate-100 text-slate-500'"
                  >
                    {{ contextCompactions.length }}
                  </span>
                </button>
              </div>

              <!-- 轻量化元信息条 + 当前 Tab 刷新操作 -->
              <div class="flex items-center gap-2 pb-1.5 text-[11px] text-slate-500 overflow-x-auto no-scrollbar max-w-full">
                <span class="font-semibold text-primary whitespace-nowrap">
                  {{ getAgentName(activeTurn?.agent_id || selectedLog.agent_id) }}
                </span>
                <template v-if="isAdmin">
                  <span class="text-slate-300">·</span>
                  <span class="font-medium text-slate-700 whitespace-nowrap">
                    {{ activeTurn?.username || selectedLog.username || '匿名' }}
                  </span>
                </template>
                <template v-if="activeTurn?.agent_version || selectedLog.agent_version">
                  <span class="text-slate-300">·</span>
                  <span class="font-mono whitespace-nowrap">{{ activeTurn?.agent_version || selectedLog.agent_version }}</span>
                </template>
                <template v-if="activeTurn?.model_id || selectedLog.model_id">
                  <span class="text-slate-300">·</span>
                  <span
                    class="font-mono truncate max-w-[150px]"
                    :title="activeTurn?.model_id || selectedLog.model_id"
                  >
                    {{ activeTurn?.model_id || selectedLog.model_id }}
                  </span>
                </template>
                <span class="text-slate-300">·</span>
                <span class="font-mono tabular-nums whitespace-nowrap">
                  {{ formatDurationMs(activeTurn?.execution_time_ms ?? selectedLog.execution_time_ms) }}
                </span>
                <span class="text-slate-300">·</span>
                <span class="font-mono text-slate-400 tabular-nums whitespace-nowrap">
                  {{ formatDate(activeTurn?.created_at || selectedLog.created_at) }}
                </span>
                <template v-if="activeDetailTab === 'trace' && (selectedTraceId || selectedLog.trace_id)">
                  <span class="text-slate-300">|</span>
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 text-[11px] text-slate-500 hover:text-primary transition-colors whitespace-nowrap"
                    :disabled="traceLoading"
                    title="重新加载链路"
                    @click="loadTrace(selectedTraceId || selectedLog.trace_id)"
                  >
                    <ArrowPathIcon class="w-3 h-3" :class="{ 'animate-spin': traceLoading }" />
                    刷新链路
                  </button>
                </template>
                <template v-else-if="activeDetailTab === 'context'">
                  <span class="text-slate-300">|</span>
                  <button
                    type="button"
                    class="inline-flex items-center gap-1 text-[11px] text-slate-500 hover:text-primary transition-colors whitespace-nowrap"
                    :disabled="contextLoading"
                    title="重新加载上下文压缩记录"
                    @click="loadContextCompactions(selectedLog.conversation_id || undefined, true)"
                  >
                    <ArrowPathIcon class="w-3 h-3" :class="{ 'animate-spin': contextLoading }" />
                    刷新上下文
                  </button>
                </template>
              </div>
            </div>
          </div>

          <!-- 多轮快捷定位条（对话 Tab 用于锚点直达；轨迹 Tab 用于切换轮次 + 全部展开/收起） -->
          <div
            v-if="
              (activeDetailTab === 'conversation' && conversationTurns.length > 1) ||
              (activeDetailTab === 'trace' && (conversationTurns.length > 1 || traceSteps.length > 0))
            "
            class="shrink-0 px-5 py-2 border-b border-gray-100 bg-slate-50/70 flex items-center gap-2.5 overflow-x-auto no-scrollbar"
          >
            <template v-if="conversationTurns.length > 1">
              <span class="text-[11px] font-semibold text-slate-400 shrink-0">
                {{ activeDetailTab === 'trace' ? '切换轮次' : '轮次定位' }}
              </span>
              <div class="flex items-center gap-1.5 flex-1 min-w-0 overflow-x-auto no-scrollbar">
                <button
                  v-for="(turn, idx) in conversationTurns"
                  :key="turn.id || turn.trace_id"
                  type="button"
                  class="px-2 py-1 rounded-md text-[11px] font-semibold border transition-all inline-flex items-center gap-1.5 shrink-0"
                  :class="
                    selectedTraceId === turn.trace_id
                      ? turn.status === 'error'
                        ? 'bg-red-50 border-red-400 text-red-700'
                        : 'bg-primary/10 border-primary text-primary'
                      : turn.status === 'error'
                        ? 'bg-red-50/60 border-red-200 text-red-600 hover:bg-red-50'
                        : 'bg-white border-gray-200 text-slate-600 hover:border-gray-300 hover:text-slate-900'
                  "
                  @click="activeDetailTab === 'trace' ? switchTraceTurn(turn) : scrollToTurn(turn, Number(idx))"
                >
                  <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="getChatLogStatusMeta(turn.status).dotClass" />
                  <span>第 {{ Number(idx) + 1 }} 轮</span>
                  <span
                    v-if="turn.execution_time_ms"
                    class="text-[10px] font-mono opacity-70 tabular-nums"
                  >
                    {{ formatDurationMs(turn.execution_time_ms) }}
                  </span>
                </button>
              </div>
            </template>

            <div
              v-if="activeDetailTab === 'trace' && traceSteps.length > 0"
              class="ml-auto flex items-center gap-1.5 shrink-0"
            >
              <button
                type="button"
                class="px-2 py-1 rounded-md text-[11px] font-medium bg-white border border-gray-200 text-slate-600 hover:text-slate-900 hover:border-gray-300 transition-colors"
                @click="setAllTraceStepsExpanded(true)"
              >
                全部展开
              </button>
              <button
                type="button"
                class="px-2 py-1 rounded-md text-[11px] font-medium bg-white border border-gray-200 text-slate-600 hover:text-slate-900 hover:border-gray-300 transition-colors"
                @click="setAllTraceStepsExpanded(false)"
              >
                全部收起
              </button>
            </div>
          </div>

          <!-- Tab 1: 对话流（去套娃宽幅文档流排版） -->
          <div
            v-show="activeDetailTab === 'conversation'"
            class="flex-1 overflow-y-auto custom-scrollbar min-h-0 px-6 py-5"
          >
            <div class="max-w-[920px] mx-auto space-y-6">
              <!-- 详情一次只取最近 100 轮（后端 page_size 上限即 100），超出的会话必须明说，
                   否则用户会以为这就是全部记录。 -->
              <div
                v-if="turnsTotal > conversationTurns.length"
                class="flex items-start gap-2 rounded-lg border border-amber-200/80 bg-amber-50/70 px-3 py-2 text-amber-800"
              >
                <ExclamationCircleIcon class="w-4 h-4 shrink-0 mt-0.5" />
                <p class="text-xs leading-relaxed">
                  本会话共 {{ turnsTotal }} 轮，此处仅显示最近 {{ conversationTurns.length }} 轮。
                </p>
              </div>

              <div v-if="turnsLoading && conversationTurns.length === 0" class="py-20 text-center text-sm text-slate-400">
                <ArrowPathIcon class="w-6 h-6 animate-spin mx-auto mb-2 text-primary" />
                正在加载对话记录...
              </div>

              <article
                v-for="(turn, index) in conversationTurns"
                :id="`chat-log-turn-${index}`"
                :key="turn.id || turn.trace_id"
                class="scroll-mt-4 pb-7 last:pb-2 transition-colors"
                :class="selectedTraceId === turn.trace_id && conversationTurns.length > 1
                  ? 'rounded-xl bg-primary/[0.02] border border-primary/25 p-4 -mx-4'
                  : ''"
              >
                <!-- 轮次分隔带：居中细线 + 轮次号 + 时间 + 状态 + 耗时。
                     左右分栏后「第 N 轮」不再独占一行，而是压成对话流里的分隔标记——
                     扫读时先认出这是新的一问一答，再看是谁在说、说了什么。 -->
                <div class="flex items-center gap-3 mb-4">
                  <span class="h-px flex-1 bg-gray-100" aria-hidden="true"></span>
                  <div class="flex items-center gap-2 text-[11px] whitespace-nowrap">
                    <span
                      class="px-2 py-0.5 rounded-full font-bold text-[11px] tabular-nums border"
                      :class="selectedTraceId === turn.trace_id && conversationTurns.length > 1
                        ? 'bg-primary border-primary text-white'
                        : 'bg-slate-50 border-gray-200 text-slate-600'"
                    >
                      第 {{ Number(index) + 1 }} 轮
                    </span>
                    <span class="font-mono text-slate-400 tabular-nums">
                      {{ formatDate(turn.created_at) }}
                    </span>
                    <span
                      class="inline-flex items-center gap-1 font-semibold"
                      :class="getChatLogStatusMeta(turn.status).textClass"
                    >
                      <span
                        class="w-1.5 h-1.5 rounded-full"
                        :class="getChatLogStatusMeta(turn.status).dotClass"
                      />
                      {{ getChatLogStatusMeta(turn.status).label }}
                    </span>
                    <span
                      v-if="turn.execution_time_ms"
                      class="font-mono text-slate-400 tabular-nums"
                    >
                      {{ formatDurationMs(turn.execution_time_ms) }}
                    </span>
                  </div>
                  <span class="h-px flex-1 bg-gray-100" aria-hidden="true"></span>
                </div>

                <!-- 用户消息：右侧主色气泡。限宽 74%——长问题撑满整行会把对话节奏压平 -->
                <div class="flex flex-row-reverse items-start gap-2.5 mb-3.5">
                  <span
                    class="w-7 h-7 rounded-lg bg-slate-200 text-slate-700 flex items-center justify-center text-[11px] font-bold shrink-0"
                  >
                    {{ turn.username ? turn.username.charAt(0).toUpperCase() : 'U' }}
                  </span>
                  <div class="flex flex-col items-end min-w-0 max-w-[74%]">
                    <div class="flex items-center gap-2 mb-1 text-[11px] text-slate-400 max-w-full">
                      <span
                        v-if="parseChatLogQuery(turn.query).isAutomation"
                        class="inline-flex items-center px-1.5 py-0.5 rounded bg-sky-50 text-sky-700 border border-sky-200/80 text-[10px] font-semibold shrink-0"
                      >
                        定时任务{{ parseChatLogQuery(turn.query).taskId ? ` #${parseChatLogQuery(turn.query).taskId}` : '' }}
                      </span>
                      <button
                        v-if="parseChatLogQuery(turn.query).systemPrefix"
                        type="button"
                        class="shrink-0 font-medium hover:text-primary transition-colors"
                        @click="toggleAutoRuleExpanded(turn.id)"
                      >
                        {{ isAutoRuleExpanded(turn.id) ? '收起系统规则' : '查看系统规则' }}
                      </button>
                      <span class="font-semibold text-slate-500 truncate">
                        {{ turn.username || '用户' }} 提问
                      </span>
                    </div>
                    <div
                      class="rounded-2xl rounded-tr-sm bg-primary text-white px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap break-words shadow-sm"
                    >
                      {{
                        parseChatLogQuery(turn.query).systemPrefix && !isAutoRuleExpanded(turn.id)
                          ? parseChatLogQuery(turn.query).displayText
                          : (turn.query || '(空)')
                      }}
                    </div>
                    <button
                      type="button"
                      class="mt-1 text-[11px] text-slate-400 hover:text-primary font-medium transition-colors"
                      @click="copyText(turn.query || '', '用户提问')"
                    >
                      复制
                    </button>
                  </div>
                </div>

                <!-- AI 回复：左侧白底文档流。正文含表格 / 代码 / 图表，所以不按气泡限宽 -->
                <div class="flex items-start gap-2.5 group/reply">
                  <span
                    class="w-7 h-7 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0"
                  >
                    <SparklesIcon class="w-3.5 h-3.5" />
                  </span>
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 mb-1.5 flex-wrap text-[11px] text-slate-400">
                      <span class="text-xs font-bold text-slate-700">AI 回复</span>
                      <!-- 回复归属：同一会话常在主助手 / 专家 / 子代理之间切换，只看「AI 回复」
                           四个字分不清是谁答的，非要点进轨迹才发现。 -->
                      <span
                        v-if="turn.agent_display_name || turn.agent_id"
                        class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-violet-50 text-violet-700 border border-violet-200/70 text-[10.5px] font-semibold max-w-[260px]"
                        :title="`本轮由「${turn.agent_display_name || getAgentName(turn.agent_id)}」回复`"
                      >
                        <span class="truncate">{{ turn.agent_display_name || getAgentName(turn.agent_id) }}</span>
                        <span v-if="turn.agent_version" class="opacity-70 shrink-0">{{ turn.agent_version }}</span>
                      </span>
                      <button
                        v-if="countTurnToolSteps(turn.process_timeline) > 0 && turn.trace_id"
                        type="button"
                        class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-50 border border-gray-200 text-slate-500 hover:text-primary hover:border-primary/30 hover:bg-primary/5 transition-colors"
                        title="点击查看此轮详细执行步骤"
                        @click="viewTurnTrace(turn)"
                      >
                        <WrenchScrewdriverIcon class="w-3 h-3" />
                        执行 {{ countTurnToolSteps(turn.process_timeline) }} 步
                      </button>
                      <div
                        v-if="turn.summary"
                        class="inline-flex rounded-md bg-slate-100 p-0.5 text-[11px]"
                      >
                        <button
                          type="button"
                          class="px-2 py-0.5 rounded transition-all"
                          :class="getTurnReplyMode(turn.id) === 'render'
                            ? 'bg-white text-slate-900 font-semibold shadow-2xs'
                            : 'text-slate-500 hover:text-slate-800'"
                          @click="setTurnReplyMode(turn.id, 'render')"
                        >
                          渲染
                        </button>
                        <button
                          type="button"
                          class="px-2 py-0.5 rounded transition-all"
                          :class="getTurnReplyMode(turn.id) === 'source'
                            ? 'bg-white text-slate-900 font-semibold shadow-2xs'
                            : 'text-slate-500 hover:text-slate-800'"
                          @click="setTurnReplyMode(turn.id, 'source')"
                        >
                          源码
                        </button>
                      </div>
                      <div class="ml-auto flex items-center gap-2.5 shrink-0">
                        <span
                          v-if="turn.model_id"
                          class="font-mono text-slate-400 hidden md:inline"
                        >
                          {{ turn.model_id }}
                        </span>
                        <button
                          v-if="turn.trace_id"
                          type="button"
                          class="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-primary text-[11px] font-semibold hover:bg-primary hover:text-white transition-colors"
                          @click="viewTurnTrace(turn)"
                        >
                          <ClockIcon class="w-3.5 h-3.5" />
                          查看此轮轨迹
                        </button>
                        <button
                          type="button"
                          class="text-[11px] text-slate-400 hover:text-primary font-medium transition-colors opacity-75 group-hover/reply:opacity-100"
                          @click="copyText(turn.summary || turn.userQuestion?.question || turn.userQuestionText || '', '回复内容')"
                        >
                          复制
                        </button>
                      </div>
                    </div>

                    <!-- 气泡体：白底圆角，左上角收窄指向 AI 头像 -->
                    <div
                      class="rounded-2xl rounded-tl-sm border border-gray-200/80 bg-white px-3.5 py-3 shadow-sm"
                    >
                      <div
                        v-if="!turn.summary && !turn.userQuestion && !turn.userQuestionText"
                        class="flex items-start gap-2.5 rounded-lg border border-dashed px-3 py-2.5"
                        :class="turn.status === 'error'
                          ? 'border-red-200 bg-red-50/60 text-red-700'
                          : 'border-gray-200 bg-slate-50/70 text-slate-600'"
                      >
                        <ExclamationCircleIcon class="w-4 h-4 shrink-0 mt-0.5 opacity-80" />
                        <div>
                          <p class="text-xs font-semibold">{{ emptyReplyView(turn).title }}</p>
                          <p class="text-[11px] opacity-80 mt-0.5">{{ emptyReplyView(turn).detail }}</p>
                        </div>
                      </div>

                      <!-- 主动提问轮没有正文（模型只提了问题），回放提问卡，否则日志里只剩「(无响应内容)」 -->
                      <UserQuestionCard
                        v-if="turn.userQuestion"
                        :payload="turn.userQuestion"
                        disabled
                        class="mb-2"
                      />
                      <!-- 修复前落库的轮次没有卡片快照，退化为只回放问题文本 -->
                      <div
                        v-else-if="turn.userQuestionText"
                        class="mb-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs leading-relaxed text-primary-dark"
                      >
                        <span class="font-semibold">AI 提问：</span>{{ turn.userQuestionText }}
                      </div>

                      <div
                        v-if="turn.summary && getTurnReplyMode(turn.id) === 'render'"
                        class="message-host chat-log-message-host max-w-none text-slate-800 break-words"
                      >
                        <MessageRenderer
                          :content="turn.summary"
                          theme="default"
                          hide-quick-buttons
                          readonly
                        />
                      </div>
                      <pre
                        v-else-if="turn.summary"
                        class="text-xs text-slate-700 whitespace-pre-wrap break-words leading-relaxed font-mono bg-slate-50 border border-gray-200 rounded-lg p-3 overflow-x-auto"
                      >{{ turn.summary }}</pre>
                    </div>
                  </div>
                </div>
              </article>
            </div>
          </div>

          <!-- Tab 2: 轨迹内容（总览卡 + 耗时占比条 + 可折叠步骤卡片） -->
          <div
            v-show="activeDetailTab === 'trace'"
            class="flex-1 overflow-y-auto custom-scrollbar min-h-0 px-6 py-5"
          >
            <div v-if="traceLoading" class="py-20 text-center text-sm text-slate-400">
              <ArrowPathIcon class="w-6 h-6 animate-spin mx-auto mb-2 text-primary" />
              正在加载执行链路...
            </div>
            <div
              v-else-if="!traceSteps.length"
              class="py-16 text-center text-sm text-slate-400"
            >
              <ClockIcon class="w-8 h-8 text-slate-200 mx-auto mb-2" />
              暂无执行链路数据
            </div>
            <div v-else class="max-w-[940px] mx-auto space-y-4 pb-4">
              <!-- 单轮执行概览条 -->
              <div class="rounded-xl border border-gray-200/80 bg-slate-50/80 px-4 py-3 flex flex-wrap items-center justify-between gap-3">
                <div class="flex flex-wrap items-center gap-5 text-xs">
                  <div>
                    <div class="text-[10.5px] text-slate-400">当前轮次</div>
                    <div class="font-bold text-slate-900 mt-0.5 tabular-nums">
                      {{ activeTurnIndex >= 0 ? `第 ${activeTurnIndex + 1} 轮` : '单次调用' }}
                    </div>
                  </div>
                  <div>
                    <div class="text-[10.5px] text-slate-400">状态</div>
                    <div
                      class="font-semibold mt-0.5 inline-flex items-center gap-1"
                      :class="getChatLogStatusMeta(activeTurn?.status || selectedLog.status).textClass"
                    >
                      <span
                        class="w-1.5 h-1.5 rounded-full"
                        :class="getChatLogStatusMeta(activeTurn?.status || selectedLog.status).dotClass"
                      />
                      {{ getChatLogStatusMeta(activeTurn?.status || selectedLog.status).label }}
                    </div>
                  </div>
                  <div>
                    <div class="text-[10.5px] text-slate-400">总耗时</div>
                    <div class="font-mono font-bold text-slate-900 mt-0.5 tabular-nums">
                      {{ formatDurationMs(activeTurn?.execution_time_ms ?? traceTotalStepMs) }}
                    </div>
                  </div>
                  <div>
                    <div class="text-[10.5px] text-slate-400">步骤 / 工具调用</div>
                    <div class="font-semibold text-slate-900 mt-0.5 tabular-nums">
                      {{ traceSteps.length }} 步 / {{ traceToolCallCount }} 次工具
                    </div>
                  </div>
                </div>
                <button
                  v-if="selectedTraceId || selectedLog.trace_id"
                  type="button"
                  class="px-2.5 py-1 rounded-lg bg-white border border-gray-200 text-[11px] font-mono text-slate-500 hover:text-primary hover:border-primary/30 transition-colors inline-flex items-center gap-1.5"
                  :title="selectedTraceId || selectedLog.trace_id"
                  @click="copyText((selectedTraceId || selectedLog.trace_id)!, 'Trace ID')"
                >
                  <span>{{ (selectedTraceId || selectedLog.trace_id)!.slice(0, 14) }}…</span>
                  <DocumentDuplicateIcon class="w-3.5 h-3.5" />
                </button>
              </div>

              <!-- 可折叠轨迹步骤列表 -->
              <div class="space-y-2">
                <div
                  v-for="(step, idx) in traceSteps"
                  :key="idx"
                  class="rounded-xl border bg-white overflow-hidden transition-colors"
                  :class="step.status !== 'success' || step.error_message
                    ? 'border-red-200'
                    : 'border-gray-200/80 hover:border-gray-300'"
                >
                  <!-- 步骤折叠头 -->
                  <button
                    type="button"
                    class="w-full px-3.5 py-2.5 flex items-center gap-2.5 text-left hover:bg-slate-50/70 transition-colors"
                    @click="toggleTraceStep(step, Number(idx))"
                  >
                    <span
                      class="w-5 h-5 rounded-md text-[11px] font-bold flex items-center justify-center shrink-0 tabular-nums"
                      :class="step.status !== 'success' || step.error_message
                        ? 'bg-red-50 text-red-600'
                        : 'bg-slate-100 text-slate-600'"
                    >
                      {{ Number(step.step_number ?? (Number(idx) + 1)) }}
                    </span>

                    <span
                      class="px-1.5 py-0.5 rounded text-[10.5px] font-semibold border shrink-0"
                      :class="getTraceEventMeta(step.event_type).badgeClass"
                      :title="step.event_type"
                    >
                      {{ getTraceEventMeta(step.event_type).label }}
                    </span>

                    <span
                      v-if="getSubagentMeta(step)"
                      class="px-1.5 py-0.5 rounded-full text-[10px] font-semibold border border-primary/20 bg-primary/5 text-primary shrink-0"
                      :title="formatSubagentTraceSummary(getSubagentMeta(step))"
                    >
                      子代理 · {{ subagentDisplayName(getSubagentMeta(step)) }}
                    </span>

                    <div class="flex-1 min-w-0 flex items-center gap-2 text-xs text-slate-600">
                      <code
                        v-if="step.tool_name"
                        class="px-1.5 py-0.5 bg-slate-100 border border-slate-200/80 rounded font-mono text-slate-800 text-[11px] font-semibold shrink-0"
                      >
                        {{ step.tool_name }}
                      </code>
                      <span class="truncate text-slate-500">
                        {{ summarizeTraceStep(step) }}
                      </span>
                    </div>

                    <span
                      v-if="formatStepTime(step.timestamp)"
                      class="hidden md:inline text-[10.5px] font-mono text-slate-400 shrink-0 tabular-nums"
                    >
                      {{ formatStepTime(step.timestamp) }}
                    </span>

                    <div
                      v-if="step.execution_time_ms"
                      class="hidden sm:flex items-center gap-2 w-[96px] shrink-0"
                      :title="`占本轮步骤总耗时 ${stepDurationPercent(step)}%`"
                    >
                      <div class="flex-1 h-1.5 rounded-full bg-slate-100 overflow-hidden">
                        <div
                          class="h-full rounded-full"
                          :class="step.status !== 'success' || step.error_message ? 'bg-red-500' : 'bg-primary'"
                          :style="{ width: `${stepDurationPercent(step)}%` }"
                        />
                      </div>
                    </div>

                    <span class="w-14 text-right text-[11px] font-mono text-slate-500 shrink-0 tabular-nums">
                      {{ step.execution_time_ms ? formatDurationMs(step.execution_time_ms) : '-' }}
                    </span>

                    <ChevronDownIcon
                      class="w-4 h-4 text-slate-400 shrink-0 transition-transform duration-150"
                      :class="isTraceStepExpanded(step, Number(idx)) ? 'rotate-180' : ''"
                    />
                  </button>

                  <!-- 步骤展开详情 -->
                  <div
                    v-if="isTraceStepExpanded(step, Number(idx))"
                    class="px-4 pb-4 pt-3 border-t border-gray-100 bg-slate-50/40 space-y-3"
                  >
                    <div
                      v-if="getSubagentMeta(step)"
                      class="rounded-lg border border-primary/15 bg-primary/5 p-3 space-y-2"
                    >
                      <div class="flex items-center justify-between gap-2">
                        <span class="text-xs font-bold text-primary-dark">子代理委派</span>
                        <span class="text-[10.5px] text-primary">
                          {{ formatSubagentTraceSummary(getSubagentMeta(step)) }}
                        </span>
                      </div>
                      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px] text-primary-dark/75">
                        <template v-if="getSubagentMeta(step)?.run_id">
                          <dt class="font-semibold">Run ID</dt>
                          <dd class="font-mono break-all">{{ getSubagentMeta(step)?.run_id }}</dd>
                        </template>
                        <template v-if="getSubagentMeta(step)?.parent_trace_id">
                          <dt class="font-semibold">父 Trace</dt>
                          <dd class="font-mono break-all">{{ getSubagentMeta(step)?.parent_trace_id }}</dd>
                        </template>
                        <template v-if="getSubagentMeta(step)?.child_trace_id">
                          <dt class="font-semibold">子 Trace</dt>
                          <dd class="font-mono break-all">{{ getSubagentMeta(step)?.child_trace_id }}</dd>
                        </template>
                        <template v-if="getSubagentMeta(step)?.stop_reason">
                          <dt class="font-semibold">停止原因</dt>
                          <dd>{{ getSubagentMeta(step)?.stop_reason }}</dd>
                        </template>
                        <template v-if="getSubagentMeta(step)?.tool_filter?.length">
                          <dt class="font-semibold">工具过滤</dt>
                          <dd class="break-all">{{ getSubagentMeta(step)?.tool_filter?.join('、') }}</dd>
                        </template>
                      </dl>
                    </div>

                    <!-- 图像用量：后端把生图/改图的张数与尺寸写进 meta_info.image_usage -->
                    <div
                      v-if="imageUsageFromStep(step)"
                      class="flex flex-wrap items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50/70 px-2.5 py-1.5"
                    >
                      <span class="text-[10px] font-bold text-amber-700/80 uppercase tracking-wider">用量</span>
                      <span
                        v-for="chip in imageUsageChips(step)"
                        :key="`usage-${idx}-${chip}`"
                        class="text-[11px] font-medium text-amber-900 bg-white/80 border border-amber-200 rounded px-1.5 py-0.5"
                      >
                        {{ chip }}
                      </span>
                    </div>

                    <div v-if="step.tool_input" class="space-y-1.5">
                      <div class="flex items-center justify-between gap-2">
                        <label class="text-[11px] font-semibold text-slate-500">
                          输入参数 / 思考内容
                        </label>
                        <div class="flex items-center gap-1.5">
                          <div
                            v-if="payloadView(step.tool_input).hasMarkdown"
                            class="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-[10.5px]"
                          >
                            <button
                              type="button"
                              class="px-1.5 py-0.5 rounded transition-colors"
                              :class="getStepViewMode(`input-${idx}`) === 'json'
                                ? 'bg-slate-800 text-white font-medium'
                                : 'text-slate-500 hover:text-slate-800'"
                              @click="toggleStepViewMode(`input-${idx}`, 'json')"
                            >
                              JSON
                            </button>
                            <button
                              type="button"
                              class="px-1.5 py-0.5 rounded transition-colors"
                              :class="getStepViewMode(`input-${idx}`) === 'render'
                                ? 'bg-primary text-white font-medium'
                                : 'text-slate-500 hover:text-primary'"
                              @click="toggleStepViewMode(`input-${idx}`, 'render')"
                            >
                              渲染预览
                            </button>
                          </div>
                          <button
                            type="button"
                            class="text-[11px] text-slate-400 hover:text-primary transition-colors"
                            title="复制原始参数"
                            @click="copyText(formatStepPayload(step.tool_input), '参数内容')"
                          >
                            复制
                          </button>
                        </div>
                      </div>

                      <div
                        v-if="getStepViewMode(`input-${idx}`) === 'render' && payloadView(step.tool_input).textContent"
                        class="payload-markdown max-w-none text-slate-800 break-words bg-white border border-gray-200/80 rounded-lg p-3.5"
                        v-html="payloadView(step.tool_input).markdownHtml"
                      />
                      <pre
                        v-else
                        class="text-xs text-slate-100 leading-relaxed font-mono bg-[#0d1117] border border-slate-800 rounded-lg p-3.5 overflow-x-auto"
                        v-html="payloadView(step.tool_input).jsonHtml"
                      />
                    </div>

                    <div v-if="step.tool_output" class="space-y-1.5">
                      <div class="flex items-center justify-between gap-2">
                        <label class="text-[11px] font-semibold text-slate-500">
                          返回结果
                        </label>
                        <div class="flex items-center gap-1.5">
                          <div
                            v-if="payloadView(step.tool_output).hasMarkdown"
                            class="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-[10.5px]"
                          >
                            <button
                              type="button"
                              class="px-1.5 py-0.5 rounded transition-colors"
                              :class="getStepViewMode(`output-${idx}`) === 'json'
                                ? 'bg-slate-800 text-white font-medium'
                                : 'text-slate-500 hover:text-slate-800'"
                              @click="toggleStepViewMode(`output-${idx}`, 'json')"
                            >
                              JSON
                            </button>
                            <button
                              type="button"
                              class="px-1.5 py-0.5 rounded transition-colors"
                              :class="getStepViewMode(`output-${idx}`) === 'render'
                                ? 'bg-primary text-white font-medium'
                                : 'text-slate-500 hover:text-primary'"
                              @click="toggleStepViewMode(`output-${idx}`, 'render')"
                            >
                              渲染预览
                            </button>
                          </div>
                          <button
                            type="button"
                            class="text-[11px] text-slate-400 hover:text-primary transition-colors"
                            title="复制原始结果"
                            @click="copyText(formatStepPayload(step.tool_output), '返回结果')"
                          >
                            复制
                          </button>
                        </div>
                      </div>

                      <div
                        v-if="getStepViewMode(`output-${idx}`) === 'render' && payloadView(step.tool_output).textContent"
                        class="payload-markdown max-w-none text-slate-800 break-words bg-white border border-gray-200/80 rounded-lg p-3.5"
                        v-html="payloadView(step.tool_output).markdownHtml"
                      />
                      <pre
                        v-else
                        class="text-xs text-slate-100 leading-relaxed font-mono bg-[#0d1117] border border-slate-800 rounded-lg p-3.5 overflow-x-auto"
                        v-html="payloadView(step.tool_output).jsonHtml"
                      />
                    </div>

                    <div
                      v-if="step.error_message"
                      class="flex items-start gap-2 bg-red-50/70 border border-red-200/80 rounded-lg p-3 text-red-700"
                    >
                      <ExclamationCircleIcon class="w-4 h-4 shrink-0 mt-0.5" />
                      <p class="text-xs font-mono leading-relaxed break-words">{{ step.error_message }}</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Tab 3: 上下文压缩记录 -->
          <ContextCompactionTimeline
            v-show="activeDetailTab === 'context'"
            :records="contextCompactions"
            :loading="contextLoading"
            :error="contextError"
            :show-refresh="false"
            @refresh="loadContextCompactions(selectedLog.conversation_id || undefined, true)"
          />
        </template>

        <div
          v-else
          class="flex-1 flex flex-col items-center justify-center text-slate-400 px-6"
        >
          <div class="w-12 h-12 rounded-2xl bg-slate-50 border border-gray-200/80 flex items-center justify-center mb-3">
            <ChatBubbleLeftRightIcon class="w-6 h-6 text-slate-300" />
          </div>
          <p class="text-sm font-medium text-slate-600">从左侧选择一条会话</p>
          <p class="text-xs mt-1 text-center text-slate-400">列表只展示摘要，完整内容与执行链路在这里查看</p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 日志审阅场景中把 Markdown 标题收敛为深墨色，避免和系统主色按钮撞色抢焦点 */
.chat-log-message-host :deep(.markdown-body h1),
.chat-log-message-host :deep(.markdown-body h2),
.chat-log-message-host :deep(.markdown-body h3) {
  color: #0f172a;
  font-weight: 700;
}

.chat-log-message-host :deep(.markdown-body h3) {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.chat-log-message-host :deep(.markdown-body h3::before) {
  content: '';
  width: 3px;
  height: 0.9em;
  border-radius: 9999px;
  background-color: var(--primary-color, #1677ff);
  flex-shrink: 0;
}

.payload-markdown {
  font-size: 14px;
  line-height: 1.65;
  overflow-wrap: break-word;
}
.payload-markdown :deep(p) { margin-bottom: 0.85em; }
.payload-markdown :deep(p:last-child) { margin-bottom: 0; }
.payload-markdown :deep(h1),
.payload-markdown :deep(h2),
.payload-markdown :deep(h3),
.payload-markdown :deep(h4) {
  font-weight: 700;
  color: #111827;
  margin-top: 1.1em;
  margin-bottom: 0.5em;
  line-height: 1.35;
}
.payload-markdown :deep(h1) { font-size: 1.35em; }
.payload-markdown :deep(h2) { font-size: 1.2em; }
.payload-markdown :deep(h3) { font-size: 1.05em; }
.payload-markdown :deep(h1:first-child),
.payload-markdown :deep(h2:first-child),
.payload-markdown :deep(h3:first-child) { margin-top: 0; }
.payload-markdown :deep(ul) {
  list-style-type: disc;
  padding-left: 1.4em;
  margin-bottom: 0.85em;
}
.payload-markdown :deep(ol) {
  list-style-type: decimal;
  padding-left: 1.4em;
  margin-bottom: 0.85em;
}
.payload-markdown :deep(li) { margin-bottom: 0.35em; }
.payload-markdown :deep(strong) { font-weight: 700; color: #111827; }
.payload-markdown :deep(a) { color: #2563eb; text-decoration: underline; }
.payload-markdown :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  background-color: rgba(175, 184, 193, 0.22);
  padding: 0.15em 0.35em;
  border-radius: 4px;
  font-size: 85%;
  color: #b91c1c;
}
.payload-markdown :deep(pre) {
  margin: 0.85em 0;
  padding: 0.85em 1em;
  background-color: #1e293b;
  border-radius: 8px;
  overflow: auto;
}
.payload-markdown :deep(pre code) {
  background: transparent;
  color: #e2e8f0;
  padding: 0;
  font-size: 12.5px;
}
.payload-markdown :deep(blockquote) {
  border-left: 3px solid #93c5fd;
  padding-left: 0.85em;
  color: #4b5563;
  margin: 0.85em 0;
}
.payload-markdown :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.85em 0;
  font-size: 13px;
}
.payload-markdown :deep(th),
.payload-markdown :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 0.4em 0.6em;
}
.payload-markdown :deep(th) {
  background: #f8fafc;
  font-weight: 600;
}
</style>
