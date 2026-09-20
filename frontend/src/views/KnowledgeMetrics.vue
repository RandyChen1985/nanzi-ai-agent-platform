<template>
  <div class="space-y-6 pb-12">
    <!-- Header -->
    <div class="flex flex-col gap-4">
      <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
        <div>
          <h1 class="text-xl sm:text-2xl font-bold text-gray-900 tracking-tight flex items-center gap-2">
            <svg class="h-7 w-7 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 3.055A9.003 9.003 0 1020.945 13H11V3.055z" />
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z" />
            </svg>
            知识库运营分析
          </h1>
          <p class="text-sm text-gray-500 mt-1">统计各知识库和具体文档被智能体调用、检索与最终引用的全生命周期行为指标</p>
        </div>

        <div class="flex items-center gap-3 flex-wrap">
          <!-- 区间快捷选择 -->
          <div
            role="group"
            aria-label="统计区间"
            class="inline-flex rounded-xl border border-gray-200 bg-white p-0.5 shadow-sm"
          >
            <button
              v-for="option in PERIOD_OPTIONS"
              :key="option.value"
              type="button"
              :aria-pressed="period === option.value"
              class="px-3 py-1.5 text-sm font-bold rounded-[10px] transition-all active:scale-95"
              :class="period === option.value ? 'bg-primary text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'"
              @click="selectPeriod(option.value)"
            >
              {{ option.label }}
            </button>
            <button
              type="button"
              :aria-pressed="period === 'custom'"
              class="px-3 py-1.5 text-sm font-bold rounded-[10px] transition-all active:scale-95"
              :class="period === 'custom' ? 'bg-primary text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'"
              @click="openCustomRange"
            >
              自定义
            </button>
          </div>

          <button
            type="button"
            aria-label="刷新数据"
            :aria-busy="loading"
            :disabled="loading"
            class="inline-flex items-center justify-center min-h-[38px] min-w-[38px] p-2 sm:px-4 sm:py-2 border border-gray-200 rounded-xl shadow-sm text-sm font-bold text-gray-700 bg-white hover:bg-gray-50 active:scale-95 transition-all disabled:opacity-50"
            @click="fetchMetrics({ notify: true })"
          >
            <svg
              class="h-5 w-5 text-primary"
              :class="{ 'animate-spin': loading, 'sm:mr-2': true }"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2.5"
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            <span class="hidden sm:inline">刷新</span>
          </button>
        </div>
      </div>

      <!-- 自定义区间 -->
      <div
        v-if="period === 'custom'"
        class="flex flex-wrap items-end gap-3 rounded-xl border border-gray-200 bg-white p-3 shadow-sm"
      >
        <div class="flex flex-col gap-1">
          <label for="metrics-start-date" class="text-xs font-medium text-gray-500">开始日期</label>
          <input
            id="metrics-start-date"
            v-model="customStart"
            type="date"
            :max="customEnd || today"
            class="rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-700 outline-none focus:border-primary focus:ring-1 focus:ring-primary"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label for="metrics-end-date" class="text-xs font-medium text-gray-500">结束日期</label>
          <input
            id="metrics-end-date"
            v-model="customEnd"
            type="date"
            :min="customStart"
            :max="today"
            class="rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-700 outline-none focus:border-primary focus:ring-1 focus:ring-primary"
          />
        </div>
        <button
          type="button"
          class="px-4 py-2 rounded-lg bg-primary text-white text-sm font-bold hover:bg-primary-hover active:scale-95 transition-all disabled:opacity-50"
          :disabled="!canApplyCustomRange || loading"
          @click="applyCustomRange"
        >
          应用
        </button>
        <p v-if="customRangeError" class="text-xs text-amber-600">{{ customRangeError }}</p>
      </div>

      <!-- 口径与新鲜度 -->
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-400" aria-live="polite">
        <span>统计区间：{{ activeRange.startDate }} ~ {{ activeRange.endDate }}（{{ activeRangeDays }} 天）</span>
        <span>数据截至：{{ formatDataFreshness(summary?.last_updated ?? null) }}</span>
      </div>
    </div>

    <!-- 错误态：保留旧数据的同时给出可重试的明确信号 -->
    <div
      v-if="errorMessage"
      role="alert"
      class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3"
    >
      <div class="flex items-start gap-2 text-sm text-rose-700">
        <svg class="h-5 w-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
        </svg>
        <div>
          <p class="font-medium">{{ errorMessage }}</p>
          <p v-if="hasLoaded" class="text-xs text-rose-500 mt-0.5">下方展示的是上一次成功加载的数据。</p>
        </div>
      </div>
      <button
        type="button"
        class="px-3 py-1.5 rounded-lg border border-rose-300 bg-white text-sm font-bold text-rose-700 hover:bg-rose-50 active:scale-95 transition-all"
        @click="fetchMetrics({ notify: true })"
      >
        重试
      </button>
    </div>

    <!-- Overview Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <KnowledgeMetricStatCard
        v-for="card in statCards"
        :key="card.key"
        :label="card.label"
        :value="card.value"
        :description="card.description"
        :hint="card.hint"
        :tone="card.tone"
        :delta="card.delta"
        :delta-title="card.deltaTitle"
        :loading="showSkeleton"
        :warning="card.warning"
      >
        <template #icon>
          <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              v-for="(pathData, index) in card.iconPaths"
              :key="index"
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              :d="pathData"
            />
          </svg>
        </template>
      </KnowledgeMetricStatCard>
    </div>

    <!-- Trend Analysis -->
    <section class="bg-white rounded-2xl shadow-sm p-4 sm:p-6 border border-gray-100">
      <div class="mb-4">
        <h2 class="text-lg font-bold text-gray-900">每日检索与引用趋势</h2>
        <p class="text-xs text-gray-500 mt-1">展示每日知识库检索量与大模型最终采纳引用量的动态趋势对比</p>
      </div>
      <div class="h-72 w-full">
        <div
          v-if="showSkeleton"
          class="h-full w-full rounded-xl bg-gray-100 animate-pulse"
          aria-hidden="true"
        ></div>
        <KnowledgeMetricsEmptyState
          v-else-if="!hasTrendActivity"
          title="所选区间内还没有检索记录"
          description="智能体调用知识库后，这里会显示每日的检索量与引用量趋势。"
        />
        <v-chart
          v-else
          class="h-full w-full"
          :option="trendOption"
          :aria-label="trendAriaLabel"
          autoresize
        />
      </div>
    </section>

    <!-- Top Ranking Section -->
    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
      <h2 class="text-lg font-bold text-gray-900">引用排行榜 (Top 10)</h2>
      <div class="flex items-center gap-3 flex-wrap">
        <div role="group" aria-label="排行榜排序依据" class="inline-flex rounded-xl border border-gray-200 bg-white p-0.5 shadow-sm">
          <button
            v-for="option in ORDER_OPTIONS"
            :key="option.value"
            type="button"
            :aria-pressed="orderBy === option.value"
            class="px-3 py-1.5 text-xs font-bold rounded-[10px] transition-all active:scale-95"
            :class="orderBy === option.value ? 'bg-gray-900 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'"
            @click="selectOrderBy(option.value)"
          >
            {{ option.label }}
          </button>
        </div>
        <p class="text-xs text-gray-400">点击柱条可跳转对应知识库{{ orderBy === 'citation' ? '' : '（按检索量排序）' }}</p>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Top 10 Knowledge Bases -->
      <section class="bg-white rounded-2xl shadow-sm p-4 sm:p-6 border border-gray-100 flex flex-col">
        <div class="mb-4 flex items-start justify-between gap-3">
          <div>
            <h3 class="text-base font-bold text-gray-900">知识库引用排行榜 (Top 10)</h3>
            <p class="text-xs text-gray-500 mt-1">按{{ orderByLabel }}排序的知识库 Top 10</p>
          </div>
          <button
            type="button"
            class="flex-shrink-0 inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-gray-200 text-xs font-bold text-gray-600 hover:bg-gray-50 active:scale-95 transition-all disabled:opacity-40"
            :disabled="!summary || summary.datasets.length === 0"
            @click="exportCsv('datasets')"
          >
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
            </svg>
            CSV
          </button>
        </div>
        <div class="h-80 w-full min-h-[320px]">
          <div
            v-if="showSkeleton"
            class="h-full w-full rounded-xl bg-gray-100 animate-pulse"
            aria-hidden="true"
          ></div>
          <KnowledgeMetricsEmptyState
            v-else-if="!summary || summary.datasets.length === 0"
            title="暂无知识库统计"
            description="所选区间内没有知识库检索记录，或你无权查看相关知识库。"
          />
          <v-chart
            v-else
            class="h-full w-full cursor-pointer"
            :option="datasetOption"
            :aria-label="`知识库${orderByLabel}排行榜前 ${summary.datasets.length} 名`"
            autoresize
            @click="onDatasetChartClick"
          />
        </div>
      </section>

      <!-- Top 10 Documents -->
      <section class="bg-white rounded-2xl shadow-sm p-4 sm:p-6 border border-gray-100 flex flex-col">
        <div class="mb-4 flex items-start justify-between gap-3">
          <div>
            <h3 class="text-base font-bold text-gray-900">核心文档引用排行榜 (Top 10)</h3>
            <p class="text-xs text-gray-500 mt-1">按{{ orderByLabel }}排序的物理文件 Top 10，名称包含所属知识库</p>
          </div>
          <button
            type="button"
            class="flex-shrink-0 inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-gray-200 text-xs font-bold text-gray-600 hover:bg-gray-50 active:scale-95 transition-all disabled:opacity-40"
            :disabled="!summary || summary.documents.length === 0"
            @click="exportCsv('documents')"
          >
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
            </svg>
            CSV
          </button>
        </div>
        <div class="h-80 w-full min-h-[320px]">
          <div
            v-if="showSkeleton"
            class="h-full w-full rounded-xl bg-gray-100 animate-pulse"
            aria-hidden="true"
          ></div>
          <KnowledgeMetricsEmptyState
            v-else-if="!summary || summary.documents.length === 0"
            title="暂无文档统计"
            description="所选区间内没有文档级检索记录，或你无权查看相关知识库。"
          />
          <v-chart
            v-else
            class="h-full w-full"
            :option="documentOption"
            :aria-label="`文档${orderByLabel}排行榜前 ${summary.documents.length} 名`"
            autoresize
          />
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import axios from '../utils/axios'
import { useToast } from '@/composables/useToast'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart } from 'echarts/charts'
import {
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import KnowledgeMetricStatCard from '@/components/knowledge/KnowledgeMetricStatCard.vue'
import KnowledgeMetricsEmptyState from '@/components/knowledge/KnowledgeMetricsEmptyState.vue'
import {
  buildMetricsCsv,
  buildRankingOption,
  buildTrendOption,
  computeCitationRate,
  computeDelta,
  formatCitationRate,
  formatCount,
  formatDataFreshness,
  metricsCsvFileName,
  METRICS_COLORS,
  type KnowledgeMetricsSummary,
  type MetricsOrderBy,
} from '@/utils/knowledgeMetricsChart'

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
])

/** 快捷区间选项：`days` 按「含今天在内的 N 天」解释。 */
const PERIOD_OPTIONS = [
  { value: 'week', label: '近 7 天', days: 7 },
  { value: 'month', label: '近 30 天', days: 30 },
  { value: 'quarter', label: '近 90 天', days: 90 },
] as const

const ORDER_OPTIONS: Array<{ value: MetricsOrderBy; label: string }> = [
  { value: 'citation', label: '按引用量' },
  { value: 'search', label: '按检索量' },
]

const router = useRouter()
const { showToast } = useToast()

const loading = ref(false)
const hasLoaded = ref(false)
const errorMessage = ref('')
const period = ref<(typeof PERIOD_OPTIONS)[number]['value'] | 'custom'>('week')
const orderBy = ref<MetricsOrderBy>('citation')

const customStart = ref('')
const customEnd = ref('')
const appliedCustomRange = ref<{ startDate: string; endDate: string } | null>(null)

const summary = ref<KnowledgeMetricsSummary | null>(null)

const today = (() => {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
})()

const formatDateValue = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`

/**
 * 计算快捷区间。此前用 `getDate() - 7` 会把首尾都算进去，实际是 8 天，
 * 这里按「含今天在内的 N 天」计算，与界面文案一致。
 */
const getQuickRange = (days: number) => {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - (days - 1))
  return { startDate: formatDateValue(start), endDate: formatDateValue(end) }
}

const activeRange = computed(() => {
  if (period.value === 'custom' && appliedCustomRange.value) {
    return appliedCustomRange.value
  }
  const option = PERIOD_OPTIONS.find((item) => item.value === period.value)
  return getQuickRange(option?.days ?? 7)
})

const activeRangeDays = computed(() => {
  const start = new Date(`${activeRange.value.startDate}T00:00:00`)
  const end = new Date(`${activeRange.value.endDate}T00:00:00`)
  return Math.max(1, Math.round((end.getTime() - start.getTime()) / 86400000) + 1)
})

const customRangeError = computed(() => {
  if (period.value !== 'custom') return ''
  if (!customStart.value || !customEnd.value) return '请选择完整的起止日期'
  if (customStart.value > customEnd.value) return '开始日期不能晚于结束日期'
  return ''
})

const canApplyCustomRange = computed(() => period.value === 'custom' && !customRangeError.value)

/** 首屏（尚无任何数据）显示骨架；后续刷新只转圈，不闪空态。 */
const showSkeleton = computed(() => loading.value && !hasLoaded.value)

const trendOption = computed(() => buildTrendOption(summary.value?.trend ?? []))
const datasetOption = computed(() =>
  buildRankingOption({
    items: summary.value?.datasets ?? [],
    searchColor: METRICS_COLORS.search,
    citationColor: METRICS_COLORS.citation,
  }),
)
const documentOption = computed(() =>
  buildRankingOption({
    items: summary.value?.documents ?? [],
    searchColor: METRICS_COLORS.searchAlt,
    citationColor: METRICS_COLORS.citationAlt,
  }),
)

const hasTrendActivity = computed(() =>
  (summary.value?.trend ?? []).some(
    (point) => (point.search_count || 0) > 0 || (point.citation_count || 0) > 0,
  ),
)

const orderByLabel = computed(() => (orderBy.value === 'citation' ? '引用量' : '检索量'))

const trendAriaLabel = computed(() => {
  const trend = summary.value?.trend ?? []
  const searchTotal = trend.reduce((sum, point) => sum + (point.search_count || 0), 0)
  const citationTotal = trend.reduce((sum, point) => sum + (point.citation_count || 0), 0)
  return `每日检索与引用趋势折线图，区间共 ${trend.length} 天，检索量合计 ${searchTotal}，引用量合计 ${citationTotal}`
})

const citationRate = computed(() => computeCitationRate(summary.value?.totals ?? null))
const previousCitationRate = computed(() => {
  const previous = summary.value?.previous_totals ?? null
  if (!previous) return null
  return computeCitationRate(previous)
})

const statCards = computed(() => {
  const totals = summary.value?.totals ?? { search_count: 0, citation_count: 0, active_docs: 0 }
  const previous = summary.value?.previous_totals ?? null
  const rate = citationRate.value
  // 检索量为 0 时引用率本身无法计算，此时不应拿 0 去比出「-100%」这种假信号
  const rateDelta = rate == null
    ? { text: null, direction: 'flat' as const }
    : computeDelta(rate, previousCitationRate.value)

  return [
    {
      key: 'search',
      label: '区间检索量 (RAG)',
      value: formatCount(totals.search_count),
      description: '召回匹配的切片次数',
      hint: '所选区间内知识库检索命中的切片累计次数（文档维度口径，与趋势图一致）',
      tone: 'blue' as const,
      delta: computeDelta(totals.search_count, previous?.search_count),
      deltaTitle: '与上一个等长周期对比',
      iconPaths: ['M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z'],
      warning: null,
    },
    {
      key: 'citation',
      label: '区间引用量',
      value: formatCount(totals.citation_count),
      description: '最终出现在模型回答中的次数',
      hint: '所选区间内被模型回答实际引用的切片累计次数',
      tone: 'indigo' as const,
      delta: computeDelta(totals.citation_count, previous?.citation_count),
      deltaTitle: '与上一个等长周期对比',
      iconPaths: [
        'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
      ],
      warning: null,
    },
    {
      key: 'rate',
      label: '引用率',
      value: formatCitationRate(rate),
      description: '引用量 ÷ 检索量（区间聚合）',
      hint: '所选区间内「引用量 / 检索量」的聚合比值，不是每日引用率的平均值',
      tone: 'emerald' as const,
      delta: rateDelta,
      deltaTitle: '与上一个等长周期的引用率对比',
      iconPaths: [
        'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
      ],
      warning:
        rate != null && rate > 100
          ? '引用量高于检索量，请检查埋点数据口径'
          : null,
    },
    {
      key: 'activeDocs',
      label: '活跃文献源',
      value: formatCount(totals.active_docs),
      description: '有检索记录的文档数',
      hint: '所选区间内检索次数大于 0 的去重文档数量',
      tone: 'amber' as const,
      delta: computeDelta(totals.active_docs, previous?.active_docs),
      deltaTitle: '与上一个等长周期对比',
      iconPaths: [
        'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.168.477 4 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4 1.253',
      ],
      warning: null,
    },
  ]
})

// 只认最后一次请求：快速切换区间/排序时，早发出的响应不得覆盖新结果
let requestSeq = 0
let activeController: AbortController | null = null

const isCanceled = (error: unknown) => {
  const candidate = error as { code?: string; name?: string } | null
  return candidate?.code === 'ERR_CANCELED' || candidate?.name === 'CanceledError'
}

const extractErrorMessage = (error: unknown) => {
  const candidate = error as { response?: { data?: { detail?: string; message?: string } }; message?: string } | null
  return (
    candidate?.response?.data?.detail
    || candidate?.response?.data?.message
    || candidate?.message
    || '未知错误'
  )
}

const fetchMetrics = async (options: { notify?: boolean } = {}) => {
  const seq = ++requestSeq
  activeController?.abort()
  const controller = new AbortController()
  activeController = controller

  loading.value = true
  errorMessage.value = ''
  const { startDate, endDate } = activeRange.value

  try {
    const res = await axios.get('/api/portal/ragflow/metrics/summary', {
      params: { start_date: startDate, end_date: endDate, order_by: orderBy.value },
      signal: controller.signal,
    })
    if (seq !== requestSeq) return
    if (!res.data || res.data.code !== 0) {
      throw new Error(res.data?.message || '接口返回异常')
    }

    summary.value = res.data.data as KnowledgeMetricsSummary
    hasLoaded.value = true

    if (options.notify) showToast('数据已刷新', 'success')
  } catch (error) {
    if (seq !== requestSeq || isCanceled(error)) return
    const detail = extractErrorMessage(error)
    errorMessage.value = `数据加载失败：${detail}`
    showToast('数据加载失败，请稍后重试', 'error')
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

const selectPeriod = (value: (typeof PERIOD_OPTIONS)[number]['value']) => {
  if (period.value === value) return
  period.value = value
  fetchMetrics()
}

/**
 * 进入自定义模式时先沿用当前生效区间预填输入框，
 * 否则「统计区间」文案会退回默认 7 天，与页面上仍是旧区间的数据错位。
 */
const openCustomRange = () => {
  if (!appliedCustomRange.value) {
    const current = activeRange.value
    appliedCustomRange.value = { ...current }
    customStart.value = current.startDate
    customEnd.value = current.endDate
  }
  period.value = 'custom'
}

const selectOrderBy = (value: MetricsOrderBy) => {
  if (orderBy.value === value) return
  orderBy.value = value
  fetchMetrics()
}

const applyCustomRange = () => {
  if (!canApplyCustomRange.value) return
  appliedCustomRange.value = { startDate: customStart.value, endDate: customEnd.value }
  fetchMetrics()
}

const onDatasetChartClick = (params: unknown) => {
  const dataIndex = (params as { dataIndex?: number } | null)?.dataIndex
  const item = dataIndex == null ? null : summary.value?.datasets[dataIndex]
  if (!item) return
  router.push({ path: '/dashboard/knowledge-bases', query: { dataset: item.id } })
}

const exportCsv = (scope: 'datasets' | 'documents') => {
  const current = summary.value
  if (!current || current[scope].length === 0) {
    showToast('当前没有可导出的数据', 'warning')
    return
  }
  const blob = new Blob([buildMetricsCsv(current, scope)], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = metricsCsvFileName(scope, current.range)
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
  showToast('已导出 CSV', 'success')
}

onMounted(() => {
  const initial = getQuickRange(7)
  customStart.value = initial.startDate
  customEnd.value = initial.endDate
  fetchMetrics()
})

onUnmounted(() => {
  requestSeq += 1
  activeController?.abort()
  activeController = null
})
</script>

<style scoped>
.text-primary {
  color: var(--color-primary, #3b82f6);
}
</style>
