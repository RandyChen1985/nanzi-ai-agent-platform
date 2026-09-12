<script setup lang="ts">
import { ref, computed, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    routingMode?: string
    expertAgentId?: string
    allowedAgents?: any[]
    isLoadingAgents?: boolean
    compact?: boolean
    /** 移动端底部抽屉内铺满宽度 */
    fullWidth?: boolean
    /** 桌面侧栏浮层：高度铺满左侧加号菜单，上下对齐 */
    fillHeight?: boolean
  }>(),
  {
    routingMode: 'auto',
    expertAgentId: '',
    allowedAgents: () => [],
    isLoadingAgents: false,
    compact: false,
    fullWidth: false,
    fillHeight: false,
  },
)

const emit = defineEmits<{
  (e: 'select-auto'): void
  (e: 'select-expert', agentId: string): void
  (e: 'refresh'): void
  (e: 'close'): void
}>()

const isExpertMode = (routingMode?: string, expertAgentId?: string) =>
  routingMode === 'expert' && !!expertAgentId

const expertTab = ref<'system' | 'custom'>('system')
const expertSearchQuery = ref('')

const isMainAgent = (agent: any) => {
  if (!agent) return false
  if (typeof agent === 'string') return agent === 'sys-agent-chat' || agent === 'main'
  return (
    agent.id === 'sys-agent-chat' ||
    ['main', 'assistant', 'general-chat'].includes(String(agent.name || '').trim().toLowerCase())
  )
}

const systemAgents = computed(() => {
  const sys = (props.allowedAgents || []).filter((agent) => agent.is_system || isMainAgent(agent))
  return [...sys].sort((a, b) => {
    const aMain = isMainAgent(a) ? 1 : 0
    const bMain = isMainAgent(b) ? 1 : 0
    if (aMain !== bMain) return bMain - aMain
    return (b.sort_order || 0) - (a.sort_order || 0)
  })
})

const customAgents = computed(() => {
  return (props.allowedAgents || [])
    .filter((agent) => !agent.is_system && !isMainAgent(agent))
    .sort((a, b) => (b.sort_order || 0) - (a.sort_order || 0))
})

const filterList = (list: any[]) => {
  const q = expertSearchQuery.value.trim().toLowerCase()
  if (!q) return list
  return list.filter((agent) => {
    const matchName = String(agent.name || '').toLowerCase().includes(q)
    const matchDisplay = String(agent.display_name || '').toLowerCase().includes(q)
    const matchDesc = String(agent.description || '').toLowerCase().includes(q)
    return matchName || matchDisplay || matchDesc
  })
}

const filteredSystemAgents = computed(() => filterList(systemAgents.value))
const filteredCustomAgents = computed(() => filterList(customAgents.value))

const shouldShowAutoCard = computed(() => {
  if (expertTab.value !== 'system') return false
  const q = expertSearchQuery.value.trim().toLowerCase()
  if (!q) return true
  return '智能委派自动委派主助手auto'.includes(q)
})

watch(
  () => [props.expertAgentId, props.routingMode],
  () => {
    if (props.routingMode === 'expert' && props.expertAgentId) {
      const match = (props.allowedAgents || []).find((a) => a.id === props.expertAgentId)
      if (match) {
        expertTab.value = match.is_system || isMainAgent(match) ? 'system' : 'custom'
      }
    }
  },
  { immediate: true },
)

const currentTabTotalCount = computed(() => {
  return expertTab.value === 'system' ? systemAgents.value.length : customAgents.value.length
})

const showSearchInput = computed(() => {
  return currentTabTotalCount.value > 5 || !!expertSearchQuery.value.trim()
})

const switchTab = (tab: 'system' | 'custom') => {
  if (expertTab.value === tab) return
  expertTab.value = tab
  expertSearchQuery.value = ''
}

const handleSelectAuto = () => {
  expertSearchQuery.value = ''
  emit('select-auto')
}

const handleSelectExpert = (agentId: string) => {
  expertSearchQuery.value = ''
  emit('select-expert', agentId)
}
</script>

<template>
  <div
    class="flex flex-col overflow-hidden bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-2xl"
    :class="fullWidth
      ? 'w-full max-h-[min(70vh,32rem)] rounded-none border-x-0 border-b-0 shadow-none'
      : fillHeight
        ? 'h-full w-[min(28rem,calc(100vw-1.5rem))] max-h-none rounded-xl shadow-xl'
        : compact
          ? 'w-[min(26rem,calc(100vw-1.25rem))] max-h-[min(65vh,32rem)] rounded-xl shadow-xl'
          : 'w-[min(28rem,calc(100vw-1.5rem))] max-h-[min(68vh,34rem)] rounded-xl shadow-xl'"
    role="menu"
    aria-label="专家中心"
  >
    <!-- 头部标题 -->
    <div class="px-3 py-2.5 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between shrink-0 bg-white dark:bg-gray-800">
      <div class="flex items-center gap-1.5 min-w-0">
        <span class="w-1 h-3.5 bg-primary rounded-full shrink-0" />
        <span class="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
          专家中心({{ allowedAgents.length }})
        </span>
        <button
          type="button"
          class="text-gray-500 hover:text-primary transition-all p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 shrink-0"
          :class="{ 'animate-spin text-primary': isLoadingAgents }"
          title="刷新列表"
          @click.stop="emit('refresh')"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>
      </div>
      <button
        v-if="!fullWidth"
        type="button"
        class="flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200 shrink-0"
        aria-label="关闭专家中心"
        title="关闭"
        @click.stop="emit('close')"
      >
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.25" d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </div>

    <!-- Tab 切换头与即时搜索框 -->
    <div class="px-2.5 pt-2 pb-1.5 border-b border-gray-100 dark:border-gray-700/80 bg-gray-50/50 dark:bg-gray-800/80 shrink-0 space-y-1.5">
      <!-- 双 Tab 切换 -->
      <div class="flex items-center gap-1 rounded-lg bg-gray-200/60 dark:bg-gray-700/60 p-1">
        <button
          type="button"
          class="flex flex-1 items-center justify-center gap-1.5 rounded-md py-1 text-xs font-semibold transition-all"
          :class="
            expertTab === 'system'
              ? 'bg-white dark:bg-gray-800 text-blue-600 dark:text-blue-400 shadow-sm'
              : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
          "
          @click.stop="switchTab('system')"
        >
          <svg class="h-3.5 w-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
          </svg>
          <span>系统专家</span>
          <span
            class="rounded px-1 text-[9px] font-normal"
            :class="expertTab === 'system' ? 'bg-blue-50 dark:bg-blue-900/40 text-blue-600 dark:text-blue-300' : 'bg-gray-200 dark:bg-gray-600 text-gray-500 dark:text-gray-300'"
          >{{ expertSearchQuery ? `${filteredSystemAgents.length}/${systemAgents.length}` : systemAgents.length }}</span>
        </button>

        <button
          type="button"
          class="flex flex-1 items-center justify-center gap-1.5 rounded-md py-1 text-xs font-semibold transition-all"
          :class="
            expertTab === 'custom'
              ? 'bg-white dark:bg-gray-800 text-emerald-600 dark:text-emerald-400 shadow-sm'
              : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
          "
          @click.stop="switchTab('custom')"
        >
          <svg class="h-3.5 w-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
          </svg>
          <span>自定义专家</span>
          <span
            class="rounded px-1 text-[9px] font-normal"
            :class="expertTab === 'custom' ? 'bg-emerald-50 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-300' : 'bg-gray-200 dark:bg-gray-600 text-gray-500 dark:text-gray-300'"
          >{{ expertSearchQuery ? `${filteredCustomAgents.length}/${customAgents.length}` : customAgents.length }}</span>
        </button>
      </div>

      <!-- 搜索过滤输入框：仅当前分类专家数量 > 5 或已有搜索词时展示 -->
      <div v-if="showSearchInput" class="relative flex items-center">
        <svg
          class="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-gray-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
          />
        </svg>
        <input
          v-model="expertSearchQuery"
          type="text"
          placeholder="搜索专家名称、标识或说明..."
          class="w-full rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700/60 py-1.5 pl-8 pr-7 text-xs text-gray-700 dark:text-gray-200 placeholder-gray-400 outline-none transition-all focus:border-primary/50 focus:ring-2 focus:ring-primary/20"
          @click.stop
        />
        <button
          v-if="expertSearchQuery"
          type="button"
          class="absolute right-2 flex h-4 w-4 items-center justify-center rounded-full text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
          title="清空搜索"
          @click.stop="expertSearchQuery = ''"
        >
          <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 列表内容滚动区 -->
    <div
      class="overflow-y-auto custom-scrollbar p-2 space-y-0.5 bg-white dark:bg-gray-800"
      :class="fillHeight ? 'flex-1 min-h-0' : 'min-h-[10rem] max-h-[min(52vh,26rem)]'"
    >
      <div v-if="isLoadingAgents && allowedAgents.length === 0" class="flex flex-col items-center justify-center py-12 opacity-50">
        <svg class="w-7 h-7 animate-spin text-primary mb-2" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
        <span class="text-xs font-medium text-gray-400">同步中</span>
      </div>

      <!-- 系统专家 Tab 内容 -->
      <template v-if="expertTab === 'system'">
        <button
          v-if="shouldShowAutoCard"
          type="button"
          class="w-full flex items-start gap-2.5 px-2.5 py-2.5 rounded-lg cursor-pointer transition-colors border border-transparent text-left"
          :class="!isExpertMode(routingMode, expertAgentId)
            ? 'bg-primary/10 border-primary/15'
            : 'hover:bg-gray-50 dark:hover:bg-gray-700/60'"
          @click.stop="handleSelectAuto"
        >
          <div class="w-9 h-9 mt-0.5 rounded-full bg-primary/10 flex items-center justify-center text-primary border border-primary/15 shrink-0">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between gap-2">
              <span
                class="text-sm font-semibold truncate"
                :class="!isExpertMode(routingMode, expertAgentId) ? 'text-primary' : 'text-gray-900 dark:text-gray-100'"
              >智能委派</span>
              <svg
                v-if="!isExpertMode(routingMode, expertAgentId)"
                class="w-3.5 h-3.5 text-primary shrink-0"
                fill="currentColor"
                viewBox="0 0 20 20"
              >
                <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
              </svg>
            </div>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-snug line-clamp-2">由主助手直接处理，或按任务需要自动委派其他专家</p>
          </div>
        </button>

        <div v-if="shouldShowAutoCard && filteredSystemAgents.length" class="h-px bg-gray-200 dark:bg-gray-700 my-1 mx-1.5" />

        <button
          v-for="agent in filteredSystemAgents"
          :key="agent.id"
          type="button"
          class="w-full flex items-start gap-2.5 px-2.5 py-2.5 rounded-lg cursor-pointer transition-colors border border-transparent text-left"
          :class="isExpertMode(routingMode, expertAgentId) && expertAgentId === agent.id
            ? 'bg-primary/10 border-primary/15'
            : 'hover:bg-gray-50 dark:hover:bg-gray-700/60'"
          :title="agent.description || agent.display_name"
          @click.stop="handleSelectExpert(agent.id)"
        >
          <div class="w-9 h-9 mt-0.5 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center overflow-hidden border border-gray-200 dark:border-gray-600 shrink-0">
            <img v-if="agent.avatar_url" :src="agent.avatar_url" class="w-full h-full object-cover" />
            <span v-else class="text-xs font-bold text-gray-500 dark:text-gray-300">{{ Array.from(agent.display_name || 'E')[0] }}</span>
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-1.5 min-w-0">
                <span
                  class="text-sm font-semibold truncate"
                  :class="isExpertMode(routingMode, expertAgentId) && expertAgentId === agent.id
                    ? 'text-primary'
                    : 'text-gray-900 dark:text-gray-100'"
                >{{ agent.display_name }}</span>
                <span
                  v-if="isMainAgent(agent)"
                  class="shrink-0 px-1 py-px text-[8px] font-semibold rounded border border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300 uppercase"
                >MAIN</span>
                <span
                  v-else-if="agent.is_system"
                  class="shrink-0 px-1 py-px text-[8px] font-semibold rounded bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300 uppercase"
                >SYS</span>
              </div>
              <svg
                v-if="isExpertMode(routingMode, expertAgentId) && expertAgentId === agent.id"
                class="w-3.5 h-3.5 text-primary shrink-0"
                fill="currentColor"
                viewBox="0 0 20 20"
              >
                <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
              </svg>
            </div>
            <p
              class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-snug line-clamp-2"
              :title="agent.description || '专属能力专家'"
            >{{ agent.description || '专属能力专家' }}</p>
          </div>
        </button>

        <div v-if="!filteredSystemAgents.length && !shouldShowAutoCard" class="py-8 text-center text-xs text-gray-400">
          <p v-if="expertSearchQuery">未找到与 "<span class="text-gray-600 dark:text-gray-300 font-medium">{{ expertSearchQuery }}</span>" 匹配的系统专家</p>
          <p v-else>暂无系统专家</p>
          <button
            v-if="expertSearchQuery"
            type="button"
            class="mt-2 text-[11px] text-primary hover:underline"
            @click.stop="expertSearchQuery = ''"
          >
            清空搜索条件
          </button>
        </div>
      </template>

      <!-- 自定义专家 Tab 内容 -->
      <template v-else-if="expertTab === 'custom'">
        <button
          v-for="agent in filteredCustomAgents"
          :key="agent.id"
          type="button"
          class="w-full flex items-start gap-2.5 px-2.5 py-2.5 rounded-lg cursor-pointer transition-colors border border-transparent text-left"
          :class="isExpertMode(routingMode, expertAgentId) && expertAgentId === agent.id
            ? 'bg-primary/10 border-primary/15'
            : 'hover:bg-gray-50 dark:hover:bg-gray-700/60'"
          :title="agent.description || agent.display_name"
          @click.stop="handleSelectExpert(agent.id)"
        >
          <div class="w-9 h-9 mt-0.5 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center overflow-hidden border border-gray-200 dark:border-gray-600 shrink-0">
            <img v-if="agent.avatar_url" :src="agent.avatar_url" class="w-full h-full object-cover" />
            <span v-else class="text-xs font-bold text-gray-500 dark:text-gray-300">{{ Array.from(agent.display_name || 'E')[0] }}</span>
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-1.5 min-w-0">
                <span
                  class="text-sm font-semibold truncate"
                  :class="isExpertMode(routingMode, expertAgentId) && expertAgentId === agent.id
                    ? 'text-primary'
                    : 'text-gray-900 dark:text-gray-100'"
                >{{ agent.display_name }}</span>
              </div>
              <svg
                v-if="isExpertMode(routingMode, expertAgentId) && expertAgentId === agent.id"
                class="w-3.5 h-3.5 text-primary shrink-0"
                fill="currentColor"
                viewBox="0 0 20 20"
              >
                <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
              </svg>
            </div>
            <p
              class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-snug line-clamp-2"
              :title="agent.description || '专属能力专家'"
            >{{ agent.description || '专属能力专家' }}</p>
          </div>
        </button>

        <div v-if="!filteredCustomAgents.length" class="py-8 text-center text-xs text-gray-400">
          <p v-if="expertSearchQuery">未找到与 "<span class="text-gray-600 dark:text-gray-300 font-medium">{{ expertSearchQuery }}</span>" 匹配的自定义专家</p>
          <p v-else>暂无自定义专家</p>
          <button
            v-if="expertSearchQuery"
            type="button"
            class="mt-2 text-[11px] text-primary hover:underline"
            @click.stop="expertSearchQuery = ''"
          >
            清空搜索条件
          </button>
        </div>
      </template>
    </div>

    <!-- 底部统计 -->
    <div class="px-3 py-2 border-t border-gray-200 dark:border-gray-700 text-center shrink-0 bg-gray-50 dark:bg-gray-900/80">
      <span class="text-[11px] text-gray-500 dark:text-gray-400">
        <template v-if="expertSearchQuery">
          筛选到 {{ filteredSystemAgents.length + filteredCustomAgents.length }} 个专家 (共 {{ allowedAgents.length }} 个)
        </template>
        <template v-else>
          共 {{ allowedAgents.length }} 个专家
        </template>
      </span>
    </div>
  </div>
</template>
