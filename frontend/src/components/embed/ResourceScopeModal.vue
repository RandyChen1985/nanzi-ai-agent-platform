<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { mcpToolDisplayName as formatMcpToolDisplayName } from "@/utils/mcpToolDisplayName";

type ResourceScopeGroup = {
  key: ResourceScopeGroupKey;
  label: string;
  shortLabel?: string;
  hint: string;
};

type ResourceScopeGroupKey = "datasets" | "knowledge_bases" | "skills" | "mcp_tools";

type ResourceScopeChip = {
  key: string;
  label: string;
  orphan?: boolean;
  item: any;
};

type McpOptionGroup = {
  serverName: string;
  serverRemark?: string;
  tools: any[];
};

const props = defineProps<{
  visible: boolean;
  draft: Record<string, any>;
  groups: ResourceScopeGroup[];
  activeTab: ResourceScopeGroupKey;
  orphanCount: number;
  loading: boolean;
  saving: boolean;
  optionSearch: Record<ResourceScopeGroupKey, string>;
  selectedCount: (key: ResourceScopeGroupKey) => number;
  optionTotalCount: (key: ResourceScopeGroupKey) => number;
  skillScopeSelectedCount: (scope: "global" | "personal") => number;
  skillScopeTotalCount: (scope: "global" | "personal") => number;
  orphanSelections: (key: ResourceScopeGroupKey) => any[];
  selectedChips: (key: ResourceScopeGroupKey) => ResourceScopeChip[];
  sortedOptions: (key: ResourceScopeGroupKey) => any[];
  optionSelected: (key: ResourceScopeGroupKey, option: any) => boolean;
  optionInitial: (option: any) => string;
  optionAccent: (index: number) => string;
}>();

const emit = defineEmits<{
  (event: "close"): void;
  (event: "refresh"): void;
  (event: "save"): void;
  (event: "update:activeTab", value: ResourceScopeGroupKey): void;
  (event: "remove-draft", type: ResourceScopeGroupKey, item: any): void;
  (event: "toggle-option", type: ResourceScopeGroupKey, option: any): void;
  /** MCP 分组全选 / 取消全选：selectAll=true 表示全部勾选 */
  (event: "toggle-group", type: ResourceScopeGroupKey, options: any[], selectAll: boolean): void;
}>();

const projectNameInput = ref<HTMLInputElement | null>(null);
const collapsedMcpGroups = ref<Set<string>>(new Set());
/** 用户尝试保存或失焦后，才强化展示项目名称必填提示 */
const projectNameTouched = ref(false);
/** 技能面板：平台 / 个人，对齐输入框技能中心 */
const skillScopeTab = ref<"global" | "personal">("global");

const projectName = computed(() => String(props.draft?.project_name || "").trim());
const projectNameMissing = computed(() => !projectName.value);
const showProjectNameError = computed(() => projectNameTouched.value && projectNameMissing.value);
const canSave = computed(() => !props.saving && !projectNameMissing.value);

watch(() => props.visible, async (visible) => {
  if (!visible) {
    projectNameTouched.value = false;
    skillScopeTab.value = "global";
    return;
  }
  await nextTick();
  projectNameInput.value?.focus();
});

watch(() => props.activeTab, (tab) => {
  collapsedMcpGroups.value = new Set();
  if (tab === "skills") skillScopeTab.value = "global";
});

watch(projectName, (value) => {
  if (value) projectNameTouched.value = false;
});

const focusProjectName = async () => {
  projectNameTouched.value = true;
  await nextTick();
  projectNameInput.value?.focus();
  projectNameInput.value?.scrollIntoView({ behavior: "smooth", block: "nearest" });
};

const handleSaveClick = async () => {
  if (projectNameMissing.value) {
    await focusProjectName();
    return;
  }
  emit("save");
};

const groupMcpOptions = (options: any[]): McpOptionGroup[] => {
  const map = new Map<string, any[]>();
  for (const option of options || []) {
    const key = String(option?.server_name || "未命名服务").trim() || "未命名服务";
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(option);
  }
  return Array.from(map.entries())
    .map(([serverName, tools]) => ({
      serverName,
      serverRemark: String(tools.find((t) => t?.server_remark)?.server_remark || "").trim() || undefined,
      tools: tools.slice().sort((a, b) => String(a.name || a.id || "").localeCompare(String(b.name || b.id || ""), "zh-CN")),
    }))
    .sort((a, b) => a.serverName.localeCompare(b.serverName, "zh-CN"));
};

const mcpGroupsForActiveOptions = computed(() => groupMcpOptions(props.sortedOptions("mcp_tools")));

const mcpGroupSelectionState = (tools: any[]): "none" | "partial" | "all" => {
  if (!tools.length) return "none";
  const selected = tools.filter((tool) => props.optionSelected("mcp_tools", tool));
  if (!selected.length) return "none";
  if (selected.length === tools.length) return "all";
  return "partial";
};

const toggleMcpGroupCollapsed = (serverName: string) => {
  const next = new Set(collapsedMcpGroups.value);
  if (next.has(serverName)) next.delete(serverName);
  else next.add(serverName);
  collapsedMcpGroups.value = next;
};

const toggleMcpGroupSelectAll = (tools: any[]) => {
  if (!tools.length) return;
  const selectAll = mcpGroupSelectionState(tools) !== "all";
  emit("toggle-group", "mcp_tools", tools, selectAll);
};

/** 分组内展示短名：去掉 server_name: / server_name/ 前缀 */
const mcpToolDisplayName = (option: any, serverName: string) =>
  formatMcpToolDisplayName(option?.name || option?.id, serverName || option?.server_name);

const optionTitle = (option: any) => String(option?.name || option?.id || "");

const tabCountLabel = (key: ResourceScopeGroupKey) => {
  const selected = Number(props.selectedCount(key) || 0);
  const total = Number(props.optionTotalCount(key) || 0);
  return `(${selected}/${total})`;
};

const isPersonalOption = (option: any) => String(option?.scope || "").toLowerCase() === "personal";

const skillsForActiveScope = computed(() => {
  const options = props.sortedOptions("skills") || [];
  return options.filter((option) =>
    skillScopeTab.value === "personal" ? isPersonalOption(option) : !isPersonalOption(option),
  );
});
</script>

<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 p-4"
    @click.self="emit('close')"
    @keydown.esc="emit('close')"
  >
    <div
      class="w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden rounded-2xl bg-white dark:bg-gray-800 shadow-2xl"
      role="dialog"
      aria-labelledby="resource-scope-modal-title"
      aria-modal="true"
    >
      <div class="flex-shrink-0 px-5 pt-5 pb-3 border-b border-gray-100 dark:border-gray-700">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 id="resource-scope-modal-title" class="text-base font-black text-gray-900 dark:text-gray-100">项目会话资源</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-1 leading-relaxed">
              先填写项目名称，再按需勾选资源；保存后仅对本会话生效。
            </p>
          </div>
          <button type="button" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none p-1" aria-label="关闭" @click="emit('close')">×</button>
        </div>
        <p v-if="orphanCount > 0" class="mt-3 text-[11px] text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 border border-amber-100 dark:border-amber-800/50 rounded-lg px-3 py-2">
          有 {{ orphanCount }} 项已保存的资源当前不可用，请移除或重新选择。
        </p>
      </div>

      <div class="flex-1 overflow-y-auto px-5 py-4 space-y-4 min-h-0">
        <div class="block">
          <div class="flex items-center justify-between gap-2">
            <label for="resource-scope-project-name" class="text-xs font-bold text-gray-700 dark:text-gray-300">
              项目名称 <span class="text-red-500">*</span>
            </label>
            <span class="text-[11px] text-gray-400">必填，用于标识本会话资源范围</span>
          </div>
          <input
            id="resource-scope-project-name"
            ref="projectNameInput"
            v-model="draft.project_name"
            class="mt-1.5 w-full rounded-xl border bg-white dark:bg-gray-900/30 px-3 py-2.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 transition-colors disabled:bg-gray-100"
            :class="showProjectNameError
              ? 'border-red-400 focus:ring-red-200 focus:border-red-400'
              : 'border-gray-200 dark:border-gray-600 focus:ring-primary/30 focus:border-primary'"
            placeholder="例如：销售经营分析"
            aria-required="true"
            :aria-invalid="showProjectNameError"
            :aria-describedby="showProjectNameError ? 'resource-scope-project-name-error' : 'resource-scope-project-name-hint'"
            @blur="projectNameTouched = true"
          />
          <p
            v-if="showProjectNameError"
            id="resource-scope-project-name-error"
            class="mt-1.5 text-[11px] text-red-600 dark:text-red-400 font-medium"
          >
            请填写项目名称后再保存
          </p>
          <p
            v-else
            id="resource-scope-project-name-hint"
            class="mt-1.5 text-[11px] text-gray-400"
          >
            资源可不选；未选时按账号默认权限使用。
          </p>
        </div>

        <div class="rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col min-h-[300px]">
          <div class="flex border-b border-gray-200 dark:border-gray-700 bg-gray-50/80 dark:bg-gray-900/40" role="tablist" aria-label="资源类型">
            <button
              v-for="group in groups"
              :key="group.key"
              type="button"
              role="tab"
              :id="`resource-scope-tab-${group.key}`"
              :aria-selected="activeTab === group.key"
              :aria-controls="`resource-scope-panel-${group.key}`"
              class="relative flex-1 min-w-0 px-2 py-2.5 text-xs font-bold transition-colors border-b-2 -mb-px"
              :class="activeTab === group.key
                ? 'border-primary text-primary bg-white dark:bg-gray-800'
                : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'"
              @click="emit('update:activeTab', group.key)"
            >
              <span class="block truncate text-center">
                {{ group.shortLabel || group.label }}{{ tabCountLabel(group.key) }}
              </span>
              <span v-if="orphanSelections(group.key).length" class="mt-0.5 block text-center text-[9px] font-bold text-amber-600 dark:text-amber-400" title="有失效项">!</span>
            </button>
          </div>

          <div
            v-for="group in groups"
            :key="'panel-' + group.key"
            v-show="activeTab === group.key"
            :id="`resource-scope-panel-${group.key}`"
            role="tabpanel"
            :aria-labelledby="`resource-scope-tab-${group.key}`"
            class="flex-1 p-3 space-y-2.5 min-h-0 flex flex-col"
          >
            <p class="text-[11px] text-gray-400 dark:text-gray-500 leading-relaxed shrink-0">{{ group.hint }}</p>

            <!-- 技能：平台 / 个人，对齐输入框技能中心 -->
            <div
              v-if="group.key === 'skills'"
              class="flex items-center gap-1 rounded-lg bg-gray-50 dark:bg-gray-900/50 p-0.5 shrink-0"
            >
              <button
                type="button"
                class="flex-1 py-1.5 text-center text-xs font-semibold rounded-md transition-colors"
                :class="skillScopeTab === 'global'
                  ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700 dark:text-gray-400'"
                @click="skillScopeTab = 'global'"
              >
                平台
                <span class="ml-0.5 text-[10px] font-normal text-gray-400">
                  ({{ skillScopeSelectedCount('global') }}/{{ skillScopeTotalCount('global') }})
                </span>
              </button>
              <button
                type="button"
                class="flex-1 py-1.5 text-center text-xs font-semibold rounded-md transition-colors"
                :class="skillScopeTab === 'personal'
                  ? 'bg-white dark:bg-gray-700 text-emerald-700 dark:text-emerald-300 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700 dark:text-gray-400'"
                @click="skillScopeTab = 'personal'"
              >
                我的
                <span class="ml-0.5 text-[10px] font-normal text-gray-400">
                  ({{ skillScopeSelectedCount('personal') }}/{{ skillScopeTotalCount('personal') }})
                </span>
              </button>
            </div>

            <!-- 已选项以下方勾选为准；仅失效项在上方展示便于移除 -->
            <div v-if="orphanSelections(group.key).length > 0" class="flex flex-wrap gap-1.5 shrink-0">
              <button
                v-for="chip in selectedChips(group.key).filter((item) => item.orphan)"
                :key="chip.key"
                type="button"
                class="inline-flex items-center gap-1 max-w-full px-2 py-1 rounded-full text-[10px] font-bold border transition-colors bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700"
                title="资源已不可用，点击移除"
                @click="emit('remove-draft', group.key, chip.item)"
              >
                <span class="truncate">{{ chip.label }}</span>
                <span class="shrink-0 opacity-70">×</span>
              </button>
            </div>

            <input
              v-model="optionSearch[group.key]"
              type="search"
              class="w-full rounded-lg border border-gray-200 dark:border-gray-600 bg-transparent px-3 py-2 text-xs shrink-0"
              :placeholder="group.key === 'mcp_tools'
                ? `搜索${group.label}或服务名`
                : group.key === 'skills'
                  ? (skillScopeTab === 'personal' ? '搜索我的技能' : '搜索平台技能')
                  : `搜索${group.label}`"
            />

            <div v-if="loading" class="text-xs text-gray-400 py-6 text-center flex-1">正在加载资源…</div>

            <!-- MCP：按服务分组 + 组内全选 -->
            <div
              v-else-if="group.key === 'mcp_tools' && mcpGroupsForActiveOptions.length"
              class="space-y-2 flex-1 min-h-0 overflow-y-auto pr-0.5"
            >
              <div
                v-for="mcpGroup in mcpGroupsForActiveOptions"
                :key="mcpGroup.serverName"
                class="rounded-lg border border-gray-100 dark:border-gray-700/80 overflow-hidden"
              >
                <div class="flex items-center gap-2 px-2.5 py-2 bg-gray-50/90 dark:bg-gray-900/60 border-b border-gray-100 dark:border-gray-700/60">
                  <label class="inline-flex items-center justify-center shrink-0 cursor-pointer" @click.stop>
                    <input
                      type="checkbox"
                      class="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary/40 cursor-pointer"
                      :checked="mcpGroupSelectionState(mcpGroup.tools) === 'all'"
                      :indeterminate="mcpGroupSelectionState(mcpGroup.tools) === 'partial'"
                      @change.stop="toggleMcpGroupSelectAll(mcpGroup.tools)"
                    />
                  </label>
                  <button
                    type="button"
                    class="flex-1 min-w-0 flex items-center gap-1.5 text-left"
                    @click="toggleMcpGroupCollapsed(mcpGroup.serverName)"
                  >
                    <svg
                      class="w-3.5 h-3.5 text-gray-400 shrink-0 transition-transform"
                      :class="collapsedMcpGroups.has(mcpGroup.serverName) ? '-rotate-90' : ''"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                    </svg>
                    <span class="min-w-0 flex-1">
                      <span
                        class="text-xs font-semibold text-gray-800 dark:text-gray-100 truncate block"
                        :title="mcpGroup.serverRemark ? `${mcpGroup.serverName} · ${mcpGroup.serverRemark}` : mcpGroup.serverName"
                      >
                        {{ mcpGroup.serverName }}
                      </span>
                      <span
                        v-if="mcpGroup.serverRemark"
                        class="text-[10px] text-gray-400 truncate block leading-snug mt-0.5"
                      >{{ mcpGroup.serverRemark }}</span>
                    </span>
                    <span class="shrink-0 text-[10px] text-gray-400 font-medium">({{ mcpGroup.tools.length }})</span>
                  </button>
                  <button
                    type="button"
                    class="shrink-0 text-[10px] font-semibold text-primary/90 hover:text-primary px-1.5 py-0.5 rounded hover:bg-primary/5"
                    @click.stop="toggleMcpGroupSelectAll(mcpGroup.tools)"
                  >
                    {{ mcpGroupSelectionState(mcpGroup.tools) === 'all' ? '取消全选' : '全选' }}
                  </button>
                </div>

                <div v-show="!collapsedMcpGroups.has(mcpGroup.serverName)" class="py-0.5">
                  <button
                    v-for="(option, optionIndex) in mcpGroup.tools"
                    :key="option.id"
                    type="button"
                    role="checkbox"
                    :aria-checked="optionSelected(group.key, option)"
                    class="w-full flex items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-colors border border-transparent"
                    :class="optionSelected(group.key, option)
                      ? 'bg-primary/5 dark:bg-primary/10 border-primary/20'
                      : 'hover:bg-gray-50 dark:hover:bg-gray-700/60'"
                    @click="emit('toggle-option', group.key, option)"
                  >
                    <span
                      class="w-8 h-8 rounded-full flex items-center justify-center text-xs font-black text-white shrink-0"
                      :class="option.scope === 'personal' ? 'bg-emerald-500' : optionAccent(optionIndex)"
                    >{{ optionInitial({ name: mcpToolDisplayName(option, mcpGroup.serverName) }) }}</span>
                    <span class="min-w-0 flex-1">
                      <span
                        class="block text-sm font-bold text-gray-900 dark:text-gray-100 truncate"
                        :title="optionTitle(option)"
                      >{{ mcpToolDisplayName(option, mcpGroup.serverName) }}</span>
                      <span
                        v-if="option.description"
                        class="block text-xs text-gray-400 dark:text-gray-500 truncate"
                        :title="option.description"
                      >{{ option.description }}</span>
                    </span>
                    <span
                      class="w-5 h-5 rounded-md border flex items-center justify-center shrink-0"
                      :class="optionSelected(group.key, option) ? 'bg-primary border-primary text-white' : 'border-gray-300 dark:border-gray-600'"
                    >
                      <svg v-if="optionSelected(group.key, option)" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m5 12 4 4L19 6" /></svg>
                    </span>
                  </button>
                </div>
              </div>
            </div>

            <!-- 技能：按平台 / 个人过滤 -->
            <div v-else-if="group.key === 'skills' && skillsForActiveScope.length" class="space-y-0.5 flex-1 min-h-0 overflow-y-auto pr-0.5">
              <button
                v-for="(option, optionIndex) in skillsForActiveScope"
                :key="`${option.scope || 'global'}:${option.id}`"
                type="button"
                role="checkbox"
                :aria-checked="optionSelected(group.key, option)"
                class="w-full flex items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-colors border border-transparent"
                :class="optionSelected(group.key, option)
                  ? 'bg-primary/5 dark:bg-primary/10 border-primary/20'
                  : 'hover:bg-gray-50 dark:hover:bg-gray-700/60'"
                @click="emit('toggle-option', group.key, option)"
              >
                <span
                  class="w-8 h-8 rounded-full flex items-center justify-center text-xs font-black text-white shrink-0"
                  :class="isPersonalOption(option) ? 'bg-emerald-500' : optionAccent(optionIndex)"
                >{{ optionInitial(option) }}</span>
                <span class="min-w-0 flex-1">
                  <span class="flex items-center gap-1.5 min-w-0">
                    <span class="block text-sm font-bold text-gray-900 dark:text-gray-100 truncate" :title="option.name || option.id">{{ option.name || option.id }}</span>
                    <span
                      v-if="isPersonalOption(option)"
                      class="shrink-0 px-1 py-px text-[8px] font-semibold rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
                    >个人</span>
                  </span>
                  <span v-if="option.description" class="block text-xs text-gray-400 dark:text-gray-500 truncate">{{ option.description }}</span>
                </span>
                <span
                  class="w-5 h-5 rounded-md border flex items-center justify-center shrink-0"
                  :class="optionSelected(group.key, option) ? 'bg-primary border-primary text-white' : 'border-gray-300 dark:border-gray-600'"
                >
                  <svg v-if="optionSelected(group.key, option)" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m5 12 4 4L19 6" /></svg>
                </span>
              </button>
            </div>
            <div
              v-else-if="group.key === 'skills'"
              class="text-xs text-gray-400 py-6 text-center flex-1 space-y-1.5"
            >
              <p>{{ (optionSearch[group.key] || '').trim() ? '无匹配结果，试试清空搜索' : (skillScopeTab === 'personal' ? '暂无个人技能' : '暂无平台技能') }}</p>
              <p v-if="skillScopeTab === 'personal' && !(optionSearch[group.key] || '').trim()" class="text-[11px] leading-relaxed">
                可前往
                <a
                  class="text-emerald-600 hover:underline font-semibold"
                  href="/dashboard/personal?tab=skills"
                  target="_blank"
                  rel="noopener noreferrer"
                >个人中心 · 我的技能</a>
                新建 / 导入
              </p>
            </div>

            <!-- 其他资源：扁平列表 -->
            <div v-else-if="sortedOptions(group.key).length" class="space-y-0.5 flex-1 min-h-0 overflow-y-auto pr-0.5">
              <button
                v-for="(option, optionIndex) in sortedOptions(group.key)"
                :key="option.id"
                type="button"
                role="checkbox"
                :aria-checked="optionSelected(group.key, option)"
                class="w-full flex items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-colors border border-transparent"
                :class="optionSelected(group.key, option)
                  ? 'bg-primary/5 dark:bg-primary/10 border-primary/20'
                  : 'hover:bg-gray-50 dark:hover:bg-gray-700/60'"
                @click="emit('toggle-option', group.key, option)"
              >
                <span class="w-8 h-8 rounded-full flex items-center justify-center text-xs font-black text-white shrink-0" :class="optionAccent(optionIndex)">{{ optionInitial(option) }}</span>
                <span class="min-w-0 flex-1">
                  <span class="block text-sm font-bold text-gray-900 dark:text-gray-100 truncate" :title="option.name || option.id">{{ option.name || option.id }}</span>
                  <span v-if="option.description" class="block text-xs text-gray-400 dark:text-gray-500 truncate">{{ option.description }}</span>
                </span>
                <span
                  class="w-5 h-5 rounded-md border flex items-center justify-center shrink-0"
                  :class="optionSelected(group.key, option) ? 'bg-primary border-primary text-white' : 'border-gray-300 dark:border-gray-600'"
                >
                  <svg v-if="optionSelected(group.key, option)" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m5 12 4 4L19 6" /></svg>
                </span>
              </button>
            </div>
            <div v-else class="text-xs text-gray-400 py-6 text-center flex-1">
              {{ (optionSearch[group.key] || '').trim() ? '无匹配结果，试试清空搜索' : '暂无可选资源' }}
            </div>
          </div>
        </div>
      </div>

      <div class="flex-shrink-0 px-5 py-3.5 border-t border-gray-100 dark:border-gray-700 space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <button
            type="button"
            class="px-2.5 py-1.5 rounded-lg text-xs text-gray-500 hover:text-primary hover:bg-blue-50 dark:hover:bg-gray-700 disabled:opacity-40"
            :disabled="loading || saving"
            @click="emit('refresh')"
          >
            ↻ 刷新资源列表
          </button>
          <div class="flex items-center gap-2 ml-auto">
            <button type="button" class="px-3 py-2 text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300" :disabled="saving" @click="emit('close')">取消</button>
            <button
              type="button"
              class="px-4 py-2 rounded-lg text-sm font-bold min-w-[6.5rem] transition-colors"
              :class="canSave
                ? 'bg-primary text-white hover:bg-primary/90'
                : 'bg-primary/40 text-white cursor-pointer'"
              :disabled="saving"
              :title="projectNameMissing ? '请先填写项目名称' : '保存本会话资源范围'"
              @click="handleSaveClick"
            >
              {{ saving ? '保存中…' : '保存范围' }}
            </button>
          </div>
        </div>
        <p
          v-if="projectNameMissing"
          class="text-right text-[11px] text-amber-700 dark:text-amber-400"
        >
          保存前需填写项目名称，
          <button type="button" class="font-semibold underline underline-offset-2 hover:text-amber-800" @click="focusProjectName">
            去填写
          </button>
        </p>
      </div>
    </div>
  </div>
</template>
