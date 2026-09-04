<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import axios from '@/utils/axios'
import { copyToClipboard } from '@/utils/clipboard'
import { useToast } from '@/composables/useToast'
import {
  ArrowPathIcon,
  ClipboardDocumentIcon,
  CheckIcon,
  ChevronRightIcon,
  ClockIcon,
  CheckCircleIcon,
  XCircleIcon,
  CpuChipIcon,
  ExclamationTriangleIcon,
  DocumentTextIcon,
  ServerIcon,
  MagnifyingGlassIcon,
  FunnelIcon,
} from '@heroicons/vue/24/outline'

const { showToast } = useToast()

// 状态管理
const loading = ref(false)
const logs = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(15)

// 筛选字段
const selectedServerId = ref('')
const serverOptions = ref<{ id: string; server_name: string }[]>([])
const toolNameFilter = ref('')
const statusFilter = ref<'all' | 'success' | 'failed'>('all')
const timeRangeFilter = ref<'24h' | '7d' | '30d'>('7d')

// 指标聚合
const metrics = ref({
  total_calls: 0,
  success_calls: 0,
  failed_calls: 0,
  success_rate: 100.0,
  avg_latency_ms: 0,
})

// 展开行的集合
const expandedRowIds = ref<Set<string>>(new Set())

// 复制状态记录
const copiedKeys = ref<Record<string, boolean>>({})

const toggleRowExpand = (id: string) => {
  if (expandedRowIds.value.has(id)) {
    expandedRowIds.value.delete(id)
  } else {
    expandedRowIds.value.add(id)
  }
}

const copyContent = async (key: string, content: any) => {
  try {
    const textToCopy = typeof content === 'string' ? content : JSON.stringify(content, null, 2)
    await copyToClipboard(textToCopy)
    copiedKeys.value[key] = true
    showToast('已复制到剪贴板', 'success')
    setTimeout(() => {
      copiedKeys.value[key] = false
    }, 2000)
  } catch (err) {
    showToast('复制失败', 'error')
  }
}

// 格式化 JSON 安全输出
const formatJson = (val: any) => {
  if (!val) return '无'
  if (typeof val === 'string') {
    try {
      const parsed = JSON.parse(val)
      return JSON.stringify(parsed, null, 2)
    } catch {
      return val
    }
  }
  return JSON.stringify(val, null, 2)
}

// 格式化时间
const formatTime = (timeStr: string | null) => {
  if (!timeStr) return '-'
  const d = new Date(timeStr)
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

// 获取全局日志列表及指标
const fetchLogs = async () => {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize.value,
      range: timeRangeFilter.value,
    }
    if (selectedServerId.value) {
      params.server_id = selectedServerId.value
    }
    if (toolNameFilter.value.trim()) {
      params.tool_name = toolNameFilter.value.trim()
    }
    if (statusFilter.value !== 'all') {
      params.status = statusFilter.value
    }

    const res: any = await axios.get('/api/portal/mcp/outbound-logs', { params })
    const data = res?.data || {}
    logs.value = data.items || []
    total.value = data.total || 0
    if (data.server_options) {
      serverOptions.value = data.server_options
    }
    if (data.summary) {
      metrics.value = data.summary
    }
  } catch (err: any) {
    showToast(err?.response?.data?.detail || '获取出站审计日志失败', 'error')
  } finally {
    loading.value = false
  }
}

// 筛选变更
const handleFilterChange = () => {
  page.value = 1
  fetchLogs()
}

// 重置所有筛选
const resetFilters = () => {
  selectedServerId.value = ''
  toolNameFilter.value = ''
  statusFilter.value = 'all'
  timeRangeFilter.value = '7d'
  page.value = 1
  fetchLogs()
}

// 分页计算
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

const prevPage = () => {
  if (page.value > 1) {
    page.value--
    fetchLogs()
  }
}

const nextPage = () => {
  if (page.value < totalPages.value) {
    page.value++
    fetchLogs()
  }
}

onMounted(() => {
  fetchLogs()
})
</script>

<template>
  <div class="flex flex-col h-full overflow-hidden bg-white rounded-xl border border-gray-200 shadow-xs">
    <!-- 顶部大盘指标卡 -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 px-6 py-4 bg-slate-50/60 border-b border-gray-200 shrink-0">
      <div class="rounded-xl border border-gray-200 bg-white p-3.5 shadow-2xs">
        <div class="flex items-center justify-between">
          <span class="text-xs font-medium text-gray-500">周期调用总量</span>
          <CpuChipIcon class="h-4 w-4 text-indigo-500" />
        </div>
        <div class="mt-1 flex items-baseline gap-1">
          <span class="text-2xl font-black text-gray-900">{{ metrics.total_calls.toLocaleString() }}</span>
          <span class="text-[11px] text-gray-400">次</span>
        </div>
      </div>

      <div class="rounded-xl border border-gray-200 bg-white p-3.5 shadow-2xs">
        <div class="flex items-center justify-between">
          <span class="text-xs font-medium text-gray-500">调用成功率</span>
          <CheckCircleIcon
            class="h-4 w-4"
            :class="metrics.success_rate >= 95 ? 'text-emerald-500' : metrics.success_rate >= 80 ? 'text-amber-500' : 'text-rose-500'"
          />
        </div>
        <div class="mt-1 flex items-baseline gap-1">
          <span
            class="text-2xl font-black"
            :class="metrics.success_rate >= 95 ? 'text-emerald-600' : metrics.success_rate >= 80 ? 'text-amber-600' : 'text-rose-600'"
          >
            {{ metrics.success_rate }}%
          </span>
          <span class="text-[11px] text-gray-400">({{ metrics.success_calls }} 成功)</span>
        </div>
      </div>

      <div class="rounded-xl border border-gray-200 bg-white p-3.5 shadow-2xs">
        <div class="flex items-center justify-between">
          <span class="text-xs font-medium text-gray-500">平均响应耗时</span>
          <ClockIcon class="h-4 w-4 text-indigo-500" />
        </div>
        <div class="mt-1 flex items-baseline gap-1">
          <span class="text-2xl font-black text-indigo-600">{{ metrics.avg_latency_ms }}</span>
          <span class="text-[11px] text-gray-400">ms</span>
        </div>
      </div>

      <div class="rounded-xl border border-gray-200 bg-white p-3.5 shadow-2xs">
        <div class="flex items-center justify-between">
          <span class="text-xs font-medium text-gray-500">调用失败数</span>
          <ExclamationTriangleIcon class="h-4 w-4" :class="metrics.failed_calls > 0 ? 'text-rose-500' : 'text-gray-300'" />
        </div>
        <div class="mt-1 flex items-baseline gap-1">
          <span
            class="text-2xl font-black"
            :class="metrics.failed_calls > 0 ? 'text-rose-600' : 'text-gray-700'"
          >
            {{ metrics.failed_calls }}
          </span>
          <span class="text-[11px] text-gray-400">次异常</span>
        </div>
      </div>
    </div>

    <!-- 综合筛选与控制栏 -->
    <div class="px-6 py-3.5 border-b border-gray-200 flex flex-wrap items-center justify-between gap-3 bg-white shrink-0">
      <div class="flex flex-wrap items-center gap-2.5">
        <!-- 所属 MCP 服务下拉选择 -->
        <div class="flex items-center gap-1.5">
          <ServerIcon class="h-4 w-4 text-gray-400" />
          <select
            v-model="selectedServerId"
            @change="handleFilterChange"
            class="w-52 rounded-lg border border-gray-300 bg-white py-1.5 px-2.5 text-xs font-medium text-gray-700 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            <option value="">全部 MCP 服务 (全量)</option>
            <option v-for="srv in serverOptions" :key="srv.id" :value="srv.id">
              {{ srv.server_name }}
            </option>
          </select>
        </div>

        <!-- 时间范围胶囊 -->
        <div class="inline-flex rounded-lg border border-gray-200 p-0.5 bg-gray-100/80 text-xs">
          <button
            type="button"
            @click="timeRangeFilter = '24h'; handleFilterChange()"
            class="rounded-md px-2.5 py-1 font-medium transition-colors"
            :class="timeRangeFilter === '24h' ? 'bg-white text-gray-800 shadow-xs' : 'text-gray-500 hover:text-gray-800'"
          >
            近 24 小时
          </button>
          <button
            type="button"
            @click="timeRangeFilter = '7d'; handleFilterChange()"
            class="rounded-md px-2.5 py-1 font-medium transition-colors"
            :class="timeRangeFilter === '7d' ? 'bg-white text-gray-800 shadow-xs' : 'text-gray-500 hover:text-gray-800'"
          >
            近 7 天
          </button>
          <button
            type="button"
            @click="timeRangeFilter = '30d'; handleFilterChange()"
            class="rounded-md px-2.5 py-1 font-medium transition-colors"
            :class="timeRangeFilter === '30d' ? 'bg-white text-gray-800 shadow-xs' : 'text-gray-500 hover:text-gray-800'"
          >
            近 30 天
          </button>
        </div>

        <!-- 工具名过滤输入框 -->
        <div class="relative">
          <MagnifyingGlassIcon class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <input
            v-model="toolNameFilter"
            @keyup.enter="handleFilterChange"
            type="text"
            placeholder="按工具名称过滤..."
            class="w-44 rounded-lg border border-gray-300 bg-white py-1.5 pl-8 pr-2.5 text-xs text-gray-800 placeholder-gray-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </div>

        <!-- 状态筛选 -->
        <select
          v-model="statusFilter"
          @change="handleFilterChange"
          class="rounded-lg border border-gray-300 bg-white py-1.5 px-2.5 text-xs font-medium text-gray-700 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        >
          <option value="all">全部状态</option>
          <option value="success">仅成功 (Success)</option>
          <option value="failed">仅失败 (Failed)</option>
        </select>

        <button
          type="button"
          @click="handleFilterChange"
          class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-100 transition-colors"
        >
          筛选
        </button>

        <button
          v-if="selectedServerId || toolNameFilter || statusFilter !== 'all' || timeRangeFilter !== '7d'"
          type="button"
          @click="resetFilters"
          class="text-xs text-gray-500 hover:text-indigo-600 transition-colors underline"
        >
          重置
        </button>
      </div>

      <button
        type="button"
        @click="fetchLogs"
        :disabled="loading"
        class="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3.5 py-1.5 text-xs font-semibold text-gray-700 shadow-2xs hover:bg-gray-50 transition-colors disabled:opacity-50"
      >
        <ArrowPathIcon class="h-3.5 w-3.5" :class="loading ? 'animate-spin text-indigo-600' : 'text-gray-500'" />
        刷新数据
      </button>
    </div>

    <!-- 流水列表区（可滚动） -->
    <div class="flex-1 overflow-y-auto bg-slate-50/50 p-4">
      <div v-if="loading && logs.length === 0" class="flex flex-col items-center justify-center h-64 text-gray-400">
        <ArrowPathIcon class="h-8 w-8 animate-spin text-indigo-500 mb-2" />
        <p class="text-xs">正在查询 MCP 出站审计记录...</p>
      </div>

      <div v-else-if="logs.length === 0" class="flex flex-col items-center justify-center h-64 text-gray-400">
        <DocumentTextIcon class="h-12 w-12 text-gray-300 mb-2" />
        <p class="text-sm font-medium text-gray-600">暂无出站调用记录</p>
        <p class="text-xs text-gray-400 mt-1">智能体在对话或执行工作流中调用任何外部 MCP 工具时，将自动在此记录审计流水</p>
      </div>

      <div v-else class="space-y-2.5">
        <div
          v-for="item in logs"
          :key="item.id"
          class="rounded-xl border border-gray-200 bg-white shadow-2xs transition-shadow hover:shadow-xs overflow-hidden"
        >
          <!-- 概要行（可点击展开） -->
          <div
            @click="toggleRowExpand(item.id)"
            class="flex flex-wrap items-center justify-between gap-3 px-4 py-3 cursor-pointer select-none hover:bg-slate-50/80 transition-colors"
          >
            <div class="flex items-center gap-3 min-w-0">
              <button
                type="button"
                class="text-gray-400 hover:text-gray-600 transition-transform duration-200"
                :class="expandedRowIds.has(item.id) ? 'rotate-90' : ''"
              >
                <ChevronRightIcon class="h-4 w-4" />
              </button>

              <!-- 状态徽标 -->
              <span
                v-if="item.status === 'success'"
                class="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700 border border-emerald-200/80"
              >
                <CheckCircleIcon class="h-3.5 w-3.5" />
                成功
              </span>
              <span
                v-else
                class="inline-flex items-center gap-1 rounded-md bg-rose-50 px-2 py-0.5 text-xs font-semibold text-rose-700 border border-rose-200/80"
              >
                <XCircleIcon class="h-3.5 w-3.5" />
                失败
              </span>

              <!-- 服务徽标与工具名 -->
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <span class="inline-flex items-center rounded-md bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-700/10">
                    {{ item.server_name || 'MCP 服务' }}
                  </span>
                  <span class="font-mono text-xs font-bold text-gray-900">{{ item.tool_name }}</span>
                  <span
                    class="rounded px-1.5 py-0.5 text-[10px] font-medium font-mono"
                    :class="
                      (item.latency_ms || 0) < 500
                        ? 'bg-emerald-50 text-emerald-700'
                        : (item.latency_ms || 0) < 2000
                        ? 'bg-amber-50 text-amber-700'
                        : 'bg-rose-50 text-rose-700'
                    "
                  >
                    {{ item.latency_ms ?? 0 }} ms
                  </span>
                </div>
                <div class="text-[11px] text-gray-400 truncate max-w-md font-mono mt-0.5">
                  Trace: {{ item.trace_id || '-' }}
                </div>
              </div>
            </div>

            <!-- 右侧时间与调用方摘要 -->
            <div class="flex items-center gap-4 text-xs text-gray-500">
              <div class="text-right">
                <div class="font-mono text-gray-700 text-xs">{{ formatTime(item.created_at) }}</div>
                <div class="text-[11px] text-gray-400 mt-0.5">
                  <span v-if="item.agent_name || item.agent_id" class="mr-1">Agent: {{ item.agent_name || item.agent_id?.substring(0, 8) }}</span>
                  <span v-if="item.user_name || item.user_id">User: {{ item.user_name || item.user_id?.substring(0, 8) }}</span>
                </div>
              </div>
              <button
                type="button"
                class="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-50 font-medium transition-colors"
              >
                {{ expandedRowIds.has(item.id) ? '收起明细' : '查看明细' }}
              </button>
            </div>
          </div>

          <!-- 展开详情面板 -->
          <div
            v-if="expandedRowIds.has(item.id)"
            class="border-t border-gray-100 bg-slate-50/50 p-4 space-y-4 animate-fade-in"
          >
            <!-- 链路标识栏 -->
            <div class="grid grid-cols-1 md:grid-cols-4 gap-2 bg-white p-3 rounded-lg border border-gray-200 text-xs">
              <div class="flex flex-col">
                <span class="text-gray-400 text-[10px]">Trace ID (全链路追踪)</span>
                <div class="flex items-center justify-between mt-0.5">
                  <span class="font-mono text-gray-700 truncate mr-1 select-all">{{ item.trace_id || '-' }}</span>
                  <button
                    v-if="item.trace_id"
                    type="button"
                    @click="copyContent('trace_' + item.id, item.trace_id)"
                    class="text-gray-400 hover:text-gray-600 p-1 rounded"
                    title="复制 Trace ID"
                  >
                    <CheckIcon v-if="copiedKeys['trace_' + item.id]" class="h-3.5 w-3.5 text-emerald-600" />
                    <ClipboardDocumentIcon v-else class="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              <div class="flex flex-col">
                <span class="text-gray-400 text-[10px]">智能体 (Agent)</span>
                <span class="font-mono text-gray-700 mt-0.5 truncate select-all">{{ item.agent_name || item.agent_id || '平台直接调用' }}</span>
              </div>

              <div class="flex flex-col">
                <span class="text-gray-400 text-[10px]">调用用户 (User)</span>
                <span class="font-mono text-gray-700 mt-0.5 truncate select-all">{{ item.user_name || item.user_id || '-' }}</span>
              </div>

              <div class="flex flex-col">
                <span class="text-gray-400 text-[10px]">耗时 (Latency)</span>
                <span class="font-mono text-gray-700 mt-0.5 truncate select-all">{{ item.latency_ms ?? 0 }} ms</span>
              </div>
            </div>

            <!-- 失败错误详情 (如有) -->
            <div
              v-if="item.status === 'failed' || item.error_message"
              class="rounded-lg border border-rose-200 bg-rose-50/80 p-3 text-xs"
            >
              <div class="flex items-center gap-1.5 font-bold text-rose-800 mb-1">
                <ExclamationTriangleIcon class="h-4 w-4 text-rose-600" />
                调用异常信息 (Error Message)
              </div>
              <div class="font-mono text-rose-700 whitespace-pre-wrap break-all bg-white/70 p-2.5 rounded border border-rose-200">
                {{ item.error_message || '未知错误' }}
              </div>
            </div>

            <!-- 入参与出参对照 -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <!-- 请求入参 -->
              <div class="rounded-lg border border-gray-200 bg-white overflow-hidden shadow-2xs">
                <div class="flex items-center justify-between px-3 py-2 bg-slate-100/70 border-b border-gray-200 text-xs font-semibold text-gray-700">
                  <span>请求参数 (Request Arguments)</span>
                  <button
                    type="button"
                    @click="copyContent('req_' + item.id, item.tool_input)"
                    class="inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-indigo-600 transition-colors"
                  >
                    <CheckIcon v-if="copiedKeys['req_' + item.id]" class="h-3.5 w-3.5 text-emerald-600" />
                    <ClipboardDocumentIcon v-else class="h-3.5 w-3.5" />
                    {{ copiedKeys['req_' + item.id] ? '已复制' : '复制 JSON' }}
                  </button>
                </div>
                <pre class="p-3 text-[11px] font-mono text-gray-800 bg-slate-900/5 overflow-x-auto max-h-60 leading-relaxed">{{ formatJson(item.tool_input) }}</pre>
              </div>

              <!-- 响应出参 -->
              <div class="rounded-lg border border-gray-200 bg-white overflow-hidden shadow-2xs">
                <div class="flex items-center justify-between px-3 py-2 bg-slate-100/70 border-b border-gray-200 text-xs font-semibold text-gray-700">
                  <span>响应结果 (Response Result)</span>
                  <button
                    type="button"
                    @click="copyContent('res_' + item.id, item.tool_output)"
                    class="inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-indigo-600 transition-colors"
                  >
                    <CheckIcon v-if="copiedKeys['res_' + item.id]" class="h-3.5 w-3.5 text-emerald-600" />
                    <ClipboardDocumentIcon v-else class="h-3.5 w-3.5" />
                    {{ copiedKeys['res_' + item.id] ? '已复制' : '复制 JSON' }}
                  </button>
                </div>
                <pre class="p-3 text-[11px] font-mono text-gray-800 bg-slate-900/5 overflow-x-auto max-h-60 leading-relaxed">{{ formatJson(item.tool_output) }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 底栏分页控制条 -->
    <div class="px-6 py-3.5 border-t border-gray-200 bg-white flex flex-wrap items-center justify-between gap-3 shrink-0">
      <div class="text-xs text-gray-500">
        共 <span class="font-bold text-gray-800">{{ total }}</span> 条全平台审计日志，当前第 {{ page }} / {{ totalPages }} 页
      </div>

      <div class="flex items-center gap-2">
        <button
          type="button"
          @click="prevPage"
          :disabled="page <= 1 || loading"
          class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          上一页
        </button>
        <span class="text-xs font-mono text-gray-600 px-1">{{ page }} / {{ totalPages }}</span>
        <button
          type="button"
          @click="nextPage"
          :disabled="page >= totalPages || loading"
          class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          下一页
        </button>
      </div>
    </div>
  </div>
</template>
