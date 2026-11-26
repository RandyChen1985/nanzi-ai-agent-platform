<script setup lang="ts">
import { UserIcon,
  BellAlertIcon,
  ChartBarIcon,
  CloudIcon,
  DocumentTextIcon,
  MagnifyingGlassIcon,
  PauseCircleIcon,
  PlayCircleIcon,
  TrashIcon,
} from "@heroicons/vue/24/outline";
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
  PencilSquareIcon,
  ClockIcon,
  PaperAirplaneIcon,
  WrenchScrewdriverIcon,
} from '@heroicons/vue/24/outline'
import { useRoute, useRouter } from 'vue-router'
import MetadataCronInspectionModal from '../components/metadata/MetadataCronInspectionModal.vue'
import HoverTip from '../components/HoverTip.vue'
import CronAdvancedBuilder from '@/components/task/CronAdvancedBuilder.vue'
import CronFieldLegend from '@/components/common/CronFieldLegend.vue'
import { formatCronRunTime, nextCronRunTimes, validateCronExpression } from '@/utils/cronSchedule'
import { describeScene, inferScene } from '@/utils/cronScenes'
import { formatInPlatformTimezoneCompact, getPlatformTimezone } from '@/utils/platformTimezone'
import TaskFlowGuideBanner from '@/components/task/TaskFlowGuideBanner.vue'
import TaskFormSection from '@/components/task/TaskFormSection.vue'
import {
  buildTaskSectionSummaries,
  resolveTaskSectionIssues,
  TASK_SECTION_IDS,
  type TaskSectionKey,
} from '@/utils/taskFormSections'
import TaskPromptComposer, {
  type TaskApprovalMode,
  type TaskResourceScope,
} from '@/components/task/TaskPromptComposer.vue'
import PromptAiOptimize from '@/components/PromptAiOptimize.vue'
import type { ReasoningEffort } from '@/api/model'
// 历史抽屉复用聊天页渲染器：summary 里存的是 markdown 原文（表格、加粗、列表），
// 直接丢进 <p> 会显示成 ## / ** / | 的裸源码。只读模式不接引用面板与产物下载。
import MessageRenderer from '@/components/MessageRenderer.vue'
import '@/styles/messageMarkdownHost.css'

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
const taskTemperatureOverride = ref<number | null>(null)
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
  const temp = cfg.temperature
  taskTemperatureOverride.value = typeof temp === 'number' && Number.isFinite(temp) ? temp : null
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
  taskTemperatureOverride.value = null
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

/**
 * 执行链路按角色取源：admin 可能正在看**别人**的任务（见 `canViewDetails`），
 * 走审计端点 `/chat/admin/logs/*` 才能展开步骤；普通用户走自隔离端点。
 *
 * 自隔离端点在拿不到自己的历史行时返回 404，而这里的 catch 只打 console，
 * 失败表现是「步骤静默为空」——比报错更难发现，所以必须显式分流。
 */
const traceApi = computed(() =>
  userInfo.value?.role === 'admin' ? agentApi.getAdminChatTrace : agentApi.getChatTrace,
)
const showHistoryTab = computed(() => true)
const mainViewTab = ref<'tasks' | 'history'>('tasks')

// View & Filter States
const viewMode = ref<'grid' | 'list'>((localStorage.getItem('task_center_view_mode') as 'grid' | 'list') || 'grid')
const searchQuery = ref('')
const statusFilter = ref<'all' | 'running' | 'stopped' | 'attn'>('all')
const taskTypeFilter = ref<'all' | 'agent' | 'saved_report' | 'metadata_inspection'>('all')
const taskTypeTabs = [
  { value: 'all' as const, label: '全部任务' },
  { value: 'agent' as const, label: '智能体任务' },
  { value: 'saved_report' as const, label: '报表订阅' },
  { value: 'metadata_inspection' as const, label: '元数据巡检' },
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
  tasks.value.filter((task) => task.task_type !== 'saved_report' && task.source !== 'saved_report' && task.task_type !== 'metadata_inspection'),
)

const showCronInspectionModal = ref(false)

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

const isMainAgent = (agent: AIAgent | string) => {
  if (typeof agent === 'string') return agent === 'sys-agent-chat' || agent === 'main'
  return (
    agent.id === 'sys-agent-chat' ||
    ['main', 'assistant', 'general-chat'].includes(String(agent.name || '').trim().toLowerCase())
  )
}

const systemAgents = computed(() => {
  const sys = agents.value.filter((agent) => agent.is_system || isMainAgent(agent))
  return [...sys].sort((a, b) => {
    const aMain = isMainAgent(a) ? 1 : 0
    const bMain = isMainAgent(b) ? 1 : 0
    if (aMain !== bMain) return bMain - aMain
    return (b.sort_order || 0) - (a.sort_order || 0)
  })
})

const customAgents = computed(() => {
  return agents.value
    .filter((agent) => !agent.is_system && !isMainAgent(agent))
    .sort((a, b) => (b.sort_order || 0) - (a.sort_order || 0))
})

const agentTab = ref<'system' | 'custom'>('system')
const agentSearchQuery = ref('')

const filterAgentList = (list: AIAgent[]) => {
  const query = agentSearchQuery.value.trim().toLowerCase()
  if (!query) return list
  return list.filter((agent) => {
    const matchName = String(agent.name || '').toLowerCase().includes(query)
    const matchDisplay = String(agent.display_name || '').toLowerCase().includes(query)
    const matchDesc = String(agent.description || '').toLowerCase().includes(query)
    return matchName || matchDisplay || matchDesc
  })
}

const filteredSystemAgents = computed(() => filterAgentList(systemAgents.value))
const filteredCustomAgents = computed(() => filterAgentList(customAgents.value))

const syncAgentTab = () => {
  if (selectedEditingAgent.value) {
    agentTab.value =
      selectedEditingAgent.value.is_system || isMainAgent(selectedEditingAgent.value)
        ? 'system'
        : 'custom'
  } else {
    agentTab.value = 'system'
  }
}

const currentAgentTabTotalCount = computed(() => {
  return agentTab.value === 'system' ? systemAgents.value.length : customAgents.value.length
})

const showAgentSearchInput = computed(() => {
  return currentAgentTabTotalCount.value > 5 || !!agentSearchQuery.value.trim()
})

const switchAgentTab = (tab: 'system' | 'custom') => {
  if (agentTab.value === tab) return
  agentTab.value = tab
  agentSearchQuery.value = ''
}

const toggleAgentDropdown = () => {
  if (!showAgentDropdown.value) {
    syncAgentTab()
    showAgentDropdown.value = true
  } else {
    showAgentDropdown.value = false
  }
}

const selectEditingAgent = (agentId: string) => {
  editingTask.value.agent_id = agentId
  showAgentDropdown.value = false
  agentSearchQuery.value = ''
}
const handleAgentDropdownOutsideClick = (e: MouseEvent) => {
  if (agentDropdownRef.value && !agentDropdownRef.value.contains(e.target as Node)) {
    showAgentDropdown.value = false
  }
}
const loading = ref(false)
const showEditModal = ref(false)
const showPromptHelpModal = ref(false)
const showConstraintsHelpModal = ref(false)
const taskConstraints = ref('')
/**
 * 第 2 栏「输出有什么要求」的常驻快捷项：点一下追加一句，解决「不知道写什么」。
 * 文案都是可直接进 system 段的祈使句，不与第 1 栏的任务描述重复。
 */
const constraintPresets = [
  { label: '移动端列表', text: '用无序列表呈现，按移动端阅读习惯分行，不要使用宽表格' },
  { label: '只要结论', text: '只输出结论和关键数据，不要过程说明，不要追加反问或建议' },
  { label: '不要图表', text: '不要输出图表代码块（chart / ECharts）' },
  { label: '金额统一万元', text: '金额一律以万元为单位，保留两位小数' },
  { label: '只看华东区', text: '仅统计华东区，排除测试与内部账号' },
] as const
/**
 * 「像是输出要求」的判定：只认明确的祈使 + 形态要求，避免把「查一下图表访问量」这类
 * 正常业务描述误报成格式要求。宁可漏报，也不打断用户写任务。
 */
const PROMPT_FORMAT_ADVICE_RE =
  /((不要|禁止|不允许|别|勿)[^。；;\n]{0,10}(图表|表格|画图|echart|代码块|寒暄|反问|废话|过程说明|猜测|臆测))|((用|按|以|改成)[^。；;\n]{0,6}(无序列表|列表|纯文本|移动端)[^。；;\n]{0,6}(呈现|输出|排版|分行|展示))|((单位|金额)[^。；;\n]{0,6}(统一|一律))|(保留[一二两三四0-9]{1,2}位小数)/i
/** 用户点过「忽略」的那一句：只抑制同一句的提示，内容变了会重新评估，不做持久化 */
const dismissedAdviceSentence = ref('')
const promptFormatAdviceSentence = computed(() => {
  const text = String(editingTask.value.prompt || '')
  if (!text.trim()) return ''
  // 宁可漏报不可乱报：「图表」「表格」单独出现不算（查询图表数据也是正常业务），
  // 必须有祈使前缀或「按列表 / 统一单位 / 保留X位小数」这类形态要求才提示。
  const hit = text
    .split(/[\n。；;]/)
    .map((part) => part.trim())
    .filter(Boolean)
    .find((part) => PROMPT_FORMAT_ADVICE_RE.test(part))
  if (!hit || hit === dismissedAdviceSentence.value) return ''
  return hit
})
const promptFormatAdviceVisible = computed(
  () => Boolean(promptFormatAdviceSentence.value) && String(editingTask.value.prompt || '').trim().length > 0,
)
const moveAdviceToConstraints = () => {
  const sentence = promptFormatAdviceSentence.value
  if (!sentence) return
  const rest = String(editingTask.value.prompt || '')
    .split(/[\n。；;]/)
    .map((part) => part.trim())
    .filter(Boolean)
    .filter((part) => part !== sentence)
  editingTask.value.prompt = rest.length ? `${rest.join('。')}。` : ''
  appendConstraintPreset(sentence.replace(/^[，,、]\s*/, ''))
  showToast('已移到「输出有什么要求」')
}
const appendConstraintPreset = (text: string) => {
  const raw = String(text || '').trim()
  if (!raw) return
  const current = String(taskConstraints.value || '').trim()
  if (current.includes(raw)) {
    showToast('这条已经在输出要求里了', 'warning')
    return
  }
  const base = current.replace(/[；;。]\s*$/, '')
  taskConstraints.value = base ? `${base}；${raw}。` : `${raw}。`
}
const showLogsDrawer = ref(false)
const editingTask = ref<Partial<AgentTask>>({})
/**
 * 方案 C：新建任务时先选模板。
 *
 * 解决的是「不知道该填什么」——用户不会写提示词，但会认「我要一份日报」。
 * 模板只做**预填**：选完落到正常表单，每一栏都能改；占位符统一写成【】，
 * 提示用户把对象换掉（不改也能保存，只是提示词里会留着【】）。
 */
const taskTemplates = [
  {
    id: 'report',
    icon: ChartBarIcon,
    tone: 'text-primary',
    title: '每日 / 每周指标报表',
    desc: '查指定指标，出核心结论 + 关键数据，按周期推送。',
    name: '【指标】日报',
    cronExpr: '0 8 * * *',
    summary: '每天 08:00 · 站内消息',
    prompt: '查【华东一号机房】昨天的【PUE 均值与峰值】，列出告警次数 Top3。',
    constraints: '用无序列表呈现，不要输出图表代码块；关键数字保留两位小数。',
  },
  {
    id: 'inspection',
    icon: BellAlertIcon,
    tone: 'text-amber-500',
    title: '阈值巡检 / 告警',
    desc: '异常显著标注，正常一句话简报，避免天天收长报告。',
    name: '【核心业务库】巡检',
    cronExpr: '0 */1 * * *',
    summary: '每小时 · 站内消息',
    prompt: '检查【核心业务库】的【连接数与慢查询】，超过阈值的项显著标出；无异常时只回一句「本次巡检正常」。',
    constraints: '异常项放最前面并加粗，正常项一句话带过；不要输出图表。',
  },
  {
    id: 'digest',
    icon: DocumentTextIcon,
    tone: 'text-emerald-600',
    title: '周期汇总简报',
    desc: '按「总体结论 → 分项明细 → 风险建议」结构输出周报。',
    name: '【运维组】周报',
    cronExpr: '0 9 * * 1',
    summary: '每周一 09:00 · 站内消息',
    prompt: '汇总【运维组】上周的【告警单】，按「总体结论 → 分项明细 → 风险建议」三段输出。',
    constraints: '用无序列表，不要输出图表；只写结论和关键数据，不要过程说明。',
  },
  {
    id: 'retro',
    icon: MagnifyingGlassIcon,
    tone: 'text-sky-600',
    title: '失败盘点 / 质量复查',
    desc: '汇总本周失败记录，按原因分组并给改进建议。',
    name: '【定时任务】失败盘点',
    cronExpr: '0 18 * * 5',
    summary: '每周五 18:00 · 站内消息',
    prompt: '盘点本周【定时任务】的执行失败记录，按失败原因分组，给出改进建议。',
    constraints: '按原因分组，每组给条数与占比；不要输出图表。',
  },
  {
    id: 'external',
    icon: CloudIcon,
    tone: 'text-teal-600',
    title: '外部信息巡检',
    desc: '调用工具查天气 / 汇率 / 公告，整理成简报。',
    name: '【上海】天气与预警',
    cronExpr: '30 7 * * *',
    summary: '每天 07:30 · 站内消息',
    prompt: '查【上海】今天的【天气与空气质量】，以及【机房所在区】的高温预警。',
    constraints: '三行以内，直接给结论；不要过程说明。',
  },
] as const
/** 新建流程的门槛页：true 时弹窗显示模板卡，选完或点「从空白开始」才进表单 */
const showTemplatePicker = ref(false)
const applyTaskTemplate = (template: (typeof taskTemplates)[number]) => {
  editingTask.value.name = template.name
  editingTask.value.prompt = template.prompt
  editingTask.value.cron_expr = template.cronExpr
  taskConstraints.value = template.constraints
  // 让「运行周期」区的人话控件与 cron 对齐（每天/每周/间隔/高级）
  parseCronToUI(template.cronExpr)
  showTemplatePicker.value = false
  showToast('已套用模板，把【】里的内容换成你的对象就行', 'success')
}
const startBlankTask = () => {
  showTemplatePicker.value = false
}
const closeEditModal = () => {
  showEditModal.value = false
  showTemplatePicker.value = false
}
const notificationChannelOptions = [
  { value: 'portal', label: '站内消息' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'wechat_work', label: '企业微信' },
  { value: 'feishu', label: '飞书' },
  { value: 'email', label: '邮件' },
] as const
const notificationChannels = ref<string[]>(['portal'])
// 任务级「发到哪个群」：渠道 -> 群 ID 字符串（'' 表示跟随个人中心的默认群）。
// 存字符串而不是 number：select 的空选项天然是 ''，混用 number 会被 v-model.number
// 转成 NaN 或 0，反而要处处防御。
const notificationChannelGroups = ref<Record<string, string>>({})
// 个人中心里各渠道的群清单（只用来渲染下拉，权威校验仍在投递时做）
const channelGroupOptions = ref<Record<string, Array<{ id: number; name: string; is_enabled: boolean; is_default: boolean }>>>({})
// 只有这三个渠道有「群」的概念：邮件发的是收件人列表，站内消息没有群
const GROUP_CAPABLE_CHANNELS = ['dingtalk', 'wechat_work', 'feishu'] as const
const selectedGroupChannels = computed(() =>
  GROUP_CAPABLE_CHANNELS.filter((channel) => notificationChannels.value.includes(channel))
)
// 新勾选的渠道必须有一个 '' 初值，否则 v-model 绑到 undefined 上，select 会显示
// 空白而不是「默认群（跟随个人中心设置）」—— 用户看不出这行到底发给谁。
watch(
  selectedGroupChannels,
  (channels) => {
    const next = { ...notificationChannelGroups.value }
    let changed = false
    for (const channel of channels) {
      if (next[channel] === undefined) {
        next[channel] = ''
        changed = true
      }
    }
    if (changed) notificationChannelGroups.value = next
  },
  { immediate: true }
)
const notificationChannelLabel = (channel: string) =>
  notificationChannelOptions.find((option) => option.value === channel)?.label || channel
// 渠道就绪：后端 /notifications/readiness 的权威判定，与发信路径同源。
// 前端**不再自己推断**渠道能不能发：渠道可用性取决于「全局邮件服务是否启用」
// 这类前端根本看不到的事实，自己推断必然与投递路径漂移（曾把可用渠道锁死）。
const notificationReadiness = ref<Record<string, { ready: boolean; reason: string }>>({})
const personalNotificationConfigs = ref<Record<string, any>>({})
const personalNotificationLoading = ref(false)
const promptOverlapsNotificationChannels = computed(() => {
  const prompt = String(editingTask.value.prompt || '').toLowerCase()
  if (!prompt || !notificationChannels.value.length) return false
  const hints: Record<string, string[]> = {
    portal: ['站内', '铃铛', 'inbox', '门户消息', '消息中心'],
    dingtalk: ['钉钉', 'dingtalk'],
    wechat_work: ['企微', '企业微信', 'wechat'],
    feishu: ['飞书', 'feishu', 'lark'],
    email: ['邮件', '邮箱', 'email', 'smtp'],
  }
  return notificationChannels.value.some((channel) =>
    (hints[channel] || []).some((term) => prompt.includes(term.toLowerCase()))
  )
})
/**
 * 邮件渠道的**本地兜底**就绪判定，仅在就绪接口不可用（旧后端 / 瞬时失败）时使用。
 *
 * 必须认 smtp_source：选「使用全局邮件服务」时个人配置里本就没有
 * smtp_host/smtp_user，按「必须填 host」判定会把可用渠道锁死——那正是本 bug。
 */
const isEmailConfigLocallyReady = (cfg: Record<string, any>) => {
  const host = String(cfg?.smtp_host || '').trim()
  const source = String(cfg?.smtp_source || '').trim().toLowerCase() || (host ? 'custom' : 'global')
  // 平台全局 SMTP 是否启用，前端拿不到权威事实：未知不锁死，允许勾选；
  // 真发不出去时后端投递会留下失败原因，好过在此对用户说假话。
  if (source === 'global') return true
  if (!host) return false
  // 免认证内网中继允许账号与授权码同时留空；只填一个属半套凭据。
  return (
    Boolean(String(cfg?.smtp_user || '').trim()) ===
    Boolean(String(cfg?.smtp_password || '').trim())
  )
}
const isNotificationChannelReady = (channel: string) => {
  if (channel === 'portal') return true
  const verdict = notificationReadiness.value[channel]
  if (verdict) return Boolean(verdict.ready)
  // 就绪接口不可用时的退化路径：用本地判定，且不因未知把渠道全锁死。
  const cfg = personalNotificationConfigs.value[channel]
  if (!cfg || !cfg.is_enabled) return false
  if (channel === 'dingtalk' || channel === 'wechat_work' || channel === 'feishu') {
    return Boolean(String(cfg.webhook_url || '').trim())
  }
  if (channel === 'email') return isEmailConfigLocallyReady(cfg)
  return false
}
const unavailableExternalChannels = computed(() =>
  notificationChannelOptions
    .filter((c) => c.value !== 'portal' && !isNotificationChannelReady(c.value))
    .map((c) => ({
      label: c.label,
      reason: String(notificationReadiness.value[c.value]?.reason || '').trim() || '尚未配置或未启用',
    }))
)
const unavailableExternalChannelsHint = computed(() =>
  unavailableExternalChannels.value.map((c) => `${c.label}（${c.reason}）`).join('、')
)
// 悬浮提示要给出**具体**原因（如「平台尚未启用全局邮件服务」），
// 否则用户面对灰按钮只能反复去个人中心重填已经填好的表单。
const notificationChannelBlockedTitle = (channel: string) => {
  const reason = String(notificationReadiness.value[channel]?.reason || '').trim()
  return reason
    ? `${reason}（可在个人中心 → 消息通知中查看或配置）`
    : '请先在个人中心 → 消息通知中配置并启用该通道'
}
const groupOptionsFor = (channel: string) => {
  // 只列启用的群；旧键名 enabled 兜底，避免部署窗口期读不到就当作「全部停用」
  const list = (channelGroupOptions.value[channel] || []).filter(
    (group) => group.is_enabled ?? (group as { enabled?: boolean }).enabled
  )
  const options = list.map((group) => ({
    id: group.id,
    label: `${group.name || '未命名群'}（id ${group.id}）${group.is_default ? ' · 默认' : ''}`,
  }))
  const current = Number(notificationChannelGroups.value[channel])
  if (current > 0 && !list.some((group) => group.id === current)) {
    // 群被删或停用后仍然显示出来：静默改发默认群会让人以为任务还在发原群
    options.push({ id: current, label: `id ${current}（已失效，将改发默认群）` })
  }
  return options
}
const pruneUnavailableNotificationChannels = () => {
  notificationChannels.value = notificationChannels.value.filter((channel) =>
    isNotificationChannelReady(channel)
  )
  // 渠道被裁剪掉后，它的群选择留着只会在保存时写进一份没人读的配置
  for (const channel of Object.keys(notificationChannelGroups.value)) {
    if (!notificationChannels.value.includes(channel)) {
      delete notificationChannelGroups.value[channel]
    }
  }
}
const fetchNotificationReadiness = async () => {
  try {
    const res = await axios.get('/api/portal/notifications/readiness')
    notificationReadiness.value = res.data?.channels || {}
  } catch (error) {
    console.warn('Failed to load notification channel readiness', error)
    notificationReadiness.value = {}
  }
}
const fetchPersonalNotificationConfigs = async () => {
  personalNotificationLoading.value = true
  try {
    const [configRes, groupsRes] = await Promise.all([
      axios.get('/api/portal/notifications/config'),
      // 群清单失败不影响主流程：下拉里只剩「默认群」，任务照样能保存
      axios.get('/api/portal/notifications/channels').catch(() => null),
    ])
    personalNotificationConfigs.value = configRes.data || {}
    channelGroupOptions.value = groupsRes?.data || {}
  } catch (error) {
    console.warn('Failed to load personal notification configs', error)
    personalNotificationConfigs.value = {}
  } finally {
    personalNotificationLoading.value = false
  }
}
// 就绪判定（权威）与配置明细（兜底）一起取，再统一裁剪掉不可用的已选渠道——
// 顺序不能反：readiness 未到位就裁剪会按兜底规则误删用户原本选中的渠道。
const refreshNotificationAvailability = async () => {
  await Promise.all([fetchNotificationReadiness(), fetchPersonalNotificationConfigs()])
  pruneUnavailableNotificationChannels()
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
    tip: '查数类任务写清对象、时间范围与要什么结论即可',
    text: `查询华东一号机房昨天的 PUE 峰值与均值，并列出告警次数 Top3。
要什么结论：核心结论 + 关键指标对比。`,
  },
  {
    title: '仅业务指令（推荐）',
    tip: '通知渠道请用下方「结果通知」勾选，第 1 栏专注业务本身',
    text: `汇总本周任务失败次数，按失败原因分组，给出可执行的改进建议（3 条以内）。`,
  },
] as const
const applyPromptExample = (text: string) => {
  editingTask.value.prompt = text
  showPromptHelpModal.value = false
  showToast('已填入示例，可按需修改')
}
const constraintExamples = [
  {
    title: '移动端列表交付',
    tip: '不要图表，用列表排版',
    text: '用无序列表呈现结论与关键数据；禁止输出图表代码块（chart / ECharts）；不要使用宽表格；按移动端阅读习惯分行。',
  },
  {
    title: '统一口径与单位',
    tip: '固化指标口径',
    text: '金额一律以万元为单位并保留两位小数；时间口径按自然日；同比对比去年同期。',
  },
  {
    title: '限定数据范围',
    tip: '收窄统计范围',
    text: '仅统计华东区；排除测试账号与内部账号；只取已生效订单。',
  },
  {
    title: '精简交付',
    tip: '只要结论',
    text: '只输出结论与关键数据，不要过程说明、不要复述任务；不要追加反问或建议。',
  },
]

const applyConstraintExample = (text: string) => {
  taskConstraints.value = text
  showConstraintsHelpModal.value = false
}
const promptTemplates = [
  {
    title: '通用结构',
    desc: '目标 / 范围 / 结果形式四段骨架',
    text: `【任务目标】____（要做什么，如：查询 / 统计 / 汇总…）
【数据范围】时间：____（如：昨天 / 本周）；对象：____（如：某机房 / 某项目）
【结果形式】____（要什么结论，如：核心结论 + 关键指标对比）
【其他说明】____（可选，如：超过阈值请重点标注；不需要可删除本行）
※ 排版 / 单位 / 不要图表这类要求请写到下方「输出有什么要求」，那边才会强制生效。`,
  },
  {
    title: '数据查询 / 报表',
    desc: '查数出报表，含环比对比',
    text: `查询 ____（对象，如：华东一号机房）在 ____（时间范围，如：昨天）的 ____（指标，如：PUE 峰值与均值）。
要说清的结论：核心结论先行 + 关键指标对比上一周期的变化。`,
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

/** 行操作菜单（⋯）点空白处收起 */
const handleTaskMenuOutsideClick = (e: MouseEvent) => {
  if (taskMenuId.value === null) return
  const target = e.target as HTMLElement | null
  if (!target?.closest?.('[data-task-menu]')) taskMenuId.value = null
}
const applyPromptTemplate = (text: string) => {
  editingTask.value.prompt = text
  showPromptTemplateDropdown.value = false
  showToast('已插入模板，骨架仅供参考，可自由增删')
}
const clearTaskPrompt = () => {
  editingTask.value.prompt = ''
  showToast('已清空任务内容')
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

/**
 * 用「容器宽度」而不是「窗口宽度」决定列表能不能放得下。
 *
 * 个人中心把 TaskCenter 嵌进约 900–1100px 的窄容器里，此时窗口可能很宽、
 * 但表格（至少 820px + 横向滚动）在容器里必然溢出，用户得左右拖才能看到状态列；
 * 反过来窗口很窄、容器却更窄时 `isMobile` 也判不出来。ResizeObserver 量容器
 * 才是唯一可靠的依据。
 */
const rootRef = ref<HTMLElement | null>(null)
const containerWidth = ref(0)
let containerObserver: ResizeObserver | null = null

const containerIsNarrow = computed(() => containerWidth.value > 0 && containerWidth.value < 760)
const showAgentColumn = computed(() => containerWidth.value === 0 || containerWidth.value >= 980)
const showRateColumn = computed(() => containerWidth.value === 0 || containerWidth.value >= 880)

const currentViewMode = computed(() =>
  isMobile.value || containerIsNarrow.value ? 'grid' : viewMode.value,
)

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
  document.addEventListener('click', handleTaskMenuOutsideClick)
  if (rootRef.value && typeof ResizeObserver !== 'undefined') {
    containerObserver = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) containerWidth.value = entry.contentRect.width
    })
    containerObserver.observe(rootRef.value)
  }
})
onUnmounted(() => {
  if (historyFilterTimer !== undefined) window.clearTimeout(historyFilterTimer)
  clearRunNowTimers()
  window.removeEventListener('resize', handleWindowResize)
  document.removeEventListener('click', handleAgentDropdownOutsideClick)
  document.removeEventListener('click', handlePromptTemplateOutsideClick)
  document.removeEventListener('click', handleTaskMenuOutsideClick)
  if (containerObserver) {
    containerObserver.disconnect()
    containerObserver = null
  }
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

// Cron 可视化生成器（自定义模式下的面板）
const showCronBuilder = ref(false)
const cronDraft = ref('* * * * *')
const cronDraftValid = computed(() => validateCronExpression(cronDraft.value).valid)

const openCronBuilder = () => {
  const current = String(editingTask.value.cron_expr || '').trim()
  cronDraft.value = current || '* * * * *'
  showCronBuilder.value = true
}

const applyCronDraft = () => {
  if (!cronDraftValid.value) return
  editingTask.value.cron_expr = cronDraft.value.trim()
  showCronBuilder.value = false
}

// Cron Sync: UI -> Expression
watch([cronMode, cronConfig], () => {
  if (!editingTask.value) return
  const { time, weekday, day, intervalValue, intervalUnit } = cronConfig.value
  
  if (cronMode.value === 'custom') return // Don't overwrite if custom

  try {
      let expr = ''
      const [h, m] = (time || '00:00').split(':').map(Number)
      // 若 time 被清空或格式非法，h/m 可能是 NaN 或缺失，直接 bail out。
      // 下标访问在 noUncheckedIndexedAccess 下是 `number | undefined`，先判一下。
      if (h === undefined || m === undefined || isNaN(h) || isNaN(m)) {
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

// 「高级」模式输入框直接绑定 editingTask.cron_expr，这里做即时校验：
// 非法时把输入框本身标红，并在下方给出具体到字段的原因，而不是一句笼统的「格式不正确」
const cronExprValidation = computed(() => {
  const expr = String(editingTask.value?.cron_expr || '').trim()
  // 空值不标红，交给「请输入」提示与保存时的必填校验
  if (!expr) return { valid: true, message: '' }
  return validateCronExpression(expr)
})

const cronDescription = computed(() => {
  const expr = String(editingTask.value?.cron_expr || '').trim()
  if (!expr) return '请输入 Cron 表达式'
  if (expr.includes('NaN')) return '表达式格式不正确'
  if (!cronExprValidation.value.valid) return cronExprValidation.value.message
  try {
    return cronstrue.toString(expr, { locale: 'zh_CN' })
  } catch (e) {
    return '表达式格式不正确'
  }
})

/**
 * 「接下来三次运行」：文字翻译（Auto-Translate）证明不了日期，用户没法确认自己
 * 填的是本周四还是下周四。这里直接把未来的具体时间点摆出来，用平台业务时区渲染
 * （与后端调度同一时区口径，见 utils/cronSchedule 的注释）。
 */
const cronNextRuns = computed(() => {
  const expr = String(editingTask.value?.cron_expr || '').trim()
  if (!cronExprValidation.value.valid) return []
  return nextCronRunTimes(expr, 3).map((date) => formatCronRunTime(date))
})

/**
 * 三段折叠：默认全展开。已确认「打开就要看全貌，想收再收」，
 * 因此不做持久化、不跨弹窗记忆；编辑已有任务同样全展开。
 */
const showWhatSection = ref(true)
const showWhenSection = ref(true)
const showResultSection = ref(true)

const expandSection = (key: TaskSectionKey | null) => {
  if (!key) return
  if (key === 'what') showWhatSection.value = true
  else if (key === 'when') showWhenSection.value = true
  else showResultSection.value = true
  if (typeof document !== 'undefined') {
    document
      .getElementById(TASK_SECTION_IDS[key])
      ?.scrollIntoView({ block: 'nearest' })
  }
}

/** 重试策略是低频技术项，默认收起，避免和第 1 栏抢视线 */
const showAdvancedRetry = ref(false)

/**
 * 保存前的复核面：把散在 6 个字段里的意图拼成一句话。
 * 只做展示，不参与保存 —— 措辞必须与真实行为一致，不确定的一律不写。
 */
const taskSummaryText = computed(() => {
  const prompt = String(editingTask.value?.prompt || '').trim()
  const firstSentence = prompt
    ? (prompt.split(/[\n。；;]/).map((part) => part.trim()).filter(Boolean)[0] || '')
    : ''
  const channels = notificationChannels.value
    .map((value) => notificationChannelOptions.find((option) => option.value === value)?.label ?? '')
    .filter(Boolean)
  return {
    when: cronDescription.value,
    whenValid: Boolean(String(editingTask.value?.cron_expr || '').trim()) && cronExprValidation.value.valid,
    agent: selectedEditingAgent.value?.display_name || '',
    what: firstSentence,
    constraints: String(taskConstraints.value || '')
      .split(/[；;。\n]/)
      .map((part) => part.trim())
      .filter(Boolean),
    channels,
  }
})
const taskSummaryNext = computed(() => cronNextRuns.value[0] || '')

const taskSectionSummary = computed(() =>
  buildTaskSectionSummaries({
    name: String(editingTask.value?.name || ''),
    agent: taskSummaryText.value.agent,
    firstSentence: taskSummaryText.value.what,
    when: taskSummaryText.value.when,
    whenValid: taskSummaryText.value.whenValid,
    nextRun: taskSummaryNext.value,
    channels: taskSummaryText.value.channels,
  }),
)

const taskSectionIssues = computed(() =>
  resolveTaskSectionIssues({
    name: String(editingTask.value?.name || ''),
    prompt: String(editingTask.value?.prompt || ''),
    cronExpr: String(editingTask.value?.cron_expr || ''),
    cronValid: cronExprValidation.value.valid,
  }),
)

/** 保存失败时展开出错的组并滚到可见——收起草稿里报错等于让用户找不到。 */

// Filtered Tasks
const scopedTasks = computed(() => {
  if (props.personalOnly) {
    return tasks.value.filter((task) => isTaskOwner(task))
  }
  return tasks.value
})

const taskTypeCounts = computed(() => ({
  all: scopedTasks.value.length,
  agent: scopedTasks.value.filter(task => task.task_type !== 'saved_report' && task.task_type !== 'metadata_inspection').length,
  saved_report: scopedTasks.value.filter(task => task.task_type === 'saved_report').length,
  metadata_inspection: scopedTasks.value.filter(task => task.task_type === 'metadata_inspection').length,
}))

const filteredTasks = computed(() => {
  let result = [...scopedTasks.value]
  if (taskTypeFilter.value === 'agent') {
    result = result.filter(task => task.task_type !== 'saved_report' && task.task_type !== 'metadata_inspection')
  } else if (taskTypeFilter.value === 'saved_report') {
    result = result.filter(task => task.task_type === 'saved_report')
  } else if (taskTypeFilter.value === 'metadata_inspection') {
    result = result.filter(task => task.task_type === 'metadata_inspection')
  }
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    result = result.filter(t => t.name.toLowerCase().includes(q) || t.prompt.toLowerCase().includes(q))
  }
  if (statusFilter.value !== 'all') {
    if (statusFilter.value === 'attn') {
      result = result.filter(t => {
        const key = taskStatusMeta(t).key
        return key === 'error' || key === 'warn'
      })
    } else {
      const wantOn = statusFilter.value === 'running'
      result = result.filter(t => (taskStatusMeta(t).key === 'on') === wantOn)
    }
  }
  return sortTaskList(result)
})

/** 顶部概览：三类状态互斥计数 + 最近一次待执行任务，替代原先只能靠肉眼扫的列表 */
const statusSummary = computed(() => {
  const list = scopedTasks.value
  let on = 0
  let off = 0
  let attn = 0
  list.forEach((task) => {
    const key = taskStatusMeta(task).key
    if (key === 'on') on += 1
    else if (key === 'off') off += 1
    else attn += 1
  })
  const upcoming = list
    .filter(task => task.status === 1 && task.next_run_at)
    .sort((a, b) => String(a.next_run_at).localeCompare(String(b.next_run_at)))[0]
  const pctOf = (n: number) => (list.length ? Math.round((n / list.length) * 100) : 0)
  return {
    on,
    off,
    attn,
    total: list.length,
    upcoming,
    onPct: pctOf(on),
    offPct: pctOf(off),
    attnPct: pctOf(attn),
    // 4~5 个任务画占比条没有意义（每段都是一大块），少于 6 条退化成纯计数
    compact: list.length < 6,
  }
})

/** 概览条默认收起（一行说完），只有用户主动展开过才保持展开，别每次刷新又弹回来 */
const overviewCollapsed = ref(localStorage.getItem('task_center_overview_collapsed') !== '0')
const toggleOverview = () => {
  overviewCollapsed.value = !overviewCollapsed.value
  localStorage.setItem('task_center_overview_collapsed', overviewCollapsed.value ? '1' : '0')
}

/** 三个状态计数，折叠态与展开态共用一套渲染 */
const statusChips = computed(() => {
  const summary = statusSummary.value
  return [
    { key: 'running' as const, dot: 'bg-emerald-500', label: '运行中', count: summary.on, clickable: true, title: '只看运行中的任务' },
    { key: 'stopped' as const, dot: 'bg-gray-300', label: '已暂停', count: summary.off, clickable: true, title: '只看已暂停的任务' },
    {
      key: 'attn' as const,
      dot: summary.attn > 0 ? 'bg-red-500' : 'bg-gray-300',
      label: summary.attn > 0 ? '需处理' : '全部正常',
      count: summary.attn,
      clickable: summary.attn > 0,
      title: summary.attn > 0 ? '只看异常 / 需核对的任务' : '当前没有需要处理的任务',
    },
  ]
})

/**
 * 顶部风险提醒：从需处理的任务里挑最严重的一条说清楚「哪个任务、为什么、什么时候重试」。
 *
 * 列表有 15+ 条时，异常任务混在中间很容易漏看；红色提醒条把「连续失败 N 次 + 原因 + 下次重试」
 * 提到最上面，比让用户逐行扫状态徽章可靠。
 */
const attentionHeadline = computed(() => {
  const candidates = scopedTasks.value
    .map((task) => ({ task, meta: taskStatusMeta(task) }))
    .filter((item) => item.meta.key === 'error' || item.meta.key === 'warn')
  const severity = (item: { task: AgentTask; meta: { key: string } }) =>
    (item.meta.key === 'error' ? 1000 : 0) + Number(item.task.consecutive_failures || 0)
  candidates.sort((a, b) => severity(b) - severity(a))
  const top = candidates[0]
  if (!top) return null
  const task = top.task

  const rawReason = task.last_error
    ? `${Number(task.consecutive_failures || 0) > 1 ? `连续失败 ${task.consecutive_failures} 次：` : ''}${task.last_error}`
    : (taskScheduleMismatch(task) || taskStatusTip(task))
  const reason = rawReason.length > 90 ? `${rawReason.slice(0, 90)}…` : rawReason
  const next = nextRunCell(task)

  return {
    task,
    reason,
    count: candidates.length,
    isError: top.meta.key === 'error',
    retryHint: next.muted ? '' : `${next.text}会自动重试。`,
  }
})

const toastState = ref({ show: false, message: '', type: 'success' as any })
const showToast = (message: string, type: 'success' | 'error' | 'warning' | 'info' = 'success') => {
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
    const res = await agentApi.listAllowedAgents()
    agents.value = res.data
  } catch (e) { 
    console.error('Failed to fetch agents', e) 
  }
}

const openCreateModal = async () => {
  const defaultAgentId = systemAgents.value[0]?.id || agents.value[0]?.id || ''
  editingTask.value = { name: '', agent_id: defaultAgentId, cron_expr: '0 8 * * *', prompt: '', status: 1 }
  notificationChannels.value = ['portal']
  notificationChannelGroups.value = {}
  taskConstraints.value = ''
  hydrateExecutionOptions({})
  showAgentDropdown.value = false
  agentSearchQuery.value = ''
  syncAgentTab()
  cronMode.value = 'daily'
  cronConfig.value = { time: '08:00', weekday: 1, day: 1, intervalValue: 30, intervalUnit: 'minutes' }
  // 新建先过一层模板门槛（方案 C）；编辑已有任务不进模板页
  showTemplatePicker.value = true
  showEditModal.value = true
  await refreshNotificationAvailability()
}

const openEditModal = async (task: AgentTask) => {
  if (task.task_type === 'metadata_inspection') {
    showToast('元数据巡检为系统级内置任务，请前往【元数据管理】配置执行周期', 'info')
    return
  }
  if (task.task_type === 'saved_report') {
    openSavedReportSubscriptionSettings(task)
    return
  }
  editingTask.value = { ...task }
  const cfg = task.config && typeof task.config === 'object' ? task.config : {}
  notificationChannels.value = Array.isArray(cfg.notification_channels)
    ? cfg.notification_channels.map((c: string) => String(c))
    : []
  notificationChannelGroups.value = {}
  const rawGroups = cfg.notification_channel_groups
  if (rawGroups && typeof rawGroups === 'object' && !Array.isArray(rawGroups)) {
    for (const [channel, value] of Object.entries(rawGroups as Record<string, unknown>)) {
      const groupId = Number(value)
      if (groupId > 0) notificationChannelGroups.value[channel] = String(groupId)
    }
  }
  taskConstraints.value = String(cfg.task_constraints || '')
  hydrateExecutionOptions(cfg)
  showAgentDropdown.value = false
  agentSearchQuery.value = ''
  syncAgentTab()
  parseCronToUI(task.cron_expr || '')
  showTemplatePicker.value = false
  showEditModal.value = true
  await refreshNotificationAvailability()
}

const saveTask = async () => {
  try {
    // 名称与提示词在后端是必填（422）；前端前移校验，否则收起态下报错位置不可见
    const sectionIssues = resolveTaskSectionIssues({
      name: String(editingTask.value?.name || ''),
      prompt: String(editingTask.value?.prompt || ''),
      cronExpr: String(editingTask.value?.cron_expr || ''),
      cronValid: cronExprValidation.value.valid,
    })
    if (sectionIssues.issues.what) {
      expandSection('what')
      showToast(
        String(editingTask.value?.name || '').trim()
          ? '请填写「要做什么」'
          : '请填写任务名称',
        'error',
      )
      return
    }
    // 非法表达式就地拦截：后端会返回 422，但原因埋在 detail 里，不如直接说明是哪一段错了
    const cronExpr = String(editingTask.value?.cron_expr || '').trim()
    if (!cronExpr) {
      expandSection('when')
      showToast('请输入 Cron 表达式', 'error')
      return
    }
    if (!cronExprValidation.value.valid) {
      expandSection('when')
      showToast(cronExprValidation.value.message, 'error')
      return
    }
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
    // 只保留仍然勾选、且选了具体群的渠道；其余一律不写，读的时候自然回落到默认群
    const groupPayload: Record<string, number> = {}
    for (const channel of notificationChannels.value) {
      const groupId = Number(notificationChannelGroups.value[channel])
      if (groupId > 0) groupPayload[channel] = groupId
    }
    if (Object.keys(groupPayload).length) {
      baseConfig.notification_channel_groups = groupPayload
    } else {
      delete baseConfig.notification_channel_groups
    }
    const constraints = String(taskConstraints.value || '').trim()
    if (constraints) baseConfig.task_constraints = constraints
    else delete baseConfig.task_constraints
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
    if (taskTemperatureOverride.value !== null && taskTemperatureOverride.value !== undefined) {
      baseConfig.temperature = taskTemperatureOverride.value
    } else {
      delete baseConfig.temperature
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
  if (task.task_type === 'metadata_inspection') {
    showToast('元数据巡检为系统内置任务，不支持删除；如需停用请关闭任务开关', 'warning')
    return
  }
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
        isExpanded: false,       // 执行步骤展开
        steps: [],
        stepsLoading: false,
        mdExpanded: null,        // 正文展开：null 跟随全局密度，true/false 为单条覆盖
        // 短摘要不值得给「展开全文」按钮
        mdExpandable: String(item.summary || item.query || '').length > 180,
    }))
    
    if (append) {
        logs.value = [...logs.value, ...newItems]
    } else {
        logs.value = newItems
    }
    logsTotal.value = res.data.data.total
  } catch (e) { showToast('获取日志失败', 'error') } finally { logsLoading.value = false }
}

/** 历史抽屉：只看失败（打开这个抽屉最常见的原因就是「刚才那次怎么失败了」） */
const logsFilter = ref<'all' | 'fail'>('all')
/** 历史抽屉：「原文」开关，渲染异常时就地自查，不必让用户去猜 markdown 长什么样 */
const showRawMarkdown = ref(false)

const isLogOk = (log: any) => String(log?.status || '').toLowerCase() === 'success'

/** 历史按天分组：今天 / 昨天 / 前天 / 11-22（同年省略年份，跨年才带年） */
const logDayLabel = (value?: string): string => {
  const raw = String(value || '').trim()
  if (!raw) return '未知时间'
  const target = new Date(raw)
  if (Number.isNaN(target.getTime())) return raw.slice(0, 10)
  const targetParts = platformClockParts(target)
  const nowParts = platformClockParts(new Date())
  const diffDays = Math.round(
    (Date.parse(`${targetParts.ymd}T00:00:00Z`) - Date.parse(`${nowParts.ymd}T00:00:00Z`)) / 86400000,
  )
  if (diffDays === 0) return '今天'
  if (diffDays === -1) return '昨天'
  if (diffDays === -2) return '前天'
  const [year, month, day] = targetParts.ymd.split('-')
  return year === nowParts.ymd.split('-')[0] ? `${month}-${day}` : `${year}-${month}-${day}`
}

/** 组内只显示时刻，日期由分组头交代，不必每条重复「今天」 */
const logClock = (value?: string): string => {
  const raw = String(value || '').trim()
  if (!raw) return '—'
  const target = new Date(raw)
  if (Number.isNaN(target.getTime())) return ''
  return platformClockParts(target).hhmm
}

/** 紧凑行的一句话摘要：去掉 markdown 标记，只留能扫读的文字 */
const logPreview = (log: any, max = 64): string => {
  const text = String(log?.summary || log?.query || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[#*`>|_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return '（无摘要）'
  return text.length > max ? `${text.slice(0, max)}…` : text
}

/**
 * 抽屉密度：compact 每条压成一行 / normal 正文折叠 132px / expanded 全部展开。
 * 记在本地；单条点过之后以单条为准（mdExpanded 非 null），一键按钮会把单条选择清掉统一覆盖。
 */
const logsDensity = ref<'compact' | 'normal' | 'expanded'>(
  (localStorage.getItem('task_center_logs_density') as 'compact' | 'normal' | 'expanded') || 'normal',
)
const setLogsDensity = (mode: 'compact' | 'expanded') => {
  const next = logsDensity.value === mode ? 'normal' : mode
  logsDensity.value = next
  localStorage.setItem('task_center_logs_density', next)
  logs.value.forEach((log: any) => { log.mdExpanded = null })
}

const logShowsBody = (log: any): boolean => {
  if (log?.mdExpanded === true) return true
  if (log?.mdExpanded === false) return false
  return logsDensity.value === 'expanded'
}
const logIsCompactRow = (log: any): boolean => logsDensity.value === 'compact' && log?.mdExpanded !== true

const logsGroupCollapsed = ref<Record<string, boolean>>({})
const toggleLogsGroup = (day: string) => {
  logsGroupCollapsed.value = { ...logsGroupCollapsed.value, [day]: !logsGroupCollapsed.value[day] }
}

const groupedLogs = computed(() => {
  const order: string[] = []
  const buckets: Record<string, any[]> = {}
  filteredLogs.value.forEach((log: any) => {
    const day = logDayLabel(log.created_at)
    if (!buckets[day]) {
      buckets[day] = []
      order.push(day)
    }
    buckets[day].push(log)
  })
  return order.map((day) => {
    const runs = buckets[day] || []
    const ok = runs.filter(isLogOk).length
    return { day, runs, ok, fail: runs.length - ok }
  })
})

const filteredLogs = computed(() => (
  logsFilter.value === 'fail' ? logs.value.filter((log: any) => !isLogOk(log)) : logs.value
))

/** 概览只统计已加载的这批（接口按页返回，没加载的不该算进来） */
const logsSummary = computed(() => {
  const items = logs.value as any[]
  const ok = items.filter(isLogOk).length
  const durations = items
    .map((log) => Number(log.execution_time_ms || 0))
    .filter((ms) => ms > 0)
  const avg = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0
  return { loaded: items.length, ok, fail: items.length - ok, avg: avg ? formatDurationMs(avg) : '—' }
})

const toggleLogSteps = async (log: any) => {
    log.isExpanded = !log.isExpanded
    if (log.isExpanded && (!log.steps || log.steps.length === 0)) {
        log.stepsLoading = true
        try {
            const res = await traceApi.value(log.trace_id)
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
    const res = await traceApi.value(traceId)
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

const formatTaskSchedule = (cron: string) => {
  const text = String(cron || '').trim()
  if (!text) return '未配置'
  const inferred = inferScene(text)
  if (inferred.scene !== 'custom') {
    // 场景摘要比 cronstrue 的完整描述短，表格列不会被撑爆
    return describeScene(inferred.scene, inferred.state).summary.replace(/ 执行$/, '')
  }
  return text
}

const formatTaskScheduleFull = (cron: string) => {
  const text = String(cron || '').trim()
  if (!text) return '未配置'
  try { return cronstrue.toString(text, { locale: 'zh_CN' }) } catch { return text }
}

const cronScheduleTip = (cron: string) => {
  const text = String(cron || '').trim()
  if (!text) return '未配置'
  const full = formatTaskScheduleFull(text)
  return full === text ? `Cron: ${text}` : `${full}；Cron: ${text}`
}

/**
 * 合成展示状态：把一个任务的「调度开关」(status) 和「执行健康」(health_status) 压成一个
 * 用户能直接决策的信号。优先级 异常 > 需核对 > 活跃 > 已暂停。
 *
 * 之前列表只写 `status === 1 ? '活跃' : '停止'`，而后端 status 是 0 停 / 1 跑 / 2 错误，
 * 出错被停的任务会显示成「停止」，看起来像用户自己关的；健康徽章又单独出现，
 * 于是出现「健康 + 失败 2」这种自相矛盾的组合。
 */
type TaskDisplayStatusKey = 'error' | 'warn' | 'on' | 'off'

const taskStatusMeta = (task: AgentTask): {
  key: TaskDisplayStatusKey
  label: string
  class: string
  dot: string
  rank: number
} => {
  const health = String(task.health_status || 'unknown')
  if (health === 'error') {
    return { key: 'error', label: '异常', class: 'bg-red-50 text-red-700 border-red-200', dot: 'bg-red-500', rank: 0 }
  }
  if (health === 'warning') {
    return { key: 'warn', label: '需核对', class: 'bg-amber-50 text-amber-700 border-amber-200', dot: 'bg-amber-500', rank: 1 }
  }
  if (task.status === 1) {
    return { key: 'on', label: '活跃', class: 'bg-green-50 text-green-700 border-green-200', dot: 'bg-green-500', rank: 2 }
  }
  if (task.status === 2) {
    return { key: 'error', label: '异常', class: 'bg-red-50 text-red-700 border-red-200', dot: 'bg-red-500', rank: 0 }
  }
  return { key: 'off', label: '已暂停', class: 'bg-gray-100 text-gray-500 border-gray-200', dot: 'bg-gray-400', rank: 3 }
}

const taskStatusTip = (task: AgentTask) => {
  const meta = taskStatusMeta(task)
  if (meta.key === 'error') {
    const times = Number(task.consecutive_failures || 0)
    const tail = task.last_error ? `：${task.last_error}` : ''
    return times > 0 ? `调度仍在运行，但已连续失败 ${times} 次${tail}` : `最近一次执行失败${tail}`
  }
  if (meta.key === 'warn') {
    return String(task.last_error || task.last_message || '健康状态需要关注，建议查看最近执行记录')
  }
  if (meta.key === 'on') return '调度运行中，会按周期自动执行'
  return '调度已暂停，不会自动执行'
}

/** 成功率：成功 / (成功 + 失败)；一次都没跑过时返回 null，由调用方显示「—」 */
const taskRateNum = (task: AgentTask): number | null => {
  const success = Number(task.success_count ?? task.run_count ?? 0)
  const fail = Number(task.failure_count ?? 0)
  const total = success + fail
  if (!total) return null
  return Math.round((success / total) * 100)
}

const taskRateText = (task: AgentTask) => {
  const rate = taskRateNum(task)
  return rate === null ? '—' : `${rate}%`
}

/** 卡片/列表统一的「运行 N 次 · 成功率 x%」口径，替代卡片四宫格与列表里两套算法 */
const taskMetricText = (task: AgentTask) => {
  const runs = Number(task.trigger_count ?? task.success_count ?? task.run_count ?? 0)
  return { runs, rate: taskRateText(task), rateNum: taskRateNum(task) }
}

/** 成功率跌破 90% 才标红；没有分母时（rateNum 为 null）不判红 */
const taskRateIsLow = (task: AgentTask): boolean => {
  const rate = taskMetricText(task).rateNum
  return rate !== null && rate < 90
}

// ── 下次运行：相对时间打头，绝对时间兜底 ───────────────────────────────
/**
 * Intl.DateTimeFormat 构造开销不小，而列表里每张卡片会算 3~4 次时间文案，
 * 所以按平台时区缓存 formatter 实例，只在时区变化时重建。
 */
let clockFormatterCache: {
  tz: string
  parts: Intl.DateTimeFormat
  weekday: Intl.DateTimeFormat
} | null = null

const clockFormatters = () => {
  const tz = getPlatformTimezone()
  if (!clockFormatterCache || clockFormatterCache.tz !== tz) {
    clockFormatterCache = {
      tz,
      parts: new Intl.DateTimeFormat('zh-CN', {
        timeZone: tz,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }),
      weekday: new Intl.DateTimeFormat('zh-CN', { timeZone: tz, weekday: 'short' }),
    }
  }
  return clockFormatterCache
}

const platformClockParts = (date: Date) => {
  const formatters = clockFormatters()
  const parts = formatters.parts.formatToParts(date)
  const pick = (type: string) => parts.find((part) => part.type === type)?.value || ''
  return {
    ymd: `${pick('year')}-${pick('month')}-${pick('day')}`,
    hhmm: `${pick('hour')}:${pick('minute')}`,
    weekday: formatters.weekday.format(date),
  }
}

/**
 * 「明天 09:00 / 周三 10:00 / 11-30 09:00」，配合绝对时间一起展示。
 * 只写 `11/24 09:00` 时，用户判断「多久之后」要靠心算，跨年还会误判。
 */
const formatRunRelative = (value?: string): string => {
  const raw = String(value || '').trim()
  if (!raw) return ''
  const target = new Date(raw)
  if (Number.isNaN(target.getTime())) return formatInPlatformTimezoneCompact(raw)
  const targetParts = platformClockParts(target)
  const nowParts = platformClockParts(new Date())
  const diffDays = Math.round(
    (Date.parse(`${targetParts.ymd}T00:00:00Z`) - Date.parse(`${nowParts.ymd}T00:00:00Z`)) / 86400000,
  )
  if (diffDays === 0) return `今天 ${targetParts.hhmm}`
  if (diffDays === 1) return `明天 ${targetParts.hhmm}`
  if (diffDays === 2) return `后天 ${targetParts.hhmm}`
  if (diffDays > 2 && diffDays <= 6) return `${targetParts.weekday} ${targetParts.hhmm}`.trim()
  return formatInPlatformTimezoneCompact(raw)
}

/** 暂停任务不再写「暂无计划」——那和「配置有问题的没有下次」长得一模一样 */
const nextRunCell = (task: AgentTask) => {
  if (task.status !== 1) {
    return {
      text: '已暂停',
      abs: '',
      muted: true,
      tip: task.cron_expr ? `停止前周期：${formatTaskSchedule(task.cron_expr)}（恢复后重新排期）` : '任务已暂停',
    }
  }
  const raw = String(task.next_run_at || '').trim()
  if (!raw) {
    return { text: '待排期', abs: '', muted: true, tip: '调度已开启但还没有下一次运行时间，可尝试刷新' }
  }
  return {
    text: formatRunRelative(raw),
    abs: formatInPlatformTimezoneCompact(raw),
    muted: false,
    tip: cronScheduleTip(task.cron_expr),
  }
}

// ── 周期与指令意图是否一致（低成本启发式，纯前端，不阻断保存）───────────────
const DAILY_TIME_INTENT = /(每天|每日|每晚|每天早上|每日早上|每天早上|工作日)\s*(?:早上|上午|下午|晚上)?\s*(\d{1,2})\s*[点时:：]/
const cronFiresMoreThanOncePerDay = (expr: string) => {
  const parts = String(expr || '').trim().split(/\s+/)
  if (parts.length < 5) return false
  const wildish = (field: string) => /[*/,]|-/.test(field)
  // 分钟或小时不固定 → 一天内会跑多次
  return wildish(parts[0] || '') || wildish(parts[1] || '')
}

const taskScheduleMismatch = (task: AgentTask): string => {
  if (task.cron_valid === false) return ''
  const prompt = String(task.prompt || '')
  const intent = prompt.match(DAILY_TIME_INTENT)
  if (!intent) return ''
  if (!cronFiresMoreThanOncePerDay(task.cron_expr)) return ''
  return `周期与指令不符：指令要求「每天 ${intent[2]} 点」，实际是「${formatTaskSchedule(task.cron_expr)}」`
}

// ── 排序 ────────────────────────────────────────────────────────────
type TaskSortKey = 'priority' | 'name' | 'next' | 'rate' | 'last'
const sortKey = ref<TaskSortKey>('priority')
const sortDir = ref<1 | -1>(1)

const RECENT_RUN_SLOTS = 7
/**
 * 「最近 7 次」的槽位：后端按时间正序下发，这里换成等宽色块。
 * 不足 7 次时在左侧补空槽——左边是更早的时间，留白比伪造绿色诚实。
 */
const recentRunSlots = (task: AgentTask) => {
  const runs = Array.isArray(task.recent_runs) ? task.recent_runs.slice(-RECENT_RUN_SLOTS) : []
  const slots = runs.map((run, index) => {
    const value = String(run?.status || '').toLowerCase()
    const succeeded = value === 'success'
    const failed = value === 'failed' || value === 'error' || value === 'timeout'
    return {
      key: `${task.id}-run-${index}`,
      cls: succeeded ? 'bg-emerald-400' : failed ? 'bg-red-500' : 'bg-gray-300',
      tip: `${succeeded ? '成功' : failed ? '失败' : value || '未知'}${run?.at ? ' · ' + formatRunRelative(run.at) : ''}`,
      empty: false,
    }
  })
  const padding = RECENT_RUN_SLOTS - slots.length
  for (let i = 0; i < padding; i += 1) {
    slots.unshift({ key: `${task.id}-empty-${i}`, cls: 'bg-gray-100', tip: '暂无更早的执行记录', empty: true })
  }
  return slots
}

const sortRank = (task: AgentTask) => taskStatusMeta(task).rank
const sortNextValue = (task: AgentTask) => {
  if (task.status !== 1) return Number.MAX_SAFE_INTEGER
  const time = Date.parse(String(task.next_run_at || ''))
  return Number.isNaN(time) ? Number.MAX_SAFE_INTEGER : time
}
const sortLastValue = (task: AgentTask) => {
  const time = Date.parse(String(task.last_attempt_at || task.last_run_at || ''))
  return Number.isNaN(time) ? 0 : time
}

/**
 * 默认「需处理优先」：异常 → 需核对 → 活跃 → 已暂停，组内按下次运行时间升序。
 * 任务数上了十几个以后，这一条比任何视觉优化都管用。
 */
const sortTaskList = (list: AgentTask[]) => {
  const dir = sortDir.value
  const key = sortKey.value
  const sorted = [...list]
  sorted.sort((a, b) => {
    if (key === 'priority') {
      const rankDiff = sortRank(a) - sortRank(b)
      if (rankDiff !== 0) return rankDiff
      return sortNextValue(a) - sortNextValue(b)
    }
    if (key === 'name') return a.name.localeCompare(b.name, 'zh') * dir
    if (key === 'next') return (sortNextValue(a) - sortNextValue(b)) * dir
    if (key === 'last') return (sortLastValue(a) - sortLastValue(b)) * dir
    const rateA = taskRateNum(a)
    const rateB = taskRateNum(b)
    return ((rateA ?? -1) - (rateB ?? -1)) * dir
  })
  return sorted
}

const toggleSort = (key: TaskSortKey) => {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 1 ? -1 : 1
    return
  }
  sortKey.value = key
  sortDir.value = 1
}

const sortIndicator = (key: TaskSortKey) => {
  if (sortKey.value !== key) return '↕'
  return sortDir.value === 1 ? '↑' : '↓'
}

// ── 批量启停（串行调用现有单条接口，逐条汇总，不新增后端接口）──────────────
const selectedTaskIds = ref<Set<number>>(new Set())
const bulkRunning = ref(false)
const selectableTasks = computed(() => filteredTasks.value.filter(task => canManageTask(task)))
const selectedTasks = computed(() => scopedTasks.value.filter(task => selectedTaskIds.value.has(task.id)))
const allSelected = computed(() =>
  selectableTasks.value.length > 0 && selectableTasks.value.every(task => selectedTaskIds.value.has(task.id)),
)

const toggleTaskSelection = (id: number) => {
  const next = new Set(selectedTaskIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedTaskIds.value = next
}

const toggleSelectAll = () => {
  if (allSelected.value) {
    selectedTaskIds.value = new Set()
    return
  }
  selectedTaskIds.value = new Set(selectableTasks.value.map(task => task.id))
}

const clearSelection = () => {
  selectedTaskIds.value = new Set()
}

const resetTaskFilters = () => {
  searchQuery.value = ''
  statusFilter.value = 'all'
  taskTypeFilter.value = 'all'
  clearSelection()
}

const bulkToggleStatus = async (target: 0 | 1) => {
  const targets = selectedTasks.value.filter(task => {
    if (task.task_type === 'metadata_inspection') return false
    if (target === 1) return task.status !== 1
    return task.status === 1
  })
  if (!targets.length) {
    showToast(target === 1 ? '所选任务都已在运行中' : '所选任务都已暂停', 'info')
    return
  }
  bulkRunning.value = true
  let ok = 0
  const failed: string[] = []
  // 串行执行：并发打同一批接口容易触发后端限流，且失败时说不清是哪条没成功
  for (const task of targets) {
    try {
      if (task.task_type === 'saved_report' && task.subscription_id) {
        await taskApi.updateReportSubscriptionStatus(task.subscription_id, target === 1)
      } else {
        await taskApi.update(task.id, { status: target })
      }
      ok += 1
    } catch {
      failed.push(task.name)
    }
  }
  bulkRunning.value = false
  clearSelection()
  await fetchTasks(true)
  const verb = target === 1 ? '恢复' : '暂停'
  if (!failed.length) {
    showToast(`已${verb} ${ok} 个任务`)
    return
  }
  showToast(`已${verb} ${ok} 个，失败 ${failed.length} 个：${failed.slice(0, 2).join('、')}${failed.length > 2 ? '…' : ''}`, 'warning')
}

// ── 行操作菜单（⋯）：把编辑 / 历史 / 删除从一排同级图标里收进来 ──────────────
const taskMenuId = ref<number | null>(null)
/** 列表末尾几行的菜单要向上弹：表格容器是 overflow-x-auto，向下展开会被容器底边裁掉 */
const taskMenuOpensUp = ref(false)
const toggleTaskMenu = (id: number, opensUp = false) => {
  if (taskMenuId.value === id) {
    taskMenuId.value = null
    return
  }
  taskMenuId.value = id
  taskMenuOpensUp.value = opensUp
}
/** 末尾 3 行以内就把菜单向上展开（一份菜单约两行高，向上必然有空间） */
const isNearListEnd = (task: AgentTask) => {
  const list = filteredTasks.value
  const index = list.findIndex((item) => item.id === task.id)
  return index >= 0 && index >= list.length - 3
}

const closeTaskMenu = () => {
  taskMenuId.value = null
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
  <div ref="rootRef" class="space-y-5">
    <!-- Header：标题一行；窄屏搜索通栏，状态+刷新并排，新建通栏 -->
    <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div class="flex items-center space-x-3">
        <h1
          class="font-bold text-gray-900"
          :class="personalOnly ? 'text-xl' : 'text-xl sm:text-2xl'"
        >
          {{ personalOnly ? '我的任务' : '任务调度台' }}
        </h1>
        <button
          v-if="!personalOnly"
          type="button"
          class="hidden sm:flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-300 cursor-pointer"
          title="任务调度设计规范与全流程指引"
          @click="showSpecsModal = true"
        >
          <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093M12 17h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </button>

        <button
          v-if="!showTaskFlowGuide && !personalOnly"
          type="button"
          class="hidden sm:inline shrink-0 whitespace-nowrap text-xs font-medium text-gray-500 underline-offset-2 transition-colors hover:text-primary hover:underline cursor-pointer"
          title="重新展开任务调度全流程指引"
          @click="restoreTaskFlowGuide"
        >
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
            class="task-filter-select min-w-0 flex-1 rounded-lg border border-gray-300 bg-white text-sm shadow-sm outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20 sm:w-auto sm:flex-none"
            title="按状态筛选"
          >
            <option value="all">状态：全部</option>
            <option value="running">状态：运行中</option>
            <option value="stopped">状态：已暂停</option>
            <option value="attn">状态：需处理</option>
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

    <!-- 概览：健康构成占比条 + 可点击计数；可折叠成一行，选择记在本地 -->
    <div
      v-if="mainViewTab === 'tasks' && !loading && statusSummary.total > 0"
      class="rounded-xl border border-gray-200 bg-white px-4 shadow-sm transition-all"
      :class="overviewCollapsed ? 'py-2' : 'py-2.5'"
    >
      <!-- 收起态：一行说完，计数照样能点，不拿「省空间」换掉筛选能力 -->
      <div v-if="overviewCollapsed" class="flex flex-wrap items-center gap-x-3 gap-y-1">
        <p class="text-sm font-bold text-gray-900">
          <template v-if="statusSummary.compact">共 {{ statusSummary.total }} 个任务</template>
          <template v-else>
            {{ statusSummary.onPct }}% 在运行
            <span class="ml-1.5 text-[11px] font-semibold text-gray-400">共 {{ statusSummary.total }} 个</span>
          </template>
        </p>
        <div class="flex flex-wrap items-center gap-1">
          <button
            v-for="chip in statusChips"
            :key="'c-' + chip.key"
            type="button"
            class="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs font-semibold transition-colors"
            :class="[
              statusFilter === chip.key
                ? (chip.clickable ? 'cursor-pointer bg-gray-900 text-white' : 'cursor-default bg-gray-900 text-white')
                : (chip.clickable
                    ? (chip.key === 'attn' ? 'cursor-pointer text-red-600 hover:bg-red-50' : 'cursor-pointer text-gray-500 hover:bg-gray-100 hover:text-gray-800')
                    : 'cursor-default text-gray-400'),
            ]"
            :disabled="!chip.clickable"
            :title="chip.title"
            @click="statusFilter = statusFilter === chip.key ? 'all' : chip.key"
          >
            <span class="h-1.5 w-1.5 rounded-sm" :class="statusFilter === chip.key ? 'bg-white' : chip.dot"></span>
            {{ chip.label }}
            <b
              v-if="chip.count > 0 || chip.clickable"
              class="font-bold tabular-nums"
              :class="statusFilter === chip.key ? 'text-white' : (chip.key === 'attn' ? 'text-red-600' : 'text-gray-900')"
            >{{ chip.count }}</b>
          </button>
        </div>
        <div v-if="statusSummary.upcoming" class="ml-auto flex min-w-0 items-baseline gap-1.5 text-xs">
          <span class="shrink-0 font-bold text-gray-400">下一次</span>
          <b class="shrink-0 font-bold tabular-nums text-primary">{{ nextRunCell(statusSummary.upcoming).text }}</b>
          <span class="truncate text-gray-400">{{ statusSummary.upcoming.name }}</span>
        </div>
        <button
          type="button"
          class="shrink-0 cursor-pointer rounded-md px-1.5 py-0.5 text-xs font-semibold text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
          title="展开：看健康构成占比条"
          @click="toggleOverview"
        >展开 ⌄</button>
      </div>

      <!-- 展开态 -->
      <template v-else>
        <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <p class="text-sm font-bold text-gray-900">
            <template v-if="statusSummary.compact">共 {{ statusSummary.total }} 个任务</template>
            <template v-else>{{ statusSummary.onPct }}% 在运行</template>
            <span v-if="!statusSummary.compact" class="ml-1.5 text-[11px] font-semibold text-gray-400">
              共 {{ statusSummary.total }} 个任务
            </span>
          </p>
          <div v-if="statusSummary.upcoming" class="ml-auto flex min-w-0 items-baseline gap-1.5 text-xs">
            <span class="shrink-0 font-bold text-gray-400">下一次</span>
            <b class="shrink-0 font-bold tabular-nums text-primary">{{ nextRunCell(statusSummary.upcoming).text }}</b>
            <span class="truncate text-gray-400">{{ statusSummary.upcoming.name }}</span>
          </div>
        </div>

        <!-- 占比条：异常一出现就是一道红色缺口，比数字更早被眼睛抓到 -->
        <div
          v-if="!statusSummary.compact"
          class="mt-2 flex h-2 gap-[2px] overflow-hidden rounded-full bg-gray-100"
        >
          <span
            v-if="statusSummary.on"
            class="h-full bg-emerald-500"
            :style="{ width: statusSummary.onPct + '%' }"
            :title="`运行中 ${statusSummary.on} 个 · ${statusSummary.onPct}%`"
          ></span>
          <span
            v-if="statusSummary.off"
            class="h-full bg-gray-300"
            :style="{ width: statusSummary.offPct + '%' }"
            :title="`已暂停 ${statusSummary.off} 个 · ${statusSummary.offPct}%`"
          ></span>
          <span
            v-if="statusSummary.attn"
            class="h-full bg-red-500"
            :style="{ width: statusSummary.attnPct + '%' }"
            :title="`需处理 ${statusSummary.attn} 个 · ${statusSummary.attnPct}%`"
          ></span>
        </div>

        <!-- 图例同时是筛选入口；右侧空白用收起按钮填上 -->
        <div class="mt-1.5 flex flex-wrap items-center gap-1">
          <button
            v-for="chip in statusChips"
            :key="'e-' + chip.key"
            type="button"
            class="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs font-semibold transition-colors"
            :class="[
              statusFilter === chip.key
                ? 'bg-gray-900 text-white'
                : (chip.clickable
                    ? (chip.key === 'attn' ? 'cursor-pointer text-red-600 hover:bg-red-50' : 'cursor-pointer text-gray-500 hover:bg-gray-100 hover:text-gray-800')
                    : 'cursor-default text-gray-400'),
            ]"
            :disabled="!chip.clickable"
            :title="chip.title"
            @click="statusFilter = statusFilter === chip.key ? 'all' : chip.key"
          >
            <span class="h-1.5 w-1.5 rounded-sm" :class="statusFilter === chip.key ? 'bg-white' : chip.dot"></span>
            {{ chip.label }}
            <b
              v-if="chip.count > 0 || chip.clickable"
              class="font-bold tabular-nums"
              :class="statusFilter === chip.key ? 'text-white' : (chip.key === 'attn' ? 'text-red-600' : 'text-gray-900')"
            >{{ chip.count }}</b>
          </button>
          <button
            type="button"
            class="ml-auto shrink-0 cursor-pointer rounded-md px-1.5 py-0.5 text-xs font-semibold text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
            title="收起成一行"
            @click="toggleOverview"
          >收起 ⌃</button>
        </div>
      </template>
    </div>

    <!-- 一级 tab：类型筛选 + 右侧「执行记录」入口（不再有第二层 pill 筛选） -->
    <div class="border-b border-gray-200 -mt-1">
      <div class="flex items-center gap-1 overflow-x-auto -mb-px" style="-webkit-overflow-scrolling: touch;">
        <button
          v-for="tab in taskTypeTabs"
          :key="'main-' + tab.value"
          type="button"
          class="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors sm:px-4"
          :class="mainViewTab === 'tasks' && taskTypeFilter === tab.value
            ? 'border-primary text-primary'
            : 'border-transparent text-gray-500 hover:text-gray-700'"
          @click="taskTypeFilter = tab.value; mainViewTab = 'tasks'"
        >
          <span>{{ tab.label }}</span>
          <span
            class="min-w-5 rounded-full px-1.5 py-0.5 text-center text-[10px] font-semibold"
            :class="mainViewTab === 'tasks' && taskTypeFilter === tab.value ? 'bg-primary/10 text-primary-dark' : 'bg-gray-100 text-gray-500'"
          >
            {{ taskTypeCounts[tab.value] }}
          </span>
        </button>
        <span class="flex-1"></span>
        <button
          v-if="showHistoryTab"
          type="button"
          class="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-2.5 text-sm font-medium transition-colors sm:px-4"
          :class="mainViewTab === 'history'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-500 hover:text-primary-dark'"
          title="查看全部任务的执行记录"
          @click="mainViewTab = 'history'"
        >执行记录 →</button>
      </div>
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
          class="task-filter-select shrink-0 rounded-lg border border-gray-300 bg-white text-sm outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20"
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
          class="task-filter-select max-w-full rounded-lg border border-gray-300 bg-white text-sm outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20 lg:max-w-xs"
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
                  <span class="rounded-full px-2 py-0.5 text-[9px] font-bold" :class="logStatusMeta(item.status).class">
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
    <!-- 风险提醒：最严重的一条提到最上面，「只看需处理」一键收窄 -->
    <div
      v-if="!loading && attentionHeadline"
      class="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border px-3 py-2 text-xs leading-relaxed"
      :class="attentionHeadline.isError
        ? 'border-red-200 bg-red-50 text-red-700'
        : 'border-amber-200 bg-amber-50 text-amber-800'"
    >
      <span class="shrink-0">⚠</span>
      <span class="min-w-0 flex-1">
        <b class="font-bold">{{ attentionHeadline.task.name }}</b>
        {{ attentionHeadline.reason }}{{ attentionHeadline.retryHint ? ' ' + attentionHeadline.retryHint : '' }}
        <span v-if="attentionHeadline.count > 1" class="opacity-70">（另有 {{ attentionHeadline.count - 1 }} 个任务需处理）</span>
      </span>
      <button
        type="button"
        class="shrink-0 cursor-pointer rounded-md border border-black/10 px-2 py-0.5 font-bold transition-opacity hover:opacity-70"
        @click="statusFilter = statusFilter === 'attn' ? 'all' : 'attn'"
      >{{ statusFilter === 'attn' ? '显示全部' : '只看需处理' }}</button>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="py-20 text-center">
      <div class="animate-spin h-10 w-10 border-4 border-primary border-t-transparent rounded-full mx-auto mb-4"></div>
      <p class="text-gray-500">加载任务列表中...</p>
    </div>

    <!-- Empty State：区分「一个任务都没有」和「筛选没命中」，前者直接给创建入口 -->
    <div v-else-if="filteredTasks.length === 0" class="rounded-xl border border-dashed border-gray-200 bg-white px-6 py-16 text-center">
      <p class="text-sm font-semibold text-gray-700">
        {{ scopedTasks.length === 0 ? '还没有任务' : '没有匹配的任务' }}
      </p>
      <p class="mt-1 text-xs text-gray-400">
        {{ scopedTasks.length === 0
          ? '定时任务可以让智能体按周期自动跑：监控发版、巡检数据、推送日报。'
          : '当前搜索或筛选条件下没有任务，试试放宽条件。' }}
      </p>
      <button
        v-if="scopedTasks.length === 0 && canManage"
        type="button"
        class="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-primary-dark"
        @click="openCreateModal"
      >
        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
        </svg>
        新建第一个任务
      </button>
      <button
        v-else-if="scopedTasks.length > 0"
        type="button"
        class="mt-4 rounded-lg border border-gray-200 px-3.5 py-2 text-xs font-semibold text-gray-600 transition-colors hover:border-gray-300 hover:text-gray-900"
        @click="resetTaskFilters"
      >清空筛选</button>
    </div>

    <!-- Grid View：auto-fill 自适应列宽，窄容器（个人中心约 900px）自动降 2 列，不再把徽章和开关挤出卡片 -->
    <div
      v-else-if="currentViewMode === 'grid'"
      class="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3"
    >
      <article
        v-for="task in filteredTasks"
        :key="task.id"
        class="group relative flex flex-col gap-2 rounded-xl border bg-white p-3.5 transition-all hover:shadow-md"
        :class="taskStatusMeta(task).key === 'error'
          ? 'border-red-200 border-l-[3px] border-l-red-500'
          : (task.status === 1 ? 'border-gray-200' : 'border-gray-200 bg-gray-50/40')"
      >
        <!-- 标题行：状态点 + 标题（两行截断，占满剩余宽度）+ ⋯ 菜单 -->
        <div class="flex items-start gap-2">
          <span
            class="mt-1.5 h-2 w-2 shrink-0 rounded-full"
            :class="taskStatusMeta(task).dot"
            :title="taskStatusTip(task)"
          ></span>
          <h3
            class="line-clamp-2 min-w-0 flex-1 break-words text-sm font-bold leading-snug text-gray-900"
            :title="task.name"
          >{{ task.name }}</h3>
          <div class="relative shrink-0" data-task-menu>
            <button
              type="button"
              class="-mr-1 -mt-1 flex h-6 w-6 cursor-pointer items-center justify-center rounded-md text-base leading-none text-gray-300 transition-colors hover:bg-gray-100 hover:text-gray-700"
              title="更多操作"
              @click.stop="toggleTaskMenu(task.id)"
            >⋯</button>
            <div
              v-if="taskMenuId === task.id"
              class="absolute right-0 top-full z-20 mt-1 w-36 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-left shadow-lg"
            >
              <!-- 报表订阅专属操作：订阅走自己的设置弹窗，且不按任务归属拦（与重构前一致） -->
              <button
                v-if="task.task_type === 'saved_report'"
                type="button"
                class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                @click="closeTaskMenu(); openSavedReportSubscriptionSettings(task)"
              ><BellAlertIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />订阅设置</button>
              <button
                v-else-if="canManageTask(task) && task.task_type !== 'metadata_inspection'"
                type="button"
                class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                @click="closeTaskMenu(); openEditModal(task)"
              ><PencilSquareIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />编辑任务</button>
              <button
                type="button"
                class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                @click="closeTaskMenu(); openLogs(task)"
              ><ClockIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ task.task_type === 'saved_report' ? '运行历史' : '执行历史' }}</button>
              <button
                v-if="task.task_type === 'metadata_inspection' && userInfo?.role === 'admin'"
                type="button"
                class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                @click="closeTaskMenu(); showCronInspectionModal = true"
              ><WrenchScrewdriverIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />巡检配置</button>
              <button
                v-if="canManageTask(task) && task.task_type !== 'metadata_inspection'"
                type="button"
                class="mt-0.5 flex w-full cursor-pointer items-center gap-2 border-t border-gray-100 px-3 py-1.5 text-left text-xs text-red-600 hover:bg-red-50"
                @click="closeTaskMenu(); deleteTask(task)"
              ><TrashIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ task.task_type === 'saved_report' ? '删除订阅' : '删除任务' }}</button>
            </div>
          </div>
        </div>

        <!-- 元信息：执行对象 · 周期 · 一个合成状态（不再出现「健康 + 失败 2」并排） -->
        <div class="flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500">
          <span class="rounded bg-primary/5 px-1.5 py-0.5 font-bold text-primary">{{ task.agent_name }}</span>
          <HoverTip :text="cronScheduleTip(task.cron_expr)" :width="300">
            <span class="cursor-help border-b border-dashed border-gray-300 font-semibold text-gray-700">{{ formatTaskSchedule(task.cron_expr) }}</span>
          </HoverTip>
          <span
            class="rounded-full border px-2 py-0.5 text-[10px] font-bold"
            :class="taskStatusMeta(task).class"
            :title="taskStatusTip(task)"
          >{{ taskStatusMeta(task).label }}</span>
          <span
            v-if="task.task_type === 'saved_report'"
            class="rounded-full border border-emerald-100 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700"
          >报表订阅</span>
          <span
            v-else-if="task.task_type === 'metadata_inspection'"
            class="rounded-full border border-primary/20 bg-primary/5 px-2 py-0.5 text-[10px] font-bold text-primary-dark"
          >元数据巡检</span>
        </div>

        <p class="line-clamp-1 text-[11px] leading-relaxed text-gray-400" :title="task.prompt">"{{ task.prompt }}"</p>

        <p
          v-if="taskScheduleMismatch(task)"
          class="line-clamp-2 rounded-md border border-amber-100 bg-amber-50 px-2 py-1 text-[11px] leading-snug text-amber-700"
          :title="taskScheduleMismatch(task)"
        > {{ taskScheduleMismatch(task) }}</p>
        <p
          v-else-if="task.last_error"
          class="line-clamp-2 rounded-md border border-red-100 bg-red-50 px-2 py-1 text-[11px] leading-snug text-red-700"
          :title="task.last_error"
        >⚠ {{ task.consecutive_failures ? '连续失败 ' + task.consecutive_failures + ' 次：' : '' }}{{ task.last_error }}</p>

        <!-- 指标：卡片与列表同一套口径「运行 N 次 · 成功率 x%」，四宫格明细进 tooltip -->
        <div
          class="flex items-center gap-1.5 text-[11px] text-gray-500"
          :title="'触发 ' + metricValue(task.trigger_count) + ' · 成功 ' + metricValue(task.success_count || task.run_count) + ' · 失败 ' + metricValue(task.failure_count) + ' · 跳过 ' + metricValue(task.skipped_count)"
        >
          <span>运行 <b class="font-bold text-gray-900">{{ taskMetricText(task).runs }}</b> 次</span>
          <span class="text-gray-300">·</span>
          <span>
            成功率
            <b
              class="font-bold"
              :class="taskRateIsLow(task) ? 'text-red-600' : 'text-gray-900'"
            >{{ taskMetricText(task).rate }}</b>
          </span>
        </div>

        <!-- 时间：相对时间打头，绝对时间兜底；暂停任务写「已暂停」而不是「暂无计划」 -->
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-dashed border-gray-100 pt-2 text-[11px]">
          <div class="flex items-center gap-1">
            <span class="text-gray-400">上次</span>
            <span class="font-semibold text-gray-700">{{ formatDate(task.last_attempt_at || task.last_run_at) }}</span>
          </div>
          <div class="flex items-center gap-1">
            <span class="text-gray-400">下次</span>
            <span v-if="nextRunCell(task).muted" class="font-medium text-gray-400" :title="nextRunCell(task).tip">{{ nextRunCell(task).text }}</span>
            <span v-else class="font-bold text-primary" :title="nextRunCell(task).tip">
              {{ nextRunCell(task).text }}<span class="ml-1 font-medium text-gray-400">{{ nextRunCell(task).abs }}</span>
            </span>
          </div>
        </div>

        <!-- 操作：主操作文字按钮 + 启停一处，其余进 ⋯ 菜单 -->
        <div class="mt-auto flex items-center gap-1.5 border-t border-gray-100 pt-2">
          <button
            v-if="canManageTask(task)"
            type="button"
            class="rounded-md bg-primary px-2.5 py-1 text-[11px] font-bold text-white transition-colors hover:bg-primary-dark disabled:opacity-50"
            :disabled="runningTaskIds.has(task.id)"
            @click="runTaskNow(task)"
          >{{ runningTaskIds.has(task.id) ? '正在触发' : '立即执行' }}</button>
          <button
            type="button"
            class="rounded-md border border-gray-200 px-2.5 py-1 text-[11px] font-semibold text-gray-600 transition-colors hover:border-primary/40 hover:text-primary"
            @click="openLogs(task)"
          >历史</button>
          <span class="flex-1"></span>
          <button
            v-if="canManageTask(task)"
            type="button"
            class="rounded-md px-2 py-1 text-[11px] font-semibold text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-800"
            :title="task.status === 1 ? '暂停后不再自动执行' : '恢复调度'"
            @click="toggleStatus(task)"
          >{{ task.status === 1 ? '暂停' : '恢复' }}</button>
        </div>
      </article>
    </div>

    <!-- List View：任务多（10+）时的主力视图，默认「需处理优先」排序，表头可排序 -->
    <!-- 不用 overflow-x-auto：<980px 已隐藏次要列、<760px 直接降级卡片，表格在能走到这里的容器宽度下不会溢出；
         而 overflow 会建裁剪上下文，把行内 ⋯ 菜单的弹层裁在表格底边。 -->
    <div v-else class="rounded-xl border border-gray-200 bg-white shadow-sm">
        <table class="w-full min-w-[760px] table-fixed border-collapse text-left">
          <thead>
            <tr class="border-b border-gray-200 bg-gray-50/60">
              <th class="w-10 px-2 py-2.5">
                <input
                  type="checkbox"
                  class="h-3.5 w-3.5 cursor-pointer accent-primary"
                  title="全选当前筛选下的可管理任务"
                  :checked="allSelected"
                  @change="toggleSelectAll"
                />
              </th>
              <th class="px-3 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-400">
                <button type="button" class="inline-flex cursor-pointer items-center gap-1 hover:text-gray-700" @click="toggleSort('name')">
                  任务 <span class="text-[9px] text-gray-400">{{ sortIndicator('name') }}</span>
                </button>
              </th>
              <th
                v-if="showAgentColumn"
                class="w-[120px] px-3 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-400"
              >执行对象</th>
              <th class="w-[176px] px-3 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-400">
                <button type="button" class="inline-flex cursor-pointer items-center gap-1 hover:text-gray-700" @click="toggleSort('next')">
                  预计下次运行 <span class="text-[9px] text-gray-400">{{ sortIndicator('next') }}</span>
                </button>
              </th>
              <th
                v-if="showRateColumn"
                class="w-[84px] px-3 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-400"
              >
                <button type="button" class="inline-flex cursor-pointer items-center gap-1 hover:text-gray-700" @click="toggleSort('rate')">
                  成功率 <span class="text-[9px] text-gray-400">{{ sortIndicator('rate') }}</span>
                </button>
              </th>
              <th class="w-[96px] px-3 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-400">最近 7 次</th>
              <th class="w-[92px] px-3 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-400">
                <button type="button" class="inline-flex cursor-pointer items-center gap-1 hover:text-gray-700" @click="toggleSort('priority')">
                  状态 <span class="text-[9px] text-gray-400">{{ sortIndicator('priority') }}</span>
                </button>
              </th>
              <th class="w-[132px] px-3 py-2.5 text-right text-[11px] font-bold uppercase tracking-wider text-gray-400">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr
              v-for="task in filteredTasks"
              :key="task.id"
              class="group transition-colors hover:bg-primary/5"
              :class="selectedTaskIds.has(task.id) ? 'bg-primary/5' : ''"
            >
              <td class="px-2 py-3 align-middle">
                <input
                  v-if="canManageTask(task)"
                  type="checkbox"
                  class="h-3.5 w-3.5 cursor-pointer accent-primary"
                  :checked="selectedTaskIds.has(task.id)"
                  @change="toggleTaskSelection(task.id)"
                />
              </td>
              <td class="px-3 py-3">
                <div class="flex items-start gap-2">
                  <span class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" :class="taskStatusMeta(task).dot" :title="taskStatusTip(task)"></span>
                  <div class="min-w-0">
                    <div class="flex items-center gap-1.5">
                      <p class="truncate text-sm font-bold leading-5 text-gray-900 group-hover:text-primary" :title="task.name">{{ task.name }}</p>
                      <span
                        v-if="task.task_type === 'saved_report'"
                        class="shrink-0 rounded-full border border-emerald-100 bg-emerald-50 px-1.5 py-0.5 text-[9px] font-bold text-emerald-700"
                      >报表</span>
                      <span
                        v-else-if="task.task_type === 'metadata_inspection'"
                        class="shrink-0 rounded-full border border-primary/20 bg-primary/5 px-1.5 py-0.5 text-[9px] font-bold text-primary-dark"
                      >巡检</span>
                    </div>
                    <p class="mt-0.5 max-w-full truncate text-[11px] text-gray-400">
                      {{ formatTaskSchedule(task.cron_expr) }} · 运行 {{ taskMetricText(task).runs }} 次 · 上次 {{ formatDate(task.last_attempt_at || task.last_run_at) }}
                    </p>
                  </div>
                </div>
              </td>
              <td v-if="showAgentColumn" class="truncate px-3 py-3 text-xs text-gray-600" :title="task.agent_name">{{ task.agent_name }}</td>
              <td class="px-3 py-3">
                <span v-if="nextRunCell(task).muted" class="text-xs font-medium text-gray-400" :title="nextRunCell(task).tip">{{ nextRunCell(task).text }}</span>
                <span v-else class="text-xs" :title="nextRunCell(task).tip">
                  <b class="font-bold text-primary">{{ nextRunCell(task).text }}</b>
                  <span class="ml-1 text-gray-400">{{ nextRunCell(task).abs }}</span>
                </span>
              </td>
              <td v-if="showRateColumn" class="px-3 py-3">
                <span
                  class="text-xs font-bold tabular-nums"
                  :class="taskRateIsLow(task) ? 'text-red-600' : 'text-gray-800'"
                >{{ taskMetricText(task).rate }}</span>
              </td>
              <td class="px-3 py-3">
                <div class="flex h-4 items-end gap-[3px]">
                  <span
                    v-for="slot in recentRunSlots(task)"
                    :key="slot.key"
                    class="w-[6px] rounded-sm"
                    :class="[slot.cls, slot.empty ? 'h-2' : 'h-3.5']"
                    :title="slot.tip"
                  ></span>
                </div>
              </td>
              <td class="px-3 py-3">
                <span class="rounded-full border px-2 py-0.5 text-[10px] font-bold" :class="taskStatusMeta(task).class" :title="taskStatusTip(task)">
                  {{ taskStatusMeta(task).label }}
                </span>
              </td>
              <td class="px-3 py-3">
                <div class="flex items-center justify-end gap-1">
                  <button
                    v-if="canManageTask(task)"
                    type="button"
                    class="rounded-md bg-primary px-2.5 py-1 text-[11px] font-bold text-white transition-colors hover:bg-primary-dark disabled:opacity-50"
                    :disabled="runningTaskIds.has(task.id)"
                    title="立即执行一次"
                    @click="runTaskNow(task)"
                  >{{ runningTaskIds.has(task.id) ? '触发中' : '执行' }}</button>
                  <div class="relative" data-task-menu>
                    <button
                      type="button"
                      class="flex h-6 w-6 cursor-pointer items-center justify-center rounded-md border border-gray-200 text-base leading-none text-gray-400 transition-colors hover:border-gray-300 hover:text-gray-700"
                      title="更多操作"
                      @click.stop="toggleTaskMenu(task.id, isNearListEnd(task))"
                    >⋯</button>
                    <div
                      v-if="taskMenuId === task.id"
                      class="absolute right-0 z-20 w-36 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-left shadow-lg"
                      :class="taskMenuOpensUp ? 'bottom-full mb-1' : 'top-full mt-1'"
                    >
                        <!-- 报表订阅专属操作：订阅走自己的设置弹窗，且不按任务归属拦（与重构前一致） -->
                        <button
                          v-if="task.task_type === 'saved_report'"
                          type="button"
                          class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                          @click="closeTaskMenu(); openSavedReportSubscriptionSettings(task)"
                        ><BellAlertIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />订阅设置</button>
                        <button
                          v-else-if="canManageTask(task) && task.task_type !== 'metadata_inspection'"
                          type="button"
                          class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                          @click="closeTaskMenu(); openEditModal(task)"
                        ><PencilSquareIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />编辑任务</button>
                        <button
                          type="button"
                          class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                          @click="closeTaskMenu(); openLogs(task)"
                        ><ClockIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ task.task_type === 'saved_report' ? '运行历史' : '执行历史' }}</button>
                      <button
                        v-if="task.task_type === 'metadata_inspection' && userInfo?.role === 'admin'"
                        type="button"
                        class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                        @click="closeTaskMenu(); showCronInspectionModal = true"
                      ><WrenchScrewdriverIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />巡检配置</button>
                      <button
                        v-if="canManageTask(task)"
                        type="button"
                        class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                        @click="closeTaskMenu(); toggleStatus(task)"
                      ><PauseCircleIcon v-if="task.status === 1" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" /><PlayCircleIcon v-else class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ task.status === 1 ? '暂停调度' : '恢复调度' }}</button>
                      <button
                        v-if="canManageTask(task) && task.task_type !== 'metadata_inspection'"
                        type="button"
                        class="mt-0.5 flex w-full cursor-pointer items-center gap-2 border-t border-gray-100 px-3 py-1.5 text-left text-xs text-red-600 hover:bg-red-50"
                        @click="closeTaskMenu(); deleteTask(task)"
                      ><TrashIcon class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ task.task_type === 'saved_report' ? '删除订阅' : '删除任务' }}</button>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
    </div>

    <!-- 批量操作条：勾选后浮出，串行调用现有单条接口并逐条汇总结果 -->
    <div
      v-if="selectedTaskIds.size > 0"
      class="fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 items-center gap-3 rounded-xl bg-gray-900 px-4 py-2.5 text-xs text-white shadow-2xl"
    >
      <span>已选 <b class="font-bold">{{ selectedTaskIds.size }}</b> 项</span>
      <button
        type="button"
        class="cursor-pointer rounded-md border border-white/25 px-2.5 py-1 font-semibold transition-colors hover:bg-white/10 disabled:opacity-50"
        :disabled="bulkRunning"
        @click="bulkToggleStatus(0)"
      >批量暂停</button>
      <button
        type="button"
        class="cursor-pointer rounded-md border border-white/25 px-2.5 py-1 font-semibold transition-colors hover:bg-white/10 disabled:opacity-50"
        :disabled="bulkRunning"
        @click="bulkToggleStatus(1)"
      >批量恢复</button>
      <button
        type="button"
        class="cursor-pointer rounded-md px-2 py-1 font-semibold text-gray-300 transition-colors hover:text-white"
        @click="clearSelection"
      >取消</button>
    </div>
    </template>

    <!-- 任务调度设计规范与全流程指引 Modal -->
    <Teleport to="body">
      <div v-if="showSpecsModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showSpecsModal = false">
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
                <div class="bg-gradient-to-r from-orange-50 to-amber-50 border-l-4 border-orange-600 p-4 rounded-r-xl shadow-sm">
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
                            <span class="w-6 h-6 rounded-full bg-primary text-white flex items-center justify-center text-xs font-bold">2</span>
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
                            <span class="w-6 h-6 rounded-full bg-primary text-white flex items-center justify-center text-xs font-bold">3</span>
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
                            <span class="w-6 h-6 rounded-full bg-primary text-white flex items-center justify-center text-xs font-bold">4</span>
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
                <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-600 leading-relaxed">
                   <h4 class="font-bold text-gray-900 text-base">自动化上下文注入与处理规范</h4>
                   <div class="space-y-3">
                      <p class="text-xs text-gray-600">
                         每次调度执行时，系统在 <code>user_info</code> 中自动注入以下上下文元数据，智能体可通过这些标识识别定时自动化场景：
                      </p>
                      <div class="bg-gray-50 rounded-xl p-4 border border-gray-200 font-mono text-[11px] space-y-1.5">
                         <div class="flex"><span class="text-primary w-36 font-semibold">is_scheduled_task:</span><span class="text-emerald-600 font-bold">true</span><span class="text-gray-400 ml-auto">// 标识当前为定时自动化任务</span></div>
                         <div class="flex"><span class="text-primary w-36 font-semibold">task_name:</span><span class="text-emerald-600 font-bold">"数据巡检与周报"</span><span class="text-gray-400 ml-auto">// 当前执行的任务名称</span></div>
                         <div class="flex"><span class="text-primary w-36 font-semibold">user_id / role:</span><span class="text-emerald-600 font-bold">"admin" / "1"</span><span class="text-gray-400 ml-auto">// 模拟创建者的身份与权限</span></div>
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
                <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-600 leading-relaxed">
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
    </Teleport>

    <!-- Edit Modal -->
    <Modal 
      v-if="showEditModal" 
      :title="editingTask.id ? '编辑定时任务' : '新建定时任务'" 
      @close="closeEditModal" 
      size="max-w-[1080px]"
      class="transition-all"
      :class="isMobile ? 'inset-0 !m-0 !max-w-none !rounded-none h-full' : ''"
    >
      <div class="h-full flex flex-col">
        <!-- 方案 C：新建先选模板。模板只预填，每一栏选完都能改 -->
        <div v-if="showTemplatePicker" class="min-h-0 flex-1 space-y-3 overflow-y-auto px-1 p-1">
          <div class="rounded-xl border border-primary/10 bg-primary/5 px-3 py-2.5">
            <p class="text-xs font-bold text-primary-dark">先选一个起点，再改对象和时间</p>
            <p class="mt-1 text-[11px] leading-relaxed text-primary-dark">
              选模板会预填任务名、执行时间、要做什么、输出要求和通知渠道；<b>【】里的内容需要你换成自己的对象</b>，其余都能继续改。
            </p>
          </div>
          <div class="grid gap-2.5 sm:grid-cols-3">
            <button
              v-for="tpl in taskTemplates"
              :key="tpl.id"
              type="button"
              class="flex flex-col items-start gap-1 rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-left transition hover:-translate-y-px hover:border-primary/20 hover:bg-primary/5"
              @click="applyTaskTemplate(tpl)"
            >
              <component :is="tpl.icon" class="h-5 w-5" :class="tpl.tone" aria-hidden="true" />
              <span class="text-xs font-bold text-gray-800">{{ tpl.title }}</span>
              <span class="text-[10px] leading-relaxed text-gray-500">{{ tpl.desc }}</span>
              <span class="mt-1 rounded border border-primary/10 bg-primary/5 px-1.5 py-0.5 text-[9px] font-semibold text-primary-dark">{{ tpl.summary }}</span>
            </button>
            <button
              type="button"
              class="flex flex-col items-start gap-1 rounded-xl border border-dashed border-gray-300 bg-white px-3 py-2.5 text-left transition hover:border-gray-400 hover:bg-gray-50"
              @click="startBlankTask"
            >
              <PencilSquareIcon class="h-5 w-5 text-gray-400" aria-hidden="true" />
              <span class="text-xs font-bold text-gray-800">从空白开始</span>
              <span class="text-[10px] leading-relaxed text-gray-500">不预填任何内容，自己写全部字段。</span>
              <span class="mt-1 rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[9px] font-semibold text-gray-500">完全自定义</span>
            </button>
          </div>
        </div>

        <div
          v-else
          class="grid min-h-0 flex-1"
          :class="isMobile ? 'grid-cols-1' : 'grid-cols-[minmax(0,1fr)_290px]'"
        >
        <div class="min-h-0 overflow-y-auto space-y-5 px-1 p-1">
          <!-- 复核面：这条任务意图原本散在 6 个字段里，保存前没有任何地方能一眼确认。
               宽屏时挪到右侧粘性栏（见本区块下方 aside），窄屏才留在表单顶部 -->
          <div v-if="isMobile" class="rounded-xl border border-gray-100 bg-gray-50/70 px-3 py-2.5">
            <div class="mb-1 flex items-center gap-1.5">
              <span class="text-[10px] font-bold text-gray-400"> 这个任务会这样跑</span>
              <span v-if="taskSummaryNext" class="ml-auto rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 font-mono text-[10px] font-bold text-emerald-700">
                下次 {{ taskSummaryNext }}
              </span>
            </div>
            <p class="text-xs leading-relaxed text-gray-600">
              <span class="font-bold text-gray-800">{{ taskSummaryText.whenValid ? taskSummaryText.when : '（还没选执行时间）' }}</span>
              <template v-if="taskSummaryText.agent">，让 <span class="font-bold text-amber-700">{{ taskSummaryText.agent }}</span> 执行</template>
              <template v-if="taskSummaryText.what">「<span class="text-gray-800">{{ taskSummaryText.what }}</span>」</template>
              <span v-else class="text-gray-400">（还没写「要做什么」）</span>
              <template v-if="taskSummaryText.constraints.length">
                ，并按 <span class="font-bold text-gray-800">{{ taskSummaryText.constraints.length }} 条交付要求</span>输出
              </template>
              <template v-if="taskSummaryText.channels.length">，结果发到 <span class="font-bold text-gray-800">{{ taskSummaryText.channels.join(' + ') }}</span></template>
              <span class="text-gray-500">。</span>
            </p>
          </div>

          <TaskFormSection
            v-model="showWhatSection"
            :section-id="TASK_SECTION_IDS.what"
            :icon="PencilSquareIcon"
            title="做什么"
            hint="一句话说清目标与交付"
            :summary="taskSectionSummary.what"
            :has-error="taskSectionIssues.issues.what"
          >
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
                @click.stop="toggleAgentDropdown"
              >
                <div class="flex min-w-0 flex-1 items-center gap-2">
                  <div class="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-gray-100 bg-gray-50 text-sm">
                    <img
                      v-if="isAgentAvatarUrl(selectedEditingAgent?.avatar_url)"
                      :src="selectedEditingAgent?.avatar_url"
                      class="h-full w-full object-cover"
                    />
                    <span v-else-if="selectedEditingAgent?.avatar_url" class="text-sm">{{ selectedEditingAgent?.avatar_url }}</span>
                    <span v-else class="text-sm">{{ selectedEditingAgent && isMainAgent(selectedEditingAgent) ? '🌟' : (selectedEditingAgent?.is_system ? '🔒' : '👤') }}</span>
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5">
                      <p class="truncate text-sm font-bold text-gray-800">
                        {{ selectedEditingAgent?.display_name || '选择智能体' }}
                      </p>
                      <span
                        v-if="selectedEditingAgent && isMainAgent(selectedEditingAgent)"
                        class="shrink-0 rounded border border-amber-200 bg-amber-50 px-1 text-[8px] font-semibold text-amber-600"
                      >MAIN</span>
                      <span
                        v-else-if="selectedEditingAgent?.is_system"
                        class="shrink-0 rounded border border-primary/20 bg-primary/5 px-1 text-[8px] font-mono text-primary"
                      >SYSTEM</span>
                      <span
                        v-else-if="selectedEditingAgent"
                        class="shrink-0 rounded border border-gray-200 bg-gray-50 px-1 text-[8px] font-mono text-gray-500"
                      >CUSTOM</span>
                    </div>
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
                class="absolute left-0 right-0 z-50 mt-1 max-h-96 flex flex-col rounded-xl border border-gray-200 bg-white p-1.5 shadow-xl"
              >
                <!-- Tab 切换头 -->
                <div class="flex items-center gap-1 rounded-lg bg-gray-100/90 p-1 mb-1.5 shrink-0">
                  <button
                    type="button"
                    class="flex flex-1 items-center justify-center gap-1.5 rounded-md py-1 text-xs font-semibold transition-all"
                    :class="
                      agentTab === 'system'
                        ? 'bg-white text-primary shadow-sm'
                        : 'text-gray-500 hover:text-gray-700'
                    "
                    @click.stop="switchAgentTab('system')"
                  >
                    <svg class="h-3.5 w-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                    </svg>
                    <span>系统智能体</span>
                    <span
                      class="rounded px-1 text-[9px] font-normal"
                      :class="agentTab === 'system' ? 'bg-primary/5 text-primary' : 'bg-gray-200/70 text-gray-500'"
                    >{{ agentSearchQuery ? `${filteredSystemAgents.length}/${systemAgents.length}` : systemAgents.length }}</span>
                  </button>

                  <button
                    type="button"
                    class="flex flex-1 items-center justify-center gap-1.5 rounded-md py-1 text-xs font-semibold transition-all"
                    :class="
                      agentTab === 'custom'
                        ? 'bg-white text-emerald-600 shadow-sm'
                        : 'text-gray-500 hover:text-gray-700'
                    "
                    @click.stop="switchAgentTab('custom')"
                  >
                    <svg class="h-3.5 w-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                    </svg>
                    <span>自定义智能体</span>
                    <span
                      class="rounded px-1 text-[9px] font-normal"
                      :class="agentTab === 'custom' ? 'bg-emerald-50 text-emerald-600' : 'bg-gray-200/70 text-gray-500'"
                    >{{ agentSearchQuery ? `${filteredCustomAgents.length}/${customAgents.length}` : customAgents.length }}</span>
                  </button>
                </div>

                <!-- 搜索过滤输入框：仅当前分类智能体数 > 5 或已有搜索词时展示 -->
                <div v-if="showAgentSearchInput" class="px-0.5 pb-1.5 shrink-0">
                  <div class="relative flex items-center">
                    <svg
                      class="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-gray-400"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                      />
                    </svg>
                    <input
                      v-model="agentSearchQuery"
                      type="search"
                      placeholder="搜索智能体名称、标识或说明..."
                      class="w-full rounded-lg border border-gray-200 bg-gray-50/70 py-1.5 pl-8 pr-7 text-xs text-gray-700 placeholder-gray-400 outline-none transition-all focus:border-primary/50 focus:bg-white focus:ring-2 focus:ring-primary/20"
                      @click.stop
                    />
                    <button
                      v-if="agentSearchQuery"
                      type="button"
                      class="absolute right-2 flex h-4 w-4 items-center justify-center rounded-full text-gray-400 hover:bg-gray-200 hover:text-gray-600 transition-colors"
                      title="清空搜索"
                      @click.stop="agentSearchQuery = ''"
                    >
                      <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                </div>

                <!-- Tab 内容滚动区 -->
                <div class="flex-1 overflow-y-auto space-y-1 pr-0.5 min-h-[160px]">
                  <!-- 系统智能体 Tab 页面 -->
                  <template v-if="agentTab === 'system'">
                    <button
                      v-for="agent in filteredSystemAgents"
                      :key="agent.id"
                      type="button"
                      class="my-0.5 flex w-full cursor-pointer items-start gap-2.5 rounded-lg border p-2 text-left transition-all"
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
                        <span v-else class="text-sm">{{ isMainAgent(agent) ? '🌟' : '🔒' }}</span>
                      </div>
                      <div class="min-w-0 flex-1">
                        <div class="flex items-center justify-between gap-2">
                          <span
                            class="truncate text-xs font-bold text-gray-800"
                            :class="editingTask.agent_id === agent.id ? 'text-primary' : ''"
                          >{{ agent.display_name }}</span>
                          <div class="flex items-center gap-1 shrink-0">
                            <span
                              v-if="isMainAgent(agent)"
                              class="rounded border border-amber-200 bg-amber-50 px-1 text-[8px] font-semibold text-amber-600"
                            >MAIN</span>
                            <span
                              v-else
                              class="rounded border border-primary/20 bg-primary/5 px-1 text-[8px] font-mono text-primary"
                            >SYSTEM</span>
                          </div>
                        </div>
                        <div class="mt-0.5 truncate font-mono text-[9px] text-gray-400">{{ agent.name }}</div>
                        <div class="mt-1 line-clamp-2 break-words text-[10px] leading-relaxed text-gray-500" :title="agent.description">
                          {{ agent.description || '暂无备注说明信息' }}
                        </div>
                      </div>
                    </button>
                    <div v-if="!filteredSystemAgents.length" class="px-3 py-8 text-center text-xs text-gray-400">
                      <p v-if="agentSearchQuery">
                        未找到与 "<span class="text-gray-600 font-medium">{{ agentSearchQuery }}</span>" 匹配的系统智能体
                      </p>
                      <p v-else>暂无系统智能体</p>
                      <button
                        v-if="agentSearchQuery"
                        type="button"
                        class="mt-2 text-[11px] text-primary hover:underline"
                        @click.stop="agentSearchQuery = ''"
                      >
                        清空搜索条件
                      </button>
                    </div>
                  </template>

                  <!-- 自定义智能体 Tab 页面 -->
                  <template v-else-if="agentTab === 'custom'">
                    <button
                      v-for="agent in filteredCustomAgents"
                      :key="agent.id"
                      type="button"
                      class="my-0.5 flex w-full cursor-pointer items-start gap-2.5 rounded-lg border p-2 text-left transition-all"
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
                        <UserIcon v-else class="h-4 w-4 text-gray-400" aria-hidden="true" />
                      </div>
                      <div class="min-w-0 flex-1">
                        <div class="flex items-center justify-between gap-2">
                          <span
                            class="truncate text-xs font-bold text-gray-800"
                            :class="editingTask.agent_id === agent.id ? 'text-primary' : ''"
                          >{{ agent.display_name }}</span>
                          <span
                            class="shrink-0 rounded border border-gray-200 bg-gray-50 px-1 text-[8px] font-mono text-gray-500"
                          >CUSTOM</span>
                        </div>
                        <div class="mt-0.5 truncate font-mono text-[9px] text-gray-400">{{ agent.name }}</div>
                        <div class="mt-1 line-clamp-2 break-words text-[10px] leading-relaxed text-gray-500" :title="agent.description">
                          {{ agent.description || '暂无备注说明信息' }}
                        </div>
                      </div>
                    </button>
                    <div v-if="!filteredCustomAgents.length" class="px-3 py-8 text-center text-xs text-gray-400">
                      <p v-if="agentSearchQuery">
                        未找到与 "<span class="text-gray-600 font-medium">{{ agentSearchQuery }}</span>" 匹配的自定义智能体
                      </p>
                      <p v-else>暂无自定义智能体</p>
                      <button
                        v-if="agentSearchQuery"
                        type="button"
                        class="mt-2 text-[11px] text-primary hover:underline"
                        @click.stop="agentSearchQuery = ''"
                      >
                        清空搜索条件
                      </button>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </div>

          <div>
            <div class="mb-1 flex items-center gap-1.5">
              <span class="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded bg-primary text-[10px] font-bold text-white">1</span>
              <label class="text-xs font-bold text-gray-700">要做什么</label>
              <span class="text-[10px] font-bold text-gray-400">必填 · 执行指令</span>
              <button
                type="button"
                class="inline-flex h-4 w-4 items-center justify-center rounded-full border border-gray-300 text-[10px] font-bold text-gray-400 hover:border-primary hover:bg-primary/5 hover:text-primary"
                title="查看填写示例"
                aria-label="查看填写示例"
                @click="showPromptHelpModal = true"
              >?</button>
              <div class="ml-auto flex items-center gap-1">
                <button
                  v-if="String(editingTask.prompt || '').trim()"
                  type="button"
                  class="px-2.5 py-1 text-[11px] font-semibold rounded-md text-gray-400 hover:bg-red-50 hover:text-red-600"
                  title="清空任务内容"
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
                      class="w-full px-3 py-2 text-left hover:bg-primary/5 transition-colors"
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
                  confirm-message="AI 将针对当前任务内容生成 3 个侧重点不同的优化版本（结构化完整 / 精简直达 / 输出契约），大约需要几秒钟。是否开始？"
                  loading-hint="正在为您生成 3 个差异化方案"
                  @apply="applyOptimizedPrompt"
                  @toast="onOptimizeToast"
                />
              </div>
            </div>
            <div class="mb-1.5 flex items-start gap-1.5 rounded-lg border border-primary/10 bg-primary/5 px-2.5 py-1.5 text-[11px] leading-relaxed text-primary-dark">
              <span class="shrink-0 font-bold">①</span>
              <span>一句话说清<b>查什么、什么范围、要什么结果</b>。格式 / 篇幅 / 口径 / 不要图表请写第 2 栏，那边才会强制生效。</span>
            </div>
            <TaskPromptComposer
              :prompt="String(editingTask.prompt || '')"
              :model="taskModel"
              :approval-mode="taskApprovalMode"
              :resource-scope="taskResourceScope"
              :thinking-enable-override="taskThinkingEnableOverride"
              :reasoning-effort-override="taskReasoningEffortOverride"
              :temperature-override="taskTemperatureOverride"
              :agent-id="editingTask.agent_id"
              @update:prompt="editingTask.prompt = $event"
              @update:model="handleTaskModelSelection"
              @update:approval-mode="taskApprovalMode = $event"
              @update:resource-scope="taskResourceScope = $event"
              @update:thinking-enable-override="taskThinkingEnableOverride = $event"
              @update:reasoning-effort-override="taskReasoningEffortOverride = $event"
              @update:temperature-override="taskTemperatureOverride = $event"
            />
            <div
              v-if="promptFormatAdviceVisible"
              class="mt-1.5 flex items-start gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-700"
            >
              <span class="shrink-0">⚠</span>
              <span class="min-w-0 flex-1">这段像是<b>输出要求</b>（「{{ promptFormatAdviceSentence }}」），写在第 1 栏不会强制生效。</span>
              <button
                type="button"
                class="shrink-0 rounded-md border border-amber-200 bg-white px-2 py-0.5 text-[10px] font-bold text-amber-700 hover:bg-amber-100"
                @click="moveAdviceToConstraints"
              >移到第 2 栏</button>
              <button
                type="button"
                class="shrink-0 text-[10px] font-bold text-amber-500 hover:text-amber-700"
                title="这条提示先收起，内容改动后会重新判断"
                @click="dismissedAdviceSentence = promptFormatAdviceSentence"
              >忽略</button>
            </div>
          </div>

          <div>
            <div class="mb-1 flex items-center gap-1.5">
              <span class="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded bg-gray-300 text-[10px] font-bold text-white">2</span>
              <label class="text-xs font-bold text-gray-700">输出有什么要求</label>
              <span class="text-[10px] font-bold text-gray-400">选填 · 任务约束，留空不影响任务</span>
              <button
                type="button"
                class="inline-flex h-4 w-4 items-center justify-center rounded-full border border-gray-300 text-[10px] font-bold text-gray-400 hover:border-primary hover:bg-primary/5 hover:text-primary"
                title="查看填写说明与示例"
                aria-label="查看填写说明与示例"
                @click="showConstraintsHelpModal = true"
              >?</button>
            </div>
            <div class="mb-1.5 flex items-start gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-[11px] leading-relaxed text-gray-600">
              <span class="shrink-0 font-bold text-gray-400">②</span>
              <span>只写<b>规矩</b>，不写任务。没有特别要求就<b>直接留空</b> —— 留空时任务行为与不加约束完全一致。</span>
            </div>
            <textarea
              v-model="taskConstraints"
              rows="3"
              maxlength="2000"
              class="w-full resize-y rounded-xl border border-gray-200 px-3 py-2 text-xs leading-relaxed text-gray-700 placeholder:text-gray-300 focus:border-primary focus:ring-2 focus:ring-primary/20"
              placeholder="例如：用无序列表，不要图表；金额统一按万元、保留两位小数。"
            ></textarea>
            <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
              <span class="text-[10px] text-gray-400">可直接在上面的框里写，或点一下追加：</span>
              <button
                v-for="preset in constraintPresets"
                :key="preset.label"
                type="button"
                class="inline-flex items-center gap-0.5 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10px] font-semibold text-gray-600 transition hover:border-primary/20 hover:bg-primary/5 hover:text-primary-dark"
                :title="preset.text"
                @click="appendConstraintPreset(preset.text)"
              >
                <span class="font-bold text-gray-400">+</span>{{ preset.label }}
              </button>
            </div>
          </div>

          </TaskFormSection>

          <!-- 方案 A：「什么时候跑」整组前移到「做什么」之后；字段与绑定未改动 -->
          <TaskFormSection
            v-model="showWhenSection"
            :section-id="TASK_SECTION_IDS.when"
            :icon="ClockIcon"
            title="什么时候跑"
            hint="按人话选，随时能看到下次运行时间"
            :summary="taskSectionSummary.when"
            :has-error="taskSectionIssues.issues.when"
          >
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
                      m === 'interval' ? '间隔' : '高级'
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
               <div v-if="cronMode === 'custom'" class="space-y-2">
                   <CronFieldLegend />
                   <div class="flex items-center gap-2">
                       <input
                         v-model="editingTask.cron_expr"
                         placeholder="* * * * *"
                         class="min-w-0 flex-1 px-3 py-2 border rounded-xl font-mono text-sm outline-none focus:border-primary bg-white transition"
                         :class="cronExprValidation.valid ? '' : '!border-rose-300 bg-rose-50/40 text-rose-700 focus:!border-rose-400'"
                         :aria-invalid="cronExprValidation.valid ? 'false' : 'true'"
                       />
                       <button
                         type="button"
                         class="shrink-0 inline-flex items-center gap-1 rounded-xl border border-primary/30 bg-primary/5 px-3 py-2 text-xs font-bold text-primary transition hover:bg-primary/10"
                         @click="openCronBuilder"
                       >
                          可视化生成
                       </button>
                   </div>
                   <p class="text-[10px] text-gray-400">请使用标准 Cron 表达式，或点击「可视化生成」按分钟 / 小时 / 日 / 月 / 周组合</p>
               </div>
            </div>

            <div
              class="mt-2 p-2.5 rounded-xl flex items-start space-x-2 border transition"
              :class="cronExprValidation.valid ? 'bg-primary/5 border-primary/10' : 'bg-rose-50/60 border-rose-200'"
            >
              <span
                class="text-xs mt-0.5 italic shrink-0"
                :class="cronExprValidation.valid ? 'text-primary' : 'text-rose-500'"
              >{{ cronExprValidation.valid ? 'Auto-Translate:' : '⚠ 格式有误:' }}</span>
              <p
                class="text-[11px] font-bold leading-relaxed"
                :class="cronExprValidation.valid ? 'text-primary-dark' : 'text-rose-600'"
              >{{ cronDescription }}</p>
            </div>

            <div
              v-if="cronNextRuns.length"
              class="mt-2 flex flex-wrap items-center gap-1.5 rounded-xl border border-emerald-100 bg-emerald-50/70 px-2.5 py-2"
            >
              <span class="text-[10px] font-bold text-emerald-700">接下来三次运行</span>
              <span
                v-for="(run, index) in cronNextRuns"
                :key="run"
                class="rounded border border-emerald-200 bg-white px-1.5 py-0.5 font-mono text-[10px] font-bold text-emerald-700"
              >{{ run }}<span v-if="index === 0" class="ml-1 font-sans text-[9px] font-bold text-emerald-500">下次</span></span>
            </div>
          </div>

          <div>
            <button
              type="button"
              class="mb-1 flex w-full items-center gap-1.5 text-left"
              :aria-expanded="showAdvancedRetry ? 'true' : 'false'"
              @click="showAdvancedRetry = !showAdvancedRetry"
            >
              <span class="text-[10px] font-bold text-gray-400">{{ showAdvancedRetry ? '▾' : '▸' }}</span>
              <span class="text-xs font-bold text-gray-400 uppercase tracking-widest">执行失败策略</span>
              <span class="text-[10px] text-gray-400">
                {{ taskMaxRetries > 0 ? `失败重试 ${taskMaxRetries} 次 / 间隔 ${taskRetryDelayMinutes} 分钟` : '默认不重试' }}
              </span>
            </button>
            <div v-show="showAdvancedRetry" class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-3 space-y-3">
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
          </TaskFormSection>

          <TaskFormSection
            v-model="showResultSection"
            :section-id="TASK_SECTION_IDS.result"
            :icon="PaperAirplaneIcon"
            title="结果发到哪"
            hint="站内消息始终可用"
            :summary="taskSectionSummary.result"
            :has-error="taskSectionIssues.issues.result"
          >
          <div>
            <label class="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-1">结果通知</label>
            <div class="rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-3 space-y-2">
              <div class="flex flex-wrap gap-x-4 gap-y-2">
                <label
                  v-for="channel in notificationChannelOptions"
                  :key="channel.value"
                  class="flex items-center gap-1.5 text-xs font-bold"
                  :class="isNotificationChannelReady(channel.value) ? 'text-gray-600 cursor-pointer' : 'text-gray-300 cursor-not-allowed'"
                  :title="isNotificationChannelReady(channel.value) ? '' : notificationChannelBlockedTitle(channel.value)"
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
              <div
                v-if="selectedGroupChannels.length"
                class="mt-1 space-y-1.5 border-t border-gray-200/70 pt-2"
              >
                <p class="text-[10px] font-bold text-gray-400">发送到哪个群</p>
                <div
                  v-for="channel in selectedGroupChannels"
                  :key="channel"
                  class="flex items-center gap-2"
                >
                  <span class="w-14 shrink-0 text-[10px] font-bold text-gray-500">{{ notificationChannelLabel(channel) }}</span>
                  <select
                    v-model="notificationChannelGroups[channel]"
                    class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                  >
                    <option value="">默认群（跟随个人中心设置）</option>
                    <option v-for="option in groupOptionsFor(channel)" :key="option.id" :value="String(option.id)">
                      {{ option.label }}
                    </option>
                  </select>
                </div>
                <p class="text-[10px] leading-relaxed text-gray-400">
                  选的群被删除或停用后，任务结果会改发该渠道的默认群，并在投递记录里写明原因。
                </p>
              </div>
              <p class="text-[10px] text-gray-400 leading-relaxed">
                勾选后任务执行完成由调度器统一投递结果；站内消息始终可用。钉钉 / 企业微信 / 邮件需先在个人中心配置并启用对应通道。
              </p>
              <p v-if="unavailableExternalChannels.length" class="text-[10px] text-amber-600 leading-relaxed">
                {{ unavailableExternalChannelsHint }}，已禁止勾选。
                <button
                  v-if="!embedded"
                  type="button"
                  class="ml-1 font-bold text-primary underline underline-offset-2 hover:text-primary-dark"
                  @click="openPersonalNotificationSettings"
                >
                  去个人中心配置消息通知
                </button>
              </p>
              <p v-if="promptOverlapsNotificationChannels" class="text-[10px] text-amber-600 leading-relaxed">
                任务内容里已提到通知渠道；勾选渠道将与之合并，同一渠道运行时只发送一次。
              </p>
            </div>
          </div>
          </TaskFormSection>
        </div>

        <!-- 右栏：任务摘要（宽屏常驻，粘性跟随滚动）-->
        <aside
          v-if="!isMobile"
          class="min-h-0 space-y-2.5 overflow-y-auto border-l border-gray-100 bg-gray-50/40 p-3"
        >
          <div class="rounded-xl border border-gray-200 bg-white px-3 py-2.5">
            <div class="mb-1 flex items-center gap-1.5">
              <span class="text-[10px] font-bold text-gray-400"> 这个任务会这样跑</span>
              <span class="ml-auto text-[9px] font-bold text-gray-300">随填随更新</span>
            </div>
            <p class="text-xs leading-relaxed text-gray-600">
              <span class="font-bold text-gray-800">{{ taskSummaryText.whenValid ? taskSummaryText.when : '（还没选执行时间）' }}</span>
              <template v-if="taskSummaryText.agent">，让 <span class="font-bold text-amber-700">{{ taskSummaryText.agent }}</span> 执行</template>
              <template v-if="taskSummaryText.what">「<span class="text-gray-800">{{ taskSummaryText.what }}</span>」</template>
              <span v-else class="text-gray-400">（还没写「要做什么」）</span>
              <template v-if="taskSummaryText.constraints.length">
                ，并按 <span class="font-bold text-gray-800">{{ taskSummaryText.constraints.length }} 条交付要求</span>输出
              </template>
              <template v-if="taskSummaryText.channels.length">，结果发到 <span class="font-bold text-gray-800">{{ taskSummaryText.channels.join(' + ') }}</span></template>
              <span class="text-gray-500">。</span>
            </p>
          </div>

          <div class="rounded-xl border border-gray-200 bg-white px-3 py-2.5 space-y-1.5">
            <div class="flex items-baseline gap-2 text-[11px]">
              <span class="w-14 shrink-0 text-gray-400">下次运行</span>
              <span v-if="taskSummaryNext" class="rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 font-mono text-[10px] font-bold text-emerald-700">{{ taskSummaryNext }}</span>
              <span v-else class="text-gray-400">—</span>
            </div>
            <div class="flex items-baseline gap-2 text-[11px]">
              <span class="w-14 shrink-0 text-gray-400">任务名</span>
              <span class="min-w-0 flex-1 truncate font-semibold text-gray-700">{{ String(editingTask.name || '').trim() || '未命名任务' }}</span>
            </div>
            <div class="flex items-baseline gap-2 text-[11px]">
              <span class="w-14 shrink-0 text-gray-400">交付要求</span>
              <span class="font-semibold text-gray-700">{{ taskSummaryText.constraints.length ? `${taskSummaryText.constraints.length} 条` : '未填写' }}</span>
            </div>
            <div class="flex items-baseline gap-2 text-[11px]">
              <span class="w-14 shrink-0 text-gray-400">结果发到</span>
              <span class="min-w-0 flex-1 font-semibold text-gray-700">{{ taskSummaryText.channels.length ? taskSummaryText.channels.join(' + ') : '未选择' }}</span>
            </div>
          </div>

          <div class="rounded-xl border border-amber-100 bg-amber-50 px-3 py-2 text-[10px] leading-relaxed text-amber-700">
            <b>自动批准</b>：定时任务无人值守，会直接执行查询类工具、不会停下来等确认；危险操作仍会被拦截。
          </div>

          <p class="px-1 text-[10px] leading-relaxed text-gray-400">
            保存后可在任务列表里点「立即执行」试跑一次，验证「要做什么」与交付要求写得对不对。立即执行不会自动重试。
          </p>
        </aside>
        </div>

        <div v-if="!showTemplatePicker" class="flex justify-end space-x-3 pt-4 pb-[env(safe-area-inset-bottom)] border-t border-gray-50">
          <button @click="closeEditModal" class="px-4 py-2 text-sm font-bold text-gray-400 hover:text-gray-600">取消</button>
          <button @click="saveTask" class="px-8 py-2 bg-primary text-white rounded-xl shadow-lg shadow-primary/20 hover:bg-primary-dark transition-all font-bold text-sm">确认保存</button>
        </div>
      </div>
    </Modal>

    <Modal
      v-if="showCronBuilder"
      title="Cron 表达式生成器"
      size="max-w-2xl"
      :z-index="80"
      @close="showCronBuilder = false"
    >
      <CronAdvancedBuilder v-model="cronDraft" />
      <template #footer>
        <div class="flex items-center justify-between gap-2">
          <p class="text-[11px] text-gray-400">组合完成后点击「应用」回填到表达式输入框</p>
          <div class="flex items-center gap-2">
            <button type="button" class="rounded-lg px-4 py-2 text-sm font-bold text-gray-400 hover:text-gray-600" @click="showCronBuilder = false">取消</button>
            <button
              type="button"
              :disabled="!cronDraftValid"
              class="rounded-lg bg-primary px-4 py-2 text-sm font-bold text-white transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40"
              @click="applyCronDraft"
            >应用</button>
          </div>
        </div>
      </template>
    </Modal>

    <Modal
      v-if="showPromptHelpModal"
      title="要做什么 · 填写示例"
      size="max-w-md"
      :z-index="80"
      @close="showPromptHelpModal = false"
    >
      <div class="space-y-3">
        <p class="text-[11px] leading-relaxed text-gray-500">
          写清「<b>查什么、什么范围、要什么结果</b>」。通知请用下方「结果通知」勾选；格式 / 篇幅 / 单位这类要求请写到第 2 栏「输出有什么要求」，那边才会强制生效。
        </p>
        <div
          v-for="example in promptExamples"
          :key="example.title"
          class="rounded-xl border border-gray-100 bg-gray-50/70 p-2.5"
        >
          <div class="mb-1.5 flex items-center justify-between gap-2">
            <div class="min-w-0">
              <p class="text-xs font-bold text-gray-800">{{ example.title }}</p>
              <p class="mt-0.5 text-[10px] text-gray-400 line-clamp-1">{{ example.tip }}</p>
            </div>
            <button
              type="button"
              class="shrink-0 rounded-lg bg-primary/5 px-2 py-1 text-[10px] font-bold text-primary hover:bg-primary/10"
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

    <Modal
      v-if="showConstraintsHelpModal"
      title="输出要求 · 填写说明"
      size="max-w-2xl"
      :z-index="80"
      @close="showConstraintsHelpModal = false"
    >
      <div class="space-y-3">
        <p class="text-[11px] leading-relaxed text-gray-500">
          第 1 栏「要做什么」决定「<b>做什么</b>」，这里决定「<b>怎么做</b>」。<br />
          这里填写的内容会作为<b>高优先级约束</b>合并进智能体的系统提示词——当它与智能体自身的输出习惯（例如默认要求画图表、要求固定表格）冲突时，<b>以这里为准</b>。
        </p>
        <div class="overflow-hidden rounded-xl border border-gray-100">
          <p class="border-b border-gray-100 bg-gray-50/70 px-2.5 py-1.5 text-xs font-bold text-gray-800">同一件事，两栏分别写</p>
          <table class="w-full text-left text-[11px] leading-relaxed">
            <thead>
              <tr class="bg-gray-50/40 text-[10px] font-bold text-gray-400">
                <th class="px-2.5 py-1.5 font-bold">场景</th>
                <th class="px-2.5 py-1.5 font-bold">① 要做什么</th>
                <th class="px-2.5 py-1.5 font-bold">② 输出有什么要求</th>
              </tr>
            </thead>
            <tbody class="text-gray-600">
              <tr class="border-t border-gray-50 align-top">
                <td class="px-2.5 py-1.5"><b class="text-gray-800">机房能效周报</b><br /><span class="text-[10px] text-gray-400">移动端看，不想看图表</span></td>
                <td class="px-2.5 py-1.5">查华东一号机房上周的 PUE 均值与峰值，列出告警次数 Top3。</td>
                <td class="px-2.5 py-1.5">用无序列表，不要图表；PUE 保留两位小数。</td>
              </tr>
              <tr class="border-t border-gray-50 align-top">
                <td class="px-2.5 py-1.5"><b class="text-gray-800">任务失败盘点</b><br /><span class="text-[10px] text-gray-400">只想要结论，不要过程</span></td>
                <td class="px-2.5 py-1.5">汇总本周平台定时任务的失败次数，按失败原因分组。</td>
                <td class="px-2.5 py-1.5">只输出结论和关键数据，不要过程说明；改进建议最多 3 条。</td>
              </tr>
              <tr class="border-t border-gray-50 align-top">
                <td class="px-2.5 py-1.5"><b class="text-gray-800">项目进度简报</b><br /><span class="text-[10px] text-gray-400">口径固定、范围收窄</span></td>
                <td class="px-2.5 py-1.5">汇总本周研发一部的项目进度，标注延期项。</td>
                <td class="px-2.5 py-1.5">仅统计华东区项目，排除已归档；进度按百分比整数；金额按万元。</td>
              </tr>
              <tr class="border-t border-gray-50 align-top">
                <td class="px-2.5 py-1.5"><b class="text-gray-800">天气巡检</b><br /><span class="text-[10px] text-gray-400">没有特别要求</span></td>
                <td class="px-2.5 py-1.5">用 get_current_weather 查询上海实时天气与温度，整理成简报。</td>
                <td class="px-2.5 py-1.5 text-gray-400">（留空）—— 没要求就别写</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="rounded-xl border border-gray-100 bg-gray-50/70 p-2.5 space-y-1.5">
          <p class="text-xs font-bold text-gray-800">可以用来约束什么</p>
          <p class="text-[11px] leading-relaxed text-gray-600"><b>输出形态</b>：列表 / 表格 / 纯文本、禁止图表代码块、按移动端排版。</p>
          <p class="text-[11px] leading-relaxed text-gray-600"><b>业务口径</b>：指标定义、单位、时间口径、统计方式。</p>
          <p class="text-[11px] leading-relaxed text-gray-600"><b>数据范围</b>：只看哪些区域 / 业务线 / 数据集。</p>
          <p class="text-[11px] leading-relaxed text-gray-600"><b>禁止事项</b>：不要多余寒暄、禁止臆测、不要追加反问。</p>
        </div>
        <div
          v-for="example in constraintExamples"
          :key="example.title"
          class="rounded-xl border border-gray-100 bg-gray-50/70 p-2.5"
        >
          <div class="mb-1.5 flex items-center justify-between gap-2">
            <div class="min-w-0">
              <p class="text-xs font-bold text-gray-800">{{ example.title }}</p>
              <p class="mt-0.5 text-[10px] text-gray-400 line-clamp-1">{{ example.tip }}</p>
            </div>
            <button
              type="button"
              class="shrink-0 rounded-lg bg-primary/5 px-2 py-1 text-[10px] font-bold text-primary hover:bg-primary/10"
              @click="applyConstraintExample(example.text)"
            >填入</button>
          </div>
          <pre class="max-h-24 overflow-y-auto whitespace-pre-wrap rounded-lg border border-gray-100 bg-white px-2.5 py-2 text-[10px] leading-relaxed text-gray-700">{{ example.text }}</pre>
        </div>
        <p class="text-[11px] leading-relaxed text-gray-500">
          <b>留空即不生效</b> —— 不填写时任务行为与不加约束完全一致。
        </p>
        <p class="text-[11px] leading-relaxed text-amber-600">
          <b>需要强制生效的格式要求，请写在这里。</b>写在第 1 栏「要做什么」里的格式要求不会获得同等优先级。
        </p>
        <div class="flex justify-end">
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-gray-400 hover:text-gray-600" @click="showConstraintsHelpModal = false">关闭</button>
        </div>
      </div>
    </Modal>

    <!-- 执行历史抽屉：时间轴 + 渲染后的 markdown（原来把 summary 的 markdown 源码当纯文本显示） -->
    <Teleport to="body">
      <div v-if="showLogsDrawer" class="fixed inset-0 z-[100] flex justify-end">
        <div class="fixed inset-0 bg-black/20 backdrop-blur-sm" @click="showLogsDrawer = false"></div>
        <div
          class="relative flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl animate-slide-in-right"
          :class="isMobile ? 'max-w-none' : ''"
        >
          <!-- 头部 -->
          <div class="border-b border-gray-200 px-5 pb-2.5 pt-4">
            <div class="flex items-center gap-2">
              <h2 class="text-[15px] font-extrabold tracking-tight text-gray-900">执行历史</h2>
              <button
                type="button"
                class="ml-auto grid h-7 w-7 cursor-pointer place-items-center rounded-full text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
                title="关闭"
                @click="showLogsDrawer = false"
              >
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>
            <p class="mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px] text-gray-400">
              <span class="text-[12px] font-bold text-gray-700">{{ selectedTask?.name || '任务' }}</span>
              <span class="h-[3px] w-[3px] rounded-full bg-gray-300"></span>
              <span>{{ selectedTask?.agent_name || selectedTask?.agent_id || '—' }}</span>
              <span class="h-[3px] w-[3px] rounded-full bg-gray-300"></span>
              <span>共 {{ logsTotal }} 次执行</span>
            </p>
          </div>

          <!-- 概览 + 筛选 -->
          <div class="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-gray-100 bg-[#fcfcfd] px-5 py-2">
            <span class="text-[11.5px] text-gray-500">最近 <b class="font-extrabold tabular-nums text-gray-900">{{ logsSummary.loaded }}</b> 次</span>
            <span class="h-3 w-px bg-gray-200"></span>
            <span class="text-[11.5px] text-gray-500">成功 <b class="font-extrabold tabular-nums text-gray-900">{{ logsSummary.ok }}</b></span>
            <span class="text-[11.5px] text-gray-500">
              失败 <b class="font-extrabold tabular-nums" :class="logsSummary.fail ? 'text-red-600' : 'text-gray-900'">{{ logsSummary.fail }}</b>
            </span>
            <span class="h-3 w-px bg-gray-200"></span>
            <span class="text-[11.5px] text-gray-500">平均耗时 <b class="font-extrabold tabular-nums text-gray-900">{{ logsSummary.avg }}</b></span>
            <div class="ml-auto flex items-center gap-1">
              <button
                type="button"
                class="cursor-pointer rounded-md px-2 py-0.5 text-[11px] font-bold transition-colors"
                :class="logsFilter === 'all' ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-100'"
                @click="logsFilter = 'all'"
              >全部</button>
              <button
                type="button"
                class="cursor-pointer rounded-md px-2 py-0.5 text-[11px] font-bold transition-colors disabled:cursor-default disabled:opacity-40"
                :class="logsFilter === 'fail' ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-100'"
                :disabled="logsSummary.fail === 0"
                @click="logsFilter = 'fail'"
              >只看失败 {{ logsSummary.fail }}</button>
              <span class="mx-1 h-3 w-px bg-gray-200"></span>
              <button
                type="button"
                class="cursor-pointer whitespace-nowrap rounded-md px-2 py-0.5 text-[11px] font-bold transition-colors"
                :class="logsDensity === 'compact' ? 'bg-primary/5 text-primary' : 'text-gray-500 hover:bg-gray-100'"
                title="所有记录压成一行，一屏扫完（再点一次回到标准）"
                @click="setLogsDensity('compact')"
              >⌃ 全部折叠</button>
              <button
                type="button"
                class="cursor-pointer whitespace-nowrap rounded-md px-2 py-0.5 text-[11px] font-bold transition-colors"
                :class="logsDensity === 'expanded' ? 'bg-primary/5 text-primary' : 'text-gray-500 hover:bg-gray-100'"
                title="所有正文一次性展开（再点一次回到标准）"
                @click="setLogsDensity('expanded')"
              >⌄ 全部展开</button>
              <button
                type="button"
                class="ml-1 cursor-pointer rounded-md px-2 py-0.5 text-[11px] font-bold transition-colors"
                :class="showRawMarkdown ? 'bg-amber-100 text-amber-700' : 'text-gray-400 hover:bg-gray-100 hover:text-gray-700'"
                title="切换显示 markdown 原文，排查渲染问题用"
                @click="showRawMarkdown = !showRawMarkdown"
              >原文</button>
            </div>
          </div>

          <!-- 时间轴 -->
          <div class="custom-scrollbar flex-1 overflow-y-auto px-5 pb-7 pt-3.5">
            <div v-if="logsLoading && logs.length === 0" class="flex flex-col items-center justify-center py-20">
              <div class="mb-4 h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
              <p class="text-sm text-gray-400">正在拉取审计轨迹...</p>
            </div>
            <div v-else-if="logs.length === 0" class="rounded-2xl border border-dashed border-gray-200 bg-gray-50 py-20 text-center">
              <p class="text-gray-400">暂无执行记录</p>
            </div>
            <div v-else-if="filteredLogs.length === 0" class="rounded-2xl border border-dashed border-gray-200 bg-gray-50 py-12 text-center">
              <p class="text-gray-400">最近 {{ logsSummary.loaded }} 次里没有失败记录</p>
              <button type="button" class="mt-2 cursor-pointer text-xs font-bold text-primary hover:underline" @click="logsFilter = 'all'">显示全部</button>
            </div>

            <div v-else>
              <div v-for="group in groupedLogs" :key="group.day" class="mb-3">
                <!-- 分组头：有底色的分节条 + 吸顶。只靠灰字和小箭头时，它跟条目的视觉重量几乎一样，翻起来分不清「分组还是记录」 -->
                <button
                  type="button"
                  class="sticky top-0 z-10 mb-1.5 flex w-full cursor-pointer items-center gap-2 rounded-lg border border-gray-200 bg-gray-100/95 px-2.5 py-1.5 text-left backdrop-blur-sm transition-colors hover:bg-gray-200/70"
                  :title="logsGroupCollapsed[group.day] ? '展开这一组' : '收起这一组'"
                  @click="toggleLogsGroup(group.day)"
                >
                  <span class="w-2.5 shrink-0 text-[9px] text-gray-400">{{ logsGroupCollapsed[group.day] ? '▸' : '▾' }}</span>
                  <b class="text-[13px] font-extrabold tracking-tight text-gray-900">{{ group.day }}</b>
                  <span class="text-[11px] font-bold text-gray-500">{{ group.runs.length }} 次</span>
                  <span v-if="group.ok" class="text-[11px] font-bold text-emerald-600">成功 {{ group.ok }}</span>
                  <span v-if="group.fail" class="text-[11px] font-bold text-red-600">失败 {{ group.fail }}</span>
                  <span v-if="logsGroupCollapsed[group.day]" class="ml-auto text-[10.5px] font-semibold text-gray-400">已收起</span>
                </button>

                <template v-if="!logsGroupCollapsed[group.day]">
                  <div v-for="(log, idx) in group.runs" :key="log.id">
                    <!-- 紧凑行：时间 + 状态 + 耗时 + 摘要首行，一屏能扫十几条 -->
                    <div
                      v-if="logIsCompactRow(log)"
                      class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-[11.5px] transition-colors hover:bg-gray-50"
                      title="点击展开这一条"
                      @click="(log as any).mdExpanded = true"
                    >
                      <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="isLogOk(log) ? 'bg-emerald-500' : 'bg-red-500'"></span>
                      <span class="shrink-0 font-extrabold tabular-nums text-gray-900">{{ logClock((log as any).created_at) }}</span>
                      <span
                        class="shrink-0 rounded px-1 text-[10px] font-extrabold"
                        :class="isLogOk(log) ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'"
                      >{{ logStatusMeta((log as any).status).label }}</span>
                      <span class="shrink-0 tabular-nums text-gray-400">{{ formatDurationMs((log as any).execution_time_ms) }}</span>
                      <span class="min-w-0 truncate text-gray-400">{{ logPreview(log) }}</span>
                    </div>

                    <div v-else class="flex gap-2.5">
                      <!-- 时间轴轨道：连续成功是一条顺滑的线，失败那条一眼跳出来 -->
                      <div class="flex w-3.5 shrink-0 flex-col items-center pt-1.5">
                        <i
                          class="h-2.5 w-2.5 shrink-0 rounded-full"
                          :class="isLogOk(log) ? 'bg-emerald-500 ring-[3px] ring-emerald-50' : 'bg-red-500 ring-[3px] ring-red-50'"
                        ></i>
                        <s v-if="idx !== group.runs.length - 1" class="mt-1 w-px flex-1 bg-gray-200"></s>
                      </div>

                      <div class="min-w-0 flex-1 pb-3">
                        <!-- 时间 / 状态 / 耗时（日期由分组头交代，这里只留时刻） -->
                        <div class="mb-1.5 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
                          <span class="text-[12.5px] font-extrabold tabular-nums text-gray-900">{{ logClock((log as any).created_at) }}</span>
                          <span class="text-[11px] tabular-nums text-gray-400">{{ formatDate((log as any).created_at) }}</span>
                          <span
                            class="rounded-[5px] border px-1.5 py-px text-[10px] font-extrabold"
                            :class="isLogOk(log)
                              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                              : 'border-red-200 bg-red-50 text-red-700'"
                          >{{ logStatusMeta((log as any).status).label }}</span>
                          <span class="ml-auto whitespace-nowrap text-[11px] text-gray-400">
                            耗时 <span class="tabular-nums">{{ formatDurationMs((log as any).execution_time_ms) }}</span>
                          </span>
                        </div>

                        <!-- 正文：summary 是 markdown，必须渲染，不能当纯文本 -->
                        <div class="relative rounded-[10px] border border-gray-200 bg-white px-3 py-2.5">
                          <div v-if="!(log as any).summary && !(log as any).query" class="text-xs text-gray-400">
                            （本次执行没有产出摘要）
                          </div>
                          <pre
                            v-else-if="showRawMarkdown"
                            class="max-h-[132px] overflow-hidden whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-gray-600"
                          >{{ (log as any).summary || (log as any).query }}</pre>
                          <div
                            v-else
                            class="message-host log-md max-w-none break-words text-gray-800"
                            :class="logShowsBody(log) ? '' : 'max-h-[132px] overflow-hidden'"
                          >
                            <MessageRenderer
                              :content="(log as any).summary || (log as any).query"
                              theme="default"
                              hide-quick-buttons
                              readonly
                            />
                          </div>
                          <div
                            v-if="!showRawMarkdown && !logShowsBody(log) && (log as any).mdExpandable"
                            class="pointer-events-none absolute inset-x-0 bottom-0 h-8 rounded-b-[10px] bg-gradient-to-t from-white to-transparent"
                          ></div>
                        </div>

                        <!-- 操作：展开全文 / 执行步骤 / 完整链路 -->
                        <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
                          <button
                            v-if="(log as any).mdExpandable"
                            type="button"
                            class="cursor-pointer rounded-md px-2 py-0.5 text-[11px] font-bold text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900"
                            @click="(log as any).mdExpanded = logShowsBody(log) ? false : true"
                          >{{ logShowsBody(log) ? '收起' : '展开全文' }}</button>
                          <button
                            type="button"
                            class="inline-flex cursor-pointer items-center gap-1 rounded-md border border-dashed border-gray-200 px-2 py-0.5 text-[11px] font-bold text-gray-400 transition-colors hover:bg-gray-50 hover:text-gray-700"
                            @click="toggleLogSteps(log)"
                          >
                            <span v-if="(log as any).stepsLoading" class="h-3 w-3 animate-spin rounded-full border-2 border-primary/30 border-t-primary"></span>
                            执行步骤
                            <span v-if="(log as any).steps?.length" class="rounded-full bg-gray-100 px-1 text-[10px] font-bold text-gray-500">{{ (log as any).steps.length }}</span>
                            <span class="text-[9px]">{{ (log as any).isExpanded ? '⌃' : '⌄' }}</span>
                          </button>
                          <button
                            type="button"
                            class="ml-auto cursor-pointer whitespace-nowrap text-[11px] font-bold text-primary hover:underline"
                            @click="viewTrace((log as any).trace_id)"
                          >完整链路 →</button>
                        </div>

                        <!-- 执行步骤 -->
                        <div v-show="(log as any).isExpanded" class="mt-2 border-l-2 border-primary/10 pl-2.5">
                          <div v-if="(log as any).stepsLoading" class="py-3">
                            <div class="flex animate-pulse items-center gap-1.5">
                              <div class="h-1 w-1 rounded-full bg-primary/40"></div>
                              <div class="h-1 w-1 rounded-full bg-primary/40"></div>
                              <div class="h-1 w-1 rounded-full bg-primary/40"></div>
                            </div>
                          </div>
                          <template v-else-if="(log as any).steps && (log as any).steps.length > 0">
                            <div
                              v-for="(step, sIdx) in (log as any).steps"
                              :key="sIdx"
                              class="mb-1.5 rounded-lg border border-gray-100 bg-[#fafbfc] px-2 py-1.5"
                            >
                              <div class="mb-1 flex items-center gap-1.5 text-[10px]">
                                <span
                                  class="rounded px-1 py-px font-extrabold"
                                  :class="{
                                    'bg-primary/10 text-blue-700': (step as any).event_type === 'thought',
                                    'bg-primary/10 text-primary-dark': (step as any).event_type === 'router',
                                    'bg-amber-100 text-amber-700': (step as any).event_type === 'tool_call',
                                    'bg-emerald-100 text-emerald-700': (step as any).event_type === 'synthesis' || (step as any).event_type === 'final_answer',
                                    'bg-red-100 text-red-700': (step as any).event_type === 'error',
                                  }"
                                >{{ (step as any).event_type }}</span>
                                <span v-if="(step as any).tool_name" class="font-mono font-bold text-gray-700">{{ (step as any).tool_name }}</span>
                                <span class="ml-auto tabular-nums text-gray-400">{{ (step as any).execution_time_ms?.toFixed(0) }}ms</span>
                              </div>
                              <div class="line-clamp-2 text-[11px] leading-relaxed text-gray-500">
                                {{ (step as any).tool_input ? (typeof (step as any).tool_input === 'string' ? (step as any).tool_input : JSON.stringify((step as any).tool_input)) : '' }}
                                {{ (step as any).tool_output?.content || (step as any).raw_log || '' }}
                              </div>
                            </div>
                          </template>
                          <div v-else class="py-1.5 text-[11px] italic text-gray-400">未记录详细步骤</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </template>
              </div>
            </div>

            <div v-if="logsHasMore" class="flex justify-center pb-8 pt-4">
              <button
                type="button"
                class="flex cursor-pointer items-center rounded-lg bg-gray-100 px-4 py-2 text-xs font-bold text-gray-600 transition-colors hover:bg-gray-200 disabled:opacity-50"
                :disabled="logsLoading"
                @click="loadMoreLogs"
              >
                <span v-if="logsLoading" class="mr-2 h-3 w-3 animate-spin rounded-full border-2 border-gray-400 border-t-transparent"></span>
                加载更多日志...
              </button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

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

    <!-- 元数据定时巡检专属配置与监控弹窗（仅 Admin 可用） -->
    <MetadataCronInspectionModal
      v-if="userInfo?.role === 'admin'"
      :show="showCronInspectionModal"
      @close="showCronInspectionModal = false"
      @updated="fetchTasks(true)"
    />
  </div>
</template>

<style scoped>
/*
 * 筛选用的原生 select。
 *
 * 浏览器自带的箭头（Chrome 是一对折线）和这排圆角控件不是一套语言，宽度也由 UA
 * 决定，跟左边的搜索框对不齐。这里关掉原生外观、换成与搜索框图标同粗细的单箭头，
 * 并锁死 38px 高度——`py-2 + text-sm` 的输入框正好是这个高度，两者基线才对得上。
 * padding 写在类里而不是用 Tailwind：全局 base 层给 select 兜了 `padding: 8px 12px`，
 * 未分层的组件样式优先级更高，在这里一次说清最不容易被别处带偏。
 */
.task-filter-select {
  appearance: none;
  -webkit-appearance: none;
  height: 38px;
  padding: 0 2rem 0 0.75rem;
  line-height: 1.25rem;
  cursor: pointer;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%236b7280' stroke-width='2'><path stroke-linecap='round' stroke-linejoin='round' d='M6 9l6 6 6-6'/></svg>");
  background-repeat: no-repeat;
  background-position: right 0.625rem center;
  background-size: 0.875rem;
}

.drawer-slide-enter-active, .drawer-slide-leave-active {
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
.drawer-slide-enter-from, .drawer-slide-leave-to {
  transform: translateX(100%);
}

/*
 * 历史抽屉里的 markdown 比聊天页更紧凑。
 * 聊天页是沉浸阅读（14px / space-y-4），抽屉里是回溯列表，行距和段距都收一档，
 * 否则一条日报就能占掉半个抽屉。
 */
.log-md :deep(.message-renderer.space-y-4 > * + *) {
  margin-top: 0.5rem;
}
.log-md :deep(.markdown-body) {
  font-size: 12.5px;
  line-height: 1.6;
}
.log-md :deep(.markdown-body p) {
  margin-bottom: 0.5rem;
}
.log-md :deep(.markdown-body h1),
.log-md :deep(.markdown-body h2),
.log-md :deep(.markdown-body h3),
.log-md :deep(.markdown-body h4) {
  margin-top: 0.6rem;
  margin-bottom: 0.3rem;
  font-size: 13px;
  line-height: 1.4;
}
.log-md :deep(.markdown-body table) {
  font-size: 11.5px;
  margin: 0.35rem 0;
}
.log-md :deep(.markdown-body th),
.log-md :deep(.markdown-body td) {
  padding: 4px 8px;
}
.log-md :deep(.markdown-body ul),
.log-md :deep(.markdown-body ol) {
  padding-left: 1.1rem;
  margin: 0.3rem 0;
}

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
