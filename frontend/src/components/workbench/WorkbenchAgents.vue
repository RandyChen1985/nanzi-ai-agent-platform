<template>
  <section class="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-900/40">
    <WorkbenchSectionHeader
      eyebrow="可用助手"
      title="最近使用的助手"
      description="按调用次数排序，一键继续对话。"
      tone="emerald"
      view-all-label="打开智能助手"
      @view-all="$emit('view-all')"
    />
    <div v-if="agents.length" class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <button
        v-for="agent in agents"
        :key="agent.id"
        type="button"
        class="rounded-2xl border border-gray-300 bg-white p-4 text-left shadow-sm transition hover:border-emerald-300 hover:shadow-md dark:border-gray-700 dark:bg-gray-900"
        @click="$emit('open-agent', agent)"
      >
        <div class="flex items-start gap-3">
          <span
            class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-lg dark:bg-emerald-950/40"
            aria-hidden="true"
          >{{ emoji(agent.name) }}</span>
          <span class="min-w-0 flex-1">
            <span class="block text-sm font-semibold text-gray-900 dark:text-white">{{ agent.name }}</span>
            <span class="mt-1 line-clamp-2 block text-xs text-gray-500">{{ agent.description || '开始与助手对话' }}</span>
            <span class="mt-3 inline-flex items-center gap-2">
              <span class="rounded-lg bg-emerald-600 px-2.5 py-1 text-[11px] font-medium text-white">开始对话</span>
              <span
                v-if="(agent.execution_count ?? 0) > 0"
                class="text-[11px] text-gray-400"
              >{{ agent.execution_count }} 次调用</span>
            </span>
          </span>
        </div>
      </button>
    </div>
    <div
      v-else
      class="rounded-xl border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center dark:border-gray-700 dark:bg-gray-900"
    >
      <p class="text-sm text-gray-600 dark:text-gray-300">暂无可用助手</p>
      <button
        type="button"
        class="mt-3 text-xs font-medium text-emerald-600 hover:text-emerald-700"
        @click="$emit('view-all')"
      >
        打开智能助手看看
      </button>
    </div>
    <WorkbenchMobileViewAll
      v-if="agents.length"
      label="打开智能助手"
      @view-all="$emit('view-all')"
    />
  </section>
</template>

<script setup lang="ts">
import type { WorkbenchAgent } from "@/types/workbench"
import { workbenchAgentEmoji } from "@/utils/workbenchDisplay"
import WorkbenchMobileViewAll from "./WorkbenchMobileViewAll.vue"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

defineProps<{ agents: WorkbenchAgent[] }>()
defineEmits<{
  (event: "open-agent", agent: WorkbenchAgent): void
  (event: "view-all"): void
}>()

const emoji = (name: string) => workbenchAgentEmoji(name)
</script>
