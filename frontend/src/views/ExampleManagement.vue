<script setup lang="ts">
import { ref, onMounted, computed, watch } from "vue";
import { useRouter } from 'vue-router';
import ExampleFlowGuideBanner from '../components/example/ExampleFlowGuideBanner.vue';
import ExampleSearchTestModal from '../components/example/ExampleSearchTestModal.vue';
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
  MagnifyingGlassIcon,
  EllipsisHorizontalIcon,
  PencilSquareIcon,
  DocumentArrowDownIcon
} from "@heroicons/vue/24/outline";
import { copyToClipboard as copyText } from "../utils/clipboard";

const { hasPermission } = useUser();
const { showToast } = useToast();
const router = useRouter();
const EXAMPLE_FLOW_GUIDE_KEY = 'nanzi_example_flow_guide_dismissed';
const showExampleFlowGuide = ref(localStorage.getItem(EXAMPLE_FLOW_GUIDE_KEY) !== 'true');

const handleCloseExampleFlowGuide = () => {
  showExampleFlowGuide.value = false;
};

const handleDismissExampleFlowGuide = () => {
  showExampleFlowGuide.value = false;
  localStorage.setItem(EXAMPLE_FLOW_GUIDE_KEY, 'true');
};

const restoreExampleFlowGuide = () => {
  localStorage.removeItem(EXAMPLE_FLOW_GUIDE_KEY);
  showExampleFlowGuide.value = true;
};

const showHelp = ref(false);
const activeHelpTab = ref<'flow' | 'dataflow' | 'practice'>('flow');

/** 案例集检索测试弹窗（模拟器） */
const searchTestModalRef = ref<InstanceType<typeof ExampleSearchTestModal> | null>(null);

const handleExampleBannerAction = (type: 'sync_all') => {
  if (type === 'sync_all') {
    showSyncAllConfirm.value = true;
  }
};

const copyToClipboard = async (text: string) => {
  const ok = await copyText(text);
  if (ok) {
    showToast("已复制到剪贴板", "success");
  } else {
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
  category?: 'general' | 'knowledge' | 'data_query' | string;
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
  user_account_name?: string;
  agent_display_name?: string;
}


const examples = ref<ChatBIExample[]>([]);
const loading = ref(false);
const actionLoading = ref<Record<number, boolean>>({}); // 记录每一行的操作状态
const total = ref(0);
const page = ref(1);
const pageSize = ref(10);
const showSyncAllConfirm = ref(false);
// 请求版本号，用于丢弃过期请求的响应，防止切换 tab 时旧数据覆盖新数据
let fetchVersion = 0;

/** 默认进入待审核，便于审核工作流 */
const filterStatus = ref("pending");
const filterCategory = ref("");
const searchQuery = ref("");

const statusTabs = [
  { value: "pending", label: "待审核" },
  { value: "approved", label: "已通过" },
  { value: "rejected", label: "已驳回" },
  { value: "deprecated", label: "已废弃" },
  { value: "", label: "全部" },
] as const;

/** 相对默认「待审核」是否偏离，用于显示「重置」 */
const hasActiveFilters = computed(
  () => Boolean(searchQuery.value.trim() || filterCategory.value) || filterStatus.value !== "pending"
);

const fetchExamples = async () => {
  loading.value = true;
  // 用版本号丢弃过期响应（防止切换 tab 时旧请求覆盖新数据）
  const currentVersion = ++fetchVersion;
  try {
    const params: any = {
      page: page.value,
      size: pageSize.value
    };
    if (filterStatus.value) params.status = filterStatus.value;
    if (filterCategory.value) params.category = filterCategory.value;
    if (searchQuery.value.trim()) params.search = searchQuery.value.trim();

    const res = await axios.get("/api/portal/examples", { params });
    // 只处理最新的那次请求，避免竞态导致的数据闪烁
    if (currentVersion !== fetchVersion) return;
    if (res.data.code === 200) {
      examples.value = res.data.data.items;
      total.value = res.data.data.total;
    }
  } catch (error) {
    if (currentVersion !== fetchVersion) return;
    showToast("获取经验库失败", "error");
  } finally {
    if (currentVersion === fetchVersion) {
      loading.value = false;
    }
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
  searchQuery.value = "";
  filterStatus.value = "pending";
  filterCategory.value = "";
  page.value = 1;
  fetchExamples();
};

watch([searchQuery, filterCategory, filterStatus], () => scheduleFetch(true));

const auditExample = async (id: number, status: string) => {
  if (actionLoading.value[id]) return;

  // 数据查询 (data_query) 类型的案例批准时，必须存在有效 SQL 文本
  if (status === 'approved') {
    const target = examples.value.find(ex => ex.id === id) || (currentExample.value?.id === id ? currentExample.value : null);
    if (target && target.category === 'data_query' && (!target.sql_text || !target.sql_text.trim())) {
      showToast("【数据查询】类型的案例必须包含有效的 SQL 语句，请先编辑补充 SQL 后再通过", "warning");
      return;
    }
  }

  actionLoading.value[id] = true;
  try {
    const res = await axios.post("/api/portal/examples/audit", { id, status });
    if (res.data.code === 200) {
      showToast("审核操作已完成", "success");
      await fetchExamples();
    }
  } catch (error: any) {
    const errMsg = error.response?.data?.detail || "操作失败";
    showToast(errMsg, "error");
  } finally {
    delete actionLoading.value[id];
  }
};

const syncAllToRag = async () => {
  showSyncAllConfirm.value = false;
  if (loading.value) return;

  loading.value = true;
  try {
    const res = await axios.post("/api/portal/examples/sync-all");
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
    const res = await axios.post(`/api/portal/examples/sync/${id}`);
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
const showAnswer = ref(true);
const isUpdating = ref(false);

const viewDetail = (example: ChatBIExample) => {
  currentExample.value = { 
    ...example 
  }; // 使用副本以便编辑
  showDetailModal.value = true;
  showAnswer.value = true;
};

const updateExample = async () => {
  if (!currentExample.value || isUpdating.value) return;
  isUpdating.value = true;
  try {
    const { id, user_query, refined_query, context_summary, sql_text, sql_metadata, category } = currentExample.value;
    const res = await axios.put(`/api/portal/examples/${id}`, {
      user_query,
      refined_query,
      context_summary,
      sql_text,
      sql_metadata,
      category
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

const getCategoryLabel = (category?: string) => {
  const map: Record<string, { label: string; color: string }> = {
    data_query: { label: '数据查询', color: 'bg-blue-50 text-blue-700 border-blue-200' },
    knowledge: { label: '知识库', color: 'bg-purple-50 text-purple-700 border-purple-200' },
    general: { label: '通用问答', color: 'bg-gray-50 text-gray-600 border-gray-200' }
  };
  return map[category || 'general'] || { label: '通用问答', color: 'bg-gray-50 text-gray-600 border-gray-200' };
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
    const res = await axios.post(`/api/portal/examples/${currentExample.value.id}/enhance`);
    if (res.data.code === 200) {
      showToast("智能增强任务已启动，请稍后刷新查看", "success");
      currentExample.value.enhance_status = 'pending';
      // 3秒后自动刷新一次详情数据
      setTimeout(async () => {
        const checkRes = await axios.get("/api/portal/examples", { params: { trace_id: currentExample.value?.trace_id } });
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

// --- 行内「更多」下拉菜单定位 ---
const openRowMenuExample = ref<ChatBIExample | null>(null);
const rowMenuPos = ref({ top: 0, left: 0 });

const toggleRowMenu = (ex: ChatBIExample, evt: MouseEvent) => {
  if (openRowMenuExample.value?.id === ex.id) {
    openRowMenuExample.value = null;
    return;
  }
  const rect = (evt.currentTarget as HTMLElement).getBoundingClientRect();
  rowMenuPos.value = {
    top: rect.bottom + window.scrollY + 4,
    left: rect.right + window.scrollX - 176
  };
  openRowMenuExample.value = ex;
};

const closeMenus = () => {
  openRowMenuExample.value = null;
};

/** 导出当前页/筛选结果为 Markdown 文档 */
const exportToMarkdown = () => {
  if (!examples.value || examples.value.length === 0) {
    showToast("当前列表暂无案例数据可供导出", "warning");
    return;
  }

  const nowStr = new Date().toLocaleString("zh-CN");
  let mdContent = `# 案例集管理 - 导出记录\n\n`;
  mdContent += `> 📅 **导出时间**：${nowStr}  \n`;
  mdContent += `> 🔍 **案例状态**：${getStatusLabel(filterStatus.value).label || '全部'}  \n`;
  mdContent += `> 🏷 **案例分类**：${getCategoryLabel(filterCategory.value).label || '全部分类'}  \n`;
  mdContent += `> 📊 **导出数量**：共 ${examples.value.length} 条案例\n\n`;

  mdContent += `--- \n\n`;
  mdContent += `## 📋 案例汇总列表\n\n`;
  mdContent += `| ID | 分类 | 问答提问 | AI 回答 / SQL | 状态 | 智能体 | 建立时间 |\n`;
  mdContent += `| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n`;

  examples.value.forEach((ex) => {
    const categoryName = getCategoryLabel(ex.category).label;
    const statusName = getStatusLabel(ex.status).label;
    const queryClean = (ex.user_query || '').replace(/\n/g, ' ').replace(/\|/g, '\\|');
    const answerClean = (ex.ai_answer || ex.sql_text || '无回答摘要').replace(/\n/g, ' ').replace(/\|/g, '\\|');
    const agentClean = (ex.agent_display_name || ex.agent_id || '未知').replace(/\|/g, '\\|');
    const dateStr = ex.created_at ? new Date(ex.created_at).toLocaleDateString("zh-CN") : '-';

    mdContent += `| #${ex.id} | ${categoryName} | ${queryClean} | ${answerClean} | ${statusName} | ${agentClean} | ${dateStr} |\n`;
  });

  mdContent += `\n---\n\n`;
  mdContent += `## 📝 案例详细记录\n\n`;

  examples.value.forEach((ex, idx) => {
    const categoryName = getCategoryLabel(ex.category).label;
    const statusName = getStatusLabel(ex.status).label;
    mdContent += `### ${idx + 1}. #${ex.id} - ${ex.user_query.slice(0, 35)}${ex.user_query.length > 35 ? '...' : ''}\n\n`;
    mdContent += `- **案例 ID**: \`#${ex.id}\`  \n`;
    mdContent += `- **案例分类**: **${categoryName}** (\`${ex.category || 'general'}\`)  \n`;
    mdContent += `- **审核状态**: **${statusName}** (\`${ex.status}\`)  \n`;
    mdContent += `- **关联智能体**: ${ex.agent_display_name || ex.agent_id}  \n`;
    if (ex.dataset_id) mdContent += `- **数据集 ID**: Dataset #${ex.dataset_id}  \n`;
    mdContent += `- **Trace ID**: \`${ex.trace_id}\`  \n`;
    mdContent += `- **反馈类型**: ${ex.feedback_type === 'up' ? '👍 点赞' : '👎 点踩'} (历史引用 ${ex.use_count} 次)  \n`;
    mdContent += `- **创建时间**: ${ex.created_at ? new Date(ex.created_at).toLocaleString("zh-CN") : '-'}  \n\n`;

    mdContent += `#### 💬 原始用户提问 (User Query)\n\`\`\`\n${ex.user_query}\n\`\`\`\n\n`;

    if (ex.refined_query) {
      mdContent += `#### 🎯 核心独立意图 (Refined Query)\n\`\`\`\n${ex.refined_query}\n\`\`\`\n\n`;
    }

    if (ex.context_summary) {
      mdContent += `#### 🌐 上下文摘要 (Context Summary)\n\`\`\`\n${ex.context_summary}\n\`\`\`\n\n`;
    }

    if (ex.category === 'data_query' && ex.sql_text) {
      mdContent += `#### 🛠 执行 SQL 文本\n\`\`\`sql\n${ex.sql_text}\n\`\`\`\n\n`;
    }

    if (ex.ai_answer) {
      mdContent += `#### 🤖 AI 最终回答摘要\n${ex.ai_answer}\n\n`;
    }

    mdContent += `---\n\n`;
  });

  try {
    const blob = new Blob([mdContent], { type: "text/markdown;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    const fileNameDate = new Date().toISOString().slice(0, 10);
    link.setAttribute("download", `examples_export_${fileNameDate}.md`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    showToast(`已成功导出 ${examples.value.length} 条案例为 Markdown 文档`, "success");
  } catch (err) {
    showToast("导出 Markdown 文件失败", "error");
  }
};

onMounted(async () => {
  window.addEventListener("click", closeMenus);
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
          <!-- ? 规范与指引大弹窗按钮 -->
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-full border border-gray-200 bg-white text-cyan-600 shadow-sm transition-colors hover:border-cyan-300 hover:bg-cyan-50 cursor-pointer"
            title="案例集设计规范与全流程指引"
            @click="showHelp = true"
          >
            <span class="text-sm font-bold">?</span>
          </button>

          <!-- 恢复顶部流程指引常驻按钮 -->
          <button
            v-if="!showExampleFlowGuide"
            type="button"
            class="inline-flex items-center gap-1 rounded-full border border-cyan-200 bg-cyan-50/80 px-2.5 py-1 text-xs font-medium text-cyan-700 shadow-2xs transition-colors hover:bg-cyan-100 cursor-pointer"
            title="重新展开案例集全流程指引"
            @click="restoreExampleFlowGuide"
          >
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span class="whitespace-nowrap">显示指引</span>
          </button>
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
        <p class="text-sm text-gray-500 mt-1">管理问答经验样本及 RAG 语义对齐案例</p>
      </div>

      <div class="flex w-full flex-col gap-2.5 sm:w-auto sm:flex-row sm:items-center sm:gap-2.5">
        <div class="flex w-full items-center gap-2 sm:w-auto">
          <!-- 统一搜索框（支持提问内容、ID、Agent、数据集、Trace） -->
          <div class="relative min-w-0 flex-1 sm:w-64 sm:flex-none lg:w-80">
            <span class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
              <MagnifyingGlassIcon class="h-4 w-4 text-gray-400" />
            </span>
            <input
              v-model="searchQuery"
              type="search"
              placeholder="搜索提问、ID、Agent、数据集..."
              class="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm shadow-sm outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
            />
          </div>

          <!-- 分类下拉过滤器 -->
          <select
            v-model="filterCategory"
            class="h-9 px-3 py-1.5 border border-gray-300 rounded-lg text-sm bg-white shadow-sm hover:bg-gray-50 text-gray-700 font-medium outline-none cursor-pointer transition-colors shrink-0"
          >
            <option value="">分类：全部</option>
            <option value="data_query">分类：数据查询</option>
            <option value="knowledge">分类：知识库</option>
            <option value="general">分类：通用问答</option>
          </select>

          <!-- 刷新按钮 -->
          <button
            type="button"
            class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-500 shadow-sm transition-colors hover:bg-gray-50 hover:text-blue-600"
            title="刷新列表"
            @click="fetchExamples"
          >
            <ArrowPathIcon class="h-4 w-4" :class="{ 'animate-spin': loading }" />
          </button>
        </div>

        <!-- 导出 Markdown 按钮（纯图标） -->
        <button
          type="button"
          class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-500 shadow-sm transition-colors hover:bg-gray-50 hover:text-blue-600"
          title="将当前案例列表格式化导出为 Markdown 文档"
          @click="exportToMarkdown"
        >
          <DocumentArrowDownIcon class="h-4 w-4" />
        </button>

        <!-- 案例集检索测试按钮（纯图标） -->
        <button
          type="button"
          class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-amber-200 bg-amber-50 text-amber-600 shadow-sm transition-colors hover:bg-amber-100"
          title="案例集检索测试：输入问题验证样例集命中情况，可临时调节 Top K / 阈值等参数"
          @click="searchTestModalRef?.open()"
        >
          <MagnifyingGlassIcon class="h-4 w-4" />
        </button>

        <!-- 一键同步按钮 -->
        <button
          v-if="hasPermission('element:chatbi_example:sync')"
          type="button"
          class="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3.5 py-2 text-sm font-medium text-indigo-700 shadow-sm transition-all hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          :disabled="loading || (!isLocalMode && !isEngineReady)"
          :title="isLocalMode ? '一键同步至本地向量索引' : (!isEngineReady ? 'RAGFlow 服务未就绪' : '一键同步至 RAGFlow')"
          @click="(isLocalMode || isEngineReady) && (showSyncAllConfirm = true)"
        >
          <CloudArrowUpIcon class="h-4 w-4 text-indigo-600" />
          <span class="hidden sm:inline">同步</span>
        </button>
      </div>
    </div>

    <!-- 案例集全生命周期指引横幅 -->
    <div v-if="showExampleFlowGuide" class="flex-shrink-0">
      <ExampleFlowGuideBanner
        @action="handleExampleBannerAction"
        @close="handleCloseExampleFlowGuide"
        @dismiss="handleDismissExampleFlowGuide"
      />
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

    <!-- Table Card -->
    <div class="bg-white shadow-sm rounded-lg border border-gray-200 overflow-hidden">
      <div class="overflow-x-auto">
        <table class="min-w-full divide-y divide-gray-200">
          <thead class="bg-gray-50">
            <tr>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">ID</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider min-w-[20rem] whitespace-nowrap">问答内容</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">分类</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">反馈</th>
              <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">状态</th>
              <template v-if="!isLocalMode">
                <th scope="col" class="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">RAG 同步</th>
              </template>
              <th scope="col" class="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">操作</th>
            </tr>
          </thead>
          <tbody class="bg-white divide-y divide-gray-200">
            <tr v-if="!loading && examples.length === 0">
              <td :colspan="isLocalMode ? 6 : 7" class="px-6 py-14 text-center">
                <p class="text-sm text-gray-500 font-medium">
                  <template v-if="hasActiveFilters">
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
                <div class="space-y-2 max-w-2xl">
                  <!-- 用户提问 (问) -->
                  <div class="flex items-start group">
                    <span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-blue-100 text-blue-700 mr-1.5 shrink-0 mt-0.5">问</span>
                    <div class="text-sm font-semibold text-gray-900 line-clamp-2 flex-1 leading-snug">{{ ex.user_query }}</div>
                    <button @click="copyToClipboard(ex.user_query)" class="ml-1.5 p-0.5 text-gray-400 hover:text-blue-600 opacity-0 group-hover:opacity-100 transition-all shrink-0" title="复制提问">
                      <DocumentDuplicateIcon class="w-3.5 h-3.5" />
                    </button>
                  </div>
                  
                  <!-- AI 回答 (答) -->
                  <div v-if="ex.ai_answer || ex.sql_text" class="flex items-start group">
                    <span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-700 mr-1.5 shrink-0 mt-0.5">答</span>
                    <div class="text-xs text-gray-600 line-clamp-2 flex-1 font-normal leading-relaxed">
                      {{ ex.ai_answer || ex.sql_text }}
                    </div>
                  </div>

                  <!-- 胶囊元信息栏 (智能体、时间、用户、Dataset、Trace) -->
                  <div class="flex flex-wrap items-center gap-1.5 pt-1 text-[11px]">
                    <!-- 智能体胶囊 -->
                    <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 font-medium border border-slate-200/80" :title="'Agent ID: ' + ex.agent_id">
                      🤖 {{ ex.agent_display_name || ex.agent_id }}
                    </span>

                    <!-- 用户胶囊 -->
                    <span v-if="ex.user_real_name || ex.user_account_name" class="inline-flex items-center px-2 py-0.5 rounded-full bg-purple-50 text-purple-600 border border-purple-100">
                      👤 {{ ex.user_real_name || ex.user_account_name }}
                    </span>

                    <!-- Dataset 胶囊 -->
                    <span v-if="ex.dataset_id" class="inline-flex items-center px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 border border-blue-100">
                      Dataset #{{ ex.dataset_id }}
                    </span>

                    <!-- 时间胶囊 -->
                    <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-gray-50 text-gray-400 font-mono border border-gray-100">
                      🕒 {{ new Date(ex.created_at).toLocaleString() }}
                    </span>

                    <!-- Trace ID 悬浮/交互 -->
                    <button
                      type="button"
                      class="text-[10px] text-gray-400 font-mono hover:text-blue-600 transition-colors ml-1"
                      :title="'点击复制完整 Trace: ' + ex.trace_id"
                      @click="copyToClipboard(ex.trace_id)"
                    >
                      Trace {{ ex.trace_id.substring(0, 8) }}…
                    </button>
                  </div>
                </div>
              </td>
              <td class="px-4 py-3.5 whitespace-nowrap">
                <span :class="['px-2.5 py-0.5 text-xs rounded-full font-medium border', getCategoryLabel(ex.category).color]">
                  {{ getCategoryLabel(ex.category).label }}
                </span>
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
              <template v-if="!isLocalMode">
                <td class="px-4 py-3.5 whitespace-nowrap">
                  <div class="flex flex-col">
                    <span :class="['text-xs font-semibold', getRagStatusDisplay(ex).color]">
                      {{ getRagStatusDisplay(ex).label }}
                    </span>
                    <span v-if="ex.rag_synced_at" class="text-[10px] text-gray-400 font-mono">{{ new Date(ex.rag_synced_at).toLocaleString() }}</span>
                  </div>
                </td>
              </template>
              <td class="px-4 py-3.5 whitespace-nowrap text-right text-sm font-medium">
                <div class="flex items-center justify-end gap-1">
                  <!-- 高频编辑/详情按钮 -->
                  <button
                    @click="viewDetail(ex)"
                    class="p-1.5 rounded-lg text-blue-600 hover:bg-blue-50 transition-colors"
                    title="编辑/查看详情"
                  >
                    <PencilSquareIcon class="w-4 h-4" />
                  </button>

                  <template v-if="actionLoading[ex.id]">
                    <div class="p-1.5">
                      <ArrowPathIcon class="w-4 h-4 text-gray-400 animate-spin" />
                    </div>
                  </template>

                  <!-- 更多操作下拉触发表 -->
                  <template v-else>
                    <button
                      type="button"
                      @click.stop="toggleRowMenu(ex, $event)"
                      class="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
                      title="更多操作"
                    >
                      <EllipsisHorizontalIcon class="w-4 h-4" />
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
        <div :class="currentExample.category === 'data_query' ? 'grid grid-cols-1 md:grid-cols-2 gap-4' : 'space-y-4'">
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
            </div>
            <div>
              <div class="flex items-center justify-between mb-1">
                <label class="block text-xs font-semibold text-blue-600 uppercase tracking-wider">🎯 核心意图 (自动还原/可手动微调)</label>
                <div v-if="currentExample.category === 'data_query'" class="flex items-center space-x-2">
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
          
          <div v-if="currentExample.category === 'data_query'" class="space-y-4">
            <div>
              <label class="block text-xs font-semibold text-gray-500 mb-1 uppercase tracking-wider">🛠 提取的 SQL (可微调优化/无 SQL 可留空)</label>
              <textarea 
                v-model="currentExample.sql_text" 
                :disabled="currentExample.status === 'rejected' || currentExample.status === 'deprecated'"
                rows="8"
                placeholder="请输入有效 SQL 语句"
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
              <CheckCircleIcon class="w-3 h-3 mr-1" /> {{ isLocalMode ? '已同步到本地向量索引' : '已同步到 RAGFlow' }}
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
      :message="isLocalMode
        ? '确定要将所有状态为\'已通过\'的案例重新同步到本地向量索引 (Redis) 吗？'
        : '确定要将所有状态为\'已通过\'的案例重新同步到 RAGFlow 吗？这可能会覆盖 RAGFlow 中已有的对应记录。'"
      confirmText="开始同步"
      type="primary"
      @confirm="syncAllToRag"
      @cancel="showSyncAllConfirm = false"
    />

    <!-- 行内「更多」操作浮动下拉菜单：Teleport 到 body，防止被表格裁切 -->
    <Teleport to="body">
      <div
        v-if="openRowMenuExample"
        class="fixed w-44 bg-white border border-gray-200/90 rounded-xl shadow-xl py-1.5 z-[200] text-left transition-all"
        :style="{ top: `${rowMenuPos.top}px`, left: `${rowMenuPos.left}px` }"
        @click.stop
      >
        <button
          v-if="openRowMenuExample.status !== 'approved'"
          type="button"
          class="w-full text-left px-3.5 py-2 text-xs font-medium text-emerald-700 hover:bg-emerald-50/70 flex items-center gap-2 transition-colors"
          @click="auditExample(openRowMenuExample.id, 'approved'); closeMenus()"
        >
          <CheckCircleIcon class="w-4 h-4 text-emerald-600 shrink-0" />
          通过案例
        </button>

        <button
          v-if="openRowMenuExample.status !== 'rejected'"
          type="button"
          class="w-full text-left px-3.5 py-2 text-xs font-medium text-rose-600 hover:bg-rose-50/70 flex items-center gap-2 transition-colors"
          @click="auditExample(openRowMenuExample.id, 'rejected'); closeMenus()"
        >
          <XCircleIcon class="w-4 h-4 text-rose-500 shrink-0" />
          驳回案例
        </button>

        <button
          v-if="openRowMenuExample.status === 'approved'"
          type="button"
          class="w-full text-left px-3.5 py-2 text-xs font-medium text-blue-600 hover:bg-blue-50/70 flex items-center gap-2 transition-colors disabled:opacity-50"
          :disabled="!isLocalMode && !isEngineReady"
          @click="syncToRag(openRowMenuExample.id); closeMenus()"
        >
          <CloudArrowUpIcon class="w-4 h-4 text-blue-500 shrink-0" />
          {{ isLocalMode ? '同步到本地向量索引' : '同步到 RAGFlow' }}
        </button>

        <div class="my-1 border-t border-gray-100"></div>

        <button
          v-if="openRowMenuExample.status !== 'deprecated'"
          type="button"
          class="w-full text-left px-3.5 py-2 text-xs font-medium text-gray-600 hover:bg-gray-100 flex items-center gap-2 transition-colors"
          @click="auditExample(openRowMenuExample.id, 'deprecated'); closeMenus()"
        >
          <TrashIcon class="w-4 h-4 text-gray-400 shrink-0" />
          废弃案例
        </button>
      </div>
    </Teleport>
  </div>
    <!-- 案例集设计规范与全流程指引 Modal -->
    <div v-if="showHelp" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showHelp = false">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-cyan-50/30">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-xl bg-cyan-600 text-white flex items-center justify-center shadow-md shadow-cyan-500/20" style="background-color: #0891b2; color: #ffffff;">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">案例集设计规范与全流程指引</h2>
               <p class="text-xs text-gray-500 font-medium mt-0.5">从会话点赞沉淀、专家审核向量化，到大模型动态 Few-Shot 召回注入与持续进化。</p>
             </div>
          </div>
          <div class="flex items-center gap-3">
            <button
              v-if="!showExampleFlowGuide"
              type="button"
              @click="restoreExampleFlowGuide"
              class="inline-flex items-center gap-1 text-xs font-medium text-cyan-700 bg-cyan-100/70 hover:bg-cyan-200 px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
              <span>恢复顶部流程提示</span>
            </button>
            <button @click="showHelp = false" class="text-gray-400 hover:text-gray-600 transition-colors cursor-pointer">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
        </div>

        <!-- Tabs -->
        <div class="flex border-b border-gray-200 bg-white px-6">
           <button 
             v-for="tab in ['flow', 'dataflow', 'practice']" 
             :key="tab"
             @click="activeHelpTab = tab as any"
             class="px-4 py-3 text-sm font-medium border-b-2 transition-colors cursor-pointer"
             :class="activeHelpTab === tab ? 'border-cyan-600 text-cyan-700' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
           >
             {{ tab === 'flow' ? '全流程指引 (Workflow)' :
                tab === 'dataflow' ? '数据来源与消费机制 (Data Flow)' : '审核规范与最佳实践 (Best Practice)' }}
           </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-6 sm:p-8 bg-gray-50/50">
           <!-- Tab 1: Workflow Flow -->
           <div v-if="activeHelpTab === 'flow'" class="space-y-6 max-w-4xl mx-auto">
              <div class="bg-gradient-to-r from-cyan-50 to-blue-50 border-l-4 border-cyan-600 p-4 rounded-r-xl shadow-2xs">
                 <h3 class="font-bold text-cyan-900 mb-1">案例集 5 步全生命周期标准体系</h3>
                 <p class="text-xs text-cyan-700 leading-relaxed">
                    案例集是 Text-to-SQL 与 ChatBI 的核心范例库。真实会话点赞或专家录入 ➔ 审核清洗 ➔ 向量索引同步 ➔ 智能体提问时动态 Top-K 检索注入，全面提升准确率。
                 </p>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                 <!-- Step 1 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-cyan-600 text-white flex items-center justify-center text-xs font-bold">1</span>
                          <h4 class="font-bold text-gray-900 text-sm">样本沉淀与录入</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          用户在智能对话中对满意 SQL 点击 👍 采纳点赞，系统自动沉淀样本；业务专家亦可在后台直接录入高频复杂业务提问。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex items-center justify-end gap-2">
                       <button
                          type="button"
                          @click="showHelp = false; router.push('/dashboard/chat')"
                          class="text-xs text-cyan-600 hover:text-cyan-800 font-bold cursor-pointer"
                       >
                          前往智能对话 &rarr;
                       </button>
                    </div>
                 </div>

                 <!-- Step 2 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold">2</span>
                          <h4 class="font-bold text-gray-900 text-sm">专家审核与打标</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          在「待审核」Tab 下审查提问语义与 SQL 准确性，精炼意图（Refined Query）；通过审批后正式纳入经验案例库。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end text-xs text-gray-400">
                       在「待审核」Tab 下逐条审批
                    </div>
                 </div>

                 <!-- Step 3 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-teal-600 text-white flex items-center justify-center text-xs font-bold">3</span>
                          <h4 class="font-bold text-gray-900 text-sm">向量同步与索引</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          审核通过的案例需同步至向量索引库；支持单个案例同步或顶部一键全量同步，构建语义密集检索向量。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex items-center justify-end gap-2">
                       <button
                          type="button"
                          @click="showHelp = false; showSyncAllConfirm = true"
                          class="text-xs text-teal-600 hover:text-teal-800 font-bold cursor-pointer"
                       >
                          一键全量同步 &rarr;
                       </button>
                    </div>
                 </div>

                 <!-- Step 4 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs font-bold">4</span>
                          <h4 class="font-bold text-gray-900 text-sm">动态 Few-Shot 召回</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          智能体接收到新提问时，后台通过向量检索秒级召回 Top-1~3 最相似案例注入 Prompt，辅助大模型生成高精度 SQL。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                       <button
                          type="button"
                          @click="showHelp = false; router.push('/dashboard/agent-management')"
                          class="text-xs text-indigo-600 hover:text-indigo-800 font-medium cursor-pointer"
                       >
                          前往智能体中心 &rarr;
                       </button>
                    </div>
                 </div>

                 <!-- Step 5 -->
                 <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between md:col-span-2">
                    <div>
                       <div class="flex items-center gap-2 mb-2">
                          <span class="w-6 h-6 rounded-full bg-amber-600 text-white flex items-center justify-center text-xs font-bold">5</span>
                          <h4 class="font-bold text-gray-900 text-sm">引用统计与持续迭代</h4>
                       </div>
                       <p class="text-xs text-gray-500 leading-relaxed">
                          系统自动记录案例的真实引用频次（use_count），对过时或低质案例标记「废弃」，持续维持高水准业务经验库。
                       </p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end text-xs text-gray-400">
                       卡片中查看「引用次数」与「点赞类型」
                    </div>
                 </div>
              </div>
           </div>

           <!-- Tab 2: Data Flow -->
           <div v-else-if="activeHelpTab === 'dataflow'" class="space-y-4 max-w-4xl mx-auto">
              <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-650 leading-relaxed">
                 <h4 class="font-bold text-gray-900 text-base">数据从哪里来？在哪里被大模型消费？</h4>
                 <div class="space-y-3">
                    <div class="p-4 bg-cyan-50/60 rounded-xl border border-cyan-100 space-y-1 text-xs">
                       <span class="font-bold text-cyan-900 text-sm">📥 数据来源（Where it comes from）</span>
                       <ul class="list-disc list-inside text-gray-600 mt-1 space-y-1 leading-relaxed">
                          <li><strong>真实会话采纳点赞</strong>：用户在对话中对生成的 SQL 或回答点击 👍，系统自动捕获提问、SQL 与上下文沉淀为样本；</li>
                          <li><strong>业务专家手工录入</strong>：数据分析师在后台录入高频复杂报表查询、特殊业务口径的标准 SQL；</li>
                          <li><strong>历史评测集回流</strong>：将回归评测集中的金标准用例导入案例库。</li>
                       </ul>
                    </div>
                    <div class="p-4 bg-indigo-50/60 rounded-xl border border-indigo-100 space-y-1 text-xs">
                       <span class="font-bold text-indigo-900 text-sm">🚀 生产消费（Where and How it is used）</span>
                       <ul class="list-disc list-inside text-gray-600 mt-1 space-y-1 leading-relaxed">
                          <li><strong>向量动态匹配</strong>：用户提出新问题时，系统进行向量相似度检索，毫秒级召回 Top-1~3 相似案例；</li>
                          <li><strong>Few-Shot Prompt 注入</strong>：系统将召回的黄金 SQL 案例组装进大模型 System Prompt；</li>
                          <li><strong>精度飞跃提升</strong>：大模型通过参考范例中的字段名、关联条件与聚合口径，生成准确率提升至 95%+，极大消除幻觉。</li>
                       </ul>
                    </div>
                 </div>
              </div>
           </div>

           <!-- Tab 3: Best Practice -->
           <div v-else-if="activeHelpTab === 'practice'" class="space-y-4 max-w-4xl mx-auto">
              <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-650 leading-relaxed">
                 <h4 class="font-bold text-gray-900 text-base">高质量案例审核与维护最佳实践</h4>
                 <div class="space-y-3 text-xs">
                    <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-150 space-y-1">
                       <span class="font-bold text-gray-900">1. 精炼意图（Refined Query）</span>
                       <p class="text-gray-600 leading-relaxed">将口语化、含代词的多轮提问（如“那上个月的呢？”）精炼为自包含的明确语义（如“查询2026年7月份各部门总能耗”）。</p>
                    </div>
                    <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-150 space-y-1">
                       <span class="font-bold text-gray-900">2. 规范 SQL 编写</span>
                       <p class="text-gray-600 leading-relaxed">保证 SQL 格式工整、表名加别名、时间字段过滤明确，避免使用 <code>SELECT *</code>。</p>
                    </div>
                    <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-150 space-y-1">
                       <span class="font-bold text-gray-900">3. 及时全量同步</span>
                       <p class="text-gray-600 leading-relaxed">批量审核通过后，务必点击右上角「一键全量同步」，确保向量索引最新生效。</p>
                    </div>
                 </div>
              </div>
           </div>
        </div>
      </div>
    </div>

    <!-- 案例集检索测试弹窗 -->
    <ExampleSearchTestModal ref="searchTestModalRef" />
</template>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
