<script setup lang="ts">
import { ref, computed, watch, nextTick, shallowRef, onUnmounted } from 'vue'
import { metadataApi } from '../../api/metadata'
import type {
  MetadataAiProgress,
  MetadataAiRunStatus,
  RelationshipRecommendation,
  RelationshipRecommendationResult,
  RelationshipRecommendationStrategy,
} from '../../api/metadata'
import { useToast } from '../../composables/useToast'
import TraceLogViewer from '../TraceLogViewer.vue'
import { ExclamationTriangleIcon, KeyIcon, LightBulbIcon, LinkIcon, PencilIcon, SparklesIcon } from '@heroicons/vue/24/outline'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'

echarts.use([CanvasRenderer, GraphChart, TooltipComponent, LegendComponent])

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
const recommendations = ref<RelationshipRecommendation[]>([])
const lastDebug = ref<RelationshipRecommendationResult['_debug'] | null>(null)
const selectedIndices = ref<number[]>([])
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

// Result View Mode Tab ('list' | 'graph')
const resultTab = ref<'list' | 'graph'>('list')
const chartContainer = ref<HTMLDivElement | null>(null)
const chartInstance = shallowRef<echarts.ECharts | null>(null)

// Custom prompt and table selection state
const internalTables = ref<TableItem[]>([])
const selectedTableNames = ref<string[]>([])
const userPrompt = ref('')
const relationshipStrategy = ref<RelationshipRecommendationStrategy>('smart')
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

const resetRunState = () => {
  stopTimer()
  analyzing.value = false
  recommendations.value = []
  lastDebug.value = null
  selectedIndices.value = []
  currentTraceId.value = ''
  runStatus.value = 'idle'
  elapsedSeconds.value = 0
  progress.value = {
    status: 'idle',
    phase: 'idle',
    message: '等待开始',
    percent: 0,
  }
}

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
    message: '用户已取消，后端关系推导正在中断',
  }
  console.warn('关系推荐已由用户中断', {
    datasetId: props.datasetId,
    traceId: currentTraceId.value,
    progress: progress.value,
  })
  showToast('已取消实体关系智能发现', 'info')
}

const { showToast } = useToast()

// Generic Relationship Prompt Patterns
const promptExamples = [
  {
    title: '🔗 核心主外键关联',
    badge: '事实与维度/主外键',
    description: '适用于推断业务主表与维度表、字典表之间的主外键归属与级联映射。',
    prompt: '重点分析核心业务事实表与基础维度表之间的外键对应关系与 Join 连接条件。'
  },
  {
    title: '👥 用户与单据归属',
    badge: '操作人/所有者归属',
    description: '适用于挖掘用户/员工/客户与订单、工单、审批单等单据的归属关系。',
    prompt: '聚焦用户与业务单据、操作记录、申请流转表之间的归属与创建人关联。'
  },
  {
    title: '📦 单据与子表明细',
    badge: '主子表/1对多明细',
    description: '适用于挖掘主订单与订单项、主任务与子任务、单据与日志明细关联。',
    prompt: '推断主单据表与其明细子表、费用拆分表、执行记录表之间的 1:N 关联关系。'
  },
  {
    title: '🏢 组织机构与多级层级',
    badge: '组织架构/多租户',
    description: '适用于分析部门、公司、租户、区域等层级在各个实体中的穿透关联。',
    prompt: '挖掘部门组织机构、租户与各个业务数据表之间的层级归属关联。'
  },
  {
    title: '⚙️ 流转状态与事件流水',
    badge: '状态流转/审计日志',
    description: '适用于连接业务主实体与历史变更记录、状态流转流水表。',
    prompt: '推断业务实体与状态变更历史表、操作审计日志表之间的流水关联。'
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
      resetRunState()
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
  showToast('已填入偏好模式', 'info')
}

const handleRecommend = async () => {
  if (selectedTableNames.value.length < 2 && internalTables.value.length >= 2) {
    showToast('实体关系发现需要至少选择 2 张数据表', 'warning')
    return
  }

  analyzing.value = true
  recommendations.value = []
  lastDebug.value = null
  selectedIndices.value = []
  currentTraceId.value = ''
  runStatus.value = 'running'
  progress.value = {
    status: 'running',
    phase: 'connecting',
    message: '正在连接实时进度服务',
    percent: 0,
    completed_units: 0,
    total_units: 0,
    remaining_units: 0,
    unit_label: '候选组',
    batch_count: 0,
    result_count: 0,
    candidate_pair_count: 0,
    completed_pair_count: 0,
    remaining_pair_count: 0,
  }
  startTimer()
  abortController = new AbortController()
  const requestStartedAt = Date.now()
  console.info('关系推荐请求开始', {
    datasetId: props.datasetId,
    selectedTableCount: selectedTableNames.value.length,
    hasUserPrompt: Boolean(userPrompt.value.trim()),
    strategy: relationshipStrategy.value,
  })
  
  try {
    const data = await metadataApi.recommendRelationshipsStream(
      props.datasetId,
      {
        table_names: selectedTableNames.value.length > 0 ? selectedTableNames.value : undefined,
        user_prompt: userPrompt.value.trim() ? userPrompt.value.trim() : undefined,
        strategy: relationshipStrategy.value,
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

    recommendations.value = data?.relationships || []
    lastDebug.value = data?._debug || null
    currentTraceId.value = data?._trace_id || ''
    const partialInterruption = ['partial_batch_error', 'partial_group_error'].includes(data?._stop_reason || '')
    runStatus.value = partialInterruption ? 'interrupted' : 'completed'
    progress.value = {
      ...progress.value,
      status: partialInterruption ? 'interrupted' : 'completed',
      phase: partialInterruption ? 'interrupted' : 'completed',
      message: partialInterruption
        ? `关系推荐中断，已保留 ${recommendations.value.length} 条结果`
        : `关系推荐完成，共生成 ${recommendations.value.length} 条关系`,
      percent: partialInterruption ? progress.value.percent : 100,
      remaining_units: partialInterruption ? progress.value.remaining_units : 0,
      result_count: recommendations.value.length,
    }
    console.info('关系推荐请求完成', {
      datasetId: props.datasetId,
      traceId: currentTraceId.value,
      relationshipCount: recommendations.value.length,
      batchCount: data?._batch_count,
      stopReason: data?._stop_reason,
      durationMs: Date.now() - requestStartedAt,
    })
    
    if (recommendations.value.length === 0) {
      showToast('未发现新的推荐关联关系，可能所选表间无明显主外键命名或已有关系已覆盖', 'info')
    } else {
      showToast(
        partialInterruption
          ? `生成中断，已保留 ${recommendations.value.length} 条关系，可先检查或保存`
          : `AI 成功推导了 ${recommendations.value.length} 条关联关系 (耗时 ${formattedElapsedTime.value})`,
        partialInterruption ? 'warning' : 'success',
      )
      // Default select all
      selectedIndices.value = recommendations.value.map((_, i) => i)
    }
  } catch (e: any) {
    if (e.name === 'AbortError' || e.name === 'CanceledError' || e.code === 'ERR_CANCELED' || e.message === 'canceled') {
      if (runStatus.value !== 'interrupted') {
        runStatus.value = 'interrupted'
        progress.value = {
          ...progress.value,
          status: 'interrupted',
          phase: 'interrupted',
          message: '关系推荐连接已中断',
        }
      }
      return
    }
    const detail = e.response?.data?.detail || e.message || ''
    const status = e.response?.status
    const isTimeout = e.code === 'ECONNABORTED' || String(e.message || '').toLowerCase().includes('timeout')
    const isGatewayTimeout = [502, 503, 504].includes(status)
    console.error('关系推荐请求失败', {
      datasetId: props.datasetId,
      selectedTableCount: selectedTableNames.value.length,
      durationMs: Date.now() - requestStartedAt,
      errorCode: e.code,
      httpStatus: status,
      message: e.message,
      detail,
    }, e)
    const match = detail.match(/Trace ID: ([a-zA-Z0-9-]+)/)
    if (match) currentTraceId.value = match[1]
    runStatus.value = 'error'
    progress.value = {
      ...progress.value,
      status: 'error',
      phase: 'error',
      message: detail || '关系推荐失败',
    }

    if (isTimeout || isGatewayTimeout) {
      showToast('关系推荐连接等待超时，请先查看后端关系推荐日志，避免立即重复提交', 'error')
    } else {
      showToast(detail || `推荐失败${status ? `（HTTP ${status}）` : ''}，请查看控制台与后端日志`, 'error')
    }
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

// 排序与格式化辅助函数
const sortKey = ref<'confidence' | 'source_table'>('confidence')
const sortAsc = ref(false)

const relationTypeLabel = (t?: string) => {
  switch (t) {
    case 'one_to_one': return '一对一'
    case 'one_to_many': return '一对多'
    case 'many_to_one': return '多对一'
    default: return t || '-'
  }
}

const sourceBadge = (source?: string) => {
  if (source === 'FK') return { label: '外键确认', bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' }
  return { label: 'AI 推断', bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200' }
}

const confidenceStyle = (c: number) => {
  if (c >= 0.85)
    return { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200', dot: 'bg-emerald-500', label: '高置信' }
  if (c >= 0.6)
    return { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200', dot: 'bg-amber-500', label: '中置信' }
  return { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-200', dot: 'bg-red-500', label: '低置信' }
}

const sortedRecommendations = computed(() => {
  const list = recommendations.value.map((item, originalIndex) => ({
    ...item,
    _originalIndex: originalIndex
  }))
  const dir = sortAsc.value ? 1 : -1
  list.sort((a, b) => {
    if (sortKey.value === 'confidence') {
      return ((a.confidence || 0) - (b.confidence || 0)) * dir
    }
    return a.source_table.localeCompare(b.source_table) * dir
  })
  return list
})

const findTableIdByPhysicalName = (name: string): number | null => {
  const found = internalTables.value.find((t) => t.physical_name === name)
  return found?.id ?? null
}

// ===== 🕸️ ER 图画布渲染与交互 =====
const initOrUpdateGraph = () => {
  if (!chartContainer.value) return
  if (!chartInstance.value) {
    chartInstance.value = echarts.init(chartContainer.value)
    chartInstance.value.on('click', (params: any) => {
      if (params.dataType === 'edge' && params.data?.originalIndex !== undefined) {
        toggleSelection(params.data.originalIndex)
      } else if (params.dataType === 'node') {
        const tableName = params.data?.name
        showToast(`已定位数据表: ${tableName}`, 'info')
      }
    })
  }

  // 提取所有涉及的表（推荐关系的源表与目标表）
  const involvedTableNames = new Set<string>()
  recommendations.value.forEach(r => {
    if (r.source_table) involvedTableNames.add(r.source_table)
    if (r.target_table) involvedTableNames.add(r.target_table)
  })
  selectedTableNames.value.forEach(t => involvedTableNames.add(t))

  const nodes = Array.from(involvedTableNames).map(tableName => {
    const tableObj = internalTables.value.find(t => t.physical_name === tableName)
    const degree = recommendations.value.filter(
      r => r.source_table === tableName || r.target_table === tableName
    ).length
    return {
      id: tableName,
      name: tableName,
      symbolSize: Math.max(38, Math.min(64, 38 + degree * 6)),
      itemStyle: {
        color: tableName.startsWith('fact_')
          ? '#4f46e5'
          : tableName.startsWith('dim_')
          ? '#059669'
          : '#10b981',
        shadowBlur: 8,
        shadowColor: 'rgba(0, 0, 0, 0.12)'
      },
      value: tableObj?.term || tableName,
      label: {
        show: true,
        formatter: (params: any) => `${params.data.name}${tableObj?.term ? `\n[${tableObj.term}]` : ''}`,
        fontSize: 11,
        color: '#1f2937',
        fontWeight: 'bold'
      },
      tableObj
    }
  })

  const links = recommendations.value.map((rel, originalIndex) => {
    const isSelected = selectedIndices.value.includes(originalIndex)
    const conf = rel.confidence || 0
    const lineColor = isSelected
      ? (conf >= 0.85 ? '#059669' : conf >= 0.6 ? '#d97706' : '#dc2626')
      : '#cbd5e1'

    return {
      source: rel.source_table,
      target: rel.target_table,
      originalIndex,
      relData: rel,
      lineStyle: {
        curveness: 0.15,
        color: lineColor,
        width: isSelected ? 3 : 1.5,
        type: isSelected ? 'solid' : 'dashed'
      },
      symbol: ['none', 'arrow'],
      symbolSize: [0, isSelected ? 9 : 6],
      label: {
        show: true,
        formatter: `${relationTypeLabel(rel.relation_type)} ${Math.round(conf * 100)}%`,
        fontSize: 10,
        color: isSelected ? '#065f46' : '#64748b',
        backgroundColor: isSelected ? 'rgba(236, 253, 245, 0.92)' : 'rgba(248, 250, 252, 0.92)',
        borderColor: isSelected ? '#a7f3d0' : '#e2e8f0',
        borderWidth: 1,
        padding: [2, 4],
        borderRadius: 4
      }
    }
  })

  const option = {
    tooltip: {
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          const t = params.data.tableObj
          return `<div class="font-bold text-sm text-gray-900">${params.data.name}</div>
                  <div class="text-xs text-gray-500">${t?.term || '暂无业务术语'}</div>
                  ${t?.description ? `<div class="text-[11px] text-gray-400 mt-1">${t.description}</div>` : ''}`
        } else if (params.dataType === 'edge') {
          const rel = params.data.relData
          const isSelected = selectedIndices.value.includes(params.data.originalIndex)
          const conf = Math.round((rel.confidence || 0) * 100)
          return `<div class="flex items-center gap-2 mb-1">
                    <span class="font-bold text-xs text-emerald-700">${relationTypeLabel(rel.relation_type)}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold">${conf}% 置信度</span>
                    <span class="text-[10px] ${isSelected ? 'text-emerald-600 font-bold' : 'text-gray-400'}">[${isSelected ? '已勾选采纳' : '未勾选'}]</span>
                  </div>
                  <div class="text-xs font-mono font-bold text-gray-800 bg-gray-100 p-1.5 rounded">${rel.condition}</div>
                  ${rel.description ? `<div class="text-xs text-gray-500 mt-1.5 leading-relaxed">📝 ${rel.description}</div>` : ''}
                  <div class="text-[10px] text-blue-500 mt-1.5 font-sans">💡 点击此连线可直接切换勾选/取消采纳状态</div>`
        }
        return ''
      },
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: '#e5e7eb',
      borderWidth: 1,
      padding: 10,
      textStyle: { color: '#1f2937' },
      extraCssText: 'box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1); border-radius: 8px;'
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        data: nodes,
        links: links,
        roam: true,
        draggable: true,
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: [0, 8],
        label: {
          position: 'bottom',
          distance: 6
        },
        force: {
          repulsion: 380,
          edgeLength: [120, 220],
          gravity: 0.1
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: {
            width: 4
          }
        }
      }
    ]
  }

  chartInstance.value.setOption(option, true)
}

const handleResize = () => {
  if (chartInstance.value) chartInstance.value.resize()
}

watch(resultTab, (tab) => {
  if (tab === 'graph') {
    nextTick(() => {
      initOrUpdateGraph()
      setTimeout(() => handleResize(), 100)
    })
  }
})

watch(selectedIndices, () => {
  if (resultTab.value === 'graph') {
    initOrUpdateGraph()
  }
}, { deep: true })

if (typeof window !== 'undefined') {
  window.addEventListener('resize', handleResize)
}

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', handleResize)
  }
  if (chartInstance.value) {
    chartInstance.value.dispose()
    chartInstance.value = null
  }
})

const handleSave = async () => {
  if (selectedIndices.value.length === 0) return
  
  saving.value = true
  let successCount = 0
  const failed: string[] = []
  
  try {
    for (const idx of selectedIndices.value) {
      const rec = recommendations.value[idx]
      if (!rec) continue

      const srcId = findTableIdByPhysicalName(rec.source_table)
      const tgtId = findTableIdByPhysicalName(rec.target_table)
      if (srcId == null || tgtId == null) {
        failed.push(`${rec.source_table} <-> ${rec.target_table}`)
        continue
      }

      await metadataApi.createRelationship(props.datasetId, {
        source_table_id: srcId,
        target_table_id: tgtId,
        join_condition: rec.condition,
        join_type: 'left',
        description: rec.description,
      })
      successCount++
    }
    
    if (failed.length > 0) {
      showToast(`已采纳 ${successCount} 条关系，${failed.length} 条因表ID未匹配未保存`, 'warning')
    } else {
      showToast(`成功采纳 ${successCount} 条实体关联关系`, 'success')
    }

    emit('saved')
    emit('close')
  } catch (e: any) {
    console.error('Save relationships failed', e)
    showToast(e.response?.data?.detail || '保存关系失败', 'error')
  } finally {
    saving.value = false
  }
}

const handleBackToConfig = () => {
  recommendations.value = []
  selectedIndices.value = []
}
</script>

<template>
  <div 
    v-if="show" 
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fade-in"
    @click.self="emit('close')"
  >
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
      
      <!-- Header -->
      <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-gradient-to-r from-emerald-50/50 via-teal-50/30 to-indigo-50/40">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-emerald-600 flex items-center justify-center text-white shadow-lg shadow-emerald-200">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
          </div>
          <div>
            <div class="flex items-center gap-2">
              <h2 class="text-lg font-bold text-gray-900 flex items-center gap-1.5"><SparklesIcon class="w-5 h-5 text-emerald-600" /> 实体关系智能发现</h2>
              <span class="px-2 py-0.5 text-[11px] font-bold bg-emerald-100 text-emerald-700 rounded-full">AI 智能推导</span>
            </div>
            <p class="text-xs text-gray-500 mt-0.5">跨表 Schema 语义匹配与主外键关联发现，自动推导 Join 条件与置信度</p>
          </div>
        </div>
        <button 
          @click="emit('close')" 
          class="text-gray-400 hover:text-gray-600 p-2 hover:bg-white rounded-full transition-colors"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>

      <!-- Body Content -->
      <div class="flex-1 overflow-y-auto p-6 bg-slate-50/50">
        
        <!-- Config / Pre-run Mode -->
        <div v-if="!analyzing && recommendations.length === 0" class="space-y-4">
          <div
            v-if="runStatus === 'interrupted' || runStatus === 'error'"
            class="flex items-start gap-3 rounded-lg border px-4 py-3 text-left"
            :class="runStatus === 'interrupted' ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-red-200 bg-red-50 text-red-800'"
          >
            <span class="mt-0.5 h-2 w-2 flex-shrink-0 rounded-full" :class="runStatus === 'interrupted' ? 'bg-amber-500' : 'bg-red-500'"></span>
            <div class="min-w-0">
              <div class="text-xs font-bold">{{ runStatus === 'interrupted' ? '上次推导已中断' : '上次推导失败' }}</div>
              <div class="mt-0.5 break-words text-[11px]">{{ progress.message }}</div>
            </div>
          </div>

          <div
            v-if="lastDebug && runStatus === 'completed' && recommendations.length === 0"
            class="rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-left"
          >
            <div class="flex items-center justify-between gap-3">
              <div class="text-xs font-bold text-amber-900">本次未发现新的关系</div>
              <span class="rounded-full bg-white/80 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                {{ lastDebug.strategy === 'smart' ? '智能推断' : '严格模式' }}
              </span>
            </div>
            <div class="mt-2 grid grid-cols-2 gap-2 text-[11px] text-amber-900 sm:grid-cols-4">
              <span>表对总数：{{ lastDebug.possible_pair_count || 0 }}</span>
              <span>候选表对：{{ lastDebug.candidate_pair_count || 0 }}</span>
              <span>AI 分析组：{{ lastDebug.candidate_group_count || 0 }}</span>
              <span>已去重：{{ lastDebug.confirmed_duplicate_count || 0 }}</span>
            </div>
            <p v-if="lastDebug.truncated_pair_count" class="mt-2 text-[11px] leading-relaxed text-amber-800">
              智能候选较多，已按置信度截取 {{ lastDebug.candidate_pair_limit }} 对进行分析，另有 {{ lastDebug.truncated_pair_count }} 对未进入本次分析。
            </p>
            <p v-else-if="lastDebug.strategy !== 'smart'" class="mt-2 text-[11px] leading-relaxed text-amber-800">
              可切换到“智能推断”扩大无主外键场景的候选范围，或补充字段中文名称和业务描述后重试。
            </p>
            <p v-else class="mt-2 text-[11px] leading-relaxed text-amber-800">
              可补充字段中文名称和业务描述后重试；如果只想查看明确结构关系，可以切换到“严格模式”。
            </p>
          </div>
          
          <!-- 1. Relationship Discovery Strategy -->
          <div class="bg-white rounded-xl border border-gray-200/80 p-5 shadow-sm">
            <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full bg-emerald-600"></span>
                  <label class="text-sm font-bold text-gray-900">发现策略</label>
                  <span class="text-[11px] font-bold rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-700 border border-emerald-100">默认推荐</span>
                </div>
                <p class="mt-1 text-xs leading-relaxed text-gray-500">
                  两种策略都只分析 Schema 和字段元数据，不查询业务数据行。
                </p>
              </div>
              <div class="flex shrink-0 rounded-lg border border-gray-200 bg-gray-50 p-1">
                <button
                  type="button"
                  :disabled="analyzing"
                  @click="relationshipStrategy = 'smart'"
                  class="rounded-md px-3 py-1.5 text-xs font-bold transition-all"
                  :class="relationshipStrategy === 'smart' ? 'bg-white text-emerald-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
                >
                  智能推断
                </button>
                <button
                  type="button"
                  :disabled="analyzing"
                  @click="relationshipStrategy = 'strict'"
                  class="rounded-md px-3 py-1.5 text-xs font-bold transition-all"
                  :class="relationshipStrategy === 'strict' ? 'bg-white text-emerald-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
                >
                  严格模式
                </button>
              </div>
            </div>
            <div class="mt-3 rounded-lg border border-emerald-100 bg-emerald-50/60 px-3 py-2 text-[11px] leading-relaxed text-emerald-900">
              <span v-if="relationshipStrategy === 'smart'">
                智能推断会结合表名、字段名、中文术语、描述和类型等线索扩大候选；结果会标记为“AI 推断”，仍需人工确认。
              </span>
              <span v-else>
                严格模式只使用明确外键、强命名和高置信度结构线索，误推荐更少，但无主外键的数据库可能发现较少。
              </span>
            </div>
          </div>

          <!-- 2. Table Selection Section with Collapse -->
          <div class="bg-white rounded-xl border border-gray-200/80 p-5 shadow-sm">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-3">
              <div 
                @click="isTablesCollapsed = !isTablesCollapsed"
                class="flex items-center gap-2 cursor-pointer select-none group/title"
              >
                <span class="w-2 h-2 rounded-full bg-emerald-600"></span>
                <label class="text-sm font-bold text-gray-900 group-hover/title:text-emerald-600 transition-colors">
                  分析数据表范围 (推断表间关联关系)
                </label>
                <span class="text-xs text-emerald-600 font-bold bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-100">
                  已选 {{ selectedTableNames.length }} / {{ internalTables.length }} 张表
                </span>
                <span v-if="selectedTableNames.length < 2" class="text-[11px] text-amber-600 font-medium bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200">
                  <ExclamationTriangleIcon class="w-3.5 h-3.5 inline-block mr-1" />需至少选择 2 张表
                </span>
              </div>

              <!-- Action button for collapse/expand -->
              <div class="flex items-center gap-2">
                <button
                  @click="isTablesCollapsed = !isTablesCollapsed"
                  type="button"
                  class="text-xs text-gray-500 hover:text-emerald-600 px-2.5 py-1 rounded-lg hover:bg-gray-50 border border-gray-200/70 transition-colors flex items-center gap-1.5"
                >
                  <span>{{ isTablesCollapsed ? '展开选表面板' : '收起选表面板' }}</span>
                  <svg 
                    class="w-3.5 h-3.5 transition-transform duration-200" 
                    :class="isTablesCollapsed ? 'rotate-180' : ''" 
                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                  </svg>
                </button>
              </div>
            </div>

            <!-- Collapsed Summary View -->
            <div v-if="isTablesCollapsed" class="p-3 bg-gray-50/70 rounded-xl border border-gray-100 flex flex-wrap items-center gap-1.5 animate-fade-in">
              <span class="text-[11px] text-gray-400 font-medium mr-1">当前推断范围:</span>
              <span 
                v-for="tbl in selectedTableNames.slice(0, 6)" 
                :key="tbl"
                class="px-2 py-0.5 bg-white border border-gray-200 rounded-md text-[11px] font-mono text-gray-700 font-medium shadow-2xs"
              >
                {{ tbl }}
              </span>
              <span 
                v-if="selectedTableNames.length > 6" 
                class="px-2 py-0.5 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-md text-[11px] font-medium"
              >
                +{{ selectedTableNames.length - 6 }} 张... (点击展开)
              </span>
            </div>

            <!-- Expanded Full Table Grid View -->
            <div v-else class="space-y-3 animate-fade-in">
              <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div class="relative flex-1 max-w-sm">
                  <input
                    v-model="tableSearchQuery"
                    type="search"
                    placeholder="搜索物理表名、术语或描述..."
                    class="w-full text-xs pl-8 pr-7 py-1.5 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500"
                  />
                  <svg class="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
                </div>

                <div class="flex items-center gap-3">
                  <button 
                    @click="toggleSelectAllTables" 
                    type="button"
                    class="text-xs font-semibold text-emerald-600 hover:text-emerald-700"
                  >
                    {{ isAllTablesSelected ? '取消全选' : '全选所有表' }}
                  </button>
                </div>
              </div>

              <!-- Table Checkbox Grid -->
              <div class="max-h-48 overflow-y-auto border border-gray-100 rounded-xl p-3 bg-gray-50/50">
                <div v-if="loadingTables" class="py-6 text-center text-xs text-gray-400">
                  正在加载数据表列表...
                </div>
                <div v-else-if="filteredTables.length === 0" class="py-6 text-center text-xs text-gray-400">
                  未匹配到相关数据表
                </div>
                <div v-else class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
                  <div
                    v-for="tbl in filteredTables"
                    :key="tbl.physical_name"
                    @click="toggleTable(tbl.physical_name)"
                    class="flex items-center gap-2.5 p-2 rounded-lg border text-xs cursor-pointer transition-all bg-white"
                    :class="selectedTableNames.includes(tbl.physical_name) ? 'border-emerald-500 bg-emerald-50/20 text-emerald-950 font-medium' : 'border-gray-200/80 text-gray-600 hover:border-gray-300'"
                  >
                    <input
                      type="checkbox"
                      :checked="selectedTableNames.includes(tbl.physical_name)"
                      class="rounded text-emerald-600 focus:ring-emerald-500 h-3.5 w-3.5 pointer-events-none"
                    />
                    <div class="flex-1 truncate">
                      <div class="truncate font-mono">{{ tbl.physical_name }}</div>
                      <div v-if="tbl.term" class="text-[10px] text-gray-400 truncate">{{ tbl.term }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 3. Custom Business Prompt Section with Help Modal -->
          <div class="bg-white rounded-xl border border-gray-200/80 p-5 shadow-sm relative">
            <div class="flex items-center justify-between mb-2.5">
              <div class="flex items-center gap-2">
                <span class="w-2 h-2 rounded-full bg-emerald-600"></span>
                <label class="text-sm font-bold text-gray-900">自定义关联偏好与关注方向 (可选)</label>
                <!-- Help button with Question Mark -->
                <button
                  @click="showHelpModal = true"
                  type="button"
                  title="查看填写帮助与示例"
                  class="inline-flex items-center justify-center w-5 h-5 rounded-full bg-emerald-50 hover:bg-emerald-100 text-emerald-600 text-xs font-bold transition-transform hover:scale-110 shadow-sm"
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
              输入您期望重点挖掘的跨表关系或业务流转主线，AI 将定向推导符合业务语义的 Join 条件；留空则全局推断。
            </p>

            <div class="relative">
              <textarea
                v-model="userPrompt"
                rows="3"
                placeholder="例如：重点分析订单主表与用户表、支付流水表的归属与核销关系，挖掘明细子表的外键连接... (点击上方 ? 按钮查看结构化指南与模式)"
                class="w-full text-xs p-3 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 resize-none leading-relaxed transition-all"
              ></textarea>
            </div>

            <!-- Quick Template Chips (Generic Patterns) -->
            <div class="mt-3 flex flex-wrap items-center gap-2">
              <span class="text-[11px] text-gray-400 font-medium">通用关联模式:</span>
              <button
                v-for="item in promptExamples"
                :key="item.title"
                @click="userPrompt = item.prompt"
                type="button"
                class="px-2.5 py-1 bg-gray-50 hover:bg-emerald-50 hover:text-emerald-700 text-gray-600 text-[11px] rounded-lg border border-gray-200/60 transition-colors flex items-center gap-1"
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
              <span>10 分钟内自动去重，自动排除已有实体关系</span>
            </div>

            <button
              @click="handleRecommend"
              :disabled="selectedTableNames.length < 2"
              class="w-full sm:w-auto px-8 py-3 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold shadow-lg shadow-emerald-200 transition-all flex items-center justify-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
              <span>立即开始识别 (已选 {{ selectedTableNames.length }} 张表)</span>
            </button>
          </div>

        </div>

        <!-- Analyzing State：展示逐表元数据分析、剩余表数、累计批次和结果。 -->
        <div v-else-if="analyzing" class="min-h-96 flex flex-col items-center justify-center text-center p-6 space-y-5">
          <div class="relative w-20 h-20 flex items-center justify-center">
            <div class="absolute inset-0 border-4 border-emerald-100 rounded-full"></div>
            <div class="absolute inset-0 border-4 border-emerald-600 rounded-full border-t-transparent animate-spin"></div>
            <div class="flex flex-col items-center justify-center z-10">
              <span class="text-sm font-mono font-bold text-emerald-700">{{ progressPercent }}%</span>
              <span class="text-[10px] text-gray-400">{{ elapsedSeconds }}s</span>
            </div>
          </div>

          <div class="w-full max-w-2xl space-y-2 text-left">
            <div class="flex items-center justify-between gap-3 text-xs">
              <div class="min-w-0">
                <div class="font-bold text-gray-800">{{ progress.message }}</div>
                <div v-if="progress.current_item" class="mt-0.5 truncate font-mono text-[11px] text-emerald-700">
                  当前任务：{{ progress.current_item }}
                </div>
              </div>
              <span class="flex-shrink-0 font-mono text-emerald-700">{{ progressPercent }}%</span>
            </div>
            <div class="h-2 w-full overflow-hidden rounded bg-gray-200">
              <div class="h-full bg-emerald-600 transition-all duration-500" :style="{ width: `${progressPercent}%` }"></div>
            </div>
            <div class="grid grid-cols-2 gap-2 text-center text-[11px] sm:grid-cols-6">
              <div class="rounded border border-gray-200 bg-white px-2 py-2">
                <div class="text-gray-400">候选组进度</div>
                <div class="mt-0.5 font-bold text-gray-800">{{ progress.completed_units || 0 }} / {{ progress.total_units || 0 }}</div>
              </div>
              <div class="rounded border border-gray-200 bg-white px-2 py-2">
                <div class="text-gray-400">剩余候选组</div>
                <div class="mt-0.5 font-bold text-gray-800">{{ progress.remaining_units ?? 0 }}</div>
              </div>
              <div class="rounded border border-gray-200 bg-white px-2 py-2">
                <div class="text-gray-400">AI 调用组</div>
                <div class="mt-0.5 font-bold text-gray-800">{{ progress.batch_count || 0 }}</div>
              </div>
              <div class="rounded border border-gray-200 bg-white px-2 py-2">
                <div class="text-gray-400">候选表对</div>
                <div class="mt-0.5 font-bold text-gray-800">{{ progress.completed_pair_count || 0 }} / {{ progress.candidate_pair_count || 0 }}</div>
              </div>
              <div class="rounded border border-gray-200 bg-white px-2 py-2">
                <div class="text-gray-400">累计关系</div>
                <div class="mt-0.5 font-bold text-gray-800">{{ progress.result_count || 0 }}</div>
              </div>
              <div class="rounded border border-gray-200 bg-white px-2 py-2">
                <div class="text-gray-400">预计剩余</div>
                <div class="mt-0.5 font-bold text-gray-800">{{ estimatedRemainingText }}</div>
              </div>
            </div>
          </div>

          <div class="max-w-2xl rounded border border-blue-200 bg-blue-50 px-3 py-2 text-left text-[11px] leading-relaxed text-blue-900">
            后端先读取数据库外键约束等结构化元数据，能确认的关系只调用一次 AI 生成业务描述；未确认候选按候选组交给 AI 复核，每个表对只推导一次。整个过程仅分析表结构与字段元数据，不查询业务数据行。
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

        <!-- Result List & ER Graph (带 Tab 页切换) -->
        <div v-else class="space-y-3.5">
          <!-- 结果顶部工具栏：Tab 切换、标题、去重标记、排序/ER图提示、全选/取消全选 -->
          <div class="flex flex-wrap items-center justify-between gap-3 pb-2 border-b border-gray-100">
            <div class="flex items-center gap-3">
              <!-- Tab 切换按钮 -->
              <div class="flex items-center bg-gray-100 p-0.5 rounded-xl border border-gray-200/80">
                <button
                  type="button"
                  @click="resultTab = 'list'"
                  class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all"
                  :class="resultTab === 'list' ? 'bg-white text-emerald-700 shadow-xs' : 'text-gray-500 hover:text-gray-700'"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 10h16M4 14h16M4 18h16"/></svg>
                  <span>列表卡片 ({{ recommendations.length }})</span>
                </button>
                <button
                  type="button"
                  @click="resultTab = 'graph'"
                  class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all"
                  :class="resultTab === 'graph' ? 'bg-white text-emerald-700 shadow-xs' : 'text-gray-500 hover:text-gray-700'"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
                  <span>关系 ER 图</span>
                </button>
              </div>

              <span class="text-[11px] text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full font-bold border border-emerald-200/60 hidden sm:inline-block">
                已完成去重
              </span>
              <!-- 部分批次失败时保留已生成结果，并明确提示结果并非完整推导。 -->
              <span
                v-if="runStatus === 'interrupted'"
                class="text-[11px] text-amber-800 bg-amber-50 px-2 py-0.5 rounded-full font-bold border border-amber-200"
              >
                生成中断，以下为已完成结果
              </span>
            </div>

            <div class="flex items-center gap-3 text-xs">
              <!-- 列表模式下的排序控制 -->
              <div v-if="resultTab === 'list'" class="flex items-center gap-1 bg-gray-50 p-1 rounded-lg border border-gray-200/70">
                <span class="text-[11px] text-gray-400 pl-1 font-medium">排序:</span>
                <button 
                  @click="sortKey = 'confidence'" 
                  :class="[sortKey === 'confidence' ? 'bg-white text-emerald-700 font-bold shadow-xs' : 'text-gray-500 hover:text-gray-700', 'px-2 py-0.5 rounded text-[11px] transition-all']"
                >
                  置信度
                </button>
                <button 
                  @click="sortKey = 'source_table'" 
                  :class="[sortKey === 'source_table' ? 'bg-white text-emerald-700 font-bold shadow-xs' : 'text-gray-500 hover:text-gray-700', 'px-2 py-0.5 rounded text-[11px] transition-all']"
                >
                  源表
                </button>
                <button 
                  @click="sortAsc = !sortAsc" 
                  class="px-1.5 py-0.5 text-gray-400 hover:text-gray-700 text-[11px] font-bold"
                  title="切换升序/降序"
                >
                  {{ sortAsc ? '升序 ↑' : '降序 ↓' }}
                </button>
              </div>

              <!-- ER图模式下的提示 -->
              <div v-else class="text-[11px] text-gray-400 flex items-center gap-1">
                <span class="flex items-center gap-1"><LightBulbIcon class="w-3.5 h-3.5" /> 滚轮缩放 · 拖拽平移 · 点击连线切换勾选</span>
              </div>

              <div class="h-3 w-px bg-gray-200"></div>

              <!-- 批量选择控制 -->
              <button @click="selectedIndices = recommendations.map((_, i) => i)" class="text-xs text-emerald-600 font-bold hover:text-emerald-700 transition-colors">
                全选
              </button>
              <button @click="selectedIndices = []" class="text-xs text-gray-400 font-medium hover:text-gray-600 transition-colors">
                取消全选
              </button>
            </div>
          </div>

          <!-- TAB 1: 单列通栏卡片列表 -->
          <div v-show="resultTab === 'list'" class="space-y-2.5 max-h-[50vh] overflow-y-auto pr-1">
            <div 
              v-for="item in sortedRecommendations" 
              :key="item._originalIndex" 
              @click="toggleSelection(item._originalIndex)"
              class="border rounded-xl p-3.5 transition-all cursor-pointer bg-white"
              :class="selectedIndices.includes(item._originalIndex) 
                ? 'border-emerald-500 bg-emerald-50/20 ring-2 ring-emerald-500/10 shadow-xs' 
                : 'border-gray-200/80 hover:border-emerald-300 hover:bg-emerald-50/5 hover:shadow-xs'"
            >
              <div class="flex items-start justify-between gap-3">
                <!-- 左侧复选框 -->
                <div class="pt-0.5 shrink-0 flex items-center" @click.stop="toggleSelection(item._originalIndex)">
                  <input 
                    type="checkbox" 
                    :checked="selectedIndices.includes(item._originalIndex)"
                    class="w-4 h-4 rounded text-emerald-600 focus:ring-emerald-500 border-gray-300 cursor-pointer"
                  />
                </div>

                <!-- 中间内容流式排版 -->
                <div class="flex-1 min-w-0 space-y-2">
                  <!-- Header: 源表 -> 目标表 + 关系类型 + 置信度 -->
                  <div class="flex flex-wrap items-center gap-2">
                    <!-- 源表 -->
                    <span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-purple-50 border border-purple-100 text-purple-800 font-mono text-xs font-bold shrink-0">
                      <KeyIcon class="w-3 h-3 text-purple-400" />
                      <span>{{ item.source_table }}</span>
                    </span>

                    <!-- 箭头与连接 -->
                    <div class="flex items-center gap-1 text-gray-400 shrink-0">
                      <svg class="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/>
                      </svg>
                    </div>

                    <!-- 目标表 -->
                    <span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-emerald-50 border border-emerald-100 text-emerald-800 font-mono text-xs font-bold shrink-0">
                      <LinkIcon class="w-3 h-3 text-emerald-500" />
                      <span>{{ item.target_table }}</span>
                    </span>

                    <!-- 关系类型 -->
                    <span 
                      class="px-2 py-0.5 text-[10px] font-bold rounded border"
                      :class="item.relation_type === 'one_to_one' ? 'bg-blue-50 text-blue-700 border-blue-200' : (item.relation_type === 'one_to_many' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-orange-50 text-orange-700 border-orange-200')"
                    >
                      {{ relationTypeLabel(item.relation_type) }}
                    </span>

                    <!-- 判定来源徽章：区分外键确认与 AI 推断 -->
                    <span
                      v-if="item.source"
                      class="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-bold rounded border"
                      :class="[sourceBadge(item.source).bg, sourceBadge(item.source).text, sourceBadge(item.source).border]"
                    >
                      {{ sourceBadge(item.source).label }}
                    </span>

                    <!-- 置信度徽章 -->
                    <span 
                      class="inline-flex items-center gap-1.5 px-2.5 py-0.5 text-[10px] font-bold rounded-full border ml-auto"
                      :class="[confidenceStyle(item.confidence || 0).bg, confidenceStyle(item.confidence || 0).text, confidenceStyle(item.confidence || 0).border]"
                    >
                      <span class="w-1.5 h-1.5 rounded-full" :class="confidenceStyle(item.confidence || 0).dot"></span>
                      {{ Math.round((item.confidence || 0) * 100) }}% · {{ confidenceStyle(item.confidence || 0).label }}
                    </span>
                  </div>

                  <!-- Join Condition -->
                  <div class="bg-gray-50 border border-gray-100 rounded-lg px-3 py-1.5 font-mono text-xs text-gray-800 flex items-center gap-2">
                    <span class="text-gray-400 font-bold text-[11px] shrink-0">JOIN:</span>
                    <span class="font-bold text-indigo-700 truncate select-all">{{ item.condition }}</span>
                  </div>

                  <!-- Description -->
                  <p v-if="item.description" class="text-xs text-gray-500 leading-relaxed">
                    📝 {{ item.description }}
                  </p>
                </div>
              </div>
            </div>
          </div>

          <!-- TAB 2: 交互式 ER 图画布 -->
          <div v-show="resultTab === 'graph'" class="relative h-[50vh] rounded-2xl border border-gray-200/90 bg-gradient-to-br from-slate-50/50 via-white to-emerald-50/20 overflow-hidden shadow-inner">
            <div ref="chartContainer" class="w-full h-full"></div>
            <!-- Canvas Overlay Legend -->
            <div class="absolute bottom-3 left-3 bg-white/90 backdrop-blur-xs p-2 rounded-xl border border-gray-200/80 text-[10px] text-gray-500 flex items-center gap-3 pointer-events-none shadow-xs">
              <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-indigo-600"></span> 事实表</span>
              <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-emerald-600"></span> 维度表</span>
              <span class="flex items-center gap-1"><span class="w-3 h-0.5 bg-emerald-600"></span> 已选推荐</span>
              <span class="flex items-center gap-1"><span class="w-3 h-0.5 bg-slate-300 border-dashed border-t"></span> 未勾选</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div v-if="recommendations.length > 0 && !analyzing" class="p-5 border-t border-gray-100 bg-white/90 backdrop-blur flex justify-between items-center">
        <button 
          v-if="currentTraceId"
          @click="showLogs = true"
          class="text-xs text-gray-400 hover:text-emerald-600 flex items-center gap-1.5 transition-colors"
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
            class="px-7 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-bold shadow-lg shadow-emerald-200 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <svg v-if="saving" class="animate-spin h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            {{ saving ? '入库中...' : `采纳选中关系 (${selectedIndices.length})` }}
          </button>
        </div>
      </div>

    </div>
  </div>

  <!-- Help & Guidelines Modal (问号弹窗) -->
  <div 
    v-if="showHelpModal" 
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fade-in"
    @click.self="showHelpModal = false"
  >
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden border border-gray-100">
      
      <!-- Modal Header -->
      <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gradient-to-r from-emerald-50/60 to-teal-50/60">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg bg-emerald-600 flex items-center justify-center text-white text-sm font-bold shadow-sm">
            ?
          </div>
          <div>
            <h3 class="text-sm font-bold text-gray-900">实体关系智能推导：指南与说明</h3>
            <p class="text-[11px] text-gray-500">掌握结构化偏好与推导规则，让 AI 精确建立高质量实体数据关联</p>
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
        
        <!-- 1. 填与不填的区别 -->
        <div class="p-4 bg-emerald-50/60 border border-emerald-100 rounded-xl space-y-2.5">
          <div class="font-bold text-emerald-950 flex items-center gap-1.5">
            <svg class="w-4 h-4 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            填与不填的区别？具体会带来哪些影响？
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
            <div class="p-3 bg-white rounded-lg border border-emerald-100/80 space-y-1">
              <div class="font-bold text-emerald-950 flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                填写自定义偏好时
              </div>
              <ul class="text-[11px] text-gray-600 list-disc list-inside space-y-0.5">
                <li><strong>定向挖掘主线</strong>：优先分析您指定的业务链路（如订单与核销、用户与审批单）。</li>
                <li><strong>处理非标准外键</strong>：若某些表的外键字段未以 `_id` 结尾，可通过偏好提示告知 AI 语义关系。</li>
                <li><strong>提升置信度</strong>：避免大模型在全量弱关联中迷失，推导准确率显著提高。</li>
              </ul>
            </div>

            <div class="p-3 bg-white rounded-lg border border-emerald-100/80 space-y-1">
              <div class="font-bold text-emerald-950 flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full bg-gray-400"></span>
                留空（不填）时
              </div>
              <ul class="text-[11px] text-gray-600 list-disc list-inside space-y-0.5">
                <li><strong>全局 Schema 分析</strong>：AI 将自动比对所选所有表的主外键命名与字段注释。</li>
                <li><strong>标准规则匹配</strong>：基于 `xxx_id = id`、`code`、业务术语等通用模式推断高置信度关系。</li>
                <li><strong>适合冷启动</strong>：适合初次导入数据集后快速建立基础模型关联。</li>
              </ul>
            </div>
          </div>
        </div>

        <!-- 2. 怎么填？ -->
        <div class="p-4 bg-gray-50 border border-gray-200 rounded-xl space-y-2">
          <div class="font-bold text-gray-900 flex items-center gap-1.5">
            <PencilIcon class="w-4 h-4 text-gray-500" /> 怎么填？推荐关联描述句式
          </div>
          <p class="text-[11px] text-gray-500">
            您可以描述具体的业务流转场景，例如：
          </p>
          <div class="space-y-1.5 font-mono text-[11px] text-gray-700 bg-white p-3 rounded-lg border border-gray-200">
            <div>💡 <em>“重点分析 orders 订单表与 users 客户表、order_items 订单明细表的级联关联。”</em></div>
            <div>💡 <em>“分析 assets 资产表与 departments 部门表在所属组织上的外键映射关系。”</em></div>
          </div>
        </div>

        <!-- 3. 通用场景示例（一键应用） -->
        <div>
          <h4 class="font-bold text-gray-800 mb-2.5 flex items-center justify-between">
            <span class="flex items-center gap-1.5">
              <LightBulbIcon class="w-4 h-4 text-amber-500" /> 常用关联模式示例
            </span>
            <span class="text-[11px] font-normal text-gray-400">点击卡片可直接一键填入输入框</span>
          </h4>

          <div class="space-y-2.5">
            <div 
              v-for="(item, idx) in promptExamples" 
              :key="idx"
              @click="applyPromptExample(item.prompt)"
              class="p-3.5 rounded-xl border border-gray-200 hover:border-emerald-400 bg-white hover:bg-emerald-50/30 transition-all cursor-pointer group shadow-sm"
            >
              <div class="flex items-center justify-between mb-1">
                <div class="flex items-center gap-2">
                  <span class="font-bold text-gray-900 group-hover:text-emerald-700 transition-colors">{{ item.title }}</span>
                  <span class="text-[10px] px-2 py-0.2 bg-gray-100 group-hover:bg-emerald-100 text-gray-600 group-hover:text-emerald-800 rounded font-medium">{{ item.badge }}</span>
                </div>
                <span class="text-[10px] text-emerald-600 font-semibold group-hover:underline flex items-center gap-1">
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
