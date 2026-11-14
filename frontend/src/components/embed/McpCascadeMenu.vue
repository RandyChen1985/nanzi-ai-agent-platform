<script setup lang="ts">
import { ref, computed } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import { mcpToolDisplayName } from '@/utils/mcpToolDisplayName'

export interface McpToolItem {
  id: string
  name: string
  description?: string
  server_name?: string
  server_remark?: string
  scope?: 'global' | 'personal' | string
}

export interface McpToolGroup {
  serverName: string
  serverRemark?: string
  tools: McpToolItem[]
}

const props = withDefaults(
  defineProps<{
    /** 已挂载到会话的 MCP 工具名 */
    attachedToolIds?: string[]
    compact?: boolean
    fullWidth?: boolean
    /** 桌面侧栏浮层：高度铺满左侧加号菜单，上下对齐 */
    fillHeight?: boolean
  }>(),
  { attachedToolIds: () => [], compact: false, fullWidth: false, fillHeight: false },
)

const emit = defineEmits<{
  /** 确认挂载：支持多选批量 */
  (e: 'select', tools: McpToolItem[]): void
}>()

const { showToast } = useToast()

const toolsList = ref<McpToolItem[]>([])
const isLoading = ref(false)
const searchQuery = ref('')
const loadedOnce = ref(false)
/** 勾选中的工具 id（不含已挂载） */
const selectedIds = ref<Set<string>>(new Set())
/** 折叠的分组 serverName */
const collapsedGroups = ref<Set<string>>(new Set())

const attachedIdSet = computed(() => new Set(props.attachedToolIds))

/** 会话挂载仅允许个人已发布 MCP */
const personalTools = computed(() =>
  toolsList.value.filter((t) => String(t.scope || '').toLowerCase() === 'personal'),
)

const filteredTools = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return personalTools.value
  return personalTools.value.filter(
    (t) =>
      t.name?.toLowerCase().includes(query) ||
      t.id?.toLowerCase().includes(query) ||
      t.description?.toLowerCase().includes(query) ||
      t.server_name?.toLowerCase().includes(query) ||
      t.server_remark?.toLowerCase().includes(query),
  )
})

const groupedTools = computed((): McpToolGroup[] => {
  const map = new Map<string, McpToolItem[]>()
  for (const tool of filteredTools.value) {
    const key = String(tool.server_name || '未命名服务').trim() || '未命名服务'
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(tool)
  }
  return Array.from(map.entries())
    .map(([serverName, tools]) => ({
      serverName,
      serverRemark: String(tools.find((t) => t.server_remark)?.server_remark || '').trim() || undefined,
      tools: tools.slice().sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')),
    }))
    .sort((a, b) => a.serverName.localeCompare(b.serverName, 'zh-CN'))
})

const selectedCount = computed(() => selectedIds.value.size)

const selectedTools = computed(() =>
  personalTools.value.filter((t) => selectedIds.value.has(t.id) && !attachedIdSet.value.has(t.id)),
)

const isAttached = (tool: McpToolItem) => attachedIdSet.value.has(tool.id)
const isSelected = (tool: McpToolItem) => selectedIds.value.has(tool.id)

const selectableToolsInGroup = (group: McpToolGroup) => group.tools.filter((t) => !isAttached(t))

const groupSelectionState = (group: McpToolGroup): 'none' | 'partial' | 'all' => {
  const selectable = selectableToolsInGroup(group)
  if (!selectable.length) return 'none'
  const selected = selectable.filter((t) => selectedIds.value.has(t.id))
  if (!selected.length) return 'none'
  if (selected.length === selectable.length) return 'all'
  return 'partial'
}

const toolInitial = (tool: McpToolItem, serverName?: string) => {
  const raw = (mcpToolDisplayName(tool.name || tool.id, serverName) || 'M').trim()
  return raw.charAt(0).toUpperCase()
}

const clearSelection = () => {
  selectedIds.value = new Set()
}

const loadTools = async () => {
  isLoading.value = true
  try {
    const res = await axios.get('/api/portal/tools/mcp')
    const raw = Array.isArray(res.data) ? res.data : res.data?.data || []
    toolsList.value = (raw || []).map((t: any) => ({
      id: String(t.id || ''),
      name: String(t.name || ''),
      description: String(t.description || ''),
      server_name: String(t.server_name || 'Unknown'),
      server_remark: String(t.server_remark || '').trim(),
      scope: String(t.scope || 'global'),
    })).filter((t: McpToolItem) => t.id && t.name && String(t.scope || '').toLowerCase() === 'personal')
    // 加号菜单默认折叠分组，展开后再选工具
    collapsedGroups.value = new Set(
      toolsList.value.map((t) => String(t.server_name || '未命名服务').trim() || '未命名服务'),
    )
    loadedOnce.value = true
  } catch (err) {
    console.error('加载 MCP 工具列表失败:', err)
    showToast('加载 MCP 工具列表失败', 'error')
  } finally {
    isLoading.value = false
  }
}

const toggleTool = (tool: McpToolItem) => {
  if (isAttached(tool)) return
  const next = new Set(selectedIds.value)
  if (next.has(tool.id)) next.delete(tool.id)
  else next.add(tool.id)
  selectedIds.value = next
}

const toggleGroupSelectAll = (group: McpToolGroup) => {
  const selectable = selectableToolsInGroup(group)
  if (!selectable.length) return
  const state = groupSelectionState(group)
  const next = new Set(selectedIds.value)
  if (state === 'all') {
    for (const t of selectable) next.delete(t.id)
  } else {
    for (const t of selectable) next.add(t.id)
  }
  selectedIds.value = next
}

const toggleGroupCollapsed = (serverName: string) => {
  const next = new Set(collapsedGroups.value)
  if (next.has(serverName)) next.delete(serverName)
  else next.add(serverName)
  collapsedGroups.value = next
}

const confirmMount = () => {
  const tools = selectedTools.value
  if (!tools.length) {
    showToast('请先勾选要挂载的 MCP 工具', 'warning')
    return
  }
  emit('select', tools)
  clearSelection()
}

defineExpose({
  reload: loadTools,
  resetSearch: () => {
    searchQuery.value = ''
  },
  clearSelection,
})

void loadTools()
</script>

<template>
  <div
    class="flex flex-col bg-white dark:bg-gray-800 overflow-hidden border border-gray-200 dark:border-gray-700"
    :class="fullWidth
      ? 'w-full max-h-[min(65vh,28rem)] rounded-none border-x-0 border-b-0 shadow-none'
      : fillHeight
        ? 'h-full w-[min(28rem,calc(100vw-1.5rem))] max-h-none rounded-xl shadow-xl'
        : compact
          ? 'w-[min(26rem,calc(100vw-1.25rem))] max-h-[min(58vh,28rem)] rounded-xl shadow-xl'
          : 'w-[min(28rem,calc(100vw-1.5rem))] max-h-[min(60vh,30rem)] rounded-xl shadow-xl'"
    role="menu"
    aria-label="MCP 工具"
  >
    <div class="p-3 pb-2 shrink-0 space-y-2.5">
      <div class="relative">
        <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
          <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </span>
        <input
          v-model="searchQuery"
          type="search"
          placeholder="搜索我的 MCP 工具或服务名"
          class="w-full pl-9 pr-3 py-2 bg-gray-100 dark:bg-gray-900/80 border-0 rounded-lg focus:ring-2 focus:ring-primary/40 focus:outline-none text-sm text-gray-800 dark:text-gray-100 placeholder:text-gray-400"
          @click.stop
          @keydown.stop
        />
      </div>
      <p class="text-[10px] text-gray-400 leading-relaxed px-0.5">
        仅可挂载个人已发布 MCP；平台公共 MCP 请由智能体版本配置。
      </p>
    </div>

    <!-- fillHeight 时由父级定高，列表 flex-1 滚动；否则用固定 max-h -->
    <div
      class="overflow-y-auto overscroll-y-contain px-2 pb-1 custom-scrollbar"
      :class="fillHeight ? 'flex-1 min-h-0' : 'min-h-[10rem] max-h-[min(48vh,22rem)]'"
    >
      <div v-if="isLoading" class="flex flex-col items-center justify-center py-12 opacity-50">
        <div class="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        <span class="text-xs font-medium text-gray-400 mt-2">加载中...</span>
      </div>

      <div v-else-if="groupedTools.length === 0" class="text-center py-12 px-4">
        <p class="text-sm text-gray-400 font-medium">暂无个人 MCP 工具</p>
        <p class="text-xs text-gray-400/80 mt-2 leading-relaxed">
          前往
          <a
            class="text-emerald-600 hover:underline font-semibold"
            href="/dashboard/personal?tab=mcp"
            target="_blank"
            rel="noopener noreferrer"
            @click.stop
          >个人中心 · 我的 MCP</a>
          登记服务并发布工具
        </p>
      </div>

      <div
        v-for="group in groupedTools"
        :key="group.serverName"
        class="mb-2 rounded-lg border border-gray-100 dark:border-gray-700/80 overflow-hidden"
      >
        <!-- Group header -->
        <div
          class="flex items-center gap-2 px-2.5 py-2 bg-gray-50/90 dark:bg-gray-900/60 border-b border-gray-100 dark:border-gray-700/60"
        >
          <label
            class="inline-flex items-center justify-center shrink-0 cursor-pointer"
            :class="selectableToolsInGroup(group).length ? '' : 'opacity-40 cursor-not-allowed'"
            @click.stop
          >
            <input
              type="checkbox"
              class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary/40 cursor-pointer disabled:cursor-not-allowed"
              :checked="groupSelectionState(group) === 'all'"
              :indeterminate="groupSelectionState(group) === 'partial'"
              :disabled="!selectableToolsInGroup(group).length"
              @change.stop="toggleGroupSelectAll(group)"
            />
          </label>
          <button
            type="button"
            class="flex-1 min-w-0 flex items-center gap-1.5 text-left"
            @click.stop="toggleGroupCollapsed(group.serverName)"
          >
            <svg
              class="w-3.5 h-3.5 text-gray-400 shrink-0 transition-transform"
              :class="collapsedGroups.has(group.serverName) ? '-rotate-90' : ''"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
            <span class="min-w-0 flex-1">
              <span
                class="text-xs font-semibold text-gray-800 dark:text-gray-100 truncate block"
                :title="group.serverRemark ? `${group.serverName} · ${group.serverRemark}` : group.serverName"
              >
                {{ group.serverName }}
              </span>
              <span
                v-if="group.serverRemark"
                class="text-[10px] text-gray-400 truncate block leading-snug mt-0.5"
              >{{ group.serverRemark }}</span>
            </span>
            <span class="shrink-0 text-[10px] text-gray-400 font-medium">
              ({{ group.tools.length }})
            </span>
          </button>
          <button
            v-if="selectableToolsInGroup(group).length"
            type="button"
            class="shrink-0 text-[10px] font-semibold text-primary/90 hover:text-primary px-1.5 py-0.5 rounded hover:bg-primary/5"
            @click.stop="toggleGroupSelectAll(group)"
          >
            {{ groupSelectionState(group) === 'all' ? '取消全选' : '全选' }}
          </button>
        </div>

        <!-- Group tools -->
        <div v-show="!collapsedGroups.has(group.serverName)" class="py-0.5">
          <button
            v-for="tool in group.tools"
            :key="tool.id"
            type="button"
            class="w-full flex items-start gap-2.5 px-2.5 py-2 text-left transition-colors"
            :class="isAttached(tool)
              ? 'opacity-55 cursor-default'
              : isSelected(tool)
                ? 'bg-primary/5 dark:bg-primary/10'
                : 'hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer'"
            :disabled="isAttached(tool)"
            @click.stop="toggleTool(tool)"
          >
            <span class="pt-0.5 shrink-0" @click.stop>
              <input
                type="checkbox"
                class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary/40 cursor-pointer disabled:cursor-not-allowed"
                :checked="isAttached(tool) || isSelected(tool)"
                :disabled="isAttached(tool)"
                @change.stop="toggleTool(tool)"
              />
            </span>
            <div
              class="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold flex-shrink-0 mt-0.5"
              :class="tool.scope === 'personal' ? 'bg-emerald-500 text-white' : 'bg-indigo-500 text-white'"
            >
              {{ toolInitial(tool, group.serverName) }}
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1.5 min-w-0">
                <span
                  class="text-xs font-semibold text-gray-900 dark:text-gray-100 truncate"
                  :title="tool.name"
                >
                  {{ mcpToolDisplayName(tool.name || tool.id, group.serverName) }}
                </span>
                <span
                  v-if="tool.scope === 'personal'"
                  class="shrink-0 px-1 py-px text-[8px] font-semibold rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
                >个人</span>
                <span
                  v-if="isAttached(tool)"
                  class="shrink-0 text-[9px] text-gray-400 font-medium"
                >已挂载</span>
              </div>
              <p class="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5 line-clamp-2 leading-snug">
                {{ tool.description || '暂无描述' }}
              </p>
            </div>
          </button>
        </div>
      </div>
    </div>

    <div class="shrink-0 border-t border-gray-100 dark:border-gray-700/80 px-3 py-2 space-y-1.5 bg-white dark:bg-gray-800">
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="flex-1 py-2 rounded-lg text-sm font-semibold transition-colors"
          :class="selectedCount
            ? 'bg-primary text-white hover:bg-primary/90'
            : 'bg-gray-100 dark:bg-gray-700 text-gray-400 cursor-not-allowed'"
          :disabled="!selectedCount"
          @click.stop="confirmMount"
        >
          挂载已选{{ selectedCount ? `（${selectedCount}）` : '' }}
        </button>
        <button
          v-if="selectedCount"
          type="button"
          class="shrink-0 px-2.5 py-2 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-200"
          @click.stop="clearSelection"
        >
          清空
        </button>
      </div>
      <a
        v-if="!fullWidth"
        href="/dashboard/personal?tab=mcp"
        target="_blank"
        rel="noopener noreferrer"
        class="block w-full text-center text-[11px] font-medium text-primary/80 hover:text-primary py-0.5"
        @click.stop
      >
        个人中心 · 我的 MCP
      </a>
    </div>
  </div>
</template>
