<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { metadataApi } from '../../api/metadata'
import type { MetadataAiProgress, MetadataAiRunStatus, Metric } from '../../api/metadata'
import { useToast } from '../../composables/useToast'
import TraceLogViewer from '../TraceLogViewer.vue'
import { LightBulbIcon, PencilIcon, SparklesIcon } from '@heroicons/vue/24/outline'

interface TableItem {
  id?: number
  physical_name: string
  term?: string
  description?: string
  status?: number
}

const props = defineProps<{
  show: boolean
  datasetId: number
  tables?: TableItem[]
  initialSelectedTables?: string[]
}>()

const emit = defineEmits(['close', 'saved'])

const analyzing = ref(false)
const saving = ref(false)
const loadingTables = ref(false)
const recommendations = ref<Metric[]>([])
const selectedIndices = ref<number[]>([])
const selectedRecommendationIndex = ref<number | null>(null)
const currentTraceId = ref('')
const showLogs = ref(false)
const runStatus = ref<MetadataAiRunStatus>('idle')
const progress = ref<MetadataAiProgress>({
  status: 'idle',
  phase: 'idle',
  message: '等待开始',
  percent: 0,
})

const progressPercent = computed(() => Math.max(0, Math.min(100, Math.round(progress.value.percent || 0))))
const estimatedRemainingText = computed(() => {
  const seconds = progress.value.estimated_remaining_seconds
  if (seconds === undefined || seconds === null) return '计算中'
  if (seconds < 60) return `约 ${seconds} 秒`
  return `约 ${Math.ceil(seconds / 60)} 分钟`
})

const selectedRecommendation = computed(() => {
  if (selectedRecommendationIndex.value === null) return null
  return recommendations.value[selectedRecommendationIndex.value] || null
})

const openRecommendationDetail = (index: number) => {
  selectedRecommendationIndex.value = index
}

const closeRecommendationDetail = () => {
  selectedRecommendationIndex.value = null
}

// Custom prompt and table selection state
const internalTables = ref<TableItem[]>([])
const selectedTableNames = ref<string[]>([])
const userPrompt = ref('')
const tableSearchQuery = ref('')
const showHelpModal = ref(false)
const isTablesCollapsed = ref(false)

// Timer and Request Abort Controller for analyzing state
const elapsedSeconds = ref(0)
let timerInterval: any = null
let abortController: AbortController | null = null

const formattedElapsedTime = computed(() => {
  const mins = Math.floor(elapsedSeconds.value / 60)
  const secs = elapsedSeconds.value % 60
  if (mins > 0) {
    return `${mins}分${secs < 10 ? '0' : ''}${secs}秒`
  }
  return `${secs}秒`
})

const startTimer = () => {
  elapsedSeconds.value = 0
  if (timerInterval) clearInterval(timerInterval)
  timerInterval = setInterval(() => {
    elapsedSeconds.value++
  }, 1000)
}

const stopTimer = () => {
  if (timerInterval) {
    clearInterval(timerInterval)
    timerInterval = null
  }
}

// SSE 回调和取消按钮都会异步更新运行状态，通过函数读取可避免类型收窄丢失异步分支。
const hasRunInterrupted = () => runStatus.value === 'interrupted'

const handleCancelRecommend = () => {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  stopTimer()
  analyzing.value = false
  runStatus.value = 'interrupted'
  progress.value = {
    ...progress.value,
    status: 'interrupted',
    phase: 'interrupted',
    message: '用户已取消，后端生成任务正在中断',
  }
  console.warn('指标推荐已由用户中断', {
    datasetId: props.datasetId,
    traceId: currentTraceId.value,
    progress: progress.value,
  })
  showToast('已取消指标生成', 'info')
}

const { showToast } = useToast()

// Built-in Generic Prompt Examples (适用任何业务表)
const promptExamples = [
  {
    title: '📈 业务总量与趋势分析',
    badge: '总量/趋势/均值',
    description: '适用于统计业务发生频次、周期性增长趋势（日/月/年）与累计均值。',
    prompt: '重点关注核心业务量的统计，生成按日/按月的趋势分析、累计总量及平均值指标。'
  },
  {
    title: '🍰 维度分布与占比统计',
    badge: '分类/状态/占比',
    description: '适用于分析数据在不同类型、状态、部门或地域维度的结构分布与占比。',
    prompt: '按数据类型、状态及分类维度进行分组统计，生成各维度的数量分布与百分比占比指标。'
  },
  {
    title: '🎯 转化率与完成质量分析',
    badge: '转化/成功率/耗时',
    description: '适用于流转流程、任务执行或用户转化链路的效率、成功率与比率计算。',
    prompt: '重点计算业务转化率、任务成功/完成率、处理耗时均值及各阶段漏斗流失比率。'
  },
  {
    title: '⚠️ 异常监控与风险预警',
    badge: '异常/失败率/超限',
    description: '适用于挖掘系统报错、流程超时、失败重试及越界预警指标。',
    prompt: '挖掘失败率、超时频次、高频错误类型分布及超出安全阈值的监控预警指标。'
  },
  {
    title: '🏆 TOP 排名与极值分布',
    badge: '排名/极值/长尾',
    description: '适用于寻找头部实体、最大/最小值峰值与极端分布。',
    prompt: '生成头部实体贡献排行 (TOP 10)、历史最高/最低峰值极值及集中度分布指标。'
  }
]

const initSelection = () => {
  if (props.initialSelectedTables && props.initialSelectedTables.length > 0) {
    selectedTableNames.value = [...props.initialSelectedTables]
  } else if (internalTables.value.length > 0) {
    selectedTableNames.value = internalTables.value.map(t => t.physical_name)
  }
}

// Fetch tables if not provided in props
const loadTablesIfNeeded = async () => {
  if (props.tables && props.tables.length > 0) {
    internalTables.value = props.tables.filter(t => t.status === undefined || t.status === 1)
    initSelection()
    return
  }

  if (!props.datasetId) return
  loadingTables.value = true
  try {
    const res = await metadataApi.getDataset(props.datasetId)
    const ds = res.data
    const tbls: TableItem[] = (ds.tables || []).filter((t: any) => t.status === undefined || t.status === 1)
    internalTables.value = tbls
    initSelection()
  } catch (err) {
    console.error('Failed to load dataset tables', err)
  } finally {
    loadingTables.value = false
  }
}

watch(
  () => props.show,
  (newVal) => {
    if (newVal) {
      loadTablesIfNeeded()
    } else {
      stopTimer()
      if (abortController) {
        abortController.abort()
        abortController = null
      }
    }
  },
  { immediate: true }
)

watch(
  () => props.initialSelectedTables,
  (newVal) => {
    if (newVal && newVal.length > 0) {
      selectedTableNames.value = [...newVal]
    }
  },
  { deep: true }
)

watch(
  () => props.tables,
  (newTables) => {
    if (newTables && newTables.length > 0) {
      internalTables.value = newTables.filter(t => t.status === undefined || t.status === 1)
      if (selectedTableNames.value.length === 0) {
        selectedTableNames.value = internalTables.value.map(t => t.physical_name)
      }
    }
  },
  { deep: true }
)

const filteredTables = computed(() => {
  const q = tableSearchQuery.value.trim().toLowerCase()
  if (!q) return internalTables.value
  return internalTables.value.filter(t => 
    t.physical_name.toLowerCase().includes(q) ||
    (t.term && t.term.toLowerCase().includes(q)) ||
    (t.description && t.description.toLowerCase().includes(q))
  )
})

const isAllTablesSelected = computed(() => {
  if (internalTables.value.length === 0) return false
  return internalTables.value.every(t => selectedTableNames.value.includes(t.physical_name))
})

const toggleTable = (tableName: string) => {
  const idx = selectedTableNames.value.indexOf(tableName)
  if (idx > -1) {
    selectedTableNames.value.splice(idx, 1)
  } else {
    selectedTableNames.value.push(tableName)
  }
}

const toggleSelectAllTables = () => {
  if (isAllTablesSelected.value) {
    selectedTableNames.value = []
  } else {
    selectedTableNames.value = internalTables.value.map(t => t.physical_name)
  }
}

const applyPromptExample = (examplePrompt: string) => {
  userPrompt.value = examplePrompt
  showHelpModal.value = false
  showToast('已填入提示词示例', 'info')
}

const handleRecommend = async () => {
  if (selectedTableNames.value.length === 0 && internalTables.value.length > 0) {
    showToast('请至少选择一张数据表进行分析', 'warning')
    return
  }

  analyzing.value = true
  recommendations.value = []
  selectedIndices.value = []
  selectedRecommendationIndex.value = null
  currentTraceId.value = ''
  runStatus.value = 'running'
  progress.value = {
    status: 'running',
    phase: 'connecting',
    message: '正在连接实时进度服务',
    percent: 0,
    completed_units: 0,
    total_units: 5,
    remaining_units: 5,
    unit_label: '阶段',
    result_count: 0,
  }
  startTimer()
  abortController = new AbortController()
  
  try {
    const data = await metadataApi.recommendMetricsStream(
      props.datasetId,
      {
        table_names: selectedTableNames.value.length > 0 ? selectedTableNames.value : undefined,
        user_prompt: userPrompt.value.trim() ? userPrompt.value.trim() : undefined,
      },
      abortController.signal,
      (streamEvent) => {
        const eventStatus: MetadataAiRunStatus = streamEvent.event === 'completed'
          ? 'completed'
          : streamEvent.event === 'interrupted'
            ? 'interrupted'
            : 'running'
        progress.value = {
          ...progress.value,
          ...streamEvent.data,
          status: eventStatus,
        }
        runStatus.value = eventStatus
        if (streamEvent.data.trace_id) currentTraceId.value = streamEvent.data.trace_id
      },
    )

    recommendations.value = (data.metrics || []).map((m: any) => ({
      ...m,
      calculation_logic: m.calculation || m.calculation_logic || ''
    }))
    currentTraceId.value = data._trace_id || ''
    runStatus.value = 'completed'
    progress.value = {
      ...progress.value,
      status: 'completed',
      phase: 'completed',
      message: `指标推荐完成，共生成 ${recommendations.value.length} 个指标`,
      percent: 100,
      remaining_units: 0,
      result_count: recommendations.value.length,
    }
    
    if (recommendations.value.length === 0) {
      showToast('未发现推荐指标，请检查所选表是否包含有效字段或调整需求', 'info')
    } else {
      showToast(`AI 成功推荐了 ${recommendations.value.length} 个指标 (耗时 ${formattedElapsedTime.value})`, 'success')
      // Default select all
      selectedIndices.value = recommendations.value.map((_, i) => i)
    }
  } catch (e: any) {
    if (e.name === 'AbortError' || e.name === 'CanceledError' || e.code === 'ERR_CANCELED' || e.message === 'canceled') {
      if (!hasRunInterrupted()) {
        runStatus.value = 'interrupted'
        progress.value = {
          ...progress.value,
          status: 'interrupted',
          phase: 'interrupted',
          message: '指标推荐连接已中断',
        }
      }
      return
    }
    console.error('指标推荐 SSE 请求失败', {
      datasetId: props.datasetId,
      traceId: currentTraceId.value,
      progress: progress.value,
      message: e.message,
    }, e)
    const detail = e.response?.data?.detail || e.message || ''
    const match = detail.match(/Trace ID: ([a-zA-Z0-9-]+)/)
    if (match) currentTraceId.value = match[1]
    runStatus.value = 'error'
    progress.value = {
      ...progress.value,
      status: 'error',
      phase: 'error',
      message: detail || '指标推荐失败',
    }
    showToast(detail || '推荐失败，请重试', 'error')
  } finally {
    stopTimer()
    analyzing.value = false
    abortController = null
  }
}

const toggleSelection = (index: number) => {
  const i = selectedIndices.value.indexOf(index)
  if (i > -1) {
    selectedIndices.value.splice(i, 1)
  } else {
    selectedIndices.value.push(index)
  }
}

const handleSave = async () => {
  if (selectedIndices.value.length === 0) return
  
  saving.value = true
  let successCount = 0
  
  try {
    for (const idx of selectedIndices.value) {
      const metric = recommendations.value[idx]
      if (metric) {
        await metadataApi.createMetric(props.datasetId, metric)
        successCount++
      }
    }
    
    showToast(`成功保存 ${successCount} 个指标`, 'success')
    emit('saved')
    // Reset state before closing
    recommendations.value = []
    selectedIndices.value = []
    emit('close')
  } catch (e: any) {
    console.error('Save failed', e)
    showToast(e.response?.data?.detail || '部分指标保存失败', 'error')
  } finally {
    saving.value = false
  }
}

const handleBackToConfig = () => {
  recommendations.value = []
  selectedIndices.value = []
  selectedRecommendationIndex.value = null
}

const handleClose = () => {
  if (analyzing.value || saving.value) return
  recommendations.value = []
  selectedIndices.value = []
  selectedRecommendationIndex.value = null
  showHelpModal.value = false
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="handleClose">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[88vh] flex flex-col overflow-hidden animate-fade-in-up border border-gray-100">
      
        <!-- Header -->
        <div class="px-8 py-5 border-b border-gray-100 flex justify-between items-center bg-gradient-to-r from-indigo-50/70 via-blue-50/40 to-white">
          <div class="flex items-center gap-4">
            <div class="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center text-white shadow-lg shadow-indigo-200">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
            </div>
            <div>
              <div class="flex items-center gap-2">
                <h2 class="text-lg font-bold text-gray-900 flex items-center gap-1.5"><SparklesIcon class="w-5 h-5 text-indigo-600" /> 智能指标发现</h2>
                <span class="px-2 py-0.5 bg-indigo-100 text-indigo-700 text-[10px] font-semibold rounded-full">AI 自动生成</span>
              </div>
              <p class="text-xs text-gray-500 mt-0.5">深度分析数据 Schema、字段语义与聚合潜力，自动推荐高价值业务指标与 SQL</p>
            </div>
          </div>
          <button @click="handleClose" class="text-gray-400 hover:text-gray-600 transition-colors p-2 hover:bg-white rounded-full">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Main Content -->
        <div class="flex-1 overflow-y-auto p-6 md:p-8 bg-gray-50/40">
          <!-- Initial / Config State -->
          <div v-if="recommendations.length === 0 && !analyzing" class="space-y-6">
            <div
              v-if="runStatus === 'interrupted' || runStatus === 'error'"
              class="flex items-start gap-3 rounded-lg border px-4 py-3 text-left"
              :class="runStatus === 'interrupted' ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-red-200 bg-red-50 text-red-800'"
            >
              <span class="mt-0.5 h-2 w-2 flex-shrink-0 rounded-full" :class="runStatus === 'interrupted' ? 'bg-amber-500' : 'bg-red-500'"></span>
              <div class="min-w-0">
                <div class="text-xs font-bold">{{ runStatus === 'interrupted' ? '上次生成已中断' : '上次生成失败' }}</div>
                <div class="mt-0.5 break-words text-[11px]">{{ progress.message }}</div>
              </div>
            </div>
          
            <!-- 1. Table Selection Section (Collapsible) -->
            <div class="bg-white rounded-xl border border-gray-200/80 p-5 shadow-sm transition-all">
              <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3" :class="isTablesCollapsed ? 'mb-0' : 'mb-4'">
                <div 
                  @click="isTablesCollapsed = !isTablesCollapsed"
                  class="flex items-center gap-2 cursor-pointer select-none group"
                >
                  <span class="w-2 h-2 rounded-full bg-indigo-600"></span>
                  <h3 class="text-sm font-bold text-gray-900 group-hover:text-indigo-600 transition-colors flex items-center gap-1.5">
                    分析数据表范围
                  </h3>
                  <span class="text-xs px-2 py-0.5 bg-gray-100 text-gray-600 rounded-full font-medium">
                    已选 {{ selectedTableNames.length }} / {{ internalTables.length }} 张表
                  </span>
                </div>

                <div class="flex items-center gap-3">
                  <input
                    v-if="!isTablesCollapsed && internalTables.length > 5"
                    v-model="tableSearchQuery"
                    type="text"
                    placeholder="搜索表名/术语..."
                    class="px-2.5 py-1 text-xs border border-gray-200 rounded-lg focus:outline-none focus:border-indigo-500 w-36 sm:w-44"
                  />
                  <button
                    @click="toggleSelectAllTables"
                    class="text-xs font-semibold text-indigo-600 hover:text-indigo-800 transition-colors hover:underline"
                  >
                    {{ isAllTablesSelected ? '取消全选' : '全选所有表' }}
                  </button>
                  <button
                    @click="isTablesCollapsed = !isTablesCollapsed"
                    type="button"
                    class="flex items-center gap-1 text-xs text-gray-500 hover:text-indigo-600 px-2 py-1 rounded-lg hover:bg-gray-100 transition-colors"
                  >
                    <span>{{ isTablesCollapsed ? '展开' : '收起' }}</span>
                    <svg 
                      class="w-3.5 h-3.5 transform transition-transform duration-200" 
                      :class="isTablesCollapsed ? '-rotate-90' : 'rotate-0'"
                      fill="none" stroke="currentColor" viewBox="0 0 24 24"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                    </svg>
                  </button>
                </div>
              </div>

              <!-- Collapsed Summary Chips -->
              <div 
                v-if="isTablesCollapsed" 
                class="mt-3 pt-3 border-t border-gray-100 flex flex-wrap items-center gap-1.5"
              >
                <span class="text-[11px] text-gray-400">已选表预览:</span>
                <template v-if="selectedTableNames.length === 0">
                  <span class="text-[11px] text-amber-600 bg-amber-50 px-2 py-0.5 rounded">未选择任何表</span>
                </template>
                <template v-else>
                  <span
                    v-for="name in selectedTableNames.slice(0, 5)"
                    :key="name"
                    class="text-[10px] bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded font-mono border border-indigo-100/60"
                  >
                    {{ name }}
                  </span>
                  <span 
                    v-if="selectedTableNames.length > 5" 
                    @click="isTablesCollapsed = false"
                    class="text-[10px] text-gray-400 hover:text-indigo-600 cursor-pointer font-medium hover:underline"
                  >
                    +{{ selectedTableNames.length - 5 }} 张... (点击展开)
                  </span>
                </template>
              </div>

              <!-- Expanded Tables Grid -->
              <div v-show="!isTablesCollapsed">
                <div v-if="loadingTables" class="py-6 flex items-center justify-center text-xs text-gray-400 gap-2">
                  <svg class="animate-spin h-4 w-4 text-indigo-600" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                  正在加载表列表...
                </div>

                <div v-else-if="internalTables.length === 0" class="py-6 text-center text-xs text-gray-400">
                  当前数据集暂无可分析的表，请先导入或创建表结构。
                </div>

                <div v-else class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2.5 max-h-48 overflow-y-auto pr-1">
                  <div
                    v-for="tbl in filteredTables"
                    :key="tbl.physical_name"
                    @click="toggleTable(tbl.physical_name)"
                    class="flex items-center gap-2.5 p-2.5 rounded-lg border transition-all cursor-pointer select-none text-left"
                    :class="selectedTableNames.includes(tbl.physical_name) 
                      ? 'border-indigo-400 bg-indigo-50/50 text-indigo-900 shadow-sm' 
                      : 'border-gray-200 hover:border-gray-300 bg-white text-gray-700'"
                  >
                    <div 
                      class="w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 transition-colors"
                      :class="selectedTableNames.includes(tbl.physical_name) ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-gray-300 bg-white'"
                    >
                      <svg v-if="selectedTableNames.includes(tbl.physical_name)" class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>
                    </div>
                    <div class="min-w-0 flex-1">
                      <div class="text-xs font-semibold truncate">{{ tbl.term || tbl.physical_name }}</div>
                      <div class="text-[10px] text-gray-400 font-mono truncate">{{ tbl.physical_name }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 2. Custom Business Prompt Section with Help Modal -->
            <div class="bg-white rounded-xl border border-gray-200/80 p-5 shadow-sm relative">
              <div class="flex items-center justify-between mb-2.5">
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-blue-600"></span>
                  <label class="text-sm font-bold text-gray-900">自定义业务偏好与关注点 (可选)</label>
                  <!-- Help button with Question Mark -->
                  <button
                    @click="showHelpModal = true"
                    type="button"
                    title="查看填写帮助与示例"
                    class="inline-flex items-center justify-center w-5 h-5 rounded-full bg-indigo-50 hover:bg-indigo-100 text-indigo-600 text-xs font-bold transition-transform hover:scale-110 shadow-sm"
                  >
                    ?
                  </button>
                </div>
                <button
                  v-if="userPrompt"
                  @click="userPrompt = ''"
                  class="text-xs text-gray-400 hover:text-gray-600 transition-colors"
                >
                  清空
                </button>
              </div>

              <p class="text-xs text-gray-500 mb-3">
                输入您的特定关注方向或分析偏好，AI 将定向推导符合业务口径的高价值指标；留空则全局推断核心 KPI。
              </p>

              <div class="relative">
                <textarea
                  v-model="userPrompt"
                  rows="3"
                  placeholder="例如：重点统计月度业务增长趋势，按分类/状态计算占比与完成率，发掘失败与超时的预警指标... (点击上方 ? 按钮查看结构化填法与通用示例)"
                  class="w-full text-xs p-3 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 resize-none leading-relaxed transition-all"
                ></textarea>
              </div>

              <!-- Quick Template Chips (Generic Patterns) -->
              <div class="mt-3 flex flex-wrap items-center gap-2">
                <span class="text-[11px] text-gray-400 font-medium">通用分析模式:</span>
                <button
                  v-for="item in promptExamples"
                  :key="item.title"
                  @click="userPrompt = item.prompt"
                  type="button"
                  class="px-2.5 py-1 bg-gray-50 hover:bg-indigo-50 hover:text-indigo-600 text-gray-600 text-[11px] rounded-lg border border-gray-200/60 transition-colors flex items-center gap-1"
                >
                  <span>{{ item.title.split(' ')[0] }}</span>
                  <span>{{ item.title.split(' ')[1] }}</span>
                </button>
              </div>
            </div>

            <!-- Bottom Action Trigger -->
            <div class="pt-2 flex flex-col sm:flex-row items-center justify-between gap-4">
              <div class="flex items-center gap-2 text-xs text-gray-400">
                <svg class="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
                <span>10 分钟内自动去重，避免重复推荐相同指标</span>
              </div>

              <button
                @click="handleRecommend"
                :disabled="selectedTableNames.length === 0"
                class="w-full sm:w-auto px-8 py-3 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold shadow-lg shadow-indigo-200 transition-all flex items-center justify-center gap-2"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                <span>立即开始识别 (已选 {{ selectedTableNames.length }} 张表)</span>
              </button>
            </div>

          </div>

          <!-- Analyzing State：展示后端 SSE 推送的真实阶段进度。 -->
          <div v-else-if="analyzing" class="min-h-96 flex flex-col items-center justify-center text-center p-6 space-y-5">
            <div class="relative w-20 h-20 flex items-center justify-center">
              <div class="absolute inset-0 border-4 border-indigo-100 rounded-full"></div>
              <div class="absolute inset-0 border-4 border-indigo-600 rounded-full border-t-transparent animate-spin"></div>
              <div class="flex flex-col items-center justify-center z-10">
                <span class="text-sm font-mono font-bold text-indigo-700">{{ progressPercent }}%</span>
                <span class="text-[10px] text-gray-400">{{ elapsedSeconds }}s</span>
              </div>
            </div>

            <div class="w-full max-w-xl space-y-2 text-left">
              <div class="flex items-center justify-between gap-3 text-xs">
                <span class="font-bold text-gray-800">{{ progress.message }}</span>
                <span class="flex-shrink-0 font-mono text-indigo-700">{{ progressPercent }}%</span>
              </div>
              <div class="h-2 w-full overflow-hidden rounded bg-gray-200">
                <div class="h-full bg-indigo-600 transition-all duration-500" :style="{ width: `${progressPercent}%` }"></div>
              </div>
              <div class="grid grid-cols-3 gap-2 text-center text-[11px]">
                <div class="rounded border border-gray-200 bg-white px-2 py-2">
                  <div class="text-gray-400">执行阶段</div>
                  <div class="mt-0.5 font-bold text-gray-800">{{ progress.completed_units || 0 }} / {{ progress.total_units || 5 }}</div>
                </div>
                <div class="rounded border border-gray-200 bg-white px-2 py-2">
                  <div class="text-gray-400">剩余阶段</div>
                  <div class="mt-0.5 font-bold text-gray-800">{{ progress.remaining_units ?? 5 }}</div>
                </div>
                <div class="rounded border border-gray-200 bg-white px-2 py-2">
                  <div class="text-gray-400">预计剩余</div>
                  <div class="mt-0.5 font-bold text-gray-800">{{ estimatedRemainingText }}</div>
                </div>
              </div>
            </div>

            <div class="max-w-xl rounded border border-blue-200 bg-blue-50 px-3 py-2 text-left text-[11px] leading-relaxed text-blue-900">
              AI 努力生成中，可能较为耗时。已选 {{ selectedTableNames.length }} 张表；进度流中断时页面会明确显示中断状态，请耐心等待。
            </div>

            <!-- Cancel Action Button -->
            <div class="pt-1">
              <button
                @click="handleCancelRecommend"
                type="button"
                class="px-6 py-2 bg-red-50 hover:bg-red-100 text-red-600 hover:text-red-700 border border-red-200 hover:border-red-300 rounded-xl text-xs font-semibold shadow-sm transition-all flex items-center gap-2 group"
              >
                <svg class="w-3.5 h-3.5 text-gray-400 group-hover:text-red-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                <span>取消生成 (已等待 {{ formattedElapsedTime }})</span>
              </button>
            </div>
          </div>

          <!-- Result List -->
          <div v-else class="space-y-4">
            <div class="flex justify-between items-center mb-4">
              <div class="flex items-center gap-2">
                <h3 class="text-sm font-bold text-gray-700 uppercase tracking-wider">推荐列表 ({{ recommendations.length }})</h3>
                <span class="text-xs text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full font-medium">已完成去重</span>
              </div>
              <div class="flex items-center gap-4">
                <button @click="selectedIndices = recommendations.map((_, i) => i)" class="text-xs text-indigo-600 font-bold hover:underline">全选</button>
                <button @click="selectedIndices = []" class="text-xs text-gray-400 font-bold hover:underline">取消全选</button>
              </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div 
                v-for="(item, idx) in recommendations" 
                :key="idx" 
                @click="toggleSelection(idx)"
                class="relative bg-white border-2 rounded-2xl p-5 transition-all cursor-pointer group hover:shadow-xl"
                :class="selectedIndices.includes(idx) ? 'border-indigo-500 bg-indigo-50/20' : 'border-transparent shadow-sm hover:border-gray-200'"
              >
                <button
                  type="button"
                  @click.stop="openRecommendationDetail(idx)"
                  class="absolute right-14 top-4 inline-flex items-center gap-1 rounded-lg border border-indigo-100 bg-indigo-50 px-2 py-1 text-[11px] font-semibold text-indigo-600 transition-colors hover:border-indigo-200 hover:bg-indigo-100"
                  title="查看指标完整详情"
                >
                  <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0Z" />
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.477 0 8.268 2.943 9.542 7-1.274 4.057-5.065 7-9.542 7-4.477 0-8.268-2.943-9.542-7Z" />
                  </svg>
                  <span class="hidden sm:inline">查看详情</span>
                </button>

                <!-- Checkbox Overlay -->
                <div class="absolute top-4 right-4">
                  <div class="w-6 h-6 rounded-full border-2 flex items-center justify-center transition-colors"
                       :class="selectedIndices.includes(idx) ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-gray-200 bg-white'">
                    <svg v-if="selectedIndices.includes(idx)" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>
                  </div>
                </div>

                <div class="pr-8">
                  <div class="flex items-center gap-2 mb-1.5">
                    <span class="font-bold text-gray-900 group-hover:text-indigo-600 transition-colors">{{ item.display_name }}</span>
                    <span class="text-[10px] font-mono text-gray-400">#{{ item.name }}</span>
                    <span v-if="item.unit" class="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded">{{ item.unit }}</span>
                  </div>
                  <p class="text-xs text-gray-500 line-clamp-2 h-8 leading-relaxed mb-3">{{ item.description }}</p>
                
                  <div class="bg-gray-900/5 rounded-xl p-3 font-mono text-[10px] text-gray-700 overflow-hidden relative">
                    <div class="absolute top-0 right-0 px-2 py-0.5 bg-gray-200 text-gray-500 text-[8px] rounded-bl font-semibold uppercase">SQL</div>
                    <div class="line-clamp-3 leading-relaxed">{{ item.calculation_logic }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div v-if="recommendations.length > 0 && !analyzing" class="p-5 border-t border-gray-100 bg-white/90 backdrop-blur flex justify-between items-center">
          <button 
            v-if="currentTraceId"
            @click="showLogs = true"
            class="text-xs text-gray-400 hover:text-indigo-600 flex items-center gap-1.5 transition-colors"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
            查看 AI 思考过程
          </button>
          <div v-else></div>

          <div class="flex items-center gap-3">
            <button 
              @click="handleBackToConfig" 
              class="px-5 py-2.5 text-xs font-semibold text-gray-600 hover:bg-gray-100 rounded-xl transition-colors border border-gray-200"
              :disabled="saving"
            >
              调整配置 / 重新发现
            </button>
            <button 
              @click="handleSave" 
              :disabled="selectedIndices.length === 0 || saving"
              class="px-7 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold shadow-lg shadow-indigo-200 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg v-if="saving" class="animate-spin h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
              {{ saving ? '入库中...' : `保存选中指标 (${selectedIndices.length})` }}
            </button>
          </div>
        </div>

      </div>
    </div>
  </Teleport>

  <!-- Recommendation detail modal -->
  <div
    v-if="selectedRecommendation"
    class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
    @click.self="closeRecommendationDetail"
  >
    <div class="flex max-h-[82vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-2xl">
      <div class="flex items-start justify-between border-b border-gray-100 bg-gradient-to-r from-indigo-50/70 to-white px-6 py-5">
        <div class="min-w-0 pr-4">
          <p class="text-[11px] font-semibold uppercase tracking-wider text-indigo-600">推荐指标详情</p>
          <h3 class="mt-1 text-lg font-bold text-gray-900">{{ selectedRecommendation.display_name }}</h3>
          <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-400">
            <code class="font-mono">#{{ selectedRecommendation.name }}</code>
            <span v-if="selectedRecommendation.unit" class="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600">{{ selectedRecommendation.unit }}</span>
          </div>
        </div>
        <button
          type="button"
          @click="closeRecommendationDetail"
          class="rounded-full p-2 text-gray-400 transition-colors hover:bg-white hover:text-gray-700"
          title="关闭详情"
        >
          <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </div>

      <div class="space-y-5 overflow-y-auto p-6 text-sm">
        <section>
          <h4 class="mb-2 text-xs font-bold uppercase tracking-wide text-gray-500">业务描述</h4>
          <p class="rounded-xl border border-gray-100 bg-gray-50 p-4 leading-6 text-gray-700">{{ selectedRecommendation.description || '暂无描述' }}</p>
        </section>
        <section>
          <h4 class="mb-2 text-xs font-bold uppercase tracking-wide text-gray-500">计算逻辑 / SQL</h4>
          <pre class="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-gray-950 p-4 font-mono text-xs leading-6 text-emerald-300">{{ selectedRecommendation.calculation_logic || '--' }}</pre>
        </section>
        <section v-if="selectedRecommendation.tags && selectedRecommendation.tags.length">
          <h4 class="mb-2 text-xs font-bold uppercase tracking-wide text-gray-500">标签</h4>
          <div class="flex flex-wrap gap-2">
            <span v-for="tag in selectedRecommendation.tags" :key="tag" class="rounded-full border border-blue-100 bg-blue-50 px-2.5 py-1 text-xs text-blue-700">{{ tag }}</span>
          </div>
        </section>
      </div>

      <div class="flex justify-end border-t border-gray-100 bg-gray-50/70 px-6 py-3">
        <button type="button" @click="closeRecommendationDetail" class="rounded-lg border border-gray-200 bg-white px-4 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-50">关闭</button>
      </div>
    </div>
  </div>

  <!-- Help & Guidelines Modal (问号弹窗 - 通用化指南与示例) -->
  <Teleport to="body">
    <div 
      v-if="showHelpModal" 
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fade-in"
      @click.self="showHelpModal = false"
    >
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden border border-gray-100">
      
        <!-- Modal Header -->
        <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gradient-to-r from-blue-50/60 to-indigo-50/60">
          <div class="flex items-center gap-2.5">
            <div class="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white text-sm font-bold shadow-sm">
              ?
            </div>
            <div>
              <h3 class="text-sm font-bold text-gray-900">业务偏好提示词：填写指南与影响说明</h3>
              <p class="text-[11px] text-gray-500">掌握结构化填法与核心影响，让 AI 精准产出最贴合业务的分析指标</p>
            </div>
          </div>
          <button 
            @click="showHelpModal = false" 
            class="text-gray-400 hover:text-gray-600 p-1.5 hover:bg-white rounded-full transition-colors"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Modal Body -->
        <div class="p-6 overflow-y-auto space-y-5 text-xs text-gray-600 leading-relaxed">
        
          <!-- 1. 为什么填？具体产生哪些影响？ -->
          <div class="p-4 bg-indigo-50/60 border border-indigo-100 rounded-xl space-y-2.5">
            <div class="font-bold text-indigo-900 flex items-center gap-1.5">
              <svg class="w-4 h-4 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              填与不填的区别？具体会带来哪些影响？
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
              <div class="p-3 bg-white rounded-lg border border-indigo-100/80 space-y-1">
                <div class="font-bold text-indigo-950 flex items-center gap-1">
                  <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                  填写自定义偏好时
                </div>
                <ul class="text-[11px] text-gray-600 list-disc list-inside space-y-0.5">
                  <li><strong>聚焦指定领域</strong>：将有限名额（5~10个）集中在您关心的业务主线，避免无意义列求和。</li>
                  <li><strong>引导高阶 SQL</strong>：指定“趋势”生成时间函数、指定“占比”生成比率除法、指定“异常”生成条件过滤。</li>
                  <li><strong>深度发掘业务语义</strong>：推断复合比率（如完成率、流失率、利用率）。</li>
                </ul>
              </div>

              <div class="p-3 bg-white rounded-lg border border-indigo-100/80 space-y-1">
                <div class="font-bold text-indigo-950 flex items-center gap-1">
                  <span class="w-1.5 h-1.5 rounded-full bg-gray-400"></span>
                  留空（不填）时
                </div>
                <ul class="text-[11px] text-gray-600 list-disc list-inside space-y-0.5">
                  <li><strong>全局基础发掘</strong>：AI 基于所选数据表 Schema 进行通用推断。</li>
                  <li><strong>标准 KPI 组合</strong>：默认推断基础行数（COUNT）、数值列均值（AVG）、枚举字段维度分布（GROUP BY）。</li>
                  <li><strong>适合冷启动</strong>：适合初次摸排表结构或无特定分析方向时使用。</li>
                </ul>
              </div>
            </div>
          </div>

          <!-- 2. 怎么填？四段式万能填法 -->
          <div class="p-4 bg-gray-50 border border-gray-200 rounded-xl space-y-2">
            <div class="font-bold text-gray-900 flex items-center gap-1.5">
              <PencilIcon class="w-4 h-4 text-gray-500" /> 怎么填？通用的「四段式填法结构」
            </div>
            <p class="text-[11px] text-gray-500">
              无论面对什么类型的数据表（订单、日志、设备、用户、财务等），您只需在输入框中组合以下 2~3 个要素：
            </p>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
              <div class="bg-white p-2 rounded-lg border border-gray-200 text-center">
                <div class="text-[10px] text-indigo-600 font-bold">① 核心主题</div>
                <div class="text-[11px] text-gray-700 mt-0.5">业务量/活跃/耗时/收支</div>
              </div>
              <div class="bg-white p-2 rounded-lg border border-gray-200 text-center">
                <div class="text-[10px] text-indigo-600 font-bold">② 统计维度</div>
                <div class="text-[11px] text-gray-700 mt-0.5">按天/月、按分类、按部门</div>
              </div>
              <div class="bg-white p-2 rounded-lg border border-gray-200 text-center">
                <div class="text-[10px] text-indigo-600 font-bold">③ 计算类型</div>
                <div class="text-[11px] text-gray-700 mt-0.5">总量/均值/占比/排名TOP</div>
              </div>
              <div class="bg-white p-2 rounded-lg border border-gray-200 text-center">
                <div class="text-[10px] text-indigo-600 font-bold">④ 过滤/异常</div>
                <div class="text-[11px] text-gray-700 mt-0.5">失败率/超时/超阈值预警</div>
              </div>
            </div>
          </div>

          <!-- 3. 5 大全行业通用示例（一键应用） -->
          <div>
            <h4 class="font-bold text-gray-800 mb-2.5 flex items-center justify-between">
              <span class="flex items-center gap-1.5">
                <LightBulbIcon class="w-4 h-4 text-amber-500" /> 全行业通用场景示例
              </span>
              <span class="text-[11px] font-normal text-gray-400">点击卡片可直接一键填入输入框</span>
            </h4>

            <div class="space-y-2.5">
              <div 
                v-for="(item, idx) in promptExamples" 
                :key="idx"
                @click="applyPromptExample(item.prompt)"
                class="p-3.5 rounded-xl border border-gray-200 hover:border-indigo-400 bg-white hover:bg-indigo-50/30 transition-all cursor-pointer group shadow-sm"
              >
                <div class="flex items-center justify-between mb-1">
                  <div class="flex items-center gap-2">
                    <span class="font-bold text-gray-900 group-hover:text-indigo-600 transition-colors">{{ item.title }}</span>
                    <span class="text-[10px] px-2 py-0.2 bg-gray-100 group-hover:bg-indigo-100 text-gray-600 group-hover:text-indigo-700 rounded font-medium">{{ item.badge }}</span>
                  </div>
                  <span class="text-[10px] text-indigo-600 font-semibold group-hover:underline flex items-center gap-1">
                    应用此模式
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
                  </span>
                </div>
                <p class="text-[11px] text-gray-500 mb-1.5">{{ item.description }}</p>
                <div class="p-2 bg-gray-50 group-hover:bg-white rounded-lg border border-gray-100 font-mono text-[10.5px] text-gray-700">
                  "{{ item.prompt }}"
                </div>
              </div>
            </div>
          </div>

        </div>

        <!-- Modal Footer -->
        <div class="px-6 py-3.5 border-t border-gray-100 bg-gray-50/50 flex justify-end">
          <button 
            @click="showHelpModal = false"
            class="px-5 py-2 bg-gray-200 hover:bg-gray-300 text-gray-700 rounded-lg text-xs font-semibold transition-colors"
          >
            关闭
          </button>
        </div>

      </div>
    </div>
  </Teleport>

  <TraceLogViewer 
    :visible="showLogs" 
    :trace-id="currentTraceId" 
    @close="showLogs = false" 
  />
</template>

<style scoped>
.animate-fade-in-up {
  animation: fadeInUp 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}
.animate-fade-in {
  animation: fadeIn 0.2s ease-out;
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
</style>
