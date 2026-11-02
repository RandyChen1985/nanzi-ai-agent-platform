<template>
  <div
    v-if="item"
    class="flex items-center gap-3 rounded-xl border border-gray-200/80 bg-white px-3.5 py-2.5 shadow-xs dark:border-gray-800 dark:bg-gray-900/40"
  >
    <span
      class="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-[8px] bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400"
      aria-hidden="true"
    >
      <svg class="h-[14px] w-[14px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4" /><path d="M8 2v4" /><path d="M3 10h18" />
      </svg>
    </span>

    <div class="min-w-0 flex-1">
      <p class="truncate text-[13px] font-semibold text-gray-900 dark:text-gray-100">{{ item.title }}</p>
      <p class="mt-0.5 truncate text-[11.5px] text-gray-400 dark:text-gray-500">
        下次执行 {{ nextRunText }}<span v-if="item.subtitle"> · {{ item.subtitle }}</span>
      </p>
    </div>

    <button
      type="button"
      class="shrink-0 rounded-[7px] border border-gray-200 bg-white px-2.5 py-1 text-xs font-semibold text-gray-700 hover:border-blue-200 hover:text-blue-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:text-blue-400"
      @click="$emit('open-item', item)"
    >
      查看任务
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"
import type { WorkbenchItem } from "@/types/workbench"
import { formatWorkbenchRelativeTime } from "@/utils/workbenchDisplay"

export type WorkbenchScheduledItem = WorkbenchItem & { next_run_at?: string }

const props = defineProps<{ item: WorkbenchScheduledItem }>()
defineEmits<{ (event: "open-item", item: WorkbenchItem): void }>()

const nextRunText = computed(() => {
  const relative = formatWorkbenchRelativeTime(props.item.next_run_at || props.item.occurred_at || "")
  return relative || "未排期"
})
</script>
