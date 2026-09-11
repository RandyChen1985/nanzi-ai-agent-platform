<script setup lang="ts">
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import Modal from '@/components/Modal.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import { metadataApi, type MetaDriftAlert } from '@/api/metadata'
import { createSseLineParser } from '@/utils/chartRenderer'

const props = defineProps<{
  show?: boolean
  visible?: boolean
  isGlobal?: boolean
  datasets?: any[]
  dataset?: any
  datasetId?: number
  datasetName?: string
  dataSource?: string
  datasetStatus?: number
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'resolved'): void
}>()

const { showToast } = useToast()
const { hasPermission } = useUser()
const canEdit = computed(() => hasPermission('element:metadata:edit'))

const isVisible = computed(() => props.show ?? props.visible ?? false)
const isGlobalMode = computed(() => Boolean(props.isGlobal || (!props.dataset && !props.datasetId)))

const targetDatasetId = computed(() => props.datasetId || props.dataset?.id || 0)
const targetDatasetName = computed(() => props.datasetName || props.dataset?.display_name || props.dataset?.name || '')
const targetDatasetStatus = computed(() => {
  if (typeof props.datasetStatus === 'number') return props.datasetStatus
  if (typeof props.dataset?.status === 'number') return props.dataset.status
  return 1
})
const isDatasetDisabled = computed(() => !isGlobalMode.value && targetDatasetStatus.value !== 1)
const targetDataSource = computed(() => props.dataSource || props.dataset?.data_source || '')

const loading = ref(false)
const alerts = ref<MetaDriftAlert[]>([])
const activeTab = ref<'pending' | 'all'>('pending')
const selectedDatasetFilter = ref<number | 'all'>('all')
const processingId = ref<number | null>(null)
const expandedSamples = ref<Record<number, boolean>>({})

const isBatchProcessing = ref(false)

// --- 流式巡检控制台状态 ---
interface LogEntry {
  event: string
  stage: string
  message: string
  progress?: number
  error_detail?: string
  elapsed_ms?: number
}

const showInspectionConsole = ref(false)
const isInspecting = ref(false)
const inspectionStatus = ref<'idle' | 'running' | 'completed' | 'failed' | 'disconnected'>('idle')
const inspectionProgress = ref(0)
const inspectionStage = ref('')
const inspectionMessage = ref('')
const inspectionLogs = ref<LogEntry[]>([])
const inspectionElapsedSeconds = ref(0)
const inspectionLogRef = ref<HTMLElement | null>(null)

let inspectionAbortController: AbortController | null = null
let inspectionTimerInterval: ReturnType<typeof setInterval> | null = null

const startInspectionTimer = () => {
  inspectionElapsedSeconds.value = 0
  clearInterval(inspectionTimerInterval!)
  inspectionTimerInterval = setInterval(() => {
    if (inspectionStatus.value === 'running') {
      inspectionElapsedSeconds.value++
    }
  }, 1000)
}

const stopInspectionTimer = () => {
  if (inspectionTimerInterval) {
    clearInterval(inspectionTimerInterval)
    inspectionTimerInterval = null
  }
}

const scrollInspectionToBottom = () => {
  nextTick(() => {
    if (inspectionLogRef.value) {
      inspectionLogRef.value.scrollTop = inspectionLogRef.value.scrollHeight
    }
  })
}

const startInspection = async () => {
  if (!isGlobalMode.value && !targetDatasetId.value) return

  showInspectionConsole.value = true
  inspectionAbortController?.abort()
  const controller = new AbortController()
  inspectionAbortController = controller

  isInspecting.value = true
  inspectionStatus.value = 'running'
  inspectionProgress.value = 0
  inspectionStage.value = 'queued'
  inspectionMessage.value = isGlobalMode.value ? '正在启动全库批量巡检任务...' : '正在启动单数据集巡检任务...'
  inspectionLogs.value = []
  startInspectionTimer()

  try {
    let taskId = ''
    let streamUrl = ''

    if (isGlobalMode.value) {
      const res = await metadataApi.triggerAllDatasetsInspection()
      taskId = res.data.task_id
      streamUrl = `/api/portal/metadata/inspect/${taskId}/events`
    } else {
      const dId = targetDatasetId.value
      const res = await metadataApi.triggerInspection(dId)
      taskId = res.data.task_id
      streamUrl = `/api/portal/metadata/datasets/${dId}/inspect/${taskId}/events`
    }

    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    const apiKey = localStorage.getItem('api_key')
    const token = localStorage.getItem('yovole_token') || localStorage.getItem('admin_token')
    if (apiKey) headers['X-API-Key'] = apiKey
    else if (token) headers.Authorization = `Bearer ${token}`

    const response = await fetch(streamUrl, {
      headers,
      credentials: 'include',
      signal: controller.signal,
    })

    if (!response.ok) throw new Error(`巡检任务连接失败（${response.status}）`)
    if (!response.body) throw new Error('巡检服务未返回数据流')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    const parser = createSseLineParser()
    let terminalReceived = false

    const applyEvent = (dataStr: string) => {
      if (!dataStr || dataStr === '[DONE]') return
      try {
        const item: LogEntry = JSON.parse(dataStr)
        inspectionLogs.value.push(item)
        if (item.stage) inspectionStage.value = item.stage
        if (item.message) inspectionMessage.value = item.message
        if (typeof item.progress === 'number') inspectionProgress.value = item.progress

        if (item.event === 'completed' || item.event === 'failed') {
          terminalReceived = true
          inspectionStatus.value = item.event
          isInspecting.value = false
          stopInspectionTimer()
          if (item.event === 'completed') {
            showToast(isGlobalMode.value ? '全量巡检完成，已刷新全局差异大盘' : '物理巡检完成，已刷新最新差异列表', 'success')
            fetchAlerts()
            emit('resolved')
          }
        }
        scrollInspectionToBottom()
      } catch (e) {
        console.warn('Failed to parse SSE line', dataStr, e)
      }
    }

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      for (const dataStr of parser.feed(decoder.decode(value, { stream: true }))) {
        applyEvent(dataStr)
      }
      if (terminalReceived) break
    }
    for (const dataStr of parser.flush()) {
      applyEvent(dataStr)
    }

    if (!terminalReceived && !controller.signal.aborted) {
      inspectionStatus.value = 'disconnected'
      inspectionMessage.value = '巡检日志连接意外中断'
      isInspecting.value = false
      stopInspectionTimer()
    }
  } catch (err: any) {
    if (controller.signal.aborted) return
    inspectionStatus.value = 'failed'
    inspectionMessage.value = err.message || '巡检启动失败'
    isInspecting.value = false
    stopInspectionTimer()
    showToast(err.message || '巡检启动失败', 'error')
  }
}

const abortInspection = () => {
  inspectionAbortController?.abort()
  inspectionAbortController = null
  isInspecting.value = false
  inspectionStatus.value = 'failed'
  inspectionMessage.value = '已由管理员中止巡检'
  stopInspectionTimer()
}

onUnmounted(() => {
  inspectionAbortController?.abort()
  inspectionAbortController = null
  stopInspectionTimer()
})

// 确认弹窗状态配置
interface ConfirmState {
  title: string
  message: string
  confirmText: string
  cancelText?: string
  type: 'danger' | 'primary' | 'warning'
  loading: boolean
  action: () => Promise<void>
}

const confirmModal = ref<ConfirmState | null>(null)

const triggerConfirm = (config: {
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  type?: 'danger' | 'primary' | 'warning'
  action: () => Promise<void>
}) => {
  confirmModal.value = {
    title: config.title,
    message: config.message,
    confirmText: config.confirmText || '确认',
    cancelText: config.cancelText || '取消',
    type: config.type || 'primary',
    loading: false,
    action: config.action,
  }
}

const handleConfirmModalConfirm = async () => {
  if (!confirmModal.value) return
  confirmModal.value.loading = true
  try {
    await confirmModal.value.action()
    confirmModal.value = null
  } catch (err: any) {
    showToast(err.message || '操作失败', 'error')
    if (confirmModal.value) {
      confirmModal.value.loading = false
    }
  }
}

const pendingNewCount = computed(() => {
  return alerts.value.filter(a => a.status === 0 && a.drift_type === 'new_in_db').length
})

const pendingMissingCount = computed(() => {
  return alerts.value.filter(a => a.status === 0 && a.drift_type === 'missing_in_db').length
})

const fetchAlerts = async () => {
  loading.value = true
  try {
    const statusParam = activeTab.value === 'pending' ? 0 : undefined
    if (isGlobalMode.value) {
      const filterDs = selectedDatasetFilter.value === 'all' ? undefined : Number(selectedDatasetFilter.value)
      const res = await metadataApi.getAllDriftAlerts({
        status: statusParam,
        dataset_id: filterDs,
      })
      alerts.value = res.data || []
    } else {
      const dId = targetDatasetId.value
      if (!dId) return
      const res = await metadataApi.getDatasetDriftAlerts(dId, statusParam)
      alerts.value = res.data || []
    }
  } catch (err) {
    console.error('Failed to fetch drift alerts', err)
  } finally {
    loading.value = false
  }
}

const toggleSample = (id: number) => {
  expandedSamples.value[id] = !expandedSamples.value[id]
}

const handleResolve = (alert: MetaDriftAlert, action: 'drop_column' | 'add_column' | 'ignore') => {
  if (!canEdit.value) {
    showToast('需具备数据集编辑权限方可执行处置操作', 'warning')
    return
  }

  let title = '确认操作'
  let message = ''
  let confirmText = '确认'
  let type: 'danger' | 'primary' | 'warning' = 'primary'

  const datasetLabel = alert.dataset_name ? `【${alert.dataset_name}】` : ''

  if (action === 'drop_column') {
    title = '确认下线字段'
    message = `确认从元数据${datasetLabel}中下线字段【${alert.table_name}.${alert.column_name}】？\n下线后，AI 编排和查询将不再使用该字段。`
    confirmText = '确认下线'
    type = 'danger'
  } else if (action === 'add_column') {
    title = '确认收录字段'
    message = `确认将物理库新增字段【${alert.table_name}.${alert.column_name}】录入元数据${datasetLabel}？\n收录后，该字段将立即向 AI 语义检索与查询开放。`
    confirmText = '确认收录'
    type = 'primary'
  } else {
    title = '确认忽略漂移'
    message = `确认忽略该字段【${alert.table_name}.${alert.column_name}】的漂移提醒？`
    confirmText = '确认忽略'
    type = 'warning'
  }

  triggerConfirm({
    title,
    message,
    confirmText,
    type,
    action: async () => {
      processingId.value = alert.id
      try {
        const res = await metadataApi.resolveDriftAlert(alert.id, action)
        showToast(res.data?.message || '操作成功', 'success')
        await fetchAlerts()
        emit('resolved')
      } finally {
        processingId.value = null
      }
    }
  })
}

const handleBatchResolve = (action: 'drop_column' | 'add_column' | 'ignore', driftType?: string) => {
  if (!canEdit.value) {
    showToast('需具备数据集编辑权限方可执行批量处置操作', 'warning')
    return
  }

  const count = driftType === 'new_in_db' ? pendingNewCount.value : driftType === 'missing_in_db' ? pendingMissingCount.value : alerts.value.length
  if (count === 0) return

  let title = '批量操作'
  let message = ''
  let confirmText = '确认批量操作'
  let type: 'danger' | 'primary' | 'warning' = 'primary'

  if (action === 'add_column') {
    title = '批量收录新增字段'
    message = `确认将当前视图中全部 ${count} 个物理新增字段一键录入到元数据中？`
    confirmText = `一键收录 (${count})`
    type = 'primary'
  } else if (action === 'drop_column') {
    title = '批量下线缺失字段'
    message = `确认将当前视图中全部 ${count} 个物理库已缺失的字段一键从元数据中下线？`
    confirmText = `一键下线 (${count})`
    type = 'danger'
  } else {
    title = '批量忽略告警'
    message = `确认批量忽略当前视图中 ${count} 项漂移告警？`
    confirmText = `批量忽略 (${count})`
    type = 'warning'
  }

  triggerConfirm({
    title,
    message,
    confirmText,
    type,
    action: async () => {
      isBatchProcessing.value = true
      try {
        if (isGlobalMode.value) {
          const alertIds = alerts.value
            .filter(a => a.status === 0 && (!driftType || a.drift_type === driftType))
            .map(a => a.id)
          const res = await metadataApi.batchResolveAllDriftAlerts({
            action,
            drift_type: driftType,
            alert_ids: alertIds.length > 0 ? alertIds : undefined,
          })
          showToast(res.data?.message || '批量操作成功', 'success')
        } else {
          const res = await metadataApi.batchResolveDriftAlerts(targetDatasetId.value, {
            action,
            drift_type: driftType,
          })
          showToast(res.data?.message || '批量操作成功', 'success')
        }
        await fetchAlerts()
        emit('resolved')
      } finally {
        isBatchProcessing.value = false
      }
    }
  })
}

watch(
  () => [isVisible.value, activeTab.value, targetDatasetId.value, selectedDatasetFilter.value],
  ([showVal]) => {
    if (showVal) {
      fetchAlerts()
    }
  }
)
</script>

<template>
  <Modal
    :show="isVisible"
    :title="isGlobalMode ? '⚡ 全局巡检' : `⚡ Schema 巡检与差异治理 - ${targetDatasetName}`"
    :size="isGlobalMode ? 'max-w-3xl' : 'max-w-2xl'"
    @close="emit('close')"
  >
    <div class="space-y-4">
      <!-- 物理巡检控制台卡片（支持展开流式日志；大盘模式与单库模式适配） -->
      <div class="p-3 bg-gradient-to-r from-slate-50 to-amber-50/40 dark:from-slate-800/80 dark:to-amber-950/20 rounded-xl border border-slate-200/80 dark:border-slate-700/80 space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="space-y-0.5">
            <div class="flex items-center gap-2">
              <span class="text-xs font-bold text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
                <span>⚡</span> {{ isGlobalMode ? '全量数据集批量巡检' : 'Schema 物理一致性巡检' }}
              </span>
              <span
                v-if="!isGlobalMode"
                class="px-1.5 py-0.5 text-[10px] rounded font-semibold"
                :class="isDatasetDisabled ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300 border border-amber-200 dark:border-amber-800' : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800'"
              >
                {{ isDatasetDisabled ? '数据集已禁用 (维护期)' : '数据集已启用' }}
              </span>
              <span
                v-else
                class="px-1.5 py-0.5 text-[10px] rounded font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 border border-blue-200 dark:border-blue-800"
              >
                全库统筹大盘
              </span>
              <span v-if="!isGlobalMode && targetDataSource" class="text-[10px] text-slate-400 font-mono">
                ({{ targetDataSource }})
              </span>
            </div>
            <p class="text-[11px] text-slate-500 dark:text-slate-400">
              <template v-if="isGlobalMode">
                一键发起全库所有物理数据源与元数据的 DDL 差异比对，统一汇聚全量结构漂移与运行时反哺差异。
              </template>
              <template v-else>
                {{ isDatasetDisabled ? '当前数据集处于维护隔离期（智能体不可调用），支持直接开展安全的物理结构探测与治理。' : '直连底层物理库实时比对 DDL，自动检出新增或已删除的字段。' }}
              </template>
            </p>
          </div>

          <div class="flex items-center gap-2">
            <!-- 查看/收起巡检终端 -->
            <button
              v-if="inspectionLogs.length > 0 || isInspecting"
              type="button"
              class="px-2.5 py-1 text-xs rounded-lg border border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 transition-colors cursor-pointer"
              @click="showInspectionConsole = !showInspectionConsole"
            >
              {{ showInspectionConsole ? '收起终端日志' : '查看实时日志' }}
            </button>

            <!-- 中止巡检 -->
            <button
              v-if="isInspecting"
              type="button"
              class="px-2.5 py-1 text-xs font-semibold rounded-lg bg-rose-100 hover:bg-rose-200 text-rose-700 dark:bg-rose-900/50 dark:text-rose-300 transition-colors cursor-pointer"
              @click="abortInspection"
            >
              中止
            </button>

            <!-- 立即执行巡检按钮 -->
            <button
              v-else
              type="button"
              :title="isGlobalMode ? '对全库所有数据集逐一执行物理结构一致性巡检' : '直连物理数据库执行表结构完整巡检'"
              class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-amber-600 hover:bg-amber-700 text-white shadow-2xs transition-colors flex items-center gap-1.5 cursor-pointer"
              @click="startInspection"
            >
              <span>⚡</span> {{ isGlobalMode ? '🚀 一键全量巡检 (全库)' : '立即执行巡检' }}
            </button>
          </div>
        </div>

        <!-- 嵌入式流式巡检控制台 -->
        <div v-if="showInspectionConsole" class="rounded-lg overflow-hidden border border-slate-800 bg-slate-950 text-slate-200 shadow-inner mt-2">
          <div class="px-3 py-1.5 bg-slate-900 border-b border-slate-800 flex items-center justify-between text-xs">
            <div class="flex items-center gap-2">
              <span class="w-2 h-2 rounded-full" :class="{
                'bg-amber-400 animate-pulse': inspectionStatus === 'running',
                'bg-emerald-400': inspectionStatus === 'completed',
                'bg-rose-400': inspectionStatus === 'failed' || inspectionStatus === 'disconnected',
                'bg-slate-500': inspectionStatus === 'idle'
              }"></span>
              <span class="font-mono text-[11px] font-medium text-slate-300">
                {{ isGlobalMode ? '全量巡检实时流' : '巡检实时流' }} ({{ inspectionElapsedSeconds }}s)
              </span>
              <span v-if="inspectionStage" class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-amber-300 font-mono">
                {{ inspectionStage }}
              </span>
            </div>
            <div class="flex items-center gap-2">
              <span class="font-mono text-[11px] text-slate-400">{{ inspectionProgress }}%</span>
              <button
                type="button"
                class="text-slate-400 hover:text-slate-200 text-xs px-1 cursor-pointer"
                @click="showInspectionConsole = false"
              >
                ✕
              </button>
            </div>
          </div>

          <!-- 进度条 -->
          <div class="h-0.5 w-full bg-slate-900">
            <div
              class="h-full transition-all duration-300"
              :class="inspectionStatus === 'failed' ? 'bg-rose-500' : 'bg-amber-500'"
              :style="{ width: `${inspectionProgress}%` }"
            ></div>
          </div>

          <!-- 日志输出视窗 -->
          <div ref="inspectionLogRef" class="p-2.5 max-h-48 overflow-y-auto font-mono text-[11px] space-y-1 custom-scrollbar leading-relaxed">
            <div v-if="inspectionLogs.length === 0" class="text-slate-500 italic">
              等待巡检任务输出...
            </div>
            <div
              v-for="(log, idx) in inspectionLogs"
              :key="idx"
              class="flex items-start gap-1.5"
              :class="{
                'text-rose-400': log.event === 'failed',
                'text-emerald-400': log.event === 'completed',
                'text-amber-300': log.stage === 'scanning' || log.stage === 'diff_analysis',
                'text-slate-300': !['failed', 'completed'].includes(log.event) && !['scanning', 'diff_analysis'].includes(log.stage)
              }"
            >
              <span class="text-slate-600 select-none">&gt;</span>
              <span class="flex-1 break-all">{{ log.message }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Tab 切换 & 筛选器 -->
      <div class="flex flex-wrap items-center justify-between border-b border-slate-200 dark:border-slate-700 pb-2 gap-2">
        <div class="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            class="px-3 py-1.5 text-xs font-medium rounded-lg transition-colors"
            :class="activeTab === 'pending'
              ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200 font-semibold'
              : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800'"
            @click="activeTab = 'pending'"
          >
            待处理项 ({{ activeTab === 'pending' ? alerts.length : '待核对' }})
          </button>
          <button
            type="button"
            class="px-3 py-1.5 text-xs font-medium rounded-lg transition-colors"
            :class="activeTab === 'all'
              ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200 font-semibold'
              : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800'"
            @click="activeTab = 'all'"
          >
            全部历史
          </button>

          <!-- 全局模式下：数据集筛选下拉框 -->
          <div v-if="isGlobalMode && props.datasets && props.datasets.length > 0" class="flex items-center gap-1.5 ml-2">
            <span class="text-xs text-slate-400">筛选库:</span>
            <select
              v-model="selectedDatasetFilter"
              class="text-xs py-1 px-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-amber-500"
            >
              <option value="all">全部数据集 ({{ props.datasets.length }} 个)</option>
              <option v-for="d in props.datasets" :key="d.id" :value="d.id">
                {{ d.display_name || d.name }}
              </option>
            </select>
          </div>
        </div>

        <div class="text-xs flex items-center gap-1.5">
          <span v-if="!canEdit" class="inline-flex items-center gap-1 bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300 px-2 py-0.5 rounded border border-amber-200 dark:border-amber-800 text-[11px]">
            🔒 仅浏览模式 (需编辑权限进行处置)
          </span>
          <span v-else class="text-slate-500 text-[11px]">
            系统检测建议 · 需管理员确认操作
          </span>
        </div>
      </div>

      <!-- 批量快捷操作栏（仅待处理 Tab 且存在待处理项时展示） -->
      <div
        v-if="activeTab === 'pending' && alerts.length > 0 && (pendingNewCount > 0 || pendingMissingCount > 0)"
        class="flex flex-wrap items-center justify-between gap-2 p-2.5 bg-slate-50 dark:bg-slate-800/60 rounded-xl border border-slate-200 dark:border-slate-700/80 text-xs"
      >
        <span class="text-slate-600 dark:text-slate-300 font-medium">
          批量操作:
        </span>
        <div class="flex items-center gap-2 flex-wrap">
          <button
            v-if="pendingNewCount > 0"
            type="button"
            :disabled="!canEdit || isBatchProcessing"
            :title="!canEdit ? '需具备数据集编辑权限方可操作' : undefined"
            class="px-2.5 py-1 font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white shadow-2xs transition-colors flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer text-xs"
            @click="handleBatchResolve('add_column', 'new_in_db')"
          >
            <span>📥</span> 一键收录全部新增字段 ({{ pendingNewCount }})
          </button>
          <button
            v-if="pendingMissingCount > 0"
            type="button"
            :disabled="!canEdit || isBatchProcessing"
            :title="!canEdit ? '需具备数据集编辑权限方可操作' : undefined"
            class="px-2.5 py-1 font-semibold rounded-lg bg-rose-600 hover:bg-rose-700 text-white shadow-2xs transition-colors flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer text-xs"
            @click="handleBatchResolve('drop_column', 'missing_in_db')"
          >
            <span>🗑️</span> 一键下线全部缺失字段 ({{ pendingMissingCount }})
          </button>
        </div>
      </div>

      <!-- 列表内容区 -->
      <div v-if="loading" class="py-12 text-center text-xs text-slate-400">
        正在读取 Schema 差异列表...
      </div>

      <div v-else-if="alerts.length === 0" class="py-12 text-center space-y-2">
        <div class="text-3xl">🎉</div>
        <div class="text-sm font-medium text-slate-700 dark:text-slate-300">
          太棒了！当前没有待处理的 Schema 漂移告警
        </div>
        <div class="text-xs text-slate-400 max-w-sm mx-auto">
          若底层表刚刚发生过 DDL 变更，您可以点击「⚡ {{ isGlobalMode ? '全量巡检' : '物理结构巡检' }}」进行主动探测。
        </div>
      </div>

      <div v-else class="space-y-3 max-h-[55vh] overflow-y-auto pr-1 custom-scrollbar">
        <div
          v-for="alert in alerts"
          :key="alert.id"
          class="p-3.5 rounded-xl border transition-all"
          :class="{
            'bg-amber-50/60 dark:bg-amber-950/20 border-amber-200 dark:border-amber-800/60': alert.status === 0 && alert.drift_type === 'missing_in_db',
            'bg-sky-50/60 dark:bg-sky-950/20 border-sky-200 dark:border-sky-800/60': alert.status === 0 && alert.drift_type === 'new_in_db',
            'bg-slate-50 dark:bg-slate-800/40 border-slate-200 dark:border-slate-700 opacity-70': alert.status !== 0,
          }"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="space-y-1 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <!-- 全局模式下展示所属数据集 -->
                <button
                  v-if="isGlobalMode"
                  type="button"
                  title="点击仅筛选该数据集"
                  class="px-2 py-0.5 text-[10px] font-semibold rounded-md bg-blue-50 hover:bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 border border-blue-200 dark:border-blue-800 transition-colors flex items-center gap-1 cursor-pointer"
                  @click="selectedDatasetFilter = alert.dataset_id"
                >
                  <span>📦</span> {{ alert.dataset_name || `数据集 #${alert.dataset_id}` }}
                </button>

                <span class="font-mono font-semibold text-sm text-slate-900 dark:text-slate-100">
                  {{ alert.table_name }}.<span class="text-amber-600 dark:text-amber-400 underline decoration-amber-400/50 underline-offset-2">{{ alert.column_name }}</span>
                </span>

                <span
                  v-if="alert.drift_type === 'missing_in_db'"
                  class="px-2 py-0.5 text-[11px] font-semibold rounded bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300 border border-rose-200 dark:border-rose-800"
                >
                  物理库已缺失
                </span>
                <span
                  v-else-if="alert.drift_type === 'new_in_db'"
                  class="px-2 py-0.5 text-[11px] font-semibold rounded bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300 border border-sky-200 dark:border-sky-800"
                >
                  物理库新增字段
                </span>

                <span class="px-1.5 py-0.5 text-[10px] rounded bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                  {{ alert.source === 'runtime' ? '⚡ 运行时物理报错反哺' : '🔍 结构巡检发现' }}
                </span>

                <span
                  v-if="alert.hit_count > 1"
                  class="px-1.5 py-0.5 text-[10px] font-mono font-bold rounded bg-amber-200/80 text-amber-800 dark:bg-amber-900/80 dark:text-amber-200"
                >
                  累计 {{ alert.hit_count }} 次
                </span>
              </div>

              <div class="text-xs text-slate-500 dark:text-slate-400">
                检出时间: {{ alert.created_at ? new Date(alert.created_at).toLocaleString() : '未知' }}
              </div>

              <!-- 报错样例折叠 -->
              <div v-if="alert.error_sample" class="pt-1">
                <button
                  type="button"
                  class="text-[11px] text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 underline flex items-center gap-1"
                  @click="toggleSample(alert.id)"
                >
                  <span>{{ expandedSamples[alert.id] ? '▾ 收起报错详情' : '▸ 查看物理报错详情' }}</span>
                </button>
                <div
                  v-if="expandedSamples[alert.id]"
                  class="mt-1 p-2 bg-slate-900 text-slate-200 font-mono text-[11px] rounded leading-relaxed break-all border border-slate-800"
                >
                  {{ alert.error_sample }}
                </div>
              </div>
            </div>

            <!-- 操作按钮 -->
            <div class="flex items-center gap-1.5 shrink-0 pt-0.5">
              <template v-if="alert.status === 0">
                <button
                  v-if="alert.drift_type === 'missing_in_db'"
                  type="button"
                  :disabled="!canEdit || processingId === alert.id || isBatchProcessing"
                  :title="!canEdit ? '需具备数据集编辑权限方可操作' : undefined"
                  class="px-2.5 py-1 text-xs font-semibold rounded-lg bg-rose-600 hover:bg-rose-700 text-white shadow-2xs transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                  @click="handleResolve(alert, 'drop_column')"
                >
                  下线该字段
                </button>
                <button
                  v-else-if="alert.drift_type === 'new_in_db'"
                  type="button"
                  :disabled="!canEdit || processingId === alert.id || isBatchProcessing"
                  :title="!canEdit ? '需具备数据集编辑权限方可操作' : undefined"
                  class="px-2.5 py-1 text-xs font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white shadow-2xs transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                  @click="handleResolve(alert, 'add_column')"
                >
                  添加到数据集
                </button>
                <button
                  type="button"
                  :disabled="!canEdit || processingId === alert.id || isBatchProcessing"
                  :title="!canEdit ? '需具备数据集编辑权限方可操作' : undefined"
                  class="px-2.5 py-1 text-xs font-medium rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
                  @click="handleResolve(alert, 'ignore')"
                >
                  忽略
                </button>
              </template>
              <template v-else>
                <span class="text-xs font-medium text-slate-400">
                  {{ alert.status === 1 ? '✓ 已处置' : '已忽略' }}
                </span>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- 底部关闭 -->
      <div class="flex justify-end pt-2">
        <button
          type="button"
          class="px-4 py-1.5 text-xs font-medium text-slate-700 bg-slate-100 hover:bg-slate-200 dark:text-slate-300 dark:bg-slate-800 dark:hover:bg-slate-700 rounded-lg transition-colors cursor-pointer"
          @click="emit('close')"
        >
          关闭
        </button>
      </div>
    </div>
  </Modal>

  <!-- 平台统一风格确认弹窗（替代原生 confirm） -->
  <ConfirmModal
    v-if="confirmModal"
    :title="confirmModal.title"
    :message="confirmModal.message"
    :confirm-text="confirmModal.confirmText"
    :cancel-text="confirmModal.cancelText"
    :type="confirmModal.type"
    :loading="confirmModal.loading"
    @confirm="handleConfirmModalConfirm"
    @cancel="confirmModal = null"
  />
</template>

