<template>
  <section class="rounded-2xl border border-blue-100 bg-white p-4 shadow-sm dark:border-blue-900/40 dark:bg-gray-900/40">
    <WorkbenchSectionHeader
      eyebrow="继续工作"
      title="从上次停下的地方继续"
      tone="blue"
      view-all-label="打开智能助手"
      @view-all="$emit('view-all')"
    />
    <div v-if="items.length" class="grid gap-3">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="rounded-2xl border border-blue-200 bg-gradient-to-br from-blue-50 to-violet-50 p-4 text-left shadow-sm transition hover:border-blue-300 hover:shadow-md dark:border-blue-900/50 dark:from-blue-950/30 dark:to-violet-950/30"
        @click="$emit('open-item', item)"
      >
        <div class="flex items-start justify-between gap-3">
          <span class="min-w-0">
            <span class="block text-sm font-semibold text-gray-900 dark:text-white">{{ item.title }}</span>
            <span class="mt-1 block text-xs text-gray-500 line-clamp-2">{{ item.subtitle }}</span>
            <WorkbenchItemMeta
              :occurred-at="item.occurred_at"
              :severity="item.severity"
              :status="item.status"
              :type="item.type"
              :action="item.action"
            />
          </span>
          <span class="shrink-0 text-xs font-medium text-blue-700">{{ actionLabel(item) }}</span>
        </div>
      </button>
    </div>
    <div
      v-else
      class="rounded-xl border border-dashed border-blue-200 bg-blue-50/60 px-4 py-6 text-center dark:border-blue-900/50 dark:bg-blue-950/20"
    >
      <p class="text-sm text-gray-600 dark:text-gray-300">暂无最近会话</p>
      <button
        type="button"
        class="mt-3 text-xs font-medium text-blue-600 hover:text-blue-700"
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

defineProps<{ items: WorkbenchItem[] }>()
defineEmits<{
  (event: "open-item", item: WorkbenchItem): void
  (event: "view-all"): void
}>()

const actionLabel = (item: WorkbenchItem) => workbenchActionLabel(item)
</script>
