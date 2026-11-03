<template>
  <section class="overflow-hidden rounded-xl border border-gray-200/80 bg-white shadow-xs dark:border-gray-800 dark:bg-gray-900/40">
    <div class="flex items-center gap-2 px-3.5 pb-2 pt-3">
      <h2 class="text-[12.5px] font-semibold tracking-tight text-gray-900 dark:text-gray-100">常用助手</h2>
      <span class="flex-1" />
      <button
        type="button"
        class="text-[11.5px] font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
        @click="$emit('view-all')"
      >
        全部 →
      </button>
    </div>

    <!-- 外层不能用 button：行尾的置顶按钮会变成 button 套 button -->
    <div
      v-for="agent in sortedAgents"
      :key="agent.id"
      role="button"
      tabindex="0"
      class="group flex cursor-pointer items-center gap-2.5 border-t border-gray-100 px-3.5 py-2.5 transition-colors hover:bg-blue-50/40 focus:outline-none focus-visible:bg-blue-50/60 dark:border-gray-800/70 dark:hover:bg-blue-950/20"
      @click="$emit('open-agent', agent)"
      @keydown.enter.prevent="$emit('open-agent', agent)"
      @keydown.space.prevent="$emit('open-agent', agent)"
    >
      <span
        class="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-emerald-50 text-[12.5px] font-bold text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400"
        aria-hidden="true"
      >
        <img v-if="agentOwnAvatarUrl(agent)" :src="agentOwnAvatarUrl(agent)" alt="" class="h-full w-full object-cover" />
        <template v-else>{{ initial(agent.name) }}</template>
      </span>

      <span class="min-w-0 flex-1">
        <span class="block truncate text-[12.5px] font-semibold text-gray-900 dark:text-gray-100">{{ agent.name }}</span>
        <span class="mt-px block truncate text-[11px] text-gray-400 dark:text-gray-500">
          {{ agent.description || '开始与助手对话' }}
        </span>
      </span>

      <span class="relative flex h-5 w-6 shrink-0 items-center justify-center">
        <span
          v-if="(agent.execution_count ?? 0) > 0"
          class="text-[11px] tabular-nums text-gray-400 group-hover:invisible dark:text-gray-500"
        >{{ agent.execution_count }}</span>
        <button
          type="button"
          class="absolute inset-0 flex items-center justify-center rounded text-gray-400 opacity-0 transition-opacity hover:text-emerald-600 group-hover:opacity-100 dark:text-gray-500"
          :class="isPinned(agent.id) ? '!opacity-100 !text-emerald-600 dark:!text-emerald-400' : ''"
          :title="isPinned(agent.id) ? '取消置顶' : '置顶助手'"
          :aria-label="isPinned(agent.id) ? '取消置顶' : '置顶助手'"
          @click.stop="togglePin(agent.id)"
        >
          <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="17" x2="12" y2="22" />
            <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z" />
          </svg>
        </button>
      </span>
    </div>

    <div v-if="!sortedAgents.length" class="border-t border-gray-100 px-3.5 py-6 text-center dark:border-gray-800/70">
      <p class="text-[11.5px] text-gray-400 dark:text-gray-500">暂无可用助手</p>
      <button
        type="button"
        class="mt-2 text-[11.5px] font-medium text-emerald-600 hover:text-emerald-700 dark:text-emerald-400 dark:hover:text-emerald-300"
        @click="$emit('view-all')"
      >
        打开智能助手看看
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"
import type { WorkbenchAgent } from "@/types/workbench"
import { agentOwnAvatarUrl } from "@/utils/agentAvatar"

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
    // localStorage 不可用（隐私模式）时忽略，置顶只影响本次会话
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
