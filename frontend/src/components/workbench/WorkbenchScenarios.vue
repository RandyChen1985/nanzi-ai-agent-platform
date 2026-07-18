<template>
  <section class="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-900/40">
    <WorkbenchSectionHeader
      eyebrow="从场景开始"
      title="选择你想完成的工作"
      description="先看方案，再进入交付或找对应助手。"
      tone="violet"
      view-all-label="浏览场景包"
      @view-all="$emit('view-all')"
    />
    <div v-if="scenarios.length" class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <button
        v-for="scenario in scenarios"
        :key="scenario.id"
        type="button"
        class="rounded-2xl border border-gray-300 bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-violet-300 hover:shadow-md dark:border-gray-700 dark:bg-gray-900"
        @click="$emit('open-scenario', scenario)"
      >
        <span class="block text-sm font-semibold text-gray-900 dark:text-white">{{ scenario.name }}</span>
        <span class="mt-2 line-clamp-2 block text-xs text-gray-500">{{ scenario.description }}</span>
        <span class="mt-3 inline-flex items-center gap-2">
          <span
            v-if="scenario.recommended"
            class="rounded border border-blue-100 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700"
          >推荐</span>
          <span class="text-[11px] font-medium text-violet-700">查看方案</span>
        </span>
      </button>
    </div>
    <div
      v-else-if="!agentsAvailable"
      class="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center dark:border-gray-700 dark:bg-gray-900"
    >
      <p class="text-sm text-gray-600 dark:text-gray-300">当前还没有可用的业务场景</p>
      <p class="mt-1 text-xs text-gray-500">请联系管理员开通智能体或相关资源。</p>
    </div>
    <div
      v-else
      class="rounded-xl border border-dashed border-violet-200 bg-violet-50/50 px-4 py-6 text-center dark:border-violet-900/40 dark:bg-violet-950/20"
    >
      <p class="text-sm text-gray-600 dark:text-gray-300">暂时没有推荐场景</p>
      <button
        type="button"
        class="mt-3 text-xs font-medium text-violet-600 hover:text-violet-700"
        @click="$emit('view-all')"
      >
        浏览场景包市场
      </button>
    </div>
    <WorkbenchMobileViewAll
      v-if="scenarios.length"
      label="浏览场景包"
      @view-all="$emit('view-all')"
    />
  </section>
</template>

<script setup lang="ts">
import type { WorkbenchScenario } from "@/types/workbench"
import WorkbenchMobileViewAll from "./WorkbenchMobileViewAll.vue"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

defineProps<{ scenarios: WorkbenchScenario[]; agentsAvailable?: boolean }>()
defineEmits<{
  (event: "open-scenario", scenario: WorkbenchScenario): void
  (event: "view-all"): void
}>()
</script>
