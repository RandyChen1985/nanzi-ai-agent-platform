<template>
  <section>
    <WorkbenchSectionHeader
      title="需要你处理"
      :count="items.length"
      count-tone="warn"
      tone="amber"
    >
      <template #action>
        <button
          type="button"
          class="text-[11.5px] font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
          @click="$emit('view-all')"
        >
          全部任务 →
        </button>
      </template>
    </WorkbenchSectionHeader>

    <div class="overflow-hidden rounded-xl border border-gray-200/80 bg-white shadow-xs outline outline-1 outline-offset-[-1px] outline-red-500/10 dark:border-gray-800 dark:bg-gray-900/40 dark:outline-red-500/15">
      <button
        v-for="item in sortedItems"
        :key="item.id"
        type="button"
        class="relative flex w-full items-center gap-3 border-b border-gray-50 py-3 pl-3.5 pr-4 text-left transition-colors last:border-b-0 hover:bg-blue-50/40 dark:border-gray-800/60 dark:hover:bg-blue-950/20"
        @click="$emit('open-item', item)"
      >
        <!-- 左侧竖条是紧急性唯一的常驻色彩，不靠底色铺满整行 -->
        <span
          class="absolute inset-y-0 left-0 w-[3px]"
          :class="item.severity === 'critical' ? 'bg-red-500' : 'bg-amber-400'"
          aria-hidden="true"
        />
        <span
          class="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px]"
          :class="
            item.severity === 'critical'
              ? 'bg-red-50 text-red-600 dark:bg-red-950/50 dark:text-red-400'
              : 'bg-amber-50 text-amber-600 dark:bg-amber-950/50 dark:text-amber-400'
          "
          aria-hidden="true"
        >
          <svg
            class="h-[15px] w-[15px]"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path v-for="(d, index) in iconPaths(item)" :key="index" :d="d" />
          </svg>
        </span>

        <span class="min-w-0 flex-1">
          <span class="flex items-center gap-2">
            <span class="truncate text-[13.5px] font-semibold text-gray-900 dark:text-gray-100">{{ item.title }}</span>
            <span
              v-if="badgeLabel(item)"
              class="shrink-0 rounded px-1.5 py-px text-[10px] font-semibold"
              :class="
                item.severity === 'critical'
                  ? 'bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-400'
                  : 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400'
              "
            >{{ badgeLabel(item) }}</span>
          </span>
          <span class="mt-0.5 block truncate text-xs text-gray-400 dark:text-gray-500">{{ metaLine(item) }}</span>
        </span>

        <span
          class="shrink-0 rounded-[7px] px-2.5 py-1 text-xs font-semibold"
          :class="
            item.severity === 'critical'
              ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
              : 'border border-gray-200 bg-white text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200'
          "
        >{{ actionLabel(item) }}</span>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue"
import type { WorkbenchItem } from "@/types/workbench"
import {
  formatWorkbenchDurationMs,
  formatWorkbenchRelativeTime,
  workbenchActionLabel,
  workbenchSeverityLabel,
  workbenchStatusLabel,
} from "@/utils/workbenchDisplay"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

const props = defineProps<{ items: WorkbenchItem[] }>()
defineEmits<{
  (event: "open-item", item: WorkbenchItem): void
  (event: "view-all"): void
}>()

const severityRank: Record<string, number> = { critical: 3, warning: 2, info: 1 }

const sortedItems = computed(() =>
  [...props.items].sort((a, b) => {
    const sa = severityRank[String(a.severity || "info")] || 0
    const sb = severityRank[String(b.severity || "info")] || 0
    if (sa !== sb) return sb - sa
    return String(b.occurred_at || "").localeCompare(String(a.occurred_at || ""))
  })
)

const actionLabel = (item: WorkbenchItem) => workbenchActionLabel(item)

/** 图标统一走 path 数组，避免在 svg 里放条件标签 */
const iconPaths = (item: WorkbenchItem) =>
  item.severity === "critical"
    ? [
        "M12 9v4",
        "M12 17h.01",
        "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
      ]
    : ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M13.7 21a2 2 0 0 1-3.4 0"]

/** 只有一个徽章位置，状态文案优先于严重度文案 */
const badgeLabel = (item: WorkbenchItem) =>
  workbenchStatusLabel(item.status) || workbenchSeverityLabel(item.severity)

/** 副行：说明 · 耗时 · 相对时间压成一行，省掉原来那一整块标签区 */
const metaLine = (item: WorkbenchItem) => {
  const parts: string[] = []
  if (item.subtitle) parts.push(item.subtitle)
  const duration = formatWorkbenchDurationMs(item.execution_time_ms)
  if (duration) parts.push(`耗时 ${duration}`)
  const time = formatWorkbenchRelativeTime(item.occurred_at)
  if (time) parts.push(time)
  return parts.join(" · ")
}
</script>
