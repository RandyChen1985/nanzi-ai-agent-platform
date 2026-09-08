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
  MagnifyingGlassIcon,
  ClockIcon,
  ExclamationCircleIcon,
  UserIcon,
  SparklesIcon,
  ArrowDownTrayIcon,
} from '@heroicons/vue/24/outline'
import { format } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import { renderMarkdown } from '@/utils/markdown'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import {
  buildChatSessionMarkdown,
  buildSessionExportFilename,
  downloadMarkdownFile,
  type ChatTraceDetail,
} from '@/utils/chatSessionExport'
import { copyToClipboard } from '@/utils/clipboard'
import ContextCompactionTimeline from '@/components/chat/ContextCompactionTimeline.vue'
import {
  formatSubagentTraceSummary,
  normalizeSubagentTraceMeta,
  subagentDisplayName,
} from '@/utils/subagentTrace'

const { showToast } = useToast()
const { hasPermission } = useUser()

const canExportLogs = computed(() => hasPermission('element:chat_logs:export'))

const cachedUser = localStorage.getItem('user_info')
const userInfo = ref(cachedUser ? JSON.parse(cachedUser) : null)
const isAdmin = computed(() => userInfo.value?.role === 'admin')

const viewMode = ref<'conversation' | 'turn'>('conversation')
const logs = ref<AgentExecutionHistory[]>([])
const agents = ref<AIAgent[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(15)
const loading = ref(false)
const showFilters = ref(false)

const filters = ref({
  agent_id: '',
  username: '',
  keyword: '',
  status: '',
  start_date: '',
  end_date: '',
})

const selectedId = ref<number | string | null>(null)
const activeDetailTab = ref<'conversation' | 'trace' | 'context'>('conversation')
const replyViewMode = ref<'render' | 'source'>('render')
const turnReplyModes = ref<Record<string | number, 'render' | 'source'>>({})
const traceDetail = ref<any>(null)
const traceLoading = ref(false)
const contextCompactions = ref<ContextCompactionRecord[]>([])
const contextLoading = ref(false)
const contextError = ref(false)
const contextLoadedFor = ref('')
let contextRequestVersion = 0
const exporting = ref(false)

// 多轮对话状态
const conversationTurns = ref<AgentExecutionHistory[]>([])
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

const getTurnReplyMode = (id: string | number) => turnReplyModes.value[id] || 'render'
const setTurnReplyMode = (id: string | number, mode: 'render' | 'source') => {
  turnReplyModes.value[id] = mode
}

const renderTurnReply = (text?: string) => {
  if (!text) return ''
  try {
    return renderMarkdown(text)
  } catch (e) {
    console.error('Markdown render failed', e)
    return `<p class="text-red-500">Markdown 渲染失败</p>`
  }
}

const hasActiveFilters = computed(() =>
  Object.values(filters.value).some((v) => v !== ''),
)

const fetchAgents = async () => {
  try {
    const res = await agentApi.listAgents()
    agents.value = res.data
  } catch (e) {
    console.error('Failed to fetch agents', e)
  }
}

const fetchLogs = async () => {
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

    const res = await agentApi.getChatHistory(params)
    logs.value = res.data.data.items || []
    total.value = res.data.data.total

    if (
      selectedId.value == null ||
      !logs.value.some((l) => l.id === selectedId.value)
    ) {
      selectedId.value = logs.value[0]?.id ?? null
    }
  } catch (e) {
    console.error('Failed to fetch logs', e)
    showToast('获取聊天日志失败', 'error')
  } finally {
    loading.value = false
  }
}

const changeViewMode = (mode: 'conversation' | 'turn') => {
  if (viewMode.value === mode) return
  viewMode.value = mode
  page.value = 1
  selectedId.value = null
  fetchLogs()
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

const loadTrace = async (traceId?: string) => {
  if (!traceId) {
    traceDetail.value = null
    return
  }
  traceLoading.value = true
  traceDetail.value = null
  try {
    const res = await agentApi.getChatTrace(traceId)
    traceDetail.value = res.data.data
  } catch (e) {
    console.error('Failed to fetch trace', e)
    showToast('获取执行链路失败', 'error')
  } finally {
    traceLoading.value = false
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
    const res = await agentApi.getContextCompactions(id)
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

const truncateQuery = (text?: string, max = 72) => {
  const value = (text || '').replace(/\s+/g, ' ').trim()
  if (!value) return '(空提问)'
  return value.length > max ? `${value.slice(0, max)}…` : value
}

const getAgentName = (agentId: string) => {
  const agent = agents.value.find((a) => a.id === agentId)
  return agent ? agent.display_name : agentId
}

const getStatusClass = (status: string) => {
  if (status === 'success') return 'bg-emerald-50 text-emerald-700 border-emerald-100'
  if (status === 'error') return 'bg-red-50 text-red-600 border-red-100'
  return 'bg-gray-100 text-gray-700 border-gray-200'
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
        cls = 'text-purple-300 font-semibold' // boolean
      } else if (/null/.test(match)) {
        cls = 'text-slate-500 italic' // null
      }
      return `<span class="${cls}">${match}</span>`
    }
  )
}

const renderPayloadMarkdown = (text: string) => {
  try {
    return renderMarkdown(text)
  } catch {
    return escapeHtml(text)
  }
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
  const pageSize = 100
  let page = 1
  const collected: AgentExecutionHistory[] = []
  let total = 0
  do {
    const res = await agentApi.getChatHistory({
      conversation_id: conversationId,
      page,
      page_size: pageSize,
    })
    const batch = res.data.data.items || []
    total = res.data.data.total ?? batch.length
    collected.push(...batch)
    if (collected.length >= total || batch.length < pageSize) break
    page += 1
  } while (page <= 50)

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
        const res = await agentApi.getChatTrace(turn.trace_id)
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
    conversationTurns.value = [log]
    selectedTraceId.value = log.trace_id
    void loadTrace(log.trace_id)
    return
  }

  turnsLoading.value = true
  try {
    const res = await agentApi.getChatHistory({
      conversation_id: log.conversation_id.trim(),
      page: 1,
      page_size: 100,
    })
    if (reqVer !== turnsRequestVersion) return
    const items = res.data.data.items || []
    if (items.length > 0) {
      items.sort(
        (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      )
      conversationTurns.value = items
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
      conversationTurns.value = [log]
      selectedTraceId.value = log.trace_id
      void loadTrace(log.trace_id)
    }
  } catch (e) {
    if (reqVer !== turnsRequestVersion) return
    console.error('Failed to load conversation turns', e)
    conversationTurns.value = [log]
    selectedTraceId.value = log.trace_id
    void loadTrace(log.trace_id)
  } finally {
    if (reqVer === turnsRequestVersion) {
      turnsLoading.value = false
    }
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
  <div class="flex flex-col h-[calc(100vh-7.5rem)] min-h-[560px] max-w-[1600px] mx-auto gap-4">
    <!-- Header -->
    <div class="shrink-0 flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3 min-w-0">
        <div class="p-2 bg-primary/10 rounded-xl shrink-0">
          <ChatBubbleLeftRightIcon class="h-6 w-6 text-primary" />
        </div>
        <div class="min-w-0">
          <h1 class="text-xl font-bold text-gray-900 tracking-tight">聊天日志</h1>
          <p class="text-xs text-gray-500 mt-0.5">按会话浏览提问与回复，并可查看执行链路</p>
        </div>
      </div>
      <button
        type="button"
        class="inline-flex items-center px-3.5 py-2 bg-white border border-gray-200 rounded-xl shadow-sm text-sm font-medium text-gray-600 hover:bg-gray-50 transition-all"
        @click="fetchLogs"
      >
        <ArrowPathIcon class="w-4 h-4 mr-1.5" :class="{ 'animate-spin': loading }" />
        刷新
      </button>
    </div>

    <!-- Search / Filters -->
    <div class="shrink-0 bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
      <div class="px-4 py-3 flex flex-wrap items-center gap-2">
        <div class="relative flex-1 min-w-[200px]">
          <MagnifyingGlassIcon class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            v-model="filters.keyword"
            type="search"
            placeholder="搜索问题或回复内容..."
            class="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg bg-gray-50/80 focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
            @keyup.enter="applyFilters"
          />
        </div>
        <button
          type="button"
          class="px-3.5 py-2 bg-primary text-white text-sm font-medium rounded-lg hover:bg-primary-dark transition-colors"
          @click="applyFilters"
        >
          搜索
        </button>
        <button
          type="button"
          class="inline-flex items-center px-3 py-2 text-sm font-medium rounded-lg border transition-colors"
          :class="showFilters || hasActiveFilters
            ? 'border-primary/30 bg-primary/5 text-primary'
            : 'border-gray-200 text-gray-600 hover:bg-gray-50'"
          @click="showFilters = !showFilters"
        >
          <FunnelIcon class="w-4 h-4 mr-1.5" />
          高级筛选
          <span
            v-if="hasActiveFilters"
            class="ml-1.5 w-1.5 h-1.5 rounded-full bg-primary"
          />
        </button>
      </div>

      <div v-show="showFilters" class="px-4 pb-4 pt-1 border-t border-gray-50">
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
          <div class="space-y-1">
            <label class="text-[11px] font-semibold text-gray-400">智能体</label>
            <select
              v-model="filters.agent_id"
              class="w-full text-sm border border-gray-200 rounded-lg bg-gray-50 p-2 outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
            >
              <option value="">全部智能体</option>
              <option v-for="a in agents" :key="a.id" :value="a.id">{{ a.display_name }}</option>
            </select>
          </div>
          <div v-if="isAdmin" class="space-y-1">
            <label class="text-[11px] font-semibold text-gray-400">用户</label>
            <input
              v-model="filters.username"
              type="text"
              placeholder="用户名"
              class="w-full text-sm border border-gray-200 rounded-lg bg-gray-50 p-2 outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
            />
          </div>
          <div class="space-y-1">
            <label class="text-[11px] font-semibold text-gray-400">状态</label>
            <select
              v-model="filters.status"
              class="w-full text-sm border border-gray-200 rounded-lg bg-gray-50 p-2 outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
            >
              <option value="">全部状态</option>
              <option value="success">成功</option>
              <option value="error">异常</option>
            </select>
          </div>
          <div class="space-y-1">
            <label class="text-[11px] font-semibold text-gray-400">开始日期</label>
            <input
              v-model="filters.start_date"
              type="date"
              class="w-full text-sm border border-gray-200 rounded-lg bg-gray-50 p-2 outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
            />
          </div>
          <div class="space-y-1">
            <label class="text-[11px] font-semibold text-gray-400">结束日期</label>
            <input
              v-model="filters.end_date"
              type="date"
              class="w-full text-sm border border-gray-200 rounded-lg bg-gray-50 p-2 outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
            />
          </div>
        </div>
        <div class="mt-3 flex justify-end gap-2">
          <button
            type="button"
            class="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700"
            @click="resetFilters"
          >
            重置
          </button>
          <button
            type="button"
            class="px-4 py-1.5 bg-gray-900 text-white text-sm font-medium rounded-lg hover:bg-black"
            @click="applyFilters"
          >
            应用筛选
          </button>
        </div>
      </div>
    </div>

    <!-- Split pane -->
    <div class="flex-1 min-h-0 bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden flex flex-col sm:flex-row">
      <!-- Left list -->
      <aside class="sm:w-[360px] sm:max-w-[40%] shrink-0 border-b sm:border-b-0 sm:border-r border-gray-100 bg-gray-50/50 flex flex-col min-h-0 max-h-[40%] sm:max-h-none">
        <div class="px-3.5 py-2 border-b border-gray-100 flex items-center justify-between shrink-0 gap-2 bg-white/70">
          <div class="inline-flex p-0.5 rounded-lg bg-gray-100 border border-gray-200/60 text-[11px] font-medium">
            <button
              type="button"
              class="px-2 py-0.5 rounded-md transition-all"
              :class="viewMode === 'conversation'
                ? 'bg-white text-gray-900 shadow-sm font-semibold'
                : 'text-gray-500 hover:text-gray-800'"
              @click="changeViewMode('conversation')"
            >
              按会话
            </button>
            <button
              type="button"
              class="px-2 py-0.5 rounded-md transition-all"
              :class="viewMode === 'turn'
                ? 'bg-white text-gray-900 shadow-sm font-semibold'
                : 'text-gray-500 hover:text-gray-800'"
              @click="changeViewMode('turn')"
            >
              按调用
            </button>
          </div>
          <span class="font-mono text-[11px] text-gray-400 font-medium">
            共 {{ total }} {{ viewMode === 'conversation' ? '个会话' : '条' }}
          </span>
        </div>

        <div class="flex-1 overflow-y-auto custom-scrollbar min-h-0">
          <div v-if="loading && logs.length === 0" class="py-16 text-center text-sm text-gray-400">
            加载中...
          </div>
          <div v-else-if="!loading && logs.length === 0" class="py-16 px-6 text-center">
            <ChatBubbleLeftRightIcon class="w-10 h-10 text-gray-200 mx-auto mb-3" />
            <p class="text-sm font-medium text-gray-500">暂无聊天记录</p>
            <p class="text-[11px] text-gray-400 mt-1">试试调整筛选条件</p>
          </div>
          <button
            v-for="log in logs"
            :key="log.id"
            type="button"
            class="w-full text-left px-3.5 py-3 border-b border-gray-100/80 transition-colors"
            :class="selectedId === log.id
              ? 'bg-white border-l-2 border-l-primary shadow-[inset_0_0_0_1px_rgba(0,0,0,0.02)]'
              : 'hover:bg-white/80 border-l-2 border-l-transparent'"
            @click="selectLog(log)"
          >
            <div class="flex items-start justify-between gap-2 mb-1.5">
              <div class="flex items-center gap-1.5 min-w-0">
                <template v-if="isAdmin">
                  <UserIcon class="w-3.5 h-3.5 text-gray-400 shrink-0" />
                  <span class="text-[11px] font-semibold text-gray-700 truncate">{{ log.username || '匿名' }}</span>
                  <span class="text-gray-300">·</span>
                </template>
                <SparklesIcon class="w-3.5 h-3.5 text-primary shrink-0" />
                <span class="text-[11px] text-primary font-medium truncate">{{ getAgentName(log.agent_id) }}</span>
              </div>
              <span class="text-[10px] font-mono text-gray-400 whitespace-nowrap shrink-0">{{ formatListTime(log.created_at) }}</span>
            </div>
            <p
              class="text-[13px] leading-snug line-clamp-2"
              :class="selectedId === log.id ? 'text-gray-900 font-medium' : 'text-gray-600'"
            >
              {{ truncateQuery(log.query) }}
            </p>
            <div class="mt-2 flex items-center justify-between gap-1.5">
              <div class="flex items-center gap-1.5 flex-wrap min-w-0">
              <span
                class="inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide border"
                :class="getStatusClass(log.status)"
              >{{ log.status === 'success' ? '成功' : log.status === 'error' ? '异常' : log.status }}</span>
              <span
                v-if="log.agent_version"
                class="inline-flex px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 text-[9px] font-bold border border-gray-200"
              >{{ log.agent_version }}</span>
              <span
                v-if="viewMode === 'conversation' && log.turn_count"
                class="inline-flex items-center px-1.5 py-0.5 rounded bg-primary/10 text-primary text-[9px] font-bold border border-primary/20"
              >
                共 {{ log.turn_count }} 轮
              </span>
              <span
                v-else-if="log.execution_time_ms"
                class="text-[10px] font-mono text-gray-400"
              >{{ Math.round(log.execution_time_ms) }}ms</span>
              </div>
              <button
                v-if="canExportLogs"
                type="button"
                class="shrink-0 p-1 rounded-md text-gray-400 hover:text-primary hover:bg-primary/5 transition-colors disabled:opacity-40"
                :disabled="exporting"
                title="导出会话为 Markdown"
                @click.stop="exportSession(log)"
              >
                <ArrowDownTrayIcon class="w-4 h-4" />
              </button>
            </div>
          </button>
        </div>

        <div class="shrink-0 px-3 py-2.5 border-t border-gray-100 bg-white/90 flex items-center justify-between gap-2">
          <span class="text-[10px] text-gray-400 font-mono truncate">
            {{ total === 0 ? '0' : `${(page - 1) * pageSize + 1}-${Math.min(page * pageSize, total)}` }} / {{ total }}
          </span>
          <div class="flex items-center gap-1">
            <button
              type="button"
              class="p-1.5 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-30"
              :disabled="page === 1 || loading"
              @click="page--"
            >
              <ChevronLeftIcon class="w-4 h-4" />
            </button>
            <span class="text-xs font-medium text-gray-600 px-1.5">{{ page }}</span>
            <button
              type="button"
              class="p-1.5 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-30"
              :disabled="page * pageSize >= total || loading"
              @click="page++"
            >
              <ChevronRightIcon class="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      <!-- Right detail -->
      <section class="flex-1 min-w-0 min-h-0 flex flex-col bg-white">
        <template v-if="selectedLog">
          <div class="shrink-0 px-5 py-3 border-b border-gray-100 flex flex-wrap items-center gap-2 justify-between">
            <div class="flex items-center gap-2 min-w-0 flex-wrap">
              <span class="text-xs font-mono text-gray-500">{{ formatDate(activeTurn?.created_at || selectedLog.created_at) }}</span>
              <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-primary/5 text-primary text-[10px] font-bold border border-primary/10">
                <SparklesIcon class="w-3 h-3" />
                {{ getAgentName(activeTurn?.agent_id || selectedLog.agent_id) }}
              </span>
              <span
                v-if="isAdmin"
                class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-gray-50 text-gray-600 text-[10px] font-bold border border-gray-200"
              >
                <UserIcon class="w-3 h-3" />
                {{ activeTurn?.username || selectedLog.username || '匿名' }}
              </span>
              <span
                v-if="activeTurn?.agent_version || selectedLog.agent_version"
                class="inline-flex px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px] font-bold border border-gray-200"
              >{{ activeTurn?.agent_version || selectedLog.agent_version }}</span>
              <span
                v-if="activeTurn?.model_id || selectedLog.model_id"
                class="inline-flex px-2 py-0.5 rounded bg-slate-50 text-slate-600 text-[10px] font-bold border border-slate-200 truncate max-w-[160px]"
                :title="activeTurn?.model_id || selectedLog.model_id"
              >{{ activeTurn?.model_id || selectedLog.model_id }}</span>
              <span
                class="inline-flex px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide border"
                :class="getStatusClass(activeTurn?.status || selectedLog.status)"
              >{{ (activeTurn?.status || selectedLog.status) === 'success' ? '成功' : (activeTurn?.status || selectedLog.status) === 'error' ? '异常' : (activeTurn?.status || selectedLog.status) }}</span>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <button
                v-if="canExportLogs"
                type="button"
                class="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-semibold rounded-lg border border-gray-200 text-gray-600 hover:text-primary hover:border-primary/30 hover:bg-primary/5 transition-colors disabled:opacity-40"
                :disabled="exporting"
                @click="exportSession(selectedLog)"
              >
                <ArrowDownTrayIcon class="w-3.5 h-3.5" />
                {{ exporting ? '导出中…' : '导出会话' }}
              </button>
              <span class="text-[11px] font-mono text-gray-400">
                {{ (activeTurn?.execution_time_ms ?? selectedLog.execution_time_ms) ? `${Math.round(activeTurn?.execution_time_ms ?? selectedLog.execution_time_ms!)}ms` : '-' }}
              </span>
              <button
                v-if="activeTurn?.trace_id || selectedLog.trace_id"
                type="button"
                class="text-[11px] font-mono text-gray-400 hover:text-primary truncate max-w-[120px]"
                :title="activeTurn?.trace_id || selectedLog.trace_id"
                @click="copyText((activeTurn?.trace_id || selectedLog.trace_id)!, 'Trace ID')"
              >
                {{ (activeTurn?.trace_id || selectedLog.trace_id)!.slice(0, 8) }}…
              </button>
            </div>
          </div>

          <!-- Detail Tabs Header -->
          <div class="shrink-0 px-5 border-b border-gray-100 flex items-center justify-between bg-gray-50/40">
            <div class="flex items-center gap-6">
              <button
                type="button"
                class="py-2.5 text-xs font-bold border-b-2 transition-all flex items-center gap-1.5"
                :class="activeDetailTab === 'conversation'
                  ? 'border-primary text-primary'
                  : 'border-transparent text-gray-500 hover:text-gray-800'"
                @click="activeDetailTab = 'conversation'"
              >
                <ChatBubbleLeftRightIcon class="w-4 h-4" />
                对话
                <span
                  v-if="conversationTurns.length"
                  class="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-mono leading-none"
                  :class="activeDetailTab === 'conversation' ? 'bg-primary/10 text-primary' : 'bg-gray-200/80 text-gray-600'"
                >
                  {{ conversationTurns.length }}
                </span>
              </button>
              <button
                type="button"
                class="py-2.5 text-xs font-bold border-b-2 transition-all flex items-center gap-1.5"
                :class="activeDetailTab === 'trace'
                  ? 'border-primary text-primary'
                  : 'border-transparent text-gray-500 hover:text-gray-800'"
                @click="activeDetailTab = 'trace'"
              >
                <ClockIcon class="w-4 h-4" />
                轨迹
                <span
                  v-if="traceDetail?.steps?.length"
                  class="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-mono leading-none"
                  :class="activeDetailTab === 'trace' ? 'bg-primary/10 text-primary' : 'bg-gray-200/80 text-gray-600'"
                >
                  {{ traceDetail.steps.length }}
                </span>
              </button>
              <button
                type="button"
                class="py-2.5 text-xs font-bold border-b-2 transition-all flex items-center gap-1.5"
                :class="activeDetailTab === 'context'
                  ? 'border-primary text-primary'
                  : 'border-transparent text-gray-500 hover:text-gray-800'"
                @click="activeDetailTab = 'context'"
              >
                <ExclamationCircleIcon class="w-4 h-4" />
                上下文
                <span
                  v-if="contextCompactions.length"
                  class="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-mono leading-none"
                  :class="activeDetailTab === 'context' ? 'bg-primary/10 text-primary' : 'bg-gray-200/80 text-gray-600'"
                >
                  {{ contextCompactions.length }}
                </span>
              </button>
            </div>
            <div v-if="activeDetailTab === 'trace' && (selectedTraceId || selectedLog.trace_id)" class="flex items-center gap-2">
              <button
                type="button"
                class="inline-flex items-center gap-1 text-[11px] text-gray-400 hover:text-primary transition-colors"
                @click="loadTrace(selectedTraceId || selectedLog.trace_id)"
                :disabled="traceLoading"
                title="重新加载链路"
              >
                <ArrowPathIcon class="w-3.5 h-3.5" :class="{ 'animate-spin': traceLoading }" />
                刷新链路
              </button>
            </div>
            <div v-else-if="activeDetailTab === 'context'" class="flex items-center gap-2">
              <button
                type="button"
                class="inline-flex items-center gap-1 text-[11px] text-gray-400 hover:text-primary transition-colors"
                @click="loadContextCompactions(selectedLog.conversation_id || undefined, true)"
                :disabled="contextLoading"
                title="重新加载上下文压缩记录"
              >
                <ArrowPathIcon class="w-3.5 h-3.5" :class="{ 'animate-spin': contextLoading }" />
                刷新上下文
              </button>
            </div>
          </div>

          <!-- Tab 1: Conversation Content (Multi-turn Support) -->
          <div
            v-show="activeDetailTab === 'conversation'"
            class="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-5 space-y-6"
          >
            <div v-if="turnsLoading && conversationTurns.length === 0" class="py-20 text-center text-sm text-gray-400">
              <ArrowPathIcon class="w-6 h-6 animate-spin mx-auto mb-2 text-primary" />
              正在加载对话记录...
            </div>

            <div
              v-for="(turn, index) in conversationTurns"
              :key="turn.id || turn.trace_id"
              class="space-y-4 rounded-2xl border border-gray-100 bg-white p-4 shadow-sm"
              :class="{ 'ring-2 ring-primary/20': selectedTraceId === turn.trace_id }"
            >
              <!-- Turn Header / Index -->
              <div class="flex items-center justify-between gap-2 pb-2 border-b border-gray-100/70 text-xs">
                <div class="flex items-center gap-2">
                  <span class="px-2 py-0.5 rounded-md bg-gray-100 font-bold text-gray-700 text-[11px]">
                    第 {{ Number(index) + 1 }} 轮
                  </span>
                  <span class="font-mono text-gray-400 text-[11px]">{{ formatDate(turn.created_at) }}</span>
                </div>
                <div class="flex items-center gap-2">
                  <span
                    v-if="turn.model_id"
                    class="px-1.5 py-0.5 rounded bg-slate-50 text-slate-600 text-[10px] font-bold border border-slate-200"
                  >{{ turn.model_id }}</span>
                  <span
                    class="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide border"
                    :class="getStatusClass(turn.status)"
                  >{{ turn.status === 'success' ? '成功' : turn.status === 'error' ? '异常' : turn.status }}</span>
                  <span v-if="turn.execution_time_ms" class="text-[10px] font-mono text-gray-400">
                    {{ Math.round(turn.execution_time_ms) }}ms
                  </span>
                </div>
              </div>

              <!-- User query -->
              <div class="rounded-xl border border-gray-200 bg-gray-50/80 p-4 relative group">
                <div class="flex items-center justify-between gap-2 mb-2">
                  <div class="flex items-center gap-2">
                    <span class="w-6 h-6 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center text-[10px] font-bold">
                      {{ turn.username ? turn.username.charAt(0).toUpperCase() : 'U' }}
                    </span>
                    <span class="text-xs font-bold text-gray-500">用户提问</span>
                  </div>
                  <button
                    type="button"
                    class="text-[11px] text-gray-400 hover:text-primary font-medium opacity-70 group-hover:opacity-100"
                    @click="copyText(turn.query || '', '用户提问')"
                  >
                    复制
                  </button>
                </div>
                <p class="text-sm text-gray-800 whitespace-pre-wrap break-words leading-relaxed">
                  {{ turn.query || '(空)' }}
                </p>
              </div>

              <!-- AI reply -->
              <div class="rounded-xl border border-blue-100 bg-blue-50/40 p-4 relative group">
                <div class="flex items-center justify-between gap-2 mb-2">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="text-xs font-bold text-primary/70">AI 回复</span>
                    <div class="inline-flex rounded-md border border-blue-100 bg-white/80 p-0.5 text-[11px]">
                      <button
                        type="button"
                        class="px-2 py-0.5 rounded transition-colors"
                        :class="getTurnReplyMode(turn.id) === 'render'
                          ? 'bg-primary text-white font-medium shadow-sm'
                          : 'text-gray-500 hover:text-primary'"
                        @click="setTurnReplyMode(turn.id, 'render')"
                      >
                        渲染
                      </button>
                      <button
                        type="button"
                        class="px-2 py-0.5 rounded transition-colors"
                        :class="getTurnReplyMode(turn.id) === 'source'
                          ? 'bg-primary text-white font-medium shadow-sm'
                          : 'text-gray-500 hover:text-primary'"
                        @click="setTurnReplyMode(turn.id, 'source')"
                      >
                        源码
                      </button>
                    </div>
                  </div>
                  <div class="flex items-center gap-2">
                    <button
                      type="button"
                      class="text-[11px] text-primary/50 hover:text-primary font-medium opacity-70 group-hover:opacity-100 shrink-0"
                      @click="copyText(turn.summary || '', '回复内容')"
                    >
                      复制
                    </button>
                  </div>
                </div>
                <div v-if="!turn.summary" class="text-sm text-gray-500">(无响应内容)</div>
                <div
                  v-else-if="getTurnReplyMode(turn.id) === 'render'"
                  class="markdown-body prose prose-sm max-w-none text-gray-800 break-words"
                  v-html="renderTurnReply(turn.summary)"
                />
                <pre
                  v-else
                  class="text-sm text-gray-800 whitespace-pre-wrap break-words leading-relaxed font-mono bg-white/60 border border-blue-100/80 rounded-lg p-3 overflow-x-auto"
                >{{ turn.summary }}</pre>

                <!-- Bottom action bar: View Trace Link -->
                <div class="mt-3 pt-2.5 border-t border-blue-100/60 flex items-center justify-between text-xs">
                  <span class="text-[11px] font-mono text-gray-400">
                    Trace: {{ turn.trace_id ? turn.trace_id.slice(0, 12) + '…' : '-' }}
                  </span>
                  <button
                    v-if="turn.trace_id"
                    type="button"
                    class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-white border border-blue-200 text-primary text-[11px] font-semibold hover:bg-primary hover:text-white transition-all shadow-2xs"
                    @click="viewTurnTrace(turn)"
                  >
                    <ClockIcon class="w-3.5 h-3.5" />
                    查看此轮执行链路
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- Tab 2: Trace Content -->
          <div
            v-show="activeDetailTab === 'trace'"
            class="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-5"
          >
            <!-- Multi-turn Trace Selector Bar -->
            <div
              v-if="conversationTurns.length > 1"
              class="mb-5 flex items-center gap-2 p-2.5 bg-gray-50 rounded-xl border border-gray-100 overflow-x-auto shrink-0"
            >
              <span class="text-xs font-semibold text-gray-500 shrink-0">选择轮次链路：</span>
              <div class="flex items-center gap-2 shrink-0">
                <button
                  v-for="(turn, idx) in conversationTurns"
                  :key="turn.id || turn.trace_id"
                  type="button"
                  class="px-3 py-1 text-xs rounded-lg transition-all flex items-center gap-1.5"
                  :class="selectedTraceId === turn.trace_id
                    ? 'bg-primary text-white font-semibold shadow-sm'
                    : 'bg-white text-gray-600 border border-gray-200 hover:bg-gray-100 hover:text-gray-900'"
                  @click="switchTraceTurn(turn)"
                >
                  <span>第 {{ Number(idx) + 1 }} 轮</span>
                  <span class="text-[10px] opacity-80 font-mono">({{ Math.round(turn.execution_time_ms || 0) }}ms)</span>
                </button>
              </div>
            </div>

            <div v-if="traceLoading" class="py-20 text-center text-sm text-gray-400">
              <ArrowPathIcon class="w-6 h-6 animate-spin mx-auto mb-2 text-primary" />
              正在加载执行链路...
            </div>
            <div
              v-else-if="!traceDetail?.steps?.length"
              class="py-16 text-center text-sm text-gray-400"
            >
              <ClockIcon class="w-8 h-8 text-gray-200 mx-auto mb-2" />
              暂无执行链路数据
            </div>
            <div v-else class="relative space-y-8 pb-4 max-w-4xl mx-auto">
              <div class="absolute left-[83px] top-2 bottom-2 w-0.5 bg-gray-100" />
              <div
                v-for="(step, idx) in traceDetail.steps"
                :key="idx"
                class="relative flex items-start"
              >
                <!-- Step Timestamp (Left) -->
                <div class="w-[72px] shrink-0 pt-0.5 text-right pr-2">
                  <span class="text-[11px] font-mono font-medium text-gray-400 whitespace-nowrap">
                    {{ formatStepTime(step.timestamp) || '-' }}
                  </span>
                </div>

                <!-- Node Dot (Center) -->
                <div class="relative w-[22px] flex justify-center shrink-0 pt-1">
                  <div
                    class="w-3 h-3 rounded-full ring-4 ring-white border-2 bg-white z-10"
                    :class="step.status === 'success' ? 'border-primary' : 'border-red-500'"
                  >
                    <div
                      class="w-full h-full rounded-full scale-50"
                      :class="step.status === 'success' ? 'bg-primary' : 'bg-red-500'"
                    />
                  </div>
                </div>

                <!-- Step Content (Right) -->
                <div class="flex-1 min-w-0 pl-3 space-y-3">
                  <div class="flex items-center flex-wrap gap-2">
                    <span class="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                      Step {{ Number(step.step_number ?? (Number(idx) + 1)) }}
                    </span>
                    <span
                      class="px-2 py-0.5 rounded text-[9px] font-bold border uppercase tracking-wider"
                      :class="{
                        'bg-blue-50 text-blue-600 border-blue-100': step.event_type === 'thought',
                        'bg-amber-50 text-amber-600 border-amber-100': step.event_type === 'tool_call',
                        'bg-green-50 text-green-600 border-green-100': step.event_type === 'tool_result' || step.event_type === 'final_answer',
                        'bg-red-50 text-red-600 border-red-100': step.event_type === 'error',
                        'bg-gray-50 text-gray-600 border-gray-200': !['thought', 'tool_call', 'tool_result', 'final_answer', 'error'].includes(step.event_type),
                      }"
                    >
                      {{ step.event_type }}
                    </span>
                    <span
                      v-if="step.execution_time_ms"
                      class="text-[10px] text-gray-400 inline-flex items-center"
                    >
                      <ClockIcon class="w-3 h-3 mr-0.5" />
                      {{ Number(step.execution_time_ms).toFixed(0) }}ms
                    </span>
                    <span
                      v-if="getSubagentMeta(step)"
                      class="px-2 py-0.5 rounded-full text-[10px] font-semibold border border-indigo-100 bg-indigo-50 text-indigo-600"
                      :title="formatSubagentTraceSummary(getSubagentMeta(step))"
                    >
                      子代理 · {{ subagentDisplayName(getSubagentMeta(step)) }}
                    </span>
                  </div>

                  <div class="rounded-xl border border-gray-100 bg-gray-50/60 p-3 space-y-3">
                    <div
                      v-if="getSubagentMeta(step)"
                      class="rounded-lg border border-indigo-100 bg-indigo-50/60 p-3 space-y-2"
                    >
                      <div class="flex items-center justify-between gap-2">
                        <span class="text-xs font-bold text-indigo-700">子代理委派</span>
                        <span class="text-[10px] text-indigo-500">
                          {{ formatSubagentTraceSummary(getSubagentMeta(step)) }}
                        </span>
                      </div>
                      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px] text-indigo-900/70">
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
                    <div v-if="step.tool_name" class="text-xs font-bold text-gray-700">
                      <code class="px-2 py-0.5 bg-white border border-gray-200 rounded-md font-mono text-primary text-[11px]">
                        {{ step.tool_name }}
                      </code>
                    </div>
                    <div v-if="step.tool_input" class="space-y-1.5">
                      <div class="flex items-center justify-between gap-2">
                        <label class="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                          Params / Thought
                        </label>
                        <div class="flex items-center gap-1.5">
                          <div
                            v-if="parsePayloadObject(step.tool_input).hasMarkdown"
                            class="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-[10px]"
                          >
                            <button
                              type="button"
                              class="px-1.5 py-0.5 rounded transition-colors"
                              :class="getStepViewMode(`input-${idx}`) === 'json'
                                ? 'bg-gray-800 text-white font-medium shadow-sm'
                                : 'text-gray-500 hover:text-gray-800'"
                              @click="toggleStepViewMode(`input-${idx}`, 'json')"
                            >
                              JSON
                            </button>
                            <button
                              type="button"
                              class="px-1.5 py-0.5 rounded transition-colors"
                              :class="getStepViewMode(`input-${idx}`) === 'render'
                                ? 'bg-primary text-white font-medium shadow-sm'
                                : 'text-gray-500 hover:text-primary'"
                              @click="toggleStepViewMode(`input-${idx}`, 'render')"
                            >
                              渲染预览
                            </button>
                          </div>
                          <button
                            type="button"
                            class="text-[11px] text-gray-400 hover:text-primary transition-colors focus:outline-none"
                            @click="copyText(formatStepPayload(step.tool_input), '参数内容')"
                            title="复制原始参数"
                          >
                            复制
                          </button>
                        </div>
                      </div>

                      <div
                        v-if="getStepViewMode(`input-${idx}`) === 'render' && parsePayloadObject(step.tool_input).textContent"
                        class="markdown-body prose prose-sm max-w-none text-gray-800 break-words bg-white border border-gray-100 rounded-lg p-3.5"
                        v-html="renderPayloadMarkdown(parsePayloadObject(step.tool_input).textContent!)"
                      />
                      <pre
                        v-else
                        class="text-xs text-slate-100 leading-relaxed font-mono bg-[#0d1117] border border-gray-800 rounded-lg p-3.5 overflow-x-auto"
                        v-html="highlightJsonHtml(step.tool_input)"
                      />
                    </div>

                    <div v-if="step.tool_output" class="space-y-1.5">
                      <div class="flex items-center justify-between gap-2">
                        <label class="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                          Result / Answer
                        </label>
                        <div class="flex items-center gap-1.5">
                          <div
                            v-if="parsePayloadObject(step.tool_output).hasMarkdown"
                            class="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-[10px]"
                          >
                            <button
                              type="button"
                              class="px-1.5 py-0.5 rounded transition-colors"
                              :class="getStepViewMode(`output-${idx}`) === 'json'
                                ? 'bg-gray-800 text-white font-medium shadow-sm'
                                : 'text-gray-500 hover:text-gray-800'"
                              @click="toggleStepViewMode(`output-${idx}`, 'json')"
                            >
                              JSON
                            </button>
                            <button
                              type="button"
                              class="px-1.5 py-0.5 rounded transition-colors"
                              :class="getStepViewMode(`output-${idx}`) === 'render'
                                ? 'bg-primary text-white font-medium shadow-sm'
                                : 'text-gray-500 hover:text-primary'"
                              @click="toggleStepViewMode(`output-${idx}`, 'render')"
                            >
                              渲染预览
                            </button>
                          </div>
                          <button
                            type="button"
                            class="text-[11px] text-gray-400 hover:text-primary transition-colors focus:outline-none"
                            @click="copyText(formatStepPayload(step.tool_output), '返回结果')"
                            title="复制原始结果"
                          >
                            复制
                          </button>
                        </div>
                      </div>

                      <div
                        v-if="getStepViewMode(`output-${idx}`) === 'render' && parsePayloadObject(step.tool_output).textContent"
                        class="markdown-body prose prose-sm max-w-none text-gray-800 break-words bg-white border border-gray-100 rounded-lg p-3.5"
                        v-html="renderPayloadMarkdown(parsePayloadObject(step.tool_output).textContent!)"
                      />
                      <pre
                        v-else
                        class="text-xs text-slate-100 leading-relaxed font-mono bg-[#0d1117] border border-gray-800 rounded-lg p-3.5 overflow-x-auto"
                        v-html="highlightJsonHtml(step.tool_output)"
                      />
                    </div>
                    <div
                      v-if="step.error_message"
                      class="flex items-start gap-2 bg-red-50/60 border border-red-100 rounded-lg p-3 text-red-600"
                    >
                      <ExclamationCircleIcon class="w-4 h-4 shrink-0 mt-0.5" />
                      <p class="text-[11px] font-medium leading-relaxed">{{ step.error_message }}</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Tab 3: Context Compaction Timeline -->
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
          class="flex-1 flex flex-col items-center justify-center text-gray-400 px-6"
        >
          <div class="w-12 h-12 rounded-2xl bg-gray-50 border border-gray-100 flex items-center justify-center mb-3">
            <ChatBubbleLeftRightIcon class="w-6 h-6 text-gray-300" />
          </div>
          <p class="text-sm font-medium">从左侧选择一条会话</p>
          <p class="text-[11px] mt-1 text-center">列表只展示摘要，完整内容与链路在这里查看</p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 4px; height: 4px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #E5E7EB; border-radius: 2px; }
.custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #D1D5DB; }

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.markdown-body {
  font-size: 14px;
  line-height: 1.65;
  overflow-wrap: break-word;
}
.markdown-body :deep(p) { margin-bottom: 0.85em; }
.markdown-body :deep(p:last-child) { margin-bottom: 0; }
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-weight: 700;
  color: #111827;
  margin-top: 1.1em;
  margin-bottom: 0.5em;
  line-height: 1.35;
}
.markdown-body :deep(h1) { font-size: 1.35em; }
.markdown-body :deep(h2) { font-size: 1.2em; }
.markdown-body :deep(h3) { font-size: 1.05em; }
.markdown-body :deep(h1:first-child),
.markdown-body :deep(h2:first-child),
.markdown-body :deep(h3:first-child) { margin-top: 0; }
.markdown-body :deep(ul) {
  list-style-type: disc;
  padding-left: 1.4em;
  margin-bottom: 0.85em;
}
.markdown-body :deep(ol) {
  list-style-type: decimal;
  padding-left: 1.4em;
  margin-bottom: 0.85em;
}
.markdown-body :deep(li) { margin-bottom: 0.35em; }
.markdown-body :deep(strong) { font-weight: 700; color: #111827; }
.markdown-body :deep(a) { color: #2563eb; text-decoration: underline; }
.markdown-body :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  background-color: rgba(175, 184, 193, 0.22);
  padding: 0.15em 0.35em;
  border-radius: 4px;
  font-size: 85%;
  color: #b91c1c;
}
.markdown-body :deep(pre) {
  margin: 0.85em 0;
  padding: 0.85em 1em;
  background-color: #1e293b;
  border-radius: 8px;
  overflow: auto;
}
.markdown-body :deep(pre code) {
  background: transparent;
  color: #e2e8f0;
  padding: 0;
  font-size: 12.5px;
}
.markdown-body :deep(blockquote) {
  border-left: 3px solid #93c5fd;
  padding-left: 0.85em;
  color: #4b5563;
  margin: 0.85em 0;
}
.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.85em 0;
  font-size: 13px;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 0.4em 0.6em;
}
.markdown-body :deep(th) {
  background: #f8fafc;
  font-weight: 600;
}
</style>
