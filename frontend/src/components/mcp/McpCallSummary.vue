<script setup lang="ts">
/**
 * 「我的 MCP」调用概览。
 *
 * 全平台审计只对管理员开放（里面混着别人的调用明细），但服务属主应该能看到
 * 自己的服务被调了多少次、成功率如何、哪个工具最热门。这个卡片只请求
 * `/mcp/outbound-summary/mine`，后端按 `user_id = 自己` 聚合，不含他人明细。
 */
import { computed, onMounted, ref, watch } from 'vue'
import axios from '@/utils/axios'

interface SummaryMetrics {
  total_calls: number
  success_calls: number
  failed_calls: number
  success_rate: number
  avg_latency_ms: number
}

interface SummaryServer {
  server_id: string
  server_name: string
  total_calls: number
  failed_calls: number
}

interface SummaryTool {
  tool_name: string
  total_calls: number
}

interface SummaryDaily {
  date: string
  total_calls: number
}

interface SummaryPayload {
  range: string
  summary: SummaryMetrics
  servers: SummaryServer[]
  tools: SummaryTool[]
  daily: SummaryDaily[]
}

const RANGES = [
  { value: '24h', label: '近 24 小时' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
]

const range = ref('7d')
const expanded = ref(false)
const loading = ref(false)
const data = ref<SummaryPayload | null>(null)

const summary = computed<SummaryMetrics | null>(() => data.value?.summary ?? null)
const hasCalls = computed(() => (summary.value?.total_calls || 0) > 0)
const maxDaily = computed(() =>
  Math.max(1, ...(data.value?.daily || []).map((item) => item.total_calls)),
)
const failedTone = computed(() =>
  (summary.value?.failed_calls || 0) > 0
    ? 'text-rose-600 dark:text-rose-400'
    : 'text-gray-800 dark:text-gray-100',
)

async function load() {
  loading.value = true
  try {
    const response = await axios.get('/api/portal/mcp/outbound-summary/mine', {
      params: { range: range.value },
    })
    data.value = response.data?.data || response.data || null
  } catch (error) {
    // 概览是附加信息，取不到不影响主列表使用，静默降级
    console.warn('[MCP] 加载调用概览失败', error)
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(range, load)
onMounted(load)

defineExpose({ reload: load })
</script>

<template>
  <section
    class="rounded-xl border border-gray-100 bg-white px-4 py-3 shadow-sm dark:border-gray-800 dark:bg-gray-900"
  >
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
      <span class="text-xs font-semibold text-gray-700 dark:text-gray-200">调用概览</span>

      <div class="flex items-center gap-1 rounded-lg bg-gray-100 p-0.5 dark:bg-gray-800">
        <button
          v-for="item in RANGES"
          :key="item.value"
          type="button"
          class="rounded-md px-2 py-0.5 text-[11px] transition-colors"
          :class="range === item.value
            ? 'bg-white font-medium text-emerald-700 shadow-sm dark:bg-gray-700 dark:text-emerald-300'
            : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'"
          @click="range = item.value"
        >
          {{ item.label }}
        </button>
      </div>

      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-500 dark:text-gray-400">
        <span>
          调用
          <strong class="ml-0.5 text-sm font-bold tabular-nums text-gray-800 dark:text-gray-100">
            {{ summary?.total_calls ?? 0 }}
          </strong>
          次
        </span>
        <span>
          成功率
          <strong class="ml-0.5 text-sm font-bold tabular-nums text-emerald-600 dark:text-emerald-400">
            {{ summary?.success_rate ?? 100 }}%
          </strong>
        </span>
        <span>
          失败
          <strong class="ml-0.5 text-sm font-bold tabular-nums" :class="failedTone">
            {{ summary?.failed_calls ?? 0 }}
          </strong>
          次
        </span>
        <span>
          平均耗时
          <strong class="ml-0.5 text-sm font-bold tabular-nums text-gray-800 dark:text-gray-100">
            {{ summary?.avg_latency_ms ?? 0 }}
          </strong>
          ms
        </span>
      </div>

      <button
        v-if="hasCalls"
        type="button"
        class="ml-auto flex items-center gap-1 text-[11px] text-gray-400 transition-colors hover:text-emerald-600 dark:text-gray-500 dark:hover:text-emerald-400"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起' : '明细' }}
        <svg
          class="h-3 w-3 transition-transform"
          :class="expanded ? 'rotate-180' : ''"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
    </div>

    <p v-if="!hasCalls && !loading" class="mt-1.5 text-[11px] text-gray-400 dark:text-gray-500">
      这段时间内还没有调用记录。挂载到智能体后，这里的调用量、成功率和热门工具会自动统计。
    </p>

    <div v-if="expanded && hasCalls" class="mt-3 grid gap-3 border-t border-gray-100 pt-3 dark:border-gray-800 md:grid-cols-3">
      <div class="min-w-0">
        <p class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">调用最多的服务</p>
        <ul class="space-y-1">
          <li
            v-for="item in data?.servers || []"
            :key="item.server_id"
            class="flex items-center justify-between gap-2 text-[11px] text-gray-600 dark:text-gray-300"
          >
            <span class="truncate font-mono">{{ item.server_name }}</span>
            <span class="shrink-0 tabular-nums">
              {{ item.total_calls }}
              <span v-if="item.failed_calls" class="text-rose-500">/ 失败 {{ item.failed_calls }}</span>
            </span>
          </li>
        </ul>
      </div>

      <div class="min-w-0">
        <p class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">热门工具</p>
        <ul class="space-y-1">
          <li
            v-for="item in data?.tools || []"
            :key="item.tool_name"
            class="flex items-center justify-between gap-2 text-[11px] text-gray-600 dark:text-gray-300"
          >
            <span class="truncate font-mono">{{ item.tool_name }}</span>
            <span class="shrink-0 tabular-nums">{{ item.total_calls }}</span>
          </li>
        </ul>
      </div>

      <div class="min-w-0">
        <p class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">每日调用量</p>
        <div class="flex h-16 items-end gap-1">
          <div
            v-for="item in data?.daily || []"
            :key="item.date"
            class="flex-1 rounded-t bg-emerald-200 transition-colors hover:bg-emerald-400 dark:bg-emerald-900/60 dark:hover:bg-emerald-600"
            :style="{ height: `${Math.max(6, (item.total_calls / maxDaily) * 100)}%` }"
            :title="`${item.date}：${item.total_calls} 次`"
          ></div>
        </div>
      </div>
    </div>
  </section>
</template>
