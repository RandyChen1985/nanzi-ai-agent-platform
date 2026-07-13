<script setup lang="ts">
import type { AIAgent, AIAgentVersion } from '../../api/agent';
import type { AIModel } from '../../api/model';
import MarkdownEditor from '../MarkdownEditor.vue';

type VersionConfigStep = 'model' | 'tools' | 'prompt' | 'review';
type ToolGroup = { label: string; icon: string; tools: any[] };

const props = defineProps<{
  show: boolean;
  versionForm: Partial<AIAgentVersion>;
  selectedAgent: AIAgent | null;
  models: AIModel[];
  canEditVersion: boolean;
  toolTab: 'static' | 'mcp';
  toolSearchQuery: string;
  versionConfigStep: VersionConfigStep;
  versionConfigSteps: { id: VersionConfigStep; label: string }[];
  versionConfigProgress: number;
  selectedToolsCount: number;
  promptCharCount: number;
  versionStatusLabel: string;
  versionStatusClass: string;
  filteredGroupedTools: ToolGroup[];
  filteredGroupedMcpTools: Record<string, any[]>;
  allAvailableToolsCount: number;
  mcpToolsCount: number;
  isToolSelected: (name: string) => boolean;
  getToolCustomConfig: (name: string) => any;
  isAllMcpSelected: (serverName: string, tools: any[]) => boolean;
  isMcpGroupCollapsed: (serverName: string) => boolean;
  getMcpGroupSelectedCount: (tools: any[]) => number;
  isStaticGroupCollapsed: (label: string) => boolean;
  getStaticGroupSelectedCount: (tools: any[]) => number;
  getModelDisplayName: (modelId?: string) => string;
}>();

const emit = defineEmits<{
  close: [];
  save: [];
  'update:toolTab': [value: 'static' | 'mcp'];
  'update:toolSearchQuery': [value: string];
  'update:versionConfigStep': [value: VersionConfigStep];
  toggleTool: [name: string];
  toggleSelectAllMcp: [serverName: string, tools: any[]];
  toggleMcpGroupCollapse: [serverName: string];
  toggleStaticGroupCollapse: [label: string];
  setOrchestratorTemperature: [value: number];
  setSynthesisTemperature: [value: number];
  openToolRuntimeConfig: [name: string];
  openDingTalkConfig: [name: string];
  openEmailConfig: [name: string];
  openWeChatWorkConfig: [name: string];
  openRagSelector: [type: string, mode: string];
  copySystemPrompt: [];
  nextStep: [];
  prevStep: [];
}>();

const llmModels = () => props.models.filter((m) => m.type === 'llm' && m.is_active);

const stepIndex = () => props.versionConfigSteps.findIndex((s) => s.id === props.versionConfigStep);
const isFirstStep = () => stepIndex() <= 0;
const isLastStep = () => stepIndex() >= props.versionConfigSteps.length - 1;

const goStep = (step: VersionConfigStep) => emit('update:versionConfigStep', step);
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
                版本配置 · V{{ versionForm.version_number || 'New' }}
              </h2>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full border" :class="versionStatusClass">
                {{ versionStatusLabel }}
              </span>
            </div>
            <p v-if="selectedAgent" class="text-xs text-gray-500 mt-0.5 truncate">
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
                class="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
                :class="versionConfigStep === step.id
                  ? 'bg-primary text-white shadow-sm'
                  : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700'"
              >
                <span
                  class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold border"
                  :class="versionConfigStep === step.id
                    ? 'bg-white/20 border-white/30 text-white'
                    : 'bg-gray-100 border-gray-200 text-gray-500'"
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
          <div class="mt-2 text-[10px] text-gray-400">
            配置完成度 {{ versionConfigProgress }} / 3（模型 · 工具 · 提示词）
          </div>
        </div>

        <!-- Body -->
        <div class="flex-1 overflow-y-auto min-h-0 px-6 py-5 version-editor-body">
          <!-- Step 1: Model -->
          <div v-if="versionConfigStep === 'model'" class="space-y-4 max-w-3xl">
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
                  <option v-for="m in llmModels()" :key="m.id" :value="m.model_id">{{ m.name }}</option>
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
                  <option v-for="m in llmModels()" :key="m.id" :value="m.model_id">{{ m.name }}</option>
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
                    type="text"
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
                        <button v-if="tool.name === 'send_dingtalk_message'" type="button" @click="emit('openDingTalkConfig', tool.name)" class="tool-action-btn" title="钉钉">
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5.882V19.24a1.76 1.72 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" /></svg>
                        </button>
                        <button v-if="tool.name === 'send_email'" type="button" @click="emit('openEmailConfig', tool.name)" class="tool-action-btn" title="邮件">
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
                        </button>
                        <button v-if="tool.name === 'send_wechat_work_message'" type="button" @click="emit('openWeChatWorkConfig', tool.name)" class="tool-action-btn" title="企微">
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
                        </button>
                        <button v-if="tool.name === 'search_knowledge_base' && selectedAgent?.engine_type === 'LOCAL'" type="button" @click="emit('openRagSelector', 'dataset', 'agent_kb_immediate')" class="tool-action-btn" title="知识库">
                          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" /></svg>
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- MCP Tools -->
            <div v-else class="flex-1 overflow-y-auto space-y-3 pr-1 version-editor-scroll">
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
                placeholder="你是一个..."
                fill
              />
            </div>
            <p class="text-[10px] text-gray-400 mt-2 flex-shrink-0">{{ promptCharCount }} 字符</p>
          </div>

          <!-- Step 4: Review -->
          <div v-else class="space-y-4 max-w-2xl">
            <h3 class="text-sm font-bold text-gray-900">配置总览</h3>
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
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
          <div v-else class="flex-1"></div>

          <div class="flex items-center gap-2">
            <button type="button" @click="emit('close')" class="px-4 py-2 text-sm text-gray-500 hover:text-gray-700 font-medium">取消</button>
            <button v-if="!isFirstStep()" type="button" @click="emit('prevStep')" class="px-4 py-2 text-sm border border-gray-200 rounded-lg hover:bg-white font-medium text-gray-700">上一步</button>
            <button v-if="!isLastStep()" type="button" @click="emit('nextStep')" class="px-5 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark font-medium shadow-sm">下一步</button>
            <button
              v-if="isLastStep() && canEditVersion && (!versionForm.id || versionForm.status === 'DRAFT')"
              type="button"
              @click="emit('save')"
              class="px-6 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark font-medium shadow-sm"
            >{{ versionForm.id ? '更新草稿' : '保存为草稿' }}</button>
          </div>
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
