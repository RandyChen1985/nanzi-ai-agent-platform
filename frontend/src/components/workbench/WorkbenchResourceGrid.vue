<template>
  <section class="overflow-hidden rounded-xl border border-gray-200/80 bg-white shadow-xs dark:border-gray-800 dark:bg-gray-900/40">
    <div class="flex items-center gap-2 px-3.5 pb-2 pt-3">
      <h2 class="text-[12.5px] font-semibold tracking-tight text-gray-900 dark:text-gray-100">{{ title }}</h2>
      <span class="flex-1" />
      <slot name="action" />
    </div>

    <div class="flex flex-wrap gap-px bg-gray-100 dark:bg-gray-800">
      <button
        v-for="item in items"
        :key="item.key"
        type="button"
        class="group flex basis-[calc(50%-0.5px)] items-center gap-2 bg-white px-3.5 py-2.5 text-left transition-colors hover:bg-blue-50/40 dark:bg-gray-900/40 dark:hover:bg-blue-950/20"
        @click="$emit('select', item)"
      >
        <span
          class="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px] bg-gray-50 text-gray-400 transition-transform group-hover:scale-105 dark:bg-gray-800 dark:text-gray-500"
          :class="item.status === 'error' ? '!bg-amber-50 !text-amber-600 dark:!bg-amber-950/50 dark:!text-amber-400' : ''"
          aria-hidden="true"
        >
          <svg
            class="h-[15px] w-[15px]"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.9"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path v-for="(d, index) in iconPaths(item.key)" :key="index" :d="d" />
          </svg>
        </span>

        <span class="min-w-0">
          <span class="block truncate text-[11px] leading-tight text-gray-400 dark:text-gray-500">{{ item.label }}</span>
          <span class="mt-px flex items-baseline gap-0.5">
            <span
              class="truncate text-[15px] font-bold tabular-nums tracking-tight"
              :class="item.status === 'error' ? 'text-amber-600 dark:text-amber-400' : 'text-gray-900 dark:text-gray-100'"
            >{{ displayValue(item) }}</span>
            <span v-if="item.status !== 'error'" class="shrink-0 text-[10px] font-semibold text-gray-400 dark:text-gray-500">
              {{ item.unit }}
            </span>
          </span>
        </span>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { formatTokenCompact } from "@/utils/tokenFormat"
import type { WorkbenchPersonalResource } from "@/types/workbench"

withDefaults(defineProps<{ items: WorkbenchPersonalResource[]; title?: string }>(), {
  title: "我的资源",
})

defineEmits<{ (e: "select", item: WorkbenchPersonalResource): void }>()

/** 图标统一走 path 数组，避免在 svg 里放条件标签 */
const resourcePaths: Record<string, string[]> = {
  knowledge: [
    "M12 7v14",
    "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z",
  ],
  datasets: [
    "M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z",
    "M3 9h18",
    "M3 15h18",
    "M9 3v18",
  ],
  memory: [
    "M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z",
    "M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z",
    "M12 5v13",
  ],
  tokens: ["M13 2 3 14h9l-1 8 10-12h-9l1-8z"],
  // data：数据门户的「圆柱」图标，与 datasets 的表格刻意区分开
  data: [
    "M12 2c4.97 0 9 1.34 9 3s-4.03 3-9 3-9-1.34-9-3 4.03-3 9-3z",
    "M21 12c0 1.66-4 3-9 3s-9-1.34-9-3",
    "M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5",
  ],
  skills: [
    "m12 3 1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3Z",
    "M18 15.5 18.9 18l2.5.9-2.5.9-.9 2.5-.9-2.5-2.5-.9 2.5-.9.9-2.5Z",
  ],
  mcp: [
    "M12 22v-5",
    "M9 8V2",
    "M15 8V2",
    "M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z",
  ],
  tasks: [
    "M9 2h6a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z",
    "M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2",
    "m9 14 2 2 4-4",
  ],
  inbox: [
    "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9",
    "M10.3 21a1.94 1.94 0 0 0 3.4 0",
  ],
}

const fallbackPaths = ["M12 3a10 10 0 1 0 0 20 10 10 0 0 0 0-20z", "M12 8v4", "M12 16h.01"]

/** 兜底只用于后端将来新增、前端还没配图标的 key；漏配由契约测试拦下 */
const iconPaths = (key: string) => resourcePaths[key] || fallbackPaths

const displayValue = (item: WorkbenchPersonalResource) => {
  if (item.status === "error") return "--"
  if (item.key === "tokens") return formatTokenCompact(item.value)
  return String(item.value ?? 0)
}
</script>
