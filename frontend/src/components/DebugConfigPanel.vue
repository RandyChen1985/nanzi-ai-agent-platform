<script setup lang="ts">
import GroundingHelpPopover from "@/components/GroundingHelpPopover.vue";
import { useToast } from "@/composables/useToast";

const { showToast } = useToast();

interface DebugConfig {
  model: string;
  temperature: number;
  dryRun: boolean;
  returnRawPrompt: boolean;
  enableMultiAgent: boolean;
  enableGrounding: boolean;
  showShortcuts: boolean;
  systemPromptOverride: string;
  injectedContext: { key: string; value: string }[];
}

interface AgentParams {
  agent_id?: string | null;
  version_id?: string | null;
}

const props = defineProps<{
  visible: boolean;
  isFloating: boolean;
  config: DebugConfig;
  agentParams: AgentParams;
  loadingConfig: boolean;
  agentContext: Record<string, any>;
  ragRetrievalMeta?: Record<string, any> | null;
}>();

const formatDatasetIds = (ids: unknown) => {
  if (!Array.isArray(ids) || ids.length === 0) return "（未指定）";
  return ids.join(", ");
};

const emit = defineEmits<{
  (e: "update:visible", value: boolean): void;
  (e: "update:isFloating", value: boolean): void;
  (e: "load-config"): void;
  (e: "clear-context", key?: string): void;
}>();

const addContextItem = () => {
  props.config.injectedContext.push({ key: "", value: "" });
};

const removeContextItem = (index: number) => {
  props.config.injectedContext.splice(index, 1);
};

const handleGroundingChange = () => {
  const enabled = props.config.enableGrounding;
  showToast(
    enabled ? "反幻觉校验已开启" : "反幻觉校验已关闭",
    enabled ? "success" : "info",
  );
};
</script>

<template>
  <transition name="slide-fade">
    <div
      v-if="visible"
      class="bg-white border-l border-gray-200 flex flex-col shadow-xl z-20 transition-all duration-300"
      :class="[
        isFloating ? 'absolute right-0 h-full shadow-2xl' : 'relative',
        'w-80',
      ]"
    >
      <div
        class="p-4 border-b border-gray-100 bg-gray-50 flex items-center justify-between flex-shrink-0"
      >
        <h3 class="font-bold text-gray-700 text-sm">调试配置</h3>
        <div class="flex items-center space-x-1">
          <!-- Float/Dock Toggle -->
          <button
            @click="emit('update:isFloating', !isFloating)"
            class="p-1 text-gray-400 hover:text-gray-600 rounded hover:bg-gray-200 transition-colors"
            :title="isFloating ? '固定面板 (挤占空间)' : '悬浮面板 (覆盖空间)'"
          >
            <svg
              v-if="isFloating"
              class="w-4 h-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
              />
            </svg>
            <svg
              v-else
              class="w-4 h-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"
              />
            </svg>
          </button>
          <!-- Close -->
          <button
            @click="emit('update:visible', false)"
            class="p-1 text-gray-400 hover:text-gray-600 rounded hover:bg-gray-200 transition-colors"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
      </div>
      <div class="p-4 space-y-6 overflow-y-auto flex-1">
        <!-- Mode Settings -->
        <div class="space-y-3">
          <label class="flex items-center space-x-2 cursor-pointer">
            <input
              type="checkbox"
              v-model="config.dryRun"
              class="rounded text-primary focus:ring-primary border-gray-300"
            />
            <span class="text-sm font-medium text-gray-700"
              >空跑模式 (Review SQL)</span
            >
          </label>
          <p class="text-xs text-gray-500 ml-6">
            仅生成 SQL 但不执行。用于安全检查。
          </p>

          <label class="flex items-center space-x-2 cursor-pointer">
            <input
              type="checkbox"
              v-model="config.returnRawPrompt"
              class="rounded text-primary focus:ring-primary border-gray-300"
            />
            <span class="text-sm font-medium text-gray-700"
              >返回原始 Prompt</span
            >
          </label>

          <label class="flex items-center space-x-2 cursor-pointer">
            <input
              type="checkbox"
              v-model="config.enableMultiAgent"
              class="rounded text-primary focus:ring-primary border-gray-300"
            />
            <span class="text-sm font-medium text-gray-700"
              >启用多智能体协同</span
            >
          </label>

          <label class="flex items-center space-x-2 cursor-pointer">
            <input
              type="checkbox"
              v-model="config.showShortcuts"
              class="rounded text-primary focus:ring-primary border-gray-300"
            />
            <span class="text-sm font-medium text-gray-700"
              >显示快捷指令栏</span
            >
          </label>
        </div>

        <hr class="border-gray-100" />

        <!-- Grounding Toggle -->
        <div class="space-y-3">
          <div class="flex items-center gap-1.5">
            <label class="flex items-center space-x-2 cursor-pointer">
              <input
                type="checkbox"
                v-model="config.enableGrounding"
                @change="handleGroundingChange"
                class="rounded text-primary focus:ring-primary border-gray-300"
              />
              <span class="text-sm font-medium text-gray-700">反幻觉校验</span>
            </label>
            <GroundingHelpPopover />
          </div>
          <p class="text-xs text-gray-500 ml-6">
            开启后校验回答的事实来源并提示风险。
          </p>
        </div>

        <div class="space-y-3">
          <label class="block text-sm font-medium text-gray-700"
            >温度 (Temperature): {{ config.temperature }}</label
          >
          <input
            type="range"
            v-model.number="config.temperature"
            min="0"
            max="1"
            step="0.1"
            class="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
          />
          <div class="flex justify-between text-xs text-gray-400">
            <span>精准 (0.0)</span>
            <span>创意 (1.0)</span>
          </div>
        </div>

        <hr class="border-gray-100" />

        <!-- Prompt Override -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <label class="block text-sm font-medium text-gray-700 whitespace-nowrap"
              >系统提示词覆盖 (System Prompt)</label
            >
            <button
              @click="emit('load-config')"
              :disabled="loadingConfig || !agentParams.agent_id"
              class="text-[10px] text-primary hover:text-primary-dark font-medium flex items-center whitespace-nowrap bg-primary/5 px-1.5 py-0.5 rounded border border-primary/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <svg
                v-if="loadingConfig"
                class="animate-spin h-2.5 w-2.5 mr-1"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  class="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  stroke-width="4"
                ></circle>
                <path
                  class="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                ></path>
              </svg>
              <svg
                v-else
                class="w-2.5 h-2.5 mr-1"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              {{ loadingConfig ? "加载中..." : "同步配置" }}
            </button>
          </div>
          <textarea
            v-model="config.systemPromptOverride"
            rows="3"
            placeholder="输入临时的系统提示词..."
            class="w-full text-xs border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary custom-scrollbar"
          ></textarea>
          <p class="text-[10px] text-gray-400">
            优先级高于智能体配置。
          </p>
        </div>

        <hr class="border-gray-100" />

        <!-- Context Injection -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <label class="block text-sm font-medium text-gray-700 whitespace-nowrap"
              >手动上下文注入 (Context Injection)</label
            >
            <button
              @click="addContextItem"
              class="text-xs text-primary hover:text-primary-dark font-medium flex items-center whitespace-nowrap"
            >
              <svg
                class="w-3 h-3 mr-0.5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M12 4v16m8-8H4"
                />
              </svg>
              添加
            </button>
          </div>

          <div class="space-y-2">
            <div
              v-for="(item, index) in config.injectedContext"
              :key="index"
              class="flex space-x-2"
            >
              <input
                v-model="item.key"
                type="text"
                placeholder="键 (Key)"
                class="flex-1 w-0 text-xs border-gray-300 rounded focus:ring-primary focus:border-primary px-2 py-1"
              />
              <input
                v-model="item.value"
                type="text"
                placeholder="值 (Value)"
                class="flex-1 w-0 text-xs border-gray-300 rounded focus:ring-primary focus:border-primary px-2 py-1"
              />
              <button
                @click="removeContextItem(index)"
                class="text-gray-400 hover:text-red-500"
              >
                <svg
                  class="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
            </div>
            <p
              v-if="config.injectedContext.length === 0"
              class="text-[10px] text-gray-400 italic text-center py-2 bg-gray-50 rounded border border-dashed border-gray-200"
            >
              暂无手动注入的上下文
            </p>
          </div>
        </div>

        <hr class="border-gray-100" />

        <div
          v-if="ragRetrievalMeta"
          class="rounded-lg border border-amber-100 bg-amber-50/60 p-3 space-y-2"
        >
          <h4 class="text-[11px] font-bold text-amber-800 uppercase tracking-wider">
            知识库检索实参
          </h4>
          <div class="grid grid-cols-1 gap-1.5 text-[11px] text-gray-700 font-mono">
            <div>
              <span class="text-gray-500">dataset_ids：</span>
              {{ formatDatasetIds(ragRetrievalMeta.dataset_ids) }}
            </div>
            <div v-if="ragRetrievalMeta.request_dataset_ids?.length">
              <span class="text-gray-500">request_dataset_ids：</span>
              {{ formatDatasetIds(ragRetrievalMeta.request_dataset_ids) }}
            </div>
            <div>
              <span class="text-gray-500">threshold：</span>
              {{ ragRetrievalMeta.similarity_threshold }}
            </div>
            <div>
              <span class="text-gray-500">vector_weight：</span>
              {{ ragRetrievalMeta.vector_similarity_weight }}
            </div>
            <div>
              <span class="text-gray-500">top_k：</span>
              {{ ragRetrievalMeta.top_k }}
            </div>
            <div v-if="ragRetrievalMeta.require_explicit_dataset">
              <span class="text-amber-700">require_explicit_dataset：true</span>
            </div>
          </div>
        </div>

        <hr v-if="ragRetrievalMeta" class="border-gray-100" />

        <!-- Live Context Stack (New) -->
        <div class="flex-1 min-h-0 flex flex-col">
          <AgentContextStack :context="agentContext" @clear="(k: string) => emit('clear-context', k)" />
        </div>
      </div>
      <div class="p-4 bg-gray-50 border-t border-gray-100 text-center">
        <span class="text-xs text-gray-400"
          >更改将在下次对话生效</span
        >
      </div>
    </div>
  </transition>
</template>

<style scoped>
.slide-fade-enter-active,
.slide-fade-leave-active {
  transition: all 0.3s ease;
}

.slide-fade-enter-from,
.slide-fade-leave-to {
  transform: translateX(20px);
  opacity: 0;
}
</style>
