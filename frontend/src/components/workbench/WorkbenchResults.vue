<template>
  <section class="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-900/40">
    <WorkbenchSectionHeader
      eyebrow="最新结果"
      title="最近为你完成的工作"
      tone="violet"
      view-all-label="打开数据门户"
      @view-all="$emit('view-all')"
    />
    <div v-if="items.length" class="grid gap-3">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="rounded-2xl border border-gray-300 bg-white p-4 text-left shadow-sm transition hover:border-violet-300 hover:shadow-md dark:border-gray-700 dark:bg-gray-900"
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
          <span class="shrink-0 text-xs font-medium text-violet-700">{{ actionLabel(item) }}</span>
        </div>
      </button>
    </div>
    <div
      v-else
      class="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center dark:border-gray-700 dark:bg-gray-900"
    >
      <p class="text-sm text-gray-600 dark:text-gray-300">暂无最新报表或简报</p>
      <button
        type="button"
        class="mt-3 text-xs font-medium text-violet-600 hover:text-violet-700"
        @click="$emit('view-all')"
      >
        去数据门户看看
      </button>
    </div>
    <WorkbenchMobileViewAll
      v-if="items.length"
      label="打开数据门户"
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
