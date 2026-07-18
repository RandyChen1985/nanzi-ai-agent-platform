<template>
  <section
    v-if="item"
    class="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm sm:p-5"
  >
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <span class="h-1.5 w-1.5 rounded-full bg-blue-500" />
          <p class="text-xs font-medium text-blue-600">下次定时任务</p>
        </div>
        <h2 class="mt-1 text-sm font-bold text-gray-900">{{ item.title }}</h2>
        <p class="mt-0.5 text-xs text-gray-500">{{ item.subtitle || "定时任务" }}</p>
        <WorkbenchItemMeta
          :next-run-at="item.next_run_at"
          :occurred-at="item.occurred_at"
          :status="item.status"
        />
      </div>
      <button
        type="button"
        class="shrink-0 rounded-lg bg-blue-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-blue-700"
        @click="$emit('open-item', item)"
      >
        查看任务
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { WorkbenchItem } from "@/types/workbench"
import WorkbenchItemMeta from "./WorkbenchItemMeta.vue"

export type WorkbenchScheduledItem = WorkbenchItem & { next_run_at?: string }

defineProps<{ item: WorkbenchScheduledItem }>()
defineEmits<{ (event: "open-item", item: WorkbenchItem): void }>()
</script>
