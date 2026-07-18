<template>
  <section class="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm sm:p-5">
    <WorkbenchSectionHeader
      eyebrow="最新结果"
      title="最近产出"
      tone="violet"
      view-all-label="查看全部结果"
      @view-all="$emit('view-all')"
    />
    <div v-if="items.length" class="grid gap-2.5">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="rounded-xl border border-gray-100 bg-gray-50/40 p-3.5 text-left transition hover:border-violet-200 hover:bg-violet-50/30"
        @click="$emit('open-item', item)"
      >
        <div class="flex items-start justify-between gap-3">
          <span class="min-w-0">
            <span class="block text-sm font-semibold text-gray-900">{{ item.title }}</span>
            <span class="mt-1 block text-xs text-gray-500 line-clamp-2">{{ item.subtitle }}</span>
            <WorkbenchItemMeta
              :occurred-at="item.occurred_at"
              :severity="item.severity"
              :status="item.status"
              :type="item.type"
              :action="item.action"
            />
          </span>
          <span class="shrink-0 text-xs font-medium text-violet-600">{{ actionLabel(item) }}</span>
        </div>
      </button>
    </div>
    <div
      v-else
      class="rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-4 py-6 text-center"
    >
      <p class="text-sm text-gray-500">暂无最新报表或简报</p>
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
      label="查看全部结果"
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
