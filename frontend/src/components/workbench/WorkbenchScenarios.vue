<template>
  <section>
    <WorkbenchSectionHeader title="推荐场景">
      <template #action>
        <button
          type="button"
          class="text-[11.5px] font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
          @click="$emit('view-all')"
        >
          全部场景 →
        </button>
      </template>
    </WorkbenchSectionHeader>

    <div v-if="scenarios.length" class="grid gap-2.5 sm:grid-cols-2">
      <button
        v-for="scenario in scenarios"
        :key="scenario.id"
        type="button"
        class="flex gap-2.5 rounded-xl border border-gray-200/80 bg-white p-3.5 text-left shadow-xs transition-colors hover:border-blue-200 hover:bg-blue-50/30 dark:border-gray-800 dark:bg-gray-900/40 dark:hover:border-blue-500/40 dark:hover:bg-blue-950/20"
        @click="$emit('open-scenario', scenario)"
      >
        <span
          class="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px] bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-400"
          aria-hidden="true"
        >
          <svg class="h-[15px] w-[15px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" />
            <rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" />
          </svg>
        </span>
        <span class="min-w-0">
          <span class="flex items-center gap-1.5">
            <span class="truncate text-[13px] font-semibold text-gray-900 dark:text-gray-100">{{ scenario.name }}</span>
            <span
              v-if="scenario.recommended"
              class="shrink-0 rounded bg-blue-50 px-1.5 py-px text-[10px] font-semibold text-blue-700 dark:bg-blue-950/50 dark:text-blue-400"
            >推荐</span>
          </span>
          <span class="mt-0.5 line-clamp-2 block text-[11.5px] text-gray-400 dark:text-gray-500">{{ scenario.description }}</span>
        </span>
      </button>
    </div>

    <div
      v-else
      class="rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-4 py-7 text-center dark:border-gray-700 dark:bg-gray-800/40"
    >
      <p class="text-xs text-gray-500 dark:text-gray-400">
        {{ agentsAvailable ? "暂时没有推荐场景" : "当前还没有可用的业务场景" }}
      </p>
      <p class="mt-1 text-[11.5px] text-gray-400 dark:text-gray-500">
        {{ agentsAvailable ? "可以先浏览场景包市场，或直接打开智能助手。" : "请联系管理员开通智能体或相关资源。" }}
      </p>
      <button
        type="button"
        class="mt-3 text-[11.5px] font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
        @click="$emit('view-all')"
      >
        浏览场景包市场
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { WorkbenchScenario } from "@/types/workbench"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

defineProps<{ scenarios: WorkbenchScenario[]; agentsAvailable?: boolean }>()
defineEmits<{
  (event: "open-scenario", scenario: WorkbenchScenario): void
  (event: "view-all"): void
}>()
</script>
