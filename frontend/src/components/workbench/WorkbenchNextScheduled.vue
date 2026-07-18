<template>
  <section v-if="item" class="rounded-2xl border border-blue-200 bg-white p-4 shadow-sm dark:border-blue-900/40 dark:bg-gray-900">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p class="text-xs font-semibold uppercase tracking-wider text-blue-600">下次定时任务</p>
        <h2 class="mt-1 text-base font-semibold text-gray-900 dark:text-white">{{ item.title }}</h2>
        <p class="mt-1 text-xs text-gray-500">{{ item.subtitle || "定时任务" }}</p>
        <WorkbenchItemMeta
          :next-run-at="item.next_run_at"
          :occurred-at="item.occurred_at"
          :status="item.status"
        />
      </div>
      <button
        type="button"
        class="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700"
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
