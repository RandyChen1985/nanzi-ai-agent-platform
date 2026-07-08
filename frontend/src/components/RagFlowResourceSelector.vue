<script setup lang="ts">
import { computed, ref, onMounted, watch } from "vue";
import {
  ragflowApi,
  type RagFlowAgent,
  type RagFlowConfigSummary,
  type RagFlowDataset,
} from "@/api/ragflow";
import Modal from "@/components/Modal.vue";

const props = defineProps<{
  modelValue: boolean; // v-model for visibility
  type: "agent" | "dataset"; // Select Agent (Single) or Dataset (Multiple)
  initialSelected?: string | string[]; // For pre-selecting
  overrideUrl?: string;
  overrideKey?: string;
  includeMissing?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", val: boolean): void;
  (e: "select", val: string | string[]): void;
}>();

const loading = ref(false);
const agents = ref<RagFlowAgent[]>([]);
const datasets = ref<RagFlowDataset[]>([]);
const errorMsg = ref("");
const engineStatus = ref<"checking" | "connected" | "disconnected">("checking");
const ragflowConfig = ref<RagFlowConfigSummary | null>(null);

const selectedIds = ref<string[]>([]); // Always array for internal logic

const ragflowApiUrl = computed(() => props.overrideUrl || ragflowConfig.value?.api_url || "未配置");
const engineStatusText = computed(() => {
  if (engineStatus.value === "checking") return "知识库引擎检测中 ...";
  if (engineStatus.value === "connected") return "知识库引擎已连接";
  return "知识库引擎未连接";
});

const friendlyErrorMsg = computed(() => {
  if (!errorMsg.value) return "";
  const lower = errorMsg.value.toLowerCase();
  if (
    lower.includes("ragflow") ||
    lower.includes("bad gateway") ||
    lower.includes("failed to connect") ||
    lower.includes("configuration missing")
  ) {
    return "当前无法连接 RAGFlow 服务，请确认 RAGFlow 服务是否可访问、网关是否正常，以及系统配置中的 RAGFlow 地址/API Key 是否正确。";
  }
  return errorMsg.value;
});

const loadData = async () => {
  loading.value = true;
  engineStatus.value = "checking";
  errorMsg.value = "";
  try {
    try {
      const configRes = await ragflowApi.getConfig();
      ragflowConfig.value = (configRes.data as any)?.data || null;
    } catch {
      ragflowConfig.value = null;
    }

    if (props.type === "agent") {
      const res = await ragflowApi.listAgents(1, 100, props.overrideUrl, props.overrideKey);
      const rawData = res.data as any;

      if (rawData && Array.isArray(rawData.data)) {
        agents.value = rawData.data;
      } else if (Array.isArray(rawData)) {
        agents.value = rawData;
      } else {
        agents.value = [];
      }

      // Filter out non-existent Agent IDs
      if (selectedIds.value.length > 0) {
        const validIds = new Set(agents.value.map((a) => a.id));
        const originalCount = selectedIds.value.length;
        selectedIds.value = selectedIds.value.filter((id) => validIds.has(id));
        if (selectedIds.value.length < originalCount) {
          console.log(
            `[RagFlowResourceSelector] Filtered ${
              originalCount - selectedIds.value.length
            } invalid Agent IDs`
          );
        }
      }
    } else {
      const res = await ragflowApi.listDatasets(1, 100, props.overrideUrl, props.overrideKey, props.includeMissing);
      const rawData = res.data as any;

      // Robust parsing for RAGFlow response
      if (rawData && Array.isArray(rawData.data)) {
        datasets.value = rawData.data;
      } else if (Array.isArray(rawData)) {
        datasets.value = rawData;
      } else {
        datasets.value = [];
      }

      // Filter out non-existent Dataset IDs
      if (selectedIds.value.length > 0) {
        const validIds = new Set(datasets.value.map((d) => d.id));
        const originalCount = selectedIds.value.length;
        selectedIds.value = selectedIds.value.filter((id) => validIds.has(id));
        if (selectedIds.value.length < originalCount) {
          console.log(
            `[RagFlowResourceSelector] Filtered ${
              originalCount - selectedIds.value.length
            } invalid Dataset IDs`
          );
        }
      }
    }
    engineStatus.value = "connected";
  } catch (e: any) {
    console.error("Failed to load RAGFlow resources", e);
    errorMsg.value =
      e.response?.data?.detail || "无法连接到 RAGFlow 服务，请检查系统配置。";
    engineStatus.value = "disconnected";
  } finally {
    loading.value = false;
  }
};

// Reload when opened
watch(
  () => props.modelValue,
  (newVal) => {
    if (newVal) {
      // Initialize selection
      if (props.initialSelected) {
        selectedIds.value = Array.isArray(props.initialSelected)
          ? props.initialSelected
          : [props.initialSelected];
      }
      loadData();
    }
  }
);

onMounted(() => {
  // Initial load if already open (unlikely but safe)
  if (props.modelValue) {
    loadData();
  }
});

const toggleSelection = (id: string) => {
  if (props.type === "agent") {
    // Single Select
    selectedIds.value = [id];
    confirmSelection();
  } else {
    // Multi Select
    const idx = selectedIds.value.indexOf(id);
    if (idx > -1) selectedIds.value.splice(idx, 1);
    else selectedIds.value.push(id);
  }
};

const confirmSelection = () => {
  if (props.type === "agent") {
    emit("select", selectedIds.value[0] || "");
  } else {
    emit("select", selectedIds.value);
  }
  close();
};

const close = () => emit("update:modelValue", false);

const formatDate = (ts?: number | string) => {
  if (!ts) return "-";
  // RAGFlow usually uses ms
  return new Date(ts).toLocaleDateString();
};
</script>

<template>
  <Modal
    :show="modelValue"
    :title="type === 'agent' ? '选择 RAGFlow 智能体' : '选择知识库 (Datasets)'"
    :z-index="120"
    @close="close"
    size="max-w-3xl"
  >
    <div class="flex flex-col max-h-[calc(85vh-10rem)] min-h-[320px]">
      <div
        class="mb-3 flex-shrink-0 flex items-center justify-between gap-3 rounded-xl border px-4 py-3 text-xs"
        :class="{
          'border-blue-200 bg-blue-50 text-blue-700': engineStatus === 'checking',
          'border-emerald-200 bg-emerald-50 text-emerald-700': engineStatus === 'connected',
          'border-amber-200 bg-amber-50 text-amber-700': engineStatus === 'disconnected'
        }"
      >
        <div class="flex items-center gap-2 min-w-0">
          <span
            class="inline-block w-3 h-3 rounded-full border-2 shrink-0"
            :class="engineStatus === 'checking' ? 'border-blue-500 border-t-transparent animate-spin' : engineStatus === 'connected' ? 'bg-emerald-500 border-emerald-500' : 'bg-amber-500 border-amber-500'"
          ></span>
          <span class="font-medium whitespace-nowrap">{{ engineStatusText }}</span>
          <span class="text-gray-400 hidden sm:inline">|</span>
          <span class="hidden sm:inline-flex items-center gap-1 min-w-0">
            <span>RAGFlow 地址：</span>
            <span :title="ragflowApiUrl" class="font-mono truncate sm:max-w-[250px] inline-block align-bottom">{{ ragflowApiUrl }}</span>
          </span>
        </div>
        <span
          v-if="ragflowConfig && !ragflowConfig.api_key_configured"
          class="shrink-0 text-amber-600"
        >
          API Key 未配置
        </span>
      </div>

      <!-- Loading -->
      <div
        v-if="loading"
        class="flex-1 min-h-0 flex flex-col items-center justify-center text-gray-400"
      >
        <svg
          class="animate-spin h-8 w-8 text-primary mb-2"
          xmlns="http://www.w3.org/2000/svg"
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
        <span>知识库引擎检测中 ...</span>
      </div>

      <!-- Error -->
      <div
        v-else-if="errorMsg"
        class="flex-1 min-h-0 flex flex-col items-center justify-center p-8 text-center overflow-y-auto"
      >
        <div class="bg-red-50 p-4 rounded-full mb-3">
          <svg
            class="w-8 h-8 text-red-500"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
        </div>
        <h3 class="text-lg font-bold text-gray-800">知识库引擎未连接</h3>
        <p class="text-sm text-gray-500 mt-1 max-w-md">{{ friendlyErrorMsg }}</p>
        <p class="text-xs text-gray-400 mt-3 max-w-md">
          当前配置地址：<span :title="ragflowApiUrl" class="font-mono truncate max-w-[200px] sm:max-w-[300px] inline-block align-bottom">{{ ragflowApiUrl }}</span>
        </p>
        <p class="text-xs text-gray-400 mt-1 max-w-md">原始错误：{{ errorMsg }}</p>
        <button
          @click="loadData"
          class="mt-4 px-4 py-2 bg-white border border-gray-300 rounded hover:bg-gray-50 text-sm"
        >
          重试
        </button>
      </div>

      <!-- List（仅此区域滚动，底部按钮始终可见） -->
      <div v-else class="flex-1 min-h-0 overflow-y-auto space-y-2 p-1 custom-scrollbar">
        <!-- Agent List -->
        <template v-if="type === 'agent'">
          <div
            v-for="agent in agents"
            :key="agent.id"
            @click="toggleSelection(agent.id)"
            class="flex items-center p-3 border rounded-lg cursor-pointer hover:bg-blue-50 transition-colors group"
            :class="
              selectedIds.includes(agent.id)
                ? 'border-primary bg-blue-50/50 ring-1 ring-primary'
                : 'border-gray-200'
            "
          >
            <div
              class="w-10 h-10 rounded-full bg-indigo-100 text-indigo-600 flex items-center justify-center font-bold text-lg mr-3 shadow-sm border border-indigo-200"
            >
              {{ agent.title.charAt(0).toUpperCase() }}
            </div>
            <div class="flex-1">
              <h4
                class="text-sm font-bold text-gray-900 group-hover:text-primary"
              >
                {{ agent.title }}
              </h4>
              <p class="text-xs text-gray-500 line-clamp-1">
                {{ agent.description || "暂无描述" }}
              </p>
            </div>
            <div class="text-right">
              <span class="text-[10px] text-gray-400 block">{{
                formatDate(agent.update_time)
              }}</span>
              <span
                class="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded font-mono"
                >{{ agent.id.slice(0, 8) }}...</span
              >
            </div>
          </div>
          <div
            v-if="agents.length === 0"
            class="text-center py-10 text-gray-400"
          >
            暂无智能体数据
          </div>
        </template>

        <!-- Dataset List -->
        <template v-else>
          <div
            v-for="ds in datasets"
            :key="ds.id"
            @click="toggleSelection(ds.id)"
            class="flex items-center p-3 border rounded-lg cursor-pointer hover:bg-green-50 transition-colors group"
            :class="
              selectedIds.includes(ds.id)
                ? 'border-green-500 bg-green-50/50 ring-1 ring-green-500'
                : 'border-gray-200'
            "
          >
            <div class="mr-3">
              <div
                class="w-5 h-5 rounded border flex items-center justify-center transition-colors"
                :class="
                  selectedIds.includes(ds.id)
                    ? 'bg-green-500 border-green-500'
                    : 'bg-white border-gray-300'
                "
              >
                <svg
                  v-if="selectedIds.includes(ds.id)"
                  class="w-3.5 h-3.5 text-white"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="3"
                    d="M5 13l4 4L19 7"
                  />
                </svg>
              </div>
            </div>
            <div
              class="w-10 h-10 rounded bg-green-100 text-green-700 flex items-center justify-center mr-3 shadow-sm border border-green-200"
            >
              <svg
                class="w-5 h-5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
                />
              </svg>
            </div>
            <div class="flex-1">
              <h4
                class="text-sm font-bold text-gray-900 group-hover:text-green-700"
              >
                {{ ds.platform_name || ds.name }}
              </h4>
              <div class="flex items-center space-x-2 mt-0.5">
                <span
                  class="text-[10px] bg-gray-100 px-1.5 rounded text-gray-500"
                  >{{ ds.doc_count ?? ds.document_count ?? 0 }} Docs</span
                >
                <span
                  class="text-[10px] bg-gray-100 px-1.5 rounded text-gray-500"
                  >{{ (ds.chunk_count || 0).toLocaleString() }} Chunks</span
                >
              </div>
            </div>
            <div class="text-right">
              <span class="text-[10px] text-gray-400 block">{{
                formatDate(ds.update_time)
              }}</span>
              <span class="text-[10px] font-mono text-gray-400"
                >{{ ds.id.slice(0, 8) }}...</span
              >
            </div>
          </div>
          <div
            v-if="datasets.length === 0"
            class="text-center py-10 text-gray-400"
          >
            暂无知识库数据
          </div>
        </template>
      </div>

      <!-- Footer (Multi-select only) -->
      <div
        v-if="type === 'dataset' && !loading"
        class="flex-shrink-0 pt-3 mt-3 border-t border-gray-100 flex justify-between items-center bg-white"
      >
        <span class="text-xs text-gray-500"
          >已选: {{ selectedIds.length }} 个</span
        >
        <div class="flex items-center gap-2">
          <button
            @click="close"
            class="px-4 py-2 text-sm text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="confirmSelection"
            :disabled="engineStatus !== 'connected'"
            class="px-6 py-2 bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors font-medium text-sm shadow-sm"
            :class="{ 'opacity-50 cursor-not-allowed': engineStatus !== 'connected' }"
          >
            确认选择
          </button>
        </div>
      </div>
    </div>
  </Modal>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.12);
  border-radius: 10px;
}
</style>
