<template>
  <section>
    <WorkbenchSectionHeader title="最近活动">
      <template #action>
        <span class="text-[11.5px] text-gray-400 dark:text-gray-500">{{ totalLabel }}</span>
      </template>
    </WorkbenchSectionHeader>

    <div class="overflow-hidden rounded-xl border border-gray-200/80 bg-white shadow-xs dark:border-gray-800 dark:bg-gray-900/40">
      <!-- 筛选：把「会话 / 产出 / 任务」三类混排在一条时间线上，靠 tab 回到单一类型 -->
      <div class="flex items-center gap-2 border-b border-gray-100 px-3.5 py-2.5 dark:border-gray-800">
        <div class="flex gap-0.5 rounded-md bg-gray-100 p-0.5 dark:bg-gray-800">
          <button
            v-for="option in filterOptions"
            :key="option.value"
            type="button"
            class="rounded px-2.5 py-1 text-[11.5px] font-medium transition-colors"
            :class="
              activeFilter === option.value
                ? 'bg-white text-gray-900 shadow-xs dark:bg-gray-700 dark:text-gray-100'
                : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
            "
            :aria-pressed="activeFilter === option.value"
            @click="activeFilter = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </div>

      <button
        v-for="item in visibleItems"
        :key="item.id"
        type="button"
        class="group flex w-full items-center gap-2.5 border-b border-gray-50 px-3.5 py-2.5 text-left transition-colors last:border-b-0 hover:bg-blue-50/40 dark:border-gray-800/60 dark:hover:bg-blue-950/20"
        @click="$emit('open-item', item)"
      >
        <span
          class="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-[7px]"
          :class="kindBg(item)"
          aria-hidden="true"
        >
          <svg
            class="h-[13px] w-[13px]"
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

        <span class="flex min-w-0 flex-1 items-baseline gap-2">
          <span class="truncate text-[13px] font-medium text-gray-900 dark:text-gray-100">{{ item.title }}</span>
          <span
            v-if="secondaryText(item)"
            class="min-w-0 flex-1 truncate text-[11.5px] text-gray-400 dark:text-gray-500"
          >{{ secondaryText(item) }}</span>
        </span>

        <span
          v-if="statusLabel(item)"
          class="shrink-0 rounded px-1.5 py-px text-[10.5px] font-semibold tabular-nums"
          :class="statusClass(item)"
        >{{ statusLabel(item) }}</span>

        <span class="w-[62px] shrink-0 text-right text-[11.5px] tabular-nums text-gray-400 dark:text-gray-500">
          {{ relativeTime(item) }}
        </span>

        <span class="w-4 shrink-0 text-gray-300 opacity-0 transition-opacity group-hover:opacity-100 dark:text-gray-600">
          <svg class="h-[13px] w-[13px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="m9 6 6 6-6 6" />
          </svg>
        </span>
      </button>

      <div v-if="!visibleItems.length" class="px-4 py-7 text-center text-xs text-gray-400 dark:text-gray-500">
        {{ emptyLabel }}
      </div>

      <div
        v-else
        class="flex items-center justify-between bg-gray-50/60 px-3.5 py-2 dark:bg-gray-900/60"
      >
        <span class="text-[11.5px] text-gray-400 dark:text-gray-500">{{ totalLabel }}</span>
        <button
          v-if="viewAllLabel"
          type="button"
          class="text-[11.5px] font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
          @click="$emit('view-all', activeFilter)"
        >
          {{ viewAllLabel }} →
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"
import type { WorkbenchItem } from "@/types/workbench"
import {
  formatWorkbenchDurationMs,
  formatWorkbenchRelativeTime,
  resolveWorkbenchKind,
  workbenchStatusLabel,
  type WorkbenchKind,
} from "@/utils/workbenchDisplay"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

type FeedFilter = "all" | "conversation" | "report" | "task"

const props = defineProps<{ items: WorkbenchItem[] }>()
defineEmits<{
  (event: "open-item", item: WorkbenchItem): void
  (event: "view-all", filter: FeedFilter): void
}>()

const filterOptions: { value: FeedFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "conversation", label: "会话" },
  { value: "report", label: "产出" },
  { value: "task", label: "任务" },
]

/** 底部跳转随当前 tab 变：三类活动本来就落在不同页面 */
const viewAllTargets: Record<FeedFilter, string> = {
  all: "",
  conversation: "打开智能助手",
  report: "查看全部结果",
  task: "查看执行记录",
}

const emptyLabels: Record<FeedFilter, string> = {
  all: "还没有任何活动记录",
  conversation: "还没有会话，去找个助手聊聊",
  report: "还没有生成过产出",
  task: "还没有任务执行记录",
}

const activeFilter = ref<FeedFilter>("all")

const filteredItems = computed(() => {
  if (activeFilter.value === "all") return props.items
  return props.items.filter((item) => resolveWorkbenchKind(item) === activeFilter.value)
})

const visibleItems = computed(() => filteredItems.value)
const totalLabel = computed(() => `共 ${filteredItems.value.length} 条`)
const emptyLabel = computed(() => emptyLabels[activeFilter.value])
const viewAllLabel = computed(() => viewAllTargets[activeFilter.value])

const relativeTime = (item: WorkbenchItem) => formatWorkbenchRelativeTime(item.occurred_at)

/** 任务行优先展示耗时；其余类型展示 subtitle */
const secondaryText = (item: WorkbenchItem) => {
  const kind = resolveWorkbenchKind(item)
  if (kind === "task") {
    const duration = formatWorkbenchDurationMs(item.execution_time_ms)
    if (duration) return `耗时 ${duration}`
  }
  return item.subtitle || ""
}

const statusLabel = (item: WorkbenchItem) => {
  const kind = resolveWorkbenchKind(item)
  if (kind !== "task" && kind !== "notification") return ""
  return workbenchStatusLabel(item.status)
}

const statusClass = (item: WorkbenchItem) => {
  if (item.status === "failed") return "bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-400"
  if (item.status === "success" || item.status === "completed") {
    return "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400"
  }
  return "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
}

/**
 * 类型图标用内联 SVG。
 *
 * 旧实现在 workbenchDisplay 里给每类配了 emoji（⏱📰📊💬🔔），跟平台其他位置
 * 统一使用线性图标的约定不一致，这里一并换掉。
 */
const kindOf = (item: WorkbenchItem): WorkbenchKind => resolveWorkbenchKind(item)

/**
 * 图标用 path 数组表达。
 *
 * 图标用 path 数组表达：在 svg 里放条件标签会让 SFC 解析器误判模板块边界
 * （报 missing end tag），所以每个图标拆成纯 path 数据。
 */
const kindPaths: Record<WorkbenchKind, string[]> = {
  conversation: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"],
  report: [
    "M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z",
    "M8 16v-3",
    "M12 16V9",
    "M16 16v-5",
  ],
  task: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 7v5l3 2"],
  digest: [
    "M4 4h13a2 2 0 0 1 2 2v13H6a2 2 0 0 1-2-2z",
    "M8 8h6",
    "M8 12h6",
  ],
  notification: [
    "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9",
    "M13.7 21a2 2 0 0 1-3.4 0",
  ],
  other: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 8v8"],
}

const iconPaths = (item: WorkbenchItem) => kindPaths[kindOf(item)]

/** 任务按成功/失败分色，其余类型按语义类分色 */
const kindBg = (item: WorkbenchItem) => {
  const kind = kindOf(item)
  if (kind === "task" && item.status === "failed") {
    return "bg-red-50 text-red-600 dark:bg-red-950/50 dark:text-red-400"
  }
  if (kind === "task") {
    return "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/50 dark:text-emerald-400"
  }
  if (kind === "conversation") {
    return "bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400"
  }
  if (kind === "report") {
    return "bg-violet-50 text-violet-600 dark:bg-violet-950/50 dark:text-violet-400"
  }
  if (kind === "notification") {
    return "bg-amber-50 text-amber-600 dark:bg-amber-950/50 dark:text-amber-400"
  }
  if (kind === "digest") {
    return "bg-indigo-50 text-indigo-600 dark:bg-indigo-950/50 dark:text-indigo-400"
  }
  return "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
}
</script>
