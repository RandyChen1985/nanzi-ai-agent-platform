<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { modelApi, type AIModel, type ReasoningEffort } from '@/api/model'
import { mcpToolDisplayName } from '@/utils/mcpToolDisplayName'

export type TaskApprovalMode = 'ask' | 'allow' | 'deny'

export interface TaskScopeItem {
  id: string
  name: string
  description?: string
  scope?: string
  server_name?: string
  server_remark?: string
  dataset_name?: string
}

interface McpOptionGroup {
  serverName: string
  serverRemark?: string
  tools: TaskScopeItem[]
}

const REASONING_EFFORT_OPTIONS: Array<{ value: ReasoningEffort; label: string; description: string }> = [
  { value: 'none', label: '无', description: '不额外增加思考预算' },
  { value: 'minimal', label: '极简', description: '快速完成简单推理' },
  { value: 'low', label: '低', description: '常规代码、一般分析' },
  { value: 'medium', label: '中', description: '需要一定推理的任务' },
  { value: 'high', label: '高', description: 'Debug、SQL、复杂分析、Agent' },
  { value: 'xhigh', label: '极高', description: '极难 Coding Agent、长任务' },
]

export interface TaskResourceScope {
  project_name?: string
  datasets: TaskScopeItem[]
  knowledge_bases: TaskScopeItem[]
  skills: TaskScopeItem[]
  mcp_tools: TaskScopeItem[]
}

const props = withDefaults(
  defineProps<{
    prompt: string
    model: string
    approvalMode: TaskApprovalMode
    resourceScope: TaskResourceScope
    thinkingEnableOverride?: boolean | null
    reasoningEffortOverride?: ReasoningEffort | null
    temperatureOverride?: number | null
    agentId?: string | null
  }>(),
  {
    model: '',
    approvalMode: 'allow',
    agentId: null,
  },
)

const emit = defineEmits<{
  (e: 'update:prompt', value: string): void
  (e: 'update:model', value: string): void
  (e: 'update:approvalMode', value: TaskApprovalMode): void
  (e: 'update:resourceScope', value: TaskResourceScope): void
  (e: 'update:thinking-enable-override', value: boolean | null): void
  (e: 'update:reasoning-effort-override', value: ReasoningEffort | null): void
  (e: 'update:temperature-override', value: number | null): void
}>()

type PanelKey = 'model' | 'approval' | 'datasets' | 'knowledge_bases' | 'skills' | 'mcp_tools' | null

const APPROVAL_OPTIONS: { value: TaskApprovalMode; label: string; description: string }[] = [
  {
    value: 'allow',
    label: '自动批准',
    description: '推荐：定时任务无人值守，自动执行工具；危险操作仍会拦截',
  },
  {
    value: 'ask',
    label: '请求批准',
    description: '工具调用需人工确认；定时任务无法弹窗审批，可能卡在待确认',
  },
  {
    value: 'deny',
    label: '拒绝执行',
    description: '禁止需确认的工具调用，仅保留只读能力',
  },
]

const activePanel = ref<PanelKey>(null)
const barRef = ref<HTMLElement | null>(null)
const panelRef = ref<HTMLElement | null>(null)
const modelListScrollRef = ref<HTMLElement | null>(null)
const triggerRefs = ref<Partial<Record<Exclude<PanelKey, null>, HTMLElement | null>>>({})

const setTriggerRef = (panel: Exclude<PanelKey, null>, el: unknown) => {
  triggerRefs.value[panel] = el instanceof HTMLElement ? el : null
}
const availableModels = ref<AIModel[]>([])
const optionLists = ref<Record<'datasets' | 'knowledge_bases' | 'skills' | 'mcp_tools', TaskScopeItem[]>>({
  datasets: [],
  knowledge_bases: [],
  skills: [],
  mcp_tools: [],
})
const optionSearch = ref('')
const optionsLoading = ref(false)
/** 技能面板：平台 / 个人，对齐 EmbedChat 技能中心 */
const skillScopeTab = ref<'global' | 'personal'>('global')
/** MCP 分组默认折叠，展开后再选工具 */
const collapsedMcpGroups = ref<Set<string>>(new Set())

const modelLabel = computed(() => {
  if (!props.model) return '默认模型'
  const hit = availableModels.value.find((item) => item.model_id === props.model)
  return hit?.name || props.model
})

const selectedModelConfig = computed(() => {
  if (!props.model) return null
  return availableModels.value.find((item) => item.model_id === props.model) || null
})

const thinkingEnabledForTask = computed(() => {
  if (!selectedModelConfig.value?.thinking_enable) return false
  return props.thinkingEnableOverride ?? Boolean(selectedModelConfig.value.thinking_only)
})

const canToggleThinking = computed(() => Boolean(
  selectedModelConfig.value?.thinking_enable
  && (!thinkingEnabledForTask.value || selectedModelConfig.value.allow_disable_thinking),
))

const supportedReasoningEfforts = computed(() => {
  const supported = selectedModelConfig.value?.supported_reasoning_efforts || []
  return REASONING_EFFORT_OPTIONS.filter((option) => supported.includes(option.value))
})

const selectedReasoningEffort = computed(() => {
  if (props.reasoningEffortOverride !== undefined && props.reasoningEffortOverride !== null) {
    return props.reasoningEffortOverride
  }
  return selectedModelConfig.value?.reasoning_effort ?? null
})

const reasoningEffortLabel = computed(() => {
  if (props.reasoningEffortOverride === null || props.reasoningEffortOverride === undefined) {
    return selectedModelConfig.value?.reasoning_effort
      ? REASONING_EFFORT_OPTIONS.find((option) => option.value === selectedModelConfig.value?.reasoning_effort)?.label || '默认'
      : '默认'
  }
  return REASONING_EFFORT_OPTIONS.find((option) => option.value === props.reasoningEffortOverride)?.label || '默认'
})

const thinkingSummaryLabel = computed(() => {
  if (!selectedModelConfig.value?.thinking_enable) return ''
  if (!thinkingEnabledForTask.value) return '关'
  if (props.reasoningEffortOverride === null || props.reasoningEffortOverride === undefined) {
    return selectedModelConfig.value.reasoning_effort ? reasoningEffortLabel.value : '思考'
  }
  return reasoningEffortLabel.value
})

const isFollowingModelEffort = computed(
  () => props.reasoningEffortOverride === null || props.reasoningEffortOverride === undefined,
)

/** 模型默认温度（未配置则为 0.7） */
const defaultModelTemperature = computed(() => {
  const t = (selectedModelConfig.value as any)?.temperature
  return typeof t === 'number' && Number.isFinite(t) ? t : 0.7
})

/** 实际生效温度 */
const effectiveTemperature = computed(() => {
  if (props.temperatureOverride !== null && props.temperatureOverride !== undefined) {
    return props.temperatureOverride
  }
  return defaultModelTemperature.value
})

/** 是否存在手动温度覆盖 */
const hasTemperatureOverride = computed(
  () => props.temperatureOverride !== null && props.temperatureOverride !== undefined,
)

/** 温度微徽章文案：仅当手动覆盖时显示 [T: 0.2] */
const temperatureSummaryLabel = computed(() => {
  if (!hasTemperatureOverride.value || props.temperatureOverride === null || props.temperatureOverride === undefined) {
    return ''
  }
  return `T: ${props.temperatureOverride.toFixed(props.temperatureOverride % 1 === 0 ? 1 : 2).replace(/\.?0+$/, (m) => (m.length > 2 ? '.0' : m))}`
})

const isTemperatureOverLimit = computed(() => effectiveTemperature.value > 1.0)

const getTemperatureGuidance = (temp: number) => {
  if (temp <= 0.3) return '适合代码、SQL、数据提取与逻辑严密任务'
  if (temp <= 0.8) return '适合常规问答、通用对话与分析任务'
  if (temp <= 1.2) return '适合创意发散、头脑风暴与开放写作'
  return '高发散度：回答极具多样性，但可能降低连贯性'
}

const setTemperaturePreset = (temp: number) => {
  emit('update:temperature-override', Math.round(temp * 100) / 100)
}

const handleTemperatureSliderChange = (event: Event) => {
  const val = parseFloat((event.target as HTMLInputElement).value)
  if (Number.isFinite(val)) {
    emit('update:temperature-override', Math.round(val * 100) / 100)
  }
}

const resetTemperatureOverride = () => {
  emit('update:temperature-override', null)
}

const modelTriggerTooltip = computed(() => {
  const base = selectedModelConfig.value?.name || selectedModelConfig.value?.model_id || '当前模型'
  const parts = [`模型: ${base}`]
  if (selectedModelConfig.value?.thinking_enable) {
    parts.push(`思考: ${thinkingSummaryLabel.value || '开启'}`)
  }
  if (hasTemperatureOverride.value && props.temperatureOverride !== null && props.temperatureOverride !== undefined) {
    parts.push(`自定义温度: ${props.temperatureOverride.toFixed(2)}`)
  }
  return parts.join(' · ')
})

const thinkingPanelSubtitle = computed(() => {
  const name = selectedModelConfig.value?.name || selectedModelConfig.value?.model_id || '当前模型'
  return `${name} · 本次任务`
})

const showThinkingPanel = ref(false)

const approvalLabel = computed(
  () => APPROVAL_OPTIONS.find((item) => item.value === props.approvalMode)?.label || '自动批准',
)

const selectedChips = computed(() => {
  const chips: { key: string; label: string; group: keyof TaskResourceScope }[] = []
  for (const item of props.resourceScope.datasets || []) {
    chips.push({ key: `dataset:${item.id}`, label: item.name, group: 'datasets' })
  }
  for (const item of props.resourceScope.knowledge_bases || []) {
    chips.push({ key: `kb:${item.id}`, label: item.name, group: 'knowledge_bases' })
  }
  for (const item of props.resourceScope.skills || []) {
    chips.push({ key: `skill:${item.id}`, label: item.name, group: 'skills' })
  }
  for (const item of props.resourceScope.mcp_tools || []) {
    chips.push({
      key: `mcp:${item.id || item.name}`,
      label: mcpToolDisplayName(item.name || item.id, item.server_name),
      group: 'mcp_tools',
    })
  }
  return chips
})

const showAskWarning = computed(() => props.approvalMode === 'ask')

// 面板挂到 body 并用 fixed 定位：任务弹窗正文与本组件根节点都有 overflow 裁剪，
// 绝对定位的浮层会被切掉导致选项点不到。
const PANEL_WIDTH: Partial<Record<Exclude<PanelKey, null>, number>> = {
  model: 520,
  approval: 300,
  skills: 340,
  mcp_tools: 360,
}
const PANEL_GAP = 8
const VIEWPORT_MARGIN = 12

const panelRect = ref({
  left: 0,
  width: 240,
  top: 0,
  bottom: 0,
  maxHeight: 320,
  placement: 'top' as 'top' | 'bottom',
})

const updatePanelRect = () => {
  const bar = barRef.value
  const panel = activePanel.value
  if (!bar || !panel) return
  const barRect = bar.getBoundingClientRect()
  const trigger = triggerRefs.value[panel] || bar
  const triggerRect = trigger.getBoundingClientRect()
  // 无显式宽度时沿用整条工具栏宽度（数据集/知识库等宽面板），水平仍锚到触发按钮
  const preferredWidth = PANEL_WIDTH[panel] ?? Math.max(barRect.width, 280)
  const width = Math.min(
    preferredWidth,
    Math.max(200, window.innerWidth - VIEWPORT_MARGIN * 2),
  )
  const spaceAbove = barRect.top - PANEL_GAP - VIEWPORT_MARGIN
  const spaceBelow = window.innerHeight - barRect.bottom - PANEL_GAP - VIEWPORT_MARGIN
  const placement = spaceAbove >= spaceBelow ? 'top' : 'bottom'
  // 数据集/知识库：相对整条工具栏居中（宽面板右对齐会左侧溢出）
  // 技能/MCP：相对按钮右对齐；模型/批准：相对按钮左对齐，溢出再右对齐
  let left: number
  if (panel === 'datasets' || panel === 'knowledge_bases') {
    left = barRect.left + (barRect.width - width) / 2
  } else if (panel === 'skills' || panel === 'mcp_tools') {
    left = triggerRect.right - width
  } else {
    left = triggerRect.left
    if (left + width > window.innerWidth - VIEWPORT_MARGIN) {
      left = triggerRect.right - width
    }
  }
  left = Math.max(
    VIEWPORT_MARGIN,
    Math.min(left, window.innerWidth - width - VIEWPORT_MARGIN),
  )
  panelRect.value = {
    left,
    width,
    top: barRect.bottom + PANEL_GAP,
    bottom: window.innerHeight - barRect.top + PANEL_GAP,
    maxHeight: Math.max(180, Math.floor(placement === 'top' ? spaceAbove : spaceBelow)),
    placement,
  }
}

const panelStyle = computed(() => {
  const rect = panelRect.value
  return {
    left: `${rect.left}px`,
    width: `${rect.width}px`,
    maxHeight: `${rect.maxHeight}px`,
    ...(rect.placement === 'top'
      ? { bottom: `${rect.bottom}px` }
      : { top: `${rect.top}px` }),
  }
})

const matchesOptionSearch = (item: TaskScopeItem, q: string) => {
  if (!q) return true
  return (
    item.name.toLowerCase().includes(q) ||
    String(item.description || '').toLowerCase().includes(q) ||
    String(item.server_name || '').toLowerCase().includes(q) ||
    String(item.server_remark || '').toLowerCase().includes(q) ||
    String(item.id || '').toLowerCase().includes(q)
  )
}

const isPersonalOption = (item: TaskScopeItem) =>
  String(item.scope || '').toLowerCase() === 'personal'

const currentOptions = computed(() => {
  if (
    activePanel.value !== 'datasets' &&
    activePanel.value !== 'knowledge_bases' &&
    activePanel.value !== 'skills' &&
    activePanel.value !== 'mcp_tools'
  ) {
    return []
  }
  const list = optionLists.value[activePanel.value] || []
  const q = optionSearch.value.trim().toLowerCase()
  return list.filter((item) => matchesOptionSearch(item, q))
})

const skillsForActiveScope = computed(() => {
  const q = optionSearch.value.trim().toLowerCase()
  return (optionLists.value.skills || []).filter((item) => {
    const personal = isPersonalOption(item)
    if (skillScopeTab.value === 'personal' ? !personal : personal) return false
    return matchesOptionSearch(item, q)
  })
})

const skillScopeSelectedCount = (scope: 'global' | 'personal') =>
  (props.resourceScope.skills || []).filter((item) =>
    scope === 'personal' ? isPersonalOption(item) : !isPersonalOption(item),
  ).length

const skillScopeTotalCount = (scope: 'global' | 'personal') =>
  (optionLists.value.skills || []).filter((item) =>
    scope === 'personal' ? isPersonalOption(item) : !isPersonalOption(item),
  ).length

const groupMcpOptions = (options: TaskScopeItem[]): McpOptionGroup[] => {
  const map = new Map<string, TaskScopeItem[]>()
  for (const option of options || []) {
    const key = String(option.server_name || '未命名服务').trim() || '未命名服务'
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(option)
  }
  return Array.from(map.entries())
    .map(([serverName, tools]) => ({
      serverName,
      serverRemark:
        String(tools.find((t) => t.server_remark)?.server_remark || '').trim() || undefined,
      tools: tools
        .slice()
        .sort((a, b) => String(a.name || a.id || '').localeCompare(String(b.name || b.id || ''), 'zh-CN')),
    }))
    .sort((a, b) => a.serverName.localeCompare(b.serverName, 'zh-CN'))
}

const mcpGroupsForActiveOptions = computed(() =>
  groupMcpOptions(
    (optionLists.value.mcp_tools || []).filter((item) =>
      matchesOptionSearch(item, optionSearch.value.trim().toLowerCase()),
    ),
  ),
)

const selectedIds = (group: 'datasets' | 'knowledge_bases' | 'skills' | 'mcp_tools') =>
  new Set((props.resourceScope[group] || []).map((item) => String(item.id || item.name)))

const mcpGroupSelectionState = (tools: TaskScopeItem[]): 'none' | 'partial' | 'all' => {
  if (!tools.length) return 'none'
  const ids = selectedIds('mcp_tools')
  const selected = tools.filter((tool) => ids.has(String(tool.id || tool.name)))
  if (!selected.length) return 'none'
  if (selected.length === tools.length) return 'all'
  return 'partial'
}

const toggleMcpGroupCollapsed = (serverName: string) => {
  const next = new Set(collapsedMcpGroups.value)
  if (next.has(serverName)) next.delete(serverName)
  else next.add(serverName)
  collapsedMcpGroups.value = next
}

const toggleMcpGroupSelectAll = (tools: TaskScopeItem[]) => {
  if (!tools.length) return
  const selectAll = mcpGroupSelectionState(tools) !== 'all'
  const ids = selectedIds('mcp_tools')
  let current = [...(props.resourceScope.mcp_tools || [])]
  if (selectAll) {
    for (const tool of tools) {
      const key = String(tool.id || tool.name)
      if (!ids.has(key)) current.push(tool)
    }
  } else {
    const remove = new Set(tools.map((tool) => String(tool.id || tool.name)))
    current = current.filter((item) => !remove.has(String(item.id || item.name)))
  }
  emit('update:resourceScope', {
    ...props.resourceScope,
    mcp_tools: current,
  })
}

const searchPlaceholder = computed(() => {
  if (optionsLoading.value) return '加载中…'
  if (activePanel.value === 'skills') {
    return skillScopeTab.value === 'personal' ? '搜索我的技能' : '搜索平台技能'
  }
  if (activePanel.value === 'mcp_tools') return '搜索 MCP 或服务名'
  return '搜索…'
})

const resourceEmptyHint = computed(() => {
  if (optionsLoading.value) return '加载中…'
  if (activePanel.value === 'skills') {
    return skillScopeTab.value === 'personal' ? '暂无个人技能' : '暂无平台技能'
  }
  if (activePanel.value === 'mcp_tools') return '暂无个人已发布 MCP'
  return '暂无可用项'
})

const PANEL_TITLES: Record<Exclude<PanelKey, null>, string> = {
  model: '选择模型',
  approval: '工具批准方式',
  datasets: '数据集',
  knowledge_bases: '知识库',
  skills: '技能',
  mcp_tools: 'MCP 工具',
}

const panelTitle = computed(() => (activePanel.value ? PANEL_TITLES[activePanel.value] : ''))

// 指针移开后延时关闭：给一点余量让指针跨过按钮栏与面板之间的间隙
const CLOSE_DELAY_MS = 400
let closeTimer: number | undefined

const cancelPendingClose = () => {
  if (closeTimer !== undefined) {
    window.clearTimeout(closeTimer)
    closeTimer = undefined
  }
}

const closePanel = () => {
  cancelPendingClose()
  activePanel.value = null
  showThinkingPanel.value = false
}

const scrollSelectedModelIntoView = () => {
  const list = modelListScrollRef.value
  if (!list) return
  const selected = list.querySelector('[data-model-current="true"]') as HTMLElement | null
  if (!selected) return
  const listRect = list.getBoundingClientRect()
  const itemRect = selected.getBoundingClientRect()
  list.scrollTop += itemRect.top - listRect.top - (list.clientHeight - itemRect.height) / 2
}

const scheduleClose = (event?: PointerEvent) => {
  // 触摸设备抬手即触发 pointerleave，会导致点开就自动收起，只对鼠标生效
  if (event && event.pointerType !== 'mouse') return
  cancelPendingClose()
  closeTimer = window.setTimeout(() => {
    closeTimer = undefined
    // 正在面板内搜索时不打断输入
    const focused = document.activeElement
    if (focused && panelRef.value?.contains(focused)) return
    activePanel.value = null
    showThinkingPanel.value = false
  }, CLOSE_DELAY_MS)
}

const togglePanel = (panel: PanelKey) => {
  cancelPendingClose()
  const next = activePanel.value === panel ? null : panel
  activePanel.value = next
  optionSearch.value = ''
  showThinkingPanel.value = false
  if (next === 'skills') skillScopeTab.value = 'global'
  if (next === 'mcp_tools') {
    // 打开时按当前列表默认全部折叠，对齐 EmbedChat 加号菜单
    collapsedMcpGroups.value = new Set(
      (optionLists.value.mcp_tools || []).map(
        (t) => String(t.server_name || '未命名服务').trim() || '未命名服务',
      ),
    )
  }
  if (next === 'model') nextTick(scrollSelectedModelIntoView)
}

const selectModel = (modelId: string) => {
  emit('update:model', modelId)
  emit('update:thinking-enable-override', null)
  emit('update:reasoning-effort-override', null)
  emit('update:temperature-override', null)
  showThinkingPanel.value = false
  if (!modelId) closePanel()
}

const selectModelOption = (model: AIModel) => {
  if (props.model !== model.model_id) {
    emit('update:model', model.model_id)
    emit('update:thinking-enable-override', null)
    emit('update:reasoning-effort-override', null)
    emit('update:temperature-override', null)
  }
  showThinkingPanel.value = false
  // 点模型行仅选中；参数设置通过右侧「思考」/「参数」按钮打开
}

const openThinkingSettings = (model: AIModel, event?: Event) => {
  event?.stopPropagation()
  event?.preventDefault()
  emit('update:model', model.model_id)
  if (props.model !== model.model_id) {
    emit('update:thinking-enable-override', null)
    emit('update:reasoning-effort-override', null)
    emit('update:temperature-override', null)
  }
  activePanel.value = 'model'
  showThinkingPanel.value = true
}

const toggleThinkingForTask = () => {
  if (!canToggleThinking.value) return
  const nextEnabled = !thinkingEnabledForTask.value
  emit('update:thinking-enable-override', nextEnabled)
  if (nextEnabled) return
  emit('update:reasoning-effort-override', null)
}

const selectReasoningEffort = (effort: ReasoningEffort | null) => {
  emit('update:reasoning-effort-override', effort)
}

const selectApproval = (mode: TaskApprovalMode) => {
  emit('update:approvalMode', mode)
  closePanel()
}

const toggleScopeItem = (
  group: 'datasets' | 'knowledge_bases' | 'skills' | 'mcp_tools',
  item: TaskScopeItem,
) => {
  const current = [...(props.resourceScope[group] || [])]
  const key = String(item.id || item.name)
  const index = current.findIndex((entry) => String(entry.id || entry.name) === key)
  if (index >= 0) current.splice(index, 1)
  else current.push(item)
  emit('update:resourceScope', {
    ...props.resourceScope,
    [group]: current,
  })
}

const removeChip = (chip: { key: string; group: keyof TaskResourceScope }) => {
  if (
    chip.group !== 'datasets' &&
    chip.group !== 'knowledge_bases' &&
    chip.group !== 'skills' &&
    chip.group !== 'mcp_tools'
  ) {
    return
  }
  const id = chip.key.split(':').slice(1).join(':')
  emit('update:resourceScope', {
    ...props.resourceScope,
    [chip.group]: (props.resourceScope[chip.group] || []).filter(
      (item) => String(item.id || item.name) !== id,
    ),
  })
}

const loadOptions = async () => {
  optionsLoading.value = true
  try {
    const [models, datasets, knowledge, globalSkills, personalSkills, mcpTools] = await Promise.allSettled([
      modelApi.list(),
      axios.get('/api/portal/metadata/datasets/accessible'),
      axios.get('/api/portal/ragflow/datasets', { params: { page: 1, page_size: 100, include_missing: false } }),
      axios.get('/api/portal/skills'),
      axios.get('/api/portal/skills/personal'),
      axios.get('/api/portal/tools/mcp'),
    ])
    if (models.status === 'fulfilled') {
      availableModels.value = (models.value.data || []).filter(
        (item: AIModel) => (item.type === 'llm' || item.type === 'multimodal') && item.is_active,
      )
    }
    if (datasets.status === 'fulfilled') {
      const raw = datasets.value.data
      const list = Array.isArray(raw) ? raw : raw?.data || raw?.datasets || []
      optionLists.value.datasets = list
        .filter((item: any) => item.status === undefined || item.status === 1 || item.status === '1' || item.status === 'active')
        .map((item: any) => ({
          id: String(item.id || item.name),
          name: item.display_name || item.name || item.dataset_name,
          dataset_name: item.name || item.dataset_name,
          description: item.description || '',
        }))
    }
    if (knowledge.status === 'fulfilled') {
      const data = knowledge.value.data?.data
      const list = Array.isArray(data) ? data : data?.datasets || data?.items || []
      optionLists.value.knowledge_bases = list
        .filter((item: any) => item.status === undefined || item.status === 'active' || item.status === 1 || item.status === '1')
        .map((item: any) => ({
          id: String(item.id || item.dataset_id),
          name: item.name || item.display_name || item.dataset_name,
          description: item.description || '',
        }))
    }
    const skills: TaskScopeItem[] = []
    for (const [result, scope] of [
      [globalSkills, 'global'],
      [personalSkills, 'personal'],
    ] as const) {
      if (result.status !== 'fulfilled') continue
      for (const item of result.value.data?.data || []) {
        if (item.enabled === false || item.enabled === 'false' || item.enabled === 0 || item.enabled === '0') continue
        skills.push({
          id: String(item.id),
          name: item.name,
          description: item.description || '',
          scope,
        })
      }
    }
    optionLists.value.skills = skills
    if (mcpTools.status === 'fulfilled') {
      const raw = mcpTools.value.data
      const list = Array.isArray(raw) ? raw : raw?.data || []
      optionLists.value.mcp_tools = list
        .map((item: any) => ({
          id: String(item.id || ''),
          name: String(item.name || ''),
          description: item.description || '',
          server_name: item.server_name || '',
          server_remark: item.server_remark || '',
          scope: item.scope || 'global',
        }))
        // 与 EmbedChat / 服务端收敛一致：任务动态挂载仅个人已发布 MCP
        .filter(
          (item: TaskScopeItem) =>
            item.id && item.name && String(item.scope || '').toLowerCase() === 'personal',
        )
    }
  } finally {
    optionsLoading.value = false
  }
}

// 只有按钮栏与面板本身算「内部」；点文本框 / 已选标签 / 说明文字都应收起面板
const onDocClick = (event: MouseEvent) => {
  const target = event.target as Node
  if (barRef.value?.contains(target) || panelRef.value?.contains(target)) return
  closePanel()
}

const onViewportChange = () => {
  if (activePanel.value) updatePanelRect()
}

const onEscape = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && activePanel.value) {
    event.stopPropagation()
    closePanel()
  }
}

watch(activePanel, async (panel) => {
  if (!panel) return
  await nextTick()
  updatePanelRect()
})

onMounted(() => {
  void loadOptions()
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onEscape, true)
  window.addEventListener('resize', onViewportChange)
  // capture=true：任务弹窗正文自身滚动时也要跟随
  window.addEventListener('scroll', onViewportChange, true)
})
onUnmounted(() => {
  cancelPendingClose()
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onEscape, true)
  window.removeEventListener('resize', onViewportChange)
  window.removeEventListener('scroll', onViewportChange, true)
})

watch(
  () => props.agentId,
  () => {
    // agent 切换不影响已选资源；仅保留钩子便于后续按智能体过滤技能
  },
)
</script>

<template>
  <div class="rounded-xl border border-gray-200 bg-white overflow-hidden">
    <textarea
      :value="prompt"
      rows="5"
      class="w-full resize-y border-0 px-3 pt-3 pb-2 text-sm outline-none focus:ring-0"
      placeholder="例如：帮我查一下华东一号机房昨天的 PUE 峰值..."
      @input="emit('update:prompt', ($event.target as HTMLTextAreaElement).value)"
    />

    <div v-if="selectedChips.length" class="flex flex-wrap gap-1.5 border-t border-gray-100 px-3 py-2">
      <button
        v-for="chip in selectedChips"
        :key="chip.key"
        type="button"
        class="inline-flex max-w-full items-center gap-1 rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
        :title="'移除 ' + chip.label"
        @click="removeChip(chip)"
      >
        <span class="truncate">{{ chip.label }}</span>
        <span class="text-blue-400">×</span>
      </button>
    </div>

    <div
      ref="barRef"
      class="relative flex flex-wrap items-center gap-1 border-t border-gray-100 bg-gray-50/70 px-2 py-1.5"
      @pointerenter="cancelPendingClose"
      @pointerleave="scheduleClose"
    >
      <button
        :ref="(el) => setTriggerRef('model', el)"
        type="button"
        class="inline-flex h-7 max-w-[15rem] items-center gap-1 rounded-full border px-2 text-[11px] font-semibold transition"
        :class="activePanel === 'model' ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'"
        :title="modelTriggerTooltip"
        @click.stop="togglePanel('model')"
      >
        <span class="truncate">{{ modelLabel }}</span>
        <span v-if="thinkingSummaryLabel" class="shrink-0 rounded-full bg-violet-50 px-1.5 py-0.5 text-[9px] font-semibold text-violet-600">
          {{ thinkingSummaryLabel }}
        </span>
        <span
          v-if="temperatureSummaryLabel"
          class="shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-semibold"
          :class="isTemperatureOverLimit ? 'bg-amber-100 text-amber-800' : 'bg-sky-50 text-sky-600'"
        >
          {{ temperatureSummaryLabel }}
        </span>
        <svg class="h-3 w-3 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
      </button>

      <button
        :ref="(el) => setTriggerRef('approval', el)"
        type="button"
        class="inline-flex h-7 items-center gap-1 rounded-full border px-2 text-[11px] font-semibold transition"
        :class="approvalMode === 'allow'
          ? 'border-blue-200 bg-blue-50 text-blue-700'
          : approvalMode === 'ask'
            ? 'border-amber-200 bg-amber-50 text-amber-700'
            : 'border-gray-200 bg-white text-gray-600'"
        @click.stop="togglePanel('approval')"
      >
        <svg class="h-3 w-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
        <span>{{ approvalLabel }}</span>
        <svg class="h-3 w-3 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
      </button>

      <button
        :ref="(el) => setTriggerRef('datasets', el)"
        type="button"
        class="inline-flex h-7 items-center gap-1 rounded-full border px-2 text-[11px] font-semibold transition"
        :class="activePanel === 'datasets' || resourceScope.datasets.length ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'"
        @click.stop="togglePanel('datasets')"
      >
        数据集{{ resourceScope.datasets.length ? ` ${resourceScope.datasets.length}` : '' }}
        <svg class="h-3 w-3 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
      </button>

      <button
        :ref="(el) => setTriggerRef('knowledge_bases', el)"
        type="button"
        class="inline-flex h-7 items-center gap-1 rounded-full border px-2 text-[11px] font-semibold transition"
        :class="activePanel === 'knowledge_bases' || resourceScope.knowledge_bases.length ? 'border-violet-200 bg-violet-50 text-violet-700' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'"
        @click.stop="togglePanel('knowledge_bases')"
      >
        知识库{{ resourceScope.knowledge_bases.length ? ` ${resourceScope.knowledge_bases.length}` : '' }}
        <svg class="h-3 w-3 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
      </button>

      <button
        :ref="(el) => setTriggerRef('skills', el)"
        type="button"
        class="inline-flex h-7 items-center gap-1 rounded-full border px-2 text-[11px] font-semibold transition"
        :class="activePanel === 'skills' || resourceScope.skills.length ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'"
        @click.stop="togglePanel('skills')"
      >
        技能{{ resourceScope.skills.length ? ` ${resourceScope.skills.length}` : '' }}
        <svg class="h-3 w-3 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
      </button>

      <button
        :ref="(el) => setTriggerRef('mcp_tools', el)"
        type="button"
        class="inline-flex h-7 items-center gap-1 rounded-full border px-2 text-[11px] font-semibold transition"
        :class="activePanel === 'mcp_tools' || resourceScope.mcp_tools.length ? 'border-indigo-200 bg-indigo-50 text-indigo-700' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'"
        @click.stop="togglePanel('mcp_tools')"
      >
        MCP{{ resourceScope.mcp_tools.length ? ` ${resourceScope.mcp_tools.length}` : '' }}
        <svg class="h-3 w-3 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
      </button>

    </div>

    <!-- 浮层挂到 body，避开弹窗正文与本组件根节点的 overflow 裁剪 -->
    <Teleport to="body">
      <div
        v-if="activePanel === 'model'"
        ref="panelRef"
        class="fixed z-[2000] flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl"
        :style="panelStyle"
        @click.stop
        @pointerenter="cancelPendingClose"
        @pointerleave="scheduleClose"
      >
        <div class="flex shrink-0 items-center justify-between border-b border-gray-100 px-2.5 py-1.5">
          <span class="text-[11px] font-bold text-gray-500">{{ panelTitle }}</span>
          <button type="button" class="rounded-md p-1 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600" title="关闭" @click="closePanel">
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="flex min-h-0 flex-1 flex-col sm:flex-row">
          <div ref="modelListScrollRef" class="min-h-0 min-w-0 flex-1 overflow-y-auto p-1">
            <button
              type="button"
              class="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-xs"
              :class="!model ? 'bg-primary/5 font-bold text-primary' : 'text-gray-700 hover:bg-gray-50'"
              :data-model-current="!model ? 'true' : undefined"
              @click="selectModel('')"
            >
              <span>使用智能体默认模型</span>
              <span v-if="!model">✓</span>
            </button>
            <button
              v-for="item in availableModels"
              :key="item.model_id"
              type="button"
              class="mt-0.5 flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs"
              :class="model === item.model_id ? 'bg-primary/5 font-bold text-primary' : 'text-gray-700 hover:bg-gray-50'"
              :data-model-current="model === item.model_id ? 'true' : undefined"
              @click="selectModelOption(item)"
            >
              <span class="min-w-0 flex-1">
                <span class="block truncate">{{ item.name || item.model_id }}</span>
                <span v-if="item.name && item.name !== item.model_id" class="mt-0.5 block truncate text-[10px] font-normal text-gray-400">
                  {{ item.model_id }}
                </span>
              </span>
              <span class="flex shrink-0 items-center gap-1">
                <span v-if="model === item.model_id">✓</span>
                <button
                  v-if="item.thinking_enable"
                  type="button"
                  class="inline-flex items-center gap-0.5 rounded-full bg-violet-50 px-1.5 py-0.5 text-[9px] font-medium text-violet-600 hover:bg-violet-100"
                  title="调整本次任务思考与参数"
                  @click="openThinkingSettings(item, $event)"
                >
                  <span>{{ model === item.model_id ? (thinkingSummaryLabel || '思考') : '思考' }}</span>
                  <svg class="h-3 w-3 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 5 7 7-7 7" /></svg>
                </button>
                <button
                  v-else
                  type="button"
                  class="inline-flex items-center gap-0.5 rounded-full bg-gray-100 px-1.5 py-0.5 text-[9px] font-medium text-gray-600 hover:bg-gray-200"
                  title="调整本次任务参数"
                  @click="openThinkingSettings(item, $event)"
                >
                  <span>参数</span>
                  <svg class="h-3 w-3 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 5 7 7-7 7" /></svg>
                </button>
              </span>
            </button>
          </div>

          <div
            v-if="showThinkingPanel && selectedModelConfig"
            class="w-full shrink-0 border-t border-gray-100 bg-gray-50/70 p-3 sm:w-[260px] sm:border-l sm:border-t-0 max-h-[380px] overflow-y-auto space-y-3"
          >
            <!-- 头部：思考开关 或 参数标题 -->
            <div v-if="selectedModelConfig.thinking_enable" class="flex items-center justify-between gap-2">
              <div class="min-w-0">
                <div class="text-xs font-bold text-gray-800">思考模式</div>
                <div class="mt-0.5 truncate text-[10px] text-gray-500">{{ thinkingPanelSubtitle }}</div>
              </div>
              <button
                v-if="canToggleThinking"
                type="button"
                class="relative inline-flex h-6 w-11 shrink-0 overflow-hidden rounded-full p-0 transition-colors"
                :class="thinkingEnabledForTask ? 'bg-primary' : 'bg-gray-300'"
                :aria-pressed="thinkingEnabledForTask"
                aria-label="切换本次任务思考模式"
                :title="thinkingEnabledForTask ? '关闭本次任务思考' : '开启本次任务思考'"
                @click="toggleThinkingForTask"
              >
                <span class="absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform" :class="thinkingEnabledForTask ? 'translate-x-5' : 'translate-x-0.5'"></span>
              </button>
              <span v-else class="rounded-full bg-violet-100 px-2 py-1 text-[10px] font-semibold text-violet-700">
                已开启
              </span>
            </div>
            <div v-else class="flex items-center justify-between gap-2">
              <div class="min-w-0">
                <div class="text-xs font-bold text-gray-800">模型参数设置</div>
                <div class="mt-0.5 truncate text-[10px] text-gray-500">{{ thinkingPanelSubtitle }}</div>
              </div>
            </div>

            <!-- 思考设置详情 -->
            <div v-if="selectedModelConfig.thinking_enable">
              <div v-if="thinkingEnabledForTask">
                <div class="mb-2 rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[10px] text-gray-500">
                  开启思考可能增加响应耗时，适合复杂推理任务。
                </div>
                <div class="mb-1 text-[10px] font-semibold text-gray-500">思考强度</div>
                <div class="max-h-[140px] overflow-y-auto rounded-lg border border-gray-200 bg-white p-1">
                  <button
                    type="button"
                    class="w-full rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-gray-50"
                    :class="isFollowingModelEffort ? 'bg-primary/5 font-semibold text-primary' : 'text-gray-700'"
                    title="不覆盖模型注册配置"
                    @click="selectReasoningEffort(null)"
                  >
                    <span class="flex items-center justify-between gap-2">
                      <span>跟随模型默认</span>
                      <span v-if="isFollowingModelEffort">✓</span>
                    </span>
                  </button>
                  <button
                    v-for="option in supportedReasoningEfforts"
                    :key="option.value"
                    type="button"
                    class="w-full rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-gray-50"
                    :class="!isFollowingModelEffort && selectedReasoningEffort === option.value ? 'bg-primary/5 font-semibold text-primary' : 'text-gray-700'"
                    :title="option.description"
                    @click="selectReasoningEffort(option.value)"
                  >
                    <span class="flex items-center justify-between gap-2">
                      <span>{{ option.label }}</span>
                      <span v-if="!isFollowingModelEffort && selectedReasoningEffort === option.value">✓</span>
                    </span>
                  </button>
                  <div v-if="supportedReasoningEfforts.length === 0" class="px-2 py-1.5 text-[10px] text-gray-400">模型未配置可选思考强度</div>
                </div>
              </div>
              <div v-else-if="canToggleThinking" class="rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[10px] text-gray-500">
                关闭思考后，本次任务将以非思考模式执行。
              </div>
            </div>

            <!-- 采样温度卡片 (所有模型均展示) -->
            <div class="rounded-xl border border-gray-200 bg-white p-2.5 shadow-sm">
              <div class="flex items-center justify-between">
                <span class="text-[11px] font-semibold text-gray-700">采样温度 (Temperature)</span>
                <span
                  class="font-mono text-xs font-bold transition-colors"
                  :class="isTemperatureOverLimit ? 'text-amber-600' : hasTemperatureOverride ? 'text-primary' : 'text-gray-600'"
                >
                  {{ effectiveTemperature.toFixed(2) }}
                </span>
              </div>

              <!-- 滑块 -->
              <div class="mt-2.5">
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.05"
                  :value="effectiveTemperature"
                  class="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-gray-200 accent-primary"
                  @input="handleTemperatureSliderChange"
                />
                <div class="mt-1 flex justify-between text-[9px] text-gray-400 font-mono">
                  <span>0.0</span>
                  <span>1.0</span>
                  <span>2.0</span>
                </div>
              </div>

              <!-- 预设场景胶囊 -->
              <div class="mt-2 flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  class="rounded border px-1.5 py-0.5 text-[10px] transition-colors"
                  :class="effectiveTemperature === 0.2 ? 'border-primary/40 bg-primary/10 font-semibold text-primary' : 'border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100'"
                  @click="setTemperaturePreset(0.2)"
                >
                  严谨 0.2
                </button>
                <button
                  type="button"
                  class="rounded border px-1.5 py-0.5 text-[10px] transition-colors"
                  :class="effectiveTemperature === 0.7 ? 'border-primary/40 bg-primary/10 font-semibold text-primary' : 'border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100'"
                  @click="setTemperaturePreset(0.7)"
                >
                  均衡 0.7
                </button>
                <button
                  type="button"
                  class="rounded border px-1.5 py-0.5 text-[10px] transition-colors"
                  :class="effectiveTemperature === 1.0 ? 'border-primary/40 bg-primary/10 font-semibold text-primary' : 'border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100'"
                  @click="setTemperaturePreset(1.0)"
                >
                  发散 1.0
                </button>
                <button
                  v-if="hasTemperatureOverride"
                  type="button"
                  class="ml-auto text-[10px] text-gray-400 hover:text-primary transition-colors underline"
                  title="清除本次温度覆盖，跟随模型默认值"
                  @click="resetTemperatureOverride"
                >
                  跟随默认
                </button>
              </div>

              <!-- 场景指引说明 -->
              <p class="mt-2 text-[10px] leading-tight text-gray-400">
                {{ getTemperatureGuidance(effectiveTemperature) }}
              </p>

              <!-- 大于 1.0 醒目警示卡片 -->
              <div
                v-if="isTemperatureOverLimit"
                class="mt-2 rounded-lg border border-amber-200 bg-amber-50/80 p-2 text-[10px] leading-relaxed text-amber-700"
              >
                <div class="flex items-start gap-1 font-semibold">
                  <span>⚠️</span>
                  <span>当前温度大于 1.0。部分模型仅支持 0.0～1.0 范围，超出范围可能被服务商忽略或引发调用异常，请确认模型官方文档支持。</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div
        v-else-if="activePanel === 'approval'"
        ref="panelRef"
        class="fixed z-[2000] flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl"
        :style="panelStyle"
        @click.stop
        @pointerenter="cancelPendingClose"
        @pointerleave="scheduleClose"
      >
        <div class="flex shrink-0 items-center justify-between border-b border-gray-100 px-2.5 py-1.5">
          <span class="text-[11px] font-bold text-gray-500">{{ panelTitle }}</span>
          <button type="button" class="rounded-md p-1 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600" title="关闭" @click="closePanel">
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="min-h-0 flex-1 overflow-y-auto p-1">
          <button
            v-for="option in APPROVAL_OPTIONS"
            :key="option.value"
            type="button"
            class="w-full rounded-lg px-2.5 py-2 text-left"
            :class="approvalMode === option.value ? 'bg-primary/5' : 'hover:bg-gray-50'"
            @click="selectApproval(option.value)"
          >
            <div class="flex items-center justify-between text-xs font-bold" :class="approvalMode === option.value ? 'text-primary' : 'text-gray-800'">
              <span>{{ option.label }}</span>
              <span v-if="approvalMode === option.value">✓</span>
            </div>
            <p class="mt-0.5 text-[11px] leading-snug text-gray-500">{{ option.description }}</p>
          </button>
        </div>
      </div>

      <div
        v-else-if="activePanel"
        ref="panelRef"
        class="fixed z-[2000] flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl"
        :style="panelStyle"
        @click.stop
        @pointerenter="cancelPendingClose"
        @pointerleave="scheduleClose"
      >
        <div class="flex shrink-0 items-center justify-between border-b border-gray-100 px-2.5 py-1.5">
          <span class="text-[11px] font-bold text-gray-500">{{ panelTitle }}</span>
          <button type="button" class="rounded-md p-1 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600" title="关闭" @click="closePanel">
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- 技能：平台 / 个人 Tab，对齐 EmbedChat -->
        <div
          v-if="activePanel === 'skills'"
          class="mx-2 mt-2 flex shrink-0 items-center gap-1 rounded-lg bg-gray-50 p-0.5"
        >
          <button
            type="button"
            class="flex-1 rounded-md py-1.5 text-center text-xs font-semibold transition-colors"
            :class="skillScopeTab === 'global'
              ? 'bg-white text-gray-900 shadow-sm'
              : 'text-gray-500 hover:text-gray-700'"
            @click="skillScopeTab = 'global'"
          >
            平台
            <span class="ml-0.5 text-[10px] font-normal text-gray-400">
              ({{ skillScopeSelectedCount('global') }}/{{ skillScopeTotalCount('global') }})
            </span>
          </button>
          <button
            type="button"
            class="flex-1 rounded-md py-1.5 text-center text-xs font-semibold transition-colors"
            :class="skillScopeTab === 'personal'
              ? 'bg-white text-emerald-700 shadow-sm'
              : 'text-gray-500 hover:text-gray-700'"
            @click="skillScopeTab = 'personal'"
          >
            我的
            <span class="ml-0.5 text-[10px] font-normal text-gray-400">
              ({{ skillScopeSelectedCount('personal') }}/{{ skillScopeTotalCount('personal') }})
            </span>
          </button>
        </div>

        <div class="shrink-0 border-b border-gray-100 p-2">
          <input
            v-model="optionSearch"
            type="search"
            class="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-xs outline-none focus:border-primary"
            :placeholder="searchPlaceholder"
          />
        </div>

        <!-- MCP：按服务分组 + 默认折叠 + 组内全选 -->
        <div
          v-if="activePanel === 'mcp_tools'"
          class="min-h-0 flex-1 space-y-2 overflow-y-auto p-2"
        >
          <p v-if="!mcpGroupsForActiveOptions.length" class="px-2 py-6 text-center text-xs text-gray-400">
            {{ resourceEmptyHint }}
          </p>
          <template v-else>
            <p class="px-0.5 text-[10px] leading-relaxed text-gray-400">
              仅可挂载个人已发布 MCP；平台公共 MCP 请由智能体版本配置。
            </p>
            <div
              v-for="mcpGroup in mcpGroupsForActiveOptions"
              :key="mcpGroup.serverName"
              class="overflow-hidden rounded-lg border border-gray-100"
            >
              <div class="flex items-center gap-2 border-b border-gray-100 bg-gray-50/90 px-2 py-1.5">
                <label class="inline-flex shrink-0 cursor-pointer items-center justify-center" @click.stop>
                  <input
                    type="checkbox"
                    class="h-3.5 w-3.5 cursor-pointer rounded border-gray-300 text-primary focus:ring-primary/40"
                    :checked="mcpGroupSelectionState(mcpGroup.tools) === 'all'"
                    :indeterminate="mcpGroupSelectionState(mcpGroup.tools) === 'partial'"
                    @change.stop="toggleMcpGroupSelectAll(mcpGroup.tools)"
                  />
                </label>
                <button
                  type="button"
                  class="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  @click="toggleMcpGroupCollapsed(mcpGroup.serverName)"
                >
                  <svg
                    class="h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform"
                    :class="collapsedMcpGroups.has(mcpGroup.serverName) ? '-rotate-90' : ''"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                  </svg>
                  <span class="min-w-0 flex-1">
                    <span
                      class="block truncate text-xs font-semibold text-gray-800"
                      :title="mcpGroup.serverRemark ? `${mcpGroup.serverName} · ${mcpGroup.serverRemark}` : mcpGroup.serverName"
                    >{{ mcpGroup.serverName }}</span>
                    <span
                      v-if="mcpGroup.serverRemark"
                      class="mt-0.5 block truncate text-[10px] leading-snug text-gray-400"
                    >{{ mcpGroup.serverRemark }}</span>
                  </span>
                  <span class="shrink-0 text-[10px] font-medium text-gray-400">({{ mcpGroup.tools.length }})</span>
                </button>
                <button
                  type="button"
                  class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold text-primary/90 hover:bg-primary/5 hover:text-primary"
                  @click.stop="toggleMcpGroupSelectAll(mcpGroup.tools)"
                >
                  {{ mcpGroupSelectionState(mcpGroup.tools) === 'all' ? '取消全选' : '全选' }}
                </button>
              </div>
              <div v-show="!collapsedMcpGroups.has(mcpGroup.serverName)" class="py-0.5">
                <button
                  v-for="item in mcpGroup.tools"
                  :key="item.id"
                  type="button"
                  class="flex w-full items-start gap-2 px-2 py-1.5 text-left hover:bg-gray-50"
                  @click="toggleScopeItem('mcp_tools', item)"
                >
                  <span
                    class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px]"
                    :class="selectedIds('mcp_tools').has(String(item.id || item.name)) ? 'border-primary bg-primary text-white' : 'border-gray-300 text-transparent'"
                  >✓</span>
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-xs font-semibold text-gray-800">
                      {{ mcpToolDisplayName(item.name || item.id, mcpGroup.serverName) }}
                    </span>
                    <span v-if="item.description" class="mt-0.5 block truncate text-[10px] text-gray-400">
                      {{ item.description }}
                    </span>
                  </span>
                </button>
              </div>
            </div>
          </template>
        </div>

        <!-- 技能：按平台 / 个人过滤 -->
        <div
          v-else-if="activePanel === 'skills'"
          class="min-h-0 flex-1 overflow-y-auto p-1"
        >
          <p v-if="!skillsForActiveScope.length" class="space-y-1.5 px-2 py-6 text-center text-xs text-gray-400">
            <span class="block">{{ optionSearch.trim() ? '无匹配结果，试试清空搜索' : resourceEmptyHint }}</span>
            <span
              v-if="skillScopeTab === 'personal' && !optionSearch.trim()"
              class="block text-[11px] leading-relaxed"
            >
              可前往
              <a
                class="font-semibold text-emerald-600 hover:underline"
                href="/dashboard/personal?tab=skills"
                target="_blank"
                rel="noopener noreferrer"
              >个人中心 · 我的技能</a>
              新建 / 导入
            </span>
          </p>
          <button
            v-for="item in skillsForActiveScope"
            :key="`${item.scope || 'global'}:${item.id}`"
            type="button"
            class="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-gray-50"
            @click="toggleScopeItem('skills', item)"
          >
            <span
              class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px]"
              :class="selectedIds('skills').has(String(item.id || item.name)) ? 'border-primary bg-primary text-white' : 'border-gray-300 text-transparent'"
            >✓</span>
            <span class="min-w-0 flex-1">
              <span class="flex min-w-0 items-center gap-1.5">
                <span class="block truncate text-xs font-semibold text-gray-800">{{ item.name }}</span>
                <span
                  v-if="isPersonalOption(item)"
                  class="shrink-0 rounded bg-emerald-100 px-1 py-px text-[8px] font-semibold text-emerald-700"
                >个人</span>
              </span>
              <span v-if="item.description" class="mt-0.5 block truncate text-[10px] text-gray-400">
                {{ item.description }}
              </span>
            </span>
          </button>
        </div>

        <!-- 数据集 / 知识库：扁平列表 -->
        <div v-else class="min-h-0 flex-1 overflow-y-auto p-1">
          <p v-if="!currentOptions.length" class="px-2 py-6 text-center text-xs text-gray-400">
            {{ resourceEmptyHint }}
          </p>
          <button
            v-for="item in currentOptions"
            :key="item.id"
            type="button"
            class="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-gray-50"
            @click="toggleScopeItem(activePanel, item)"
          >
            <span
              class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px]"
              :class="selectedIds(activePanel).has(String(item.id || item.name)) ? 'border-primary bg-primary text-white' : 'border-gray-300 text-transparent'"
            >✓</span>
            <span class="min-w-0 flex-1">
              <span class="block truncate text-xs font-semibold text-gray-800">{{ item.name }}</span>
              <span v-if="item.description" class="mt-0.5 block truncate text-[10px] text-gray-400">
                {{ item.description }}
              </span>
            </span>
          </button>
        </div>
      </div>
    </Teleport>

    <p v-if="showAskWarning" class="border-t border-amber-100 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-700">
      已选择「请求批准」。定时任务执行时无法弹窗确认，工具调用可能停在待确认并导致任务失败；无人值守场景建议改回「自动批准」。
    </p>
    <p v-else class="border-t border-gray-100 px-3 py-1.5 text-[10px] text-gray-400">
      可限定本任务仅访问所选数据集 / 知识库 / 技能 / MCP，避免扩散到全部资源。默认自动批准适合定时执行。
    </p>
  </div>
</template>
