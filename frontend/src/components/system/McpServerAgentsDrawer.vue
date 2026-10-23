<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  XMarkIcon,
  ArrowPathIcon,
  MagnifyingGlassIcon,
  UsersIcon,
} from '@heroicons/vue/24/outline'

interface McpAgentUsage {
  id: string
  name: string
  display_name: string
  is_enabled: boolean
  active: boolean
  version_count: number
}

interface McpServerAgentUsage {
  bound_agent_count: number
  active_agent_count: number
  bound_version_count: number
  agents: McpAgentUsage[]
}

/**
 * 服务侧的「谁在用这个 MCP」只读清单。
 * 数据完全来自父级已拉取的 /servers/{id}/usage，组件不自己发请求，
 * 避免同一个服务在两个地方各算一遍（版本配置匹配逻辑只在后端一处）。
 */
const props = withDefaults(defineProps<{
  visible: boolean
  server: { id: string; server_name?: string } | null
  usage: McpServerAgentUsage | null
  loading?: boolean
}>(), {
  loading: false,
})

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'refresh'): void
}>()

const search = ref('')
const searchInput = ref<HTMLInputElement | null>(null)

const agents = computed<McpAgentUsage[]>(() => props.usage?.agents || [])
const filteredAgents = computed<McpAgentUsage[]>(() => {
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) return agents.value
  return agents.value.filter((agent) =>
    `${agent.display_name || ''} ${agent.name || ''}`.toLowerCase().includes(keyword),
  )
})

const agentInitial = (agent: McpAgentUsage): string =>
  (agent.display_name || agent.name || '?').trim().slice(0, 1).toUpperCase()

/** 三个状态互斥，顺序即优先级：停用的智能体即便版本生效也调不动。 */
const statusLabel = (agent: McpAgentUsage): string => {
  if (!agent.is_enabled) return '已停用'
  return agent.active ? '生效中' : '未生效'
}

const statusClass = (agent: McpAgentUsage): string => {
  if (!agent.is_enabled) return 'bg-gray-100 text-gray-500'
  return agent.active ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600'
}

const statusHint = (agent: McpAgentUsage): string => {
  if (!agent.is_enabled) return '智能体已停用，绑定关系仍在但不会调用'
  if (agent.active) return '智能体已启用、版本已发布，且引用了已发布的 MCP 工具'
  return '智能体已启用，但版本未发布或未勾选该服务已发布的工具'
}

const handleKeydown = (event: KeyboardEvent) => {
  if (!props.visible || event.key !== 'Escape') return
  event.stopPropagation()
  emit('close')
}

watch(
  () => props.visible,
  async (visible) => {
    if (!visible) {
      search.value = ''
      return
    }
    await nextTick()
    searchInput.value?.focus()
  },
)

onMounted(() => document.addEventListener('keydown', handleKeydown, true))
onUnmounted(() => document.removeEventListener('keydown', handleKeydown, true))
</script>

<template>
  <div v-if="visible" class="fixed inset-0 z-[70] flex justify-end">
    <div class="absolute inset-0 bg-gray-900/30 backdrop-blur-sm" @click="emit('close')"></div>

    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="mcp-agent-usage-title"
      class="relative flex h-full w-full max-w-md flex-col bg-white shadow-2xl animate-slide-in-right"
    >
      <div class="flex items-start justify-between gap-3 border-b border-gray-100 bg-gray-50/60 p-4">
        <div class="min-w-0">
          <h3 id="mcp-agent-usage-title" class="flex items-center gap-2 text-sm font-bold text-slate-800">
            <UsersIcon class="h-4 w-4 text-indigo-500" />
            绑定智能体
          </h3>
          <p class="mt-1 truncate font-mono text-[11px] text-slate-400" :title="server?.server_name">
            {{ server?.server_name }}
          </p>
        </div>
        <button
          type="button"
          class="shrink-0 rounded-md p-1 text-gray-400 transition-colors hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-200"
          aria-label="关闭绑定智能体"
          @click="emit('close')"
        >
          <XMarkIcon class="h-5 w-5" />
        </button>
      </div>

      <div class="flex items-center gap-3 border-b border-gray-100 px-4 py-3 text-[11px]">
        <span class="text-slate-500">绑定 <b class="font-bold text-slate-800">{{ usage?.bound_agent_count ?? 0 }}</b> 个</span>
        <span class="text-emerald-600">生效 <b class="font-bold">{{ usage?.active_agent_count ?? 0 }}</b> 个</span>
        <span class="text-slate-400">{{ usage?.bound_version_count ?? 0 }} 个版本配置</span>
        <button
          type="button"
          class="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 disabled:cursor-wait disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-200"
          :disabled="loading"
          @click="emit('refresh')"
        >
          <ArrowPathIcon class="h-3.5 w-3.5" :class="loading ? 'animate-spin' : ''" />
          刷新
        </button>
      </div>

      <div v-if="agents.length" class="border-b border-gray-100 px-4 py-2.5">
        <label class="relative block">
          <MagnifyingGlassIcon class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <input
            ref="searchInput"
            v-model="search"
            type="search"
            placeholder="搜索智能体名称"
            class="w-full rounded-lg border border-gray-200 bg-white py-1.5 pl-8 pr-3 text-xs text-slate-700 placeholder:text-gray-400 focus:border-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-100"
          />
        </label>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        <p v-if="loading && !agents.length" class="p-6 text-center text-xs text-slate-400">正在统计绑定情况...</p>

        <div v-else-if="!usage" class="flex h-full flex-col items-center justify-center px-6 text-center">
          <UsersIcon class="mb-3 h-8 w-8 text-slate-200" />
          <p class="text-sm font-semibold text-slate-600">暂时无法获取绑定情况</p>
          <p class="mt-1 text-[11px] leading-relaxed text-slate-400">请点击下方「刷新」重试。</p>
        </div>

        <div v-else-if="!agents.length" class="flex h-full flex-col items-center justify-center px-6 text-center">
          <UsersIcon class="mb-3 h-8 w-8 text-slate-200" />
          <p class="text-sm font-semibold text-slate-600">暂无智能体绑定该 MCP</p>
          <p class="mt-1 text-[11px] leading-relaxed text-slate-400">发布该服务的工具后，在智能体版本配置里勾选 MCP 工具即可绑定。</p>
        </div>

        <p v-else-if="!filteredAgents.length" class="p-6 text-center text-xs text-slate-400">没有匹配「{{ search }}」的智能体</p>

        <ul v-else class="divide-y divide-gray-100">
          <li v-for="agent in filteredAgents" :key="agent.id" class="flex items-center gap-3 px-4 py-3">
            <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-50 text-xs font-bold text-indigo-600">
              {{ agentInitial(agent) }}
            </span>
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-semibold text-slate-800" :title="agent.display_name || agent.name">
                {{ agent.display_name || agent.name }}
              </p>
              <p class="truncate font-mono text-[10px] text-slate-400">{{ agent.name }}</p>
            </div>
            <div class="flex shrink-0 flex-col items-end gap-1">
              <span
                class="rounded px-1.5 py-0.5 text-[10px] font-medium"
                :class="statusClass(agent)"
                :title="statusHint(agent)"
              >{{ statusLabel(agent) }}</span>
              <span class="text-[10px] text-slate-400">{{ agent.version_count }} 个版本</span>
            </div>
          </li>
        </ul>
      </div>

      <p class="border-t border-gray-100 bg-slate-50/60 px-4 py-2.5 text-[10px] leading-relaxed text-slate-400">
        「生效」= 智能体已启用、版本已发布，且勾选了该服务已发布的工具；停用服务或下线工具后，这些智能体将立即不可用。
      </p>
    </div>
  </div>
</template>

<style scoped>
.animate-slide-in-right { animation: slideInRight 0.25s ease-out; }
@keyframes slideInRight { from { transform: translateX(100%); } to { transform: translateX(0); } }
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #e5e7eb; border-radius: 4px; }
</style>
