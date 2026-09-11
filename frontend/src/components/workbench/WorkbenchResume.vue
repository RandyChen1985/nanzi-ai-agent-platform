<template>
  <section class="flex h-full flex-col rounded-2xl border border-gray-100 bg-white p-4 shadow-sm sm:p-5 dark:border-gray-800 dark:bg-gray-800/80">
    <WorkbenchSectionHeader
      eyebrow="继续工作"
      title="最近会话"
      tone="blue"
      view-all-label="打开智能助手"
      @view-all="$emit('view-all')"
    />
    <div v-if="items.length" class="flex min-h-0 flex-1 flex-col gap-2.5">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="rounded-xl border border-gray-100 bg-gray-50/40 p-3.5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-blue-200 hover:bg-blue-50/30 hover:shadow-xs dark:border-gray-700/60 dark:bg-gray-800/50 dark:hover:border-blue-500/40 dark:hover:bg-blue-950/20"
        @click="$emit('open-item', item)"
      >
        <div class="flex items-start justify-between gap-3">
          <span class="min-w-0">
            <span class="block text-sm font-semibold text-gray-900 dark:text-gray-100">{{ item.title }}</span>
            <span class="mt-1 block text-xs text-gray-500 dark:text-gray-400 line-clamp-2">{{ item.subtitle }}</span>
            <WorkbenchItemMeta
              :occurred-at="item.occurred_at"
              :severity="item.severity"
              :status="item.status"
              :type="item.type"
              :action="item.action"
            />
          </span>
          <span class="shrink-0 text-xs font-medium text-blue-600 dark:text-blue-400">{{ actionLabel(item) }}</span>
        </div>
      </button>
      <div
        v-if="items.length < slotCount"
        class="flex flex-1 items-center justify-center rounded-xl border border-dashed border-gray-200 bg-gray-50/40 px-4 py-4 text-center dark:border-gray-700/70 dark:bg-gray-800/30"
      >
        <p class="text-xs text-gray-400 dark:text-gray-500">更多会话会出现在这里</p>
      </div>
    </div>
    <div
      v-else
      class="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-4 py-6 text-center dark:border-gray-700 dark:bg-gray-800/40"
    >
      <p class="text-sm text-gray-500 dark:text-gray-400">暂无最近会话</p>
      <button
        type="button"
        class="mt-3 text-xs font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
        @click="$emit('view-all')"
      >
        去找个助手聊聊
      </button>
    </div>
    <WorkbenchMobileViewAll
      v-if="items.length"
      label="打开智能助手"
      @view-all="$emit('view-all')"
    />
  </section>
</template>

<script setup lang="ts">
import type { WorkbenchItem } from "@/types/workbench"
import { workbenchActionLabel } from "@/utils/workbenchDisplay"
import WorkbenchItemMeta from "./WorkbenchItemMeta.vue"
import WorkbenchMobileViewAll from "./WorkbenchMobileViewAll.vue"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

/** 与最近产出并排时对齐的展示槽位数 */
const slotCount = 4

defineProps<{ items: WorkbenchItem[] }>()
defineEmits<{
  (event: "open-item", item: WorkbenchItem): void
  (event: "view-all"): void
}>()

const actionLabel = (item: WorkbenchItem) => workbenchActionLabel(item)
</script>
