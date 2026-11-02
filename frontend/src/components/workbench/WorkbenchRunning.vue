<template>
  <section>
    <WorkbenchSectionHeader title="正在运行" :count="items.length" tone="blue" />

    <div class="overflow-hidden rounded-xl border border-gray-200/80 bg-white shadow-xs dark:border-gray-800 dark:bg-gray-900/40">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="flex w-full items-center gap-3 border-b border-gray-50 px-3.5 py-2.5 text-left transition-colors last:border-b-0 hover:bg-blue-50/40 dark:border-gray-800/60 dark:hover:bg-blue-950/20"
        @click="$emit('open-item', item)"
      >
        <span
          class="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-[8px] bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400"
          aria-hidden="true"
        >
          <svg class="h-[14px] w-[14px] animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
            <path d="M21 12a9 9 0 1 1-6.2-8.6" />
          </svg>
        </span>

        <span class="min-w-0 flex-1">
          <span class="block truncate text-[13px] font-semibold text-gray-900 dark:text-gray-100">{{ item.title }}</span>
          <span class="mt-0.5 block truncate text-[11.5px] text-gray-400 dark:text-gray-500">
            {{ item.status === 'pending' ? '排队中' : '正在生成' }}
            <span v-if="item.subtitle"> · {{ item.subtitle }}</span>
          </span>
        </span>

        <!-- 旧实现画了一条固定 33% 的脉冲进度条，后端没有进度字段，它永远不动 -->
        <span class="shrink-0 text-[11.5px] tabular-nums text-gray-400 dark:text-gray-500">
          {{ runningFor(item) }}
        </span>

        <span class="shrink-0 rounded-[7px] border border-gray-200 bg-white px-2.5 py-1 text-xs font-semibold text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200">
          查看
        </span>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { WorkbenchItem } from "@/types/workbench"
import { formatWorkbenchDurationMs, formatWorkbenchRelativeTime } from "@/utils/workbenchDisplay"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

defineProps<{ items: WorkbenchItem[] }>()
defineEmits<{ (event: "open-item", item: WorkbenchItem): void }>()

/** 真实可用的时间信息：有耗时用耗时，否则退回「已开始多久」 */
const runningFor = (item: WorkbenchItem) => {
  const duration = formatWorkbenchDurationMs(item.execution_time_ms)
  if (duration) return `已运行 ${duration}`
  const started = formatWorkbenchRelativeTime(item.occurred_at)
  return started ? `${started}开始` : ""
}
</script>
