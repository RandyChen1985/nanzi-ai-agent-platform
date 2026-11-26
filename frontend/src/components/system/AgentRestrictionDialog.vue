<template>
  <Teleport to="body">
    <div
      v-if="modelValue"
      class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9995] px-2 py-4"
      @click.self="close"
    >
      <div
        class="bg-white rounded-2xl shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden"
      >
        <!-- 头部：作用域必须写在标题旁，否则管理员会以为这里改的是全局禁用 -->
        <div class="px-4 sm:px-5 py-3 border-b border-gray-100 flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 class="text-sm sm:text-base font-bold text-gray-900 truncate">
              功能限定<span v-if="agentLabel"> · {{ agentLabel }}</span>
            </h3>
          </div>
          <button
            type="button"
            class="shrink-0 p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            title="关闭"
            @click="close"
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- 作用域与只读语义：铺满整行、红色警示样式（与「禁用资源」页的提示同一套语气），
             并可折叠——它每条都要读一遍才能确认"改的是哪个范围"，但不能一直占着屏幕高度。
             挤在标题旁当灰字时管理员会略过，从而误以为这里改的是全局禁用。 -->
        <div class="shrink-0 px-4 sm:px-5 pt-3">
          <div class="rounded-lg border border-red-200 bg-red-50 overflow-hidden">
            <button
              type="button"
              class="w-full flex items-center gap-1.5 px-2.5 py-2 text-left"
              :aria-expanded="!hintCollapsed"
              aria-controls="agent-restriction-hint"
              @click="hintCollapsed = !hintCollapsed"
            >
              <svg
                class="w-3.5 h-3.5 shrink-0 text-red-400 transition-transform"
                :class="{ 'rotate-90': !hintCollapsed }"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
              </svg>
              <span class="text-[11px] sm:text-xs font-bold text-red-700 shrink-0">作用范围说明</span>
              <span class="text-[10px] sm:text-xs text-red-600/80 truncate">
                只对「{{ scopeNoun }} × 该智能体」生效
              </span>
            </button>
            <div
              v-show="!hintCollapsed"
              id="agent-restriction-hint"
              class="px-2.5 pb-2 space-y-1 border-t border-red-200/70 pt-1.5"
            >
              <p class="flex gap-1.5 text-[10px] sm:text-xs text-red-800 leading-relaxed">
                <span class="shrink-0 font-bold">•</span>
                <span>只对「{{ scopeNoun }} × 该智能体」生效：勾选的资源在该智能体里不可用，在其它智能体里仍然可用。</span>
              </p>
              <p class="flex gap-1.5 text-[10px] sm:text-xs text-red-800 leading-relaxed">
                <span class="shrink-0 font-bold">•</span>
                <span>已全局禁用的项在此只读，需到「禁用资源」里调整。</span>
              </p>
            </div>
          </div>
        </div>

        <!-- 工具栏：搜索 + 视图说明 -->
        <div class="px-4 sm:px-5 py-2 border-b border-gray-100 flex items-center justify-between gap-3">
          <p class="text-[10px] text-gray-400 truncate">
            条目来自该智能体实际绑定的资源<template v-if="sourceVersionLabel">（{{ sourceVersionLabel }}）</template>；保存为覆盖式，取消勾选即解除限制。已全局禁用的项在列表里置灰，不计入页签的可选数量。
          </p>
          <input
            v-model="searchQuery"
            type="search"
            placeholder="搜索工具或技能名…"
            class="shrink-0 text-[11px] border border-gray-200 rounded-lg px-2 py-1 w-[170px] focus:outline-none focus:border-primary"
          />
        </div>

        <!-- 内容区：按「工具 / MCP / SKILLS」分页签，避免一个智能体绑几十项时一长条滚动 -->
        <div class="flex-1 overflow-y-auto px-4 sm:px-5 py-3 bg-gray-50">
          <p v-if="loading" class="text-[11px] text-gray-400 py-6 text-center">
            正在加载该智能体可限制的资源…
          </p>
          <div v-else-if="loadError" class="py-6 text-center space-y-2">
            <p class="text-[11px] text-red-500 leading-relaxed">{{ loadError }}</p>
            <button
              type="button"
              class="text-[11px] font-bold text-primary hover:underline"
              @click="load"
            >
              重新加载
            </button>
          </div>
          <p v-else-if="!groups.length" class="text-[11px] text-gray-400 py-6 text-center">
            该智能体暂无可限制的工具或技能。
          </p>

          <template v-else>
            <!-- 页签固定三个：空页签用空态说明回答"为什么这里没有内容" -->
            <div class="flex items-stretch border-b border-gray-200 bg-gray-50/80 rounded-t-xl">
              <div
                class="flex flex-1 min-w-0"
                role="tablist"
                aria-label="资源类型"
              >
              <button
                v-for="tab in tabs"
                :key="tab.key"
                type="button"
                role="tab"
                :id="`agent-restriction-tab-${tab.key}`"
                :aria-selected="activeTab === tab.key"
                :aria-controls="`agent-restriction-panel-${tab.key}`"
                class="relative flex-1 min-w-0 px-2 py-2.5 text-xs font-bold transition-colors border-b-2 -mb-px"
                :class="activeTab === tab.key
                  ? 'border-primary text-primary bg-white'
                  : 'border-transparent text-gray-500 hover:text-gray-700'"
                @click="activeTab = tab.key"
              >
                <span class="block truncate text-center">
                  {{ tab.label }} ({{ tabState(tab).selected }}/{{ tabState(tab).selectable }})
                </span>
              </button>
              </div>
              <!-- 一个智能体可能绑几十项、十几个分组，逐个点开很费事。
                   只作用于**当前页签**：跨页签一起展开会把另一个页签也铺开，找起来更乱。 -->
              <button
                v-if="activeTabGroups.length > 1"
                type="button"
                class="shrink-0 self-center mr-2 px-2 py-1 rounded-md text-[10px] font-bold text-primary hover:bg-primary/5 whitespace-nowrap"
                :title="allGroupsCollapsedInActiveTab
                  ? '展开当前页签的全部分组'
                  : '折叠当前页签的全部分组'"
                @click="toggleAllGroupsInActiveTab"
              >
                {{ allGroupsCollapsedInActiveTab ? '展开全部' : '折叠全部' }}
              </button>
            </div>

            <div
              v-for="tab in tabs"
              v-show="activeTab === tab.key"
              :key="`panel-${tab.key}`"
              :id="`agent-restriction-panel-${tab.key}`"
              role="tabpanel"
              :aria-labelledby="`agent-restriction-tab-${tab.key}`"
              class="space-y-3 pt-3"
            >
              <p
                v-if="!tab.groups.length"
                class="text-[11px] text-gray-400 py-6 text-center"
              >{{ tabEmptyHint(tab.key) }}</p>
              <p
                v-else-if="!visibleGroupsOf(tab).length"
                class="text-[11px] text-gray-400 py-6 text-center"
              >没有匹配的资源，试试其它关键词。</p>

              <!-- 分区：分组与条目全部来自接口，MCP 组数量不定，不硬编码 -->
              <div
                v-for="group in visibleGroupsOf(tab)"
                :key="group.key"
                class="rounded-xl border bg-white"
                :class="groupState(group).selected > 0 ? 'border-red-200' : 'border-gray-200'"
              >
                <div
                  class="flex items-center justify-between gap-2 px-3 py-2 rounded-t-xl"
                  :class="groupState(group).selected > 0 ? 'bg-red-50' : 'bg-gray-50'"
                >
                  <button
                    type="button"
                    class="flex items-center gap-1.5 min-w-0 flex-1 text-left"
                    :disabled="isSearching"
                    :title="isSearching ? '搜索状态下分组自动展开' : ''"
                    @click="toggleCollapse(group.key)"
                  >
                    <svg
                      class="w-3.5 h-3.5 transition-transform shrink-0"
                      :class="[groupState(group).selected > 0 ? 'text-red-400' : 'text-gray-400', { 'rotate-90': isExpanded(group.key) }]"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                    <span
                      class="text-[11px] font-bold truncate"
                      :class="groupState(group).selected > 0 ? 'text-red-700' : 'text-gray-700'"
                    >{{ group.label }}</span>
                    <span
                      class="text-[10px] shrink-0"
                      :class="groupState(group).selected > 0 ? 'text-red-500' : 'text-gray-400'"
                    >({{ groupState(group).selected }}/{{ groupState(group).selectable }})</span>
                  </button>
                  <button
                    type="button"
                    class="shrink-0 text-[10px] font-bold disabled:opacity-40 disabled:cursor-not-allowed"
                    :class="groupState(group).selected > 0 ? 'text-red-600 hover:text-red-800' : 'text-primary hover:text-primary-dark'"
                    :disabled="!groupState(group).selectable"
                    @click.stop="toggleGroup(group, !groupState(group).allSelected)"
                  >
                    {{ groupState(group).allSelected ? '取消全选' : '全选' }}
                  </button>
                </div>

                <div v-show="isExpanded(group.key)" class="grid grid-cols-1 sm:grid-cols-2 gap-2 p-2">
                  <label
                    v-for="item in group.items"
                    :key="item.id"
                    class="flex items-start gap-2 p-2 rounded-lg border bg-white transition-colors"
                    :class="item.forbidden_globally || item.not_restrictable ? 'opacity-60 cursor-not-allowed border-gray-200' : 'hover:bg-gray-50 cursor-pointer border-gray-200'"
                    :title="item.not_restrictable
                      ? (item.not_restrictable_reason || '该项不支持限定')
                      : (item.forbidden_globally ? '该项已被全局禁用，无法在此放开，也不能重复勾选' : item.id)"
                  >
                    <input
                      type="checkbox"
                      class="mt-0.5 h-4 w-4 rounded text-red-600 focus:ring-red-500 disabled:cursor-not-allowed"
                      :checked="isItemSelected(group, item)"
                      :disabled="item.forbidden_globally || item.not_restrictable"
                      @change="toggleItem(group, item, ($event.target as HTMLInputElement).checked)"
                    />
                    <div class="min-w-0 flex-1">
                      <p class="text-xs font-bold text-gray-900 truncate">{{ item.label }}</p>
                      <!-- 有中文描述就显示描述（与智能体编辑页一致）；没有才退回等宽 id -->
                      <p
                        class="text-[9px] text-gray-400 truncate"
                        :class="item.description ? '' : 'font-mono'"
                        :title="item.description || item.id"
                      >{{ item.description || item.id }}</p>
                    </div>
                    <span
                      v-if="item.not_restrictable"
                      class="shrink-0 text-[9px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700 font-semibold leading-none"
                    >不支持限定</span>
                    <span
                      v-else-if="item.forbidden_globally"
                      class="shrink-0 text-[9px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 font-semibold leading-none"
                    >已全局禁用</span>
                  </label>
                </div>
              </div>
            </div>
          </template>

          <!-- 解绑/改名的历史项：必须可见且可一键清理，否则用户永远删不掉 -->
          <div
            v-if="unboundIds.length"
            class="rounded-xl border border-amber-200 bg-amber-50/50 p-3"
          >
            <h4 class="text-xs font-bold text-amber-700 mb-1 flex items-center gap-1.5">
              <span class="w-1.5 h-1.5 bg-amber-500 rounded-full"></span>
              该智能体已不再绑定（{{ unboundIds.length }}）
            </h4>
            <p class="text-[10px] text-amber-600 mb-2 leading-relaxed">
              这些条目已从该智能体的绑定中移除（工具改名或解绑、技能下架），限制已无意义。
            </p>
            <div class="flex flex-wrap items-center gap-2">
              <span
                v-for="id in unboundIds"
                :key="id"
                class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white border border-amber-200 text-gray-600"
              >{{ id }}</span>
              <button
                type="button"
                class="text-[10px] font-bold text-amber-700 hover:underline"
                @click="clearUnbound"
              >
                一键清理
              </button>
            </div>
          </div>
        </div>

        <!-- 底部：计数 + 保存 -->
        <div class="px-4 sm:px-5 py-3 border-t border-gray-100 flex items-center justify-between gap-3">
          <div class="min-w-0">
            <p class="text-[11px] text-gray-600">
              已选 <b class="text-red-600">{{ selectedCount }}</b> 项
            </p>
            <p v-if="saveError" class="text-[10px] text-red-500 truncate">{{ saveError }}</p>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <button
              type="button"
              class="px-3 py-1.5 rounded-lg text-xs font-bold text-gray-600 hover:bg-gray-100"
              @click="close"
            >
              取消
            </button>
            <button
              type="button"
              class="px-4 py-1.5 rounded-lg text-xs font-bold text-white bg-red-600 hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
              :disabled="saving || !loaded"
              @click="save"
            >
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import axios from '../../utils/axios'
import { extractApiErrorDetail } from '../../utils/apiErrorDetail'
import {
  agentRestrictableCatalogCacheKey,
  agentRestrictionCacheKey,
  agentRestrictionPath,
  agentRestrictableResourcesPath,
  applyGroupRestrictionSelection,
  buildAgentRestrictionPayload,
  countAgentRestrictionBadge,
  excludeSelectedIds,
  filterRestrictableGroups,
  findUnboundRestrictionIds,
  groupRestrictionTabs,
  groupSelectionState,
  tabEmptyHint,
  tabSelectionState,
  normalizeAgentRestriction,
  regroupToolGroups,
  resourceKindOfGroup,
  sanitizeAgentRestrictionLists,
  type AgentRestrictionLists,
  type AgentRestrictionScopeKind,
  type AgentRestrictionSourceVersion,
  type AgentRestrictionTab,
  type AgentRestrictionTabKey,
  type RestrictableResourceGroup,
  type RestrictableResourceItem,
  type RestrictableGroupSelectionState,
} from '../../utils/agentRestrictions'
import {
  loadAgentRestrictableCatalog,
  loadAgentRestriction,
  writeAgentRestriction,
} from '../../utils/agentRestrictionCache'

/**
 * 「功能限定」弹框：配置 **用户/角色 × 单个智能体** 的资源排除。
 *
 * 与「禁用资源」tab 的区别（必须在 UI 上说清，否则会被当成全局拉黑）：
 *   - 这里的限制只在该智能体里生效，不跟版本走；
 *   - 与全局禁用是**并集**：已全局禁用的项只读置灰（排除只增不减）；
 *   - 只含工具 / MCP / 技能，不含命令（命令禁用仍只由全局禁用资源管理）。
 *
 * 分组与条目全部来自接口
 * `GET /api/portal/management/agents/{id}/restrictable-resources`（按用户或角色视角取数，
 * 每项的 forbidden_globally 是相对该用户 / 角色算的）：MCP 只作为分组标题、存的是具体
 * 工具名，绑什么才有什么，分组数量不固定，不硬编码。
 */
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    /** 作用域：用户直配 or 角色 */
    kind: AgentRestrictionScopeKind
    /** 用户 id 或角色 id */
    scopeId: number | string | null
    agentId: string
    agentLabel?: string
  }>(),
  { agentLabel: '' },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'saved', lists: AgentRestrictionLists): void
}>()

const scopeNoun = computed(() => (props.kind === 'role' ? '该角色' : '该用户'))

const groups = ref<RestrictableResourceGroup[]>([])
/**
 * 候选清单来自哪个版本。它必须显示出来：限制是按 agent 维度配的，而候选必然取自某个
 * 具体版本（优先已发布）。管理员在智能体编辑页改的是草稿版本，两个页面的数量天然可能不同，
 * 不写清来源就会被当成 bug。
 */
const sourceVersion = ref<AgentRestrictionSourceVersion | null>(null)
const selectedTools = ref<string[]>([])
const selectedSkills = ref<string[]>([])
const searchQuery = ref('')
const loading = ref(false)
/** 允许保存的前提：候选与已有配置都加载成功（否则空缓冲写回去等于清空真实配置） */
const loaded = ref(false)
const loadError = ref('')
const saveError = ref('')
const saving = ref(false)
const collapsedKeys = ref<Set<string>>(new Set())

const cacheKey = computed(() =>
  agentRestrictionCacheKey(props.kind, props.scopeId, props.agentId),
)

/**
 * 候选清单缓存键同样带视角：`forbidden_globally` 是相对该用户 / 角色算的，
 * 只按 agent_id 缓存会把上一个管理员的置灰结果带给下一个。
 */
const catalogCacheKey = computed(() =>
  agentRestrictableCatalogCacheKey(props.kind, props.scopeId, props.agentId),
)

const isSearching = computed(() => searchQuery.value.trim().length > 0)
const tabs = computed(() => groupRestrictionTabs(groups.value))
const activeTab = ref<AgentRestrictionTabKey>('tools')
const tabState = (tab: AgentRestrictionTab) => tabSelectionState(tab, selectedIds.value)

/** 「已发布版本 v3」/「草稿版本 v4（尚未发布）」；未知时返回空串，不编造。 */
const sourceVersionLabel = computed(() => {
  const version = sourceVersion.value
  if (!version) return ''
  const numberPart = version.version_number ? `v${version.version_number}` : '未编号版本'
  return version.is_published ? `已发布版本 ${numberPart}` : `${numberPart}（尚未发布）`
})

/**
 * 当前页签内、按搜索词过滤后的分组。
 *
 * 搜索只作用于当前页签：跨页签"跳着找"会让勾选位置随时变化，反而更难用；
 * 页签上的计数已经能说明哪一类有内容。
 */
const visibleGroupsOf = (tab: AgentRestrictionTab) => {
  // 工具页签按能力域/业务分组重新聚合，与智能体编辑页「工具能力」显示同一套结构
  // （接口只给"内置 / 自定义 API"两桶，直接渲染会和智能体页对不上）。
  const groups = tab.key === 'tools' ? regroupToolGroups(tab.groups) : tab.groups
  return filterRestrictableGroups(groups, searchQuery.value)
}

/** 两组选中项合并：页签与"已选 N 项"按整个智能体看。 */
const selectedIds = computed(() => [...selectedTools.value, ...selectedSkills.value])

/**
 * 取某个分组自己的选择集。
 *
 * 分组独立取值是必须的：`applyGroupRestrictionSelection` 返回的是**全局**集合，
 * 若把它的结果整体写回某一组，另一组的 id 会被搬进来（工具与技能互相污染）。
 */
const selectionForGroup = (group: RestrictableResourceGroup) =>
  resourceKindOfGroup(group) === 'skills' ? selectedSkills.value : selectedTools.value

const writeSelection = (group: RestrictableResourceGroup, next: string[]) => {
  if (resourceKindOfGroup(group) === 'skills') selectedSkills.value = next
  else selectedTools.value = next
}

const groupState = (group: RestrictableResourceGroup): RestrictableGroupSelectionState =>
  groupSelectionState(selectionForGroup(group), group.items)

const isExpanded = (key: string) => isSearching.value || !collapsedKeys.value.has(key)

const toggleCollapse = (key: string) => {
  const next = new Set(collapsedKeys.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedKeys.value = next
}

/** 顶部说明的折叠态：默认展开（首次使用者需要读一遍），同一会话内记住选择。 */
const hintCollapsed = ref(false)

/**
 * 当前页签里**可见**（已按搜索过滤）的分组。
 *
 * 不用 `tabs.find(...)` 直接在模板里取：页签找不到时会把 undefined 传给
 * visibleGroupsOf，那里立刻读 tab.key 就会抛错、整块内容白屏。
 */
const activeTabGroups = computed(() => {
  const tab = tabs.value.find((candidate) => candidate.key === activeTab.value)
  return tab ? visibleGroupsOf(tab) : []
})

/** 当前页签的所有分组是否都已折叠——决定按钮显示「展开全部」还是「折叠全部」。 */
const allGroupsCollapsedInActiveTab = computed(() => {
  const keys = activeTabGroups.value.map((group) => group.key)
  return keys.length > 0 && keys.every((key) => collapsedKeys.value.has(key))
})

/**
 * 一键折叠/展开当前页签的全部分组。
 *
 * 只作用于当前页签：把其它页签一起展开会让列表更长、更难定位，而每个页签的计数已经
 * 说明了哪一类有内容。
 */
const toggleAllGroupsInActiveTab = () => {
  const next = new Set(collapsedKeys.value)
  const collapse = !allGroupsCollapsedInActiveTab.value
  for (const key of activeTabGroups.value.map((group) => group.key)) {
    if (collapse) next.add(key)
    else next.delete(key)
  }
  collapsedKeys.value = next
}

/** 全选 / 取消全选走共享纯函数：勾选只并入可勾选项，全局禁用项永远写不进来。 */
const toggleGroup = (group: RestrictableResourceGroup, checked: boolean) => {
  writeSelection(
    group,
    applyGroupRestrictionSelection(selectionForGroup(group), group.items, checked),
  )
}

const toggleItem = (
  group: RestrictableResourceGroup,
  item: RestrictableResourceItem,
  checked: boolean,
) => {
  writeSelection(
    group,
    applyGroupRestrictionSelection(selectionForGroup(group), [item], checked),
  )
}

const isItemSelected = (group: RestrictableResourceGroup, item: RestrictableResourceItem) =>
  selectionForGroup(group).includes(item.id)

const unboundIds = computed(() => findUnboundRestrictionIds(selectedIds.value, groups.value))

const clearUnbound = () => {
  const stale = unboundIds.value
  selectedTools.value = excludeSelectedIds(selectedTools.value, stale)
  selectedSkills.value = excludeSelectedIds(selectedSkills.value, stale)
}

// 走 `countAgentRestrictionBadge`：它的入参形状正是 { forbidden_tools, forbidden_skills }，
// 而且与卡片角标**同一口径**（底部"已选 N 项"必须和角标对得上）。
//
// 不能直接把 {forbidden_tools, forbidden_skills} 交给 countRestrictionEntries：它收的是**数组**
// （内部 `normalizeSelectedIds` 对非数组返回 []），传对象会恒为 0。
const selectedCount = computed(() =>
  countAgentRestrictionBadge(
    { forbidden_tools: selectedTools.value, forbidden_skills: selectedSkills.value },
    groups.value,
  ),
)

/** 关闭 / 打开 / 换智能体都要清空：否则会看到上一个智能体的勾选。 */
const resetBuffers = () => {
  groups.value = []
  selectedTools.value = []
  selectedSkills.value = []
  searchQuery.value = ''
  collapsedKeys.value = new Set()
  loading.value = false
  loaded.value = false
  loadError.value = ''
  saveError.value = ''
}

const load = async () => {
  const agentId = String(props.agentId || '').trim()
  if (!agentId) return
  loading.value = true
  loadError.value = ''
  try {
    const [catalog, lists] = await Promise.all([
      loadAgentRestrictableCatalog(catalogCacheKey.value, async () => {
        const response = await axios.get(
          agentRestrictableResourcesPath(props.kind, props.scopeId, agentId),
        )
        return response.data
      }),
      loadAgentRestriction(cacheKey.value, async () => {
        const response = await axios.get(
          agentRestrictionPath(props.kind, props.scopeId, agentId),
        )
        return response.data
      }),
    ])
    groups.value = catalog.groups
    sourceVersion.value = catalog.sourceVersion
    // 回显清洗：全局已禁项不写成智能体级记录（界面上它们是灰的、不可勾选）
    const clean = sanitizeAgentRestrictionLists(lists, catalog.groups)
    selectedTools.value = clean.forbidden_tools
    selectedSkills.value = clean.forbidden_skills
    loaded.value = true
  } catch (error: any) {
    loadError.value = `加载失败：${extractApiErrorDetail(error?.response?.data, '操作失败')}`
  } finally {
    loading.value = false
  }
}

const close = () => {
  if (saving.value) return
  emit('update:modelValue', false)
}

const save = async () => {
  if (saving.value || !loaded.value) return
  saving.value = true
  saveError.value = ''
  try {
    const payload = buildAgentRestrictionPayload(
      { forbidden_tools: selectedTools.value, forbidden_skills: selectedSkills.value },
      groups.value,
    )
    const response = await axios.put(
      agentRestrictionPath(props.kind, props.scopeId, props.agentId),
      payload,
    )
    // 后端返回同形状（GET/PUT 契约一致）；异常情况退回本地 payload，避免角标丢数
    const written = normalizeAgentRestriction(response?.data ?? payload)
    writeAgentRestriction(cacheKey.value, written)
    emit('saved', written)
    emit('update:modelValue', false)
  } catch (error: any) {
    saveError.value = `保存失败：${extractApiErrorDetail(error?.response?.data, '操作失败')}`
  } finally {
    saving.value = false
  }
}

// 每次打开都重新取数（缓存会做去重与短 TTL），关闭时清空缓冲杜绝跨智能体残留
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      hintCollapsed.value = false
      resetBuffers()
      void load()
    } else {
      resetBuffers()
    }
  },
)
</script>
