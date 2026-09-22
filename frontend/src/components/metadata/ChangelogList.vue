<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { diffLines } from 'diff'
import { changelogApi } from '../../api/changelog'
import type { ChangelogResponse, ChangeDiffResponse } from '../../api/changelog'
import { useToast } from '../../composables/useToast'
import { LightBulbIcon } from '@heroicons/vue/24/outline'

interface Props {
  datasetId: number
}

const props = defineProps<Props>()
const { showToast } = useToast()

const changelogs = ref<ChangelogResponse[]>([])
const loading = ref(false)
const showDiffModal = ref(false)
const selectedChange = ref<ChangelogResponse | null>(null)
const diffData = ref<ChangeDiffResponse | null>(null)
const loadingDiff = ref(false)

// 视图模式: timeline (默认时间线视图), list (列表视图)
const viewMode = ref<'timeline' | 'list'>(
  (localStorage.getItem('nanzi_changelog_view_mode') as any) || 'timeline'
)

const setViewMode = (mode: 'timeline' | 'list') => {
  viewMode.value = mode
  localStorage.setItem('nanzi_changelog_view_mode', mode)
}

// 筛选与检索
const searchQuery = ref('')
const selectedOperation = ref<string>('all')
const selectedResourceType = ref<string>('all')

// 分页
const currentPage = ref(1)
const pageSize = 30
const total = ref(0)

const totalPages = computed(() => Math.ceil(total.value / pageSize))

const fetchChangelogs = async () => {
  loading.value = true
  try {
    const offset = (currentPage.value - 1) * pageSize
    const response = await changelogApi.getDatasetChangelog(props.datasetId, {
      limit: pageSize,
      offset
    })
    changelogs.value = response.data || []
    total.value = response.data?.length || 0
  } catch (error) {
    console.error('Failed to fetch changelogs:', error)
    showToast('获取变更日志失败', 'error')
  } finally {
    loading.value = false
  }
}

const filteredChangelogs = computed(() => {
  return changelogs.value.filter(log => {
    if (selectedOperation.value !== 'all' && log.operation !== selectedOperation.value) {
      return false
    }
    if (selectedResourceType.value !== 'all' && log.resource_type !== selectedResourceType.value) {
      return false
    }
    if (searchQuery.value.trim()) {
      const q = searchQuery.value.trim().toLowerCase()
      const matchResource = log.resource_id && log.resource_id.toLowerCase().includes(q)
      const matchUser = log.user_name && log.user_name.toLowerCase().includes(q)
      const matchReason = log.reason && log.reason.toLowerCase().includes(q)
      if (!matchResource && !matchUser && !matchReason) {
        return false
      }
    }
    return true
  })
})

const viewChangeDiff = async (changelog: ChangelogResponse) => {
  selectedChange.value = changelog
  loadingDiff.value = true
  
  try {
    const response = await changelogApi.getChangeDiff(changelog.id)
    diffData.value = response.data
    showDiffModal.value = true
  } catch (error) {
    console.error('Failed to fetch change diff:', error)
    showToast('获取变更详情失败', 'error')
  } finally {
    loadingDiff.value = false
  }
}

/** 计算两个值之间的 diff 行列表 */
const computeDiff = (oldVal: any, newVal: any) => {
  const toStr = (v: any) => {
    if (v === null || v === undefined) return ''
    if (typeof v === 'object') return JSON.stringify(v, null, 2)
    return String(v)
  }
  return diffLines(toStr(oldVal), toStr(newVal))
}

const formatDate = (dateString: string) => {
  return new Date(dateString).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

const formatRelativeTime = (dateString: string) => {
  const diff = Date.now() - new Date(dateString).getTime()
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return '刚刚'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 7) return `${day} 天前`
  return formatDate(dateString).split(' ')[0]
}

const getOperationBadgeStyle = (operation: string) => {
  switch (operation) {
    case 'create':
      return 'bg-emerald-50 text-emerald-700 border-emerald-200/80'
    case 'update':
      return 'bg-blue-50 text-blue-700 border-blue-200/80'
    case 'delete':
      return 'bg-red-50 text-red-700 border-red-200/80'
    default:
      return 'bg-gray-50 text-gray-700 border-gray-200'
  }
}

const getOperationNodeIconStyle = (operation: string) => {
  switch (operation) {
    case 'create':
      return 'bg-emerald-500 text-white ring-4 ring-emerald-50'
    case 'update':
      return 'bg-blue-500 text-white ring-4 ring-blue-50'
    case 'delete':
      return 'bg-red-500 text-white ring-4 ring-red-50'
    default:
      return 'bg-gray-500 text-white ring-4 ring-gray-50'
  }
}

const getOperationText = (operation: string) => {
  switch (operation) {
    case 'create': return '新增'
    case 'update': return '更新'
    case 'delete': return '删除'
    default: return operation
  }
}

const getResourceTypeText = (resourceType: string) => {
  switch (resourceType) {
    case 'dataset': return '数据集'
    case 'table': return '数据表'
    case 'column': return '字段'
    case 'metric': return '业务指标'
    case 'relationship': return '实体关系'
    default: return resourceType
  }
}

const getResourceTypeBadgeStyle = (resourceType: string) => {
  switch (resourceType) {
    case 'dataset': return 'bg-purple-50 text-purple-700 border-purple-100'
    case 'table': return 'bg-blue-50 text-blue-700 border-blue-100'
    case 'column': return 'bg-cyan-50 text-cyan-700 border-cyan-100'
    case 'metric': return 'bg-amber-50 text-amber-700 border-amber-100'
    case 'relationship': return 'bg-indigo-50 text-indigo-700 border-indigo-100'
    default: return 'bg-gray-50 text-gray-600 border-gray-200'
  }
}

const handlePageChange = (page: number) => {
  currentPage.value = page
  fetchChangelogs()
}

onMounted(() => {
  fetchChangelogs()
})
</script>

<template>
  <div class="space-y-3">
    <!-- Toolbar -->
    <div class="flex flex-wrap justify-between items-center gap-3 bg-white p-3 rounded-xl border border-gray-100 shadow-sm">
      <div class="flex items-center gap-3 flex-1 min-w-[280px]">
        <!-- Search Input -->
        <div class="relative flex-1 max-w-xs">
          <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
            </svg>
          </span>
          <input 
            v-model="searchQuery" 
            type="search"
            class="block w-full pl-9 pr-3 py-1.5 border border-gray-200 rounded-lg text-xs bg-gray-50 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary transition-all focus:bg-white" 
            placeholder="搜索对象名称/操作人/变更原因..."
          >
        </div>

        <!-- 动作类型筛选 -->
        <select 
          v-model="selectedOperation"
          class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-primary disabled:bg-gray-100"
        >
          <option value="all">所有操作</option>
          <option value="create">🟢 新增 (Create)</option>
          <option value="update">🔵 更新 (Update)</option>
          <option value="delete">🔴 删除 (Delete)</option>
        </select>

        <!-- 资源类型筛选 -->
        <select 
          v-model="selectedResourceType"
          class="border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-primary hidden sm:block disabled:bg-gray-100"
        >
          <option value="all">全部对象类型</option>
          <option value="table">数据表 (Table)</option>
          <option value="column">字段 (Column)</option>
          <option value="metric">业务指标 (Metric)</option>
          <option value="relationship">实体关系 (Relationship)</option>
          <option value="dataset">数据集 (Dataset)</option>
        </select>

        <span class="text-xs text-gray-400 font-medium hidden md:inline shrink-0">
          共 {{ changelogs.length }} 条记录
          <template v-if="searchQuery || selectedOperation !== 'all' || selectedResourceType !== 'all'">
            (过滤出 {{ filteredChangelogs.length }} 条)
          </template>
        </span>
      </div>

      <div class="flex items-center gap-3">
        <!-- View Switcher -->
        <div class="flex items-center p-1 bg-gray-100/80 rounded-lg border border-gray-200/60 shrink-0">
          <button 
            type="button"
            @click="setViewMode('timeline')"
            :class="[viewMode === 'timeline' ? 'bg-white text-blue-700 font-bold shadow-xs' : 'text-gray-500 hover:text-gray-800', 'px-2.5 py-1 text-xs rounded-md transition-all flex items-center gap-1.5']"
            title="时间线轨迹视图"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>时间线</span>
          </button>
          <button 
            type="button"
            @click="setViewMode('list')"
            :class="[viewMode === 'list' ? 'bg-white text-blue-700 font-bold shadow-xs' : 'text-gray-500 hover:text-gray-800', 'px-2.5 py-1 text-xs rounded-md transition-all flex items-center gap-1.5']"
            title="结构列表卡片视图"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
            <span>列表</span>
          </button>
        </div>

        <!-- 刷新按钮 -->
        <button 
          @click="fetchChangelogs"
          :disabled="loading"
          class="p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-100 rounded-lg border border-gray-200 bg-white transition-all shadow-2xs"
          title="刷新变更日志"
        >
          <svg class="w-4 h-4" :class="{ 'animate-spin': loading }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="flex justify-center py-12 bg-white rounded-xl border border-gray-100 shadow-xs">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
    </div>

    <!-- VIEW 1: 时间线视图 (Timeline View) -->
    <div 
      v-else-if="filteredChangelogs.length > 0 && viewMode === 'timeline'" 
      class="bg-white rounded-xl border border-gray-100 p-5 shadow-xs"
    >
      <div class="relative pl-6 sm:pl-8 before:absolute before:left-3 sm:before:left-3.5 before:top-3 before:bottom-3 before:w-0.5 before:bg-slate-200 dark:before:bg-slate-700 space-y-6">
        <div 
          v-for="log in filteredChangelogs" 
          :key="log.id"
          class="relative group cursor-pointer"
          @click="viewChangeDiff(log)"
        >
          <!-- 节点标记徽标 -->
          <div 
            class="absolute -left-6 sm:-left-8 top-1.5 flex h-6 w-6 sm:h-7 sm:w-7 items-center justify-center rounded-full text-xs shadow-xs transition-transform duration-200 group-hover:scale-110"
            :class="getOperationNodeIconStyle(log.operation)"
          >
            <!-- create: + -->
            <svg v-if="log.operation === 'create'" class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" />
            </svg>
            <!-- delete: 垃圾桶 -->
            <svg v-else-if="log.operation === 'delete'" class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
            <!-- update: 铅笔 -->
            <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
            </svg>
          </div>

          <!-- 时间线内容卡片 -->
          <div class="bg-slate-50/70 hover:bg-white rounded-xl border border-slate-200/80 hover:border-blue-300 p-3.5 transition-all shadow-2xs hover:shadow-xs ml-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="flex flex-wrap items-center gap-2 min-w-0">
                <!-- 用户头像胶囊 -->
                <div class="flex items-center gap-1.5 bg-white px-2 py-0.5 rounded-full border border-gray-200/80 text-xs font-semibold text-gray-700 shadow-2xs">
                  <div class="w-4 h-4 rounded-full bg-indigo-100 text-indigo-700 flex items-center justify-center text-[10px] font-bold">
                    {{ (log.user_name || 'U').slice(0, 1).toUpperCase() }}
                  </div>
                  <span>{{ log.user_name || '系统操作' }}</span>
                </div>

                <!-- 动作标签 -->
                <span 
                  :class="[
                    'px-2 py-0.5 rounded-md text-xs font-bold border',
                    getOperationBadgeStyle(log.operation)
                  ]"
                >
                  {{ getOperationText(log.operation) }}
                </span>

                <!-- 资源类型 -->
                <span 
                  :class="[
                    'px-2 py-0.5 rounded-md text-xs font-medium border',
                    getResourceTypeBadgeStyle(log.resource_type)
                  ]"
                >
                  {{ getResourceTypeText(log.resource_type) }}
                </span>

                <!-- 资源名称 -->
                <span class="font-mono font-bold text-gray-900 text-xs bg-white px-2 py-0.5 rounded border border-gray-200 shadow-2xs truncate max-w-xs sm:max-w-md">
                  {{ log.resource_id }}
                </span>
              </div>

              <!-- 时间 -->
              <div class="flex items-center gap-2 text-xs text-gray-400 font-mono shrink-0">
                <span class="font-sans font-medium text-gray-600 bg-white px-2 py-0.5 rounded border border-gray-100">
                  {{ formatRelativeTime(log.created_at) }}
                </span>
                <span class="hidden sm:inline text-gray-400">
                  {{ formatDate(log.created_at) }}
                </span>
              </div>
            </div>

            <!-- 变更原因说明 -->
            <div v-if="log.reason" class="mt-2.5 text-xs text-gray-600 bg-white p-2 rounded-lg border border-gray-200/70 flex items-start gap-1.5">
              <LightBulbIcon class="w-4 h-4 text-amber-500 shrink-0" />
              <span class="leading-relaxed">{{ log.reason }}</span>
            </div>

            <!-- 卡片底栏交互 -->
            <div class="mt-2.5 pt-2 border-t border-gray-200/50 flex items-center justify-between text-xs text-gray-400">
              <span class="text-[11px] text-gray-400">点击卡片可查看字段级详细差异对比 (Diff)</span>
              <span class="text-blue-600 group-hover:text-blue-700 font-bold inline-flex items-center gap-1 transition-colors">
                <span>查看 Diff</span>
                <svg class="w-3.5 h-3.5 transition-transform group-hover:translate-x-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                </svg>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- VIEW 2: 列表视图 (List View) -->
    <div v-else-if="filteredChangelogs.length > 0 && viewMode === 'list'" class="space-y-2.5">
      <div 
        v-for="log in filteredChangelogs" 
        :key="log.id"
        class="bg-white rounded-xl border border-gray-100 p-4 hover:border-blue-300 hover:shadow-xs transition-all cursor-pointer group"
        @click="viewChangeDiff(log)"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="flex-1 space-y-2 min-w-0">
            <!-- 操作信息 -->
            <div class="flex flex-wrap items-center gap-2.5">
              <span 
                :class="[
                  'px-2 py-0.5 rounded-md text-xs font-bold border',
                  getOperationBadgeStyle(log.operation)
                ]"
              >
                {{ getOperationText(log.operation) }}
              </span>
              <span 
                :class="[
                  'px-2 py-0.5 rounded-md text-xs font-medium border',
                  getResourceTypeBadgeStyle(log.resource_type)
                ]"
              >
                {{ getResourceTypeText(log.resource_type) }}
              </span>
              <span class="font-mono font-bold text-gray-900 text-xs truncate">
                {{ log.resource_id }}
              </span>
            </div>

            <!-- 用户和时间 -->
            <div class="flex items-center gap-4 text-xs text-gray-400">
              <span class="text-gray-600 font-medium">操作人: {{ log.user_name || '系统操作' }}</span>
              <span>{{ formatDate(log.created_at) }}</span>
            </div>

            <!-- 变更原因 -->
            <div v-if="log.reason" class="text-xs text-gray-600 bg-gray-50 p-2 rounded-lg border border-gray-100">
              <strong class="text-gray-700">变更说明:</strong> {{ log.reason }}
            </div>
          </div>

          <!-- 右侧操作箭头 -->
          <div class="flex items-center gap-1.5 text-xs text-blue-600 font-bold shrink-0 self-center">
            <span class="hidden sm:inline opacity-0 group-hover:opacity-100 transition-opacity">查看详情</span>
            <svg class="w-4 h-4 text-gray-400 group-hover:text-blue-600 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
            </svg>
          </div>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="bg-white rounded-xl border border-gray-100 p-10 text-center shadow-xs">
      <div class="text-4xl mb-3 grayscale opacity-30">📋</div>
      <h3 class="text-base font-bold text-gray-800 mb-1">
        {{ changelogs.length === 0 ? '暂无变更记录' : '未匹配到符合条件的记录' }}
      </h3>
      <p class="text-xs text-gray-500">
        {{ changelogs.length === 0 ? '该数据集还没有任何变更历史记录' : '尝试调整搜索条件或过滤选项' }}
      </p>
      <button 
        v-if="searchQuery || selectedOperation !== 'all' || selectedResourceType !== 'all'"
        @click="searchQuery = ''; selectedOperation = 'all'; selectedResourceType = 'all'"
        class="mt-3 text-primary text-xs font-bold hover:underline"
      >
        重置筛选条件
      </button>
    </div>

    <!-- 分页 -->
    <div v-if="totalPages > 1" class="flex justify-center items-center gap-2 mt-4">
      <button
        @click="handlePageChange(currentPage - 1)"
        :disabled="currentPage <= 1"
        class="px-3 py-1 bg-white border border-gray-200 rounded-lg text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50 shadow-2xs"
      >
        上一页
      </button>
      
      <span class="text-xs text-gray-500 font-mono">
        第 {{ currentPage }} / {{ totalPages }} 页
      </span>
      
      <button
        @click="handlePageChange(currentPage + 1)"
        :disabled="currentPage >= totalPages"
        class="px-3 py-1 bg-white border border-gray-200 rounded-lg text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50 shadow-2xs"
      >
        下一页
      </button>
    </div>

    <!-- 变更详情弹窗 -->
    <div v-if="showDiffModal && selectedChange" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div class="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] overflow-hidden flex flex-col border border-gray-200">
        <!-- 头部 -->
        <div class="px-6 py-4 border-b border-gray-200 flex justify-between items-center bg-gray-50/80">
          <div>
            <h2 class="text-lg font-bold text-gray-900">变更差异对比 (Diff)</h2>
            <p class="text-xs text-gray-500 mt-0.5">{{ diffData?.summary || '对象属性前后版本变更明细' }}</p>
          </div>
          <button 
            @click="showDiffModal = false" 
            class="text-gray-400 hover:text-gray-600 transition-colors p-1 rounded-lg hover:bg-gray-200/50"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- 内容 -->
        <div class="flex-1 overflow-auto p-5 space-y-4">
          <div v-if="loadingDiff" class="flex justify-center items-center py-12">
            <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
          </div>
          
          <div v-else-if="diffData" class="space-y-4">
            <!-- 基本信息 -->
            <div class="bg-gray-50/70 p-3.5 rounded-xl border border-gray-200/80 text-xs">
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <span class="text-gray-400">操作类型:</span>
                  <span :class="[
                    'ml-1.5 px-2 py-0.5 rounded text-[11px] font-bold border',
                    getOperationBadgeStyle(selectedChange.operation)
                  ]">
                    {{ getOperationText(selectedChange.operation) }}
                  </span>
                </div>
                <div>
                  <span class="text-gray-400">对象类型:</span>
                  <span class="ml-1.5 font-bold text-gray-800">{{ getResourceTypeText(selectedChange.resource_type) }}</span>
                </div>
                <div>
                  <span class="text-gray-400">操作人:</span>
                  <span class="ml-1.5 font-bold text-gray-800">{{ selectedChange.user_name || '系统操作' }}</span>
                </div>
                <div>
                  <span class="text-gray-400">操作时间:</span>
                  <span class="ml-1.5 font-mono text-gray-600">{{ formatDate(selectedChange.created_at) }}</span>
                </div>
              </div>
              <div v-if="selectedChange.reason" class="mt-2.5 pt-2 border-t border-gray-200/60">
                <span class="text-gray-400">变更原因:</span>
                <span class="ml-1.5 text-gray-700">{{ selectedChange.reason }}</span>
              </div>
            </div>

            <!-- 变更对比 -->
            <div class="space-y-3">
              <h3 class="text-xs font-bold text-gray-800 uppercase tracking-wider">字段级 Diff 对比</h3>
              <div v-if="diffData.changes && diffData.changes.length > 0" class="space-y-3">
                <div 
                  v-for="change in diffData.changes" 
                  :key="change.field"
                  class="bg-white rounded-xl border border-gray-200 overflow-hidden shadow-2xs"
                >
                  <div class="text-xs font-bold font-mono text-gray-700 bg-gray-50 px-3.5 py-1.5 border-b border-gray-200 flex items-center justify-between">
                    <span>属性: {{ change.field }}</span>
                  </div>
                  <!-- Diff 视图 -->
                  <div class="font-mono text-xs overflow-x-auto p-2 bg-slate-900 text-slate-100">
                    <template v-if="change.old_value !== null || change.new_value !== null">
                      <div
                        v-for="(part, idx) in computeDiff(change.old_value, change.new_value)"
                        :key="idx"
                        :class="[
                          'px-2 py-0.5 whitespace-pre-wrap break-all rounded',
                          part.added   ? 'bg-emerald-950/80 text-emerald-400 font-bold' :
                          part.removed ? 'bg-rose-950/80 text-rose-400 line-through opacity-80' :
                                         'text-slate-300'
                        ]"
                      >
                        <span class="select-none mr-1 opacity-60">
                          {{ part.added ? '+' : part.removed ? '-' : ' ' }}
                        </span>{{ part.value }}</div>
                    </template>
                    <div v-else class="px-2 py-1 text-slate-500">(无数据)</div>
                  </div>
                </div>
              </div>
              <div v-else class="text-center py-6 text-xs text-gray-400 bg-gray-50 rounded-xl border border-dashed border-gray-200">
                暂无具体字段级 Diff 变更内容
              </div>
            </div>
          </div>
        </div>

        <!-- 底部 -->
        <div class="px-6 py-3 border-t border-gray-200 bg-gray-50 flex justify-end">
          <button 
            @click="showDiffModal = false"
            class="px-4 py-1.5 bg-white border border-gray-300 rounded-lg text-xs font-bold text-gray-700 hover:bg-gray-50 shadow-2xs"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.line-through {
  text-decoration: line-through;
}
</style>
