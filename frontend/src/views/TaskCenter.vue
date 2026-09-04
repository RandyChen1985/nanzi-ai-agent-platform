<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { taskApi, type AgentTask, type TaskLog, type TaskExecutionHistoryItem } from '../api/task'
import { agentApi, type AIAgent } from '../api/agent'
import Modal from '../components/Modal.vue'
import Toast from '../components/Toast.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import cronstrue from 'cronstrue/i18n'
import SessionTraceModal from '../components/SessionTraceModal.vue'
import axios from '../utils/axios'
import { 
  PlayCircleIcon,
  PauseCircleIcon
} from '@heroicons/vue/24/outline'
import { useRoute, useRouter } from 'vue-router'
import { formatInPlatformTimezoneCompact } from '@/utils/platformTimezone'
import TaskFlowGuideBanner from '@/components/task/TaskFlowGuideBanner.vue'
import TaskPromptComposer, {
  type TaskApprovalMode,
  type TaskResourceScope,
} from '@/components/task/TaskPromptComposer.vue'
import PromptAiOptimize from '@/components/PromptAiOptimize.vue'
import type { ReasoningEffort } from '@/api/model'

const emptyResourceScope = (): TaskResourceScope => ({
  project_name: '',
  datasets: [],
  knowledge_bases: [],
  skills: [],
  mcp_tools: [],
})

const taskModel = ref('')
const taskApprovalMode = ref<TaskApprovalMode>('allow')
const taskResourceScope = ref<TaskResourceScope>(emptyResourceScope())
const taskThinkingEnableOverride = ref<boolean | null>(null)
const taskReasoningEffortOverride = ref<ReasoningEffort | null>(null)
const taskMaxRetries = ref(0)
const taskRetryDelayMinutes = ref(5)

const MAX_TASK_RETRIES = 3
const MIN_RETRY_DELAY_MINUTES = 1
const MAX_RETRY_DELAY_MINUTES = 60

const clampNumber = (value: unknown, fallback: number, minimum: number, maximum: number) => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(maximum, Math.max(minimum, Math.trunc(parsed)))
}

const REASONING_EFFORT_VALUES = new Set<ReasoningEffort>([
  'none',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
])

const hydrateExecutionOptions = (config: Record<string, any> | undefined) => {
  const cfg = config && typeof config === 'object' ? config : {}
  taskModel.value = String(cfg.model || cfg.model_id || '')
  taskThinkingEnableOverride.value = typeof cfg.thinking_enable === 'boolean' ? cfg.thinking_enable : null
  taskReasoningEffortOverride.value = REASONING_EFFORT_VALUES.has(cfg.reasoning_effort)
    ? cfg.reasoning_effort
    : null
  if (taskThinkingEnableOverride.value === false) taskReasoningEffortOverride.value = null
  const mode = String(cfg.approval_mode || 'allow').toLowerCase()
  taskApprovalMode.value = mode === 'ask' || mode === 'deny' || mode === 'allow' ? mode : 'allow'
  taskMaxRetries.value = clampNumber(cfg.max_retries, 0, 0, MAX_TASK_RETRIES)
  const retryDelaySeconds = Number(cfg.retry_delay_seconds)
  taskRetryDelayMinutes.value = Number.isFinite(retryDelaySeconds)
    ? clampNumber(Math.round(retryDelaySeconds / 60), 5, MIN_RETRY_DELAY_MINUTES, MAX_RETRY_DELAY_MINUTES)
    : 5
  const scope = cfg.resource_scope && typeof cfg.resource_scope === 'object' ? cfg.resource_scope : {}
  taskResourceScope.value = {
    project_name: String(scope.project_name || ''),
    datasets: Array.isArray(scope.datasets) ? scope.datasets : [],
    knowledge_bases: Array.isArray(scope.knowledge_bases) ? scope.knowledge_bases : [],
    skills: Array.isArray(scope.skills) ? scope.skills : [],
    mcp_tools: Array.isArray(scope.mcp_tools) ? scope.mcp_tools : [],
  }
}

const handleTaskModelSelection = (model: string) => {
  taskModel.value = model
  taskThinkingEnableOverride.value = null
  taskReasoningEffortOverride.value = null
}

const props = withDefaults(defineProps<{
  /** 个人中心嵌入：管理自己的任务，不依赖 menu:task_center / element:task:manage */
  personalOnly?: boolean
  /**
   * Embed「我的资源」弹层传入（:embedded）：导航改 emit，不跳转 dashboard。
   * 与 DataPortalHome 的 delegateNavigation 同义；个人中心勿传（仅 personal-only）。
   */
  embedded?: boolean
  /** 优先于 route.query.view */
  initialView?: 'tasks' | 'history'
  /** 优先于 route.query.task_id */
  initialTaskId?: string | number
}>(), {
  personalOnly: false,
  embedded: false,
})

const emit = defineEmits<{
  (e: 'open-report', payload: {
    report_id: string
    run_id?: string
    detail_tab: 'runs' | 'subscription'
  }): void
}>()

const router = useRouter()
const route = useRoute()

// Auth & Permission
const cachedUser = localStorage.getItem('user_info')
const userInfo = ref(cachedUser ? JSON.parse(cachedUser) : null)
const isTaskOwner = (task: AgentTask) =>
  String(task.user_id) === String(userInfo.value?.user_id)
const canManage = computed(() => {
  if (props.personalOnly) return true
  if (!userInfo.value) return false
  if (userInfo.value.role === 'admin') return true
  const userElements = userInfo.value.permissions?.elements || []
  return userElements.includes('element:task:manage')
})
const canManageTask = (task: AgentTask) => {
  if (task.task_type === 'saved_report') return isTaskOwner(task)
  if (props.personalOnly) {
    return isTaskOwner(task) || userInfo.value?.role === 'admin'
  }
  return canManage.value
}
const showHistoryTab = computed(() => true)
const mainViewTab = ref<'tasks' | 'history'>('tasks')

// View & Filter States
const viewMode = ref<'grid' | 'list'>((localStorage.getItem('task_center_view_mode') as 'grid' | 'list') || 'grid')
const searchQuery = ref('')
const statusFilter = ref<'all' | 'running' | 'stopped'>('all')
const taskTypeFilter = ref<'all' | 'agent' | 'saved_report'>('all')
const taskTypeTabs = [
  { value: 'all' as const, label: '全部任务' },
  { value: 'agent' as const, label: '智能体任务' },
  { value: 'saved_report' as const, label: '报表订阅' },
]

// 执行记录（管理员看全部，普通用户仅看自己的）
const historyItems = ref<TaskExecutionHistoryItem[]>([])
const historyLoading = ref(false)
const historyPage = ref(1)
const historyTotal = ref(0)
const historyQ = ref('')
const historyStatus = ref('')
const historyTaskId = ref('')
const historyStartAt = ref('')
const historyEndAt = ref('')
const historyExpandedIds = ref<Set<number>>(new Set())
const historyHasMore = computed(() => historyItems.value.length < historyTotal.value)
const agentTasksForFilter = computed(() =>
  tasks.value.filter((task) => task.task_type !== 'saved_report' && task.source !== 'saved_report'),
)

let historyFilterTimer: number | undefined
const toApiDateTime = (value: string, endOfDay = false) => {
  const raw = String(value || '').trim()
  if (!raw) return undefined
  // datetime-local → 本地时间补秒，交给后端解析
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(raw)) {
    return `${raw}:${endOfDay ? '59' : '00'}`
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    return endOfDay ? `${raw}T23:59:59` : `${raw}T00:00:00`
  }
  return raw
}

const fetchExecutionHistory = async (reset = true) => {
  if (!showHistoryTab.value) return
  if (reset) {
    historyPage.value = 1
    historyItems.value = []
  }
  historyLoading.value = true
  try {
    const res = await taskApi.executionHistory({
      page: historyPage.value,
      page_size: 20,
      status: historyStatus.value || undefined,
      task_id: historyTaskId.value ? Number(historyTaskId.value) : undefined,
      q: historyQ.value.trim() || undefined,
      start_at: toApiDateTime(historyStartAt.value),
      end_at: toApiDateTime(historyEndAt.value, true),
    })
    const payload = res.data.data
    const items = payload?.items || []
    historyItems.value = reset ? items : [...historyItems.value, ...items]
    historyTotal.value = Number(payload?.total || 0)
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '获取执行记录失败', 'error')
  } finally {
    historyLoading.value = false
  }
}

const loadMoreHistory = () => {
  if (historyLoading.value || !historyHasMore.value) return
  historyPage.value += 1
  void fetchExecutionHistory(false)
}

const scheduleHistoryReload = () => {
  if (historyFilterTimer !== undefined) window.clearTimeout(historyFilterTimer)
  historyFilterTimer = window.setTimeout(() => {
    historyFilterTimer = undefined
    void fetchExecutionHistory(true)
  }, 300)
}

const toggleHistoryExpand = (id: number) => {
  const next = new Set(historyExpandedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  historyExpandedIds.value = next
}

const openTaskLogsFromHistory = (item: TaskExecutionHistoryItem) => {
  if (!item.task_id) {
    showToast('关联任务已删除，无法打开任务日志', 'error')
    return
  }
  const task = tasks.value.find((t) => t.id === item.task_id)
  if (task) {
    openLogs(task)
    return
  }
  showToast('任务不在当前列表中，请先刷新任务列表', 'error')
}

watch(mainViewTab, (tab) => {
  if (tab === 'history') void fetchExecutionHistory(true)
})

watch(
  [historyQ, historyStatus, historyTaskId, historyStartAt, historyEndAt],
  () => {
    if (mainViewTab.value === 'history') scheduleHistoryReload()
  },
)

watch(viewMode, (newMode) => {
  localStorage.setItem('task_center_view_mode', newMode)
})

const tasks = ref<AgentTask[]>([])
const agents = ref<AIAgent[]>([])
const showAgentDropdown = ref(false)
const agentDropdownRef = ref<HTMLElement | null>(null)
const selectedEditingAgent = computed(() =>
  agents.value.find((agent) => agent.id === editingTask.value.agent_id) || null
)
const isAgentAvatarUrl = (url?: string) =>
  Boolean(url && (url.startsWith('http') || url.startsWith('/') || url.startsWith('data:')))
const selectEditingAgent = (agentId: string) => {
  editingTask.value.agent_id = agentId
  showAgentDropdown.value = false
}
const handleAgentDropdownOutsideClick = (e: MouseEvent) => {
  if (agentDropdownRef.value && !agentDropdownRef.value.contains(e.target as Node)) {
    showAgentDropdown.value = false
  }
}
const loading = ref(false)
const showEditModal = ref(false)
const showPromptHelpModal = ref(false)
const showLogsDrawer = ref(false)
const editingTask = ref<Partial<AgentTask>>({})
const notificationChannelOptions = [
  { value: 'portal', label: '站内消息' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'wechat_work', label: '企业微信' },
  { value: 'email', label: '邮件' },
] as const
const notificationChannels = ref<string[]>(['portal'])
const personalNotificationConfigs = ref<Record<string, any>>({})
const personalNotificationLoading = ref(false)
const promptOverlapsNotificationChannels = computed(() => {
  const prompt = String(editingTask.value.prompt || '').toLowerCase()
  if (!prompt || !notificationChannels.value.length) return false
  const hints: Record<string, string[]> = {
    portal: ['站内', '铃铛', 'inbox', '门户消息', '消息中心'],
    dingtalk: ['钉钉', 'dingtalk'],
    wechat_work: ['企微', '企业微信', 'wechat'],
    email: ['邮件', '邮箱', 'email', 'smtp'],
  }
  return notificationChannels.value.some((channel) =>
    (hints[channel] || []).some((term) => prompt.includes(term.toLowerCase()))
  )
})
const isNotificationChannelReady = (channel: string) => {
  if (channel === 'portal') return true
  const cfg = personalNotificationConfigs.value[channel]
  if (!cfg || !cfg.is_enabled) return false
  if (channel === 'dingtalk' || channel === 'wechat_work') {
    return Boolean(String(cfg.webhook_url || '').trim())
  }
  if (channel === 'email') {
    return Boolean(String(cfg.smtp_host || '').trim() && String(cfg.smtp_user || '').trim())
  }
  return false
}
const unavailableExternalChannels = computed(() =>
  notificationChannelOptions
    .filter((c) => c.value !== 'portal' && !isNotificationChannelReady(c.value))
    .map((c) => c.label)
)
const pruneUnavailableNotificationChannels = () => {
  notificationChannels.value = notificationChannels.value.filter((channel) =>
    isNotificationChannelReady(channel)
  )
}
const fetchPersonalNotificationConfigs = async () => {
  personalNotificationLoading.value = true
  try {
    const res = await axios.get('/api/portal/notifications/config')
    personalNotificationConfigs.value = res.data || {}
    pruneUnavailableNotificationChannels()
  } catch (error) {
    console.warn('Failed to load personal notification configs', error)
    personalNotificationConfigs.value = {}
  } finally {
    personalNotificationLoading.value = false
  }
}
const openPersonalNotificationSettings = () => {
  if (props.embedded) return
  router.push({ path: '/dashboard/personal', query: { tab: 'notifications' } })
}
const promptExamples = [
  {
    title: '天气与环境巡检',
    tip: '适合工具查询 + 下方勾选站内消息（不必在指令里写 send_portal_notification）',
    text: `1. 使用 get_current_weather 工具查询指定城市（如 Shanghai、Beijing）的实时天气与温度。
2. 整理成简洁 Markdown 报告：城市、天气概况、当前气温与风向。
3. 将报告作为本次任务结果输出。`,
  },
  {
    title: '机房巡检简报',
    tip: '查数类任务写清对象、时间范围与输出格式即可',
    text: `查询华东一号机房昨天的 PUE 峰值与均值，并列出告警次数 Top3。
用简洁 Markdown 输出：核心结论 + 关键指标表格。`,
  },
  {
    title: '仅业务指令（推荐）',
    tip: '通知渠道请用下方「结果通知」勾选，执行指令专注业务本身',
    text: `汇总本周任务失败次数，按失败原因分组，给出可执行的改进建议（3 条以内）。`,
  },
] as const
const applyPromptExample = (text: string) => {
  editingTask.value.prompt = text
  showPromptHelpModal.value = false
  showToast('已填入示例，可按需修改')
}
const promptTemplates = [
  {
    title: '通用结构',
    desc: '目标 / 范围 / 输出要求四段骨架',
    text: `【任务目标】____（要做什么，如：查询 / 统计 / 汇总…）
【数据范围】时间：____（如：昨天 / 本周）；对象：____（如：某机房 / 某项目）
【输出要求】____（如：Markdown 输出，核心结论 + 关键指标表格）
【其他说明】____（可选，如：超过阈值请重点标注；不需要可删除本行）`,
  },
  {
    title: '数据查询 / 报表',
    desc: '查数出报表，含环比对比',
    text: `查询 ____（对象，如：华东一号机房）在 ____（时间范围，如：昨天）的 ____（指标，如：PUE 峰值与均值）。
用 Markdown 输出：核心结论先行 + 关键指标表格，并与上一周期对比说明变化。`,
  },
  {
    title: '监控巡检 / 告警',
    desc: '异常显著标注，正常一句话简报',
    text: `检查 ____（对象）在 ____（时间范围）内的 ____（指标 / 告警）情况。
若 ____（异常条件，如：超过阈值 / 出现告警），请在结果开头用「【异常】」显著标注并给出原因分析；一切正常则只输出一句话简报。`,
  },
  {
    title: '定期汇总简报',
    desc: '周报 / 月报式结构化汇总',
    text: `汇总 ____（时间范围，如：本周）的 ____（内容，如：任务执行情况 / 项目进展）。
按「总体结论 → 分项明细 → 风险与建议（3 条以内）」结构输出，保持简洁。`,
  },
] as const
const showPromptTemplateDropdown = ref(false)
const promptTemplateDropdownRef = ref<HTMLElement | null>(null)
const handlePromptTemplateOutsideClick = (e: MouseEvent) => {
  if (promptTemplateDropdownRef.value && !promptTemplateDropdownRef.value.contains(e.target as Node)) {
    showPromptTemplateDropdown.value = false
  }
}
const applyPromptTemplate = (text: string) => {
  editingTask.value.prompt = text
  showPromptTemplateDropdown.value = false
  showToast('已插入模板，骨架仅供参考，可自由增删')
}
const clearTaskPrompt = () => {
  editingTask.value.prompt = ''
  showToast('已清空执行指令')
}
const applyOptimizedPrompt = (content: string) => {
  editingTask.value.prompt = content
}
const onOptimizeToast = (message: string, type?: 'success' | 'error' | 'info') => {
  showToast(message, type === 'error' ? 'error' : 'success')
}
const selectedTask = ref<AgentTask | null>(null)
const logs = ref<TaskLog[]>([])
const logsLoading = ref(false)
const logsPage = ref(1)
const logsTotal = ref(0)
const logsHasMore = computed(() => logs.value.length < logsTotal.value)
const runningTaskIds = ref(new Set<number>())
const showSpecsModal = ref(false)
const TASK_FLOW_GUIDE_KEY = 'nanzi_task_flow_guide_dismissed'
const showTaskFlowGuide = ref(localStorage.getItem(TASK_FLOW_GUIDE_KEY) !== 'true')

const handleCloseTaskFlowGuide = () => {
  showTaskFlowGuide.value = false
}

const handleDismissTaskFlowGuide = () => {
  showTaskFlowGuide.value = false
  localStorage.setItem(TASK_FLOW_GUIDE_KEY, 'true')
}

const restoreTaskFlowGuide = () => {
  localStorage.removeItem(TASK_FLOW_GUIDE_KEY)
  showTaskFlowGuide.value = true
}

const activeSpecsTab = ref<'flow' | 'context' | 'approval'>('flow')

const handleTaskBannerAction = (type: 'create' | 'history') => {
  if (type === 'create') {
    openCreateModal()
  } else if (type === 'history') {
    mainViewTab.value = 'history'
  }
}

// Mobile State
const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
const currentViewMode = computed(() => isMobile.value ? 'grid' : viewMode.value)

const handleWindowResize = () => {
  windowWidth.value = window.innerWidth
}

/** runTaskNow 轮询相关定时器；卸载时统一清理，避免页面切走后仍叠加回调 */
const runNowTimerIds = new Set<number>()

const trackRunNowTimer = (id: number) => {
  runNowTimerIds.add(id)
  return id
}

const releaseRunNowTimer = (id: number) => {
  window.clearInterval(id)
  window.clearTimeout(id)
  runNowTimerIds.delete(id)
}

const clearRunNowTimers = () => {
  for (const id of [...runNowTimerIds]) {
    releaseRunNowTimer(id)
  }
}

onMounted(() => {
  window.addEventListener('resize', handleWindowResize)
  document.addEventListener('click', handleAgentDropdownOutsideClick)
  document.addEventListener('click', handlePromptTemplateOutsideClick)
})
onUnmounted(() => {
  if (historyFilterTimer !== undefined) window.clearTimeout(historyFilterTimer)
  clearRunNowTimers()
  window.removeEventListener('resize', handleWindowResize)
  document.removeEventListener('click', handleAgentDropdownOutsideClick)
  document.removeEventListener('click', handlePromptTemplateOutsideClick)
})

// Cron Builder Logic
const cronMode = ref<'daily' | 'weekly' | 'monthly' | 'interval' | 'custom'>('daily')
const cronConfig = ref({
  time: '08:00',
  weekday: 1,
  day: 1,
  intervalValue: 30,
  intervalUnit: 'minutes' as 'minutes' | 'hours'
})

// Cron Sync: UI -> Expression
watch([cronMode, cronConfig], () => {
  if (!editingTask.value) return
  const { time, weekday, day, intervalValue, intervalUnit } = cronConfig.value
  
  if (cronMode.value === 'custom') return // Don't overwrite if custom

  try {
      let expr = ''
      const [h, m] = (time || '00:00').split(':').map(Number)
      // 若 time 被清空或格式非法，h/m 可能是 NaN，直接 bail out
      if (isNaN(h) || isNaN(m)) {
        console.warn('Invalid time value, skipping cron build')
        return
      }
      
      switch (cronMode.value) {
        case 'daily':
          expr = `${m} ${h} * * *`
          break
        case 'weekly':
          expr = `${m} ${h} * * ${weekday}`
          break
        case 'monthly': {
          const safeDay = Math.min(31, Math.max(1, Math.floor(day) || 1))
          expr = `${m} ${h} ${safeDay} * *`
          break
        }
        case 'interval': {
          const val = Math.max(1, Math.floor(intervalValue || 1))
          if (intervalUnit === 'minutes') {
             const safeVal = Math.min(val, 59)
             expr = `*/${safeVal} * * * *`
          } else {
             const safeVal = Math.min(val, 23)
             expr = `0 */${safeVal} * * *`
          }
          break
        }
      }
      editingTask.value.cron_expr = expr
  } catch (e) {
      console.warn('Cron build error', e)
  }
}, { deep: true })

// Cron Sync: Expression -> UI (On Edit)
const parseCronToUI = (expr: string) => {
    if (!expr) return
    const parts = expr.split(' ')
    if (parts.length < 5) { cronMode.value = 'custom'; return }
    const min = parts[0] || '*'
    const hour = parts[1] || '*'
    const dom = parts[2] || '*'
    const mon = parts[3] || '*'
    const dow = parts[4] || '*'
    
    // Interval Check
    if (min.startsWith('*/') && hour === '*' && dom === '*') {
        cronMode.value = 'interval'
        cronConfig.value.intervalUnit = 'minutes'
        cronConfig.value.intervalValue = parseInt(min.replace('*/', '')) || 1
        return
    }
    if (min === '0' && hour.startsWith('*/') && dom === '*') {
        cronMode.value = 'interval'
        cronConfig.value.intervalUnit = 'hours'
        cronConfig.value.intervalValue = parseInt(hour.replace('*/', '')) || 1
        return
    }

    // Standard Check: hour 和 min 必须是纯数字，否则无法回显到 time picker
    const hourNum = parseInt(hour, 10)
    const minNum = parseInt(min, 10)
    if (isNaN(hourNum) || isNaN(minNum)) {
        cronMode.value = 'custom'
        return
    }
    const timeStr = `${String(hourNum).padStart(2, '0')}:${String(minNum).padStart(2, '0')}`
    if (dom === '*' && mon === '*' && dow === '*') {
        cronMode.value = 'daily'
        cronConfig.value.time = timeStr
    } else if (dom === '*' && mon === '*' && dow !== '*') {
        cronMode.value = 'weekly'
        cronConfig.value.time = timeStr
        cronConfig.value.weekday = parseInt(dow) || 0
    } else if (dom !== '*' && mon === '*' && dow === '*') {
        cronMode.value = 'monthly'
        cronConfig.value.time = timeStr
        cronConfig.value.day = parseInt(dom) || 1
    } else {
        cronMode.value = 'custom'
    }
}


const cronDescription = computed(() => {
  if (!editingTask.value.cron_expr || editingTask.value.cron_expr.includes('NaN')) return '请输入有效的 Cron 表达式'
  try {
    return cronstrue.toString(editingTask.value.cron_expr, { locale: 'zh_CN' })
  } catch (e) {
    return '表达式格式不正确'
  }
})

// Filtered Tasks
const scopedTasks = computed(() => {
  if (props.personalOnly) {
    return tasks.value.filter((task) => isTaskOwner(task))
  }
  return tasks.value
})

const taskTypeCounts = computed(() => ({
  all: scopedTasks.value.length,
  agent: scopedTasks.value.filter(task => task.task_type !== 'saved_report').length,
  saved_report: scopedTasks.value.filter(task => task.task_type === 'saved_report').length,
}))

const filteredTasks = computed(() => {
  let result = [...scopedTasks.value]
  if (taskTypeFilter.value === 'agent') {
    result = result.filter(task => task.task_type !== 'saved_report')
  } else if (taskTypeFilter.value === 'saved_report') {
    result = result.filter(task => task.task_type === 'saved_report')
  }
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    result = result.filter(t => t.name.toLowerCase().includes(q) || t.prompt.toLowerCase().includes(q))
  }
  if (statusFilter.value !== 'all') {
    const isRunning = statusFilter.value === 'running'
    result = result.filter(t => t.status === (isRunning ? 1 : 0))
  }
  return result
})

const toastState = ref({ show: false, message: '', type: 'success' as any })
const showToast = (message: string, type: 'success' | 'error' | 'warning' = 'success') => {
  toastState.value = { show: true, message, type }
}

const confirmState = ref({ show: false, title: '', message: '', type: 'danger' as any, onConfirm: () => {} })

const fetchTasks = async (isSilent = false) => {
  if (!isSilent) loading.value = true
  try {
    const [agentRes, reportRes] = await Promise.all([taskApi.list(), taskApi.listReportSubscriptions()])
    tasks.value = [...(agentRes.data.data || []), ...(reportRes.data.data || [])]
  } catch (e) {
    if (!isSilent) showToast('获取任务列表失败', 'error')
    console.error('Failed to fetch tasks', e)
  } finally {
    loading.value = false
  }
}

const fetchAgents = async () => {
  try {
    const res = await agentApi.listAgents()
    agents.value = res.data
  } catch (e) { 
    console.error('Failed to fetch agents', e) 
  }
}

const openCreateModal = async () => {
  editingTask.value = { name: '', agent_id: agents.value[0]?.id || '', cron_expr: '0 8 * * *', prompt: '', status: 1 }
  notificationChannels.value = ['portal']
  hydrateExecutionOptions({})
  showAgentDropdown.value = false
  cronMode.value = 'daily'
  cronConfig.value = { time: '08:00', weekday: 1, day: 1, intervalValue: 30, intervalUnit: 'minutes' }
  showEditModal.value = true
  await fetchPersonalNotificationConfigs()
}

const openEditModal = async (task: AgentTask) => {
  if (task.task_type === 'saved_report') {
    openSavedReportSubscriptionSettings(task)
    return
  }
  editingTask.value = { ...task }
  const cfg = task.config && typeof task.config === 'object' ? task.config : {}
  notificationChannels.value = Array.isArray(cfg.notification_channels)
    ? cfg.notification_channels.map((c: string) => String(c))
    : []
  hydrateExecutionOptions(cfg)
  showAgentDropdown.value = false
  parseCronToUI(task.cron_expr || '')
  showEditModal.value = true
  await fetchPersonalNotificationConfigs()
}

const getTaskEditTitle = (task: AgentTask) => (
  task.task_type === 'saved_report' ? '订阅设置' : '编辑'
)


const saveTask = async () => {
  try {
    pruneUnavailableNotificationChannels()
    const baseConfig =
      editingTask.value.config && typeof editingTask.value.config === 'object'
        ? { ...editingTask.value.config }
        : {}
    if (notificationChannels.value.length) {
      baseConfig.notification_channels = [...notificationChannels.value]
    } else {
      delete baseConfig.notification_channels
    }
    baseConfig.approval_mode = taskApprovalMode.value
    if (taskModel.value) baseConfig.model = taskModel.value
    else {
      delete baseConfig.model
      delete baseConfig.model_id
    }
    if (taskThinkingEnableOverride.value !== null) {
      baseConfig.thinking_enable = taskThinkingEnableOverride.value
    } else {
      delete baseConfig.thinking_enable
    }
    if (taskReasoningEffortOverride.value !== null && taskThinkingEnableOverride.value !== false) {
      baseConfig.reasoning_effort = taskReasoningEffortOverride.value
    } else {
      delete baseConfig.reasoning_effort
    }
    baseConfig.max_retries = clampNumber(taskMaxRetries.value, 0, 0, MAX_TASK_RETRIES)
    baseConfig.retry_delay_seconds = clampNumber(
      taskRetryDelayMinutes.value,
      5,
      MIN_RETRY_DELAY_MINUTES,
      MAX_RETRY_DELAY_MINUTES,
    ) * 60
    const scope = taskResourceScope.value || emptyResourceScope()
    const hasScope = Boolean(
      (scope.datasets || []).length ||
      (scope.knowledge_bases || []).length ||
      (scope.skills || []).length ||
      (scope.mcp_tools || []).length ||
      scope.project_name,
    )
    if (hasScope) {
      baseConfig.resource_scope = {
        project_name: scope.project_name || '',
        datasets: scope.datasets || [],
        knowledge_bases: scope.knowledge_bases || [],
        skills: scope.skills || [],
        mcp_tools: scope.mcp_tools || [],
      }
    } else {
      delete baseConfig.resource_scope
    }
    const payload = { ...editingTask.value, config: baseConfig }
    if (editingTask.value.id) {
      await taskApi.update(editingTask.value.id, payload)
      showToast('更新成功')
    } else {
      await taskApi.create(payload)
      showToast('创建成功')
    }
    showEditModal.value = false
    fetchTasks(true)
  } catch (e: any) {
    showToast(e.response?.data?.message || '保存失败', 'error')
  }
}

const toggleStatus = (task: AgentTask) => {
  const isRunning = task.status === 1
  confirmState.value = {
    show: true,
    title: isRunning ? '暂停任务' : '启动任务',
    message: `确定要${isRunning ? '停止' : '激活'}任务 "${task.name}" 吗？`,
    type: isRunning ? 'warning' : 'primary',
    onConfirm: async () => {
      try {
        const newStatus = isRunning ? 0 : 1
        if (task.task_type === 'saved_report') await taskApi.updateReportSubscriptionStatus(task.subscription_id!, newStatus === 1)
        else await taskApi.update(task.id, { status: newStatus })
        showToast(newStatus === 1 ? '任务已启动' : '任务已停止')
        fetchTasks(true)
        confirmState.value.show = false
      } catch (e) {
        showToast('操作失败', 'error')
      }
    }
  }
}

const deleteTask = (task: AgentTask) => {
  confirmState.value = {
    show: true,
    title: '确认删除',
    message: `确定要删除任务 "${task.name}" 吗？此操作不可恢复。`,
    type: 'danger',
    onConfirm: async () => {
      try {
        if (task.task_type === 'saved_report') await taskApi.deleteReportSubscription(task.subscription_id!)
        else await taskApi.delete(task.id)
        showToast('删除成功')
        fetchTasks(true)
        confirmState.value.show = false
      } catch (e) {
        showToast('删除失败', 'error')
      }
    }
  }
}

const runTaskNow = async (task: AgentTask) => {
  if (runningTaskIds.value.has(task.id)) return
  runningTaskIds.value.add(task.id)
  try {
    if (task.task_type === 'saved_report') await taskApi.runReportSubscription(task.subscription_id!)
    else await taskApi.run(task.id)
    showToast(`任务 已发送触发指令`, 'success')
    // Poll for status update for 5 seconds
    let attempts = 0
    const poll = trackRunNowTimer(window.setInterval(async () => {
        attempts++
        if (attempts > 5) {
          releaseRunNowTimer(poll)
          runningTaskIds.value.delete(task.id)
          return
        }
        await fetchTasks(true)
    }, 1000))
    const pollTimeout = trackRunNowTimer(window.setTimeout(() => {
      releaseRunNowTimer(poll)
      releaseRunNowTimer(pollTimeout)
      runningTaskIds.value.delete(task.id)
    }, 5000))
  } catch (e) {
    showToast('触发失败', 'error')
    runningTaskIds.value.delete(task.id)
  }
}


const openLogs = async (task: AgentTask) => {
  if (task.task_type === 'saved_report') {
    openSavedReportTask(task, 'runs')
    return
  }
  selectedTask.value = task; logsPage.value = 1; logs.value = []; showLogsDrawer.value = true; fetchLogs()
}

const openSavedReportTask = async (
  task: AgentTask,
  detailTab: 'runs' | 'subscription' = 'runs',
) => {
  const reportId = String(task.report_id || '')
  const runId = detailTab === 'runs' && task.last_run_id ? String(task.last_run_id) : undefined
  if (props.embedded) {
    emit('open-report', {
      report_id: reportId,
      ...(runId ? { run_id: runId } : {}),
      detail_tab: detailTab,
    })
    return
  }
  const query: Record<string, string> = {
    dataset_portal: '1',
    report_id: reportId,
    report_detail_tab: detailTab,
  }
  if (runId) {
    query.run_id = runId
  }
  await router.push({ path: '/dashboard/chat', query })
}

const openSavedReportSubscriptionSettings = (task: AgentTask) => {
  openSavedReportTask(task, 'subscription')
}

const fetchLogs = async (append = false) => {
  if (!selectedTask.value) return
  logsLoading.value = true
  try {
    const res = await taskApi.logs(selectedTask.value.id, { page: logsPage.value, page_size: 10 })
    const newItems = (res.data.data.items || []).map((item: any) => ({
        ...item,
        isExpanded: false,
        steps: [],
        stepsLoading: false
    }))
    
    if (append) {
        logs.value = [...logs.value, ...newItems]
    } else {
        logs.value = newItems
    }
    logsTotal.value = res.data.data.total
  } catch (e) { showToast('获取日志失败', 'error') } finally { logsLoading.value = false }
}

const toggleLogSteps = async (log: any) => {
    log.isExpanded = !log.isExpanded
    if (log.isExpanded && (!log.steps || log.steps.length === 0)) {
        log.stepsLoading = true
        try {
            const res = await agentApi.getChatTrace(log.trace_id)
            if (res.data?.data?.steps) {
                log.steps = res.data.data.steps
            }
        } catch (e) {
            console.error('Failed to fetch steps', e)
        } finally {
            log.stepsLoading = false
        }
    }
}

const loadMoreLogs = () => {
    logsPage.value++
    fetchLogs(true)
}


// Detail State for Trace
const selectedTraceId = ref<string | null>(null)
const showSessionModal = ref(false) // This controls SessionTraceModal
const sessionTurns = ref<any[]>([])
const sessionLoading = ref(false)

const viewTrace = async (traceId: string) => {
  selectedTraceId.value = traceId
  showSessionModal.value = true
  sessionLoading.value = true
  sessionTurns.value = []
  
  try {
    // 1. Get Log Detail
    const res = await agentApi.getChatTrace(traceId)
    const traceData = res.data.data
    
    // 2. Wrap as a single turn session (Task execution is usually single turn)
    // But we use the rich structure
    sessionTurns.value = [{
        ...traceData.history,
        steps: traceData.steps || [],
        isExpanded: true, // Auto expand for single task trace
        trace_id: traceId
    }]
  } catch (e) {
    console.error('Failed to load trace', e)
    showToast('加载详情失败', 'error')
  } finally {
    sessionLoading.value = false
  }
}

const toggleSessionStep = async (turn: any) => {
    turn.isExpanded = !turn.isExpanded
}

const formatDate = (d: string | undefined) => {
  if (!d) return '从未执行'
  return formatInPlatformTimezoneCompact(d)
}

/** 耗时：毫秒 →「x分x秒」；不足 1 秒显示「x秒」或「不足1秒」 */
const formatDurationMs = (ms: number | null | undefined) => {
  if (ms == null || Number.isNaN(Number(ms))) return '—'
  const totalMs = Math.max(0, Math.round(Number(ms)))
  if (totalMs < 1000) return totalMs === 0 ? '0秒' : `${(totalMs / 1000).toFixed(1)}秒`
  const totalSec = Math.floor(totalMs / 1000)
  const minutes = Math.floor(totalSec / 60)
  const seconds = totalSec % 60
  if (minutes <= 0) return `${seconds}秒`
  return `${minutes}分${seconds}秒`
}

const formatNextRunCompact = (d: string | undefined) => {
  if (!d) return '暂无计划'
  return formatInPlatformTimezoneCompact(d)
}

const formatTaskSchedule = (cron: string) => {
  const parts = String(cron || '').trim().split(/\s+/)
  if (parts.length !== 5) return cron || '未配置'
  const [minute, hour, day, month, weekday] = parts
  const time = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
  const fixedTime = /^\d+$/.test(String(hour)) && /^\d+$/.test(String(minute))
  if (fixedTime && day === '*' && month === '*' && weekday === '*') return `每天 ${time}`
  if (fixedTime && day === '*' && month === '*' && weekday !== '*') {
    const weekLabels = ['日', '一', '二', '三', '四', '五', '六']
    return `每周${weekLabels[Number(weekday)] ?? weekday} ${time}`
  }
  if (fixedTime && day !== '*' && month === '*' && weekday === '*') return `每月${day}日 ${time}`
  try { return cronstrue.toString(cron, { locale: 'zh_CN' }) } catch { return cron }
}

const taskHealthMeta = (task: AgentTask) => {
  const status = task.health_status || 'unknown'
  if (status === 'healthy') {
    return { label: '健康', class: 'bg-green-50 text-green-700 border-green-100', dot: 'bg-green-500' }
  }
  if (status === 'warning') {
    return { label: '需关注', class: 'bg-amber-50 text-amber-700 border-amber-100', dot: 'bg-amber-500' }
  }
  if (status === 'error') {
    return { label: '异常', class: 'bg-red-50 text-red-700 border-red-100', dot: 'bg-red-500' }
  }
  if (status === 'skipped') {
    return { label: '已跳过', class: 'bg-slate-50 text-slate-600 border-slate-100', dot: 'bg-slate-400' }
  }
  return { label: '未运行', class: 'bg-gray-50 text-gray-500 border-gray-100', dot: 'bg-gray-300' }
}

const logStatusMeta = (status: string | undefined) => {
  const value = String(status || '').toLowerCase()
  if (value === 'success') {
    return { label: '成功', class: 'bg-green-100 text-green-700' }
  }
  if (value === 'awaiting_permission') {
    return { label: '待确认', class: 'bg-amber-100 text-amber-700' }
  }
  if (value === 'awaiting_external_execution') {
    return { label: '待外部执行', class: 'bg-amber-100 text-amber-700' }
  }
  if (value === 'no_tool_execution') {
    return { label: '未调用工具', class: 'bg-red-100 text-red-700' }
  }
  if (value === 'rejected' || value === 'denied') {
    return { label: '已拒绝', class: 'bg-slate-100 text-slate-600' }
  }
  return { label: '失败', class: 'bg-red-100 text-red-700' }
}

const metricValue = (value: number | undefined) => Number(value || 0)

onMounted(async () => {
  await Promise.all([fetchTasks(true), fetchAgents()])
  const view = props.initialView || String(route.query.view || '')
  if (view === 'history' && showHistoryTab.value) {
    mainViewTab.value = 'history'
  }
  const taskId = props.initialTaskId ?? route.query.task_id
  if (taskId) {
    const target = tasks.value.find(task => String(task.id) === String(taskId))
    if (target) openLogs(target)
  }
})
</script>

<template>
  <div class="space-y-5">
    <!-- Header：标题一行；窄屏搜索通栏，状态+刷新并排，新建通栏 -->
    <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div class="flex items-center space-x-3">
        <h1
          class="font-bold text-gray-900"
          :class="personalOnly ? 'text-xl' : 'text-xl sm:text-2xl'"
        >
          {{ personalOnly ? '我的任务' : '任务调度台' }}
        </h1>
        <span
          v-if="personalOnly"
          class="inline-flex items-center rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700"
        >
          个人私有
        </span>
        <button
          v-if="!personalOnly"
          type="button"
          class="flex h-7 w-7 items-center justify-center rounded-full border border-gray-200 bg-white text-orange-600 shadow-sm transition-colors hover:border-orange-300 hover:bg-orange-50 cursor-pointer"
          title="任务调度设计规范与全流程指引"
          @click="showSpecsModal = true"
        >
          <span class="text-sm font-bold">?</span>
        </button>

        <button
          v-if="!showTaskFlowGuide && !personalOnly"
          type="button"
          class="inline-flex items-center gap-1 rounded-full border border-orange-200 bg-orange-50/80 px-2.5 py-1 text-xs font-medium text-orange-700 shadow-2xs transition-colors hover:bg-orange-100 cursor-pointer"
          title="重新展开任务调度全流程指引"
          @click="restoreTaskFlowGuide"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span class="whitespace-nowrap">显示指引</span>
        </button>
      </div>

      <div v-if="mainViewTab === 'tasks'" class="flex w-full flex-col gap-2.5 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center sm:gap-3 lg:justify-end">
        <div class="relative w-full sm:w-56 lg:w-64">
          <span class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </span>
          <input
            v-model="searchQuery"
            type="search"
            placeholder="搜索任务名称或指令..."
            class="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm shadow-sm outline-none transition-all focus:border-primary focus:ring-2 focus:ring-primary/20"
          />
        </div>

        <div class="flex items-center gap-2">
          <select
            v-model="statusFilter"
            class="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 sm:w-auto sm:flex-none"
            title="按状态筛选"
          >
            <option value="all">状态：全部</option>
            <option value="running">状态：运行中</option>
            <option value="stopped">状态：已停止</option>
          </select>

          <div class="hidden shrink-0 select-none items-center gap-0.5 rounded-lg border border-gray-300 bg-gray-200/60 p-0.5 md:flex">
            <button
              type="button"
              class="rounded-md p-1.5 transition-all"
              :class="currentViewMode === 'grid' ? 'border border-gray-200 bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-800'"
              title="网格视图"
              @click="viewMode = 'grid'"
            >
              <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
              </svg>
            </button>
            <button
              type="button"
              class="rounded-md p-1.5 transition-all"
              :class="currentViewMode === 'list' ? 'border border-gray-200 bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-800'"
              title="列表视图"
              @click="viewMode = 'list'"
            >
              <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
          </div>

          <button
            type="button"
            class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-500 shadow-sm transition-colors hover:bg-gray-50 hover:text-primary"
            title="刷新列表"
            @click="fetchTasks(false)"
          >
            <svg class="h-4 w-4" :class="loading ? 'animate-spin' : ''" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
        </div>

        <button
          v-if="canManage"
          type="button"
          class="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-primary-dark sm:w-auto"
          @click="openCreateModal"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
          </svg>
          <span class="hidden sm:inline">新建任务</span>
          <span class="sm:hidden">新建</span>
        </button>
      </div>
    </div>

    <!-- 任务调度 5 步全生命周期指引横幅 -->
    <div v-if="showTaskFlowGuide && !personalOnly" class="flex-shrink-0">
      <TaskFlowGuideBanner
        @action="handleTaskBannerAction"
        @close="handleCloseTaskFlowGuide"
        @dismiss="handleDismissTaskFlowGuide"
      />
    </div>

    <!-- 主 Tab：管理员为「任务列表 | 执行记录」；否则直接显示类型筛选 -->
    <div class="border-b border-gray-200 -mt-1">
      <div class="flex gap-1 overflow-x-auto -mb-px" style="-webkit-overflow-scrolling: touch;">
        <template v-if="showHistoryTab">
          <button
            type="button"
            class="inline-flex shrink-0 items-center gap-1.5 px-3 sm:px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap"
            :class="mainViewTab === 'tasks' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
            @click="mainViewTab = 'tasks'"
          >任务列表</button>
          <button
            type="button"
            class="inline-flex shrink-0 items-center gap-1.5 px-3 sm:px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap"
            :class="mainViewTab === 'history' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
            @click="mainViewTab = 'history'"
          >执行记录</button>
        </template>
        <template v-else>
          <button
            v-for="tab in taskTypeTabs"
            :key="tab.value"
            type="button"
            class="inline-flex shrink-0 items-center gap-1.5 px-3 sm:px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap"
            :class="taskTypeFilter === tab.value ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
            @click="taskTypeFilter = tab.value"
          >
            <span>{{ tab.label }}</span>
            <span
              class="min-w-5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold text-center"
              :class="taskTypeFilter === tab.value ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'"
            >
              {{ taskTypeCounts[tab.value] }}
            </span>
          </button>
        </template>
      </div>
    </div>

    <!-- 任务列表下的类型二级筛选（仅管理员主 Tab 模式下需要） -->
    <div
      v-if="showHistoryTab && mainViewTab === 'tasks'"
      class="flex gap-1.5 overflow-x-auto -mt-1 pt-2"
      style="-webkit-overflow-scrolling: touch;"
    >
      <button
        v-for="tab in taskTypeTabs"
        :key="'sub-' + tab.value"
        type="button"
        class="inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold transition-colors whitespace-nowrap"
        :class="taskTypeFilter === tab.value
          ? 'border-blue-200 bg-blue-50 text-blue-700'
          : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-gray-700'"
        @click="taskTypeFilter = tab.value"
      >
        <span>{{ tab.label }}</span>
        <span
          class="min-w-4 rounded-full px-1 text-[10px]"
          :class="taskTypeFilter === tab.value ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'"
        >{{ taskTypeCounts[tab.value] }}</span>
      </button>
    </div>

    <!-- 管理员全局执行记录 -->
    <div v-if="mainViewTab === 'history' && showHistoryTab" class="space-y-4">
      <div class="flex flex-col gap-2.5 rounded-xl border border-gray-200 bg-white p-3 shadow-sm lg:flex-row lg:flex-wrap lg:items-center">
        <div class="relative min-w-0 flex-1 lg:max-w-xs">
          <input
            v-model="historyQ"
            type="search"
            placeholder="搜索任务名 / 创建人 / 摘要 / Trace..."
            class="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
          />
        </div>
        <select
          v-model="historyStatus"
          class="rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm outline-none focus:border-primary"
        >
          <option value="">状态：全部</option>
          <option value="success">成功</option>
          <option value="failed">失败</option>
          <option value="awaiting_permission">待确认</option>
          <option value="awaiting_external_execution">待外部执行</option>
          <option value="no_tool_execution">未调用工具</option>
          <option value="denied">已拒绝</option>
        </select>
        <select
          v-model="historyTaskId"
          class="max-w-full rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm outline-none focus:border-primary lg:max-w-xs"
        >
          <option value="">任务：全部</option>
          <option v-for="task in agentTasksForFilter" :key="task.id" :value="task.id">
            {{ task.name }}
          </option>
        </select>
        <input
          v-model="historyStartAt"
          type="datetime-local"
          class="rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm outline-none focus:border-primary"
          title="开始时间"
        />
        <input
          v-model="historyEndAt"
          type="datetime-local"
          class="rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm outline-none focus:border-primary"
          title="结束时间"
        />
        <button
          type="button"
          class="inline-flex items-center justify-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50"
          @click="fetchExecutionHistory(true)"
        >
          <svg class="h-4 w-4" :class="historyLoading ? 'animate-spin' : ''" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          刷新
        </button>
        <span class="text-xs text-gray-400 lg:ml-auto">共 {{ historyTotal }} 条</span>
      </div>

      <div v-if="historyLoading && historyItems.length === 0" class="flex flex-col items-center justify-center py-20">
        <div class="mb-4 h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
        <p class="text-sm text-gray-400">正在拉取全局执行记录...</p>
      </div>
      <div
        v-else-if="!historyItems.length"
        class="rounded-2xl border border-dashed border-gray-200 bg-gray-50 py-20 text-center text-gray-400"
      >
        暂无匹配的执行记录
      </div>
      <div v-else class="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[960px] table-fixed border-collapse text-left">
            <thead class="bg-gray-50/80">
              <tr>
                <th class="w-[16%] px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-gray-400">任务</th>
                <th class="w-[10%] px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-gray-400">创建人</th>
                <th class="w-[12%] px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-gray-400">Agent</th>
                <th class="w-[8%] px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-gray-400">状态</th>
                <th class="w-[12%] px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-gray-400">时间</th>
                <th class="w-[7%] px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-gray-400">耗时</th>
                <th class="w-[10%] px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-gray-400">Trace</th>
                <th class="w-[17%] px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-gray-400">摘要</th>
                <th class="w-[8%] px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-gray-400">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in historyItems"
                :key="item.id"
                class="border-t border-gray-100 align-top hover:bg-gray-50/60"
              >
                <td class="px-4 py-3">
                  <p class="truncate text-sm font-semibold text-gray-900" :title="item.task_name || ''">
                    {{ item.task_name || '已删除任务' }}
                  </p>
                  <p v-if="item.task_id" class="mt-0.5 text-[10px] text-gray-400">#{{ item.task_id }}</p>
                </td>
                <td class="px-4 py-3 text-xs text-gray-600">{{ item.creator_name || item.username || '—' }}</td>
                <td class="px-4 py-3 text-xs text-gray-600 truncate" :title="item.agent_name || ''">{{ item.agent_name || item.agent_id || '—' }}</td>
                <td class="px-4 py-3">
                  <span class="rounded-full px-2 py-0.5 text-[9px] font-black" :class="logStatusMeta(item.status).class">
                    {{ logStatusMeta(item.status).label }}
                  </span>
                </td>
                <td class="px-4 py-3 text-[11px] text-gray-500">{{ formatDate(item.created_at) }}</td>
                <td class="px-4 py-3 text-[11px] text-gray-500 whitespace-nowrap" :title="item.execution_time_ms != null ? `${Math.round(item.execution_time_ms)}ms` : ''">
                  {{ formatDurationMs(item.execution_time_ms) }}
                </td>
                <td class="px-4 py-3">
                  <button
                    type="button"
                    class="font-mono text-[10px] text-primary hover:underline"
                    @click="viewTrace(item.trace_id)"
                  >{{ item.trace_id.split('-')[0] }}…</button>
                </td>
                <td class="px-4 py-3">
                  <button
                    type="button"
                    class="w-full text-left text-xs text-gray-600"
                    @click="toggleHistoryExpand(item.id)"
                  >
                    <span :class="historyExpandedIds.has(item.id) ? '' : 'line-clamp-2'">
                      {{ item.summary || item.query || '—' }}
                    </span>
                  </button>
                </td>
                <td class="px-4 py-3">
                  <div class="flex flex-col gap-1">
                    <button type="button" class="text-[10px] font-bold text-primary hover:underline" @click="viewTrace(item.trace_id)">Trace</button>
                    <button
                      type="button"
                      class="text-[10px] font-bold text-gray-500 hover:text-primary hover:underline disabled:opacity-40"
                      :disabled="!item.task_id"
                      @click="openTaskLogsFromHistory(item)"
                    >任务日志</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="historyHasMore" class="flex justify-center border-t border-gray-100 py-3">
          <button
            type="button"
            class="rounded-lg bg-gray-100 px-4 py-2 text-xs font-bold text-gray-600 hover:bg-gray-200 disabled:opacity-50"
            :disabled="historyLoading"
            @click="loadMoreHistory"
          >
            {{ historyLoading ? '加载中…' : '加载更多' }}
          </button>
        </div>
      </div>
    </div>

    <template v-if="mainViewTab === 'tasks'">
    <!-- Loading State -->
    <div v-if="loading" class="py-20 text-center">
      <div class="animate-spin h-10 w-10 border-4 border-primary border-t-transparent rounded-full mx-auto mb-4"></div>
      <p class="text-gray-500">加载任务列表中...</p>
    </div>

    <!-- Empty State -->
    <div v-else-if="filteredTasks.length === 0" class="py-20 text-center bg-white rounded-xl border border-dashed border-gray-200">
      <p class="text-gray-500">没有找到匹配的任务</p>
    </div>

    <!-- Grid View -->
    <div v-else-if="currentViewMode === 'grid'" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
      <div 
        v-for="task in filteredTasks" 
        :key="task.id"
        class="bg-white rounded-xl shadow-sm border border-gray-100 hover:shadow-md transition-all group overflow-hidden"
      >
        <div class="p-5">
          <div class="flex justify-between items-start mb-4">
            <div class="flex items-center space-x-3">
              <div class="w-10 h-10 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center text-xl shadow-inner relative">
                🕒
                <!-- Source Badge -->
                <div 
                  class="absolute -top-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center border-2 border-white shadow-sm text-[10px]"
                  :class="task.task_type === 'saved_report' ? 'bg-emerald-500 text-white' : task.source === 'agent' ? 'bg-indigo-500 text-white' : 'bg-amber-500 text-white'"
                  :title="task.task_type === 'saved_report' ? '报表订阅' : task.source === 'agent' ? '智能体创建' : '手动创建'"
                >
                  {{ task.task_type === 'saved_report' ? '📊' : task.source === 'agent' ? '🤖' : '👤' }}
                </div>
              </div>
              <div class="min-w-0">
                <div class="flex items-center space-x-2">
                  <h3 class="font-bold text-gray-900 truncate">{{ task.name }}</h3>
                  <span v-if="task.task_type === 'saved_report'" class="px-2 py-0.5 bg-emerald-50 text-emerald-700 text-[9px] font-black rounded-full border border-emerald-100">报表订阅</span>
                  <span v-if="String(task.user_id) === String(userInfo?.user_id)" class="px-2 py-0.5 bg-amber-100 text-amber-700 text-[9px] font-black rounded-full border border-amber-200 flex-shrink-0">
                    我创建的
                  </span>
                  <span class="px-2 py-0.5 text-[9px] font-black rounded-full border flex items-center flex-shrink-0" :class="taskHealthMeta(task).class">
                    <span class="w-1.5 h-1.5 rounded-full mr-1" :class="taskHealthMeta(task).dot"></span>
                    {{ taskHealthMeta(task).label }}
                  </span>
                </div>
                <div class="flex items-center mt-1">
                  <span class="text-[10px] text-primary font-bold mr-2 bg-primary/5 px-1.5 py-0.5 rounded">{{ task.agent_name }}</span>
                  <span 
                    class="w-2 h-2 rounded-full mr-2"
                    :class="task.status === 1 ? 'bg-green-500 animate-pulse' : 'bg-gray-300'"
                  ></span>
                  <span
                    v-if="task.cron_valid === false"
                    class="text-[10px] font-bold text-red-500 bg-red-50 px-1.5 py-0.5 rounded mr-1"
                    :title="`无效 Cron: ${task.cron_expr}`"
                  >⚠️ Cron无效</span>
                  <span
                    v-else
                    class="text-[10px] font-bold text-blue-600"
                    :title="`Cron: ${task.cron_expr}`"
                  >{{ formatTaskSchedule(task.cron_expr) }}</span>
                </div>
              </div>
            </div>
            
            <!-- Switch UI Replacement -->
            <button 
              v-if="canManageTask(task)"
              @click="toggleStatus(task)"
              class="relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200"
              :class="task.status === 1 ? 'bg-green-500' : 'bg-gray-200'"
            >
              <span class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200" :class="task.status === 1 ? 'translate-x-4' : 'translate-x-0'"></span>
            </button>
            <div v-else class="relative inline-flex h-5 w-9 flex-shrink-0 rounded-full border-2 border-transparent" :class="task.status === 1 ? 'bg-green-500/50' : 'bg-gray-200'">
               <span class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow" :class="task.status === 1 ? 'translate-x-4' : 'translate-x-0'"></span>
            </div>
          </div>

          <div class="space-y-3">
            <div class="p-3 bg-gray-50 rounded-lg border border-gray-100 min-h-[60px]">
              <p class="text-[10px] text-gray-400 font-bold uppercase tracking-widest mb-1">{{ task.task_type === 'saved_report' ? '报表说明' : '指令' }}</p>
              <p class="text-xs text-gray-600 line-clamp-2 italic leading-relaxed">"{{ task.prompt }}"</p>
            </div>
            <div class="grid grid-cols-4 gap-2 text-[10px]">
              <div>
                <p class="text-gray-400 mb-0.5">触发</p>
                <p class="text-gray-900 font-black text-xs">{{ metricValue(task.trigger_count) }}</p>
              </div>
              <div>
                <p class="text-gray-400 mb-0.5">成功</p>
                <p class="text-green-700 font-black text-xs">{{ metricValue(task.success_count || task.run_count) }}</p>
              </div>
              <div>
                <p class="text-gray-400 mb-0.5">失败</p>
                <p class="text-red-600 font-black text-xs">{{ metricValue(task.failure_count) }}</p>
              </div>
              <div>
                <p class="text-gray-400 mb-0.5">跳过</p>
                <p class="text-slate-600 font-black text-xs">{{ metricValue(task.skipped_count) }}</p>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-2 text-[10px]">
              <div>
                <p class="text-gray-400 mb-0.5">上次尝试</p>
                <p class="text-gray-700 font-medium">{{ formatDate(task.last_attempt_at || task.last_run_at) }}</p>
              </div>
              <div>
                <p class="text-gray-400 mb-0.5">预计下次</p>
                <p class="whitespace-nowrap text-primary font-bold">{{ formatNextRunCompact(task.next_run_at) }}</p>
              </div>
            </div>

            <div v-if="task.last_error" class="text-[10px] text-red-600 bg-red-50 border border-red-100 rounded-lg px-2 py-1.5 line-clamp-2">
              {{ task.consecutive_failures ? `连续失败 ${task.consecutive_failures} 次：` : '' }}{{ task.last_error }}
            </div>
            
            <!-- Audit Info -->
            <div class="pt-3 border-t border-gray-50 flex items-center justify-between text-[9px] text-gray-400">
              <span class="flex items-center">
                <span class="mr-1">👤</span>
                {{ task.creator_name || '系统' }}
              </span>
              <span>{{ formatDate(task.created_at) }} 创建</span>
            </div>
          </div>
        </div>

        <div class="bg-gray-50/50 px-5 py-3 border-t border-gray-100 flex items-center justify-between opacity-80 group-hover:opacity-100 transition-opacity">
          <div class="flex items-center space-x-1">
            <button @click="openLogs(task)" class="p-1.5 text-gray-400 hover:text-primary hover:bg-white rounded-md transition-all shadow-sm border border-transparent hover:border-gray-100" title="执行历史">
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
            </button>
            <button v-if="canManageTask(task)" @click="openEditModal(task)" class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-white rounded-md transition-all shadow-sm border border-transparent hover:border-gray-100" :title="task.task_type === 'saved_report' ? '订阅设置' : '编辑'">
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
            </button>
            <button v-if="canManageTask(task)" @click="toggleStatus(task)" class="p-1.5 text-gray-400 hover:bg-white rounded-md transition-all shadow-sm border border-transparent hover:border-gray-100" :class="task.status === 1 ? 'hover:text-orange-600' : 'hover:text-green-600'" :title="task.status === 1 ? '停止' : '激活'">
              <PauseCircleIcon v-if="task.status === 1" class="w-4 h-4" />
              <PlayCircleIcon v-else class="w-4 h-4" />
            </button>
            <button v-if="canManageTask(task)" @click="deleteTask(task)" class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-white rounded-md transition-all shadow-sm border border-transparent hover:border-gray-100" title="删除">
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
            </button>
          </div>
          <button 
            v-if="canManageTask(task)"
            @click="runTaskNow(task)"
            :disabled="runningTaskIds.has(task.id)"
            class="text-[10px] font-bold flex items-center px-2 py-1 rounded bg-white border border-gray-200 shadow-sm hover:border-primary hover:text-primary transition-all disabled:opacity-50"
          >
            <span v-if="runningTaskIds.has(task.id)" class="mr-1 animate-spin">⌛</span>
            {{ runningTaskIds.has(task.id) ? '正在触发' : '立即执行' }}
          </button>
        </div>
      </div>
    </div>

    <!-- List View -->
    <div v-else class="bg-white rounded-xl border border-gray-200 overflow-x-auto shadow-sm">
      <table class="w-full min-w-[1080px] table-fixed text-left border-collapse">
        <thead>
          <tr class="bg-gray-50/50 border-b border-gray-200">
            <th class="w-[30%] px-5 py-3 text-[11px] font-bold text-gray-400 uppercase tracking-wider">任务名称</th>
            <th class="w-[12%] px-4 py-3 text-[11px] font-bold text-gray-400 uppercase tracking-wider">执行对象</th>
            <th class="w-[8%] px-4 py-3 text-[11px] font-bold text-gray-400 uppercase tracking-wider text-center">执行次数</th>
            <th class="w-[12%] px-4 py-3 text-[11px] font-bold text-gray-400 uppercase tracking-wider hidden md:table-cell">运行周期</th>
            <th class="w-[12%] px-4 py-3 text-[11px] font-bold text-gray-400 uppercase tracking-wider">预计下次运行</th>
            <th class="w-[8%] px-4 py-3 text-[11px] font-bold text-gray-400 uppercase tracking-wider text-center">状态</th>
            <th class="w-[18%] px-5 py-3 text-[11px] font-bold text-gray-400 uppercase tracking-wider text-right">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="task in filteredTasks" :key="task.id" class="hover:bg-blue-50/30 transition-colors group">
            <td class="px-5 py-4">
              <div class="flex items-start space-x-3">
                <span 
                  class="w-6 h-6 rounded-lg flex items-center justify-center text-xs shadow-inner"
                  :class="task.task_type === 'saved_report' ? 'bg-emerald-50 text-emerald-600' : task.source === 'agent' ? 'bg-indigo-50 text-indigo-600' : 'bg-amber-50 text-amber-600'"
                  :title="task.task_type === 'saved_report' ? '报表订阅' : task.source === 'agent' ? '智能体创建' : '手动创建'"
                >
                  {{ task.task_type === 'saved_report' ? '📊' : task.source === 'agent' ? '🤖' : '👤' }}
                </span>
                <div class="min-w-0 flex-1">
                  <p class="text-sm font-bold leading-5 text-gray-900 group-hover:text-primary line-clamp-2" :title="task.name">{{ task.name }}</p>
                  <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <span v-if="task.task_type === 'saved_report'" class="whitespace-nowrap px-2 py-0.5 bg-emerald-50 text-emerald-700 text-[8px] font-black rounded-full border border-emerald-100">报表订阅</span>
                    <span v-if="String(task.user_id) === String(userInfo?.user_id)" class="px-2 py-0.5 bg-amber-100 text-amber-700 text-[8px] font-black rounded-full border border-amber-200">
                      我创建的
                    </span>
                    <span class="px-2 py-0.5 text-[8px] font-black rounded-full border flex items-center" :class="taskHealthMeta(task).class">
                      <span class="w-1 h-1 rounded-full mr-1" :class="taskHealthMeta(task).dot"></span>
                      {{ taskHealthMeta(task).label }}
                    </span>
                  </div>
                  <span class="block text-[10px] text-gray-400 line-clamp-1 mt-1">
                    {{ task.creator_name }} · 触发 {{ metricValue(task.trigger_count) }} / 成功 {{ metricValue(task.success_count || task.run_count) }} / 失败 {{ metricValue(task.failure_count) }}
                  </span>
                </div>
              </div>
            </td>
            <td class="px-4 py-4">
              <span class="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium text-gray-600"><span>{{ task.task_type === 'saved_report' ? '📊' : '🤖' }}</span>{{ task.agent_name }}</span>
            </td>
            <td class="px-4 py-4 text-center">
              <div class="flex flex-col items-center">
                <span class="text-xs font-black text-gray-900">{{ metricValue(task.success_count || task.run_count) }}</span>
                <span v-if="metricValue(task.failure_count)" class="text-[9px] text-red-500 mt-0.5">失败 {{ metricValue(task.failure_count) }}</span>
              </div>
            </td>
            <td class="px-4 py-4 hidden md:table-cell">
              <template v-if="task.cron_valid === false">
                <span
                  class="inline-flex whitespace-nowrap rounded-lg bg-red-50 px-2 py-1 text-[11px] font-bold text-red-600"
                  :title="`无效 Cron: ${task.cron_expr}`"
                >⚠️ Cron无效</span>
              </template>
              <template v-else>
                <span class="inline-flex whitespace-nowrap rounded-lg bg-blue-50 px-2 py-1 text-[11px] font-bold text-blue-700" :title="`Cron: ${task.cron_expr}`">{{ formatTaskSchedule(task.cron_expr) }}</span>
              </template>
            </td>
            <td class="px-4 py-4">
              <span class="whitespace-nowrap text-xs font-medium text-gray-600">{{ formatNextRunCompact(task.next_run_at) }}</span>
            </td>
            <td class="px-4 py-4">
              <div class="flex justify-center">
                <span 
                  class="px-2 py-0.5 rounded-full text-[10px] font-bold border flex items-center whitespace-nowrap flex-shrink-0"
                  :class="task.status === 1 ? 'bg-green-50 text-green-600 border-green-100' : 'bg-gray-100 text-gray-400 border-gray-200'"
                >
                  <span class="w-1.5 h-1.5 rounded-full mr-1.5 flex-shrink-0" :class="task.status === 1 ? 'bg-green-500 animate-pulse' : 'bg-gray-400'"></span>
                  {{ task.status === 1 ? '活跃' : '停止' }}
                </span>
              </div>
            </td>
            <td class="px-5 py-4 text-right">
              <!-- 报表订阅专属操作 -->
              <div v-if="task.task_type === 'saved_report'" class="flex items-center justify-end gap-1.5">
                <button class="whitespace-nowrap rounded-lg border border-blue-100 bg-blue-50 px-2 py-1.5 text-[10px] font-bold text-blue-600 hover:bg-blue-100" @click="openSavedReportSubscriptionSettings(task)">订阅设置</button>
                <button class="whitespace-nowrap rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-[10px] font-bold text-gray-600 hover:border-blue-200 hover:text-blue-600" @click="openLogs(task)">运行历史</button>
                <button v-if="canManageTask(task)" class="whitespace-nowrap rounded-lg border border-emerald-100 bg-emerald-50 px-2 py-1.5 text-[10px] font-bold text-emerald-700 hover:bg-emerald-100 disabled:opacity-50" :disabled="runningTaskIds.has(task.id)" @click="runTaskNow(task)">{{ runningTaskIds.has(task.id) ? '执行中' : '立即执行' }}</button>
                <button v-if="canManageTask(task)" class="whitespace-nowrap rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-[10px] font-bold text-gray-500 hover:text-orange-600" @click="toggleStatus(task)">{{ task.status === 1 ? '暂停' : '恢复' }}</button>
                <button v-if="canManageTask(task)" class="rounded-lg p-1.5 text-gray-300 hover:bg-red-50 hover:text-red-500" title="删除订阅" @click="deleteTask(task)"><svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg></button>
              </div>
              <div v-else class="flex items-center justify-end space-x-1 opacity-60 group-hover:opacity-100 transition-opacity">
                <button v-if="canManageTask(task)" @click="runTaskNow(task)" :disabled="runningTaskIds.has(task.id)" class="p-1.5 text-primary hover:bg-white rounded shadow-sm border border-transparent hover:border-gray-100" title="立即执行">
                  <svg v-if="!runningTaskIds.has(task.id)" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
                  <span v-else class="animate-spin text-[10px]">⌛</span>
                </button>
                <button @click="openLogs(task)" class="p-1.5 text-gray-400 hover:text-primary hover:bg-white rounded shadow-sm border border-transparent hover:border-gray-100" title="历史">
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                </button>
                <button v-if="canManageTask(task)" @click="openEditModal(task)" class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-white rounded shadow-sm border border-transparent hover:border-gray-100" :title="getTaskEditTitle(task)">
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                </button>
                <button v-if="canManageTask(task)" @click="toggleStatus(task)" class="p-1.5 text-gray-400 hover:bg-white rounded shadow-sm border border-transparent hover:border-gray-100" :class="task.status === 1 ? 'hover:text-orange-600' : 'hover:text-green-600'" :title="task.status === 1 ? '停止' : '激活'">
                  <PauseCircleIcon v-if="task.status === 1" class="w-4 h-4" />
                  <PlayCircleIcon v-else class="w-4 h-4" />
                </button>
                <button v-if="canManageTask(task)" @click="deleteTask(task)" class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-white rounded shadow-sm border border-transparent hover:border-gray-100" title="删除">
                  <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    </template>

    <!-- 任务调度设计规范与全流程指引 Modal -->
    <div v-if="showSpecsModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showSpecsModal = false">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-orange-50/30">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-xl bg-orange-600 text-white flex items-center justify-center shadow-md shadow-orange-500/20" style="background-color: #ea580c; color: #ffffff;">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">任务调度设计规范与全流程指引</h2>
               <p class="text-xs text-gray-500 font-medium mt-0.5">从 Cron 定时周期编排、资源限定与安全审批，到渠道触达、时序观测与健康监控。</p>
             </div>
          </div>
          <div class="flex items-center gap-3">
            <button
              v-if="!showTaskFlowGuide"
              type="button"
              @click="restoreTaskFlowGuide"
              class="inline-flex items-center gap-1 text-xs font-medium text-orange-700 bg-orange-100/70 hover:bg-orange-200 px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
              <span>恢复顶部流程提示</span>
            </button>
            <button @click="showSpecsModal = false" class="text-gray-400 hover:text-gray-600 transition-colors cursor-pointer">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
        </div>

        <!-- Tabs -->
        <div class="flex border-b border-gray-200 bg-white px-6">
           <button 
             v-for="tab in ['flow', 'context', 'approval']" 
             :key="tab"
             @click="activeSpecsTab = tab as any"
             class="px-4 py-3 text-sm font-medium border-b-2 transition-colors cursor-pointer"
             :class="activeSpecsTab === tab ? 'border-orange-600 text-orange-700' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
           >
             {{ tab === 'flow' ? '全流程指引 (Workflow)' :
                tab === 'context' ? '上下文注入与智能体规范 (Context & Specs)' : '安全审批与资源限定 (Approval & Scope)' }}
           </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-6 sm:p-8 bg-gray-50/50">
           <!-- Tab 1: Workflow Flow -->
           <div v-if="activeSpecsTab === 'flow'" class="space-y-6 max-w-4xl mx-auto">
              <div class="bg-gradient-to-r from-orange-50 to-amber-50 border-l-4 border-orange-600 p-4 rounded-r-xl shadow-2xs">
                 <h3 class="font-bold text-orange-900 mb-1">任务调度 5 步全生命周期自动化体系</h3>
                 <p class="text-xs text-orange-700 leading-relaxed">
                    基于分布式调度引擎。定义目标智能体与 Cron 周期，限定资源沙箱与审批模式，配置钉钉/企微/邮件触达，支持时序 Trace 全景观测。
                 </p>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                 <!-- Step 1 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-orange-600 text-white flex items-center justify-center text-xs font-bold">1</span>
                          <h4 class="font-bold text-gray-900 text-sm">任务创建与周期编排</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          指定负责执行的目标智能体与模型参数；配置每天/每周/每月定时周期与提示词，支持 AI 智能扩写优化 Prompt。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex items-center justify-end gap-2">
                       <button
                          type="button"
                          @click="showSpecsModal = false; router.push('/dashboard/agent-management')"
                          class="text-xs text-gray-600 hover:text-gray-900 font-medium px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 cursor-pointer"
                       >
                          智能体中心 &rarr;
                       </button>
                       <button
                          type="button"
                          @click="showSpecsModal = false; openCreateModal()"
                          class="text-xs text-orange-600 hover:text-orange-800 font-bold cursor-pointer"
                       >
                          新建任务 &rarr;
                       </button>
                    </div>
                 </div>

                 <!-- Step 2 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold">2</span>
                          <h4 class="font-bold text-gray-900 text-sm">资源限定与安全审批</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          限定任务可访问的数据集、知识库、Skills 与 MCP 工具；设置 Allow 放行、Ask 人工确认或 Deny 拦截高危操作。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end text-xs text-gray-400">
                       任务编辑中设置安全与资源范围
                    </div>
                 </div>

                 <!-- Step 3 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-teal-600 text-white flex items-center justify-center text-xs font-bold">3</span>
                          <h4 class="font-bold text-gray-900 text-sm">渠道分发与触达订阅</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          配置站内通知、钉钉群机器人、企业微信 Webhook 或邮件，任务完成后自动沉淀 Markdown 报告并向相关人推送。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end text-xs text-gray-400">
                       在通知配置中勾选推送渠道
                    </div>
                 </div>

                 <!-- Step 4 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-purple-600 text-white flex items-center justify-center text-xs font-bold">4</span>
                          <h4 class="font-bold text-gray-900 text-sm">手动试跑与时序观测</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          在任务卡片上点击「立即执行」即刻试跑；点击「Trace」展开时序链路，全景观测思考耗时与工具调用入参返回。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end text-xs text-gray-400">
                       卡片点击「立即执行」或「Trace」
                    </div>
                 </div>

                 <!-- Step 5 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between md:col-span-2">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-emerald-600 text-white flex items-center justify-center text-xs font-bold">5</span>
                          <h4 class="font-bold text-gray-900 text-sm">健康监控与异常处置</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          切换「执行记录」查看历史执行日志、状态统计（成功/待确认/失败）与平均耗时，支持异常审计告警与一键重试。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                       <button
                          type="button"
                          @click="showSpecsModal = false; mainViewTab = 'history'"
                          class="text-xs text-emerald-600 hover:text-emerald-800 font-medium cursor-pointer"
                       >
                          前往执行记录 &rarr;
                       </button>
                    </div>
                 </div>
              </div>
           </div>

           <!-- Tab 2: Context & Specs -->
           <div v-else-if="activeSpecsTab === 'context'" class="space-y-4 max-w-4xl mx-auto">
              <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-650 leading-relaxed">
                 <h4 class="font-bold text-gray-900 text-base">自动化上下文注入与处理规范</h4>
                 <div class="space-y-3">
                    <p class="text-xs text-gray-600">
                       每次调度执行时，系统在 <code>user_info</code> 中自动注入以下上下文元数据，智能体可通过这些标识识别定时自动化场景：
                    </p>
                    <div class="bg-gray-50 rounded-xl p-4 border border-gray-150 font-mono text-[11px] space-y-1.5">
                       <div class="flex"><span class="text-blue-600 w-36 font-semibold">is_scheduled_task:</span><span class="text-emerald-600 font-bold">true</span><span class="text-gray-400 ml-auto">// 标识当前为定时自动化任务</span></div>
                       <div class="flex"><span class="text-blue-600 w-36 font-semibold">task_name:</span><span class="text-emerald-600 font-bold">"数据巡检与周报"</span><span class="text-gray-400 ml-auto">// 当前执行的任务名称</span></div>
                       <div class="flex"><span class="text-blue-600 w-36 font-semibold">user_id / role:</span><span class="text-emerald-600 font-bold">"admin" / "1"</span><span class="text-gray-400 ml-auto">// 模拟创建者的身份与权限</span></div>
                    </div>
                    <div class="p-3.5 bg-amber-50/70 rounded-xl border border-amber-100 space-y-1 text-xs">
                       <span class="font-bold text-amber-900">结果导向输出原则</span>
                       <p class="text-gray-600 leading-relaxed">识别到 <code>is_scheduled_task</code> 上下文后，智能体应跳过“您好”、“请稍等”等交互用语，直接输出结构化 Markdown 报表或数据结论。</p>
                    </div>
                 </div>
              </div>
           </div>

           <!-- Tab 3: Approval & Scope -->
           <div v-else-if="activeSpecsTab === 'approval'" class="space-y-4 max-w-4xl mx-auto">
              <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-650 leading-relaxed">
                 <h4 class="font-bold text-gray-900 text-base">安全审批策略与资源沙箱限定</h4>
                 <div class="space-y-3 text-xs">
                    <div class="p-3.5 bg-green-50/60 rounded-xl border border-green-100 space-y-1">
                       <span class="font-bold text-green-900 text-sm">Allow 模式（自动放行）</span>
                       <p class="text-gray-600 leading-relaxed">智能体调用只读类或常规工具时全自动执行，无需人工干预，适合日常数据巡检与日报推送。</p>
                    </div>
                    <div class="p-3.5 bg-amber-50/60 rounded-xl border border-amber-100 space-y-1">
                       <span class="font-bold text-amber-900 text-sm">Ask 模式（待人工授权）</span>
                       <p class="text-gray-600 leading-relaxed">触发涉及数据写操作或外部高危 API 时，任务将挂起并向管理员发送审批通知，人工点击确认后继续。</p>
                    </div>
                    <div class="p-3.5 bg-red-50/60 rounded-xl border border-red-100 space-y-1">
                       <span class="font-bold text-red-900 text-sm">Deny 模式（严格拦截）</span>
                       <p class="text-gray-600 leading-relaxed">禁止所有需要二次确认的操作，一旦大模型尝试发起高危调用将直接拦截并记录异常审计日志。</p>
                    </div>
                 </div>
              </div>
           </div>
        </div>
      </div>
    </div>

    <!-- Edit Modal -->
    <Modal 
      v-if="showEditModal" 
      :title="editingTask.id ? '编辑定时任务' : '新建定时任务'" 
      @close="showEditModal = false" 
      size="max-w-2xl"
      class="transition-all"
      :class="isMobile ? 'inset-0 !m-0 !max-w-none !rounded-none h-full' : ''"
    >
      <div class="space-y-5 h-full flex flex-col">
        <div class="flex-1 overflow-y-auto space-y-5 px-1 p-1">
          <div>
            <label class="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-1">任务基本信息</label>
            <input v-model="editingTask.name" placeholder="任务名称 (e.g. PUE日报)" class="w-full px-3 py-2 border rounded-xl outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all" />
          </div>
          
          <div>
            <label class="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-1">执行大脑 (Agent)</label>
            <div ref="agentDropdownRef" class="relative z-40">
              <button
                type="button"
                class="flex w-full items-center justify-between rounded-xl border border-gray-200 bg-white px-3 py-2 text-left shadow-sm outline-none transition-all hover:border-gray-300 focus:border-primary focus:ring-2 focus:ring-primary/20"
                @click.stop="showAgentDropdown = !showAgentDropdown"
              >
                <div class="flex min-w-0 flex-1 items-center gap-2">
                  <div class="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-gray-100 bg-gray-50 text-sm">
                    <img
                      v-if="isAgentAvatarUrl(selectedEditingAgent?.avatar_url)"
                      :src="selectedEditingAgent?.avatar_url"
                      class="h-full w-full object-cover"
                    />
                    <span v-else-if="selectedEditingAgent?.avatar_url" class="text-sm">{{ selectedEditingAgent?.avatar_url }}</span>
                    <span v-else class="text-sm">{{ selectedEditingAgent?.is_system ? '🔒' : '👤' }}</span>
                  </div>
                  <div class="min-w-0">
                    <p class="truncate text-sm font-bold text-gray-800">
                      {{ selectedEditingAgent?.display_name || '选择智能体' }}
                    </p>
                    <p v-if="selectedEditingAgent?.name" class="truncate text-[10px] font-mono text-gray-400">
                      {{ selectedEditingAgent.name }}
                    </p>
                  </div>
                </div>
                <svg
                  class="ml-2 h-4 w-4 shrink-0 text-gray-400 transition-transform duration-200"
                  :class="{ 'rotate-180': showAgentDropdown }"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>

              <div
                v-show="showAgentDropdown"
                class="absolute left-0 right-0 z-50 mt-1 max-h-72 overflow-y-auto rounded-xl border border-gray-200 bg-white py-1 px-1 shadow-xl"
              >
                <button
                  v-for="agent in agents"
                  :key="agent.id"
                  type="button"
                  class="my-1 flex w-full cursor-pointer items-start gap-2.5 rounded-lg border p-2 text-left transition-all"
                  :class="
                    editingTask.agent_id === agent.id
                      ? 'border-primary/40 bg-primary/5 ring-1 ring-primary/5'
                      : 'border-transparent hover:bg-gray-50'
                  "
                  @click.stop="selectEditingAgent(agent.id)"
                >
                  <div
                    class="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded border border-gray-100 bg-gray-50 text-sm"
                    :class="editingTask.agent_id === agent.id ? 'border-primary/20 bg-primary/10' : ''"
                  >
                    <img
                      v-if="isAgentAvatarUrl(agent.avatar_url)"
                      :src="agent.avatar_url"
                      class="h-full w-full object-cover"
                    />
                    <span v-else-if="agent.avatar_url" class="text-sm">{{ agent.avatar_url }}</span>
                    <span v-else class="text-sm">{{ agent.is_system ? '🔒' : '👤' }}</span>
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center justify-between gap-2">
                      <span
                        class="truncate text-xs font-bold text-gray-800"
                        :class="editingTask.agent_id === agent.id ? 'text-primary' : ''"
                      >{{ agent.display_name }}</span>
                      <span
                        v-if="agent.is_system"
                        class="shrink-0 rounded border border-gray-200 bg-gray-50 px-1 text-[8px] font-mono text-gray-400"
                      >SYSTEM</span>
                    </div>
                    <div class="mt-0.5 truncate font-mono text-[9px] text-gray-400">{{ agent.name }}</div>
                    <div class="mt-1 line-clamp-2 break-words text-[10px] leading-relaxed text-gray-500" :title="agent.description">
                      {{ agent.description || '暂无备注说明信息' }}
                    </div>
                  </div>
                </button>
                <p v-if="!agents.length" class="px-3 py-4 text-center text-xs text-gray-400">暂无可选智能体</p>
              </div>
            </div>
          </div>

          <div>
            <label class="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-2 flex justify-between items-center">
              <span>运行周期配置</span>
              <div class="flex bg-gray-100 rounded-lg p-0.5 text-[9px] overflow-hidden">
                 <button v-for="m in ['daily','weekly','monthly','interval', 'custom']" :key="m"
                   @click="cronMode = m as any"
                   class="px-2 py-1 rounded transition-all capitalize"
                   :class="cronMode === m ? 'bg-white shadow-sm text-primary font-bold' : 'text-gray-500 hover:text-gray-700'"
                 >
                   {{ 
                      m === 'daily' ? '每天' : 
                      m === 'weekly' ? '每周' : 
                      m === 'monthly' ? '每月' : 
                      m === 'interval' ? '间隔' : '自定义'
                   }}
                 </button>
              </div>
            </label>

            <div class="bg-gray-50 p-4 rounded-xl border border-gray-200 space-y-4">
               <!-- Daily -->
               <div v-if="cronMode === 'daily'" class="flex items-center space-x-3">
                  <span class="text-xs font-bold text-gray-500">每天执行时间:</span>
                  <input type="time" v-model="cronConfig.time" class="flex-1 border rounded-lg px-2 py-1.5 text-sm" />
               </div>
               <!-- Weekly -->
               <div v-if="cronMode === 'weekly'" class="space-y-3">
                   <div class="flex items-center space-x-3">
                      <span class="text-xs font-bold text-gray-500">执行时间:</span>
                      <input type="time" v-model="cronConfig.time" class="flex-1 border rounded-lg px-2 py-1.5 text-sm" />
                   </div>
                   <div class="space-y-1">
                      <span class="text-xs font-bold text-gray-500">重复日:</span>
                      <div class="flex flex-wrap gap-2">
                          <button v-for="d in [1,2,3,4,5,6,0]" :key="d"
                            @click="cronConfig.weekday = d"
                            class="w-8 h-8 rounded-full text-xs font-bold flex items-center justify-center border transition-all"
                            :class="cronConfig.weekday === d ? 'bg-primary text-white border-primary' : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300'"
                          >
                            {{ d === 0 ? '日' : d }}
                          </button>
                      </div>
                   </div>
               </div>
               <!-- Monthly -->
               <div v-if="cronMode === 'monthly'" class="flex items-center space-x-3">
                   <div class="flex-1 grid grid-cols-2 gap-2">
                      <div class="flex flex-col">
                          <span class="text-xs mb-1 text-gray-400">日期 (1-31)</span>
                          <input type="number" min="1" max="31" v-model.number="cronConfig.day" class="border rounded-lg px-2 py-1.5 text-sm" />
                      </div>
                      <div class="flex flex-col">
                          <span class="text-xs mb-1 text-gray-400">时间</span>
                          <input type="time" v-model="cronConfig.time" class="border rounded-lg px-2 py-1.5 text-sm" />
                      </div>
                   </div>
               </div>
               <!-- Interval -->
               <div v-if="cronMode === 'interval'" class="flex items-center space-x-2">
                   <span class="text-xs text-gray-500">每隔</span>
                   <input type="number" min="1" :max="cronConfig.intervalUnit === 'minutes' ? 59 : 23" v-model.number="cronConfig.intervalValue" class="w-20 border rounded-lg px-2 py-1.5 text-sm text-center" />
                   <select v-model="cronConfig.intervalUnit" class="border rounded-lg px-2 py-1.5 text-sm bg-white">
                      <option value="minutes">分钟</option>
                      <option value="hours">小时</option>
                   </select>
                   <span class="text-xs text-gray-500">执行一次</span>
               </div>
               <!-- Custom -->
               <div v-if="cronMode === 'custom'">
                   <input v-model="editingTask.cron_expr" placeholder="* * * * *" class="w-full px-3 py-2 border rounded-xl font-mono text-sm outline-none focus:border-primary bg-white" />
                   <p class="text-[10px] text-gray-400 mt-1">请使用标准 Cron 表达式</p>
               </div>
            </div>

            <div class="mt-2 p-2.5 bg-blue-50/50 rounded-xl flex items-start space-x-2 border border-blue-100/50">
              <span class="text-blue-500 text-xs mt-0.5 italic">Auto-Translate:</span>
              <p class="text-[11px] text-blue-700 font-bold leading-relaxed">{{ cronDescription }}</p>
            </div>
          </div>

          <div>
            <label class="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-2">执行失败策略</label>
            <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-3 space-y-3">
              <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label class="flex flex-col gap-1 text-xs font-bold text-gray-600">
                  最大重试次数
                  <select
                    v-model.number="taskMaxRetries"
                    class="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  >
                    <option v-for="count in [0, 1, 2, 3]" :key="count" :value="count">{{ count }} 次</option>
                  </select>
                </label>
                <label class="flex flex-col gap-1 text-xs font-bold text-gray-600">
                  重试间隔（分钟）
                  <input
                    v-model.number="taskRetryDelayMinutes"
                    type="number"
                    min="1"
                    max="60"
                    step="1"
                    class="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  />
                </label>
              </div>
              <p class="text-[10px] leading-relaxed text-gray-400">
                仅定时触发失败时自动重试；立即执行不自动重试。默认不重试，最多重试 3 次，间隔支持 1–60 分钟。
              </p>
            </div>
          </div>

          <div>
            <div class="mb-1 flex items-center gap-1.5">
              <label class="text-xs font-bold text-gray-400 uppercase tracking-widest">执行指令 (Prompt)</label>
              <button
                type="button"
                class="inline-flex h-4 w-4 items-center justify-center rounded-full border border-gray-300 text-[10px] font-black text-gray-400 hover:border-blue-400 hover:bg-blue-50 hover:text-blue-600"
                title="查看填写示例"
                aria-label="查看填写示例"
                @click="showPromptHelpModal = true"
              >?</button>
              <div class="ml-auto flex items-center gap-1">
                <button
                  v-if="String(editingTask.prompt || '').trim()"
                  type="button"
                  class="px-2.5 py-1 text-[11px] font-semibold rounded-md text-gray-400 hover:bg-red-50 hover:text-red-600"
                  title="清空执行指令"
                  @click="clearTaskPrompt"
                >清空</button>
                <div v-else ref="promptTemplateDropdownRef" class="relative">
                  <button
                    type="button"
                    class="px-2.5 py-1 text-[11px] font-semibold rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-700 flex items-center gap-0.5"
                    title="选择结构模板插入，骨架仅供参考"
                    @click.stop="showPromptTemplateDropdown = !showPromptTemplateDropdown"
                  >
                    插入模板
                    <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" /></svg>
                  </button>
                  <div
                    v-if="showPromptTemplateDropdown"
                    class="absolute right-0 top-full mt-1 z-50 w-60 rounded-xl border border-gray-100 bg-white py-1 shadow-xl"
                  >
                    <button
                      v-for="tpl in promptTemplates"
                      :key="tpl.title"
                      type="button"
                      class="w-full px-3 py-2 text-left hover:bg-indigo-50/60 transition-colors"
                      @click="applyPromptTemplate(tpl.text)"
                    >
                      <div class="text-xs font-semibold text-gray-700">{{ tpl.title }}</div>
                      <div class="text-[10px] text-gray-400 mt-0.5">{{ tpl.desc }}</div>
                    </button>
                  </div>
                </div>
                <PromptAiOptimize
                  :content="String(editingTask.prompt || '')"
                  endpoint="/api/portal/prompts/optimize/task-instruction"
                  :require-permission="false"
                  confirm-message="AI 将针对当前执行指令生成 3 个侧重点不同的优化版本（结构化完整 / 精简直达 / 输出契约），大约需要几秒钟。是否开始？"
                  loading-hint="正在为您生成 3 个差异化方案"
                  @apply="applyOptimizedPrompt"
                  @toast="onOptimizeToast"
                />
              </div>
            </div>
            <TaskPromptComposer
              :prompt="String(editingTask.prompt || '')"
              :model="taskModel"
              :approval-mode="taskApprovalMode"
              :resource-scope="taskResourceScope"
              :thinking-enable-override="taskThinkingEnableOverride"
              :reasoning-effort-override="taskReasoningEffortOverride"
              :agent-id="editingTask.agent_id"
              @update:prompt="editingTask.prompt = $event"
              @update:model="handleTaskModelSelection"
              @update:approval-mode="taskApprovalMode = $event"
              @update:resource-scope="taskResourceScope = $event"
              @update:thinking-enable-override="taskThinkingEnableOverride = $event"
              @update:reasoning-effort-override="taskReasoningEffortOverride = $event"
            />
          </div>

          <div>
            <label class="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-1">结果通知</label>
            <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-3 space-y-2">
              <div class="flex flex-wrap gap-x-4 gap-y-2">
                <label
                  v-for="channel in notificationChannelOptions"
                  :key="channel.value"
                  class="flex items-center gap-1.5 text-xs font-bold"
                  :class="isNotificationChannelReady(channel.value) ? 'text-gray-600 cursor-pointer' : 'text-gray-300 cursor-not-allowed'"
                  :title="isNotificationChannelReady(channel.value) ? '' : '请先在个人中心 → 消息通知中配置并启用该通道'"
                >
                  <input
                    v-model="notificationChannels"
                    type="checkbox"
                    :value="channel.value"
                    class="rounded border-gray-300 text-primary focus:ring-primary/30 disabled:opacity-40"
                    :disabled="!isNotificationChannelReady(channel.value) || personalNotificationLoading"
                  />
                  {{ channel.label }}
                </label>
              </div>
              <p class="text-[10px] text-gray-400 leading-relaxed">
                勾选后任务执行完成由调度器统一投递结果；站内消息始终可用。钉钉 / 企业微信 / 邮件需先在个人中心配置并启用对应通道。
              </p>
              <p v-if="unavailableExternalChannels.length" class="text-[10px] text-amber-600 leading-relaxed">
                {{ unavailableExternalChannels.join('、') }} 尚未在个人中心配置或未启用，已禁止勾选。
                <button
                  v-if="!embedded"
                  type="button"
                  class="ml-1 font-black text-blue-600 underline underline-offset-2 hover:text-blue-700"
                  @click="openPersonalNotificationSettings"
                >
                  去个人中心配置消息通知
                </button>
              </p>
              <p v-if="promptOverlapsNotificationChannels" class="text-[10px] text-amber-600 leading-relaxed">
                执行指令里已提到通知渠道；勾选渠道将与之合并，同一渠道运行时只发送一次。
              </p>
            </div>
          </div>
        </div>

        <div class="flex justify-end space-x-3 pt-4 pb-safe-area border-t border-gray-50">
          <button @click="showEditModal = false" class="px-4 py-2 text-sm font-bold text-gray-400 hover:text-gray-600">取消</button>
          <button @click="saveTask" class="px-8 py-2 bg-primary text-white rounded-xl shadow-lg shadow-primary/20 hover:bg-primary-dark transition-all font-bold text-sm">确认保存</button>
        </div>
      </div>
    </Modal>

    <Modal
      v-if="showPromptHelpModal"
      title="执行指令填写示例"
      size="max-w-md"
      :z-index="80"
      @close="showPromptHelpModal = false"
    >
      <div class="space-y-3">
        <p class="text-[11px] leading-relaxed text-gray-500">
          写清「做什么、用什么、输出什么」。通知优先勾选下方「结果通知」。
        </p>
        <div
          v-for="example in promptExamples"
          :key="example.title"
          class="rounded-xl border border-gray-100 bg-gray-50/70 p-2.5"
        >
          <div class="mb-1.5 flex items-center justify-between gap-2">
            <div class="min-w-0">
              <p class="text-xs font-black text-gray-800">{{ example.title }}</p>
              <p class="mt-0.5 text-[10px] text-gray-400 line-clamp-1">{{ example.tip }}</p>
            </div>
            <button
              type="button"
              class="shrink-0 rounded-lg bg-blue-50 px-2 py-1 text-[10px] font-bold text-blue-600 hover:bg-blue-100"
              @click="applyPromptExample(example.text)"
            >填入</button>
          </div>
          <pre class="max-h-24 overflow-y-auto whitespace-pre-wrap rounded-lg border border-gray-100 bg-white px-2.5 py-2 text-[10px] leading-relaxed text-gray-700">{{ example.text }}</pre>
        </div>
        <div class="flex justify-end">
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-gray-400 hover:text-gray-600" @click="showPromptHelpModal = false">关闭</button>
        </div>
      </div>
    </Modal>

    <!-- Execution History Logs Drawer -->
    <div v-if="showLogsDrawer" class="fixed inset-0 z-50 flex justify-end">
      <div class="fixed inset-0 bg-black/20 backdrop-blur-sm" @click="showLogsDrawer = false"></div>
      <div class="relative w-full max-w-2xl bg-white h-full shadow-2xl flex flex-col animate-slide-in-right"
           :class="isMobile ? 'max-w-none' : ''"
      >
        <div class="p-6 border-b flex items-center justify-between bg-gray-50/50">
          <div>
            <h2 class="text-xl font-bold text-gray-900">执行历史回溯</h2>
            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest font-mono">{{ selectedTask?.name }} · Logs</p>
          </div>
          <button @click="showLogsDrawer = false" class="p-2 hover:bg-gray-200 rounded-full transition-colors text-gray-400">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        
        <div class="flex-1 overflow-y-auto p-6 custom-scrollbar">
          <div v-if="logsLoading && logs.length === 0" class="flex flex-col items-center justify-center py-20">
            <div class="animate-spin h-8 w-8 border-4 border-primary border-t-transparent rounded-full mb-4"></div>
            <p class="text-sm text-gray-400">正在拉取审计轨迹...</p>
          </div>
          <div v-else-if="logs.length === 0" class="text-center py-20 bg-gray-50 rounded-2xl border border-dashed border-gray-200">
            <p class="text-gray-400">暂无执行记录</p>
          </div>
          <div v-else class="space-y-4">
            <div v-for="log in logs" :key="log.id" class="p-4 border rounded-2xl hover:border-primary/30 transition-all hover:shadow-sm bg-white overflow-hidden">
              <div class="flex justify-between items-start mb-3">
                <div class="flex items-center space-x-2">
                  <span class="px-2 py-0.5 rounded-full text-[9px] font-black tracking-tighter" :class="logStatusMeta(log.status).class">{{ logStatusMeta(log.status).label }}</span>
                  <span class="text-[10px] text-gray-300 font-mono">{{ log.trace_id.split('-')[0] }}...</span>
                </div>
                <span class="text-[10px] text-gray-400 font-medium">{{ formatDate(log.created_at) }}</span>
              </div>
              
              <!-- Result Content -->
              <p class="text-xs text-gray-600 line-clamp-3 mb-4 leading-relaxed font-medium bg-gray-50 p-3 rounded-xl border border-gray-100">"{{ log.summary || log.query }}"</p>
              
              <!-- Steps Accordion -->
              <div class="mb-4">
                  <button 
                    @click="toggleLogSteps(log)"
                    class="flex items-center space-x-2 text-[10px] font-black text-gray-400 uppercase tracking-widest hover:text-primary transition-colors group"
                  >
                    <div class="flex items-center">
                        <div v-if="(log as any).stepsLoading" class="w-3 h-3 border-2 border-primary/30 border-t-primary rounded-full animate-spin mr-2"></div>
                        <svg class="w-4 h-4 transform transition-transform duration-300" :class="{ 'rotate-180': (log as any).isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" /></svg>
                    </div>
                    <span>执行步骤 (Steps)</span>
                    <span v-if="(log as any).steps?.length" class="bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded-full text-[8px] ml-1 group-hover:bg-primary/10 group-hover:text-primary transition-colors">{{ (log as any).steps.length }}</span>
                  </button>

                  <div v-show="(log as any).isExpanded" class="mt-3 pl-4 border-l-2 border-primary/10 space-y-3 animate-fade-in">
                      <div v-if="(log as any).stepsLoading" class="py-4 flex justify-center">
                          <div class="animate-pulse flex space-x-2 items-center">
                              <div class="w-1 h-1 bg-primary/40 rounded-full"></div>
                              <div class="w-1 h-1 bg-primary/40 rounded-full"></div>
                              <div class="w-1 h-1 bg-primary/40 rounded-full"></div>
                          </div>
                      </div>
                      <div v-else-if="(log as any).steps && (log as any).steps.length > 0" class="space-y-3">
                          <div v-for="(step, sIdx) in (log as any).steps" :key="sIdx" class="bg-gray-50/50 p-2.5 rounded-xl border border-gray-100/50 group/step relative">
                              <div class="flex justify-between items-center mb-1.5">
                                  <div class="flex items-center space-x-2">
                                      <span 
                                        class="text-[8px] font-black px-1.5 py-0.5 rounded uppercase tracking-tighter"
                                        :class="{
                                            'bg-blue-100 text-blue-700': (step as any).event_type === 'thought',
                                            'bg-purple-100 text-purple-700': (step as any).event_type === 'router',
                                            'bg-amber-100 text-amber-700': (step as any).event_type === 'tool_call',
                                            'bg-green-100 text-green-700': (step as any).event_type === 'synthesis' || (step as any).event_type === 'final_answer',
                                            'bg-red-100 text-red-700': (step as any).event_type === 'error'
                                        }"
                                      >
                                        {{ (step as any).event_type }}
                                      </span>
                                      <span v-if="(step as any).tool_name" class="text-[9px] font-bold text-gray-700 font-mono">{{ (step as any).tool_name }}</span>
                                  </div>
                                  <span class="text-[8px] text-gray-300 font-mono italic">{{ (step as any).execution_time_ms?.toFixed(0) }}ms</span>
                              </div>
                              
                              <!-- Simplified Content Preview -->
                              <div class="text-[10px] text-gray-500 leading-relaxed break-words line-clamp-2 italic opacity-80 group-hover/step:opacity-100 transition-opacity">
                                  {{ (step as any).tool_input ? (typeof (step as any).tool_input === 'string' ? (step as any).tool_input : JSON.stringify((step as any).tool_input)) : '' }}
                                  {{ (step as any).tool_output?.content || (step as any).raw_log || '' }}
                              </div>
                          </div>
                      </div>
                      <div v-else class="py-2 text-[10px] text-gray-400 italic">未记录详细步骤</div>
                  </div>
              </div>

              <!-- Footer Actions -->
              <div class="flex justify-between items-center pt-2 border-t border-gray-50">
                <span class="text-[9px] text-gray-300 font-medium" :title="`${Math.round(log.execution_time_ms)}ms`">时长: {{ formatDurationMs(log.execution_time_ms) }}</span>
                <button @click="viewTrace(log.trace_id)" class="text-[10px] text-primary font-black flex items-center hover:underline uppercase tracking-widest">
                  完整链路 (Trace)
                  <svg class="w-3 h-3 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M14 5l7 7-7 7" /></svg>
                </button>
              </div>
            </div>
          </div>
            <div v-if="logsHasMore" class="pt-4 pb-8 flex justify-center">
                 <button 
                    @click="loadMoreLogs" 
                    :disabled="logsLoading"
                    class="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-600 text-xs font-bold rounded-lg transition-colors disabled:opacity-50 flex items-center"
                 >
                    <svg v-if="logsLoading" class="w-3 h-3 mr-2 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    加载更多日志...
                 </button>
            </div>
        </div>
      </div>
    </div>

    <!-- Reusable Session Trace Modal (Rich UI) -->
    <SessionTraceModal
      :visible="showSessionModal"
      :loading="sessionLoading"
      :turns="sessionTurns"
      :active-trace-id="selectedTraceId || ''"
      :show-continue="false"
      :show-delete="false"
      @close="showSessionModal = false"
      @toggle-steps="toggleSessionStep"
    />

    <Toast v-if="toastState.show" :message="toastState.message" :type="toastState.type" @close="toastState.show = false" />
    <ConfirmModal v-if="confirmState.show" :title="confirmState.title" :message="confirmState.message" :type="confirmState.type" @confirm="confirmState.onConfirm" @cancel="confirmState.show = false" />
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background-color: #e5e7eb; border-radius: 10px; }
@keyframes slide-in-right { from { transform: translateX(100%); } to { transform: translateX(0); } }
.animate-slide-in-right { animation: slide-in-right 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards; }

.drawer-slide-enter-active, .drawer-slide-leave-active {
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
.drawer-slide-enter-from, .drawer-slide-leave-to {
  transform: translateX(100%);
}

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
