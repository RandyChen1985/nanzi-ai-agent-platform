<script setup lang="ts">
import { computed, ref } from 'vue';
import type { AIAgent, AIAgentBase, AIAgentVersion, AgentType } from '../../api/agent';
import type { AIModel } from '../../api/model';
import MarkdownEditor from '../MarkdownEditor.vue';

type VersionConfigStep = 'agent' | 'model' | 'tools' | 'prompt' | 'review';
type ToolGroup = { label: string; icon: string; tools: any[] };
type SkillItem = { id: string; name?: string; description?: string; enabled?: string | boolean; path?: string };

const props = defineProps<{
  show: boolean;
  isCreatingAgent: boolean;
  isOnboardingFlow: boolean;
  agentForm: Partial<AIAgentBase>;
  canConfigureSystemAgent: boolean;
  versionForm: Partial<AIAgentVersion>;
  selectedAgent: AIAgent | null;
  models: AIModel[];
  canEditVersion: boolean;
  toolTab: 'static' | 'mcp' | 'skills';
  toolSearchQuery: string;
  versionConfigStep: VersionConfigStep;
  versionConfigSteps: { id: VersionConfigStep; label: string }[];
  versionConfigProgress: number;
  selectedToolsCount: number;
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
  isMcpGroupCollapsed: (serverName: string) => boolean;
  getMcpGroupSelectedCount: (tools: any[]) => number;
  isStaticGroupCollapsed: (label: string) => boolean;
  getStaticGroupSelectedCount: (tools: any[]) => number;
  getModelDisplayName: (modelId?: string) => string;
  canReachVersionConfigStep?: (step: VersionConfigStep) => boolean;
  isVersionConfigStepComplete?: (step: VersionConfigStep) => boolean;
}>();

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
  toggleMcpGroupCollapse: [serverName: string];
  toggleStaticGroupCollapse: [label: string];
  setOrchestratorTemperature: [value: number];
  setSynthesisTemperature: [value: number];
  openToolRuntimeConfig: [name: string];
  openMetadataDatasetBinding: [name: string];
  openDingTalkConfig: [name: string];
  openEmailConfig: [name: string];
  openWeChatWorkConfig: [name: string];
  openRagSelector: [type: 'agent' | 'dataset', mode: 'app_id' | 'dataset_ids' | 'agent_kb_immediate'];
  copySystemPrompt: [];
  nextStep: [];
  prevStep: [];
  toast: [message: string, type?: 'success' | 'error' | 'info' | 'warning'];
}>();

const llmModels = () => props.models.filter((m) => (m.type === 'llm' || m.type === 'multimodal') && m.is_active);

const stepIndex = () => props.versionConfigSteps.findIndex((s) => s.id === props.versionConfigStep);
const isFirstStep = () => stepIndex() <= 0;
const isLastStep = () => stepIndex() >= props.versionConfigSteps.length - 1;
const canReachStep = (step: VersionConfigStep) =>
  props.canReachVersionConfigStep ? props.canReachVersionConfigStep(step) : true;
const isCurrentStepComplete = () =>
  props.isVersionConfigStepComplete
    ? props.isVersionConfigStepComplete(props.versionConfigStep)
    : true;
const goStep = (step: VersionConfigStep) => emit('update:versionConfigStep', step);

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
            配置完成度 {{ versionConfigProgress }} / {{ isCreatingAgent ? 4 : 3 }}（{{ isCreatingAgent ? '智能体 · ' : '' }}模型 · 工具 · 提示词）
          </div>
        </div>

        <!-- Body -->
        <div class="flex-1 overflow-y-auto min-h-0 px-6 py-5 version-editor-body">
          <!-- Step 1: Agent information (first-time creation only) -->
          <div v-if="versionConfigStep === 'agent'" class="space-y-5 max-w-3xl">
            <div class="flex items-center justify-between gap-4">
              <h3 class="text-sm font-bold text-gray-900">智能体信息</h3>
              <label v-if="canConfigureSystemAgent" class="group flex cursor-pointer items-center gap-2" title="系统预置智能体，防止误删并提高路由权重">
                <span class="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">Admin Only</span>
                <span
                  class="inline-flex items-center gap-1 rounded-full border px-2 py-1 text-xs font-semibold transition-colors"
                  :class="agentForm.is_system ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 bg-gray-50 text-gray-500'"
                >
                  <span aria-hidden="true">🛡️</span>
                  系统智能体
                </span>
                <span class="relative inline-flex h-5 w-9 items-center rounded-full transition-colors" :class="agentForm.is_system ? 'bg-primary' : 'bg-gray-300'">
                  <input v-model="agentForm.is_system" type="checkbox" class="sr-only" />
                  <span class="h-4 w-4 rounded-full bg-white shadow-sm transition-transform" :class="agentForm.is_system ? 'translate-x-4' : 'translate-x-0.5'"></span>
                </span>
              </label>
            </div>
            <p class="text-sm text-gray-500">先选择执行引擎，页面会自动调整所需配置和后续流程。</p>
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
            <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">物理标识符 <span class="text-red-500">*</span></label>
                <input v-model="agentForm.name" placeholder="例如 sales-data-agent" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
                <p class="mt-1 text-[10px] text-gray-400">保存后不可修改，建议使用小写英文与连字符。</p>
              </div>
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">显示名称 <span class="text-red-500">*</span></label>
                <input v-model="agentForm.display_name" placeholder="例如 销售数据助手" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
            </div>
            <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">头像地址</label>
                <input v-model="agentForm.avatar_url" placeholder="可选，填写图片 URL" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">排序权重</label>
                <input v-model.number="agentForm.sort_order" type="number" placeholder="值越大越靠前" class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
            </div>
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
                <select
                  :value="versionForm.model_name"
                  @change="versionForm.model_name = ($event.target as HTMLSelectElement).value"
                  :disabled="!canEditVersion"
                  class="w-full px-3 py-2 border border-gray-200 rounded-lg outline-none focus:ring-2 focus:ring-primary/30 bg-white text-sm"
                >
                  <option value="" disabled>选择编排模型...</option>
                  <option v-for="m in llmModels()" :key="m.id" :value="m.model_id">{{ m.type === 'multimodal' ? '🖼️ ' : '' }}{{ m.name }}</option>
                </select>
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
                    type="range" min="0" max="1" step="0.1"
                    :value="versionForm.temperature"
                    @input="versionForm.temperature = Number(($event.target as HTMLInputElement).value)"
                    :disabled="!canEditVersion"
                    class="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-blue-600"
                  />
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
                <select
                  :value="versionForm.synthesis_model_name"
                  @change="versionForm.synthesis_model_name = ($event.target as HTMLSelectElement).value"
                  :disabled="!canEditVersion"
                  class="w-full px-3 py-2 border border-gray-200 rounded-lg outline-none focus:ring-2 focus:ring-emerald-500/30 bg-white text-sm"
                >
                  <option value="">跟随编排模型</option>
                  <option v-for="m in llmModels()" :key="m.id" :value="m.model_id">{{ m.type === 'multimodal' ? '🖼️ ' : '' }}{{ m.name }}</option>
                </select>
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
                </div>
              </div>
            </div>

            <div class="rounded-lg bg-gray-50 border border-gray-200 px-4 py-3 text-xs text-gray-600">
              <span class="font-semibold text-gray-700">当前策略：</span>
              {{ getModelDisplayName(versionForm.model_name) }} @ {{ versionForm.temperature }}
              <span class="text-gray-400 mx-1">→</span>
              {{ versionForm.synthesis_model_name ? getModelDisplayName(versionForm.synthesis_model_name) + ' @ ' + versionForm.synthesis_temperature : '跟随编排模型' }}
            </div>
          </div>

          <!-- Step 2: Tools -->
          <div v-else-if="versionConfigStep === 'tools'" class="flex flex-col min-h-[calc(100vh-280px)]">
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
                  已选 {{ selectedToolsCount }} 个
                </span>
              </div>
              <div class="flex bg-gray-100 p-0.5 rounded-lg text-xs">
                <button
                  type="button"
                  @click="emit('update:toolTab', 'static')"
                  class="px-3 py-1.5 rounded-md transition-all font-medium"
                  :class="toolTab === 'static' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                >系统工具 ({{ allAvailableToolsCount }})</button>
                <button
                  type="button"
                  @click="emit('update:toolTab', 'mcp')"
                  class="px-3 py-1.5 rounded-md transition-all font-medium"
                  :class="toolTab === 'mcp' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                >MCP 工具 ({{ mcpToolsCount }})</button>
                <button
                  type="button"
                  @click="emit('update:toolTab', 'skills')"
                  class="px-3 py-1.5 rounded-md transition-all font-medium"
                  :class="toolTab === 'skills' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                >Skills ({{ enabledGlobalSkillsCount }})</button>
              </div>
            </div>

            <!-- Static Tools -->
            <div v-if="toolTab === 'static'" class="flex-1 overflow-y-auto space-y-3 pr-1 version-editor-scroll">
              <div v-for="group in filteredGroupedTools" :key="group.label" class="rounded-lg border border-gray-200 overflow-hidden">
                <div class="flex items-center justify-between py-2 px-3 bg-gray-50 border-b border-gray-100">
                  <button
                    type="button"
                    @click="emit('toggleStaticGroupCollapse', group.label)"
                    class="flex items-center gap-2 min-w-0 flex-1 text-left"
                  >
                    <svg class="w-3.5 h-3.5 text-gray-400 transition-transform" :class="{ 'rotate-90': !isStaticGroupCollapsed(group.label) }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                    <span class="text-xs">{{ group.icon }}</span>
                    <span class="text-xs font-bold text-gray-700">{{ group.label }}</span>
                    <span class="text-[10px] text-gray-400">({{ group.tools.length }})</span>
                    <span v-if="getStaticGroupSelectedCount(group.tools) > 0" class="text-[9px] font-bold text-primary bg-primary/10 px-1.5 py-0.5 rounded-full">
                      已选 {{ getStaticGroupSelectedCount(group.tools) }}
                    </span>
                  </button>
                </div>
                <div v-show="!isStaticGroupCollapsed(group.label)" class="grid grid-cols-1 sm:grid-cols-2 gap-2 p-3">
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
                        <button v-if="tool.name === 'search_knowledge_base' && (selectedAgent?.engine_type || agentForm.engine_type) === 'LOCAL'" type="button" @click="emit('openRagSelector', 'dataset', 'agent_kb_immediate')" class="tool-action-btn" title="知识库">
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
              <div v-if="mcpToolsCount === 0" class="p-12 text-center text-gray-400 text-sm">
                暂无已发布的 MCP 工具，请前往系统设置配置。
              </div>
              <div v-else v-for="(tools, serverName) in filteredGroupedMcpTools" :key="serverName" class="rounded-lg border border-indigo-100 overflow-hidden">
                <div class="flex items-center justify-between py-2 px-3 bg-indigo-50/60 border-b border-indigo-100/50">
                  <button type="button" @click="emit('toggleMcpGroupCollapse', serverName)" class="flex items-center gap-1.5 min-w-0 flex-1 text-left">
                    <svg class="w-3.5 h-3.5 text-indigo-400 transition-transform" :class="{ 'rotate-90': !isMcpGroupCollapsed(serverName) }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                    <span class="text-[10px] font-bold text-indigo-700 uppercase truncate">{{ serverName }}</span>
                    <span class="text-[10px] text-indigo-400">({{ tools.length }})</span>
                    <span v-if="getMcpGroupSelectedCount(tools) > 0" class="text-[9px] font-bold text-indigo-700 bg-indigo-100 px-1.5 py-0.5 rounded-full">已选 {{ getMcpGroupSelectedCount(tools) }}</span>
                  </button>
                  <button v-if="canEditVersion" type="button" @click.stop="emit('toggleSelectAllMcp', serverName, tools)" class="text-[10px] font-bold text-indigo-600 hover:text-indigo-800 ml-2 flex-shrink-0">
                    {{ isAllMcpSelected(serverName, tools) ? '取消全选' : '一键全选' }}
                  </button>
                </div>
                <div v-show="!isMcpGroupCollapsed(serverName)" class="grid grid-cols-1 sm:grid-cols-2 gap-2 p-3">
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
                      <div class="text-[11px] font-bold font-mono text-gray-800">
                        <span v-if="tool.name.includes(':')" class="text-gray-400 text-[9px] font-sans font-normal">{{ tool.name.split(':')[0] }}:</span>
                        {{ tool.name.includes(':') ? tool.name.split(':')[1] : tool.name }}
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
                    已选 {{ selectedSkillsCount }} 个公共技能
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
              />
            </div>
            <p class="text-[10px] text-gray-400 mt-2 flex-shrink-0">{{ promptCharCount }} 字符</p>
          </div>

          <!-- Step 4: Review -->
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
                <div class="text-sm font-semibold text-gray-800">{{ selectedToolsCount }} 个已启用</div>
              </div>
              <div class="summary-card">
                <div class="text-[10px] text-gray-400 uppercase font-bold mb-1">Skills</div>
                <div class="text-sm font-semibold text-gray-800">
                  {{ versionForm.skills_custom ? `自定义 ${selectedSkillsCount} 个` : '全部公共 + 个人' }}
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
                class="w-full text-sm border border-gray-200 rounded-xl focus:border-primary focus:ring-2 focus:ring-primary/20 resize-none p-3 bg-gray-50 outline-none"
              ></textarea>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div class="px-6 py-4 border-t border-gray-200 bg-gray-50 flex items-center justify-between gap-3 flex-shrink-0">
          <div v-if="selectedAgent?.is_editable === false" class="text-xs text-amber-700 bg-amber-50 px-3 py-1.5 rounded-lg border border-amber-100">
            只读模式，无法修改
          </div>
          <div v-else class="flex-1">
            <p v-if="externalCreationMissingFields.length > 0" class="text-xs font-medium text-amber-700">
              请先填写：{{ externalCreationMissingFields.join('、') }}
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
              v-if="isLastStep() && canEditVersion && (!versionForm.id || versionForm.status === 'DRAFT') && !(isOnboardingFlow && agentForm.engine_type === 'LOCAL')"
              type="button"
              :disabled="externalCreationMissingFields.length > 0"
              @click="emit('save')"
              class="px-6 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark font-medium shadow-sm disabled:cursor-not-allowed disabled:bg-gray-300 disabled:shadow-none"
            >{{ isCreatingAgent && agentForm.engine_type !== 'LOCAL' ? `创建 ${agentForm.engine_type === 'RAGFLOW' ? 'RAGFlow' : 'OpenClaw'} 智能体` : isCreatingAgent ? '创建智能体与 V1 草稿' : versionForm.id ? '更新草稿' : '保存为草稿' }}</button>
            <template v-if="isLastStep() && canEditVersion && (!versionForm.id || versionForm.status === 'DRAFT') && isOnboardingFlow && agentForm.engine_type === 'LOCAL'">
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
