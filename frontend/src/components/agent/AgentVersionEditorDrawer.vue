<script setup lang="ts">
import ScopeGateSelect from "./ScopeGateSelect.vue";
import { computed, ref } from 'vue';
import type { AIAgent, AIAgentBase, AIAgentVersion, AgentType, StaleToolRef } from '../../api/agent';
import type { AIModel } from '../../api/model';
import MarkdownEditor from '../MarkdownEditor.vue';
import Modal from '../Modal.vue';
import MessageRenderer from '../MessageRenderer.vue';
import ModelSelect from '../common/ModelSelect.vue';
import AgentAvatarField from './AgentAvatarField.vue';
import AgentNameField from './AgentNameField.vue';
import { normalizeMarkdownTheme, type MarkdownTheme } from '@/types/markdownTheme';
import { mcpToolDisplayName } from '../../utils/mcpToolDisplayName';
import { getTemperatureGuidance } from '../../utils/temperatureGuidance';
import { selectableLlmModels } from '../../utils/modelSelect';

type VersionConfigStep = 'agent' | 'model' | 'tools' | 'prompt' | 'welcome' | 'review';
type ToolGroup = { label: string; icon: string; tools: any[] };
type SkillItem = { id: string; name?: string; description?: string; enabled?: string | boolean; path?: string };

const props = withDefaults(defineProps<{
  show: boolean;
  isCreatingAgent: boolean;
  isOnboardingFlow: boolean;
  /** 物理标识符预检进行中 */
  agentNameChecking: boolean;
  /** 物理标识符预检结论（不可用原因） */
  agentNameMessage: string;
  agentForm: Partial<AIAgentBase>;
  versionForm: Partial<AIAgentVersion>;
  globalAgentToolcallTimeout: number;
  selectedAgent: AIAgent | null;
  models: AIModel[];
  globalModelTemperature: number;
  canEditVersion: boolean;
  toolTab: 'static' | 'mcp' | 'skills';
  toolSearchQuery: string;
  versionConfigStep: VersionConfigStep;
  versionConfigSteps: { id: VersionConfigStep; label: string }[];
  versionConfigProgress: number;
  versionConfigProgressTotal: number;
  versionConfigProgressLabels: string;
  selectedToolsCount: number;
  selectedStaticToolsCount: number;
  selectedMcpToolsCount: number;
  selectedSkillsCount: number;
  promptCharCount: number;
  versionStatusLabel: string;
  versionStatusClass: string;
  filteredGroupedTools: ToolGroup[];
  filteredGroupedMcpTools: Record<string, any[]>;
  filteredEnabledSkills: SkillItem[];
  enabledGlobalSkillsCount: number;
  allAvailableToolsCount: number;
  mcpToolsCount: number;
  isToolSelected: (name: string) => boolean;
  isSkillSelected: (skillId: string) => boolean;
  getToolCustomConfig: (name: string) => any;
  hasToolMetadataDatasetBinding: (name: string) => boolean;
  isAllMcpSelected: (serverName: string, tools: any[]) => boolean;
  isAllStaticGroupSelected: (label: string) => boolean;
  isMcpGroupCollapsed: (serverName: string) => boolean;
  getMcpGroupSelectedCount: (tools: any[]) => number;
  isStaticGroupCollapsed: (label: string) => boolean;
  getStaticGroupSelectedCount: (tools: any[]) => number;
  getModelDisplayName: (modelId?: string) => string;
  canReachVersionConfigStep?: (step: VersionConfigStep) => boolean;
  isVersionConfigStepComplete?: (step: VersionConfigStep) => boolean;
  versionConfigIncompleteHint?: string;
  knowledgeBaseToolsStepIssues?: {
    missingTool: boolean;
    missingBinding: boolean;
    datasetCount: number;
  } | null;
  /** 运行时已失效的工具引用（服务删除/禁用、工具下线），由父级按版本诊断后传入 */
  staleToolRefs?: StaleToolRef[];
  staleToolRefsLoading?: boolean;
  staleToolRefsError?: string;
}>(), {
  staleToolRefs: () => [],
  staleToolRefsLoading: false,
  staleToolRefsError: '',
});

const emit = defineEmits<{
  close: [];
  save: [];
  publish: [];
  'update:toolTab': [value: 'static' | 'mcp' | 'skills'];
  'update:toolSearchQuery': [value: string];
  'update:versionConfigStep': [value: VersionConfigStep];
  toggleTool: [name: string];
  toggleSkill: [skillId: string];
  setSkillsCustom: [enabled: boolean];
  toggleSelectAllMcp: [serverName: string, tools: any[]];
  toggleSelectAllStatic: [label: string];
  toggleMcpGroupCollapse: [serverName: string];
  toggleStaticGroupCollapse: [label: string];
  expandAllMcpGroups: [];
  collapseAllMcpGroups: [];
  expandAllStaticGroups: [];
  collapseAllStaticGroups: [];
  setOrchestratorTemperature: [value: number];
  setSynthesisTemperature: [value: number];
  openToolRuntimeConfig: [name: string];
  openMetadataDatasetBinding: [name: string];
  openDingTalkConfig: [name: string];
  openEmailConfig: [name: string];
  openWeChatWorkConfig: [name: string];
  openFeishuConfig: [name: string];
  openRagSelector: [type: 'agent' | 'dataset', mode: 'app_id' | 'dataset_ids' | 'agent_kb_immediate'];
  copySystemPrompt: [];
  nextStep: [];
  prevStep: [];
  toast: [message: string, type?: 'success' | 'error' | 'info' | 'warning'];
  checkAgentName: [name: string];
  resetAgentNameCheck: [];
  removeStaleToolRefs: [names: string[]];
  refreshStaleToolRefs: [];
}>();

// 搜索期间一律展开分组（只读覆盖，不写回折叠集合）：否则搜索命中的工具躲在折叠的
// 分组里，用户只看到标题与计数；清空搜索即回到原本的折叠状态，无需保存快照。
const isSearching = computed(() => props.toolSearchQuery.trim().length > 0);

// 批量展开 / 折叠按当前页签派发；Skills 页签没有分组，不渲染这两个按钮。
const emitBulkExpandGroups = () => {
  if (props.toolTab === 'mcp') emit('expandAllMcpGroups');
  else emit('expandAllStaticGroups');
};

const emitBulkCollapseGroups = () => {
  if (props.toolTab === 'mcp') emit('collapseAllMcpGroups');
  else emit('collapseAllStaticGroups');
};

const llmModels = () => selectableLlmModels(props.models);

const setOrchestratorModel = (modelName: string) => {
  props.versionForm.model_name = modelName;
  const selectedModel = llmModels().find((model) => model.model_id === modelName);
  if (selectedModel) {
    props.versionForm.temperature = selectedModel.temperature ?? props.globalModelTemperature;
  }
};

const selectedOrchestratorModel = computed(() =>
  llmModels().find((model) => model.model_id === props.versionForm.model_name) ?? null,
);

const modelTemperatureForVersion = computed(() =>
  selectedOrchestratorModel.value?.temperature ?? props.globalModelTemperature,
);

const orchestratorTemperatureMismatch = computed(() => {
  const versionTemperature = Number(props.versionForm.temperature);
  return Boolean(
    selectedOrchestratorModel.value
    && Number.isFinite(versionTemperature)
    && Math.abs(versionTemperature - modelTemperatureForVersion.value) > 0.001,
  );
});

const orchestratorTemperatureAboveOne = computed(() => Number(props.versionForm.temperature) > 1);

/** 是否允许用户在输入框手动切换模型；历史版本未配置该字段时默认允许 */
const allowModelSwitch = computed({
  get: () => props.versionForm.allow_model_switch !== false,
  set: (enabled: boolean) => {
    if (!props.canEditVersion) return;
    props.versionForm.allow_model_switch = enabled;
  },
});

const formatTemperature = (value: unknown) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(2) : '—';
};

const stepIndex = () => props.versionConfigSteps.findIndex((s) => s.id === props.versionConfigStep);
const isFirstStep = () => stepIndex() <= 0;
const isLastStep = () => stepIndex() >= props.versionConfigSteps.length - 1;
const canReachStep = (step: VersionConfigStep) =>
  props.canReachVersionConfigStep ? props.canReachVersionConfigStep(step) : true;
const isCurrentStepComplete = () =>
  props.isVersionConfigStepComplete
    ? props.isVersionConfigStepComplete(props.versionConfigStep)
    : true;
const activeAgentType = computed(
  () => (props.selectedAgent?.agent_type ?? props.agentForm.agent_type ?? 'GENERAL') as AgentType,
);
const showKnowledgeBaseToolsGuidance = computed(
  () =>
    props.versionConfigStep === 'tools'
    && activeAgentType.value === 'KNOWLEDGE_BASE'
    && !!props.knowledgeBaseToolsStepIssues
    && (props.knowledgeBaseToolsStepIssues.missingTool || props.knowledgeBaseToolsStepIssues.missingBinding),
);
const goStep = (step: VersionConfigStep) => emit('update:versionConfigStep', step);
const canPublishLocalVersion = computed(() =>
  props.agentForm.engine_type === 'LOCAL'
  && props.canEditVersion
  && (!props.versionForm.id || props.versionForm.status === 'DRAFT')
  // 系统级智能体对非管理员不提供发布入口：能改、能存草稿，线上基线只能由管理员变更
  && props.selectedAgent?.can_publish_version !== false
  // 待审期间不给发布入口：草稿随便改，但线上配置在审核结束前不能变
  && props.selectedAgent?.system_status !== 'PENDING_REVIEW'
);

const AGENT_VERSION_TOOLCALL_TIMEOUT_DEFAULT = 120;
const AGENT_VERSION_TOOLCALL_TIMEOUT_MIN = 1;
const AGENT_VERSION_TOOLCALL_TIMEOUT_MAX = 86400;
const isAgentVersionToolcallTimeoutEnabled = computed(
  () => props.versionForm.toolcall_timeout_seconds != null,
);
const getAgentVersionToolcallTimeoutValue = () => {
  const value = Number(props.versionForm.toolcall_timeout_seconds);
  return Number.isInteger(value)
    && value >= AGENT_VERSION_TOOLCALL_TIMEOUT_MIN
    && value <= AGENT_VERSION_TOOLCALL_TIMEOUT_MAX
    ? value
    : AGENT_VERSION_TOOLCALL_TIMEOUT_DEFAULT;
};
const setAgentVersionToolcallTimeoutEnabled = (enabled: boolean) => {
  if (!props.canEditVersion) return;
  props.versionForm.toolcall_timeout_seconds = enabled
    ? getAgentVersionToolcallTimeoutValue()
    : null;
};
const adjustAgentVersionToolcallTimeout = (delta: number) => {
  if (!props.canEditVersion || !isAgentVersionToolcallTimeoutEnabled.value) return;
  props.versionForm.toolcall_timeout_seconds = Math.min(
    AGENT_VERSION_TOOLCALL_TIMEOUT_MAX,
    Math.max(AGENT_VERSION_TOOLCALL_TIMEOUT_MIN, getAgentVersionToolcallTimeoutValue() + delta),
  );
};
const handleAgentVersionToolcallTimeoutKeydown = (event: KeyboardEvent) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'Tab', 'Enter', 'Escape'].includes(event.key)) return;
  if (!/^\d$/.test(event.key)) event.preventDefault();
};
const handleAgentVersionToolcallTimeoutInput = (event: Event) => {
  if (!props.canEditVersion || !isAgentVersionToolcallTimeoutEnabled.value) return;
  const target = event.target as HTMLInputElement;
  const digits = target.value.replace(/\D/g, '');
  props.versionForm.toolcall_timeout_seconds = digits ? Number(digits) : null;
  target.value = digits;
};
const normalizeAgentVersionToolcallTimeoutInput = () => {
  if (!isAgentVersionToolcallTimeoutEnabled.value) return;
  const raw = String(props.versionForm.toolcall_timeout_seconds ?? '').replace(/\D/g, '');
  const value = Number(raw);
  props.versionForm.toolcall_timeout_seconds = Number.isInteger(value)
    && value >= AGENT_VERSION_TOOLCALL_TIMEOUT_MIN
    ? Math.min(AGENT_VERSION_TOOLCALL_TIMEOUT_MAX, value)
    : AGENT_VERSION_TOOLCALL_TIMEOUT_DEFAULT;
};

const mcpSubTab = ref<'global' | 'personal'>('global');

const currentScopeGroupedMcpTools = computed(() => {
  const result: Record<string, any[]> = {};
  const targetScope = mcpSubTab.value;
  
  Object.entries(props.filteredGroupedMcpTools || {}).forEach(([serverName, tools]) => {
    const isPersonalServer = tools[0]?.scope === 'personal';
    if (targetScope === 'personal' && isPersonalServer) {
      result[serverName] = tools;
    } else if (targetScope === 'global' && !isPersonalServer) {
      result[serverName] = tools;
    }
  });
  
  return result;
});

const currentScopeGroupedMcpToolsCount = computed(() => {
  return Object.keys(currentScopeGroupedMcpTools.value).length;
});

/**
 * 分组选中态：全选的分组必须在头部就能和「一个都没选」区分开。
 * 只靠右侧那行 (8/8) 小字，46 个工具分四组时根本扫不出来，
 * 折叠状态下更是完全看不见——这决定分组头用什么底色、色条和计数样式。
 */
const groupSelectionState = (
  tools: any[] | undefined,
  selectedCount: (list: any[]) => number,
): 'all' | 'some' | 'none' => {
  const total = tools?.length || 0;
  if (total <= 0) return 'none';
  const selected = selectedCount(tools as any[]);
  if (selected >= total) return 'all';
  return selected > 0 ? 'some' : 'none';
};

const mcpGroupState = (tools: any[]) =>
  groupSelectionState(tools, (list) => props.getMcpGroupSelectedCount(list));

const staticGroupState = (tools: any[]) =>
  groupSelectionState(tools, (list) => props.getStaticGroupSelectedCount(list));

const globalMcpToolsCount = computed(() => {
  let count = 0;
  Object.values(props.filteredGroupedMcpTools || {}).forEach((tools) => {
    if (tools[0]?.scope !== 'personal') count += tools.length;
  });
  return count;
});

const personalMcpToolsCount = computed(() => {
  let count = 0;
  Object.values(props.filteredGroupedMcpTools || {}).forEach((tools) => {
    if (tools[0]?.scope === 'personal') count += tools.length;
  });
  return count;
});

const welcomeIcons = [
  { value: 'chart', label: '数据' }, { value: 'knowledge', label: '知识' },
  { value: 'workspace', label: '工作台' }, { value: 'report', label: '报告' },
  { value: 'alert', label: '提醒' }, { value: 'chat', label: '对话' },
];
const welcomeConfig = computed<any>(() => {
  if (!props.versionForm.welcome_config) {
    props.versionForm.welcome_config = { enabled: false, mode: 'manual', generation_requirement: '', cards: [] };
  }
  return props.versionForm.welcome_config;
});
const ensureWelcomeCards = () => {
  if (welcomeConfig.value.cards?.length === 3) return;
  welcomeConfig.value.cards = Array.from({ length: 3 }, (_, index) => ({
    icon: 'chat', title: `欢迎提问 ${index + 1}`, subtitle: '填写卡片说明', prompt: '',
  }));
};
const toggleWelcomeCards = () => {
  welcomeConfig.value.enabled = !welcomeConfig.value.enabled;
  if (welcomeConfig.value.enabled) ensureWelcomeCards();
};
const setWelcomeMode = (mode: 'manual' | 'ai') => {
  welcomeConfig.value.mode = mode;
  if (mode === 'ai') {
    welcomeConfig.value.cards = [];
  } else {
    ensureWelcomeCards();
  }
};

// AI 消息排版样式选择与效果预览变量
const showThemePreviewHelp = ref(false);
const previewTheme = ref<MarkdownTheme>("default");
const markdownThemeOptions = [
  { value: 'default', label: '现代', emoji: '✨' },
  { value: 'minimal', label: '极简', emoji: '🍃' },
  { value: 'academic', label: '学术', emoji: '📖' },
  { value: 'apple', label: '苹果', emoji: '🍎' },
  { value: 'warm', label: '护眼', emoji: '🍂' },
  { value: 'compact', label: '紧凑', emoji: '🔎' },
  { value: 'bauhaus', label: '包豪斯', emoji: '📐' },
  { value: 'editorial', label: '日报', emoji: '📰' },
  { value: 'zen', label: '禅意', emoji: '🍃' },
] as const;
const previewMarkdownContent = ref(
  "# 数据智能分析报告 📊\n\n我们已为您完成数据检索，本次对比分析了 2026 年度的核心业务指标。点击 [查看详情链接](https://example.com) 可以获取完整报表。\n\n> **业务目标**：通过多端排版持久化，解决多平台多终端切换下的个性化体验断层。\n\n### 1. 核心数据对比\n\n| 业务方向 | 现代风格 | 极简主义 | 包豪斯 / 日报 |\n| :--- | :--- | :--- | :--- |\n| **排版对齐** | 居左对齐 | 两端对齐 | 极致秩序感 |\n| **视觉呈现** | 渐变圆角 | 灰度卡片 | 直角/人文宋体 |\n| **阅读体验** | 现代极速 | 禅意放松 | 纸质印刷级 |\n\n### 2. 核心代码规范\n\n- **单向数据流**：Props 单向绑定，避免双向脏数据。\n- **持久化策略**：前端首屏加载时首选 Redis 用户偏好，辅以 LocalStorage 容错兜底。\n\n```python\n# 核心数据同步逻辑\ndef sync_theme_preference(user_id: int, theme: str):\n    print(f\"Syncing theme {theme} to Redis for user {user_id}\")\n    return {\"status\": \"success\", \"code\": 200}\n```\n\n如有任何疑问，请随时联系管理员或在下方继续提问。"
);
const applyPreviewTheme = () => {
  if (!props.agentForm.engine_config) {
    props.agentForm.engine_config = {};
  }
  props.agentForm.engine_config.default_markdown_theme = previewTheme.value;
  showThemePreviewHelp.value = false;
};

const agentTypes: { value: AgentType; label: string; icon: string; description: string }[] = [
  { value: 'GENERAL', label: '通用助手', icon: '💬', description: '适合问答、写作和大多数专业任务' },
  { value: 'CHATBI', label: 'ChatBI', icon: '📊', description: '查询业务数据并生成分析结果' },
  { value: 'KNOWLEDGE_BASE', label: '知识库助手', icon: '📚', description: '检索已授权知识库并据此回答' },
];

const selectAgentType = (value: AgentType) => {
  props.agentForm.agent_type = value;
  const locked = value === 'CHATBI' ? 'data_query' : value === 'KNOWLEDGE_BASE' ? 'knowledge_base' : 'general_chat';
  const extension = (props.agentForm.capabilities || []).filter((item) => !['general_chat', 'data_query', 'knowledge_base'].includes(item));
  props.agentForm.capabilities = [locked, ...extension];
  const tools = (props.versionForm.tools || []).filter((item) => {
    const name = typeof item === 'string' ? item : (item as any).name;
    return !['get_dataset_schema', 'execute_sql_query', 'search_knowledge_base'].includes(name);
  });
  props.versionForm.tools = value === 'CHATBI'
    ? ['get_dataset_schema', 'execute_sql_query', ...tools]
    : value === 'KNOWLEDGE_BASE'
      ? ['search_knowledge_base', ...tools]
      : tools;
};

const engineConfig = computed<Record<string, any>>(() => {
  if (!props.agentForm.engine_config) props.agentForm.engine_config = {};
  return props.agentForm.engine_config;
});
const newCapability = ref('');
const showAgentTypeHelp = ref(false);
const showEngineHelp = ref(false);
const showCapabilityHelp = ref(false);
const primaryCapabilities = new Set(['general_chat', 'data_query', 'knowledge_base']);
const isExternalEngine = (engineType?: string | null) =>
  engineType === 'RAGFLOW' || engineType === 'OPENCLAW';
const normalizeCapabilitiesForForm = (
  agentType: AgentType,
  capabilities: string[] | undefined,
  engineType?: string | null,
) => {
  const locked =
    engineType === 'RAGFLOW' || engineType === 'OPENCLAW'
      ? 'general_chat'
      : agentType === 'CHATBI'
        ? 'data_query'
        : agentType === 'KNOWLEDGE_BASE'
          ? 'knowledge_base'
          : 'general_chat';
  const extension = (capabilities || []).filter((item) => !primaryCapabilities.has(item));
  return [locked, ...extension];
};
const supportsCapabilityTags = computed(
  () =>
    props.agentForm.engine_type === 'LOCAL'
    || props.agentForm.engine_type === 'RAGFLOW'
    || props.agentForm.engine_type === 'OPENCLAW',
);
const lockedPrimaryCapability = computed(() => {
  if (isExternalEngine(props.agentForm.engine_type)) {
    return 'general_chat';
  }
  return props.agentForm.agent_type === 'CHATBI'
    ? 'data_query'
    : props.agentForm.agent_type === 'KNOWLEDGE_BASE'
      ? 'knowledge_base'
      : 'general_chat';
});
const addCapability = () => {
  const value = newCapability.value.trim();
  if (!value || (props.agentForm.capabilities || []).includes(value)) return;
  props.agentForm.capabilities = [...(props.agentForm.capabilities || []), value];
  newCapability.value = '';
};
// 「跟随全局」不落键，保持 engine_config 干净；后端缺省即 inherit。
const scopeGateMode = computed(() => {
  const raw = String(engineConfig.value.scope_gate || '').trim().toLowerCase();
  return ['inherit', 'on', 'off', 'bound_only'].includes(raw) ? raw : 'inherit';
});
const scopeGateModeModel = computed({
  get: () => scopeGateMode.value,
  set: (mode: string) => {
    if (mode === 'inherit') {
      delete engineConfig.value.scope_gate;
      return;
    }
    engineConfig.value.scope_gate = mode;
  },
});
const removeCapability = (value: string) => {
  props.agentForm.capabilities = (props.agentForm.capabilities || []).filter((item) => item !== value);
};
const selectEngine = (value: 'LOCAL' | 'RAGFLOW' | 'OPENCLAW') => {
  props.agentForm.engine_type = value;
  if (value !== 'LOCAL') {
    props.agentForm.agent_type = 'GENERAL';
  }
  props.agentForm.capabilities = normalizeCapabilitiesForForm(
    props.agentForm.agent_type || 'GENERAL',
    props.agentForm.capabilities,
    value,
  );
};
const extensionCapabilities = computed(() =>
  (props.agentForm.capabilities || []).filter((item) => !primaryCapabilities.has(item))
);
const builtInEngineCapabilities = computed(() =>
  props.agentForm.engine_type === 'RAGFLOW'
    ? ['RAGFlow 远程智能体调用', '工作流执行', '流式对话', ...(engineConfig.value.dataset_ids?.length ? ['知识库检索'] : [])]
    : props.agentForm.engine_type === 'OPENCLAW'
      ? ['OpenClaw 远程任务执行', '流式对话', ...(engineConfig.value.safety_check_enabled ? ['输入安全审计', '输出安全审计'] : [])]
      : []
);

const externalCreationMissingFields = computed(() => {
  if (!props.isCreatingAgent || props.agentForm.engine_type === 'LOCAL') return [];
  const missing: string[] = [];
  if (!String(props.agentForm.name || '').trim()) missing.push('物理标识符');
  if (!String(props.agentForm.display_name || '').trim()) missing.push('显示名称');
  if (props.agentForm.engine_type === 'RAGFLOW' && !String(engineConfig.value.app_id || '').trim()) {
    missing.push('RAGFlow App ID');
  }
  if (props.agentForm.engine_type === 'OPENCLAW') {
    if (!String(engineConfig.value.base_url || '').trim()) missing.push('OpenClaw 地址');
    if (!String(engineConfig.value.model || '').trim()) missing.push('机器人 ID');
  }
  return missing;
});
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="fixed inset-0 z-[9990]">
      <div class="absolute inset-0 bg-black/40 backdrop-blur-sm" @click="emit('close')"></div>

      <div class="absolute inset-y-0 right-0 w-full max-w-4xl bg-white shadow-2xl flex flex-col version-editor-drawer">
        <!-- Header -->
        <div class="px-6 py-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between flex-shrink-0">
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <h2 class="text-lg font-bold text-gray-900 truncate">
                {{ isCreatingAgent ? '新建智能体' : `版本配置 · V${versionForm.version_number || 'New'}` }}
              </h2>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full border" :class="versionStatusClass">
                {{ versionStatusLabel }}
              </span>
            </div>
            <p v-if="isCreatingAgent" class="text-xs text-gray-500 mt-0.5 truncate">创建智能体并配置初始版本 V1</p>
            <p v-else-if="selectedAgent" class="text-xs text-gray-500 mt-0.5 truncate">
              {{ selectedAgent.display_name }}
            </p>
          </div>
          <button
            type="button"
            @click="emit('close')"
            class="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- Stepper -->
        <div class="px-6 py-3 border-b border-gray-100 bg-white flex-shrink-0">
          <div class="flex items-center gap-1">
            <template v-for="(step, idx) in versionConfigSteps" :key="step.id">
              <button
                type="button"
                @click="goStep(step.id)"
                :disabled="!canReachStep(step.id) && versionConfigStep !== step.id"
                class="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
                :class="versionConfigStep === step.id
                  ? 'bg-primary text-white shadow-sm'
                  : canReachStep(step.id)
                    ? 'text-gray-500 hover:bg-gray-100 hover:text-gray-700'
                    : 'cursor-not-allowed text-gray-300'"
                :title="!canReachStep(step.id) && versionConfigStep !== step.id ? '请先完成前面的配置步骤' : undefined"
              >
                <span
                  class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold border"
                  :class="versionConfigStep === step.id
                    ? 'bg-white/20 border-white/30 text-white'
                    : canReachStep(step.id)
                      ? 'bg-gray-100 border-gray-200 text-gray-500'
                      : 'bg-gray-50 border-gray-100 text-gray-300'"
                >{{ idx + 1 }}</span>
                {{ step.label }}
              </button>
              <svg
                v-if="idx < versionConfigSteps.length - 1"
                class="w-4 h-4 text-gray-300 flex-shrink-0"
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
              </svg>
            </template>
          </div>
          <div v-if="isCreatingAgent && agentForm.engine_type !== 'LOCAL'" class="mt-2 text-[10px] text-gray-400">
            外部引擎不创建本地版本，完成当前页面即可创建智能体。
          </div>
          <div v-else class="mt-2 text-[10px] text-gray-400">
            配置完成度 {{ versionConfigProgress }} / {{ versionConfigProgressTotal }}（{{ versionConfigProgressLabels }}）
          </div>
        </div>

        <!-- Body -->
        <div class="flex-1 overflow-y-auto min-h-0 px-6 py-5 version-editor-body">
          <!-- Step 1: Agent information (first-time creation only) -->
          <div v-if="versionConfigStep === 'agent'" class="space-y-5 max-w-3xl">
            <div>
              <div class="flex items-center gap-2">
                <h3 class="text-sm font-bold text-gray-900">智能体信息</h3>
                <!-- 只表白状态的小徽章：整块说明已移除（发布/下架都在卡片菜单），
                     但编辑系统级时完全不提又会让用户不知道「为什么这里动不了」。 -->
                <span
                  v-if="!isCreatingAgent && selectedAgent?.is_system"
                  class="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700"
                  title="系统级智能体：发布、下架请走智能体卡片的操作菜单"
                >
                  <svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                  系统级
                </span>
              </div>
              <p class="mt-1 text-sm text-gray-500">先选择执行引擎并配置智能体属性，页面会自动调整所需配置和后续流程。</p>
            </div>

            <div class="rounded-xl border border-blue-100 bg-blue-50/40 p-4">
              <div class="flex items-center gap-1.5">
                <label class="block text-xs font-black uppercase tracking-widest text-gray-600">执行引擎</label>
                <button
                  type="button"
                  class="flex h-5 w-5 items-center justify-center rounded-full border border-blue-200 bg-white text-xs font-bold text-blue-600 hover:bg-blue-50"
                  aria-label="查看执行引擎说明"
                  title="查看三个引擎的区别"
                  @click="showEngineHelp = true"
                >?</button>
              </div>
              <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
                <button v-for="engine in [{ value: 'LOCAL', label: 'NanZi Engine', icon: '🧠', note: '平台原生编排' }, { value: 'RAGFLOW', label: 'RAGFlow', icon: '🌊', note: '外部智能体' }, { value: 'OPENCLAW', label: 'OpenClaw', icon: '🦞', note: '外部任务机器人' }]" :key="engine.value" type="button" @click="selectEngine(engine.value as any)" class="rounded-xl border-2 bg-white p-4 text-left transition-all" :class="agentForm.engine_type === engine.value ? 'border-primary shadow-sm ring-1 ring-primary/10' : 'border-white hover:border-blue-200'">
                  <div class="flex items-center gap-2"><span class="text-xl">{{ engine.icon }}</span><span class="text-sm font-bold text-gray-800">{{ engine.label }}</span></div><div class="mt-2 text-[11px] text-gray-500">{{ engine.note }}</div>
                </button>
              </div>
            </div>
            <!-- 与「编辑智能体」弹窗同一布局：标识符 / 显示名称 / 排序权重 同行 -->
            <div class="grid grid-cols-1 gap-4 md:grid-cols-[1fr_1fr_8rem]">
              <AgentNameField
                v-model="agentForm.name"
                required
                :checking="agentNameChecking"
                :error-message="agentNameMessage"
                hint="保存后不可修改，建议使用小写英文与连字符。"
                @check="emit('checkAgentName', $event)"
                @reset="emit('resetAgentNameCheck')"
              />
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">显示名称 <span class="text-red-500">*</span></label>
                <input v-model="agentForm.display_name" placeholder="例如 销售数据助手" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
              <div>
                <label class="mb-1 flex items-center gap-1 text-sm font-medium text-gray-700">
                  <span>排序权重</span>
                  <span
                    class="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-gray-300 text-[10px] font-semibold text-gray-400"
                    title="仅影响聊天页面的智能体选择列表顺序，值越大越靠前"
                  >?</span>
                </label>
                <input v-model.number="agentForm.sort_order" type="number" placeholder="值越大越靠前" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
            </div>
            <!-- 头像独占一行，与编辑弹窗完全一致 -->
            <AgentAvatarField v-model="agentForm.avatar_url" :agent-id="selectedAgent?.id" />
            <div v-if="agentForm.engine_type === 'LOCAL'">
              <div class="mb-2 flex items-center gap-1.5">
                <label class="text-sm font-medium text-gray-700">智能体类型 <span class="text-red-500">*</span></label>
                <button
                  type="button"
                  class="flex h-5 w-5 items-center justify-center rounded-full border border-blue-200 bg-blue-50 text-xs font-bold text-blue-600 hover:bg-blue-100"
                  aria-label="查看智能体类型说明"
                  title="查看智能体类型差异"
                  @click="showAgentTypeHelp = true"
                >?</button>
              </div>
              <div v-if="isCreatingAgent" class="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <button v-for="option in agentTypes" :key="option.value" type="button" @click="selectAgentType(option.value)" class="rounded-xl border p-4 text-left transition-all" :class="agentForm.agent_type === option.value ? 'border-primary bg-blue-50 ring-1 ring-primary/20' : 'border-gray-200 hover:border-blue-300'">
                  <div class="font-bold text-gray-900"><span class="mr-2">{{ option.icon }}</span>{{ option.label }}</div>
                  <p class="mt-2 text-xs leading-5 text-gray-500">{{ option.description }}</p>
                </button>
              </div>
              <div v-else class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">当前类型：<span class="font-semibold">{{ agentTypes.find((item) => item.value === agentForm.agent_type)?.label }}</span><span class="ml-2 font-mono text-xs text-gray-500">{{ lockedPrimaryCapability }}</span></div>
            </div>
            <div
              v-else-if="agentForm.engine_type === 'RAGFLOW' || agentForm.engine_type === 'OPENCLAW'"
              class="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-gray-600"
            >
              <div class="font-semibold text-gray-800">智能体类型：通用助手</div>
              <p class="mt-1 leading-5">
                外部引擎固定主能力为 <span class="font-mono">general_chat</span>，可在下方补充扩展能力标签。
              </p>
            </div>
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">描述信息</label>
              <textarea v-model="agentForm.description" rows="4" placeholder="说明它负责什么业务，以及适合处理哪些问题。" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30"></textarea>
            </div>
            <div v-if="agentForm.engine_type !== 'LOCAL'" class="rounded-xl border border-gray-200 bg-gray-50/60 p-4 space-y-4">
              <label class="block text-xs font-black uppercase tracking-widest text-gray-500">引擎参数</label>
              <div v-if="agentForm.engine_type === 'RAGFLOW'" class="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div><label class="mb-1 block text-xs font-medium text-gray-700">RAGFlow App ID <span class="text-red-500">*</span></label><input v-model="engineConfig.app_id" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" placeholder="App / Dialog ID" /></div>
                <div><label class="mb-1 block text-xs font-medium text-gray-700">默认 Dataset IDs</label><div class="flex gap-2"><input v-model="engineConfig.dataset_ids" class="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm" placeholder="多个 ID 逗号分隔" /><button type="button" @click="emit('openRagSelector', 'dataset', 'dataset_ids')" class="rounded-lg border bg-white px-3 text-xs text-gray-600">选择</button></div></div>
                <div><label class="mb-1 block text-xs font-medium text-gray-700">相似度阈值</label><input v-model.number="engineConfig.ragflow_similarity_threshold" type="number" min="0" max="1" step="0.05" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" /></div>
                <div><label class="mb-1 block text-xs font-medium text-gray-700">向量检索权重</label><input v-model.number="engineConfig.ragflow_vector_weight" type="number" min="0" max="1" step="0.05" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" /></div>
              </div>
              <div v-else-if="agentForm.engine_type === 'OPENCLAW'" class="space-y-3">
                <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div><label class="mb-1 block text-xs font-medium text-gray-700">OpenClaw 地址 <span class="text-red-500">*</span></label><input v-model="engineConfig.base_url" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" placeholder="https://api.openclaw.example.com" /><p class="mt-1 text-[10px] text-gray-400">OpenClaw 服务的完整访问地址</p></div>
                  <div><label class="mb-1 block text-xs font-medium text-gray-700">机器人 ID <span class="text-red-500">*</span></label><input v-model="engineConfig.model" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" placeholder="bot-123" /><p class="mt-1 text-[10px] text-gray-400">OpenClaw 中对应的机器人标识</p></div>
                </div>
                <div><label class="mb-1 block text-xs font-medium text-gray-700">API 密钥</label><input v-model="engineConfig.api_key" type="password" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" /></div>
                <label class="flex items-center gap-2 text-xs font-medium text-gray-700"><input v-model="engineConfig.safety_check_enabled" type="checkbox" class="rounded text-primary" />启用内容安全审查</label>
                <div v-if="engineConfig.safety_check_enabled" class="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div class="space-y-2"><select v-model="engineConfig.safety_check_input_strategy" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-xs"><option value="append">输入审计：追加规则</option><option value="override">输入审计：覆盖默认规则</option></select><textarea v-model="engineConfig.safety_check_input_prompt" rows="3" placeholder="输入审计提示词" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-xs"></textarea></div>
                  <div class="space-y-2"><select v-model="engineConfig.safety_check_output_strategy" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-xs"><option value="append">输出审计：追加规则</option><option value="override">输出审计：覆盖默认规则</option></select><textarea v-model="engineConfig.safety_check_output_prompt" rows="3" placeholder="输出审计提示词" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-xs"></textarea></div>
                </div>
              </div>
              <div class="rounded-lg border border-emerald-100 bg-emerald-50/70 p-3">
                <div class="text-xs font-bold text-emerald-800">内置能力</div>
                <div class="mt-2 flex flex-wrap gap-2"><span v-for="capability in builtInEngineCapabilities" :key="capability" class="rounded-full border border-emerald-200 bg-white px-2.5 py-1 text-[11px] font-medium text-emerald-700">✓ {{ capability }}</span></div>
                <p class="mt-2 text-[10px] leading-4 text-emerald-700/70">由所选引擎直接提供，无需在后续工具或扩展标签中重复配置。</p>
              </div>
            </div>
            <div v-if="supportsCapabilityTags" class="rounded-xl border border-gray-200 p-4">
              <div class="flex items-center gap-1.5">
                <label class="text-sm font-bold text-gray-800">扩展能力标签</label>
                <button
                  type="button"
                  class="flex h-5 w-5 items-center justify-center rounded-full border border-blue-200 bg-blue-50 text-xs font-bold text-blue-600 hover:bg-blue-100"
                  aria-label="查看扩展能力标签说明"
                  title="查看扩展能力标签说明"
                  @click="showCapabilityHelp = true"
                >?</button>
              </div>
              <p class="mt-1 text-xs text-gray-500">主类型能力由系统锁定；这里可补充 contract_review 等自定义路由标签。</p>
              <div class="mt-3 rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2">
                <div class="text-[10px] font-bold uppercase tracking-wider text-blue-600">系统内置标签</div>
                <span class="mt-2 inline-flex items-center rounded-full border border-blue-200 bg-white px-2.5 py-1 font-mono text-xs font-semibold text-blue-700">🔒 {{ lockedPrimaryCapability }}</span>
              </div>
              <div class="mt-3 flex gap-2"><input v-model="newCapability" @keyup.enter="addCapability" placeholder="输入标签并回车" class="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm" /><button type="button" @click="addCapability" class="rounded-lg bg-gray-100 px-4 text-xs font-medium text-gray-700">添加</button></div>
              <div class="mt-3 flex min-h-8 flex-wrap gap-2"><span v-for="capability in extensionCapabilities" :key="capability" class="inline-flex items-center rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-700">{{ capability }}<button type="button" @click="removeCapability(capability)" class="ml-1 text-gray-400 hover:text-red-500">×</button></span><span v-if="extensionCapabilities.length === 0" class="text-xs text-gray-400">暂无扩展能力</span></div>
            </div>

            <!-- 知识库检索范围确认：智能体级 engine_config，保存版本时一并同步 -->
            <div
              v-if="agentForm.agent_type === 'KNOWLEDGE_BASE'"
              class="rounded-xl border border-indigo-100 bg-indigo-50/50 p-4"
            >
              <label class="text-sm font-bold text-gray-800">知识库检索范围确认</label>
              <p class="mt-1 text-xs leading-5 text-gray-500">
                可检索的知识库较多、且用户本轮没有指定库时，先让用户在「全部 / 仅智能体默认 / 最相关几个」里确认范围。触发阈值与推荐库数量由系统配置统一控制，这里只决定本智能体是否参与。
              </p>
              <div class="mt-3">
                <ScopeGateSelect v-model="scopeGateModeModel" :disabled="!canEditVersion" />
              </div>
            </div>

            <!-- 推荐排版样式选择 -->
            <div class="rounded-xl border border-gray-200 p-4">
              <div class="flex items-center gap-1.5">
                <label class="text-sm font-bold text-gray-800">推荐排版样式</label>
                <button
                  type="button"
                  class="flex h-5 w-5 items-center justify-center rounded-full border border-blue-200 bg-blue-50 text-xs font-bold text-blue-600 hover:bg-blue-100"
                  aria-label="查看排版样式效果预览说明"
                  title="查看排版样式效果预览说明"
                  @click="previewTheme = normalizeMarkdownTheme(agentForm.engine_config?.default_markdown_theme); showThemePreviewHelp = true"
                >?</button>
              </div>
              <p class="mt-1 text-xs text-gray-500">指定该智能体默认推荐给用户的 Markdown 排版呈现样式，历史数据未指定则默认为现代样式。</p>
              
              <!-- 6个预置样式卡片选择 -->
              <div class="mt-4 grid grid-cols-3 gap-2">
                <button
                  v-for="themeOpt in markdownThemeOptions"
                  :key="themeOpt.value"
                  type="button"
                  @click="
                    if (!agentForm.engine_config) agentForm.engine_config = {};
                    agentForm.engine_config.default_markdown_theme = themeOpt.value;
                  "
                  class="py-2 px-3 text-xs border rounded-lg font-medium transition-all text-center flex items-center justify-center gap-1.5 active:scale-95"
                  :class="
                    (agentForm.engine_config?.default_markdown_theme || 'default') === themeOpt.value
                      ? 'bg-blue-50 border-blue-200 text-blue-600 shadow-sm font-bold'
                      : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                  "
                >
                  <span>{{ themeOpt.emoji }}</span>
                  <span>{{ themeOpt.label }}</span>
                </button>
              </div>
              <label class="mt-4 flex cursor-pointer items-center justify-between rounded-lg border border-gray-100 bg-gray-50/70 px-3 py-2.5">
                <span>
                  <span class="block text-xs font-semibold text-gray-700">隐藏 AI 消息外框</span>
                  <span class="mt-0.5 block text-[10px] text-gray-400">仅隐藏消息气泡边框，Markdown 表格边框保留</span>
                </span>
                <span class="relative inline-flex h-5 w-9 items-center rounded-full transition-colors" :class="engineConfig.hide_message_border ? 'bg-primary' : 'bg-gray-300'">
                  <input v-model="engineConfig.hide_message_border" type="checkbox" class="sr-only" />
                  <span class="h-4 w-4 rounded-full bg-white shadow-sm transition-transform" :class="engineConfig.hide_message_border ? 'translate-x-4' : 'translate-x-0.5'"></span>
                </span>
              </label>
            </div>
          </div>

          <!-- Model -->
          <div v-else-if="versionConfigStep === 'model'" class="space-y-4 max-w-3xl">
            <p class="text-sm text-gray-500">
              配置编排与合成模型。编排负责推理与工具调用，合成负责整合结果并组织回复。
            </p>
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div class="rounded-xl border border-gray-200 bg-white p-4 shadow-sm border-l-4 border-l-blue-500">
                <label class="block text-sm font-bold text-gray-900 mb-1">编排模型 (Orchestrator)</label>
                <p class="text-[11px] text-gray-500 mb-3 leading-relaxed">
                  负责任务拆解、逻辑推理及工具调用
                </p>
                <ModelSelect
                  :model-value="versionForm.model_name || ''"
                  :models="llmModels()"
                  :disabled="!canEditVersion"
                  placeholder="选择编排模型..."
                  disabled-hint="当前版本不可编辑，无法切换模型"
                  @update:model-value="setOrchestratorModel"
                />
                <div class="mt-3">
                  <div class="flex justify-between items-center mb-1">
                    <label class="text-[11px] font-medium text-gray-600">采样温度 {{ versionForm.temperature }}</label>
                    <div class="flex gap-1">
                      <button type="button" @click="emit('setOrchestratorTemperature', 0)" class="temp-preset">精确</button>
                      <button type="button" @click="emit('setOrchestratorTemperature', 0.5)" class="temp-preset">平衡</button>
                      <button type="button" @click="emit('setOrchestratorTemperature', 0.8)" class="temp-preset">灵活</button>
                    </div>
                  </div>
                  <input
                    type="range" min="0" max="2" step="0.1"
                    :value="versionForm.temperature"
                    @input="versionForm.temperature = Number(($event.target as HTMLInputElement).value)"
                    :disabled="!canEditVersion"
                    class="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
                  />
                  <p class="mt-2 text-[11px] leading-4 text-gray-500">
                    当前 {{ formatTemperature(versionForm.temperature) }}：{{ getTemperatureGuidance(versionForm.temperature) }}
                  </p>
                  <p v-if="orchestratorTemperatureMismatch" class="mt-2 text-[11px] leading-4 text-amber-600">
                    当前版本温度 {{ formatTemperature(versionForm.temperature) }}，与模型配置温度 {{ formatTemperature(modelTemperatureForVersion) }} 不一致；版本配置将以当前值为准。
                  </p>
                  <p v-if="orchestratorTemperatureAboveOne" class="mt-2 text-[11px] leading-4 text-amber-600">
                    温度大于 1，请确认官方模型文档是否支持该范围；部分模型可能不支持或忽略该参数。
                  </p>
                </div>
              </div>

              <div class="rounded-xl border border-gray-200 bg-white p-4 shadow-sm border-l-4 border-l-emerald-500">
                <div class="flex justify-between items-center mb-1">
                  <label class="block text-sm font-bold text-gray-900">合成模型 (Synthesizer)</label>
                  <span class="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">可选</span>
                </div>
                <p class="text-[11px] text-gray-500 mb-3 leading-relaxed">
                  负责整合工具结果并组织语言回复，留空则跟随编排模型
                </p>
                <ModelSelect
                  :model-value="versionForm.synthesis_model_name || ''"
                  :models="llmModels()"
                  :disabled="!canEditVersion"
                  allow-empty
                  empty-label="跟随编排模型"
                  placeholder="跟随编排模型"
                  accent="emerald"
                  disabled-hint="当前版本不可编辑，无法切换模型"
                  @update:model-value="versionForm.synthesis_model_name = $event"
                />
                <div v-if="versionForm.synthesis_model_name" class="mt-3">
                  <div class="flex justify-between items-center mb-1">
                    <label class="text-[11px] font-medium text-gray-600">采样温度 {{ versionForm.synthesis_temperature }}</label>
                    <div class="flex gap-1">
                      <button type="button" @click="emit('setSynthesisTemperature', 0.3)" class="temp-preset">严谨</button>
                      <button type="button" @click="emit('setSynthesisTemperature', 0.7)" class="temp-preset">平衡</button>
                      <button type="button" @click="emit('setSynthesisTemperature', 1.2)" class="temp-preset">发散</button>
                    </div>
                  </div>
                  <input
                    type="range" min="0" max="2" step="0.1"
                    :value="versionForm.synthesis_temperature"
                    @input="versionForm.synthesis_temperature = Number(($event.target as HTMLInputElement).value)"
                    :disabled="!canEditVersion"
                    class="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-emerald-600"
                  />
                  <p class="mt-2 text-[11px] leading-4 text-gray-500">
                    当前 {{ formatTemperature(versionForm.synthesis_temperature) }}：{{ getTemperatureGuidance(versionForm.synthesis_temperature) }}
                  </p>
                  <p v-if="Number(versionForm.synthesis_temperature) > 1" class="mt-2 text-[11px] leading-4 text-amber-600">
                    温度大于 1，请确认官方模型文档是否支持该范围；部分模型可能不支持或忽略该参数。
                  </p>
                </div>
              </div>
            </div>

            <div class="rounded-lg bg-gray-50 border border-gray-200 px-4 py-3 text-xs text-gray-600">
              <span class="font-semibold text-gray-700">当前策略：</span>
              {{ getModelDisplayName(versionForm.model_name) }} @ {{ versionForm.temperature }}
              <span class="text-gray-400 mx-1">→</span>
              {{ versionForm.synthesis_model_name ? getModelDisplayName(versionForm.synthesis_model_name) + ' @ ' + versionForm.synthesis_temperature : '跟随编排模型' }}
            </div>

            <!-- 是否允许用户在对话输入框手动切换模型（版本级开关） -->
            <div class="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-gray-200 bg-gray-50/70 px-4 py-3">
              <div class="min-w-0">
                <div class="text-sm font-bold text-gray-800">允许在输入框手动切换模型</div>
                <p class="mt-1 text-[11px] leading-relaxed text-gray-500">
                  默认开启。关闭后，用户在对话输入框中看不到模型切换入口，只能使用本版本配置的编排模型。
                </p>
                <p class="mt-1 text-[11px] text-gray-400">历史版本未配置时同样按「允许切换」处理。</p>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-xs font-medium text-gray-600">{{ allowModelSwitch ? '允许切换' : '禁止切换' }}</span>
                <button
                  type="button"
                  role="switch"
                  :aria-checked="allowModelSwitch"
                  :disabled="!canEditVersion"
                  aria-label="是否允许在输入框手动切换模型"
                  @click="allowModelSwitch = !allowModelSwitch"
                  class="relative inline-flex h-6 w-11 flex-shrink-0 rounded-full transition-colors"
                  :class="[
                    allowModelSwitch ? 'bg-primary' : 'bg-gray-300',
                    canEditVersion ? 'cursor-pointer' : 'opacity-60 cursor-not-allowed'
                  ]"
                >
                  <span
                    class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition mt-0.5"
                    :class="allowModelSwitch ? 'translate-x-5 ml-0.5' : 'translate-x-0.5'"
                  />
                </button>
              </div>
            </div>
          </div>

          <!-- Step 2: Tools -->
          <div v-else-if="versionConfigStep === 'tools'" class="flex flex-col min-h-[calc(100vh-280px)]">
            <div
              v-if="showKnowledgeBaseToolsGuidance"
              class="mb-4 flex-shrink-0 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-amber-950"
            >
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="text-sm font-semibold">知识库助手还需完成以下配置，才能进入下一步</div>
                  <ul class="mt-2 space-y-1.5 text-xs leading-5 text-amber-900">
                    <li class="flex items-start gap-2">
                      <span :class="knowledgeBaseToolsStepIssues?.missingTool ? 'text-amber-600' : 'text-emerald-600'">
                        {{ knowledgeBaseToolsStepIssues?.missingTool ? '○' : '✓' }}
                      </span>
                      <span>
                        选择 <span class="font-mono font-semibold">search_knowledge_base</span> 检索工具
                        <span v-if="knowledgeBaseToolsStepIssues?.missingTool" class="text-amber-700">（在下方系统工具中勾选）</span>
                      </span>
                    </li>
                    <li class="flex items-start gap-2">
                      <span :class="knowledgeBaseToolsStepIssues?.missingBinding ? 'text-amber-600' : 'text-emerald-600'">
                        {{ knowledgeBaseToolsStepIssues?.missingBinding ? '○' : '✓' }}
                      </span>
                      <span>
                        为检索工具绑定至少 1 个知识库
                        <span v-if="knowledgeBaseToolsStepIssues?.missingBinding" class="text-amber-700">
                          （点击工具卡片上的 <span class="font-semibold">知识库</span> 图标选择）
                        </span>
                        <span v-else class="text-emerald-700">（已绑定 {{ knowledgeBaseToolsStepIssues?.datasetCount }} 个）</span>
                      </span>
                    </li>
                  </ul>
                </div>
                <button
                  v-if="!knowledgeBaseToolsStepIssues?.missingTool && knowledgeBaseToolsStepIssues?.missingBinding && (selectedAgent?.engine_type || agentForm.engine_type) === 'LOCAL'"
                  type="button"
                  @click="emit('openRagSelector', 'dataset', 'agent_kb_immediate')"
                  class="shrink-0 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700"
                >
                  去绑定知识库
                </button>
              </div>
            </div>
            <div class="mb-4 flex-shrink-0 rounded-xl border border-blue-100 bg-blue-50/50 px-4 py-3">
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="text-sm font-bold text-gray-800">智能体版本工具调用超时时间</div>
                  <p class="mt-1 text-[11px] leading-relaxed text-gray-500">
                    默认使用全局配置；启用后，以当前智能体版本配置为准，不再与全局或工具配置取最大值。
                  </p>
                  <p class="mt-1 text-[11px] text-gray-400">当前全局配置：{{ globalAgentToolcallTimeout }} 秒（范围 1–3600）</p>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-xs font-medium text-gray-600">
                    {{ isAgentVersionToolcallTimeoutEnabled ? '使用智能体专属配置' : '跟随全局配置' }}
                  </span>
                  <button
                    type="button"
                    role="switch"
                    :aria-checked="isAgentVersionToolcallTimeoutEnabled"
                    :disabled="!canEditVersion"
                    @click="setAgentVersionToolcallTimeoutEnabled(!isAgentVersionToolcallTimeoutEnabled)"
                    class="relative inline-flex h-6 w-11 flex-shrink-0 rounded-full transition-colors"
                    :class="[
                      isAgentVersionToolcallTimeoutEnabled ? 'bg-primary' : 'bg-gray-300',
                      canEditVersion ? 'cursor-pointer' : 'opacity-60 cursor-not-allowed'
                    ]"
                  >
                    <span
                      class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition mt-0.5"
                      :class="isAgentVersionToolcallTimeoutEnabled ? 'translate-x-5 ml-0.5' : 'translate-x-0.5'"
                    />
                  </button>
                </div>
              </div>
              <div v-if="isAgentVersionToolcallTimeoutEnabled" class="mt-3 flex items-center gap-2">
                <button
                  type="button"
                  aria-label="减少智能体工具调用超时时间"
                  :disabled="!canEditVersion || getAgentVersionToolcallTimeoutValue() <= AGENT_VERSION_TOOLCALL_TIMEOUT_MIN"
                  @click="adjustAgentVersionToolcallTimeout(-1)"
                  class="flex h-8 w-8 items-center justify-center rounded-lg border border-gray-200 bg-white text-lg leading-none text-gray-600 hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                >−</button>
                <input
                  type="number"
                  inputmode="numeric"
                  min="1"
                  max="86400"
                  step="1"
                  :value="versionForm.toolcall_timeout_seconds"
                  :disabled="!canEditVersion"
                  @keydown="handleAgentVersionToolcallTimeoutKeydown"
                  @input="handleAgentVersionToolcallTimeoutInput"
                  @blur="normalizeAgentVersionToolcallTimeoutInput"
                  aria-label="智能体版本工具调用超时时间（秒）"
                  class="h-8 w-32 rounded-lg border border-gray-200 bg-white px-3 text-center text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:bg-gray-100"
                />
                <button
                  type="button"
                  aria-label="增加智能体工具调用超时时间"
                  :disabled="!canEditVersion || getAgentVersionToolcallTimeoutValue() >= AGENT_VERSION_TOOLCALL_TIMEOUT_MAX"
                  @click="adjustAgentVersionToolcallTimeout(1)"
                  class="flex h-8 w-8 items-center justify-center rounded-lg border border-gray-200 bg-white text-lg leading-none text-gray-600 hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                >+</button>
                <span class="text-xs text-gray-500">秒（范围 1–86400）</span>
              </div>
              <p v-else class="mt-2 text-[11px] text-gray-400">当前跟随全局配置（当前全局值 {{ globalAgentToolcallTimeout }} 秒）</p>
            </div>
            <div class="flex flex-wrap items-center justify-between gap-3 mb-4 flex-shrink-0">
              <div class="flex items-center gap-2 flex-1 min-w-[200px]">
                <div class="relative flex-1 max-w-md">
                  <svg class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  <input
                    :value="toolSearchQuery"
                    @input="emit('update:toolSearchQuery', ($event.target as HTMLInputElement).value)"
                    type="search"
                    placeholder="搜索工具名称或描述..."
                    class="w-full pl-8 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                  />
                </div>
                <span class="text-xs font-medium text-primary bg-primary/10 px-2.5 py-1 rounded-full whitespace-nowrap">
                  已选 {{ selectedToolsCount }}/{{ allAvailableToolsCount + mcpToolsCount }}
                </span>
              </div>
              <!-- 分组批量展开 / 折叠：图标按钮 + 细竖线，避免与右侧页签组形成两个并列的「盒子」 -->
              <div
                v-if="toolTab !== 'skills'"
                class="flex items-center border-r border-gray-200 pr-1.5 mr-0.5"
              >
                <button
                  type="button"
                  @click="emitBulkExpandGroups"
                  aria-label="展开当前页签下的全部分组"
                  title="展开当前页签下的全部分组"
                  class="p-1.5 rounded-md text-gray-400 hover:text-primary hover:bg-gray-100 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                >
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 13l-7 7-7-7m14-8l-7 7-7-7" />
                  </svg>
                </button>
                <button
                  type="button"
                  @click="emitBulkCollapseGroups"
                  aria-label="折叠当前页签下的全部分组"
                  title="折叠当前页签下的全部分组"
                  class="p-1.5 rounded-md text-gray-400 hover:text-primary hover:bg-gray-100 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                >
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 11l7-7 7 7M5 19l7-7 7 7" />
                  </svg>
                </button>
              </div>
              <div class="flex bg-gray-100 p-0.5 rounded-lg text-xs">
                <button
                  type="button"
                  @click="emit('update:toolTab', 'static')"
                  class="px-3 py-1.5 rounded-md transition-all font-medium"
                  :class="toolTab === 'static' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                >系统工具 ({{ selectedStaticToolsCount }}/{{ allAvailableToolsCount }})</button>
                <button
                  type="button"
                  @click="emit('update:toolTab', 'mcp')"
                  class="px-3 py-1.5 rounded-md transition-all font-medium"
                  :class="toolTab === 'mcp' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                >MCP 工具 ({{ selectedMcpToolsCount }}/{{ mcpToolsCount }})<span
                  v-if="staleToolRefs.length"
                  class="ml-1.5 inline-flex items-center rounded-full bg-red-100 px-1.5 py-0.5 text-[9px] font-bold text-red-600 dark:bg-red-900/40 dark:text-red-300"
                  :title="`${staleToolRefs.length} 项失效引用待移除`"
                >{{ staleToolRefs.length }} 项失效</span></button>
                <button
                  type="button"
                  @click="emit('update:toolTab', 'skills')"
                  class="px-3 py-1.5 rounded-md transition-all font-medium"
                  :class="toolTab === 'skills' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                >Skills ({{ versionForm.skills_custom ? selectedSkillsCount : enabledGlobalSkillsCount }}/{{ enabledGlobalSkillsCount }})</button>
              </div>
            </div>

            <!-- Static Tools -->
            <div v-if="toolTab === 'static'" class="flex-1 overflow-y-auto space-y-3 pr-1 version-editor-scroll">
              <div
                v-for="group in filteredGroupedTools"
                :key="group.label"
                class="rounded-lg border overflow-hidden transition-colors"
                :class="staticGroupState(group.tools) === 'all' ? 'border-primary/40' : 'border-gray-200'"
              >
                <div
                  class="flex items-center justify-between border-b py-2 px-3 border-l-4 transition-colors"
                  :class="staticGroupState(group.tools) === 'all'
                    ? 'border-b-primary/20 border-l-primary bg-primary/5'
                    : staticGroupState(group.tools) === 'some'
                      ? 'border-b-gray-100 border-l-primary/40 bg-gray-50'
                      : 'border-b-gray-100 border-l-transparent bg-gray-50'"
                >
                  <button
                    type="button"
                    @click="emit('toggleStaticGroupCollapse', group.label)"
                    :disabled="isSearching"
                    :title="isSearching ? '搜索状态下分组自动展开，清空搜索后可折叠' : ''"
                    :class="isSearching ? 'cursor-default' : ''"
                    class="flex items-center gap-2 min-w-0 flex-1 text-left"
                  >
                    <svg class="w-3.5 h-3.5 text-gray-400 transition-transform" :class="{ 'rotate-90': !isStaticGroupCollapsed(group.label) || isSearching }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                    <span class="text-xs">{{ group.icon }}</span>
                    <span class="text-xs font-bold text-gray-700">{{ group.label }}</span>
                    <span
                      class="shrink-0 rounded px-1.5 py-0.5 text-[10px] transition-colors"
                      :class="staticGroupState(group.tools) === 'all'
                        ? 'bg-primary font-bold text-white'
                        : staticGroupState(group.tools) === 'some'
                          ? 'bg-primary/10 font-semibold text-primary'
                          : 'text-gray-400'"
                    >({{ getStaticGroupSelectedCount(group.tools) }}/{{ group.tools.length }})</span>
                  </button>
                  <button
                    v-if="canEditVersion"
                    type="button"
                    @click.stop="emit('toggleSelectAllStatic', group.label)"
                    class="text-[10px] font-bold text-primary hover:text-primary-dark ml-2 flex-shrink-0"
                  >
                    {{ isAllStaticGroupSelected(group.label) ? '取消全选' : '全选' }}
                  </button>
                </div>
                <div v-show="!isStaticGroupCollapsed(group.label) || isSearching" class="grid grid-cols-1 sm:grid-cols-2 gap-2 p-3">
                  <div
                    v-for="tool in group.tools"
                    :key="tool.name"
                    @click="emit('toggleTool', tool.name)"
                    class="tool-card"
                    :class="[
                      isToolSelected(tool.name) ? 'tool-card--selected' : '',
                      !canEditVersion ? 'opacity-90 cursor-default' : 'cursor-pointer'
                    ]"
                  >
                    <div class="tool-checkbox" :class="isToolSelected(tool.name) ? 'tool-checkbox--on' : ''">
                      <svg v-if="isToolSelected(tool.name)" class="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="4" d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                    <div class="flex-1 min-w-0">
                      <div class="text-[11px] font-bold font-mono text-gray-800 flex items-center gap-1 flex-wrap">
                        {{ tool.name }}
                        <span v-if="tool.isSystem" class="text-[9px] bg-gray-100 text-gray-500 px-1 rounded font-sans font-normal">系统</span>
                      </div>
                      <div class="text-[10px] text-gray-400 mt-0.5 line-clamp-2">{{ tool.description }}</div>
                      <div v-if="isToolSelected(tool.name)" class="flex gap-1 mt-1.5" @click.stop>
                        <button type="button" @click="emit('openToolRuntimeConfig', tool.name)" class="tool-action-btn" title="配置">
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
                        </button>
                        <button
                          v-if="tool.name === 'get_dataset_schema'"
                          type="button"
                          @click="emit('openMetadataDatasetBinding', tool.name)"
                          class="tool-action-btn"
                          :class="hasToolMetadataDatasetBinding(tool.name) ? 'tool-action-btn--active' : ''"
                          title="绑定数据集"
                        >
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7c0 1.657 3.582 3 8 3s8-1.343 8-3-3.582-3-8-3-8 1.343-8 3zm0 0v5c0 1.657 3.582 3 8 3s8-1.343 8-3V7M4 12v5c0 1.657 3.582 3 8 3s8-1.343 8-3v-5" /></svg>
                        </button>
                        <button v-if="tool.name === 'send_dingtalk_message'" type="button" @click="emit('openDingTalkConfig', tool.name)" class="tool-action-btn" title="钉钉">
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5.882V19.24a1.76 1.72 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" /></svg>
                        </button>
                        <button v-if="tool.name === 'send_email'" type="button" @click="emit('openEmailConfig', tool.name)" class="tool-action-btn" title="邮件">
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
                        </button>
                        <button v-if="tool.name === 'send_wechat_work_message'" type="button" @click="emit('openWeChatWorkConfig', tool.name)" class="tool-action-btn" title="企微">
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
                        </button>
                        <button v-if="tool.name === 'send_feishu_message'" type="button" @click="emit('openFeishuConfig', tool.name)" class="tool-action-btn" title="飞书">
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" /></svg>
                        </button>
                        <button
                          v-if="tool.name === 'search_knowledge_base' && (selectedAgent?.engine_type || agentForm.engine_type) === 'LOCAL'"
                          type="button"
                          @click="emit('openRagSelector', 'dataset', 'agent_kb_immediate')"
                          class="tool-action-btn"
                          :class="knowledgeBaseToolsStepIssues && !knowledgeBaseToolsStepIssues.missingBinding
                            ? 'tool-action-btn--active'
                            : (showKnowledgeBaseToolsGuidance ? 'tool-action-btn--attention' : '')"
                          title="绑定知识库"
                        >
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" /></svg>
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- MCP Tools -->
            <div v-else-if="toolTab === 'mcp'" class="flex-1 overflow-y-auto space-y-3 pr-1 version-editor-scroll">
              <!--
                失效引用：工具选择器只渲染「当前可见且已发布」的工具，服务删除/禁用后
                旧引用既不会显示也不会自动消失，这里是用户唯一的解绑入口。
              -->
              <div
                v-if="staleToolRefs.length"
                class="rounded-lg border border-amber-200 bg-amber-50/70 p-3 dark:border-amber-900/50 dark:bg-amber-950/30"
              >
                <div class="flex items-start justify-between gap-2">
                  <div class="min-w-0">
                    <p class="flex items-center gap-1.5 text-xs font-bold text-amber-800 dark:text-amber-300">
                      <svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <path d="M10.29 3.86a2 2 0 0 1 3.42 0l8.6 14.86A2 2 0 0 1 20.6 21H3.4a2 2 0 0 1-1.71-2.28z" />
                        <path d="M12 9v4" />
                        <path d="M12 17h.01" />
                      </svg>
                      {{ staleToolRefs.length }} 项已失效的工具引用
                    </p>
                    <p class="mt-1 text-[11px] leading-relaxed text-amber-700 dark:text-amber-300/80">
                      这些工具已不可用（MCP 服务被删除、禁用，或工具已下线），运行时拿不到，勾选列表里也不会再显示。移除后请保存版本。
                    </p>
                    <p v-if="!canEditVersion" class="mt-1 text-[10px] font-medium text-amber-700 dark:text-amber-300">
                      当前版本不可编辑（已发布或只读）。请编辑草稿版本、移除后重新发布，改动才会生效。
                    </p>
                  </div>
                  <div class="flex shrink-0 items-center gap-1">
                    <button
                      v-if="canEditVersion"
                      type="button"
                      class="rounded-md bg-amber-600 px-2 py-1 text-[11px] font-semibold text-white transition-colors hover:bg-amber-700"
                      @click="emit('removeStaleToolRefs', staleToolRefs.map((item) => item.name))"
                    >全部移除</button>
                    <button
                      type="button"
                      class="rounded-md border border-amber-300 px-2 py-1 text-[11px] font-medium text-amber-700 transition-colors hover:bg-amber-100 dark:border-amber-800 dark:text-amber-300 dark:hover:bg-amber-900/40"
                      @click="emit('refreshStaleToolRefs')"
                    >重新检测</button>
                  </div>
                </div>
                <ul class="mt-2 max-h-40 space-y-1 overflow-y-auto">
                  <li
                    v-for="item in staleToolRefs"
                    :key="item.name"
                    class="flex items-center gap-2 rounded-md bg-white/80 px-2 py-1.5 dark:bg-gray-800/80"
                  >
                    <span class="min-w-0 flex-1">
                      <span class="block truncate font-mono text-[11px] text-gray-700 dark:text-gray-200" :title="item.name">{{ item.name }}</span>
                      <span class="block text-[10px] text-amber-700 dark:text-amber-300/80">{{ item.reason_label }}<template v-if="item.server_name"> · {{ item.server_name }}</template></span>
                    </span>
                    <button
                      v-if="canEditVersion"
                      type="button"
                      class="shrink-0 rounded-md px-2 py-1 text-[11px] font-semibold text-amber-700 transition-colors hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-900/40"
                      @click="emit('removeStaleToolRefs', [item.name])"
                    >移除</button>
                  </li>
                </ul>
              </div>
              <p
                v-else-if="staleToolRefsError"
                class="flex items-center justify-between gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-[11px] text-gray-500 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-400"
              >
                <span class="min-w-0 truncate">{{ staleToolRefsError }}</span>
                <button type="button" class="shrink-0 font-semibold text-primary hover:underline" @click="emit('refreshStaleToolRefs')">重试</button>
              </p>

              <!-- MCP Scope Tab 选项卡：平台 MCP / 我的 MCP -->
              <div class="flex items-center justify-between border-b border-indigo-100 dark:border-gray-800 pb-2 mb-1 sticky top-0 bg-white dark:bg-gray-900 z-10">
                <div class="flex items-center gap-1.5 bg-gray-100 dark:bg-gray-800 p-0.5 rounded-lg text-xs">
                  <button
                    type="button"
                    @click="mcpSubTab = 'global'"
                    class="px-3 py-1 font-semibold rounded-md transition-all flex items-center gap-1.5"
                    :class="mcpSubTab === 'global' ? 'bg-white shadow-sm text-indigo-600 dark:bg-gray-700 dark:text-indigo-300' : 'text-gray-500 hover:text-gray-700 dark:text-gray-400'"
                  >
                    <span>平台 MCP</span>
                    <span class="px-1.5 py-0.2 text-[9px] rounded-full font-mono" :class="mcpSubTab === 'global' ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-200 text-gray-600'">
                      {{ globalMcpToolsCount }}
                    </span>
                  </button>
                  <button
                    type="button"
                    @click="mcpSubTab = 'personal'"
                    class="px-3 py-1 font-semibold rounded-md transition-all flex items-center gap-1.5"
                    :class="mcpSubTab === 'personal' ? 'bg-white shadow-sm text-emerald-600 dark:bg-gray-700 dark:text-emerald-300' : 'text-gray-500 hover:text-gray-700 dark:text-gray-400'"
                  >
                    <span>我的 MCP</span>
                    <span class="px-1.5 py-0.2 text-[9px] rounded-full font-mono" :class="mcpSubTab === 'personal' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-200 text-gray-600'">
                      {{ personalMcpToolsCount }}
                    </span>
                  </button>
                </div>
              </div>

              <div v-if="currentScopeGroupedMcpToolsCount === 0" class="p-12 text-center text-gray-400 text-sm">
                {{ mcpSubTab === 'personal' ? '暂无个人的 MCP 工具，请前往 MCP 工具集 配置。' : '暂无已发布的平台 MCP 工具，请前往 MCP 工具集 配置。' }}
              </div>
              <div
                v-else
                v-for="(tools, serverName) in currentScopeGroupedMcpTools"
                :key="serverName"
                class="rounded-lg border overflow-hidden transition-colors"
                :class="mcpGroupState(tools) === 'all'
                  ? 'border-indigo-300'
                  : mcpGroupState(tools) === 'some'
                    ? 'border-indigo-200'
                    : 'border-indigo-100'"
              >
                <div
                  class="flex items-center justify-between border-b py-2 px-3 border-l-4 transition-colors"
                  :class="mcpGroupState(tools) === 'all'
                    ? 'border-b-indigo-200 border-l-indigo-500 bg-indigo-100'
                    : mcpGroupState(tools) === 'some'
                      ? 'border-b-indigo-100 border-l-indigo-300 bg-indigo-50/80'
                      : 'border-b-indigo-100/50 border-l-transparent bg-indigo-50/60'"
                >
                  <button type="button" @click="emit('toggleMcpGroupCollapse', serverName)" :disabled="isSearching" :title="isSearching ? '搜索状态下分组自动展开，清空搜索后可折叠' : ''" :class="isSearching ? 'cursor-default' : ''" class="flex items-center gap-1.5 min-w-0 flex-1 text-left">
                    <svg class="w-3.5 h-3.5 text-indigo-400 transition-transform shrink-0" :class="{ 'rotate-90': !isMcpGroupCollapsed(serverName) || isSearching }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                    <span class="min-w-0 flex-1">
                      <span
                        class="text-[10px] font-bold uppercase truncate block"
                        :class="mcpGroupState(tools) === 'all' ? 'text-indigo-900' : 'text-indigo-700'"
                        :title="tools[0]?.server_remark ? `${serverName} · ${tools[0].server_remark}` : String(serverName)"
                      >{{ serverName }}</span>
                      <span
                        v-if="tools[0]?.server_remark"
                        class="text-[10px] text-indigo-400/90 font-normal normal-case truncate block leading-snug mt-0.5"
                      >{{ tools[0].server_remark }}</span>
                    </span>
                    <span v-if="tools[0]?.scope === 'personal'" class="ml-1.5 px-1.5 py-0.2 text-[9px] rounded font-medium bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300 flex-shrink-0">
                      个人
                    </span>
                    <span v-else class="ml-1.5 px-1.5 py-0.2 text-[9px] rounded font-medium bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300 flex-shrink-0">
                      平台
                    </span>
                    <span
                      class="shrink-0 rounded px-1.5 py-0.5 text-[10px] transition-colors"
                      :class="mcpGroupState(tools) === 'all'
                        ? 'bg-indigo-600 font-bold text-white'
                        : mcpGroupState(tools) === 'some'
                          ? 'bg-indigo-100 font-semibold text-indigo-700'
                          : 'text-indigo-400'"
                    >({{ getMcpGroupSelectedCount(tools) }}/{{ tools.length }})</span>
                  </button>
                  <button v-if="canEditVersion" type="button" @click.stop="emit('toggleSelectAllMcp', serverName, tools)" class="text-[10px] font-bold text-indigo-600 hover:text-indigo-800 ml-2 flex-shrink-0">
                    {{ isAllMcpSelected(serverName, tools) ? '取消全选' : '一键全选' }}
                  </button>
                </div>
                <div v-show="!isMcpGroupCollapsed(serverName) || isSearching" class="grid grid-cols-1 sm:grid-cols-2 gap-2 p-3">
                  <div
                    v-for="tool in tools"
                    :key="tool.id"
                    @click="emit('toggleTool', tool.name)"
                    class="tool-card"
                    :class="[isToolSelected(tool.name) ? 'tool-card--selected-indigo' : '', !canEditVersion ? 'opacity-90 cursor-default' : 'cursor-pointer']"
                  >
                    <div class="tool-checkbox" :class="isToolSelected(tool.name) ? 'tool-checkbox--on-indigo' : ''">
                      <svg v-if="isToolSelected(tool.name)" class="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="4" d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                    <div class="flex-1 min-w-0">
                      <div
                        class="text-[11px] font-bold font-mono text-gray-800 truncate"
                        :title="tool.name"
                      >
                        {{ mcpToolDisplayName(tool.name, serverName) }}
                      </div>
                      <div class="text-[10px] text-gray-400 mt-0.5 line-clamp-2">{{ tool.description }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Skills -->
            <div v-else class="flex-1 overflow-y-auto space-y-3 pr-1 version-editor-scroll">
              <div class="rounded-lg border border-emerald-100 bg-emerald-50/40 px-4 py-3 flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="text-sm font-bold text-gray-800">自定义 Skills</div>
                  <p class="text-[11px] text-gray-500 mt-1 leading-relaxed">
                    关闭时加载全部已启用公共技能 + 当前用户个人技能。开启后仅从公共技能中勾选；运行时仍会附带当前用户个人技能。
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  :aria-checked="!!versionForm.skills_custom"
                  :disabled="!canEditVersion"
                  @click="emit('setSkillsCustom', !versionForm.skills_custom)"
                  class="relative inline-flex h-6 w-11 flex-shrink-0 rounded-full transition-colors"
                  :class="[
                    versionForm.skills_custom ? 'bg-primary' : 'bg-gray-300',
                    canEditVersion ? 'cursor-pointer' : 'opacity-60 cursor-not-allowed'
                  ]"
                >
                  <span
                    class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition mt-0.5"
                    :class="versionForm.skills_custom ? 'translate-x-5 ml-0.5' : 'translate-x-0.5'"
                  />
                </button>
              </div>

              <div v-if="!versionForm.skills_custom" class="p-10 text-center text-gray-400 text-sm border border-dashed border-gray-200 rounded-xl">
                当前使用默认策略：全部已启用公共 Skills + 个人 Skills
              </div>
              <template v-else>
                <div class="flex items-center justify-between gap-2">
                  <span class="text-xs font-medium text-primary bg-primary/10 px-2.5 py-1 rounded-full">
                    已选 {{ selectedSkillsCount }}/{{ enabledGlobalSkillsCount }} 个公共技能
                  </span>
                  <span class="text-[10px] text-amber-600" v-if="selectedSkillsCount === 0">至少选择 1 个公共技能</span>
                </div>
                <div v-if="enabledGlobalSkillsCount === 0" class="p-12 text-center text-gray-400 text-sm">
                  暂无已启用的公共技能，请前往技能工作台启用。
                </div>
                <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <div
                    v-for="skill in filteredEnabledSkills"
                    :key="skill.id"
                    @click="emit('toggleSkill', skill.id)"
                    class="tool-card"
                    :class="[
                      isSkillSelected(skill.id) ? 'tool-card--selected' : '',
                      !canEditVersion ? 'opacity-90 cursor-default' : 'cursor-pointer'
                    ]"
                  >
                    <div class="tool-checkbox" :class="isSkillSelected(skill.id) ? 'tool-checkbox--on' : ''">
                      <svg v-if="isSkillSelected(skill.id)" class="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="4" d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                    <div class="flex-1 min-w-0">
                      <div class="text-[11px] font-bold font-mono text-gray-800 flex items-center gap-1 flex-wrap">
                        {{ skill.name || skill.id }}
                        <span class="text-[9px] bg-emerald-50 text-emerald-600 px-1 rounded font-sans font-normal">公共</span>
                      </div>
                      <div class="text-[10px] text-gray-400 mt-0.5 font-mono">{{ skill.id }}</div>
                      <div class="text-[10px] text-gray-400 mt-0.5 line-clamp-2">{{ skill.description || '暂无描述' }}</div>
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>

          <!-- Step 3: Prompt -->
          <div v-else-if="versionConfigStep === 'prompt'" class="flex flex-col h-[calc(100vh-280px)] min-h-[400px]">
            <div class="flex items-center justify-between mb-3 flex-shrink-0">
              <div>
                <h3 class="text-sm font-bold text-gray-900">核心提示词 (System Prompt)</h3>
                <p class="text-[11px] text-gray-500 mt-0.5">定义智能体人格、输出风格与工具使用规范</p>
              </div>
              <button type="button" @click="emit('copySystemPrompt')" class="text-xs text-gray-500 hover:text-primary flex items-center gap-1 px-2 py-1 rounded-md hover:bg-gray-100">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>
                复制
              </button>
            </div>
            <div class="flex flex-col flex-1 min-h-0">
              <MarkdownEditor
                :model-value="versionForm.system_prompt"
                @update:model-value="versionForm.system_prompt = $event"
                @toast="(message, type) => emit('toast', message, type || 'info')"
                placeholder="你是一个..."
                fill
                enable-optimize
                optimize-endpoint="/api/portal/prompts/optimize/agent-editor"
                :require-optimize-permission="false"
                :disabled="!canEditVersion"
              />
            </div>
            <p class="text-[10px] text-gray-400 mt-2 flex-shrink-0">{{ promptCharCount }} 字符</p>
          </div>

          <!-- Step 4: Welcome cards (LOCAL versions only; parent omits this step otherwise) -->
          <div v-else-if="versionConfigStep === 'welcome'" class="space-y-5 max-w-3xl">
            <div class="flex items-start justify-between gap-4 rounded-2xl border border-gray-200 bg-gray-50 p-4">
              <div>
                <h3 class="text-sm font-bold text-gray-900">欢迎语设置</h3>
                <p class="mt-1 text-xs leading-5 text-gray-500">开启后替换聊天页顶部固定能力卡片；关闭时保留平台默认三卡片。</p>
              </div>
              <button type="button" :disabled="!canEditVersion" @click="toggleWelcomeCards" class="relative h-6 w-11 flex-shrink-0 rounded-full transition-colors" :class="welcomeConfig.enabled ? 'bg-primary' : 'bg-gray-300'">
                <span class="absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform" :class="welcomeConfig.enabled ? 'translate-x-5' : 'translate-x-0'"></span>
              </button>
            </div>
            <template v-if="welcomeConfig.enabled">
              <div class="flex rounded-lg bg-gray-100 p-1 text-xs font-semibold">
                <button type="button" :disabled="!canEditVersion" @click="setWelcomeMode('manual')" class="flex-1 rounded-md px-3 py-2" :class="welcomeConfig.mode === 'manual' ? 'bg-white text-primary shadow-sm' : 'text-gray-500'">人工编辑</button>
                <button type="button" :disabled="!canEditVersion" @click="setWelcomeMode('ai')" class="flex-1 rounded-md px-3 py-2" :class="welcomeConfig.mode === 'ai' ? 'bg-white text-primary shadow-sm' : 'text-gray-500'">随机自动推荐</button>
              </div>
              <div v-if="welcomeConfig.mode === 'ai'" class="rounded-xl border border-violet-100 bg-violet-50/60 p-4">
                <label class="mb-2 block text-xs font-bold text-violet-900">其他推荐要求（可选）</label>
                <textarea v-model="welcomeConfig.generation_requirement" :disabled="!canEditVersion" rows="2" placeholder="例如：优先推荐月度经营分析和异常排查问题" class="w-full resize-none rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-violet-200"></textarea>
                <p class="mt-3 text-xs leading-5 text-violet-700">每次打开欢迎页时自动推荐 3 张业务卡片；同一智能体在 5 分钟内复用同一组推荐。</p>
                <div class="mt-3 rounded-lg border border-violet-200/80 bg-white/70 px-3 py-2.5 text-xs leading-5 text-violet-800">
                  <p class="font-bold">默认推荐规则</p>
                  <ul class="mt-1 list-disc space-y-0.5 pl-4 text-violet-700">
                    <li>参考智能体名称、业务描述、系统提示词摘要和上述额外要求。</li>
                    <li>生成 3 个业务强相关、可直接点击执行的问题，并匹配合适图标、主标题和副标题。</li>
                    <li>缓存到期后才生成新一组；生成失败时回退平台默认三卡片。</li>
                  </ul>
                </div>
              </div>
              <div v-if="welcomeConfig.mode === 'manual'" class="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div v-for="(card, index) in welcomeConfig.cards" :key="index" class="rounded-xl border border-gray-200 bg-white p-3">
                  <select v-model="card.icon" :disabled="!canEditVersion" class="mb-2 w-full rounded-md border border-gray-200 px-2 py-1.5 text-xs">
                    <option v-for="icon in welcomeIcons" :key="icon.value" :value="icon.value">{{ icon.label }}图标</option>
                  </select>
                  <input v-model="card.title" :disabled="!canEditVersion" maxlength="40" placeholder="主标题" class="mb-2 w-full rounded-md border border-gray-200 px-2 py-1.5 text-xs" />
                  <input v-model="card.subtitle" :disabled="!canEditVersion" maxlength="100" placeholder="副标题" class="mb-2 w-full rounded-md border border-gray-200 px-2 py-1.5 text-xs" />
                  <textarea v-model="card.prompt" :disabled="!canEditVersion" rows="3" maxlength="300" placeholder="点击后发送的问题" class="w-full resize-none rounded-md border border-gray-200 px-2 py-1.5 text-xs"></textarea>
                </div>
              </div>
            </template>
          </div>

          <!-- Step 5: Review -->
          <div v-else class="space-y-4 max-w-2xl">
            <h3 class="text-sm font-bold text-gray-900">配置总览</h3>
            <div v-if="isCreatingAgent" class="summary-card">
              <div class="text-[10px] text-gray-400 uppercase font-bold mb-1">智能体</div>
              <div class="text-sm font-semibold text-gray-800">{{ agentForm.display_name }} · {{ agentTypes.find((item) => item.value === agentForm.agent_type)?.label }}</div>
              <div class="text-[10px] text-gray-500 mt-0.5 font-mono">{{ agentForm.name }}</div>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div class="summary-card">
                <div class="text-[10px] text-gray-400 uppercase font-bold mb-1">编排模型</div>
                <div class="text-sm font-semibold text-gray-800 truncate">{{ getModelDisplayName(versionForm.model_name) }}</div>
                <div class="text-[10px] text-gray-500 mt-0.5">温度 {{ versionForm.temperature }}</div>
              </div>
              <div class="summary-card">
                <div class="text-[10px] text-gray-400 uppercase font-bold mb-1">工具能力</div>
                <div class="text-sm font-semibold text-gray-800">{{ selectedToolsCount }}/{{ allAvailableToolsCount + mcpToolsCount }} 个已启用</div>
              </div>
              <div class="summary-card">
                <div class="text-[10px] text-gray-400 uppercase font-bold mb-1">Skills</div>
                <div class="text-sm font-semibold text-gray-800">
                  {{ versionForm.skills_custom ? `自定义 ${selectedSkillsCount}/${enabledGlobalSkillsCount}` : '全部公共 + 个人' }}
                </div>
              </div>
              <div class="summary-card">
                <div class="text-[10px] text-gray-400 uppercase font-bold mb-1">系统提示词</div>
                <div class="text-sm font-semibold text-gray-800">{{ promptCharCount }} 字符</div>
              </div>
            </div>

            <div v-if="versionForm.synthesis_model_name" class="summary-card">
              <div class="text-[10px] text-gray-400 uppercase font-bold mb-1">合成模型</div>
              <div class="text-sm text-gray-700">{{ getModelDisplayName(versionForm.synthesis_model_name) }} · 温度 {{ versionForm.synthesis_temperature }}</div>
            </div>

            <div>
              <label class="block text-sm font-bold text-gray-700 mb-2">版本变动说明</label>
              <textarea
                v-model="versionForm.comment"
                :disabled="!canEditVersion"
                placeholder="说明此版本做了哪些优化或改动..."
                rows="5"
                class="w-full text-sm border border-gray-200 rounded-xl focus:border-primary focus:ring-2 focus:ring-primary/20 resize-none p-3 bg-white outline-none disabled:bg-gray-100"
              ></textarea>
            </div>

            <!-- 新建流程的系统级说明放在最后一步：前四步还没有可发布内容，提前讲只会占位。
                 系统级只能走「发布为系统级 / 申请发布为系统级」审批，外部引擎不支持。 -->
            <div v-if="isCreatingAgent" class="rounded-xl border border-blue-100 bg-blue-50/50 p-4">
              <div class="flex items-start gap-2.5">
                <span class="mt-0.5 text-base leading-none">💡</span>
                <div class="space-y-1">
                  <p class="text-sm font-bold text-blue-900">温馨提示</p>
                  <p v-if="agentForm.engine_type === 'LOCAL'" class="text-xs leading-relaxed text-blue-900/70">
                    这里创建的是<strong class="text-blue-900">普通智能体</strong>，保存后即可手动对话。
                    若需要进入主助手的自动委派候选，请保存后走
                    <strong class="text-blue-900">「发布为系统级」</strong>或<strong class="text-blue-900">「申请发布为系统级」</strong>的
                    <strong class="text-blue-900">系统发布审批流程</strong>（要求至少已发布一个版本；每次上架/下架都会记录并通知相关人）。
                  </p>
                  <p v-else class="text-xs leading-relaxed text-blue-900/70">
                    外部引擎智能体固定为<strong class="text-blue-900">普通智能体</strong>，
                    <strong class="text-blue-900">不支持设为系统级</strong>——只有 NanZi Engine 有平台侧版本可发布。
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div class="px-6 py-4 border-t border-gray-200 bg-gray-50 flex items-center justify-between gap-3 flex-shrink-0">
          <div v-if="!canEditVersion" class="text-xs text-amber-700 bg-amber-50 px-3 py-1.5 rounded-lg border border-amber-100">
            <template v-if="selectedAgent?.can_edit_versions === false">只读模式：当前账号无编辑权限</template>
            <template v-else-if="versionForm.status === 'PUBLISHED'">当前为已发布版本，如需修改请克隆新版本</template>
            <template v-else>当前版本不可编辑</template>
          </div>
          <div v-else class="flex-1">
            <p v-if="externalCreationMissingFields.length > 0" class="text-xs font-medium text-amber-700">
              请先填写：{{ externalCreationMissingFields.join('、') }}
            </p>
            <p v-else-if="versionConfigIncompleteHint" class="text-xs font-medium text-amber-700">
              {{ versionConfigIncompleteHint }}
            </p>
          </div>

          <div class="flex items-center gap-2">
            <button type="button" @click="emit('close')" class="px-4 py-2 text-sm text-gray-500 hover:text-gray-700 font-medium">取消</button>
            <button v-if="!isFirstStep()" type="button" @click="emit('prevStep')" class="px-4 py-2 text-sm border border-gray-200 rounded-lg hover:bg-white font-medium text-gray-700">上一步</button>
            <button
              v-if="!isLastStep()"
              type="button"
              @click="emit('nextStep')"
              :disabled="!isCurrentStepComplete()"
              class="px-5 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark font-medium shadow-sm disabled:cursor-not-allowed disabled:bg-gray-300 disabled:shadow-none"
            >下一步</button>
            <button
              v-if="isLastStep() && canEditVersion && (!versionForm.id || versionForm.status === 'DRAFT') && !canPublishLocalVersion"
              type="button"
              :disabled="externalCreationMissingFields.length > 0"
              @click="emit('save')"
              class="px-6 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark font-medium shadow-sm disabled:cursor-not-allowed disabled:bg-gray-300 disabled:shadow-none"
            >{{ isCreatingAgent && agentForm.engine_type !== 'LOCAL' ? `创建 ${agentForm.engine_type === 'RAGFLOW' ? 'RAGFlow' : 'OpenClaw'} 智能体` : isCreatingAgent ? '创建智能体与 V1 草稿' : versionForm.id ? '更新草稿' : '保存为草稿' }}</button>
            <!-- 系统级所有者在这里只能存草稿，不给一句引导会在页脚卡住：
                 卡片上有说明，但用户进到编辑器里就看不到了 -->
            <span
              v-if="isLastStep() && canEditVersion && !canPublishLocalVersion && agentForm.engine_type === 'LOCAL'"
              class="max-w-[18rem] text-[11px] leading-snug text-gray-500"
            >
              系统级智能体的线上基线由管理员变更：保存草稿后到「配置与发布」里提交上线申请。
            </span>
            <template v-if="isLastStep() && canPublishLocalVersion">
              <button
                type="button"
                @click="emit('save')"
                class="px-4 py-2 text-sm border border-gray-200 rounded-lg hover:bg-white font-medium text-gray-700"
              >保存草稿</button>
              <button
                type="button"
                @click="emit('publish')"
                class="px-6 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark font-medium shadow-sm"
              >保存并发布</button>
            </template>
          </div>
        </div>
      </div>
    </div>
  </Teleport>

  <Teleport to="body">
    <div
      v-if="showCapabilityHelp"
      class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/40 p-4"
      @click.self="showCapabilityHelp = false"
    >
      <div class="w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-4 border-b border-gray-100 px-6 py-4">
          <div>
            <h3 class="text-lg font-bold text-gray-900">扩展能力标签怎么用？</h3>
            <p class="mt-1 text-sm text-gray-500">标签用于语义路由与 Main 委派，不会自动增加工具或数据权限。</p>
          </div>
          <button
            type="button"
            class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            aria-label="关闭"
            @click="showCapabilityHelp = false"
          >
            <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="max-h-[70vh] space-y-4 overflow-y-auto px-6 py-5">
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <section class="rounded-xl border border-gray-200 bg-gray-50/60 p-4">
              <h4 class="text-sm font-bold text-gray-900">有什么用途</h4>
              <p class="mt-2 text-sm leading-relaxed text-gray-600">标签会与智能体名称、描述一起提供给语义路由器，并进入 Main 的可委派智能体通讯录，用来表达这个智能体擅长处理什么任务。</p>
            </section>
            <section class="rounded-xl border border-gray-200 bg-gray-50/60 p-4">
              <h4 class="text-sm font-bold text-gray-900">如何影响路由</h4>
              <p class="mt-2 text-sm leading-relaxed text-gray-600">Main 会构建“能力 → 子智能体”映射。多个智能体拥有相同标签时，优先选择排序权重更高且当前用户有权限调用的智能体。</p>
            </section>
            <section class="rounded-xl border border-blue-100 bg-blue-50/50 p-4">
              <h4 class="text-sm font-bold text-gray-900">如何填写</h4>
              <p class="mt-2 text-sm leading-relaxed text-gray-600">建议填写 1～3 个简短、明确的小写英文标签，使用下划线分词，例如 <code class="rounded bg-white px-1">contract_review</code>、<code class="rounded bg-white px-1">ops_diagnosis</code>、<code class="rounded bg-white px-1">content_writing</code>。</p>
            </section>
            <section class="rounded-xl border border-blue-100 bg-blue-50/50 p-4">
              <h4 class="text-sm font-bold text-gray-900">示例</h4>
              <p class="mt-2 text-sm leading-relaxed text-gray-600">“擅长审核销售合同并识别风险条款”的通用助手，可填写 <code class="rounded bg-white px-1">contract_review</code>。合同审核问题会更容易路由到它。</p>
            </section>
          </div>
          <div class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
            <div class="font-bold">重要提醒</div>
            <ul class="mt-2 list-disc space-y-1 pl-4">
              <li>标签只影响路由和委派，不会自动安装工具、开放数据权限或增加执行能力。</li>
              <li>不要手动填写 general_chat、data_query、knowledge_base。</li>
              <li>普通助手不要填写 reporting、sql_generation 等查数倾向标签，否则可能产生误路由。</li>
              <li>标签应与实际模型、提示词和工具配置保持一致，过多或冲突的标签会降低路由准确性。</li>
            </ul>
          </div>
        </div>
        <div class="flex justify-end border-t border-gray-100 px-6 py-4">
          <button
            type="button"
            class="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-white hover:bg-primary-dark"
            @click="showCapabilityHelp = false"
          >知道了</button>
        </div>
      </div>
    </div>
  </Teleport>

  <Teleport to="body">
    <div
      v-if="showAgentTypeHelp"
      class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/40 p-4"
      @click.self="showAgentTypeHelp = false"
    >
      <div class="w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-4 border-b border-gray-100 px-6 py-4">
          <div>
            <h3 class="text-lg font-bold text-gray-900">智能体类型怎么选？</h3>
            <p class="mt-1 text-sm text-gray-500">类型决定运行流程、门禁规则和 Main 的委派方式；创建保存后不可修改。</p>
          </div>
          <button
            type="button"
            class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            aria-label="关闭"
            @click="showAgentTypeHelp = false"
          >
            <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="max-h-[70vh] space-y-4 overflow-y-auto px-6 py-5">
          <div class="rounded-xl border border-gray-200 bg-gray-50/60 p-4">
            <div class="flex items-center gap-2">
              <span class="text-xl">💬</span>
              <div>
                <h4 class="text-sm font-bold text-gray-900">通用助手</h4>
                <div class="mt-0.5 font-mono text-[10px] text-blue-600">general_chat</div>
              </div>
            </div>
            <p class="mt-2 text-sm leading-relaxed text-gray-600">
              通用型智能体。配置相应工具后即可获得对应能力，适合问答、写作、任务处理和大部分通用场景。
            </p>
          </div>
          <div class="rounded-xl border border-blue-100 bg-blue-50/50 p-4">
            <div class="flex items-center gap-2">
              <span class="text-xl">📊</span>
              <div>
                <h4 class="text-sm font-bold text-gray-900">ChatBI</h4>
                <div class="mt-0.5 font-mono text-[10px] text-blue-600">data_query</div>
              </div>
            </div>
            <p class="mt-2 text-sm leading-relaxed text-gray-600">
              专注数据库查数、指标统计和数据分析，执行 Schema、权限、SQL 安全与结果校验等门禁，不能回答非查数需求。
            </p>
            <p class="mt-2 text-xs leading-5 text-blue-700">查数工具必填；数据集可选，未显式绑定时，自动使用当前用户有权访问的数据集。</p>
          </div>
          <div class="rounded-xl border border-emerald-100 bg-emerald-50/50 p-4">
            <div class="flex items-center gap-2">
              <span class="text-xl">📚</span>
              <div>
                <h4 class="text-sm font-bold text-gray-900">知识库助手</h4>
                <div class="mt-0.5 font-mono text-[10px] text-emerald-600">knowledge_base</div>
              </div>
            </div>
            <p class="mt-2 text-sm leading-relaxed text-gray-600">
              专注已授权知识库的检索、归纳和基于资料回答，不能处理知识库检索以外的通用任务或数据库查数。
            </p>
            <p class="mt-2 text-xs leading-5 text-emerald-700">必须显式绑定知识库和检索工具。</p>
          </div>
          <p class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">
            创建保存后不可修改，请在创建前确认类型选择。
          </p>
        </div>
        <div class="flex justify-end border-t border-gray-100 px-6 py-4">
          <button
            type="button"
            class="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-white hover:bg-primary-dark"
            @click="showAgentTypeHelp = false"
          >知道了</button>
        </div>
      </div>
    </div>
  </Teleport>

  <Teleport to="body">
    <div
      v-if="showEngineHelp"
      class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/40 p-4"
      @click.self="showEngineHelp = false"
    >
      <div class="w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-4 border-b border-gray-100 px-6 py-4">
          <div>
            <h3 class="text-lg font-bold text-gray-900">执行引擎怎么选？</h3>
            <p class="mt-1 text-sm text-gray-500">三个引擎面向不同接入方式，选错会影响后续配置步骤与运行入口。</p>
          </div>
          <button
            type="button"
            class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            aria-label="关闭"
            @click="showEngineHelp = false"
          >
            <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="max-h-[70vh] space-y-4 overflow-y-auto px-6 py-5">
          <div class="rounded-xl border border-blue-100 bg-blue-50/50 p-4">
            <div class="flex items-center gap-2">
              <span class="text-xl">🧠</span>
              <h4 class="text-sm font-bold text-gray-900">NanZi Engine（平台原生）</h4>
            </div>
            <p class="mt-2 text-sm leading-relaxed text-gray-600">
              在本平台内完成编排：模型策略、工具能力、系统提示词、版本发布都由平台托管。适合需要多步配置、草稿发布和平台内调试的标准智能体。
            </p>
            <ul class="mt-3 space-y-1.5 text-xs text-gray-500">
              <li>· 创建后走完整向导（模型 → 工具 → 提示词 → 确认）</li>
              <li>· 支持智能体类型、扩展能力标签与本地版本生命周期</li>
              <li>· 会话与任务都在平台内执行</li>
            </ul>
          </div>
          <div class="rounded-xl border border-cyan-100 bg-cyan-50/40 p-4">
            <div class="flex items-center gap-2">
              <span class="text-xl">🌊</span>
              <h4 class="text-sm font-bold text-gray-900">RAGFlow（外部智能体）</h4>
            </div>
            <p class="mt-2 text-sm leading-relaxed text-gray-600">
              接入已在 RAGFlow 侧编排好的外部智能体。平台主要负责登记与路由，对话编排、知识库等仍在 RAGFlow 中维护。
            </p>
            <ul class="mt-3 space-y-1.5 text-xs text-gray-500">
              <li>· 单页创建，需填写 RAGFlow App ID</li>
              <li>· 不走平台内的模型/工具/提示词多步配置</li>
              <li>· 类型固定为通用对话，适合托管式外部 Agent</li>
            </ul>
          </div>
          <div class="rounded-xl border border-orange-100 bg-orange-50/40 p-4">
            <div class="flex items-center gap-2">
              <span class="text-xl">🦞</span>
              <h4 class="text-sm font-bold text-gray-900">OpenClaw（外部任务机器人）</h4>
            </div>
            <p class="mt-2 text-sm leading-relaxed text-gray-600">
              对接 OpenClaw 任务机器人，适合把外部自动化/任务执行能力挂到平台。平台登记连接信息后，实际执行仍由 OpenClaw 侧完成。
            </p>
            <ul class="mt-3 space-y-1.5 text-xs text-gray-500">
              <li>· 单页创建，需填写 Base URL 与 Bot ID</li>
              <li>· 可选开启安全检查（如路径校验）</li>
              <li>· 不走平台原生的多步编排向导</li>
            </ul>
          </div>
          <p class="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-500">
            提示：选择引擎后，页面会自动调整所需字段与后续步骤；创建保存后执行引擎不可再改。
          </p>
        </div>
        <div class="flex justify-end border-t border-gray-100 px-6 py-4">
          <button
            type="button"
            class="rounded-lg bg-primary px-5 py-2 text-sm font-medium text-white hover:bg-primary-dark"
            @click="showEngineHelp = false"
          >知道了</button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- 推荐排版样式效果预览 Modal -->
  <Modal
    v-if="showThemePreviewHelp"
    title="🎨 AI 消息排版样式效果预览"
    size="max-w-4xl"
    z-index="10000"
    @close="showThemePreviewHelp = false"
  >
    <div class="space-y-4">
      <p class="text-xs text-gray-500">点击下方按钮可实时切换并预览不同推荐样式在 AI 会话界面中的最终呈现效果。</p>
      
      <!-- 样式切换 Tab 栏 -->
      <div class="grid grid-cols-3 sm:grid-cols-9 gap-1.5 p-1.5 bg-gray-50 dark:bg-gray-900/50 rounded-xl border border-gray-100 dark:border-gray-800">
        <button
          v-for="themeOpt in markdownThemeOptions"
          :key="themeOpt.value"
          type="button"
          @click="previewTheme = themeOpt.value"
          class="py-2 px-1 rounded-lg text-[10px] sm:text-xs font-semibold transition-all flex items-center justify-center gap-1 active:scale-95"
          :class="
            previewTheme === themeOpt.value
              ? 'bg-white dark:bg-gray-800 text-blue-600 dark:text-blue-400 shadow-sm border border-gray-200/50 dark:border-gray-700'
              : 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-300'
          "
        >
          <span>{{ themeOpt.emoji }}</span>
          <span>{{ themeOpt.label }}</span>
        </button>
      </div>
      
      <!-- 效果预览区域 -->
      <div class="border border-gray-150 dark:border-gray-700/60 rounded-xl p-4 bg-gray-50/50 dark:bg-gray-900/20">
        <div class="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-2">样式效果预览</div>
        
        <!-- 使用真实的 MessageRenderer 展示排版效果 -->
        <MessageRenderer
          :content="previewMarkdownContent"
          :theme="previewTheme"
          class="border border-transparent"
        />
      </div>
    </div>
    
    <template #footer>
      <div class="flex justify-end gap-3">
        <button
          @click="applyPreviewTheme"
          class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium shadow-sm transition-all"
        >
          使用该样式
        </button>
        <button
          @click="showThemePreviewHelp = false"
          class="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700 rounded-lg text-xs font-medium transition-all"
        >
          关闭
        </button>
      </div>
    </template>
  </Modal>
</template>

<style scoped>
.version-editor-drawer {
  animation: slideInRight 0.25s ease-out;
}
@keyframes slideInRight {
  from { transform: translateX(100%); opacity: 0.8; }
  to { transform: translateX(0); opacity: 1; }
}

.temp-preset {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  color: #64748b;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  transition: all 0.15s;
}
.temp-preset:hover {
  background: #e2e8f0;
  color: #334155;
}

.tool-card {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  transition: all 0.15s;
}
.tool-card:hover {
  border-color: #93c5fd;
  background: #f8fafc;
}
.tool-card--selected {
  border-color: var(--color-primary, #2563eb);
  background: rgba(37, 99, 235, 0.05);
}
.tool-card--selected-indigo {
  border-color: #6366f1;
  background: rgba(99, 102, 241, 0.05);
}

.tool-checkbox {
  width: 14px;
  height: 14px;
  border-radius: 3px;
  border: 1px solid #d1d5db;
  background: white;
  flex-shrink: 0;
  margin-top: 2px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.tool-checkbox--on {
  background: var(--color-primary, #2563eb);
  border-color: var(--color-primary, #2563eb);
}
.tool-checkbox--on-indigo {
  background: #6366f1;
  border-color: #6366f1;
}

.tool-action-btn {
  padding: 4px;
  border-radius: 4px;
  color: #9ca3af;
  transition: all 0.15s;
}
.tool-action-btn:hover {
  color: var(--color-primary, #2563eb);
  background: white;
}

.tool-action-btn--active {
  color: var(--color-primary, #2563eb);
  background: white;
}

.tool-action-btn--attention {
  color: #b45309;
  background: #fff7ed;
  box-shadow: 0 0 0 1px #fdba74;
}

.summary-card {
  padding: 14px;
  border-radius: 12px;
  border: 1px solid #e5e7eb;
  background: #fafafa;
}

.version-editor-scroll::-webkit-scrollbar {
  width: 6px;
}
.version-editor-scroll::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}
</style>
