<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { taskApi, type AgentTask, type TaskLog } from '../api/task'
import { agentApi, type AIAgent } from '../api/agent'
import Modal from '../components/Modal.vue'
import Toast from '../components/Toast.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import cronstrue from 'cronstrue/i18n'
import SessionTraceModal from '../components/SessionTraceModal.vue'
import { 
  PlayCircleIcon,
  PauseCircleIcon
} from '@heroicons/vue/24/outline'
import { useRouter } from 'vue-router'

const router = useRouter()

// Auth & Permission
const cachedUser = localStorage.getItem('user_info')
const userInfo = ref(cachedUser ? JSON.parse(cachedUser) : null)
const canManage = computed(() => {
  if (!userInfo.value) return false
  if (userInfo.value.role === 'admin') return true
  const userElements = userInfo.value.permissions?.elements || []
  return userElements.includes('element:task:manage')
})
const canManageTask = (task: AgentTask) => task.task_type === 'saved_report'
  ? String(task.user_id) === String(userInfo.value?.user_id)
  : canManage.value

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

watch(viewMode, (newMode) => {
  localStorage.setItem('task_center_view_mode', newMode)
})

const tasks = ref<AgentTask[]>([])
const agents = ref<AIAgent[]>([])
const loading = ref(false)
const showEditModal = ref(false)
const showLogsDrawer = ref(false)
const editingTask = ref<Partial<AgentTask>>({})
const selectedTask = ref<AgentTask | null>(null)
const logs = ref<TaskLog[]>([])
const logsLoading = ref(false)
const logsPage = ref(1)
const logsTotal = ref(0)
const logsHasMore = computed(() => logs.value.length < logsTotal.value)
const runningTaskIds = ref(new Set<number>())
const showSpecsModal = ref(false)

// Mobile State
const windowWidth = ref(window.innerWidth)
const isMobile = computed(() => windowWidth.value < 768)
const currentViewMode = computed(() => isMobile.value ? 'grid' : viewMode.value)

onMounted(() => {
  window.addEventListener('resize', () => windowWidth.value = window.innerWidth)
})
onUnmounted(() => {
  window.removeEventListener('resize', () => windowWidth.value = window.innerWidth)
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
      
      switch (cronMode.value) {
        case 'daily':
          expr = `${m || 0} ${h || 0} * * *`
          break
        case 'weekly':
          expr = `${m || 0} ${h || 0} * * ${weekday}`
          break
        case 'monthly':
          expr = `${m || 0} ${h || 0} ${day} * *`
          break
        case 'interval':
          if (intervalUnit === 'minutes') {
             expr = `*/${intervalValue} * * * *`
          } else {
             expr = `0 */${intervalValue} * * *`
          }
          break
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

    // Standard Check
    const timeStr = `${String(hour).padStart(2, '0')}:${String(min).padStart(2, '0')}`
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
const taskTypeCounts = computed(() => ({
  all: tasks.value.length,
  agent: tasks.value.filter(task => task.task_type !== 'saved_report').length,
  saved_report: tasks.value.filter(task => task.task_type === 'saved_report').length,
}))

const filteredTasks = computed(() => {
  let result = [...tasks.value]
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

const openCreateModal = () => {
  editingTask.value = { name: '', agent_id: agents.value[0]?.id || '', cron_expr: '0 8 * * *', prompt: '', status: 1 }
  cronMode.value = 'daily'
  cronConfig.value = { time: '08:00', weekday: 1, day: 1, intervalValue: 30, intervalUnit: 'minutes' }
  showEditModal.value = true
}

const openEditModal = (task: AgentTask) => {
  if (task.task_type === 'saved_report') {
    openSavedReportTask(task)
    return
  }
  editingTask.value = { ...task }
  parseCronToUI(task.cron_expr || '')
  showEditModal.value = true
}


const saveTask = async () => {
  try {
    if (editingTask.value.id) {
      await taskApi.update(editingTask.value.id, editingTask.value)
      showToast('更新成功')
    } else {
      await taskApi.create(editingTask.value)
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
    const poll = setInterval(async () => {
        attempts++
        if (attempts > 5) { clearInterval(poll); runningTaskIds.value.delete(task.id); return }
        await fetchTasks(true)
    }, 1000)
    setTimeout(() => { clearInterval(poll); runningTaskIds.value.delete(task.id) }, 5000)
  } catch (e) {
    showToast('触发失败', 'error')
    runningTaskIds.value.delete(task.id)
  }
}


const openLogs = async (task: AgentTask) => {
  if (task.task_type === 'saved_report') {
    openSavedReportTask(task)
    return
  }
  selectedTask.value = task; logsPage.value = 1; logs.value = []; showLogsDrawer.value = true; fetchLogs()
}

const openSavedReportTask = async (task: AgentTask) => {
  await router.push({ path: '/dashboard/chat', query: { dataset_portal: '1', report_id: task.report_id, run_id: task.last_run_id || '' } })
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
  return new Date(d).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const formatNextRunCompact = (d: string | undefined) => {
  if (!d) return '暂无计划'
  return new Date(d).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
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

const metricValue = (value: number | undefined) => Number(value || 0)

onMounted(() => { fetchTasks(true); fetchAgents() })
</script>

<template>
  <div class="space-y-6">
    <!-- Header Section -->
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">任务调度台</h1>
        <p class="text-gray-500 mt-1 text-sm hidden sm:block">管理并监控智能体的定时自动化巡检与报表任务</p>
        <p class="text-gray-500 mt-1 text-xs sm:hidden">智能体定时任务与巡检管理</p>
      </div>
      <div class="flex items-center space-x-3">
        <button 
          @click="fetchTasks(false)"
          class="p-2 text-gray-500 hover:text-primary hover:bg-gray-100 rounded-lg transition-colors"
          title="刷新列表"
        >
           <svg class="w-5 h-5" :class="loading ? 'animate-spin' : ''" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
        </button>
        <button 
          @click="showSpecsModal = true"
          class="px-3 py-2 bg-white border border-gray-200 text-gray-600 rounded-lg shadow-sm hover:bg-gray-50 transition-all flex items-center text-sm font-medium"
        >
          <svg class="w-4 h-4 mr-1.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
          设计规范
        </button>
        <button 
          v-if="canManage"
          @click="openCreateModal"
          class="px-4 py-2 bg-primary text-white rounded-lg shadow-sm hover:bg-primary-dark transition-all flex items-center"
        >
          <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" /></svg>
          <span class="hidden sm:inline">新建任务</span>
          <span class="sm:hidden">新建</span>
        </button>
      </div>
    </div>

    <div class="flex items-center gap-1 overflow-x-auto rounded-xl border border-gray-100 bg-white p-1.5 shadow-sm">
      <button
        v-for="tab in taskTypeTabs"
        :key="tab.value"
        type="button"
        class="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-bold transition-colors"
        :class="taskTypeFilter === tab.value ? 'bg-primary text-white shadow-sm' : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700'"
        @click="taskTypeFilter = tab.value"
      >
        <span>{{ tab.label }}</span>
        <span class="min-w-5 rounded-full px-1.5 py-0.5 text-[9px] text-center" :class="taskTypeFilter === tab.value ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-500'">
          {{ taskTypeCounts[tab.value] }}
        </span>
      </button>
    </div>

    <!-- Filters & Toolbar -->
    <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-4 flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center space-x-4">
        <div class="flex items-center space-x-2">
          <span class="text-sm text-gray-500">状态:</span>
          <select v-model="statusFilter" class="text-sm border-gray-300 rounded-md focus:ring-primary focus:border-primary">
            <option value="all">全部</option>
            <option value="running">运行中</option>
            <option value="stopped">已停止</option>
          </select>
        </div>
      </div>

      <div class="flex items-center space-x-3">
        <!-- Search -->
        <div class="relative w-64">
          <input 
            v-model="searchQuery"
            placeholder="搜索任务名称或指令..."
            class="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
          />
          <svg class="w-4 h-4 text-gray-400 absolute left-3 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
        </div>

        <!-- View Mode Switch -->
        <div class="flex items-center bg-gray-100 p-1 rounded-lg border border-gray-200 hidden md:flex">
          <button 
            @click="viewMode = 'grid'"
            class="p-1.5 rounded-md transition-all"
            :class="currentViewMode === 'grid' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" /></svg>
          </button>
          <button 
            @click="viewMode = 'list'"
            class="p-1.5 rounded-md transition-all ml-0.5"
            :class="currentViewMode === 'list' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
        </div>
      </div>
    </div>

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
                  <span class="text-[10px] font-bold text-blue-600" :title="`Cron: ${task.cron_expr}`">{{ formatTaskSchedule(task.cron_expr) }}</span>
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
            <button v-if="canManageTask(task)" @click="openEditModal(task)" class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-white rounded-md transition-all shadow-sm border border-transparent hover:border-gray-100" :title="task.task_type === 'saved_report' ? '打开报表' : '编辑'">
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
              <span class="inline-flex whitespace-nowrap rounded-lg bg-blue-50 px-2 py-1 text-[11px] font-bold text-blue-700" :title="`Cron: ${task.cron_expr}`">{{ formatTaskSchedule(task.cron_expr) }}</span>
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
                <button class="whitespace-nowrap rounded-lg border border-blue-100 bg-blue-50 px-2 py-1.5 text-[10px] font-bold text-blue-600 hover:bg-blue-100" @click="openSavedReportTask(task)">打开报表</button>
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
                <button v-if="canManageTask(task)" @click="openEditModal(task)" class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-white rounded shadow-sm border border-transparent hover:border-gray-100" :title="task.task_type === 'saved_report' ? '打开报表' : '编辑'">
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

    <!-- Design Specs Modal -->
    <Modal 
      v-if="showSpecsModal" 
      title="任务交互设计规范" 
      @close="showSpecsModal = false" 
      size="max-w-2xl"
    >
      <div class="space-y-6">
        <section>
          <h3 class="text-sm font-bold text-gray-900 flex items-center mb-3">
            <span class="w-1.5 h-4 bg-primary rounded-full mr-2"></span>
            1. 执行触发机制
          </h3>
          <p class="text-xs text-gray-600 leading-relaxed pl-3.5">
            任务中心基于分布式调度引擎 <strong>APScheduler</strong>，支持秒级精度的 Cron 表达式。系统会根据配置的周期自动唤醒并调用智能体。
          </p>
        </section>

        <section>
          <h3 class="text-sm font-bold text-gray-900 flex items-center mb-3">
            <span class="w-1.5 h-4 bg-primary rounded-full mr-2"></span>
            2. 上下文注入 (Context Injection)
          </h3>
          <p class="text-xs text-gray-600 leading-relaxed mb-3 pl-3.5">
            每次任务执行时，系统会在 <code>user_info</code> 中自动注入以下元数据，智能体可通过这些标识识别自动化场景：
          </p>
          <div class="bg-gray-50 rounded-xl p-4 border border-gray-100 font-mono text-[10px] space-y-2">
            <div class="flex">
              <span class="text-blue-600 w-32">is_scheduled_task:</span>
              <span class="text-green-600">true</span>
              <span class="text-gray-400 ml-auto">// 标识当前为定时自动化任务</span>
            </div>
            <div class="flex">
              <span class="text-blue-600 w-32">task_name:</span>
              <span class="text-green-600">"PUE日报巡检"</span>
              <span class="text-gray-400 ml-auto">// 当前执行的任务名称</span>
            </div>
            <div class="flex">
              <span class="text-blue-600 w-32">user_id / role:</span>
              <span class="text-green-600">"admin" / "1"</span>
              <span class="text-gray-400 ml-auto">// 模拟创建者的身份与权限</span>
            </div>
          </div>
        </section>

        <section>
          <h3 class="text-sm font-bold text-gray-900 flex items-center mb-3">
            <span class="w-1.5 h-4 bg-primary rounded-full mr-2"></span>
            3. 智能体处理规范 (Agent Best Practices)
          </h3>
          <ul class="space-y-3 pl-3.5">
            <li class="flex items-start">
              <span class="text-primary mr-2 mt-1">●</span>
              <div class="flex-1">
                <p class="text-xs font-bold text-gray-700">结果导向输出</p>
                <p class="text-[11px] text-gray-500 mt-0.5">识别到自动化上下文后，应跳过“您好”、“请稍等”等交互式用语，直接输出结构化的报表、数据结论或 Markdown 表格。</p>
              </div>
            </li>
            <li class="flex items-start">
              <span class="text-primary mr-2 mt-1">●</span>
              <div class="flex-1">
                <p class="text-xs font-bold text-gray-700">鲁棒性异常反馈</p>
                <p class="text-[11px] text-gray-500 mt-0.5">若执行过程中遇到工具调用失败或数据缺失，应在最终回复中明确标注错误原因，以便系统审计和通知告警。</p>
              </div>
            </li>
            <li class="flex items-start">
              <span class="text-primary mr-2 mt-1">●</span>
              <div class="flex-1">
                <p class="text-xs font-bold text-gray-700">自动摘要生成</p>
                <p class="text-[11px] text-gray-500 mt-0.5">系统会利用返回的 <code>summary</code> 或结果首段内容作为任务历史的“摘要预览”。</p>
              </div>
            </li>
          </ul>
        </section>

        <section>
          <h3 class="text-sm font-bold text-gray-900 flex items-center mb-3">
            <span class="w-1.5 h-4 bg-primary rounded-full mr-2"></span>
            4. 智能体管理工具 (Built-in Tools)
          </h3>
          <p class="text-xs text-gray-600 leading-relaxed mb-3 pl-3.5">
            所有智能体均内置了任务管理能力，用户可通过对话直接要求 Agent 维护其定时任务：
          </p>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3 pl-3.5">
            <div class="p-3 bg-blue-50/50 rounded-xl border border-blue-100/50">
              <p class="text-[10px] font-bold text-blue-700 uppercase mb-1">create_recurring_task</p>
              <p class="text-[9px] text-blue-600 italic">创建新任务。参数：name, cron, prompt</p>
            </div>
            <div class="p-3 bg-indigo-50/50 rounded-xl border border-indigo-100/50">
              <p class="text-[10px] font-bold text-indigo-700 uppercase mb-1">get_my_tasks</p>
              <p class="text-[9px] text-indigo-600 italic">查看我当前运行中的所有定时任务列表</p>
            </div>
            <div class="p-3 bg-purple-50/50 rounded-xl border border-purple-100/50">
              <p class="text-[10px] font-bold text-purple-700 uppercase mb-1">cancel_task</p>
              <p class="text-[9px] text-purple-600 italic">彻底删除指定 ID 的任务。参数：task_id</p>
            </div>
            <div class="p-3 bg-green-50/50 rounded-xl border border-green-100/50">
              <p class="text-[10px] font-bold text-green-700 uppercase mb-1">start_task</p>
              <p class="text-[9px] text-green-600 italic">启动或恢复暂停的任务。参数：task_id</p>
            </div>
            <div class="p-3 bg-amber-50/50 rounded-xl border border-amber-100/50">
              <p class="text-[10px] font-bold text-amber-700 uppercase mb-1">pause_task</p>
              <p class="text-[9px] text-amber-600 italic">暂停运行中的任务。参数：task_id</p>
            </div>
            <div class="p-3 bg-blue-50/50 rounded-xl border border-blue-100/50">
              <p class="text-[10px] font-bold text-blue-700 uppercase mb-1">send_dingtalk_message</p>
              <p class="text-[9px] text-blue-600 italic">发送钉钉通知。参数：title, content</p>
            </div>
          </div>
        </section>

        <div class="pt-4 border-t border-gray-50 flex justify-end">
          <button @click="showSpecsModal = false" class="px-6 py-2 bg-gray-900 text-white rounded-xl text-xs font-bold shadow-lg hover:bg-black transition-all">
            已知晓
          </button>
        </div>
      </div>
    </Modal>

    <!-- Edit Modal -->
    <Modal 
      v-if="showEditModal" 
      :title="editingTask.id ? '编辑定时任务' : '新建定时任务'" 
      @close="showEditModal = false" 
      size="max-w-lg"
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
            <select v-model="editingTask.agent_id" class="w-full px-3 py-2 border rounded-xl outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary bg-white">
              <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.display_name }} ({{ agent.name }})</option>
            </select>
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
                   <input type="number" min="1" v-model.number="cronConfig.intervalValue" class="w-20 border rounded-lg px-2 py-1.5 text-sm text-center" />
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
            <label class="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-1">执行指令 (Prompt)</label>
            <textarea v-model="editingTask.prompt" rows="4" placeholder="例如：帮我查一下华东一号机房昨天的 PUE 峰值..." class="w-full px-3 py-2 border rounded-xl outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary text-sm"></textarea>
          </div>
        </div>

        <div class="flex justify-end space-x-3 pt-4 pb-safe-area border-t border-gray-50">
          <button @click="showEditModal = false" class="px-4 py-2 text-sm font-bold text-gray-400 hover:text-gray-600">取消</button>
          <button @click="saveTask" class="px-8 py-2 bg-primary text-white rounded-xl shadow-lg shadow-primary/20 hover:bg-primary-dark transition-all font-bold text-sm">确认保存</button>
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
                  <span class="px-2 py-0.5 rounded-full text-[9px] font-black uppercase tracking-tighter" :class="log.status === 'success' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'">{{ log.status }}</span>
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
                <span class="text-[9px] text-gray-300 font-medium">时长: {{ log.execution_time_ms.toFixed(0) }}ms</span>
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
