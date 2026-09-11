<template>
  <section class="flex h-full flex-col rounded-2xl border border-gray-100 bg-white p-4 shadow-sm sm:p-5 dark:border-gray-800 dark:bg-gray-800/80">
    <WorkbenchSectionHeader
      eyebrow="执行记录"
      title="最近任务"
      tone="amber"
      view-all-label="查看执行记录"
      @view-all="$emit('view-all')"
    />
    <div v-if="items.length" class="flex min-h-0 flex-1 flex-col gap-2.5">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="rounded-xl border border-gray-100 bg-gray-50/40 p-3.5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-amber-200 hover:bg-amber-50/30 hover:shadow-xs dark:border-gray-700/60 dark:bg-gray-800/50 dark:hover:border-amber-500/40 dark:hover:bg-amber-950/20"
        @click="$emit('open-item', item)"
      >
        <div class="flex items-start justify-between gap-3">
          <span class="min-w-0 flex-1">
            <span class="block truncate text-sm font-semibold text-gray-900 dark:text-gray-100">{{ item.title }}</span>
            <!-- 助手 · 状态 · 时间 · 耗时；不展示截断摘要 -->
            <div class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-500 dark:text-gray-400">
              <span v-if="item.subtitle" class="max-w-[10rem] truncate font-medium text-gray-600 dark:text-gray-300">{{ item.subtitle }}</span>
              <span
                v-if="statusLabel(item)"
                class="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
              >{{ statusLabel(item) }}</span>
              <span v-if="relativeTime(item)" class="text-gray-400 dark:text-gray-500">{{ relativeTime(item) }}</span>
              <span v-if="durationLabel(item)" class="tabular-nums text-gray-400 dark:text-gray-500">耗时 {{ durationLabel(item) }}</span>
            </div>
          </span>
          <span class="shrink-0 text-xs font-medium text-amber-700 dark:text-amber-400">{{ actionLabel(item) }}</span>
        </div>
      </button>
      <div
        v-if="items.length < slotCount"
        class="flex flex-1 items-center justify-center rounded-xl border border-dashed border-gray-200 bg-gray-50/40 px-4 py-4 text-center dark:border-gray-700/70 dark:bg-gray-800/30"
      >
        <p class="text-xs text-gray-400 dark:text-gray-500">更多执行记录会出现在这里</p>
      </div>
    </div>
    <div
      v-else
      class="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-4 py-6 text-center dark:border-gray-700 dark:bg-gray-800/40"
    >
      <p class="text-sm text-gray-500 dark:text-gray-400">暂无任务执行记录</p>
      <button
        type="button"
        class="mt-3 text-xs font-medium text-amber-700 hover:text-amber-800 dark:text-amber-400 dark:hover:text-amber-300"
        @click="$emit('view-all')"
      >
        去创建定时任务
      </button>
    </div>
    <WorkbenchMobileViewAll
      v-if="items.length"
      label="查看执行记录"
      @view-all="$emit('view-all')"
    />
  </section>
</template>

<script setup lang="ts">
import type { WorkbenchItem } from "@/types/workbench"
import {
  formatWorkbenchDurationMs,
  formatWorkbenchRelativeTime,
  workbenchActionLabel,
  workbenchStatusLabel,
} from "@/utils/workbenchDisplay"
import WorkbenchMobileViewAll from "./WorkbenchMobileViewAll.vue"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

/** 与会话/产出并排时对齐的展示槽位数 */
const slotCount = 4

defineProps<{ items: WorkbenchItem[] }>()
defineEmits<{
  (event: "open-item", item: WorkbenchItem): void
  (event: "view-all"): void
}>()

const actionLabel = (item: WorkbenchItem) => workbenchActionLabel(item)
const statusLabel = (item: WorkbenchItem) => workbenchStatusLabel(item.status)
const relativeTime = (item: WorkbenchItem) => formatWorkbenchRelativeTime(item.occurred_at)
const durationLabel = (item: WorkbenchItem) => formatWorkbenchDurationMs(item.execution_time_ms)
</script>
