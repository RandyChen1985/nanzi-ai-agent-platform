<template>
  <div class="space-y-3">
    <!-- 来自角色（只读）：固定在页签上方，不随页签切换。
         它是「角色还禁了什么」的全景，按类别拆进各页签后得逐个翻才能看全；
         而管理员最常问的正是「这个用户现在为什么用不了 X」——一眼看全比聚合更重要。 -->
    <slot name="inherited" />

    <!-- 四类资源分页签。改造前是纵向堆叠，工具与技能各有一个搜索框同时显示，
         且命令区要滚动才看得到；分页签后一屏只处理一类。 -->
    <div class="border-b border-gray-200" role="tablist" aria-label="禁用资源类型">
      <div class="flex -mb-px">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          role="tab"
          :id="`forbidden-resource-tab-${tab.key}`"
          :aria-selected="activeTab === tab.key"
          :aria-controls="`forbidden-resource-panel-${tab.key}`"
          class="relative flex-1 min-w-0 px-2 py-2 text-xs font-bold transition-colors border-b-2"
          :class="activeTab === tab.key
            ? 'border-red-600 text-red-600'
            : 'border-transparent text-gray-500 hover:text-gray-700'"
          @click="activeTab = tab.key"
        >
          <span class="block truncate text-center">
            {{ tab.label }}<template v-if="tab.total !== null"> ({{ tab.selected }}/{{ tab.total }})</template><template v-else-if="tab.selected > 0"> ({{ tab.selected }})</template>
          </span>
        </button>
      </div>
    </div>

    <!-- 工具页签：内置 + 自定义 API（与「功能限定」弹框同源判据，MCP 才是单独一页） -->
    <ForbiddenToolCatalog
      v-show="activeTab === 'tools'"
      :model-value="forbiddenTools"
      tool-scope="tools"
      :inherited="inheritedTools"
      :disabled="disabled"
      @update:model-value="emit('update:forbiddenTools', $event)"
    />

    <!-- MCP 页签：与工具页签**共用同一个 v-model**（`forbidden_tools`）——
         它们本质是同一份禁用清单，只是按来源拆开展示，选中状态必须互通。 -->
    <ForbiddenToolCatalog
      v-show="activeTab === 'mcp'"
      :model-value="forbiddenTools"
      tool-scope="mcp"
      :inherited="inheritedTools"
      :disabled="disabled"
      @update:model-value="emit('update:forbiddenTools', $event)"
    />

    <!-- 技能页签 -->
    <ForbiddenToolCatalog
      v-show="activeTab === 'skills'"
      :model-value="forbiddenSkills"
      resource="skills"
      :inherited="inheritedSkills"
      :disabled="disabled"
      @update:model-value="emit('update:forbiddenSkills', $event)"
    />

    <!-- 命令页签：自由关键词输入，没有候选目录，所以页签上只显示已禁数量、没有分母 -->
    <div
      v-show="activeTab === 'commands'"
      :id="'forbidden-resource-panel-commands'"
      role="tabpanel"
      aria-labelledby="forbidden-resource-tab-commands"
      class="border border-gray-200 rounded-xl p-3 bg-white flex flex-col"
    >
      <h4 class="text-xs font-bold text-gray-700 mb-2 flex items-center gap-1.5 select-none">
        <span class="w-1.5 h-1.5 bg-amber-500 rounded-full"></span>
        自定义禁用命令关键字
      </h4>
      <p class="text-[10px] text-gray-400 mb-2">
        针对 exec_command 终端工具，输入禁止{{ commandSubject }}运行的命令名或命令序列（例如：rm, shutdown, mv, wget），可用英文逗号、中文逗号、顿号、分号或换行分隔。
      </p>
      <textarea
        :value="commandsText"
        :disabled="disabled"
        placeholder="例如: rm, shutdown, mv, wget, curl"
        class="w-full text-xs sm:text-sm border border-gray-200 rounded-lg p-2.5 min-h-[80px] focus:ring-amber-500 focus:border-amber-500 custom-scrollbar focus:outline-none disabled:bg-gray-50 disabled:cursor-not-allowed"
        @input="emit('update:commandsText', ($event.target as HTMLTextAreaElement).value)"
      ></textarea>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import ForbiddenToolCatalog from './ForbiddenToolCatalog.vue'
import {
  filterCandidatesByToolTab,
  type ForbiddenToolCandidate,
} from '../../utils/forbiddenToolGroups'
import {
  peekForbiddenCatalog,
  subscribeForbiddenCatalog,
  type ForbiddenCatalogSnapshot,
} from '../../utils/forbiddenCatalogCache'
import { countSelectedEntries } from '../../utils/forbiddenResourceBadge'
import { countForbiddenCommands } from '../../utils/forbiddenCommands'

/**
 * 「禁用资源」页的四页签容器。
 *
 * 用户页与角色页共用：两页结构完全一致，只有文案主体（「该用户」/「该角色下的用户」）
 * 与工具/技能的继承项不同，所以这里用 prop 与具名插槽参数化，避免两处各写一遍
 * ——那份重复正是「用户页改了角色页忘改」的温床。
 *
 * 组件不持有保存逻辑：三份状态都用 v-model 交回父组件，保存时机完全由父页面决定。
 */
const props = withDefaults(
  defineProps<{
    forbiddenTools: string[]
    forbiddenSkills: string[]
    commandsText: string
    /** 命令说明文案里的主体，例如「该用户」/「该角色下的用户」 */
    commandSubject?: string
    /** 工具/技能的继承项（用户页传，角色页没有继承概念） */
    inheritedTools?: string[]
    inheritedSkills?: string[]
    disabled?: boolean
  }>(),
  {
    commandSubject: '该用户',
    inheritedTools: () => [],
    inheritedSkills: () => [],
    disabled: false,
  },
)

const emit = defineEmits<{
  (e: 'update:forbiddenTools', value: string[]): void
  (e: 'update:forbiddenSkills', value: string[]): void
  (e: 'update:commandsText', value: string): void
}>()

type TabKey = 'tools' | 'mcp' | 'skills' | 'commands'

const activeTab = ref<TabKey>('tools')

/**
 * 候选目录快照（页签计数用）。
 *
 * 只订阅共享缓存、不自己发请求：目录由页面里的 ForbiddenToolCatalog 实例统一加载，
 * 同一次请求的多个实例共享结果（见 utils/forbiddenCatalogCache.ts）。这里的工具与 MCP
 * 两个实例都挂在同一个 v-model 上、共享同一份目录，计数也就不会出现两个来源。
 */
const snapshot = ref<ForbiddenCatalogSnapshot>({ candidates: [], skillCandidates: [] })
const syncSnapshot = () => {
  const current = peekForbiddenCatalog()
  if (current) snapshot.value = current
}
let unsubscribe: (() => void) | null = null

onMounted(() => {
  syncSnapshot()
  unsubscribe = subscribeForbiddenCatalog(syncSnapshot)
})
onUnmounted(() => {
  unsubscribe?.()
  unsubscribe = null
})

/** 某一类工具页签的候选（内置 + 自定义 API 归 tools，MCP 单独一类）。 */
const toolCandidatesOf = (tab: 'tools' | 'mcp'): ForbiddenToolCandidate[] =>
  filterCandidatesByToolTab(snapshot.value.candidates, tab)

/** 命令没有候选目录（自由输入关键词），计数上只给已禁数量、不给分母。 */
const tabs = computed(() => {
  const tools = toolCandidatesOf('tools')
  const mcp = toolCandidatesOf('mcp')
  const skills = snapshot.value.skillCandidates
  return [
    {
      key: 'tools' as TabKey,
      label: '工具',
      selected: countSelectedEntries(props.forbiddenTools, tools),
      total: tools.length as number | null,
    },
    {
      key: 'mcp' as TabKey,
      label: 'MCP',
      selected: countSelectedEntries(props.forbiddenTools, mcp),
      total: mcp.length as number | null,
    },
    {
      key: 'skills' as TabKey,
      label: '技能',
      selected: countSelectedEntries(props.forbiddenSkills, skills),
      total: skills.length as number | null,
    },
    {
      key: 'commands' as TabKey,
      label: '命令',
      selected: countForbiddenCommands(props.commandsText),
      total: null as number | null,
    },
  ]
})
</script>
