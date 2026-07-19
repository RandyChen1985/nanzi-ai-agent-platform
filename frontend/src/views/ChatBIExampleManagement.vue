<script setup lang="ts">
import { ref, onMounted, computed, watch } from "vue";
import axios from "@/utils/axios";
import { useToast } from "../composables/useToast";
import { useUser } from "../composables/useUser";
import Modal from "../components/Modal.vue";
import ConfirmModal from "../components/ConfirmModal.vue";
import MessageRenderer from "../components/MessageRenderer.vue";
import {
  HandThumbUpIcon,
  HandThumbDownIcon,
  CheckCircleIcon,
  XCircleIcon,
  CloudArrowUpIcon,
  TrashIcon,
  InformationCircleIcon,
  ArrowPathIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  DocumentDuplicateIcon,
  MagnifyingGlassIcon
} from "@heroicons/vue/24/outline";

const { hasPermission } = useUser();
const { showToast } = useToast();

const copyToClipboard = async (text: string) => {
  try {
    await navigator.clipboard.writeText(text);
    showToast("已复制到剪贴板", "success");
  } catch (err) {
    showToast("复制失败", "error");
  }
};
interface ChatBIExample {
  id: number;
  trace_id: string;
  agent_id: string;
  dataset_id: number;
  user_query: string;
  refined_query?: string;
  context_summary?: string;
  sql_text: string;
  sql_metadata?: any;
  enhance_status: 'pending' | 'success' | 'failed';
  ai_answer: string;
  feedback_type: 'up' | 'down';
  use_count: number;
  status: 'pending' | 'approved' | 'rejected' | 'deprecated';
  rag_sync_status: 'pending' | 'synced' | 'failed' | 'removed';
  rag_sync_error?: string;
  rag_synced_at?: string;
  created_at: string;
  user_real_name?: string;
  agent_display_name?: string;
}


const examples = ref<ChatBIExample[]>([]);
const loading = ref(false);
const actionLoading = ref<Record<number, boolean>>({}); // 记录每一行的操作状态
const total = ref(0);
const page = ref(1);
const pageSize = ref(10);
const showSyncAllConfirm = ref(false);

/** 默认进入待审核，便于审核工作流 */
const filterStatus = ref("pending");
const filterId = ref<number | null>(null);
const filterAgentId = ref("");
const filterDatasetId = ref<number | null>(null);
const searchQuery = ref("");

const statusTabs = [
  { value: "pending", label: "待审核" },
  { value: "approved", label: "已通过" },
  { value: "rejected", label: "已驳回" },
  { value: "deprecated", label: "已废弃" },
  { value: "", label: "全部" },
] as const;

const hasExtraFilters = computed(() =>
  Boolean(
    searchQuery.value.trim() ||
    filterId.value ||
    filterAgentId.value.trim() ||
    filterDatasetId.value
  )
);

/** 相对默认「待审核」是否偏离，用于显示「重置」 */
const hasActiveFilters = computed(
  () => hasExtraFilters.value || filterStatus.value !== "pending"
);

const fetchExamples = async () => {
  loading.value = true;
  try {
    const params: any = {
      page: page.value,
      size: pageSize.value
    };
    if (filterId.value) params.id = filterId.value;
    if (filterStatus.value) params.status = filterStatus.value;
    if (filterAgentId.value.trim()) params.agent_id = filterAgentId.value.trim();
    if (filterDatasetId.value) params.dataset_id = filterDatasetId.value;
    if (searchQuery.value.trim()) params.search = searchQuery.value.trim();

    const res = await axios.get("/api/portal/chatbi-examples", { params });
    if (res.data.code === 200) {
      examples.value = res.data.data.items;
      total.value = res.data.data.total;
    }
  } catch (error) {
    showToast("获取经验库失败", "error");
  } finally {
    loading.value = false;
  }
};

let fetchTimer: ReturnType<typeof setTimeout> | null = null;
const scheduleFetch = (resetPage = true) => {
  if (fetchTimer) clearTimeout(fetchTimer);
  fetchTimer = setTimeout(() => {
    if (resetPage) page.value = 1;
    fetchExamples();
  }, 300);
};

const selectStatusTab = (value: string) => {
  filterStatus.value = value;
  page.value = 1;
  fetchExamples();
};

const resetFilters = () => {
  filterId.value = null;
  filterAgentId.value = "";
  filterDatasetId.value = null;
  searchQuery.value = "";
  filterStatus.value = "pending";
  page.value = 1;
  fetchExamples();
};

watch([searchQuery, filterAgentId], () => scheduleFetch(true));
watch([filterId, filterDatasetId], () => scheduleFetch(true));

const auditExample = async (id: number, status: string) => {
  if (actionLoading.value[id]) return;
  actionLoading.value[id] = true;
  try {
    const res = await axios.post("/api/portal/chatbi-examples/audit", { id, status });
    if (res.data.code === 200) {
      showToast("审核操作已完成", "success");
      await fetchExamples();
    }
  } catch (error) {
    showToast("操作失败", "error");
  } finally {
    delete actionLoading.value[id];
  }
};

const syncAllToRag = async () => {
  showSyncAllConfirm.value = false;
  if (loading.value) return;

  loading.value = true;
  try {
    const res = await axios.post("/api/portal/chatbi-examples/sync-all");
    if (res.data.code === 200) {
      showToast(res.data.message || "一键同步任务已启动", "success");
      // 3秒后自动刷新
      setTimeout(() => {
        fetchExamples();
      }, 3000);
    }
  } catch (error) {
    showToast("一键同步失败", "error");
  } finally {
    loading.value = false;
  }
};

const syncToRag = async (id: number) => {
  if (actionLoading.value[id]) return;
  actionLoading.value[id] = true;
  try {
    const res = await axios.post(`/api/portal/chatbi-examples/sync/${id}`);
    if (res.data.code === 200) {
      showToast("已触发同步任务", "success");
      // 3秒后自动刷新
      setTimeout(() => {
        fetchExamples();
      }, 3000);
    }
  } catch (error) {
    showToast("同步请求失败", "error");
  } finally {
    delete actionLoading.value[id];
  }
};

const showDetailModal = ref(false);
const currentExample = ref<ChatBIExample | null>(null);
const showAnswer = ref(false);
const isUpdating = ref(false);

const viewDetail = (example: ChatBIExample) => {
  currentExample.value = { ...example }; // 使用副本以便编辑
  showDetailModal.value = true;
  showAnswer.value = false;
};

const updateExample = async () => {
  if (!currentExample.value || isUpdating.value) return;
  isUpdating.value = true;
  try {
    const { id, user_query, refined_query, context_summary, sql_text, sql_metadata } = currentExample.value;
    const res = await axios.put(`/api/portal/chatbi-examples/${id}`, {
      user_query,
      refined_query,
      context_summary,
      sql_text,
      sql_metadata
    });
    if (res.data.code === 200) {
      showToast("更新成功", "success");
      await fetchExamples();
      showDetailModal.value = false; // 保存成功后关闭弹窗
      return true;
    }
  } catch (error) {
    showToast("更新失败", "error");
  } finally {
    isUpdating.value = false;
  }
  return false;
};

const isEnhanceTimedOut = (example: ChatBIExample) => {
  if (!example.created_at) return false;
  const created = new Date(example.created_at).getTime();
  const now = new Date().getTime();
  return (now - created) > 30 * 1000; // 超过 30 秒认为超时
};

const manualEnhance = async () => {
  if (!currentExample.value || isUpdating.value) return;
  isUpdating.value = true;
  try {
    const res = await axios.post(`/api/portal/chatbi-examples/${currentExample.value.id}/enhance`);
    if (res.data.code === 200) {
      showToast("智能增强任务已启动，请稍后刷新查看", "success");
      currentExample.value.enhance_status = 'pending';
      // 3秒后自动刷新一次详情数据
      setTimeout(async () => {
        const checkRes = await axios.get("/api/portal/chatbi-examples", { params: { trace_id: currentExample.value?.trace_id } });
        if (checkRes.data.code === 200 && checkRes.data.data.items.length > 0) {
          currentExample.value = { ...checkRes.data.data.items[0] };
        }
      }, 3000);
    }
  } catch (error) {
    showToast("触发失败", "error");
  } finally {
    isUpdating.value = false;
  }
};


const getStatusLabel = (status: string) => {
  const map: Record<string, { label: string, color: string }> = {
    pending: { label: '待审核', color: 'bg-yellow-100 text-yellow-800' },
    approved: { label: '已通过', color: 'bg-green-100 text-green-800' },
    rejected: { label: '已驳回', color: 'bg-red-100 text-red-800' },
    deprecated: { label: '已废弃', color: 'bg-gray-100 text-gray-800' }
  };
  return map[status] || { label: status, color: 'bg-gray-100 text-gray-800' };
};

const getRagStatusLabel = (status: string) => {
  const map: Record<string, { label: string, color: string }> = {
    pending: { label: '未同步', color: 'text-gray-400' },
    synced: { label: '已同步', color: 'text-green-600' },
    failed: { label: '失败', color: 'text-red-600' },
    removed: { label: '已移除', color: 'text-orange-600' }
  };
  return map[status] || { label: status, color: 'text-gray-400' };
};

// 特殊逻辑：判断是否显示“已变更”提醒
const getRagStatusDisplay = (ex: ChatBIExample) => {
  // 如果后端返回的是 pending，且之前有过同步记录（rag_synced_at 不为空），说明内容被修改过
  if (ex.rag_sync_status === 'pending' && ex.rag_synced_at) {
    return { label: '已变更 (待同步)', color: 'text-orange-500 font-bold animate-pulse' };
  }
  return getRagStatusLabel(ex.rag_sync_status);
};

// --- RAGFlow 连通性探测及配置 ---
type RagFlowConfigSummary = {
  api_url: string;
  api_key_configured: boolean;
  metadata_provider?: string;
}

const ragflowConfig = ref<RagFlowConfigSummary | null>(null);
const engineStatus = ref<'checking' | 'connected' | 'disconnected'>('checking');
const errorMessage = ref('');
const showErrorBanner = ref(true);

const isLocalMode = computed(() => ragflowConfig.value?.metadata_provider === 'local');
const isEngineReady = computed(() => {
  if (isLocalMode.value) return false;
  return engineStatus.value === 'connected' && !loading.value;
});
const engineStatusText = computed(() => {
  if (isLocalMode.value) return '本地已就绪';
  if (engineStatus.value === 'checking') return '连接中...';
  if (engineStatus.value === 'connected') return '已连接';
  return '未连接';
});

const ragflowApiUrl = computed(() => ragflowConfig.value?.api_url || '未配置');

const friendlyRagFlowError = computed(() => {
  if (!errorMessage.value) return '';
  const lower = errorMessage.value.toLowerCase();
  if (
    lower.includes('ragflow') ||
    lower.includes('api_key') ||
    lower.includes('connect') ||
    lower.includes('refused')
  ) {
    return '当前无法连接 RAGFlow 服务，请确认 RAGFlow 服务是否可访问、网关是否正常，以及系统配置中的 RAGFlow 地址/API Key 是否正确。';
  }
  return errorMessage.value;
});

const fetchRagFlowConfig = async () => {
  try {
    const response = await axios.get('/api/portal/ragflow/config');
    ragflowConfig.value = response.data?.data || null;
  } catch (e) {
    ragflowConfig.value = null;
  }
};

const checkRagFlowConnectivity = async () => {
  engineStatus.value = 'checking';
  errorMessage.value = '';
  try {
    await axios.get('/api/portal/ragflow/datasets', { params: { page_size: 1 } });
    engineStatus.value = 'connected';
  } catch (err: any) {
    errorMessage.value = err.response?.data?.detail || err.message || '连接失败';
    engineStatus.value = 'disconnected';
    showErrorBanner.value = true;
  }
};

onMounted(async () => {
  fetchExamples();
  await fetchRagFlowConfig();
  if (!isLocalMode.value) {
    checkRagFlowConnectivity();
  } else {
    engineStatus.value = 'connected';
  }
});
</script>

<template>
  <div class="space-y-5">
    <!-- Header：与任务/技能工作台一致 -->
    <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-3">
          <h1 class="text-xl sm:text-2xl font-bold text-gray-900">案例集管理</h1>
          <div
            class="flex items-center gap-2 px-2.5 py-1 rounded-lg text-xs border transition-colors shrink-0 group relative cursor-default"
            :class="{
              'border-blue-200 bg-blue-50/50 text-blue-700': !isLocalMode && engineStatus === 'checking',
              'border-emerald-200 bg-emerald-50/50 text-emerald-700': isLocalMode || engineStatus === 'connected',
              'border-amber-200 bg-amber-50/50 text-amber-700': !isLocalMode && engineStatus === 'disconnected'
            }"
          >
            <span
              class="inline-block w-2 h-2 rounded-full"
              :class="{
                'bg-blue-500 animate-pulse': !isLocalMode && engineStatus === 'checking',
                'bg-emerald-500': isLocalMode || engineStatus === 'connected',
                'bg-amber-500': !isLocalMode && engineStatus === 'disconnected'
              }"
            ></span>
            <span class="font-medium">引擎 {{ engineStatusText }}</span>
            <span class="absolute top-full left-0 mt-2 hidden group-hover:block bg-slate-900 text-white text-xs p-2.5 rounded-lg shadow-xl z-50 text-left font-sans font-normal pointer-events-none w-56">
              <div class="font-medium mb-1 border-b border-white/10 pb-1">知识库引擎信息</div>
              <template v-if="isLocalMode">
                <div class="opacity-80">运行模式: 本地 Redis 向量检索</div>
                <div class="opacity-80 mt-0.5 text-emerald-400 font-medium">无需连接外部 RAGFlow</div>
              </template>
              <template v-else>
                <div class="opacity-80 truncate">地址: {{ ragflowApiUrl }}</div>
                <div class="opacity-80 mt-1">
                  API Key:
                  <span v-if="ragflowConfig?.api_key_configured" class="text-emerald-400">已配置</span>
                  <span v-else class="text-amber-400">未配置</span>
                </div>
              </template>
            </span>
          </div>
        </div>
        <p class="text-sm text-gray-500 mt-1">管理 ChatBI 问答经验样本及 RAG 语义对齐案例</p>
      </div>

      <div class="flex w-full flex-col gap-2.5 sm:w-auto sm:flex-row sm:items-center sm:gap-3">
        <div class="flex w-full items-center gap-2 sm:w-auto">
          <div class="relative min-w-0 flex-1 sm:w-56 sm:flex-none lg:w-72">
            <span class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
              <MagnifyingGlassIcon class="h-4 w-4 text-gray-400" />
            </span>
            <input
              v-model="searchQuery"
              type="text"
              placeholder="搜索提问内容..."
              class="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm shadow-sm outline-none transition-all focus:border-primary focus:ring-2 focus:ring-primary/20"
            />
          </div>
          <button
            type="button"
            class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-500 shadow-sm transition-colors hover:bg-gray-50 hover:text-primary"
            title="刷新列表"
            @click="fetchExamples"
          >
            <ArrowPathIcon class="h-4 w-4" :class="{ 'animate-spin': loading }" />
          </button>
        </div>

        <button
          v-if="hasPermission('element:chatbi_example:sync') && !isLocalMode"
          type="button"
          class="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3.5 py-2 text-sm font-medium text-indigo-700 shadow-sm transition-all hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          :disabled="loading || !isEngineReady"
          :title="!isEngineReady ? 'RAGFlow 服务未就绪' : '一键同步至 RAGFlow'"
          @click="isEngineReady && (showSyncAllConfirm = true)"
        >
          <CloudArrowUpIcon class="h-4 w-4 text-indigo-600" />
          <span class="hidden sm:inline">一键同步</span>
          <span class="sm:hidden">同步</span>
        </button>
      </div>
    </div>

    <!-- Error Banner -->
    <div v-if="errorMessage && showErrorBanner && !isLocalMode" class="relative rounded-2xl border border-amber-200 bg-amber-50 p-4 pr-10 text-sm text-amber-800 shadow-sm flex items-start gap-3">
      <svg class="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
      <div>
        <div class="font-semibold text-amber-900">RAGFlow 服务连通性故障</div>
        <div class="mt-1 text-amber-800/90">{{ friendlyRagFlowError }}</div>
        <div class="mt-2 text-xs font-mono text-amber-700 bg-amber-100/50 p-2 rounded-lg">
          <div>连接地址: <a :href="ragflowApiUrl" target="_blank" rel="noopener noreferrer" :title="ragflowApiUrl" class="hover:underline truncate max-w-[200px] sm:max-w-[300px] inline-block align-bottom">{{ ragflowApiUrl }}</a></div>
          <div class="mt-0.5">错误日志: {{ errorMessage }}</div>
        </div>
      </div>
      <button @click="showErrorBanner = false" class="absolute top-4 right-4 text-amber-500 hover:text-amber-700 transition-colors" title="关闭">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- 审核状态 Tab -->
    <div class="border-b border-gray-200">
      <div class="flex gap-1 overflow-x-auto -mb-px" style="-webkit-overflow-scrolling: touch;">
        <button
          v-for="tab in statusTabs"
          :key="tab.value || 'all'"
          type="button"
          class="inline-flex shrink-0 items-center px-3 sm:px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap"
          :class="filterStatus === tab.value ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
          @click="selectStatusTab(tab.value)"
        >
          {{ tab.label }}
        </button>
      </div>
    </div>

    <!-- 次级筛选：ID / 数据集 / Agent -->
    <div class="flex flex-col sm:flex-row sm:flex-wrap sm:items-end gap-2.5 sm:gap-3">
      <div class="w-full sm:w-28">
        <label class="block text-xs font-medium text-gray-500 mb-1">序号 ID</label>
        <input
          type="number"
          v-model="filterId"
          placeholder="ID"
          class="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm shadow-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
        />
      </div>
      <div class="w-full sm:w-28">
        <label class="block text-xs font-medium text-gray-500 mb-1">数据集 ID</label>
        <input
          type="number"
          v-model="filterDatasetId"
          placeholder="Dataset"
          class="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm shadow-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
        />
      </div>
      <div class="w-full sm:w-40">
        <label class="block text-xs font-medium text-gray-500 mb-1">Agent ID</label>
        <input
          type="text"
          v-model="filterAgentId"
          placeholder="搜索 Agent"
          class="w-full px-2.5 py-1.5 border border-gray-300 rounded-lg text-sm shadow-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
        />
      </div>
      <button
        v-if="hasActiveFilters"
        type="button"
        @click="resetFilters"
        class="text-sm text-gray-500 hover:text-gray-800 px-2 py-1.5 transition-colors shrink-0"
      >
        重置筛选
      </button>
    </div>

    <!-- Table Card -->
    <div class="bg-white shadow-sm rounded-lg border border-gray-200 overflow-hidden">
      <div class="overflow-x-auto">
        <table class="min-w-full divide-y divide-gray-200">
          <thead class="bg-gray-50">
            <tr>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">ID</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider min-w-[12rem]">用户提问</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">反馈</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">状态</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">RAG 同步</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">智能体</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">时间</th>
              <th scope="col" class="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">操作</th>
            </tr>
          </thead>
          <tbody class="bg-white divide-y divide-gray-200">
            <tr v-if="!loading && examples.length === 0">
              <td colspan="8" class="px-6 py-14 text-center">
                <p class="text-sm text-gray-500 font-medium">
                  <template v-if="hasExtraFilters || filterStatus !== 'pending'">
                    没有符合当前筛选条件的案例
                  </template>
                  <template v-else-if="filterStatus === 'pending'">
                    暂无待审核案例
                  </template>
                  <template v-else>
                    暂无案例数据
                  </template>
                </p>
                <p v-if="hasActiveFilters" class="text-xs text-gray-400 mt-1.5">可调整关键词、状态或 ID 后重试</p>
                <button
                  v-if="hasActiveFilters"
                  type="button"
                  @click="resetFilters"
                  class="mt-4 inline-flex items-center px-3.5 py-2 text-sm font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors"
                >
                  清除筛选
                </button>
              </td>
            </tr>
            <tr v-for="ex in examples" :key="ex.id" class="hover:bg-gray-50/80 transition-colors">
              <td class="px-4 py-3.5 whitespace-nowrap text-sm font-mono text-gray-500">
                #{{ ex.id }}
              </td>
              <td class="px-4 py-3.5">
                <div class="flex items-start group max-w-md">
                  <div class="text-sm font-medium text-gray-900 line-clamp-2 flex-1">{{ ex.user_query }}</div>
                  <button @click="copyToClipboard(ex.user_query)" class="ml-1.5 p-1 text-gray-400 hover:text-blue-600 opacity-0 group-hover:opacity-100 transition-all shrink-0" title="复制提问">
                    <DocumentDuplicateIcon class="w-4 h-4" />
                  </button>
                </div>
                <button
                  type="button"
                  class="mt-1 text-[10px] text-gray-400 font-mono hover:text-blue-600 transition-colors"
                  :title="'点击复制完整 Trace: ' + ex.trace_id"
                  @click="copyToClipboard(ex.trace_id)"
                >
                  Trace {{ ex.trace_id.substring(0, 8) }}…
                </button>
              </td>
              <td class="px-4 py-3.5 whitespace-nowrap">
                <div class="flex items-center gap-1.5">
                  <HandThumbUpIcon v-if="ex.feedback_type === 'up'" class="w-5 h-5 text-green-500" />
                  <HandThumbDownIcon v-else class="w-5 h-5 text-red-500" />
                  <span class="text-xs text-gray-500">引用 {{ ex.use_count }}</span>
                </div>
              </td>
              <td class="px-4 py-3.5 whitespace-nowrap">
                <span :class="['px-2.5 py-0.5 text-xs rounded-full font-medium border', getStatusLabel(ex.status).color]">
                  {{ getStatusLabel(ex.status).label }}
                </span>
              </td>
              <td class="px-4 py-3.5 whitespace-nowrap">
                <div class="flex flex-col">
                  <span :class="['text-xs font-semibold', getRagStatusDisplay(ex).color]">
                    {{ getRagStatusDisplay(ex).label }}
                  </span>
                  <span v-if="ex.rag_synced_at" class="text-[10px] text-gray-400 font-mono">{{ new Date(ex.rag_synced_at).toLocaleString() }}</span>
                </div>
              </td>
              <td class="px-4 py-3.5 whitespace-nowrap">
                <div class="text-xs font-medium text-gray-800">{{ ex.agent_display_name || '未知智能体' }}</div>
                <div class="text-[10px] text-gray-400 mt-0.5">{{ ex.user_real_name || '系统' }} · Dataset {{ ex.dataset_id }}</div>
              </td>
              <td class="px-4 py-3.5 whitespace-nowrap text-xs text-gray-500 font-mono">
                {{ new Date(ex.created_at).toLocaleString() }}
              </td>
              <td class="px-4 py-3.5 whitespace-nowrap text-right text-sm font-medium">
                <div class="flex justify-end space-x-1">
                  <button @click="viewDetail(ex)" class="p-1.5 rounded-full text-blue-600 hover:bg-blue-50 active:scale-90 transition-all" title="查看详情">
                    <InformationCircleIcon class="w-5 h-5" />
                  </button>

                  <template v-if="actionLoading[ex.id]">
                    <div class="p-1.5">
                      <ArrowPathIcon class="w-5 h-5 text-gray-400 animate-spin" />
                    </div>
                  </template>
                  <template v-else>
                    <button v-if="hasPermission('element:chatbi_example:audit') && ex.status === 'pending'" @click="auditExample(ex.id, 'approved')" class="p-1.5 rounded-full text-green-600 hover:bg-green-50 active:scale-90 transition-all" title="批准">
                      <CheckCircleIcon class="w-5 h-5" />
                    </button>
                    <button v-if="hasPermission('element:chatbi_example:audit') && ex.status === 'pending'" @click="auditExample(ex.id, 'rejected')" class="p-1.5 rounded-full text-red-600 hover:bg-red-50 active:scale-90 transition-all" title="驳回">
                      <XCircleIcon class="w-5 h-5" />
                    </button>
                    <button v-if="hasPermission('element:chatbi_example:sync') && ex.status === 'approved' && !isLocalMode" @click="isEngineReady && syncToRag(ex.id)" :disabled="!isEngineReady" class="p-1.5 rounded-full text-indigo-600 hover:bg-indigo-50 active:scale-90 transition-all disabled:opacity-50 disabled:cursor-not-allowed" :title="!isEngineReady ? 'RAGFlow 服务未就绪' : '同步到 RAGFlow'">
                      <CloudArrowUpIcon class="w-5 h-5" />
                    </button>
                    <button v-if="hasPermission('element:chatbi_example:delete') && ex.status !== 'deprecated'" @click="auditExample(ex.id, 'deprecated')" class="p-1.5 rounded-full text-gray-400 hover:bg-gray-100 active:scale-90 transition-all" title="废弃">
                      <TrashIcon class="w-5 h-5" />
                    </button>
                  </template>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Footer/Pagination -->
      <div class="bg-gray-50 px-4 py-3 border-t border-gray-200 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div class="flex flex-wrap items-center gap-3 text-sm text-gray-700">
          <p>
            第 <span class="font-medium">{{ page }}</span> 页，共 <span class="font-medium">{{ total }}</span> 条
          </p>
          <select
            v-model.number="pageSize"
            @change="page = 1; fetchExamples()"
            class="text-sm border border-gray-300 rounded-lg py-1.5 px-2 bg-white shadow-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
          >
            <option :value="10">10 条/页</option>
            <option :value="20">20 条/页</option>
            <option :value="50">50 条/页</option>
          </select>
        </div>
        <nav class="inline-flex rounded-md shadow-sm -space-x-px">
          <button
            @click="page > 1 && (page--, fetchExamples())"
            :disabled="page <= 1"
            class="relative inline-flex items-center px-2 py-2 rounded-l-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50 transition-all"
          >
            <ChevronLeftIcon class="h-5 w-5" />
          </button>
          <button
            @click="page * pageSize < total && (page++, fetchExamples())"
            :disabled="page * pageSize >= total"
            class="relative inline-flex items-center px-2 py-2 rounded-r-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50 transition-all"
          >
            <ChevronRightIcon class="h-5 w-5" />
          </button>
        </nav>
      </div>
    </div>

    <Modal :show="showDetailModal" @close="showDetailModal = false" :title="currentExample ? 'SQL 经验详情 - ' + currentExample.trace_id.substring(0,8) : '详情'" size="max-w-4xl">
      <div v-if="currentExample" class="space-y-6">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="space-y-4">
            <div>
              <div class="flex items-center justify-between mb-1">
                <label class="block text-xs font-semibold text-gray-500 uppercase tracking-wider">原始提问 (可根据需要微调)</label>
                <button @click="copyToClipboard(currentExample.user_query)" class="text-xs text-blue-600 hover:text-blue-800 flex items-center">
                  <DocumentDuplicateIcon class="w-3 h-3 mr-1" /> 复制
                </button>
              </div>
              <textarea
                v-model="currentExample.user_query"
                :disabled="currentExample.status === 'rejected' || currentExample.status === 'deprecated'"
                rows="3"
                placeholder="输入原始用户问题..."
                class="w-full text-sm text-gray-900 bg-gray-50 p-3 rounded-lg border border-gray-200 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none shadow-inner transition-all disabled:bg-gray-50 disabled:text-gray-500 disabled:border-gray-200"
              ></textarea>
            </div>            <div>
              <div class="flex items-center justify-between mb-1">
                <label class="block text-xs font-semibold text-blue-600 uppercase tracking-wider">🎯 核心意图 (自动还原/可手动微调)</label>
                <div class="flex items-center space-x-2">
                    <span v-if="currentExample.enhance_status === 'pending'" class="text-[10px] text-orange-500 animate-pulse font-medium flex items-center">
                        <ArrowPathIcon class="w-3 h-3 mr-1 animate-spin" /> ✨ AI 智能增强中...
                    </span>
                    <span v-else-if="currentExample.enhance_status === 'failed'" class="text-[10px] text-red-500 font-medium">❌ 自动增强失败</span>
                    <span v-else-if="currentExample.enhance_status === 'success'" class="text-[10px] text-green-500 font-medium">✅ AI 自动增强已完成</span>
                    
                    <button 
                        v-if="currentExample.status !== 'rejected' && currentExample.status !== 'deprecated' && (currentExample.enhance_status !== 'pending' || isEnhanceTimedOut(currentExample))"
                        @click="manualEnhance" 
                        class="text-[10px] text-blue-500 hover:text-blue-700 underline decoration-dotted"
                        title="再次调用 AI 重新分析对话历史并提取意图"
                    >
                        {{ currentExample.enhance_status === 'pending' ? '强制重新生成' : '重新智能生成' }}
                    </button>
                </div>
              </div>
              <input 
                type="text" 
                v-model="currentExample.refined_query" 
                :disabled="currentExample.status === 'rejected' || currentExample.status === 'deprecated'"
                placeholder="例如：查询2025年全年的销售总额"
                class="w-full text-sm text-gray-900 bg-white p-3 rounded-lg border border-blue-200 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none shadow-sm transition-all disabled:bg-gray-50 disabled:text-gray-500 disabled:border-gray-200"
              />
            </div>
            <div>
              <label class="block text-xs font-semibold text-gray-500 mb-1 uppercase tracking-wider">🌐 业务背景/上下文摘要</label>
              <textarea 
                v-model="currentExample.context_summary" 
                :disabled="currentExample.status === 'rejected' || currentExample.status === 'deprecated'"
                rows="3"
                placeholder="简述对话脉络，例如：用户先查了分布，后追问对比。"
                class="w-full text-sm text-gray-900 bg-white p-3 rounded-lg border border-gray-200 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none shadow-sm transition-all disabled:bg-gray-50 disabled:text-gray-500 disabled:border-gray-200"
              ></textarea>
            </div>
          </div>
          
          <div class="space-y-4">
            <div>
              <label class="block text-xs font-semibold text-gray-500 mb-1 uppercase tracking-wider">🛠 提取的 SQL (可微调优化)</label>
              <textarea 
                v-model="currentExample.sql_text" 
                :disabled="currentExample.status === 'rejected' || currentExample.status === 'deprecated'"
                rows="8"
                class="w-full text-xs bg-gray-900 text-green-400 p-4 rounded-lg font-mono border border-gray-800 shadow-inner focus:ring-2 focus:ring-green-500 outline-none disabled:opacity-80 disabled:cursor-not-allowed"
              ></textarea>
            </div>
          </div>
        </div>

        <div class="border rounded-lg overflow-hidden border-gray-200">
          <div 
            @click="showAnswer = !showAnswer" 
            class="flex items-center justify-between px-4 py-2 bg-gray-50 cursor-pointer hover:bg-gray-100 transition-colors select-none"
          >
            <label class="text-xs font-semibold text-gray-700 cursor-pointer">AI 最终总结</label>
            <div class="flex items-center space-x-2">
              <span class="text-[10px] text-gray-400">{{ showAnswer ? '点击折叠' : '点击展开内容' }}</span>
              <ChevronDownIcon 
                class="w-4 h-4 text-gray-500 transition-transform duration-300" 
                :class="{ 'rotate-180': showAnswer }" 
              />
            </div>
          </div>
          <transition
            enter-active-class="transition duration-200 ease-out"
            enter-from-class="transform scale-95 opacity-0"
            enter-to-class="transform scale-100 opacity-100"
            leave-active-class="transition duration-150 ease-in"
            leave-from-class="transform scale-100 opacity-100"
            leave-to-class="transform scale-95 opacity-0"
          >
            <div v-show="showAnswer" class="p-4 bg-white border-t border-gray-100">
              <div class="bg-blue-50/30 p-4 rounded-lg border border-blue-100/50">
                <MessageRenderer v-if="currentExample.ai_answer" :content="currentExample.ai_answer" />
                <p v-else class="text-xs text-gray-400 italic">无回答记录</p>
              </div>
            </div>
          </transition>
        </div>
        <div v-if="currentExample.rag_sync_error" class="bg-red-50 p-3 rounded border border-red-200">
          <label class="block text-xs font-bold text-red-700 mb-1">同步错误信息</label>
          <p class="text-xs text-red-600">{{ currentExample.rag_sync_error }}</p>
        </div>
        <div class="flex justify-between items-center pt-4 border-t">
          <div class="text-[10px] text-gray-400">
            <span v-if="currentExample.rag_sync_status === 'synced'" class="text-green-500 flex items-center">
              <CheckCircleIcon class="w-3 h-3 mr-1" /> 已同步到 RAGFlow
            </span>
            <span v-else-if="currentExample.rag_sync_status === 'pending' && currentExample.rag_synced_at" class="text-orange-500 flex items-center font-bold">
              <InformationCircleIcon class="w-3 h-3 mr-1" /> 内容已变更，建议重新同步
            </span>
            <span v-else>未同步或内容已变更</span>
          </div>
          <div class="flex space-x-3">
            <button @click="showDetailModal = false" class="px-4 py-2 border border-gray-300 text-gray-700 rounded-md hover:bg-gray-50 transition-all">关闭</button>
            
            <template v-if="currentExample.status !== 'rejected' && currentExample.status !== 'deprecated'">
                <button @click="updateExample" :disabled="isUpdating" class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 shadow-md flex items-center transition-all active:scale-95">
                <ArrowPathIcon v-if="isUpdating" class="w-4 h-4 mr-2 animate-spin" />
                保存修改
                </button>
            </template>
          </div>
        </div>
      </div>
    </Modal>

    <!-- 一键同步确认弹窗 -->
    <ConfirmModal
      v-if="showSyncAllConfirm"
      title="一键同步确认"
      message="确定要将所有状态为'已通过'的案例重新同步到 RAGFlow 吗？这可能会覆盖 RAGFlow 中已有的对应记录。"
      confirmText="开始同步"
      type="primary"
      @confirm="syncAllToRag"
      @cancel="showSyncAllConfirm = false"
    />
  </div>
</template>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
