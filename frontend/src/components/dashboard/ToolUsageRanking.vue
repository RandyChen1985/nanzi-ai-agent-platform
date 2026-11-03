<template>
  <div class="flex flex-col gap-2.5">
    <div
      v-for="row in rows"
      :key="row.name"
      class="grid grid-cols-[1fr_46px] items-center gap-2.5"
    >
      <div class="flex min-w-0 flex-col gap-1">
        <div class="flex items-baseline justify-between gap-2">
          <span
            class="truncate font-mono text-[12px] text-gray-700"
            :title="row.name"
          >{{ row.name }}</span>
          <span class="shrink-0 text-[12px] font-semibold text-gray-700 tabular-nums">{{ row.count }}</span>
        </div>
        <div class="h-1.5 overflow-hidden rounded-full bg-slate-100">
          <div class="h-full rounded-full" :style="{ width: `${row.percent}%`, background: row.color }"></div>
        </div>
      </div>
      <div class="text-right text-[11px] text-gray-400 tabular-nums">{{ row.percentText }}</div>
    </div>

    <div v-if="!rows.length" class="py-8 text-center text-[11.5px] text-gray-400">
      暂无调用数据
    </div>

    <slot name="footer"></slot>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * 工具/智能体调用排行。
 *
 * 替代原来的环形图：十几个扇区里最小的一项只占 3%，靠角度比较不出差异；
 * 而工具名（尤其 MCP 的 `mcp-public-admin-hcp_get-tickets_...`）在 10px 图例里
 * 必然被截断。横向条形同时解决了「可比」和「可读」两件事。
 *
 * 条形长度按**占总量比例**计算，与右侧百分比同源；不要改成按最大值归一化，
 * 那样条最长的一项与 100% 对不上，读起来像两套数字。
 */
const props = withDefaults(
  defineProps<{
    items: Array<{ name: string; value: number }>
    /** 最多展示多少项，其余聚合成一行 */
    limit?: number
    /** 总量；缺省时用 items 求和 */
    total?: number
  }>(),
  { limit: 7 },
)

const PALETTE = [
  'linear-gradient(90deg,#3b5bdb,#4c6ef5)',
  'linear-gradient(90deg,#52b788,#74c69d)',
  'linear-gradient(90deg,#f0a500,#f6bd60)',
  'linear-gradient(90deg,#e56b6f,#f28482)',
  'linear-gradient(90deg,#5aa9d6,#84c5e8)',
  'linear-gradient(90deg,#b088f9,#c8a8ff)',
]
const REST_COLOR = '#cbd5e1'

const totalCount = computed(
  () =>
    props.total ??
    props.items.reduce((sum, item) => sum + (Number(item.value) || 0), 0),
)

const rows = computed(() => {
  const out: Array<{
    name: string
    count: number
    percent: number
    percentText: string
    color: string
  }> = []

  if (!props.items?.length) return out

  const sorted = [...props.items].sort(
    (a, b) => (Number(b.value) || 0) - (Number(a.value) || 0),
  )

  const head = sorted.slice(0, props.limit)
  const rest = sorted.slice(props.limit)
  const restSum = rest.reduce((sum, item) => sum + (Number(item.value) || 0), 0)

  const push = (name: string, count: number, color: string) => {
    const percent = totalCount.value > 0 ? (count / totalCount.value) * 100 : 0
    out.push({
      name,
      count,
      percent: Math.max(percent, 0),
      percentText: `${percent.toFixed(1)}%`,
      color,
    })
  }

  head.forEach((item, index) => {
    push(item.name, Number(item.value) || 0, PALETTE[index % PALETTE.length]!)
  })

  if (rest.length) {
    // 聚合行的名字取前两个代表项，让用户知道「其余」里有什么
    const preview = rest.slice(0, 3).map((item) => item.name).join('、')
    push(`其余 ${rest.length} 项：${preview}`, restSum, REST_COLOR)
  }

  return out
})
</script>
