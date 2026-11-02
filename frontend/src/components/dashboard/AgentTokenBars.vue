<template>
  <div class="flex flex-col gap-2.5">
    <div
      v-for="row in rows"
      :key="row.name"
      class="grid grid-cols-[92px_1fr_52px] items-center gap-2.5"
    >
      <span class="truncate text-[11.5px] text-gray-700" :title="row.name">{{ row.name }}</span>
      <div class="h-[7px] overflow-hidden rounded-full bg-slate-100">
        <div class="h-full rounded-full bg-gradient-to-r from-blue-300 to-blue-500" :style="{ width: `${row.percent}%` }"></div>
      </div>
      <span class="text-right text-[11.5px] text-gray-400 tabular-nums">{{ row.label }}</span>
    </div>

    <div v-if="!rows.length" class="py-6 text-center text-[11.5px] text-gray-400">
      暂无 Token 数据
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * 智能体 Token 消耗分布。
 *
 * 与工具排行同一套条形语言；条形长度按占最大值归一化（这里的语义是「谁最多」，
 * 不是「占总量百分之多少」），右侧直接给原始数值，两者含义不同但都成立。
 */
const props = defineProps<{
  items: Array<{ name: string; value: number }>
}>()

const compactNumber = (value: number) => {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `${Math.round(value / 1_000)}K`
  return String(value)
}

const rows = computed(() => {
  const list = (props.items || [])
    .filter((item) => (Number(item.value) || 0) > 0)
    .sort((a, b) => Number(b.value) - Number(a.value))

  if (!list.length) return []

  const max = Math.max(...list.map((item) => Number(item.value)))
  return list.slice(0, 8).map((item) => ({
    name: item.name,
    label: compactNumber(Number(item.value)),
    percent: max > 0 ? (Number(item.value) / max) * 100 : 0,
  }))
})
</script>
