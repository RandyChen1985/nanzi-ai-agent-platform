<template>
  <div class="space-y-3">
    <!-- 一键折叠/展开全部分组：候选最多可到上百条、子组十几个，逐个点开很费事。
         搜索期间分组是强制展开的（只读覆盖，见 isExpanded），此时按钮没有意义故隐藏。 -->
    <div v-if="!isSearching && collapsibleKeys.length > 1" class="flex items-center justify-end">
      <button
        type="button"
        class="px-2 py-1 rounded-md text-[10px] font-bold text-primary hover:bg-primary/5 whitespace-nowrap"
        :title="allGroupsCollapsed ? '展开全部分组' : '折叠全部分组'"
        @click="toggleAllGroups"
      >
        {{ allGroupsCollapsed ? '展开全部' : '折叠全部' }}
      </button>
    </div>

    <!-- 工具模式的两个大组；skills 模式走下面的 v-else，两者互斥（不靠空数据兜住） -->
    <template v-if="!isSkillsMode">
    <!-- 大组 1：内置高危工具（可折叠，子组按 category 分组） -->
    <div v-show="showBuiltinSection" class="border rounded-xl p-3" :class="toneOf(builtinVisibleCandidates, 'gray').bigCard">
      <div class="flex items-center justify-between gap-3 mb-2 select-none">
        <button
          type="button"
          @click="toggleCollapse(GROUP_BUILTIN_KEY)"
          :disabled="isSearching"
          :title="isSearching ? '搜索状态下分组自动展开，清空搜索后可折叠' : ''"
          :class="isSearching ? 'cursor-default' : ''"
          class="flex items-center gap-1.5 min-w-0 flex-1 text-left"
        >
          <svg class="w-3.5 h-3.5 transition-transform shrink-0" :class="[toneOf(builtinVisibleCandidates, 'gray').arrow, { 'rotate-90': isExpanded(GROUP_BUILTIN_KEY) }]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
          <span class="w-1.5 h-1.5 rounded-full" :class="toneOf(builtinVisibleCandidates, 'gray').dot"></span>
          <span class="text-xs font-bold truncate" :class="toneOf(builtinVisibleCandidates, 'gray').title">选择要禁用的工具</span>
          <span class="text-[10px] font-normal shrink-0" :class="toneOf(builtinVisibleCandidates, 'gray').count">({{ selectedCountOf(builtinVisibleCandidates) }}/{{ builtinVisibleCandidates.length }})</span>
        </button>
        <div class="flex items-center gap-2 shrink-0">
          <button
            v-if="!disabled"
            type="button"
            :disabled="!builtinVisibleCandidates.length"
            @click.stop="toggleSelection(builtinVisibleCandidates, !isAllSelected(builtinVisibleCandidates))"
            class="text-[10px] font-bold disabled:opacity-40 disabled:cursor-not-allowed" :class="toneOf(builtinVisibleCandidates, 'gray').action"
          >
            {{ isAllSelected(builtinVisibleCandidates) ? '取消全选' : '全选' }}
          </button>
          <input
            v-model="searchQuery"
            type="search"
            :placeholder="searchPlaceholder"
            class="text-[10px] border border-gray-200 rounded px-2 py-0.5 w-[140px] focus:outline-none focus:border-primary"
          />
        </div>
      </div>
      <p v-if="loading" class="text-[10px] text-gray-400">{{ loadingText }}</p>
      <p v-else-if="loadError" class="text-[10px] text-red-500">{{ loadError }}</p>
      <div v-else v-show="isExpanded(GROUP_BUILTIN_KEY)" class="space-y-2">
        <div
          v-for="sub in visibleBuiltinGroups"
          :key="sub.key"
          class="rounded-lg border overflow-hidden" :class="toneOf(sub.candidates, 'gray').subCard"
        >
          <div class="flex items-center justify-between py-1.5 px-2.5" :class="toneOf(sub.candidates, 'gray').subHead">
            <button
              type="button"
              @click="toggleCollapse(sub.key)"
              :disabled="isSearching"
              :title="isSearching ? '搜索状态下分组自动展开，清空搜索后可折叠' : ''"
              :class="isSearching ? 'cursor-default' : ''"
              class="flex items-center gap-2 min-w-0 flex-1 text-left"
            >
              <svg class="w-3.5 h-3.5 transition-transform shrink-0" :class="[toneOf(sub.candidates, 'gray').arrow, { 'rotate-90': isExpanded(sub.key) }]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
              </svg>
              <span class="text-[11px] font-bold truncate" :class="toneOf(sub.candidates, 'gray').title">{{ sub.label }}</span>
              <span class="text-[10px] shrink-0" :class="toneOf(sub.candidates, 'gray').count">({{ selectedCountOf(sub.candidates) }}/{{ sub.candidates.length }})</span>
            </button>
            <button
              v-if="!disabled"
              type="button"
              @click.stop="toggleSelection(sub.candidates, !isAllSelected(sub.candidates))"
              class="text-[10px] font-bold ml-2 shrink-0" :class="toneOf(sub.candidates, 'gray').action"
            >
              {{ isAllSelected(sub.candidates) ? '取消全选' : '全选' }}
            </button>
          </div>
          <div v-show="isExpanded(sub.key)" class="grid grid-cols-1 sm:grid-cols-2 gap-2 p-2">
            <label
              v-for="candidate in sub.candidates"
              :key="candidate.id"
              class="flex items-start p-2 rounded border bg-white transition-colors"
              :class="disabled || isCandidateLocked(candidate) ? 'opacity-60 cursor-not-allowed' : 'hover:bg-gray-50 cursor-pointer'"
            >
              <input
                type="checkbox"
                :checked="isCandidateSelected(candidate)"
                :disabled="disabled || isCandidateLocked(candidate)"
                class="mt-1 h-4 w-4 rounded text-red-600 focus:ring-red-500"
                @change="toggleCandidate(candidate, ($event.target as HTMLInputElement).checked)"
              />
              <div class="ml-2 min-w-0">
                <p class="text-xs sm:text-sm font-bold text-gray-900 truncate">{{ candidate.label }}</p>
                <p v-if="isCandidateLocked(candidate)" class="text-[9px] text-amber-600 truncate">已由所属角色禁用</p>
                <p class="text-[9px] text-gray-400 truncate">{{ candidate.description }}</p>
                <p v-if="(candidate.aliases?.length || 0) > 1" class="text-[9px] text-red-400 truncate">
                  同时拦截：{{ (candidate.aliases || []).filter((alias) => alias !== candidate.id).join('、') }}
                </p>
              </div>
            </label>
          </div>
        </div>
      </div>
    </div>

    <!-- 大组 2：自定义 API 与 MCP 扩展工具（可折叠；无内滚动，长度交给子组折叠） -->
    <div v-show="showDynamicSection" class="border rounded-xl p-3" :class="toneOf(dynamicVisibleCandidates, 'indigo').bigCard">
      <div class="flex items-center justify-between gap-3 mb-2 select-none">
        <button
          type="button"
          @click="toggleCollapse(GROUP_DYNAMIC_KEY)"
          :disabled="isSearching"
          :title="isSearching ? '搜索状态下分组自动展开，清空搜索后可折叠' : ''"
          :class="isSearching ? 'cursor-default' : ''"
          class="flex items-center gap-1.5 min-w-0 flex-1 text-left"
        >
          <svg class="w-3.5 h-3.5 transition-transform shrink-0" :class="[toneOf(dynamicVisibleCandidates, 'indigo').arrow, { 'rotate-90': isExpanded(GROUP_DYNAMIC_KEY) }]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
          <span class="w-1.5 h-1.5 rounded-full" :class="toneOf(dynamicVisibleCandidates, 'indigo').dot"></span>
          <span class="text-xs font-bold truncate" :class="toneOf(dynamicVisibleCandidates, 'indigo').title">{{ dynamicSectionTitle }}</span>
          <span class="text-[10px] font-normal shrink-0" :class="toneOf(dynamicVisibleCandidates, 'indigo').count">({{ selectedCountOf(dynamicVisibleCandidates) }}/{{ dynamicVisibleCandidates.length }})</span>
        </button>
        <button
          v-if="!disabled"
          type="button"
          :disabled="!dynamicVisibleCandidates.length"
          @click.stop="toggleSelection(dynamicVisibleCandidates, !isAllSelected(dynamicVisibleCandidates))"
          class="text-[10px] font-bold shrink-0 disabled:opacity-40 disabled:cursor-not-allowed" :class="toneOf(dynamicVisibleCandidates, 'indigo').action"
        >
          {{ isAllSelected(dynamicVisibleCandidates) ? '取消全选' : '全选' }}
        </button>
      </div>
      <div v-show="isExpanded(GROUP_DYNAMIC_KEY)" class="space-y-2">
        <div
          v-for="sub in visibleDynamicGroups"
          :key="sub.key"
          class="rounded-lg border overflow-hidden" :class="toneOf(sub.candidates, 'indigo').subCard"
        >
          <div class="flex items-center justify-between py-1.5 px-2.5" :class="toneOf(sub.candidates, 'indigo').subHead">
            <button
              type="button"
              @click="toggleCollapse(sub.key)"
              :disabled="isSearching"
              :title="isSearching ? '搜索状态下分组自动展开，清空搜索后可折叠' : ''"
              :class="isSearching ? 'cursor-default' : ''"
              class="flex items-center gap-2 min-w-0 flex-1 text-left"
            >
              <svg class="w-3.5 h-3.5 transition-transform shrink-0" :class="[toneOf(sub.candidates, 'indigo').arrow, { 'rotate-90': isExpanded(sub.key) }]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
              </svg>
              <span class="text-[11px] font-bold truncate" :class="toneOf(sub.candidates, 'indigo').title">{{ sub.label }}</span>
              <span class="text-[10px] shrink-0" :class="toneOf(sub.candidates, 'indigo').count">({{ selectedCountOf(sub.candidates) }}/{{ sub.candidates.length }})</span>
            </button>
            <button
              v-if="!disabled"
              type="button"
              @click.stop="toggleSelection(sub.candidates, !isAllSelected(sub.candidates))"
              class="text-[10px] font-bold ml-2 shrink-0" :class="toneOf(sub.candidates, 'indigo').action"
            >
              {{ isAllSelected(sub.candidates) ? '取消全选' : '全选' }}
            </button>
          </div>
          <div v-show="isExpanded(sub.key)" class="grid grid-cols-1 sm:grid-cols-2 gap-1.5 p-2">
            <label
              v-for="candidate in sub.candidates"
              :key="candidate.id"
              class="flex items-center p-1.5 rounded border bg-white hover:bg-gray-50 transition-colors cursor-pointer text-xs"
              :class="disabled || isCandidateLocked(candidate) ? 'opacity-60 cursor-not-allowed' : ''"
            >
              <input
                type="checkbox"
                :checked="isCandidateSelected(candidate)"
                :disabled="disabled || isCandidateLocked(candidate)"
                class="h-3.5 w-3.5 rounded text-primary focus:ring-primary"
                @change="toggleCandidate(candidate, ($event.target as HTMLInputElement).checked)"
              />
              <div class="ml-1.5 min-w-0 flex-1">
                <p class="font-bold text-gray-900 truncate text-[11px] leading-tight">{{ candidate.label }}</p>
                <p v-if="isCandidateLocked(candidate)" class="text-[9px] text-amber-600 truncate">已由所属角色禁用</p>
                <p class="text-[9px] text-gray-400 truncate mt-0.5">{{ candidate.description }}</p>
              </div>
            </label>
          </div>
        </div>
      </div>
    </div>
    </template>

    <!-- 公共技能：整体只有一个大组，子组仍交给同一个 groupCandidates 聚合（不写死"只有一组"） -->
    <template v-else>
      <!-- 大组（skills 模式）：折叠 / 搜索 / (n/m) / 全选 / 变红全部复用同一套纯函数 -->
      <div v-show="showSkillSection" class="border rounded-xl p-3" :class="toneOf(skillVisibleCandidates, 'gray').bigCard">
        <div class="flex items-center justify-between gap-3 mb-2 select-none">
          <button
            type="button"
            @click="toggleCollapse(GROUP_SKILLS_KEY)"
            :disabled="isSearching"
            :title="isSearching ? '搜索状态下分组自动展开，清空搜索后可折叠' : ''"
            :class="isSearching ? 'cursor-default' : ''"
            class="flex items-center gap-1.5 min-w-0 flex-1 text-left"
          >
            <svg class="w-3.5 h-3.5 transition-transform shrink-0" :class="[toneOf(skillVisibleCandidates, 'gray').arrow, { 'rotate-90': isExpanded(GROUP_SKILLS_KEY) }]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
            </svg>
            <span class="w-1.5 h-1.5 rounded-full" :class="toneOf(skillVisibleCandidates, 'gray').dot"></span>
            <span class="text-xs font-bold truncate" :class="toneOf(skillVisibleCandidates, 'gray').title">禁用选定的公共技能</span>
            <span class="text-[10px] font-normal shrink-0" :class="toneOf(skillVisibleCandidates, 'gray').count">({{ selectedCountOf(skillVisibleCandidates) }}/{{ skillVisibleCandidates.length }})</span>
          </button>
          <div class="flex items-center gap-2 shrink-0">
            <button
              v-if="!disabled"
              type="button"
              :disabled="!skillVisibleCandidates.length"
              @click.stop="toggleSelection(skillVisibleCandidates, !isAllSelected(skillVisibleCandidates))"
              class="text-[10px] font-bold disabled:opacity-40 disabled:cursor-not-allowed" :class="toneOf(skillVisibleCandidates, 'gray').action"
            >
              {{ isAllSelected(skillVisibleCandidates) ? '取消全选' : '全选' }}
            </button>
            <input
              v-model="searchQuery"
              type="search"
              :placeholder="searchPlaceholder"
              class="text-[10px] border border-gray-200 rounded px-2 py-0.5 w-[140px] focus:outline-none focus:border-primary"
            />
          </div>
        </div>
        <p v-if="loading" class="text-[10px] text-gray-400">{{ loadingText }}</p>
        <p v-else-if="loadError" class="text-[10px] text-red-500">{{ loadError }}</p>
        <div v-else v-show="isExpanded(GROUP_SKILLS_KEY)" class="space-y-2">
          <div
            v-for="sub in visibleSkillGroups"
            :key="sub.key"
            class="rounded-lg border overflow-hidden" :class="toneOf(sub.candidates, 'gray').subCard"
          >
            <div class="flex items-center justify-between py-1.5 px-2.5" :class="toneOf(sub.candidates, 'gray').subHead">
              <button
                type="button"
                @click="toggleCollapse(sub.key)"
                :disabled="isSearching"
                :title="isSearching ? '搜索状态下分组自动展开，清空搜索后可折叠' : ''"
                :class="isSearching ? 'cursor-default' : ''"
                class="flex items-center gap-2 min-w-0 flex-1 text-left"
              >
                <svg class="w-3.5 h-3.5 transition-transform shrink-0" :class="[toneOf(sub.candidates, 'gray').arrow, { 'rotate-90': isExpanded(sub.key) }]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                </svg>
                <span class="text-[11px] font-bold truncate" :class="toneOf(sub.candidates, 'gray').title">{{ sub.label }}</span>
                <span class="text-[10px] shrink-0" :class="toneOf(sub.candidates, 'gray').count">({{ selectedCountOf(sub.candidates) }}/{{ sub.candidates.length }})</span>
              </button>
              <button
                v-if="!disabled"
                type="button"
                @click.stop="toggleSelection(sub.candidates, !isAllSelected(sub.candidates))"
                class="text-[10px] font-bold ml-2 shrink-0" :class="toneOf(sub.candidates, 'gray').action"
              >
                {{ isAllSelected(sub.candidates) ? '取消全选' : '全选' }}
              </button>
            </div>
            <div v-show="isExpanded(sub.key)" class="grid grid-cols-1 sm:grid-cols-2 gap-2 p-2">
              <label
                v-for="candidate in sub.candidates"
                :key="candidate.id"
                class="flex items-start p-2 rounded border bg-white transition-colors"
                :class="disabled || isCandidateLocked(candidate) ? 'opacity-60 cursor-not-allowed' : 'hover:bg-gray-50 cursor-pointer'"
              >
                <input
                  type="checkbox"
                  :checked="isCandidateSelected(candidate)"
                  :disabled="disabled || isCandidateLocked(candidate)"
                  class="mt-1 h-4 w-4 rounded text-red-600 focus:ring-red-500"
                  @change="toggleCandidate(candidate, ($event.target as HTMLInputElement).checked)"
                />
                <div class="ml-2 min-w-0">
                  <p class="text-xs sm:text-sm font-bold text-gray-900 truncate">{{ candidate.label }}</p>
                <p v-if="isCandidateLocked(candidate)" class="text-[9px] text-amber-600 truncate">已由所属角色禁用</p>
                  <p class="text-[9px] text-gray-400 truncate">{{ candidate.description }}</p>
                </div>
              </label>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 历史遗留：已禁用但已不在目录中的工具，必须仍可见、可解除（不被搜索过滤） -->
    <div v-if="orphanIds.length" class="border border-amber-200 rounded-xl p-3 bg-amber-50/40">
      <h4 class="text-xs font-bold text-amber-700 mb-1.5 flex items-center gap-1.5 select-none">
        <span class="w-1.5 h-1.5 bg-amber-500 rounded-full"></span>
        其他已禁用项（历史）
      </h4>
      <p class="text-[10px] text-amber-600 mb-2">{{ orphanHint }}</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
        <label
          v-for="orphanId in orphanIds"
          :key="orphanId"
          class="flex items-center p-1.5 rounded border bg-white hover:bg-gray-50 transition-colors cursor-pointer text-xs"
        >
          <input
            type="checkbox"
            checked
            :disabled="disabled"
            class="h-3.5 w-3.5 rounded text-amber-600 focus:ring-amber-500"
            @change="toggleOrphan(orphanId, ($event.target as HTMLInputElement).checked)"
          />
          <p class="ml-1.5 min-w-0 flex-1 font-mono text-[11px] text-gray-700 truncate">{{ orphanId }}</p>
        </label>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import axios from 'axios'
import {
  applyGroupSelection,
  filterSubGroups,
  findOrphanIds,
  groupCandidates,
  groupToneClasses,
  groupSelectedCount,
  type ForbiddenToolCandidate,
  type ForbiddenToolSubGroup,
} from '../../utils/forbiddenToolGroups'
import { filterCandidatesByToolTab } from '../../utils/forbiddenToolGroups'
import { isInheritedForbiddenCandidate } from '../../utils/permissionLists'
import { loadForbiddenCatalog } from '../../utils/forbiddenCatalogCache'

const props = withDefaults(
  defineProps<{
    modelValue: string[]
    disabled?: boolean
    resource?: 'tools' | 'skills'
    /**
     * 工具模式渲染哪一类候选，供外层按页签拆成两个实例（同一个 v-model，两页共享同一份选中集）：
     * - `all`（默认）：内置 + 自定义 API + MCP，即改造前的完整列表；
     * - `tools`：内置 + 自定义 API，去掉 MCP；
     * - `mcp`：只有 MCP，内置大组整块不渲染。
     * 默认值保证现有调用方行为完全不变。
     */
    toolScope?: 'all' | 'tools' | 'mcp'
    /**
     * 已由所属角色禁用的项（用户页才有）。这些项在直配区必须置灰：后端保存时会把
     * 它们 `continue` 掉，勾了只会「保存成功但勾选消失」。角色页不传。
     */
    inherited?: string[]
  }>(),
  {
    disabled: false,
    // 默认 tools：现有两个页面的工具实例不传 resource，行为必须与改造前完全一致
    resource: 'tools',
    toolScope: 'all',
    inherited: () => [],
  },
)

const emit = defineEmits<{ (e: 'update:modelValue', value: string[]): void }>()

/**
 * 同一个组件同时服务「禁用工具」与「禁用公共技能」：只切换候选来源与文案，
 * 交互逻辑（折叠 / 搜索 / 计数 / 全选 / 变红 / 历史区）一律复用同一套纯函数。
 */
const isSkillsMode = computed(() => props.resource === 'skills')

/**
 * 大组折叠键；子组折叠键直接复用后端给的 group_key。
 *
 * skills 大组必须自定一个键：用户页与角色页会**同时挂载**工具实例与技能实例，
 * 键若撞车（例如都用 group:builtin），折叠一个会让另一个跟着动。
 */
const GROUP_BUILTIN_KEY = 'group:builtin'
const GROUP_DYNAMIC_KEY = 'group:dynamic'
const GROUP_SKILLS_KEY = 'group:forbidden-skills'

const candidates = ref<ForbiddenToolCandidate[]>([])
const loading = ref(false)
const loadError = ref('')
const searchQuery = ref('')

/** 文案随资源类型切换：skills 模式里把技能说成"工具"会误导用户。 */
const resourceNoun = computed(() => (isSkillsMode.value ? '技能' : '工具'))
const searchPlaceholder = computed(() => `搜索${resourceNoun.value}名...`)
const loadingText = computed(() => `正在加载可禁用${resourceNoun.value}目录…`)
const loadErrorText = computed(() => `可禁用${resourceNoun.value}目录加载失败，请刷新页面重试。`)
const orphanHint = computed(() =>
  isSkillsMode.value
    ? '这些技能已不在当前目录中（例如公共技能已下架），取消勾选即可解除禁用。'
    : '这些工具已不在当前目录中（例如 MCP 工具已下架），取消勾选即可解除禁用。',
)

/** 折叠集合：空集 = 默认全展开；只在本组件存活期间记忆，不持久化。 */
const collapsedKeys = ref<Set<string>>(new Set())

const isSearching = computed(() => searchQuery.value.trim().length > 0)

const toggleCollapse = (key: string) => {
  const next = new Set(collapsedKeys.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedKeys.value = next
}

/** 搜索期间强制展开：只读覆盖，不写回 collapsedKeys，清空搜索即回到原状态。 */
const isExpanded = (key: string) => isSearching.value || !collapsedKeys.value.has(key)

/**
 * 当前模式下**可见**的全部可折叠分组键：大组自身 + 它下面的子组。
 *
 * 只收可见项：搜索会把子组过滤掉一部分，把不可见的键也收进来会让"折叠全部"后
 * 清空搜索时冒出一堆意外折叠的分组。skills 模式与工具模式互斥，不会同时出现。
 */
const collapsibleKeys = computed(() => {
  const keys: string[] = []
  if (isSkillsMode.value) {
    if (showSkillSection.value) {
      keys.push(GROUP_SKILLS_KEY, ...visibleSkillGroups.value.map((group) => group.key))
    }
    return keys
  }
  if (showBuiltinSection.value) {
    keys.push(GROUP_BUILTIN_KEY, ...visibleBuiltinGroups.value.map((group) => group.key))
  }
  if (showDynamicSection.value) {
    keys.push(GROUP_DYNAMIC_KEY, ...visibleDynamicGroups.value.map((group) => group.key))
  }
  return keys
})

/** 是否所有可见分组都已折叠——决定按钮显示「展开全部」还是「折叠全部」。 */
const allGroupsCollapsed = computed(() => {
  const keys = collapsibleKeys.value
  return keys.length > 0 && keys.every((key) => collapsedKeys.value.has(key))
})

/** 一键折叠/展开全部分组（含子组）：折叠时先全折叠，再次点击全展开。 */
const toggleAllGroups = () => {
  const next = new Set(collapsedKeys.value)
  const collapse = !allGroupsCollapsed.value
  for (const key of collapsibleKeys.value) {
    if (collapse) next.add(key)
    else next.delete(key)
  }
  collapsedKeys.value = next
}

const selectedIds = computed(() => new Set((props.modelValue || []).map((id) => String(id))))

// 大组划分沿用既有 category 判据：旧响应没有 group_key 时也能正确落组
const builtinCandidates = computed(() => {
  // MCP 页签里不该出现内置工具：整个内置大组都不渲染（见 showBuiltinSection）
  if (props.toolScope === 'mcp') return []
  return candidates.value.filter((candidate) => candidate.category !== 'api' && candidate.category !== 'mcp')
})
const dynamicCandidates = computed(() => {
  const dynamic = candidates.value.filter(
    (candidate) => candidate.category === 'api' || candidate.category === 'mcp',
  )
  if (props.toolScope === 'all') return dynamic
  // 工具页签去掉 MCP、MCP 页签只留 MCP。判据走共享纯函数，与「功能限定」弹框同源，
  // 否则同一个 MCP 工具在两个入口会被分到不同页签。
  return filterCandidatesByToolTab(dynamic, props.toolScope)
})

const builtinGroups = computed(() => groupCandidates(builtinCandidates.value))
const dynamicGroups = computed(() => groupCandidates(dynamicCandidates.value))
/** skills 模式的候选整体交给同一个 groupCandidates：后端将来给出多个技能组也不会漏渲染。 */
const skillGroups = computed(() => groupCandidates(candidates.value))

// 搜索只决定可见子组，不写回折叠集合
const visibleBuiltinGroups = computed(() => filterSubGroups(builtinGroups.value, searchQuery.value))
const visibleDynamicGroups = computed(() => filterSubGroups(dynamicGroups.value, searchQuery.value))
const visibleSkillGroups = computed(() => filterSubGroups(skillGroups.value, searchQuery.value))

const builtinVisibleCandidates = computed(() =>
  visibleBuiltinGroups.value.flatMap((sub) => sub.candidates),
)
const dynamicVisibleCandidates = computed(() =>
  visibleDynamicGroups.value.flatMap((sub) => sub.candidates),
)
const skillVisibleCandidates = computed(() =>
  visibleSkillGroups.value.flatMap((sub) => sub.candidates),
)

/** 计数与全选判定都委托纯函数：这里只把候选列表包成"组"的形状。 */
const asGroup = (key: string, list: ForbiddenToolCandidate[]): ForbiddenToolSubGroup => ({
  key,
  label: key,
  candidates: list,
})
const selectedCountOf = (list: ForbiddenToolCandidate[]) =>
  groupSelectedCount(asGroup('__selection__', list), selectedIds.value)
/** 组卡片色调：有选中即整卡红化。判定与 (n/m) 计数同源，搜索态下按可见项判断。 */
const toneOf = (list: ForbiddenToolCandidate[], base: 'gray' | 'indigo') =>
  groupToneClasses(selectedCountOf(list) > 0, base)
const isAllSelected = (list: ForbiddenToolCandidate[]) =>
  list.length > 0 && selectedCountOf(list) === list.length
const isCandidateSelected = (candidate: ForbiddenToolCandidate) => selectedCountOf([candidate]) > 0

/** 候选是否被锁定：已在所属角色的禁用集合里（别名命中同样算）。 */
const isCandidateLocked = (candidate: ForbiddenToolCandidate) =>
  isInheritedForbiddenCandidate(candidate.id, candidate.aliases, props.inherited)

const toggleSelection = (list: ForbiddenToolCandidate[], checked: boolean) => {
  // 锁定项绝不能进勾选：后端会丢弃，勾了只会变成「保存落空」。
  const selectable = list.filter((candidate) => !isCandidateLocked(candidate))
  emit('update:modelValue', applyGroupSelection(props.modelValue || [], selectable, checked))
}

/** 单卡片勾选与组级全选同语义：写入 / 移除该候选的全部 aliases。 */
const toggleCandidate = (candidate: ForbiddenToolCandidate, checked: boolean) => {
  toggleSelection([candidate], checked)
}

/** 已选中但已不在目录里的工具：必须可见、可解除。 */
const orphanIds = computed(() => findOrphanIds(props.modelValue || [], candidates.value))

const toggleOrphan = (id: string, checked: boolean) => {
  const next = new Set(selectedIds.value)
  if (checked) next.add(id)
  else next.delete(id)
  emit('update:modelValue', Array.from(next))
}

/** 大组 1 原来恒显示；现在搜索无命中时整组隐藏（加载中 / 失败仍要显示提示）。 */
const showBuiltinSection = computed(() =>
  props.toolScope !== 'mcp'
  && (!isSearching.value
    || loading.value
    || Boolean(loadError.value)
    || visibleBuiltinGroups.value.length > 0),
)
const showDynamicSection = computed(() => visibleDynamicGroups.value.length > 0)

/**
 * 动态大组的标题随 `toolScope` 变化：拆页签后这里可能只剩自定义 API 或只剩 MCP，
 * 沿用合并标题会让用户以为另一类凭空消失了。
 */
const dynamicSectionTitle = computed(() => {
  if (props.toolScope === 'tools') return '自定义 API 工具禁用'
  if (props.toolScope === 'mcp') return 'MCP 扩展工具禁用'
  return '自定义 API 与 MCP 扩展工具禁用'
})
/** skills 大组同样遵守"搜索无命中则整组隐藏"（加载中 / 失败仍要显示提示）。 */
const showSkillSection = computed(() =>
  !isSearching.value
  || loading.value
  || Boolean(loadError.value)
  || visibleSkillGroups.value.length > 0,
)

/**
 * 目录加载走共享缓存：同一页里工具实例与技能实例请求的是**同一个接口**
 * （只是取响应的不同字段），原先各发一次——打开一次弹窗就打两次网。
 * 缓存模块负责 in-flight 复用与短 TTL（见 utils/forbiddenCatalogCache.ts）：这里只注入
 * 真正的请求；加载成功后由缓存模块把快照广播出去（页面徽标计数要靠候选判断别名组）。
 */
const fetchCatalog = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const snapshot = await loadForbiddenCatalog(async () => {
      const response = await axios.get('/api/portal/tools/forbidden-catalog')
      return {
        candidates: response.data?.candidates,
        skillCandidates: response.data?.skill_candidates,
      }
    })
    // 两种资源同一个接口：tools 取 candidates，skills 取 skill_candidates
    candidates.value = isSkillsMode.value ? snapshot.skillCandidates : snapshot.candidates
  } catch (error) {
    loadError.value = loadErrorText.value
  } finally {
    loading.value = false
  }
}

onMounted(fetchCatalog)
</script>
