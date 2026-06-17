<script setup lang="ts">
import { ref, onMounted, computed, watch } from "vue";
import axios from "../utils/axios";
import { agentApi } from "../api/agent";
import { modelApi, type AIModel } from "../api/model";
import {
  DocumentTextIcon,
  BeakerIcon,
  ChevronRightIcon,
  CloudArrowUpIcon,
  PlayIcon,
  ArrowPathIcon,
  CheckBadgeIcon,
  ClipboardDocumentIcon,
  EyeIcon,
  PencilSquareIcon,
  SparklesIcon,
  ArrowUturnLeftIcon,
  ChevronDownIcon,
  MagnifyingGlassIcon,
  CpuChipIcon,
  CalculatorIcon,
  QueueListIcon,
  RocketLaunchIcon,
} from "@heroicons/vue/24/outline";
import { renderMarkdown } from "../utils/markdown";
import * as Diff from "diff";
import ConfirmModal from "../components/ConfirmModal.vue";

interface PromptVersionSummary {
  version_number: number;
  status: string;
  comment: string;
  updated_at: string;
}

interface PromptMetadata {
  id: string;
  name: string;
  display_name?: string;
  source: "system_config" | "agent";
  category: string;
  description: string;
  versions: PromptVersionSummary[];
  created_by?: string;
  is_system?: boolean;
}

interface PromptDetail {
  id: string;
  source: string;
  content: string;
  version_number?: number;
  variables: string[];
}

const prompts = ref<PromptMetadata[]>([]);
const selectedPrompt = ref<PromptMetadata | null>(null);
const currentDetail = ref<PromptDetail | null>(null);
const originalContent = ref<string | undefined>(undefined);
const loading = ref(false);
const loadingDetail = ref(false);
const initialLoading = ref(true);
const testing = ref(false);
const saving = ref(false);
const userInfo = ref<any>({});
const initError = ref<string | null>(null);

const testVariables = ref<Record<string, string>>({});
const testResult = ref<{
  raw_output: string;
  interpolated_prompt: string;
  latency_ms: number;
} | null>(null);
const streamedOutput = ref(""); // For typewriter effect
const selectedVersion = ref<number | null>(null);
const versionNote = ref("");
const viewMode = ref<"edit" | "preview">("edit");
const historyList = ref<any[]>([]);
const loadingHistory = ref(false);
const showHistory = ref(false);
const showOptimizeConfirm = ref(false);
const showPublishConfirm = ref(false); // New confirm state
const showOptimizeModal = ref(false);
const optimizing = ref(false);
const optimizeSuggestions = ref<any[]>([]);
const activeOptimizeTab = ref(0);
const models = ref<AIModel[]>([]);
const selectedModel = ref<string>("");
const showSnippets = ref(false);
const showSidebar = ref(true); // Sidebar toggle state
const activeCategoryTab = ref<'ALL' | 'System' | 'Agent'>('ALL'); // Category filter
const isFullscreen = ref(false); // Fullscreen mode

const snippets = [
  {
    label: "思维链 (Chain of Thought)",
    content: "Let's think step by step:\\n1. ",
  },
  {
    label: "角色设定 (Roleplay)",
    content: "You are an expert in [FIELD]. Your goal is to...",
  },
  {
    label: "JSON 输出约束",
    content:
      'Please output the result in JSON format:\\n{\\n  "key": "value"\\n}',
  },
  {
    label: "少样本 (Few-Shot)",
    content:
      "Example 1:\\nInput: ...\\nOutput: ...\\n\\nExample 2:\\nInput: ...\\nOutput: ...\\n",
  },
];

// 添加缺失的响应式变量
const searchQuery = ref("");
const collapsedGroups = ref<Record<string, boolean>>({});
const hoveredPromptId = ref<string | null>(null);

const canEdit = computed(() => {
  return userInfo.value?.role === "admin";
});

const toast = ref({ show: false, message: "", type: "success" });
const showPlayground = ref(false);
const userInput = ref("");

const showToast = (
  message: string,
  type: "success" | "error" | "warning" = "success"
) => {
  toast.value = { show: true, message, type };
  setTimeout(() => (toast.value.show = false), 3000);
};

const reloadPage = () => {
  window.location.reload();
};

const toggleFullscreen = () => {
  isFullscreen.value = !isFullscreen.value;
  if (isFullscreen.value) {
    document.body.style.overflow = 'hidden';
  } else {
    document.body.style.overflow = '';
  }
};

const copyToClipboard = async (text: string) => {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    showToast("已复制到剪贴板");
  } catch (err) {
    console.error("Failed to copy text: ", err);
    showToast("复制失败", "error");
  }
};

const restoreContent = (content: string) => {
  if (!currentDetail.value) return;
  currentDetail.value.content = content;
  viewMode.value = "edit";
  showHistory.value = false;
  showToast("内容已恢复到编辑器");
};

// AI Optimization Logic
const runOptimize = async () => {
  if (!currentDetail.value?.content) return;
  showOptimizeConfirm.value = false;
  optimizing.value = true;

  try {
    const res = await axios.post("/api/portal/prompts/optimize", {
      content: currentDetail.value.content,
    });
    optimizeSuggestions.value = res.data.suggestions || [];
    activeOptimizeTab.value = 0;
    showOptimizeModal.value = true;
  } catch (err: any) {
    showToast(err.response?.data?.detail || "优化失败", "error");
  } finally {
    optimizing.value = false;
  }
};

const applySuggestion = (content: string) => {
  if (!currentDetail.value) return;
  currentDetail.value.content = content;
  showOptimizeModal.value = false;
  viewMode.value = "edit";
  showToast("已应用优化建议");
};

const insertSnippet = (content: string) => {
  if (!currentDetail.value) return;
  currentDetail.value.content +=
    (currentDetail.value.content ? "\\n\\n" : "") + content;
  showSnippets.value = false;
  showToast("已插入模版");
};

const tokenCount = computed(() => {
  if (!currentDetail.value?.content) return 0;
  const text = currentDetail.value.content;
  const cnChars = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
  const enChars = text.length - cnChars;
  return Math.ceil(cnChars * 0.8 + enChars * 0.25);
});

const fetchModels = async () => {
  try {
    const res = await modelApi.list();
    models.value = res.data.filter((m) => m.type === "llm" && m.is_active);
    if (models.value.length > 0) {
      selectedModel.value = models.value[0]?.model_id || "";
    }
  } catch (e) {
    console.error("Failed to fetch models", e);
  }
};

const requestPublish = () => {
  if (
    !currentDetail.value ||
    !selectedPrompt.value ||
    selectedPrompt.value.source !== "agent"
  )
    return;
  showPublishConfirm.value = true;
};

const performPublish = async () => {
  showPublishConfirm.value = false;
  if (
    !currentDetail.value ||
    !selectedPrompt.value ||
    selectedPrompt.value.source !== "agent"
  )
    return;

  // Save first
  await performSave();

  try {
    const agentId = selectedPrompt.value.id.replace("agent_", "");
    await loadPromptDetail(selectedPrompt.value);

    const vRes = await agentApi.listVersions(agentId);
    const draft = vRes.data.find((v: any) => v.status === "DRAFT");

    if (draft) {
      await agentApi.publishVersion(agentId, draft.id);
      showToast("已发布并上线！", "success");
      
      // Refresh prompts list to get updated versions
      await fetchPrompts();

      // Update selectedPrompt reference to get new versions list
      const updatedPrompt = prompts.value.find(
        (p) => p.id === selectedPrompt.value?.id
      );
      if (updatedPrompt) {
        selectedPrompt.value = updatedPrompt;
        
        // Reload detail with the latest (newly published) version
        // The latest version should be the highest version number
        const latestVersion = Math.max(...updatedPrompt.versions.map(v => v.version_number));
        await loadPromptDetail(updatedPrompt, latestVersion);
      }
    } else {
      showToast("未找到草稿版本，无法发布", "warning");
    }
  } catch (e: any) {
    showToast("发布失败: " + (e.response?.data?.detail || e.message), "error");
  }
};

const getPromptColorTheme = (p: PromptMetadata) => {
  const themes = [
    {
      bg: "bg-blue-50",
      border: "border-blue-100",
      text: "text-blue-600",
      accent: "bg-blue-600",
      light: "bg-blue-50/50",
      ring: "ring-blue-500",
    },
    {
      bg: "bg-indigo-50",
      border: "border-indigo-100",
      text: "text-indigo-600",
      accent: "bg-indigo-600",
      light: "bg-indigo-50/50",
      ring: "ring-indigo-500",
    },
    {
      bg: "bg-purple-50",
      border: "border-purple-100",
      text: "text-purple-600",
      accent: "bg-purple-600",
      light: "bg-purple-50/50",
      ring: "ring-purple-500",
    },
    {
      bg: "bg-pink-50",
      border: "border-pink-100",
      text: "text-pink-600",
      accent: "bg-pink-600",
      light: "bg-pink-50/50",
      ring: "ring-pink-500",
    },
    {
      bg: "bg-rose-50",
      border: "border-rose-100",
      text: "text-rose-600",
      accent: "bg-rose-600",
      light: "bg-rose-50/50",
      ring: "ring-rose-500",
    },
    {
      bg: "bg-orange-50",
      border: "border-orange-100",
      text: "text-orange-600",
      accent: "bg-orange-600",
      light: "bg-orange-50/50",
      ring: "ring-orange-500",
    },
    {
      bg: "bg-amber-50",
      border: "border-amber-100",
      text: "text-amber-600",
      accent: "bg-amber-600",
      light: "bg-amber-50/50",
      ring: "ring-amber-500",
    },
    {
      bg: "bg-emerald-50",
      border: "border-emerald-100",
      text: "text-emerald-600",
      accent: "bg-emerald-600",
      light: "bg-emerald-50/50",
      ring: "ring-emerald-500",
    },
    {
      bg: "bg-teal-50",
      border: "border-teal-100",
      text: "text-teal-600",
      accent: "bg-teal-600",
      light: "bg-teal-50/50",
      ring: "ring-teal-500",
    },
    {
      bg: "bg-cyan-50",
      border: "border-cyan-100",
      text: "text-cyan-600",
      accent: "bg-cyan-600",
      light: "bg-cyan-50/50",
      ring: "ring-cyan-500",
    },
  ];
  let hash = 0;
  const str = p.id || p.name || "default";
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  const index = Math.abs(hash) % themes.length;
  return themes[index] || themes[0]!;
};

const fetchUserInfo = () => {
  const cached = localStorage.getItem("user_info");
  if (cached) {
    try {
      userInfo.value = JSON.parse(cached);
    } catch (e) {
      console.error("Failed to parse cached user info:", e);
      localStorage.removeItem("user_info");
    }
  }
};

const fetchPrompts = async () => {
  loading.value = true;
  console.log("[PromptStudio] Fetching prompts...");
  try {
    const res = await axios.get("/api/portal/prompts/");
    console.log("[PromptStudio] Prompts response:", res.data);
    prompts.value = Array.isArray(res.data) ? res.data : [];
  } catch (e: any) {
    console.error("[PromptStudio] Failed to fetch prompts:", e);
    const errorMessage = e.response?.data?.detail || "获取列表失败";
    showToast(errorMessage, "error");
    throw e; // Re-throw to let onMounted handle it
  } finally {
    loading.value = false;
  }
};

// Test Case Management
const saveTestCase = () => {
  if (!selectedPrompt.value) return;
  const key = `test_case_${selectedPrompt.value.id}`;
  const data = {
    userInput: userInput.value,
    variables: testVariables.value,
    model: selectedModel.value
  };
  localStorage.setItem(key, JSON.stringify(data));
  showToast("测试用例已保存 (Local)");
};

const loadTestCase = () => {
  if (!selectedPrompt.value) return;
  const key = `test_case_${selectedPrompt.value.id}`;
  const cached = localStorage.getItem(key);
  if (cached) {
    try {
      const data = JSON.parse(cached);
      if (data.userInput) userInput.value = data.userInput;
      if (data.variables) {
        // Merge with current vars to ensure we don't lose new keys
        testVariables.value = { ...testVariables.value, ...data.variables };
      }
      if (data.model && models.value.find(m => m.model_id === data.model)) {
        selectedModel.value = data.model;
      }
    } catch (e) {
      console.error("Failed to load test case", e);
    }
  }
};

const loadPromptDetail = async (prompt: PromptMetadata, version?: number) => {
  selectedPrompt.value = prompt;
  loadingDetail.value = true;
  testResult.value = null;
  versionNote.value = "";
  viewMode.value = "edit";
  showHistory.value = false;

  try {
    const res = await axios.get("/api/portal/prompts/detail", {
      params: {
        source: prompt.source,
        target_id: prompt.id,
        version: version || undefined,
      },
    });
    currentDetail.value = res.data;
    versionNote.value = res.data.version_note || "";
    originalContent.value = res.data.content;
    selectedVersion.value = res.data.version_number;

    // Init test variables
    const newVars: Record<string, string> = {};
    res.data.variables.forEach((v: string) => {
      newVars[v] = testVariables.value[v] || "";
    });
    testVariables.value = newVars;
    
    // Load saved test case
    loadTestCase();
  } catch (e: any) {
    console.error("Failed to load prompt detail:", e);
    const errorMessage = e.response?.data?.detail || "获取详情失败";
    showToast(errorMessage, "error");
  } finally {
    loadingDetail.value = false;
  }
};

const fetchHistory = async () => {
  if (!selectedPrompt.value) return;
  loadingHistory.value = true;
  showHistory.value = true;
  try {
    const res = await axios.get("/api/portal/prompts/history", {
      params: {
        source: selectedPrompt.value.source,
        target_id: selectedPrompt.value.id,
      },
    });
    historyList.value = res.data;
  } catch (e: any) {
    console.error("Failed to fetch history:", e);
    const errorMessage = e.response?.data?.detail || "获取历史失败";
    showToast(errorMessage, "error");
  } finally {
    loadingHistory.value = false;
  }
};

const runTest = async () => {
  if (!currentDetail.value) return;
  testing.value = true;
  testResult.value = null;
  streamedOutput.value = "";
  
  try {
    const res = await axios.post("/api/portal/prompts/test", {
      content: currentDetail.value.content,
      variables: testVariables.value,
      user_input: userInput.value || null,
      model: selectedModel.value || null,
    });
    
    // Start Typewriter effect
    const fullText = res.data.raw_output || "";
    testResult.value = res.data;
    testResult.value!.raw_output = ""; // Clear initially
    
    let i = 0;
    const speed = 15; // ms per char
    const typeWriter = () => {
      if (i < fullText.length) {
        streamedOutput.value += fullText.charAt(i);
        i++;
        setTimeout(typeWriter, speed);
      } else {
        // Restore full object when done
        if(testResult.value) testResult.value.raw_output = fullText;
      }
    };
    typeWriter();

  } catch (e: any) {
    console.error("Failed to run test:", e);
    const errorMessage = e.response?.data?.detail || "测试失败";
    showToast(errorMessage, "error");
  } finally {
    testing.value = false;
  }
};

const showConfirm = ref(false);

const requestSave = () => {
  if (!currentDetail.value || !selectedPrompt.value) return;
  showConfirm.value = true;
};

const performSave = async () => {
  showConfirm.value = false;
  if (!currentDetail.value || !selectedPrompt.value) return;
  saving.value = true;
  try {
    const res = await axios.post("/api/portal/prompts/save", {
      source: selectedPrompt.value.source,
      target_id: selectedPrompt.value.id,
      content: currentDetail.value.content,
      version_note: versionNote.value,
    });

    if (res.data.status === "unchanged") {
      showToast("未检测到内容变更", "success");
    } else {
      showToast("保存成功");
      if (currentDetail.value) {
        originalContent.value = currentDetail.value.content;
      }
    }

    fetchPrompts(); // Refresh version list
  } catch (e: any) {
    console.error("Failed to save:", e);
    const errorMessage = e.response?.data?.detail || "保存失败";
    showToast(errorMessage, "error");
  } finally {
    saving.value = false;
  }
};

const computeDiffHtml = (
  oldText: string,
  newText: string,
  type: "old" | "new"
) => {
  if (!oldText) oldText = "";
  if (!newText) newText = "";

  const diff = Diff.diffChars(oldText, newText);
  let html = "";

  diff.forEach((part) => {
    // Escape HTML to prevent injection
    const escaped = part.value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");

    if (type === "old") {
      if (part.removed) {
        html += `<span class="bg-red-100 text-red-800 line-through decoration-red-500">${escaped}</span>`;
      } else if (!part.added) {
        html += escaped;
      }
    } else {
      if (part.added) {
        html += `<span class="bg-green-100 text-green-800 font-bold">${escaped}</span>`;
      } else if (!part.removed) {
        html += escaped;
      }
    }
  });

  return html;
};

const groupedPrompts = computed(() => {
  const groups: Record<string, PromptMetadata[]> = {};
  const query = searchQuery.value.toLowerCase().trim();
  const tab = activeCategoryTab.value;

  prompts.value.forEach((p) => {
    // Filter by search query
    if (
      query &&
      !p.name.toLowerCase().includes(query) &&
      !p.display_name?.toLowerCase()?.includes(query)
    ) {
      return;
    }

    // Filter by Category Tab
    if (tab !== 'ALL') {
      if (tab === 'System' && p.category !== 'System' && !p.is_system) return;
      if (tab === 'Agent' && p.category !== 'Agent') return;
    }

    // Use p.category or fallback
    const cat = p.category || 'Other';
    if (!groups[cat]) groups[cat] = [];
    groups[cat]!.push(p);
  });
  return groups;
});

const isDirty = computed(() => {
  if (!currentDetail.value || originalContent.value === undefined) return false;
  return currentDetail.value.content !== originalContent.value;
});

const toggleGroup = (category: string) => {
  collapsedGroups.value[category] = !collapsedGroups.value[category];
};

const formatRelativeDate = (dateStr: string) => {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - date.getTime();

  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (days > 7) return date.toLocaleDateString();
  if (days > 0) return `${days}天前`;
  if (hours > 0) return `${hours}小时前`;
  if (minutes > 0) return `${minutes}分钟前`;
  return "刚刚";
};

onMounted(async () => {
  try {
    fetchUserInfo();
    fetchModels();
    await fetchPrompts();
  } catch (error) {
    console.error("Failed to initialize PromptStudio:", error);
    initError.value = "页面初始化失败，请刷新重试";
  } finally {
    initialLoading.value = false;
  }
});

// Regex to find variables in text
watch(
  () => currentDetail.value?.content,
  (newContent) => {
    if (!newContent) return;
    const matches = newContent.match(/\{([^{}]+)\}/g);
    if (matches) {
      const vars = matches.map((m) => m.slice(1, -1));
      vars.forEach((v) => {
        if (testVariables.value[v] === undefined) {
          testVariables.value[v] = "";
        }
      });
    }
  }
);
</script>

<template>
  <div class="h-full flex flex-col">
    <!-- Header -->
    <div class="mb-6 flex justify-between items-center">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">
          提示词工坊 (Prompt Studio)
        </h1>
        <p class="text-gray-500 text-sm mt-1">
          统一管理、编辑和测试系统及智能体的提示词。
        </p>
      </div>
      <button
        @click="fetchPrompts"
        class="p-2 text-gray-500 hover:text-primary rounded-full hover:bg-white transition-all"
      >
        <ArrowPathIcon class="w-5 h-5" :class="{ 'animate-spin': loading }" />
      </button>
    </div>

    <!-- Initial Loading State -->
    <div
      v-if="initialLoading"
      class="flex-1 flex flex-col items-center justify-center bg-white rounded-xl shadow-sm border border-gray-200"
    >
      <ArrowPathIcon class="w-12 h-12 text-primary animate-spin mb-4" />
      <h2 class="text-xl font-medium text-gray-700">加载中...</h2>
      <p class="mt-2 text-sm text-gray-500">正在初始化提示词工坊</p>
    </div>

    <!-- Initial Error State -->
    <div
      v-else-if="initError"
      class="flex-1 flex flex-col items-center justify-center bg-white rounded-xl shadow-sm border border-gray-200"
    >
      <div class="text-red-500 mb-4">
        <svg
          class="w-12 h-12"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
          />
        </svg>
      </div>
      <h2 class="text-xl font-medium text-gray-700">加载失败</h2>
      <p class="mt-2 text-sm text-gray-500">{{ initError }}</p>
      <button
        @click="reloadPage"
        class="mt-4 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-dark transition-all"
      >
        刷新页面
      </button>
    </div>

    <!-- Main Content -->
    <div v-else class="flex-1 flex gap-6 min-h-0 overflow-hidden relative">
      <!-- Sidebar Toggle Button (Absolute) -->
      <button 
        @click="showSidebar = !showSidebar"
        class="absolute left-0 top-1/2 -translate-y-1/2 z-20 bg-white border border-gray-200 shadow-md p-1 rounded-r-md hover:bg-gray-50 text-gray-400 hover:text-primary transition-all duration-300"
        :class="{ 'left-80': showSidebar }"
        style="transition: left 0.3s ease;"
        title="Toggle Sidebar"
      >
        <ChevronRightIcon class="w-3 h-3 transition-transform" :class="{ 'rotate-180': showSidebar }" />
      </button>

      <!-- Sidebar List -->
      <div
        class="flex flex-col bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden transition-all duration-300"
        :class="showSidebar ? 'w-80 opacity-100' : 'w-0 opacity-0 border-none'"
      >
        <div
          class="px-4 py-3 border-b border-gray-100 bg-gray-50 flex flex-col space-y-3"
        >
          <!-- Category Tabs -->
          <div class="flex p-1 bg-gray-200/60 rounded-lg">
             <button 
               v-for="tab in ['ALL', 'System', 'Agent']" 
               :key="tab"
               @click="activeCategoryTab = tab as any"
               class="flex-1 py-1 text-[10px] font-bold rounded-md transition-all text-center"
               :class="activeCategoryTab === tab ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
             >
               {{ tab === 'ALL' ? '全部' : (tab === 'System' ? '系统 (Sys)' : '智能体 (Bot)') }}
             </button>
          </div>

          <!-- Search Bar -->
          <div class="relative group">
            <MagnifyingGlassIcon
              class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 group-focus-within:text-primary transition-colors"
            />
            <input
              v-model="searchQuery"
              type="text"
              placeholder="搜索..."
              class="w-full pl-9 pr-4 py-2 bg-white border-gray-200 rounded-lg text-xs focus:ring-1 focus:ring-primary focus:border-primary transition-all placeholder:text-gray-300"
            />
          </div>
        </div>
        <div class="flex-1 overflow-y-auto p-2 space-y-4 custom-scrollbar">
          <div v-for="(items, category) in groupedPrompts" :key="category">
            <h3
              @click="toggleGroup(category)"
              class="px-3 py-1.5 mb-2 text-xs font-bold uppercase tracking-wider rounded-md flex items-center justify-between border border-transparent cursor-pointer group hover:bg-white hover:shadow-sm transition-all"
              :class="{
                'bg-blue-50 text-blue-700 border-blue-100':
                  category === 'System',
                'bg-emerald-50 text-emerald-700 border-emerald-100':
                  category === 'Agent',
                'bg-gray-50 text-gray-500':
                  category !== 'System' && category !== 'Agent',
              }"
            >
              <div class="flex items-center">
                <span class="mr-1.5 opacity-70">
                  {{
                    category === "System"
                      ? "🔧"
                      : category === "Agent"
                      ? "🤖"
                      : "#"
                  }}
                </span>
                {{ category }}
                <span
                  class="ml-2 px-1.5 py-0.5 rounded-full bg-white/50 text-[9px] font-mono"
                  >{{ items.length }}</span
                >
              </div>
              <ChevronDownIcon
                class="w-3 h-3 transition-transform duration-300"
                :class="{ 'rotate-[-90deg]': collapsedGroups[category] }"
              />
            </h3>
            <div v-show="!collapsedGroups[category]" class="space-y-1">
              <button
                v-for="p in items"
                :key="p.id"
                @click="loadPromptDetail(p)"
                @mouseenter="hoveredPromptId = p.id"
                @mouseleave="hoveredPromptId = null"
                class="w-full text-left px-3 py-2.5 rounded-xl text-sm transition-all flex items-start group border relative overflow-hidden"
                :class="[
                  selectedPrompt?.id === p.id
                    ? `${getPromptColorTheme(p).bg} ${
                        getPromptColorTheme(p).border
                      } shadow-sm ring-1 ${getPromptColorTheme(p).ring} z-10`
                    : 'bg-white border-transparent hover:border-gray-300 hover:shadow-sm',
                ]"
              >
                <!-- Unsaved (Dirty) Indicator -->
                <div
                  v-if="isDirty && selectedPrompt?.id === p.id"
                  class="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-orange-500 shadow-sm animate-pulse"
                  title="有未保存的修改"
                ></div>

                <!-- Color Accent Bar -->
                <div
                  class="absolute left-0 top-0 bottom-0 w-1 opacity-0 group-hover:opacity-100 transition-opacity"
                  :class="getPromptColorTheme(p).accent"
                ></div>
                <div
                  v-if="selectedPrompt?.id === p.id"
                  class="absolute left-0 top-0 bottom-0 w-1"
                  :class="getPromptColorTheme(p).accent"
                ></div>

                <!-- Icon and Main Info -->
                <div class="flex-1 min-w-0">
                  <div class="flex items-center justify-between mb-0.5">
                    <div
                      class="flex items-center text-xs font-bold truncate pr-3"
                      :class="
                        selectedPrompt?.id === p.id
                          ? 'text-primary'
                          : 'text-gray-900'
                      "
                    >
                      {{ p.display_name || p.name }}
                    </div>
                    <ChevronRightIcon
                      v-if="selectedPrompt?.id === p.id"
                      class="w-3 h-3 text-primary flex-shrink-0"
                    />
                  </div>

                  <div class="flex items-center justify-between">
                    <div
                      class="text-[9px] text-gray-400 font-mono truncate flex items-center"
                    >
                      {{ p.name }}
                    </div>
                  </div>

                  <div
                    class="mt-1.5 flex items-center justify-between gap-2 overflow-hidden"
                  >
                    <div class="flex items-center space-x-1 flex-1 min-w-0">
                      <div
                        v-if="p.created_by"
                        class="flex items-center text-[9px] text-gray-400 bg-gray-50 px-1 py-0.5 rounded flex-shrink-0"
                      >
                        {{ p.created_by }}
                      </div>
                      <!-- Version Badge in Sidebar -->
                      <div
                        v-if="p.versions?.length"
                        class="text-[9px] font-bold text-gray-500 bg-gray-100 px-1 py-0.5 rounded flex-shrink-0"
                      >
                        v{{ p.versions.length }}
                      </div>
                      <!-- Relative Modified Time -->
                      <div
                        v-if="p.versions?.[0]?.updated_at"
                        class="text-[9px] text-gray-300 truncate"
                      >
                        {{ formatRelativeDate(p.versions[0].updated_at) }}
                      </div>
                    </div>
                    <span
                      v-if="p.is_system || p.category !== 'Agent'"
                      class="text-[8px] font-bold px-1 py-0.5 rounded flex-shrink-0 border border-current opacity-30 tracking-tighter"
                      :class="
                        p.category === 'Agent'
                          ? 'text-green-700'
                          : 'text-blue-700'
                      "
                    >
                      {{ p.category === "Agent" ? "SYS" : "CFG" }}
                    </span>
                  </div>
                </div>

                <!-- Hover Preview Tooltip -->
                <transition name="fade">
                  <div
                    v-if="
                      hoveredPromptId === p.id &&
                      !collapsedGroups[category] &&
                      selectedPrompt?.id !== p.id
                    "
                    class="absolute left-full ml-2 top-0 w-64 bg-gray-900 text-gray-200 text-xs p-3 rounded-lg shadow-2xl z-[100] border border-white/10 pointer-events-none"
                  >
                    <div
                      class="text-[10px] text-gray-500 font-bold uppercase mb-1 border-b border-white/5 pb-1"
                    >
                      内容预览 (Preview)
                    </div>
                    <div
                      class="font-mono leading-relaxed opacity-80"
                      style="
                        display: -webkit-box;
                        -webkit-line-clamp: 4;
                        -webkit-box-orient: vertical;
                        overflow: hidden;
                      "
                    >
                      {{ p.description || "暂无描述" }}
                    </div>
                    <div class="mt-2 flex justify-end">
                      <span class="text-[9px] text-primary/80 font-bold"
                        >点击查看详情 ›</span
                      >
                    </div>
                    <!-- Tooltip Arrow -->
                    <div
                      class="absolute left-0 top-4 -ml-1 border-8 border-transparent border-r-gray-900"
                    ></div>
                  </div>
                </transition>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Main Editor/Playground Area -->
      <div 
        class="flex-1 flex flex-col min-w-0"
        :class="{ 'fixed inset-0 z-[100] bg-white p-6': isFullscreen }"
      >
        <div
          v-if="currentDetail"
          class="flex-1 flex flex-col bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden"
        >
          <!-- Toolbar -->
          <div
            class="px-6 py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50"
          >
            <div class="flex items-center space-x-4">
              <div class="flex items-center">
                <span class="text-sm font-bold text-gray-900 mr-3">{{
                  selectedPrompt?.display_name || selectedPrompt?.name
                }}</span>
                <span
                  v-if="selectedPrompt?.source === 'system_config'"
                  class="px-2 py-0.5 rounded bg-blue-100 text-blue-700 text-[10px] font-bold uppercase"
                  >System</span
                >
                <span
                  v-else
                  class="px-2 py-0.5 rounded bg-purple-100 text-purple-700 text-[10px] font-bold uppercase"
                  >Agent</span
                >
              </div>

              <!-- Version Selector -->
              <div
                v-if="selectedPrompt?.versions?.length"
                class="flex items-center bg-white border border-gray-200 rounded-md px-2 py-1"
              >
                <span class="text-xs text-gray-500 mr-2">版本:</span>
                <select
                  v-model="selectedVersion"
                  @change="loadPromptDetail(selectedPrompt!, selectedVersion!)"
                  class="text-xs font-medium bg-transparent border-none focus:ring-0 p-0 cursor-pointer max-w-[120px]"
                >
                  <option
                    v-for="v in selectedPrompt.versions"
                    :key="v.version_number"
                    :value="v.version_number"
                  >
                    v{{ v.version_number }}
                    {{
                      v.status === "PUBLISHED"
                        ? "(当前)"
                        : v.status === "ARCHIVED"
                        ? "(历史)"
                        : ""
                    }}
                  </option>
                </select>
              </div>
            </div>

            <div class="flex items-center space-x-3">
              <!-- Fullscreen Toggle -->
              <button 
                @click="toggleFullscreen"
                class="p-2 text-gray-400 hover:text-primary rounded-lg hover:bg-white transition-all border border-transparent hover:border-gray-200 shadow-none hover:shadow-sm"
                :title="isFullscreen ? '退出全屏' : '全屏模式'"
              >
                <svg v-if="!isFullscreen" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"/></svg>
                <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 9L4 4m0 0h5M4 4v5m11 0V4m0 0h-5m5 0l-5 5m5 6l-5-5m5 5v-5m0 5h-5m-6 0l5-5m-5 5H4m5 0v-5"/></svg>
              </button>

              <!-- History Toggle for System Prompts -->
              <button
                v-if="selectedPrompt?.source === 'system_config'"
                @click="showHistory ? (showHistory = false) : fetchHistory()"
                class="flex items-center px-3 py-1.5 text-xs font-bold rounded-lg transition-all border shadow-sm group"
                :class="
                  showHistory
                    ? 'bg-gray-200 text-gray-700 border-gray-300'
                    : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50 hover:border-gray-300'
                "
              >
                <ArrowPathIcon
                  class="w-3.5 h-3.5 mr-1.5 text-gray-400 group-hover:rotate-180 transition-transform duration-500"
                  :class="{ 'animate-spin': loadingHistory }"
                />
                历史
              </button>

              <button
                v-if="canEdit && selectedPrompt?.source !== 'agent'"
                @click="requestSave"
                :disabled="saving"
                class="flex items-center px-4 py-1.5 bg-primary text-white text-xs font-bold rounded-lg hover:bg-primary-dark transition-all disabled:opacity-50 shadow-sm"
              >
                <CloudArrowUpIcon v-if="!saving" class="w-3.5 h-3.5 mr-1.5" />
                <ArrowPathIcon v-else class="w-3.5 h-3.5 mr-1.5 animate-spin" />
                更新
              </button>
              <span
                v-else
                class="px-2 py-1 bg-gray-100 text-gray-500 text-[10px] font-bold rounded border border-gray-200"
              >
                只读 (Read Only)
              </span>
            </div>
          </div>

          <div class="flex-1 flex overflow-hidden relative">
            <!-- History Overlay -->
            <transition name="slide">
              <div
                v-if="showHistory"
                class="absolute inset-0 z-30 bg-white flex flex-col border-r border-gray-100"
              >
                <div
                  class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50"
                >
                  <span class="text-sm font-bold text-gray-900"
                    >审计日志 (Audit Log)</span
                  >
                  <button
                    @click="showHistory = false"
                    class="text-gray-400 hover:text-gray-600"
                  >
                    <svg
                      class="w-5 h-5"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
                <div
                  class="flex-1 overflow-y-auto p-6 space-y-4 custom-scrollbar"
                >
                  <div
                    v-if="historyList.length === 0"
                    class="text-center py-12 text-gray-400"
                  >
                    暂无审计记录
                  </div>
                  <div
                    v-for="log in historyList"
                    :key="log.id"
                    class="border border-gray-100 rounded-lg p-4 hover:bg-gray-50 transition-colors"
                  >
                    <div class="flex justify-between items-start mb-2">
                      <div class="flex items-center gap-2">
                        <span
                          class="px-2 py-0.5 rounded text-[10px] font-bold"
                          :class="
                            log.change_type === 'CREATE'
                              ? 'bg-green-100 text-green-700'
                              : 'bg-blue-100 text-blue-700'
                          "
                        >
                          {{ log.change_type }}
                        </span>
                        <!-- Version Badge for System Prompts -->
                        <span 
                          v-if="selectedPrompt?.versions?.length"
                          class="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-700 border border-purple-200"
                          :title="'版本 ' + (selectedPrompt.versions.length - historyList.indexOf(log))"
                        >
                          v{{ selectedPrompt.versions.length - historyList.indexOf(log) }}
                        </span>
                        <span class="text-xs font-bold text-gray-900">{{
                          log.changed_by
                        }}</span>
                      </div>
                      <span class="text-[10px] text-gray-400 font-mono">{{
                        log.created_at
                      }}</span>
                    </div>
                    <div
                      v-if="log.description"
                      class="mb-2 p-2 bg-blue-50 border border-blue-100 rounded text-xs text-blue-800 flex items-start"
                    >
                      <span class="font-bold mr-1 flex-shrink-0">📝 备注:</span>
                      <span class="whitespace-pre-wrap">{{ log.description }}</span>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                      <div v-if="log.old_value" class="relative group/pane">
                        <div class="flex justify-between items-center mb-1">
                          <span class="text-[9px] text-gray-400 uppercase"
                            >变更前 (Diff)</span
                          >
                          <button
                            @click="copyToClipboard(log.old_value)"
                            class="p-1 text-gray-400 hover:text-primary transition-colors hover:bg-white rounded"
                            title="复制原始内容"
                          >
                            <ClipboardDocumentIcon class="w-3.5 h-3.5" />
                          </button>
                          <button
                            @click="restoreContent(log.old_value)"
                            class="p-1 text-gray-400 hover:text-green-600 transition-colors hover:bg-white rounded ml-1"
                            title="从该快照回撤"
                          >
                            <ArrowUturnLeftIcon class="w-3.5 h-3.5" />
                          </button>
                        </div>
                        <pre
                          class="mt-1 p-2 bg-gray-50 rounded text-[10px] text-gray-400 whitespace-pre-wrap max-h-60 overflow-y-auto custom-scrollbar"
                          v-html="
                            computeDiffHtml(log.old_value, log.new_value, 'old')
                          "
                        ></pre>
                      </div>
                      <div class="relative group/pane">
                        <div class="flex justify-between items-center mb-1">
                          <span class="text-[9px] text-gray-400 uppercase"
                            >变更后 (Diff)</span
                          >
                          <button
                            @click="copyToClipboard(log.new_value)"
                            class="p-1 text-gray-400 hover:text-primary transition-colors hover:bg-white rounded"
                            title="复制原始内容"
                          >
                            <ClipboardDocumentIcon class="w-3.5 h-3.5" />
                          </button>
                          <button
                            @click="restoreContent(log.new_value)"
                            class="p-1 text-gray-400 hover:text-green-600 transition-colors hover:bg-white rounded ml-1"
                            title="从该快照回撤"
                          >
                            <ArrowUturnLeftIcon class="w-3.5 h-3.5" />
                          </button>
                        </div>
                        <pre
                          class="mt-1 p-2 bg-gray-100 rounded text-[10px] text-gray-700 whitespace-pre-wrap max-h-60 overflow-y-auto custom-scrollbar"
                          v-html="
                            computeDiffHtml(log.old_value, log.new_value, 'new')
                          "
                        ></pre>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </transition>

            <!-- Left Side: Editor / Preview -->
            <div
              class="flex-[3] flex flex-col border-r border-gray-100 min-w-0"
            >
              <div
                class="px-4 py-2 bg-gray-50 border-b border-gray-100 flex justify-between items-center"
              >
                <div
                  class="flex items-center space-x-1 bg-gray-200/50 p-0.5 rounded-lg"
                >
                  <div class="relative inline-block text-left mr-1">
                    <button
                      @click="showSnippets = !showSnippets"
                      class="flex items-center px-3 py-1.5 text-[10px] font-bold rounded-md transition-all text-gray-500 hover:text-gray-700 bg-transparent hover:bg-white/50"
                      title="插入常用模版"
                    >
                      <QueueListIcon class="w-3.5 h-3.5" />
                    </button>
                    <div
                      v-if="showSnippets"
                      class="absolute top-full left-0 mt-1 w-48 bg-white rounded-lg shadow-xl border border-gray-100 z-50 overflow-hidden"
                    >
                      <div
                        v-for="(snip, idx) in snippets"
                        :key="idx"
                        @click="insertSnippet(snip.content)"
                        class="px-4 py-2 text-xs text-gray-700 hover:bg-indigo-50 hover:text-primary cursor-pointer border-b border-gray-50 last:border-0"
                      >
                        {{ snip.label }}
                      </div>
                    </div>
                  </div>
                  <button
                    @click="viewMode = 'edit'"
                    class="flex items-center px-3 py-1.5 text-[10px] font-bold rounded-md transition-all"
                    :class="
                      viewMode === 'edit'
                        ? 'bg-white text-primary shadow-sm'
                        : 'text-gray-500 hover:text-gray-700'
                    "
                  >
                    <PencilSquareIcon class="w-3.5 h-3.5 mr-1.5" />
                    编辑内容 (Edit)
                  </button>
                  <button
                    @click="viewMode = 'preview'"
                    class="flex items-center px-3 py-1.5 text-[10px] font-bold rounded-md transition-all"
                    :class="
                      viewMode === 'preview'
                        ? 'bg-white text-primary shadow-sm'
                        : 'text-gray-500 hover:text-gray-700'
                    "
                  >
                    <EyeIcon class="w-3.5 h-3.5 mr-1.5" />
                    预览效果 (Preview)
                  </button>
                </div>
                <div class="flex items-center space-x-3">
                  <div class="text-[10px] text-gray-400 italic hidden sm:block">
                    支持 {variable} 占位符
                  </div>
                  <button
                    @click="showPlayground = !showPlayground"
                    class="flex items-center px-3 py-1 text-[10px] font-bold rounded transition-all shadow-sm border"
                    :class="
                      showPlayground
                        ? 'bg-green-50 text-green-700 border-green-200 shadow-inner'
                        : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                    "
                  >
                    <BeakerIcon class="w-3 h-3 mr-1" />
                    {{ showPlayground ? "关闭测试" : "打开测试 (Playground)" }}
                  </button>
                </div>
              </div>

              <!-- Content Area (Professional Editor UI) -->
              <div class="flex-1 flex flex-col min-h-0 relative bg-white group/editor overflow-hidden">
                <!-- Editor Wrapper -->
                <div 
                  v-if="viewMode === 'edit'"
                  class="flex-1 flex relative overflow-hidden border-t border-gray-100"
                >
                  <!-- Line Numbers Simulation -->
                  <div class="w-10 bg-gray-50/50 border-r border-gray-100 flex flex-col py-6 items-center select-none pt-6 z-10">
                    <div v-for="i in 30" :key="i" class="text-[10px] text-gray-300 font-mono leading-relaxed mb-0.5">{{ i }}</div>
                  </div>

                  <!-- Real Textarea -->
                  <textarea
                    v-model="currentDetail.content"
                    :readonly="!canEdit"
                    class="flex-1 pl-6 pr-6 py-6 font-mono text-sm leading-relaxed text-gray-800 focus:outline-none resize-none bg-white selection:bg-indigo-100 z-10 relative overflow-y-auto custom-scrollbar"
                    :class="{ 'cursor-not-allowed opacity-80': !canEdit }"
                    placeholder="在此输入提示词内容..."
                  ></textarea>
                </div>

                <div
                  v-else
                  class="flex-1 p-8 overflow-y-auto bg-white custom-scrollbar"
                >
                  <div class="max-w-4xl mx-auto">
                    <div
                      class="markdown-body prose prose-sm sm:prose max-w-none"
                      v-html="renderMarkdown(currentDetail.content)"
                    ></div>
                  </div>
                </div>

                <!-- Floating AI Optimize Button (Improved Position) -->
                <div
                  v-if="canEdit && viewMode === 'edit'"
                  class="absolute bottom-6 right-6 flex flex-col items-end pointer-events-none group"
                >
                  <button
                    v-has-perm="'element:prompts:optimize'"
                    @click="showOptimizeConfirm = true"
                    :disabled="optimizing"
                    class="pointer-events-auto flex items-center px-4 py-2 bg-white text-indigo-600 text-xs font-bold rounded-full shadow-xl border border-indigo-100 hover:bg-indigo-600 hover:text-white hover:border-indigo-600 hover:-translate-y-1 transition-all active:scale-95 disabled:opacity-50 group/opt"
                  >
                    <div
                      class="w-6 h-6 bg-indigo-100 rounded-full flex items-center justify-center mr-2 -ml-1 group-hover/opt:bg-white/20 transition-colors"
                    >
                      <SparklesIcon
                        class="w-3.5 h-3.5 group-hover/opt:text-white transition-colors"
                        :class="{ 'animate-spin': optimizing }"
                      />
                    </div>
                    <span>AI 智能润色 (Optimize)</span>
                  </button>
                </div>
              </div>



              <div v-if="canEdit" class="p-4 border-t border-gray-100 bg-gray-50/30 backdrop-blur-sm">
                <div class="flex items-center justify-between mb-2">
                  <label class="block text-[10px] font-black text-gray-400 uppercase tracking-widest">
                    Version Note / 变更备注
                  </label>
                  <div
                    class="text-[10px] font-mono text-gray-400 bg-white px-2 py-0.5 rounded border border-gray-100 flex items-center shadow-sm"
                  >
                    <CalculatorIcon class="w-3 h-3 mr-1 text-primary/60" />
                    Estimated Token: <span class="font-bold text-gray-700 ml-1">{{ tokenCount }}</span>
                  </div>
                </div>
                <div class="relative group/note">
                  <textarea
                    v-model="versionNote"
                    rows="2"
                    placeholder="简要描述此次变更内容（例如：修复了xx逻辑，增加了xx约束）..."
                    class="w-full text-xs border border-gray-200 rounded-xl focus:border-primary/50 focus:ring-4 focus:ring-primary/5 resize-none p-3 bg-white shadow-sm transition-all"
                  ></textarea>
                </div>
                <div
                  class="flex justify-end mt-3"
                  v-if="selectedPrompt?.source === 'agent'"
                >
                  <button
                    @click="requestPublish"
                    class="flex items-center px-5 py-2 bg-indigo-600 text-white text-xs font-black rounded-xl hover:bg-indigo-700 hover:shadow-lg hover:shadow-indigo-200 transition-all active:scale-95 shadow-md"
                  >
                    <RocketLaunchIcon class="w-3.5 h-3.5 mr-2" />
                    SAVE & PUBLISH
                  </button>
                </div>
              </div>
            </div>

            <!-- Right Side: Playground Sliding Panel (Modern Refactor) -->
            <transition name="playground-slide">
              <div
                v-if="showPlayground"
                class="absolute top-0 right-0 bottom-0 w-[500px] z-20 bg-white/95 backdrop-blur-md border-l border-gray-100 shadow-2xl flex flex-col overflow-hidden"
              >
                <!-- Premium Header -->
                <div
                  class="px-5 py-3 bg-gradient-to-r from-gray-50 to-white border-b border-gray-100 flex flex-col justify-between relative overflow-hidden space-y-3"
                >
                  <div
                    class="absolute top-0 left-0 w-1 h-full bg-primary/40"
                  ></div>
                  <div class="flex items-center justify-between w-full">
                    <div class="flex items-center">
                      <div class="p-2 bg-primary/10 rounded-lg mr-3">
                        <BeakerIcon class="w-5 h-5 text-primary" />
                      </div>
                      <div>
                        <h3
                          class="text-sm font-bold text-gray-800 tracking-tight"
                        >
                          测试沙盒
                        </h3>
                        <p
                          class="text-[10px] text-gray-400 font-medium uppercase tracking-widest leading-none"
                        >
                          Playground
                        </p>
                      </div>
                    </div>
                    <div class="flex items-center space-x-2">
                      <button
                        @click="saveTestCase"
                        class="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
                        title="保存当前测试用例 (Local)"
                      >
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"/></svg>
                      </button>
                      <button
                        @click="runTest"
                        :disabled="testing"
                        class="relative group flex items-center px-4 py-1.5 bg-primary text-white text-xs font-bold rounded-lg hover:bg-primary-dark transition-all disabled:opacity-50 overflow-hidden shadow-md active:scale-95"
                      >
                        <PlayIcon v-if="!testing" class="w-3.5 h-3.5 mr-1.5" />
                        <ArrowPathIcon
                          v-else
                          class="w-3.5 h-3.5 mr-1.5 animate-spin"
                        />
                        <span>运行测试</span>
                      </button>
                      <button
                        @click="showPlayground = false"
                        class="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
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

                  <div class="flex items-center w-full">
                    <!-- Model Selector Full Width -->
                    <div
                      class="flex-1 flex items-center bg-white border border-gray-200 rounded-lg px-3 py-1.5 shadow-sm"
                    >
                      <CpuChipIcon
                        class="w-4 h-4 text-gray-400 mr-2 flex-shrink-0"
                      />
                      <span class="text-xs text-gray-400 mr-2 flex-shrink-0"
                        >模型:</span
                      >
                      <select
                        v-model="selectedModel"
                        class="flex-1 text-xs font-bold text-gray-700 bg-transparent border-none focus:ring-0 p-0 truncate cursor-pointer"
                      >
                        <option value="" disabled>选择推理模型...</option>
                        <option
                          v-for="m in models"
                          :key="m.id"
                          :value="m.model_id"
                        >
                          {{ m.name }}
                        </option>
                      </select>
                    </div>
                  </div>
                </div>

                <!-- Context Hint Banner -->
                <div
                  class="px-6 py-2 bg-blue-50/80 border-b border-blue-100/50 flex items-center"
                >
                  <div
                    class="flex items-center text-[10px] text-blue-700 font-medium tracking-wide"
                  >
                    <span class="mr-1.5 opacity-70">ℹ️</span>
                    当前测试:
                    <span
                      class="font-bold underline decoration-blue-200 underline-offset-2 mx-1"
                      >{{
                        selectedPrompt?.display_name || selectedPrompt?.name
                      }}</span
                    >
                    {{
                      selectedPrompt?.source === "agent" ? "智能体" : "系统配置"
                    }}
                    的
                    <span class="font-bold mx-1">v{{ selectedVersion }}</span>
                    版本提示词
                  </div>
                </div>

                <div class="flex-1 flex flex-col min-h-0 bg-gray-50/20">
                  <!-- Section Tabs/Indicators (Future) -->

                  <div
                    class="flex-1 overflow-y-auto custom-scrollbar p-6 space-y-8"
                  >
                    <!-- 1. Universal User Input (Chat Style) -->
                    <div class="space-y-3">
                      <div class="flex items-center justify-between">
                        <label
                          class="text-[11px] font-bold text-gray-500 uppercase flex items-center tracking-wider"
                        >
                          <span
                            class="w-2 h-2 rounded-full bg-blue-500 mr-2 shadow-sm shadow-blue-500/50"
                          ></span>
                          用户模拟输入
                        </label>
                      </div>
                      <div class="relative group">
                        <div
                          class="absolute -inset-0.5 bg-gradient-to-r from-blue-500 to-primary rounded-2xl blur opacity-0 group-focus-within:opacity-10 transition-opacity duration-300"
                        ></div>
                        <textarea
                          v-model="userInput"
                          rows="4"
                          class="relative w-full text-sm border-gray-100 rounded-2xl bg-white focus:border-primary/30 focus:ring-4 focus:ring-primary/5 transition-all p-4 shadow-sm placeholder-gray-300 text-gray-700 leading-relaxed"
                          placeholder="输入一段话，模拟用户发给智能体的请求..."
                        ></textarea>
                      </div>
                      <div class="flex items-start space-x-2 px-1">
                        <div class="p-1 bg-blue-50 rounded text-blue-500">
                          <svg
                            class="w-3 h-3"
                            fill="currentColor"
                            viewBox="0 0 20 20"
                          >
                            <path
                              fill-rule="evenodd"
                              d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
                              clip-rule="evenodd"
                            />
                          </svg>
                        </div>
                        <p class="text-[10px] text-gray-400 leading-tight">
                          如果没有变量占位符，此处内容将作为 Human Message
                          参与对话。
                        </p>
                      </div>
                    </div>

                    <!-- 2. Variables (Card style) -->
                    <div class="space-y-4 pt-2">
                      <div class="flex items-center justify-between">
                        <label
                          class="text-[11px] font-bold text-gray-500 uppercase flex items-center tracking-wider"
                        >
                          <span
                            class="w-2 h-2 rounded-full bg-amber-500 mr-2 shadow-sm shadow-amber-500/50"
                          ></span>
                          提取的变量 (Variables)
                        </label>
                        <span
                          v-if="Object.keys(testVariables).length"
                          class="text-[10px] font-semibold text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full"
                        >
                          {{ Object.keys(testVariables).length }} 个
                        </span>
                      </div>

                      <div
                        v-if="Object.keys(testVariables).length === 0"
                        class="bg-white/40 border border-gray-100 border-dashed rounded-2xl py-8 flex flex-col items-center justify-center space-y-2"
                      >
                        <div class="p-3 bg-gray-100/50 rounded-full">
                          <DocumentTextIcon class="w-6 h-6 text-gray-300" />
                        </div>
                        <p class="text-xs text-gray-400 italic">
                          未在提示词中检测到 {变量} 占位符
                        </p>
                      </div>

                      <div v-else class="grid grid-cols-1 gap-4">
                        <div
                          v-for="(_, key) in testVariables"
                          :key="key"
                          class="bg-white border border-gray-100 rounded-2xl p-4 shadow-sm hover:shadow-md transition-shadow group/var"
                        >
                          <div
                            class="flex items-center justify-between mb-2 px-0.5"
                          >
                            <span
                              class="text-[11px] font-bold text-gray-600 group-hover/var:text-primary transition-colors flex items-center"
                            >
                              <span
                                class="px-1.5 py-0.5 bg-gray-50 text-gray-400 rounded mr-2"
                                >$</span
                              >
                              {{ key }}
                            </span>
                          </div>
                          <textarea
                            v-model="testVariables[key]"
                            rows="2"
                            class="w-full text-xs border-transparent rounded-lg bg-gray-50/50 focus:bg-white focus:border-primary/20 focus:ring-0 transition-all p-3 font-mono leading-relaxed"
                            :placeholder="'输入 ' + key + ' ...'"
                          ></textarea>
                        </div>
                      </div>
                    </div>

                    <!-- 3. Test Output Section -->
                    <div class="space-y-4 pt-2 border-t border-gray-100/50">
                      <div class="flex items-center justify-between">
                        <label
                          class="text-[11px] font-bold text-gray-500 uppercase flex items-center tracking-wider"
                        >
                          <span
                            class="w-2 h-2 rounded-full bg-green-500 mr-2 shadow-sm shadow-green-500/50"
                          ></span>
                          推理输出 (Execution Output)
                        </label>
                        <div
                          v-if="testResult"
                          class="flex items-center space-x-2"
                        >
                          <span
                            class="flex items-center px-2 py-0.5 bg-green-50 text-green-600 rounded-full text-[9px] font-bold border border-green-100"
                          >
                            {{ Math.round(testResult.latency_ms) }}ms
                          </span>
                          <button
                            @click="testResult = null"
                            class="text-gray-300 hover:text-gray-500 transition-colors"
                          >
                            <svg
                              class="w-3.5 h-3.5"
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                            >
                              <path
                                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                              />
                            </svg>
                          </button>
                        </div>
                      </div>

                      <div
                        v-if="!testResult && !testing"
                        class="bg-white border border-gray-100 rounded-2xl p-10 flex flex-col items-center justify-center space-y-4 text-center"
                      >
                        <div class="relative">
                          <div
                            class="absolute inset-0 bg-primary/20 blur-xl rounded-full scale-150 animate-pulse"
                          ></div>
                          <div class="relative p-4 bg-primary/10 rounded-full">
                            <PlayIcon class="w-8 h-8 text-primary/60" />
                          </div>
                        </div>
                        <div>
                          <p class="text-xs font-bold text-gray-500">
                            就绪 (Ready to Test)
                          </p>
                          <p
                            class="text-[10px] text-gray-400 mt-1 max-w-[200px] mx-auto"
                          >
                            配置好输入和变量后，点击右上角的运行按钮开始推理。
                          </p>
                        </div>
                      </div>

                      <div
                        v-if="testing"
                        class="bg-white border border-gray-100 rounded-2xl p-10 flex flex-col items-center justify-center space-y-6"
                      >
                        <div class="flex space-x-2">
                          <div
                            class="w-2.5 h-2.5 bg-primary/30 rounded-full animate-bounce"
                            style="animation-delay: 0s"
                          ></div>
                          <div
                            class="w-2.5 h-2.5 bg-primary/60 rounded-full animate-bounce"
                            style="animation-delay: 0.2s"
                          ></div>
                          <div
                            class="w-2.5 h-2.5 bg-primary/30 rounded-full animate-bounce"
                            style="animation-delay: 0.4s"
                          ></div>
                        </div>
                        <p
                          class="text-xs font-bold text-gray-400 animate-pulse tracking-widest uppercase"
                        >
                          LLM Inferencing...
                        </p>
                      </div>

                      <div v-if="testResult" class="space-y-6">
                        <!-- Chat Bubble Result -->
                        <div class="relative">
                          <div
                            class="absolute -left-2 top-0 bottom-0 w-0.5 bg-primary/10 rounded-full"
                          ></div>
                          <div
                            class="bg-gradient-to-br from-primary/5 to-transparent rounded-2xl rounded-tl-none p-5 border border-primary/10 shadow-sm"
                          >
                            <div class="flex items-center mb-3">
                              <div
                                class="w-5 h-5 bg-primary rounded-md flex items-center justify-center mr-2 shadow-sm"
                              >
                                <span class="text-[10px] text-white font-bold"
                                  >AI</span
                                >
                              </div>
                              <span
                                class="text-[10px] font-bold text-gray-800 uppercase tracking-tight"
                                >智能体响应</span
                              >
                            </div>
                            <pre
                              class="text-xs text-gray-800 whitespace-pre-wrap font-sans leading-relaxed selection:bg-primary/20"
                              >{{ streamedOutput }}<span v-if="testing || (streamedOutput.length < (testResult?.raw_output?.length || 0))" class="animate-pulse inline-block w-1.5 h-3 bg-primary align-middle ml-0.5"></span></pre>

                            <div
                              class="mt-4 pt-4 border-t border-primary/5 flex justify-end"
                            >
                              <button
                                @click="copyToClipboard(testResult.raw_output || streamedOutput)"
                                class="flex items-center text-[10px] font-bold text-gray-400 hover:text-primary transition-colors"
                              >
                                <ClipboardDocumentIcon
                                  class="w-3.5 h-3.5 mr-1"
                                />
                                复制响应正文
                              </button>
                            </div>
                          </div>
                        </div>

                        <!-- Rendered Prompt Detail -->
                        <details
                          class="group bg-gray-50/50 rounded-2xl border border-gray-100 overflow-hidden transition-all"
                        >
                          <summary
                            class="flex items-center justify-between p-4 cursor-pointer hover:bg-gray-100/50 transition-colors list-none outline-none"
                          >
                            <span
                              class="text-[10px] font-bold text-gray-400 uppercase tracking-wider flex items-center"
                            >
                              <DocumentTextIcon
                                class="w-3.5 h-3.5 mr-2 opacity-50"
                              />
                              查看渲染后的完整 Prompt
                            </span>
                            <div
                              class="group-open:rotate-180 transition-transform duration-300"
                            >
                              <ChevronRightIcon class="w-3 h-3 text-gray-300" />
                            </div>
                          </summary>
                          <div class="px-4 pb-4">
                            <div
                              class="bg-gray-900 rounded-xl p-4 shadow-inner"
                            >
                              <pre
                                class="text-[10px] text-gray-300 font-mono leading-relaxed whitespace-pre-wrap custom-scrollbar max-h-[300px] overflow-y-auto"
                                >{{ testResult.interpolated_prompt }}</pre
                              >
                            </div>
                          </div>
                        </details>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </transition>
          </div>
        </div>

        <!-- Empty State -->
        <div
          v-else
          class="flex-1 flex flex-col items-center justify-center bg-white rounded-xl shadow-sm border border-gray-200 text-gray-400"
        >
          <DocumentTextIcon class="w-16 h-16 mb-4 opacity-20" />
          <h2 class="text-xl font-medium">请从左侧选择一个提示词进行编辑</h2>
          <p class="mt-2 text-sm">
            您可以选择系统级核心配置或特定智能体的角色设定。
          </p>
        </div>
      </div>
    </div>

    <!-- Toast -->
    <Teleport to="body">
      <div
        v-if="toast.show"
        class="fixed bottom-8 left-1/2 -translate-x-1/2 z-[100] px-6 py-3 rounded-xl shadow-2xl flex items-center space-x-3 transition-all animate-bounce-in"
        :class="
          toast.type === 'success'
            ? 'bg-green-600 text-white'
            : toast.type === 'warning'
            ? 'bg-yellow-500 text-white'
            : 'bg-red-600 text-white'
        "
      >
        <CheckBadgeIcon v-if="toast.type === 'success'" class="w-5 h-5" />
        <span class="font-bold text-sm">{{ toast.message }}</span>
      </div>
    </Teleport>

    <!-- Confirm Modal -->
    <ConfirmModal
      v-if="showConfirm"
      title="确认更新配置"
      :message="
        selectedPrompt?.source === 'agent'
          ? '确定要为该智能体发布新版本吗？'
          : '确定要更新此系统级 Prompt 配置吗？此操作将立即生效。'
      "
      type="primary"
      confirm-text="确认更新"
      @confirm="performSave"
      @cancel="showConfirm = false"
    />
    <!-- AI Optimize Confirm Modal -->
    <ConfirmModal
      v-if="showOptimizeConfirm"
      title="✨ AI 提示词优化 (Prompt Optimization)"
      message="AI 将针对当前内容生成 5 个不同侧重点的优化建议方案。生成过程大约需要几秒钟。是否开始？"
      confirmText="开始优化"
      cancelText="取消"
      @confirm="runOptimize"
      @cancel="showOptimizeConfirm = false"
    />

    <!-- Publish Confirm Modal -->
    <ConfirmModal
      v-if="showPublishConfirm"
      title="发布验证"
      message="确定要保存当前并发布为新的线上版本吗？此操作将立即覆盖线上 Prompt。"
      confirmText="确认发布"
      cancelText="取消"
      type="primary"
      @confirm="performPublish"
      @cancel="showPublishConfirm = false"
    />

    <!-- Global Loading Mask for Optimization -->
    <Teleport to="body">
      <transition name="fade">
        <div
          v-if="optimizing"
          class="fixed inset-0 z-[9999] bg-white/50 backdrop-blur-[2px] flex flex-col items-center justify-center"
        >
          <div
            class="p-10 bg-white rounded-3xl shadow-2xl border border-indigo-100 flex flex-col items-center animate-bounce-in"
          >
            <div class="relative w-20 h-20 mb-6">
              <div
                class="absolute inset-0 rounded-full border-4 border-indigo-50"
              ></div>
              <div
                class="absolute inset-0 rounded-full border-4 border-indigo-500 border-t-transparent animate-spin"
              ></div>
              <SparklesIcon
                class="absolute inset-0 m-auto w-8 h-8 text-indigo-500 animate-pulse"
              />
            </div>
            <div class="text-base font-bold text-gray-900 mb-2">
              AI 正在深度优化中...
            </div>
            <div class="text-xs text-gray-400">正在为您生成 5 个差异化方案</div>
          </div>
        </div>
      </transition>
    </Teleport>

    <!-- AI Suggestions Modal -->
    <div
      v-if="showOptimizeModal"
      class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-gray-900/40 backdrop-blur-sm"
    >
      <div
        class="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden animate-fade-in-up"
      >
        <div
          class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/80"
        >
          <div class="flex items-center">
            <div class="p-2 bg-indigo-100 rounded-lg mr-3">
              <SparklesIcon class="w-5 h-5 text-indigo-600" />
            </div>
            <div>
              <h3 class="text-sm font-bold text-gray-900">
                AI 优化建议 (Suggestions)
              </h3>
              <p class="text-[10px] text-gray-400 font-medium">
                共生成了 5 个不同侧重点的提示词方案
              </p>
            </div>
          </div>
          <button
            @click="showOptimizeModal = false"
            class="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
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

        <div class="flex-1 flex min-h-0">
          <!-- Sidebar Tabs -->
          <div
            class="w-48 border-r border-gray-100 bg-gray-50/30 overflow-y-auto"
          >
            <div
              v-for="(item, index) in optimizeSuggestions"
              :key="index"
              @click="activeOptimizeTab = index"
              class="px-4 py-4 cursor-pointer transition-all border-l-4"
              :class="
                activeOptimizeTab === index
                  ? 'bg-white border-indigo-500 text-indigo-700 font-bold'
                  : 'border-transparent text-gray-500 hover:bg-gray-100/50'
              "
            >
              <div
                class="text-[10px] opacity-60 mb-1 uppercase tracking-tighter"
              >
                方案 {{ index + 1 }}
              </div>
              <div class="text-xs truncate">{{ item.title }}</div>
            </div>
          </div>

          <!-- Content Area -->
          <div class="flex-1 flex flex-col min-h-0 bg-white">
            <transition name="slide-fade" mode="out-in">
              <div
                v-if="optimizeSuggestions[activeOptimizeTab]"
                :key="activeOptimizeTab"
                class="flex-1 flex flex-col p-6 overflow-hidden"
              >
                <div
                  class="mb-4 p-3 bg-indigo-50 border border-indigo-100 rounded-xl"
                >
                  <div
                    class="text-[10px] font-bold text-indigo-700 uppercase mb-1"
                  >
                    推荐理由 (Reason)
                  </div>
                  <p class="text-xs text-indigo-900 leading-relaxed">
                    {{ optimizeSuggestions[activeOptimizeTab].reason }}
                  </p>
                </div>

                <div
                  class="flex-1 relative bg-gray-50 rounded-xl overflow-hidden border border-gray-100"
                >
                  <div class="absolute top-3 right-3 z-10">
                    <button
                      @click="
                        copyToClipboard(
                          optimizeSuggestions[activeOptimizeTab].content
                        )
                      "
                      class="p-1.5 bg-white shadow-sm border border-gray-200 rounded-lg text-gray-400 hover:text-indigo-600 transition-all active:scale-90"
                      title="复制此版本"
                    >
                      <ClipboardDocumentIcon class="w-4 h-4" />
                    </button>
                  </div>
                  <pre
                    class="w-full h-full p-6 text-xs text-gray-700 font-mono overflow-y-auto whitespace-pre-wrap custom-scrollbar"
                    >{{ optimizeSuggestions[activeOptimizeTab].content }}</pre
                  >
                </div>

                <div class="mt-6 flex justify-end">
                  <button
                    @click="
                      applySuggestion(
                        optimizeSuggestions[activeOptimizeTab].content
                      )
                    "
                    class="px-6 py-2.5 bg-indigo-600 text-white text-xs font-bold rounded-xl hover:bg-indigo-700 shadow-lg shadow-indigo-600/20 active:scale-95 transition-all flex items-center"
                  >
                    <CheckBadgeIcon class="w-4 h-4 mr-2" />
                    应用此优化版本 (Apply)
                  </button>
                </div>
              </div>
            </transition>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
  height: 4px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #e5e7eb;
  border-radius: 2px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #d1d5db;
}

@keyframes bounce-in {
  0% {
    transform: translate(-50%, 100%);
    opacity: 0;
  }
  60% {
    transform: translate(-50%, -10px);
    opacity: 1;
  }
  100% {
    transform: translate(-50%, 0);
  }
}
.animate-bounce-in {
  animation: bounce-in 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.slide-enter-active,
.slide-leave-active {
  transition: transform 0.3s ease, opacity 0.3s ease;
}
.slide-enter-from {
  transform: translateX(-20px);
  opacity: 0;
}
.slide-leave-to {
  transform: translateX(-20px);
  opacity: 0;
}

.playground-slide-enter-active,
.playground-slide-leave-active {
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
.playground-slide-enter-from,
.playground-slide-leave-to {
  transform: translateX(100%);
  opacity: 0;
}

/* Markdown Styles */
:deep(.markdown-body) {
  font-size: 14px;
  color: #374151;
  line-height: 1.6;
}
:deep(.markdown-body p) {
  margin-bottom: 1.25em;
}
:deep(.markdown-body p:last-child) {
  margin-bottom: 0;
}
:deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  font-weight: 700;
  margin-top: 2em;
  margin-bottom: 0.75em;
  color: #111827;
}
:deep(.markdown-body h1) {
  font-size: 1.5em;
  border-bottom: 1px solid #e5e7eb;
  padding-bottom: 0.3em;
}
:deep(.markdown-body h2) {
  font-size: 1.25em;
  border-bottom: 1px solid #f3f4f6;
  padding-bottom: 0.2em;
}
:deep(.markdown-body h3) {
  font-size: 1.1em;
}

:deep(.markdown-body code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
    "Liberation Mono", "Courier New", monospace;
  background-color: rgba(175, 184, 193, 0.2);
  padding: 0.2em 0.4em;
  border-radius: 6px;
  font-size: 85%;
  color: #ef4444;
}
:deep(.markdown-body pre) {
  margin-top: 1.5em;
  margin-bottom: 1.5em;
  padding: 1.5em 1.25em 1.25em 1.25em;
  background-color: #1e293b;
  border-radius: 12px;
  overflow: auto;
  position: relative;
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
}
:deep(.markdown-body pre):before {
  content: "";
  position: absolute;
  top: 12px;
  left: 14px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background-color: #ff5f56;
  box-shadow: 18px 0 0 #ffbd2e, 36px 0 0 #27c93f;
  z-index: 1;
}
:deep(.markdown-body pre code) {
  background-color: transparent;
  padding: 0;
  font-size: 13px;
  font-family: "Fira Code", "JetBrains Mono", source-code-pro, Menlo, Monaco,
    Consolas, monospace;
  color: #e2e8f0;
  line-height: 1.6;
}

/* Highlight.js Color Overrides (Tailwind-like Palette) */
:deep(.hljs-keyword),
:deep(.hljs-selector-tag) {
  color: #818cf8;
}
:deep(.hljs-string) {
  color: #34d399;
}
:deep(.hljs-number) {
  color: #fbbf24;
}
:deep(.hljs-type),
:deep(.hljs-built_in) {
  color: #fbbf24;
}
:deep(.hljs-attr),
:deep(.hljs-variable) {
  color: #f472b6;
}
:deep(.hljs-comment) {
  color: #94a3b8;
  font-style: italic;
}
:deep(.hljs-function) {
  color: #38bdf8;
}
:deep(.hljs-params) {
  color: #e2e8f0;
}
:deep(.hljs-meta) {
  color: #38bdf8;
}
:deep(.hljs-operator) {
  color: #2dd4bf;
}
:deep(.hljs-title) {
  color: #38bdf8;
}

:deep(.markdown-body ul, .markdown-body ol) {
  padding-left: 1.5em;
  margin-bottom: 1.25em;
}
:deep(.markdown-body li) {
  margin-bottom: 0.5em;
}
:deep(.markdown-body blockquote) {
  border-left: 4px solid #e5e7eb;
  padding-left: 1em;
  color: #6b7280;
  font-style: italic;
  margin: 1.5em 0;
}
:deep(.markdown-body table) {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 1.5em;
}
:deep(.markdown-body th, .markdown-body td) {
  border: 1px solid #e5e7eb;
  padding: 8px 14px;
  text-align: left;
}
:deep(.markdown-body th) {
  background-color: #f9fafb;
  font-weight: 600;
}
:deep(.markdown-body tr:nth-child(even)) {
  background-color: #fbfcfd;
}
</style>
