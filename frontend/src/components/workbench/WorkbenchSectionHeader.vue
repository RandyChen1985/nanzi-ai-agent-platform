<template>
  <div class="mb-2.5 flex items-center gap-2">
    <span class="h-3.5 w-[3px] shrink-0 rounded-full" :class="barClass" aria-hidden="true" />
    <h2 class="text-[13px] font-semibold tracking-tight text-gray-900 dark:text-gray-100">{{ title }}</h2>
    <span
      v-if="count !== undefined"
      class="inline-grid h-[17px] min-w-[18px] place-items-center rounded-[5px] px-1 text-[10.5px] font-bold tabular-nums"
      :class="
        countTone === 'warn'
          ? 'bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-400'
          : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
      "
    >{{ count }}</span>
    <span class="flex-1" />
    <slot name="action" />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"

const props = withDefaults(
  defineProps<{
    title: string
    count?: number
    countTone?: "default" | "warn"
    /** 左侧竖条颜色：只有「需要你处理」值得用琥珀，其余保持中性 */
    tone?: "amber" | "blue" | "slate" | "green"
  }>(),
  { countTone: "default", tone: "slate" }
)

const barClass = computed(() => {
  if (props.tone === "amber") return "bg-amber-500"
  if (props.tone === "blue") return "bg-blue-500"
  if (props.tone === "green") return "bg-emerald-500"
  return "bg-gray-300 dark:bg-gray-600"
})
</script>
