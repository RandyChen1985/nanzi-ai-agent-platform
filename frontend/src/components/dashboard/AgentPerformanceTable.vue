<template>
  <div class="overflow-x-auto">
    <table class="w-full border-collapse">
      <thead>
        <tr>
          <th class="pb-2 pr-3.5 text-left text-[10.5px] font-semibold uppercase tracking-wide text-gray-400">{{ nameHeader }}</th>
          <th class="pb-2 pr-3.5 text-right text-[10.5px] font-semibold uppercase tracking-wide text-gray-400">调用</th>
          <th class="pb-2 pr-3.5 text-right text-[10.5px] font-semibold uppercase tracking-wide text-gray-400">平均耗时</th>
          <th class="pb-2 text-right text-[10.5px] font-semibold uppercase tracking-wide text-gray-400">成功率</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.name" class="border-t border-[#f1f5f9]">
          <td class="py-2.5 pr-3.5 text-[12px] text-gray-700">{{ row.name }}</td>
          <td class="py-2.5 pr-3.5 text-right text-[12px] font-semibold tabular-nums text-gray-700">{{ row.calls.toLocaleString() }}</td>
          <td class="py-2.5 pr-3.5 text-right text-[12px] tabular-nums text-gray-500">{{ formatMs(row.avgLatency) }}</td>
          <td class="py-2.5 text-right">
            <span
              class="inline-flex rounded-full px-1.5 py-0.5 text-[10.5px] font-semibold"
              :class="rateClass(row.successRate)"
            >{{ row.successRate.toFixed(1) }}%</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="4" class="py-8 text-center text-[11.5px] text-gray-400">暂无智能体调用数据</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
/**
 * 智能体性能表。
 *
 * 成功率用徽章上色而不是给整行染色：一行里只有成功率是「可判读好坏」的字段，
 * 整行着色反而掩盖了调用量、耗时这些中性信息。
 */
withDefaults(
  defineProps<{
    rows: Array<{
      name: string
      calls: number
      avgLatency: number
      successRate: number
    }>
    nameHeader?: string
  }>(),
  { rows: () => [], nameHeader: '智能体' },
)

const formatMs = (ms: number) => `${Math.round(ms || 0).toLocaleString()}ms`

// 低于 95% 直接标红：这是「该智能体基本不可用」的信号
const rateClass = (rate: number) => {
  if (rate < 95) return 'bg-red-50 text-red-700'
  if (rate < 99) return 'bg-amber-50 text-amber-700'
  return 'bg-emerald-50 text-emerald-700'
}
</script>
