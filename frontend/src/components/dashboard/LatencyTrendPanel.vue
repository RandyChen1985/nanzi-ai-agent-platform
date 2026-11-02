<template>
  <div class="relative h-[168px] w-full">
    <v-chart v-if="hasData" :option="option" autoresize />
    <div v-else class="absolute inset-0 flex items-center justify-center text-[11.5px] text-gray-300">
      暂无趋势数据
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
  MarkPointComponent,
  MarkAreaComponent,
} from 'echarts/components'

use([
  CanvasRenderer,
  LineChart,
  GridComponent,
  TooltipComponent,
  MarkLineComponent,
  MarkPointComponent,
  MarkAreaComponent,
])

/**
 * 执行耗时趋势。
 *
 * 24 小时里有大段无调用的时段（值为 0），原来直接把 y 轴拉到峰值，曲线被压成
 * 两根竖刺。这里三处调整让它可读：
 * 1. 面积渐变给曲线一点体量，不至于细得看不见；
 * 2. 画一条阈值虚线，超过它的时段用底色标出来——「什么时候变慢」一眼可见；
 * 3. 峰值直接标注数值，不用回看 y 轴。
 */
const props = withDefaults(
  defineProps<{
    points: Array<{ hour: string; avg_ms: number }>
    /** 超过该耗时视为异常，默认 5000ms */
    threshold?: number
    lineColor?: string
  }>(),
  { threshold: 5000, lineColor: '#f59e0b' },
)

const hasData = computed(() => (props.points?.length ?? 0) > 0)

/** 连续超阈的时段，转成 markArea 的区间；单点也单独成段 */
const overRanges = computed(() => {
  const points = props.points || []
  const ranges: Array<Array<{ xAxis: string }>> = []
  let start = -1

  for (let i = 0; i < points.length; i += 1) {
    const over = Number(points[i]!.avg_ms) > props.threshold
    if (over && start < 0) start = i
    if (!over && start >= 0) {
      ranges.push([{ xAxis: points[start]!.hour }, { xAxis: points[i - 1]!.hour }])
      start = -1
    }
  }
  if (start >= 0) {
    ranges.push([
      { xAxis: points[start]!.hour },
      { xAxis: points[points.length - 1]!.hour },
    ])
  }
  return ranges
})

const option = computed(() => {
  const points = props.points || []
  const hours = points.map((item) => item.hour)
  const values = points.map((item) => Number(item.avg_ms) || 0)

  return {
    animationDuration: 400,
    grid: { left: 4, right: 8, top: 16, bottom: 20, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,.96)',
      borderColor: '#e8edf3',
      textStyle: { fontSize: 11, color: '#334155' },
      formatter: (params: Array<{ axisValue: string; data: number }>) => {
        const point = params?.[0]
        if (!point) return ''
        return `${point.axisValue}<br/>平均耗时 <b>${point.data.toLocaleString()}ms</b>`
      },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: hours,
      axisLine: { lineStyle: { color: '#e8edf3' } },
      axisTick: { show: false },
      axisLabel: {
        fontSize: 9.5,
        color: '#94a3b8',
        // 24 个刻度太密，抽样显示
        interval: Math.max(Math.floor(hours.length / 6) - 1, 0),
      },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { fontSize: 9.5, color: '#94a3b8', formatter: (v: number) => v.toLocaleString() },
    },
    series: [
      {
        name: '平均耗时',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: values,
        lineStyle: { width: 2, color: props.lineColor },
        itemStyle: { color: props.lineColor },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: `${props.lineColor}42` },
              { offset: 1, color: `${props.lineColor}00` },
            ],
          },
        },
        markLine: {
          silent: true,
          symbol: 'none',
          label: {
            formatter: `${props.threshold.toLocaleString()}ms 阈值`,
            fontSize: 9,
            color: '#f87171',
            position: 'insideStartTop',
          },
          lineStyle: { color: '#fca5a5', type: 'dashed', width: 1 },
          data: [{ yAxis: props.threshold }],
        },
        markArea: overRanges.value.length
          ? {
              silent: true,
              itemStyle: { color: 'rgba(254,243,199,.55)' },
              data: overRanges.value,
            }
          : undefined,
        markPoint: {
          symbol: 'circle',
          symbolSize: 7,
          label: {
            fontSize: 9.5,
            fontWeight: 'bold',
            color: props.lineColor,
            position: 'top',
            formatter: (p: { value: number }) => (p.value || 0).toLocaleString(),
          },
          data: [{ type: 'max', name: '峰值' }],
        },
      },
    ],
  }
})
</script>
