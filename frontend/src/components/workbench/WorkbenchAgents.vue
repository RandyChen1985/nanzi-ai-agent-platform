<template>
  <section class="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm sm:p-5 dark:border-gray-800 dark:bg-gray-800/80">
    <WorkbenchSectionHeader
      eyebrow="常用助手"
      title="最近使用的助手"
      tone="emerald"
      view-all-label="打开智能助手"
      @view-all="$emit('view-all')"
    />
    <div v-if="sortedAgents.length" class="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
      <div
        v-for="agent in sortedAgents"
        :key="agent.id"
        class="group relative flex flex-col justify-between rounded-xl border border-gray-100 bg-gray-50/40 p-3.5 transition-all duration-200 hover:-translate-y-0.5 hover:border-emerald-200 hover:bg-emerald-50/20 hover:shadow-xs dark:border-gray-700/60 dark:bg-gray-800/50 dark:hover:border-emerald-500/40 dark:hover:bg-emerald-950/20"
      >
        <!-- 置顶图钉按钮 -->
        <button
          type="button"
          class="absolute right-2.5 top-2.5 flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition-colors hover:text-emerald-600 dark:text-gray-500 dark:hover:text-emerald-400"
          :class="{ '!text-emerald-600 dark:!text-emerald-400 opacity-100': isPinned(agent.id), 'opacity-0 group-hover:opacity-100': !isPinned(agent.id) }"
          :title="isPinned(agent.id) ? '取消置顶' : '置顶助手'"
          @click.stop="togglePin(agent.id)"
        >
          <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="17" x2="12" y2="22" />
            <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z" />
          </svg>
        </button>

        <button
          type="button"
          class="text-left"
          @click="$emit('open-agent', agent)"
        >
          <div class="flex items-start gap-3">
            <span
              class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-sm font-bold text-emerald-700 ring-1 ring-emerald-100 dark:bg-emerald-950/60 dark:text-emerald-400 dark:ring-emerald-800/40"
              aria-hidden="true"
            >{{ initial(agent.name) }}</span>
            <span class="min-w-0 flex-1 pr-4">
              <span class="flex items-center gap-1.5">
                <span class="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">{{ agent.name }}</span>
                <span v-if="isPinned(agent.id)" class="inline-flex rounded bg-emerald-50 px-1 py-0.2 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-400">置顶</span>
              </span>
              <span class="mt-1 line-clamp-2 block text-xs text-gray-500 dark:text-gray-400">{{ agent.description || '开始与助手对话' }}</span>
            </span>
          </div>
        </button>

        <div class="mt-3 flex items-center justify-between border-t border-gray-100/80 pt-2.5 dark:border-gray-700/50">
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md bg-emerald-600 px-2.5 py-1 text-[11px] font-medium text-white shadow-xs transition hover:bg-emerald-700 active:scale-95"
            @click="$emit('open-agent', agent)"
          >
            <span>开始对话</span>
            <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
          <span
            v-if="(agent.execution_count ?? 0) > 0"
            class="text-[11px] text-gray-400 dark:text-gray-500"
          >{{ agent.execution_count }} 次调用</span>
        </div>
      </div>
    </div>
    <div
      v-else
      class="rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-4 py-6 text-center dark:border-gray-700 dark:bg-gray-800/40"
    >
      <p class="text-sm text-gray-500 dark:text-gray-400">暂无可用助手</p>
      <button
        type="button"
        class="mt-3 text-xs font-medium text-emerald-600 hover:text-emerald-700 dark:text-emerald-400 dark:hover:text-emerald-300"
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
import { ref, computed } from "vue"
import type { WorkbenchAgent } from "@/types/workbench"
import WorkbenchMobileViewAll from "./WorkbenchMobileViewAll.vue"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

const props = defineProps<{ agents: WorkbenchAgent[] }>()
defineEmits<{
  (event: "open-agent", agent: WorkbenchAgent): void
  (event: "view-all"): void
}>()

const initial = (name: string) => (name || "助").trim().charAt(0).toUpperCase() || "助"

const PIN_STORAGE_KEY = "workbench_pinned_agent_ids"
const loadPinnedIds = (): string[] => {
  try {
    const raw = localStorage.getItem(PIN_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as (string | number)[]).map(String) : []
  } catch {
    return []
  }
}

const pinnedIds = ref<string[]>(loadPinnedIds())

const isPinned = (id: string | number) => pinnedIds.value.includes(String(id))

const togglePin = (id: string | number) => {
  const sid = String(id)
  if (pinnedIds.value.includes(sid)) {
    pinnedIds.value = pinnedIds.value.filter((item) => item !== sid)
  } else {
    pinnedIds.value.push(sid)
  }
  try {
    localStorage.setItem(PIN_STORAGE_KEY, JSON.stringify(pinnedIds.value))
  } catch {
    // ignore
  }
}

const sortedAgents = computed(() => {
  if (!props.agents || !Array.isArray(props.agents) || !props.agents.length) return []
  return [...props.agents].sort((a, b) => {
    const aPin = a?.id != null && isPinned(a.id) ? 1 : 0
    const bPin = b?.id != null && isPinned(b.id) ? 1 : 0
    return bPin - aPin
  })
})
</script>

