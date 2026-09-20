/**
 * 知识库运营分析：共享类型、格式化与 ECharts 配置构建。
 *
 * 抽出的原因：视图内原先重复书写网格/轴/图例/色板，且排行榜与趋势图各写一套；
 * 同时把「按全名展示、按选择字段排序、可下钻」等展示契约固定在纯函数里，便于测试。
 */
import type { EChartsOption } from 'echarts'

export interface KnowledgeMetricsTotals {
  search_count: number
  citation_count: number
  active_docs: number
}

export interface KnowledgeMetricsPreviousTotals extends KnowledgeMetricsTotals {
  start_date: string
  end_date: string
}

export interface KnowledgeMetricsTrendPoint {
  date: string
  search_count: number
  citation_count: number
}

export interface KnowledgeMetricsRankItem {
  id: string
  name: string | null
  search_count: number
  citation_count: number
}

export interface KnowledgeMetricsSummary {
  datasets: KnowledgeMetricsRankItem[]
  documents: KnowledgeMetricsRankItem[]
  trend: KnowledgeMetricsTrendPoint[]
  active_docs: number
  totals: KnowledgeMetricsTotals
  previous_totals: KnowledgeMetricsPreviousTotals | null
  last_updated: string | null
  order_by: MetricsOrderBy
  range: { start_date: string; end_date: string }
}

export type MetricsOrderBy = 'citation' | 'search'

/** 超过该天数后，趋势图启用缩放且坐标轴标签改用更短的日期粒度。 */
export const TREND_ZOOM_THRESHOLD_DAYS = 31
export const TREND_MONTH_LABEL_THRESHOLD_DAYS = 120

/**
 * 图表色板：全部在白色背景上达到 WCAG 非文本 3:1 对比度，替换原先的浅蓝/淡紫。
 * 两个排行榜使用不同色系以区分维度，但不依赖颜色单独区分系列（同时启用 decal 纹理）。
 */
export const METRICS_COLORS = {
  search: '#2563eb',
  citation: '#7c3aed',
  searchAlt: '#0891b2',
  citationAlt: '#db2777',
} as const

const countFormatter = new Intl.NumberFormat('zh-CN')

/** 图表与卡片统一使用同一份 Intl 实例，避免每次渲染重建。 */
export function formatCount(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '0'
  return countFormatter.format(value)
}

export interface DeltaResult {
  /** 展示用百分比文本，无法比较时为 null */
  text: string | null
  /** up / down / flat，供着色与图标使用 */
  direction: 'up' | 'down' | 'flat'
}

/**
 * 环比计算。上期为 0 时不做除法，返回「新增」而不是伪造 +100%。
 */
export function computeDelta(current: number, previous: number | null | undefined): DeltaResult {
  if (previous == null || Number.isNaN(previous)) {
    return { text: null, direction: 'flat' }
  }
  if (previous === 0) {
    if (current === 0) return { text: '持平', direction: 'flat' }
    return { text: '新增', direction: 'up' }
  }
  const ratio = ((current - previous) / previous) * 100
  if (Math.abs(ratio) < 0.05) return { text: '持平', direction: 'flat' }
  const direction = ratio > 0 ? 'up' : 'down'
  return { text: `${ratio > 0 ? '+' : ''}${ratio.toFixed(1)}%`, direction }
}

/**
 * 引用率 = 引用量 / 检索量（区间聚合比值，非日均）。
 * 超过 100% 说明埋点口径异常，这里如实返回而不做截断，交由调用方提示。
 */
export function computeCitationRate(totals: KnowledgeMetricsTotals | null): number | null {
  if (!totals || !totals.search_count) return null
  return (totals.citation_count / totals.search_count) * 100
}

export function formatCitationRate(rate: number | null): string {
  if (rate == null) return '—'
  return `${rate.toFixed(1)}%`
}

/** 后端返回的是无时区的本地时间字符串，直接截取展示，避免被误当作 UTC 换算。 */
export function formatDataFreshness(lastUpdated: string | null): string {
  if (!lastUpdated) return '暂无同步记录'
  const normalized = lastUpdated.replace('T', ' ')
  return normalized.slice(0, 16)
}

function axisDateLabel(date: string, totalPoints: number): string {
  if (totalPoints >= TREND_MONTH_LABEL_THRESHOLD_DAYS) return date.slice(0, 7)
  return date.slice(5)
}

const AXIS_COLOR = '#6b7280'
const SPLIT_LINE_COLOR = '#f1f5f9'
const AXIS_LINE_COLOR = '#e2e8f0'
const TOOLTIP_STYLE = {
  backgroundColor: 'rgba(255, 255, 255, 0.97)',
  borderColor: '#e2e8f0',
  borderWidth: 1,
  textStyle: { color: '#1f2937', fontSize: 12 },
  extraCssText: 'box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08); border-radius: 10px;',
} as const

/** 营养可访问性：启用 decal 纹理，使两个系列不依赖颜色即可区分。 */
const ARIA_CONFIG = {
  enabled: true,
  decal: { show: true },
} as const

export function buildTrendOption(trend: KnowledgeMetricsTrendPoint[]): EChartsOption {
  const dates = trend.map((point) => point.date)
  const totalPoints = dates.length
  const nonZeroDays = trend.filter(
    (point) => (point.search_count || 0) > 0 || (point.citation_count || 0) > 0,
  ).length
  // 仅在几乎没有数据点时显示标记，避免满屏圆点遮挡趋势
  const showSymbol = nonZeroDays <= 2
  const enableZoom = totalPoints > TREND_ZOOM_THRESHOLD_DAYS

  return {
    aria: ARIA_CONFIG,
    tooltip: {
      trigger: 'axis',
      ...TOOLTIP_STYLE,
      valueFormatter: (value) => formatCount(Number(value)),
    },
    legend: {
      data: ['检索量 (RAG)', '引用量'],
      top: 0,
      right: 0,
      icon: 'roundRect',
      textStyle: { color: '#4b5563' },
    },
    grid: {
      left: 8,
      right: 16,
      bottom: enableZoom ? 44 : 4,
      top: 40,
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: dates,
      axisLine: { lineStyle: { color: AXIS_LINE_COLOR } },
      axisTick: { show: false },
      axisLabel: {
        color: AXIS_COLOR,
        hideOverlap: true,
        margin: 12,
        formatter: (value: string) => axisDateLabel(value, totalPoints),
      },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      name: '次数',
      nameTextStyle: { color: AXIS_COLOR, fontSize: 11, padding: [0, 0, 4, 0] },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { type: 'dashed', color: SPLIT_LINE_COLOR } },
      axisLabel: { color: AXIS_COLOR, formatter: (value: number) => formatCount(value) },
    },
    dataZoom: enableZoom
      ? [
          { type: 'inside', throttle: 60 },
          { type: 'slider', height: 18, bottom: 8, borderColor: 'transparent', fillerColor: 'rgba(37, 99, 235, 0.08)' },
        ]
      : undefined,
    series: [
      {
        name: '检索量 (RAG)',
        type: 'line',
        smooth: true,
        showSymbol,
        symbolSize: 8,
        data: trend.map((point) => point.search_count || 0),
        itemStyle: { color: METRICS_COLORS.search },
        lineStyle: { width: 2, color: METRICS_COLORS.search },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(37, 99, 235, 0.18)' },
              { offset: 1, color: 'rgba(37, 99, 235, 0)' },
            ],
          },
        },
      },
      {
        name: '引用量',
        type: 'line',
        smooth: true,
        showSymbol,
        symbolSize: 8,
        data: trend.map((point) => point.citation_count || 0),
        itemStyle: { color: METRICS_COLORS.citation },
        // 虚线让两条趋势在色觉障碍下依然可区分
        lineStyle: { width: 2, color: METRICS_COLORS.citation, type: 'dashed' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(124, 58, 237, 0.16)' },
              { offset: 1, color: 'rgba(124, 58, 237, 0)' },
            ],
          },
        },
      },
    ],
  }
}

export interface RankingOptionParams {
  items: KnowledgeMetricsRankItem[]
  searchColor?: string
  citationColor?: string
}

/** 文档维度的名称快照已由后端拼为「知识库名 / 文件名」，此处直接展示。 */
export function rankingDisplayName(item: KnowledgeMetricsRankItem): string {
  return item.name || item.id || '未知'
}

export function buildRankingOption({
  items,
  searchColor = METRICS_COLORS.search,
  citationColor = METRICS_COLORS.citation,
}: RankingOptionParams): EChartsOption {
  const labels = items.map((item) => rankingDisplayName(item))
  const searchCounts = items.map((item) => item.search_count || 0)
  const citationCounts = items.map((item) => item.citation_count || 0)

  return {
    aria: ARIA_CONFIG,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      ...TOOLTIP_STYLE,
      formatter: (params: unknown) => {
        const entries = Array.isArray(params) ? params : [params]
        const first = entries[0] as { dataIndex?: number } | undefined
        const index = first?.dataIndex ?? 0
        const title = labels[index] ?? '未知'
        const lines = entries.map((entry) => {
          const item = entry as { marker?: string; seriesName?: string; value?: number }
          return `${item.marker ?? ''}${item.seriesName ?? ''}: ${formatCount(Number(item.value ?? 0))}`
        })
        return [`<span style="font-weight:600">${title}</span>`, ...lines].join('<br/>')
      },
    },
    legend: {
      data: ['检索量', '引用量'],
      top: 0,
      right: 0,
      icon: 'roundRect',
      textStyle: { color: '#4b5563', fontSize: 11 },
    },
    // containLabel 会自动为长中文名预留宽度，无需手工估算
    grid: { left: 8, right: 24, top: 36, bottom: 8, containLabel: true },
    xAxis: {
      type: 'value',
      minInterval: 1,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { type: 'dashed', color: SPLIT_LINE_COLOR } },
      axisLabel: { color: AXIS_COLOR, formatter: (value: number) => formatCount(value) },
    },
    yAxis: {
      type: 'category',
      data: labels,
      // 后端已按当前排序字段降序返回，inverse 让第一名落在顶部
      inverse: true,
      axisLine: { lineStyle: { color: AXIS_LINE_COLOR } },
      axisTick: { show: false },
      axisLabel: {
        color: AXIS_COLOR,
        fontSize: 11,
        width: 148,
        overflow: 'truncate',
      },
    },
    series: [
      {
        name: '检索量',
        type: 'bar',
        data: searchCounts,
        barMaxWidth: 12,
        barGap: '20%',
        itemStyle: { color: searchColor, borderRadius: [0, 4, 4, 0] },
      },
      {
        name: '引用量',
        type: 'bar',
        data: citationCounts,
        barMaxWidth: 12,
        itemStyle: { color: citationColor, borderRadius: [0, 4, 4, 0] },
      },
    ],
  }
}

/** 导出当前区间明细为 CSV，带 BOM 以便 Excel 正确识别中文。 */
export function buildMetricsCsv(
  summary: KnowledgeMetricsSummary,
  scope: 'datasets' | 'documents',
): string {
  const header = scope === 'documents'
    ? ['文档名称', '文档ID', '检索量', '引用量']
    : ['知识库', '知识库ID', '检索量', '引用量']
  const lines = summary[scope].map((item) => [
    item.name || '',
    item.id,
    String(item.search_count),
    String(item.citation_count),
  ])
  const escape = (cell: string) => `"${String(cell).replace(/"/g, '""')}"`
  const rows = [header, ...lines].map((row) => row.map(escape).join(','))
  return `\ufeff${rows.join('\r\n')}`
}

export function metricsCsvFileName(
  scope: 'datasets' | 'documents',
  range: { start_date: string; end_date: string },
): string {
  const label = scope === 'documents' ? '文档引用排行' : '知识库引用排行'
  return `${label}_${range.start_date}_${range.end_date}.csv`
}
