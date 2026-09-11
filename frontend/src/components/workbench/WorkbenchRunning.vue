<template>
  <section
    v-if="items.length"
    class="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm sm:p-5 dark:border-gray-800 dark:bg-gray-800/80"
  >
    <WorkbenchSectionHeader
      eyebrow="进行中"
      title="正在处理的工作"
      description="可继续查看或接管当前运行"
      tone="emerald"
    />
    <p class="-mt-1 mb-3 text-xs text-gray-400 dark:text-gray-500">
      来源：正在生成的报表等可跨页面存在的后台运行任务
    </p>
    <div class="space-y-2.5">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="flex w-full items-start gap-3 rounded-xl border border-gray-100 bg-gray-50/50 p-3 text-left transition hover:-translate-y-0.5 hover:border-emerald-200 hover:bg-emerald-50/30 hover:shadow-xs dark:border-gray-700/60 dark:bg-gray-800/50 dark:hover:border-emerald-500/40 dark:hover:bg-emerald-950/20"
        @click="$emit('open-item', item)"
      >
        <span
          class="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
          :class="item.status === 'pending' ? 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-400' : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400'"
          aria-hidden="true"
        >
          ↻
        </span>
        <span class="min-w-0 flex-1">
          <span class="flex items-start justify-between gap-3">
            <span class="block min-w-0 truncate text-sm font-semibold text-gray-900 dark:text-gray-100">{{ item.title }}</span>
            <span class="shrink-0 text-xs font-medium text-emerald-700 dark:text-emerald-400">{{ actionLabel(item) }}</span>
          </span>
          <span class="mt-1 block text-xs text-gray-500 dark:text-gray-400">{{ item.subtitle }}</span>
          <span
            v-if="item.status === 'running'"
            class="mt-2 block h-1 overflow-hidden rounded-full bg-emerald-100 dark:bg-emerald-950/60"
            aria-label="运行中"
          >
            <span class="block h-full w-1/3 animate-pulse rounded-full bg-emerald-500" />
          </span>
          <span class="mt-2 flex flex-wrap items-center gap-1.5">
            <span class="rounded-md border border-gray-200 bg-white px-1.5 py-0.5 text-[10px] text-gray-500 dark:border-gray-700 dark:bg-gray-900/60 dark:text-gray-400">
              {{ sourceLabel(item.source) }}
            </span>
            <WorkbenchItemMeta
              :occurred-at="item.occurred_at"
              :severity="item.severity"
              :status="item.status"
              :type="item.type"
              :action="item.action"
              :show-kind="false"
            />
          </span>
        </span>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { WorkbenchItem } from "@/types/workbench"
import { workbenchActionLabel } from "@/utils/workbenchDisplay"
import WorkbenchItemMeta from "./WorkbenchItemMeta.vue"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

defineProps<{ items: WorkbenchItem[] }>()
defineEmits<{
  (event: "open-item", item: WorkbenchItem): void
}>()

const actionLabel = (item: WorkbenchItem) => workbenchActionLabel(item)

const sourceLabel = (source?: string) => {
  const labels: Record<string, string> = {
    saved_report_run: "报表运行",
  }
  return labels[source || ""] || "运行状态"
}
</script>
