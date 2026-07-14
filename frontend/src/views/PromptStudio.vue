<script setup lang="ts">
import { ref, onMounted, computed, watch, nextTick } from "vue";
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
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
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
const viewMode = ref<"edit" | "preview">("preview");
const historyList = ref<any[]>([]);
const loadingHistory = ref(false);
const showHistory = ref(false);
const showOptimizeConfirm = ref(false);
const showPublishConfirm = ref(false);
const showOptimizeModal = ref(false);
const optimizing = ref(false);
const optimizeSuggestions = ref<any[]>([]);
const activeOptimizeTab = ref(0);
const models = ref<AIModel[]>([]);
const selectedModel = ref<string>("");
const showSnippets = ref(false);
const showSidebar = ref(true);
const activeCategoryTab = ref<'ALL' | 'System' | 'Agent'>('ALL');
const isFullscreen = ref(false);
const showUnsavedConfirm = ref(false);
const pendingPromptSwitch = ref<{ prompt: PromptMetadata; version?: number } | null>(null);

const editorTextareaRef = ref<HTMLTextAreaElement | null>(null);
const lineNumbersRef = ref<HTMLDivElement | null>(null);

const lineCount = computed(() => {
  const content = currentDetail.value?.content || "";
  return content.split("\n").length || 1;
});

const charCount = computed(() => currentDetail.value?.content?.length || 0);

const syncLineNumberScroll = () => {
  if (lineNumbersRef.value && editorTextareaRef.value) {
    lineNumbersRef.value.scrollTop = editorTextareaRef.value.scrollTop;
  }
};

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
    models.value = res.data.filter((m) => (m.type === "llm" || m.type === "multimodal") && m.is_active);
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
  showHistory.value = false;
  showSnippets.value = false;

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
    if (canEdit.value && (!res.data.content || res.data.content.trim() === "")) {
      viewMode.value = "edit";
    } else {
      viewMode.value = "preview";
    }

    const newVars: Record<string, string> = {};
    res.data.variables.forEach((v: string) => {
      newVars[v] = testVariables.value[v] || "";
    });
    testVariables.value = newVars;

    loadTestCase();
    await nextTick();
    syncLineNumberScroll();
  } catch (e: any) {
    console.error("Failed to load prompt detail:", e);
    const errorMessage = e.response?.data?.detail || "获取详情失败";
    showToast(errorMessage, "error");
  } finally {
    loadingDetail.value = false;
  }
};

const selectPrompt = async (prompt: PromptMetadata, version?: number) => {
  if (
    isDirty.value &&
    selectedPrompt.value &&
    (selectedPrompt.value.id !== prompt.id ||
      (version !== undefined && version !== selectedVersion.value))
  ) {
    pendingPromptSwitch.value = { prompt, version };
    showUnsavedConfirm.value = true;
    return;
  }
  await loadPromptDetail(prompt, version);
};

const confirmDiscardAndSwitch = async () => {
  showUnsavedConfirm.value = false;
  const pending = pendingPromptSwitch.value;
  pendingPromptSwitch.value = null;
  if (pending) {
    await loadPromptDetail(pending.prompt, pending.version);
  }
};

const cancelPromptSwitch = () => {
  showUnsavedConfirm.value = false;
  pendingPromptSwitch.value = null;
};

const onVersionChange = () => {
  if (!selectedPrompt.value || selectedVersion.value == null) return;
  const targetVersion = selectedVersion.value;
  // v-model 已切版本；若有脏数据取消时需回滚
  const previousVersion = Number(
    (currentDetail.value as any)?.version_number ?? selectedVersion.value
  );
  if (isDirty.value) {
    pendingPromptSwitch.value = {
      prompt: selectedPrompt.value,
      version: targetVersion,
    };
    // 先回滚显示，确认后再切
    selectedVersion.value = previousVersion;
    showUnsavedConfirm.value = true;
    return;
  }
  selectPrompt(selectedPrompt.value, targetVersion);
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
  <div class="h-full flex flex-col min-h-0">
    <!-- Compact Header -->
    <div class="mb-4 flex justify-between items-center shrink-0">
      <div class="flex items-center gap-3">
        <h1 class="text-xl font-bold text-gray-900">提示词工坊</h1>
        <span class="text-xs text-gray-400 hidden sm:inline">统一管理、编辑并测试系统与智能体提示词</span>
      </div>
      <button
        @click="fetchPrompts"
        class="p-2 text-gray-500 hover:text-primary rounded-lg hover:bg-white border border-transparent hover:border-gray-200 transition-all"
        title="刷新列表"
      >
        <ArrowPathIcon class="w-4 h-4" :class="{ 'animate-spin': loading }" />
      </button>
    </div>

    <div
      v-if="initialLoading"
      class="flex-1 flex flex-col items-center justify-center bg-white rounded-xl shadow-sm border border-gray-200"
    >
      <ArrowPathIcon class="w-10 h-10 text-primary animate-spin mb-3" />
      <p class="text-sm text-gray-600">正在初始化提示词工坊...</p>
    </div>

    <div
      v-else-if="initError"
      class="flex-1 flex flex-col items-center justify-center bg-white rounded-xl shadow-sm border border-gray-200"
    >
      <p class="text-sm font-medium text-gray-700">加载失败</p>
      <p class="mt-1 text-xs text-gray-500">{{ initError }}</p>
      <button
        @click="reloadPage"
        class="mt-4 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-dark text-sm"
      >
        刷新页面
      </button>
    </div>

    <div v-else class="flex-1 flex gap-4 min-h-0 overflow-hidden">
      <!-- Sidebar -->
      <div
        class="flex flex-col bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden transition-all duration-300 shrink-0"
        :class="showSidebar ? 'w-72' : 'w-0 border-0 opacity-0'"
      >
        <div class="px-3 py-3 border-b border-gray-100 bg-gray-50 space-y-2.5 shrink-0">
          <div class="flex p-0.5 bg-gray-200/60 rounded-lg">
            <button
              v-for="tab in [
                { id: 'ALL', label: '全部' },
                { id: 'System', label: '系统' },
                { id: 'Agent', label: '智能体' },
              ]"
              :key="tab.id"
              @click="activeCategoryTab = tab.id as any"
              class="flex-1 py-1.5 text-[11px] font-semibold rounded-md transition-all text-center"
              :class="activeCategoryTab === tab.id ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
            >
              {{ tab.label }}
            </button>
          </div>
          <div class="relative">
            <MagnifyingGlassIcon class="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              v-model="searchQuery"
              type="text"
              placeholder="搜索提示词..."
              class="w-full pl-8 pr-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs focus:ring-1 focus:ring-primary focus:border-primary outline-none"
            />
          </div>
        </div>

        <div class="flex-1 overflow-y-auto p-2 space-y-3 custom-scrollbar">
          <div v-for="(items, category) in groupedPrompts" :key="category">
            <button
              type="button"
              @click="toggleGroup(category)"
              class="w-full px-2.5 py-1.5 mb-1 text-[10px] font-bold uppercase tracking-wider rounded-md flex items-center justify-between text-gray-500 hover:bg-gray-50"
            >
              <span class="flex items-center gap-1.5">
                <span>{{ category === 'System' ? '系统' : category === 'Agent' ? '智能体' : category }}</span>
                <span class="px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-400 font-mono">{{ items.length }}</span>
              </span>
              <ChevronDownIcon
                class="w-3 h-3 transition-transform"
                :class="{ 'rotate-[-90deg]': collapsedGroups[category] }"
              />
            </button>
            <div v-show="!collapsedGroups[category]" class="space-y-2 mt-1.5">
              <button
                v-for="p in items"
                :key="p.id"
                type="button"
                @click="selectPrompt(p)"
                class="w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all border shadow-sm"
                :class="
                  selectedPrompt?.id === p.id
                    ? 'bg-primary/5 border-primary/30 text-primary ring-1 ring-primary/10'
                    : 'bg-white border-gray-100 hover:border-gray-300 hover:shadow hover:bg-gray-50/50 text-gray-800'
                "
              >
                <div class="flex items-center justify-between gap-2">
                  <div class="flex items-center gap-1.5 min-w-0">
                    <!-- 根据类型展示图标 -->
                    <CpuChipIcon
                      v-if="p.source === 'system_config'"
                      class="w-3.5 h-3.5 shrink-0"
                      :class="selectedPrompt?.id === p.id ? 'text-primary/70' : 'text-gray-400'"
                    />
                    <SparklesIcon
                      v-else
                      class="w-3.5 h-3.5 shrink-0"
                      :class="selectedPrompt?.id === p.id ? 'text-primary/80' : 'text-amber-500'"
                    />
                    <span class="text-xs font-semibold truncate">{{ p.display_name || p.name }}</span>
                  </div>
                  <span
                    v-if="isDirty && selectedPrompt?.id === p.id"
                    class="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0"
                    title="未保存"
                  ></span>
                </div>
                <div
                  v-if="p.description"
                  class="mt-1.5 px-2 py-1 text-[11px] rounded-r border-l-2 truncate"
                  :class="
                    selectedPrompt?.id === p.id
                      ? 'bg-primary/10 border-primary/30 text-primary/80'
                      : 'bg-gray-50 border-gray-200 text-gray-500'
                  "
                  :title="p.description"
                >
                  {{ p.description }}
                </div>
                <div class="mt-1.5 flex items-center gap-1.5 text-[10px] text-gray-400">
                  <template v-if="p.versions?.length">
                    <span
                      class="font-mono px-1 rounded text-[10px]"
                      :class="selectedPrompt?.id === p.id ? 'bg-primary/15 text-primary-dark font-medium' : 'bg-gray-100 text-gray-600'"
                    >
                      v{{ p.versions.length }}
                    </span>
                    <span
                      v-if="p.versions[0]?.updated_at"
                      class="truncate"
                      :class="selectedPrompt?.id === p.id ? 'text-primary/60' : 'text-gray-400'"
                    >
                      {{ formatRelativeDate(p.versions[0].updated_at) }}
                    </span>
                  </template>
                  <template v-else>
                    <span
                      class="font-mono px-1 rounded text-[10px] border"
                      :class="selectedPrompt?.id === p.id ? 'bg-primary/10 border-primary/20 text-primary/60' : 'bg-gray-50 border-gray-100 text-gray-400'"
                    >
                      无版本
                    </span>
                    <span
                      :class="selectedPrompt?.id === p.id ? 'text-primary/60' : 'text-gray-400'"
                    >
                      未编辑
                    </span>
                  </template>
                </div>
              </button>
            </div>
          </div>
          <div v-if="Object.keys(groupedPrompts).length === 0" class="py-10 text-center text-xs text-gray-400">
            没有匹配的提示词
          </div>
        </div>
      </div>

      <!-- Main -->
      <div
        class="flex-1 flex flex-col min-w-0 min-h-0"
        :class="{ 'fixed inset-0 z-[100] bg-slate-50 p-4': isFullscreen }"
      >
        <div
          v-if="currentDetail"
          class="flex-1 flex flex-col bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden min-h-0"
        >
          <!-- Top bar: identity + primary actions -->
          <div class="px-4 py-3 border-b border-gray-100 flex items-center justify-between gap-3 bg-gray-50 shrink-0">
            <div class="flex items-center gap-2 min-w-0">
              <button
                type="button"
                @click="showSidebar = !showSidebar"
                class="p-1.5 text-gray-400 hover:text-primary hover:bg-white rounded-lg border border-transparent hover:border-gray-200"
                :title="showSidebar ? '隐藏列表' : '显示列表'"
              >
                <ChevronRightIcon class="w-4 h-4" :class="{ 'rotate-180': showSidebar }" />
              </button>
              <div class="min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-sm font-bold text-gray-900 truncate">
                    {{ selectedPrompt?.display_name || selectedPrompt?.name }}
                  </span>
                  <span
                    class="px-1.5 py-0.5 rounded text-[10px] font-bold"
                    :class="selectedPrompt?.source === 'system_config' ? 'bg-blue-50 text-blue-700' : 'bg-emerald-50 text-emerald-700'"
                  >
                    {{ selectedPrompt?.source === 'system_config' ? '系统' : '智能体' }}
                  </span>
                  <span v-if="isDirty" class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-50 text-amber-700 border border-amber-100">未保存</span>
                  <span v-if="!canEdit" class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-100 text-gray-500">只读</span>
                </div>
                <p v-if="selectedPrompt?.description" class="text-[11px] text-gray-400 truncate mt-0.5">
                  {{ selectedPrompt.description }}
                </p>
              </div>
            </div>

            <div class="flex items-center gap-2 shrink-0">
              <div
                v-if="selectedPrompt?.versions?.length"
                class="hidden sm:flex items-center bg-white border border-gray-200 rounded-lg px-2 py-1"
              >
                <span class="text-[11px] text-gray-400 mr-1">版本</span>
                <select
                  v-model="selectedVersion"
                  @change="onVersionChange"
                  class="text-xs font-medium bg-transparent border-none focus:ring-0 p-0 cursor-pointer max-w-[130px]"
                >
                  <option
                    v-for="v in selectedPrompt.versions"
                    :key="v.version_number"
                    :value="v.version_number"
                  >
                    v{{ v.version_number }}
                    {{ v.status === 'PUBLISHED' ? '·已发布' : v.status === 'ARCHIVED' ? '·历史' : '' }}
                  </option>
                </select>
              </div>

              <button
                type="button"
                @click="toggleFullscreen"
                class="p-2 text-gray-400 hover:text-primary rounded-lg hover:bg-white border border-transparent hover:border-gray-200 flex items-center justify-center shrink-0"
                :title="isFullscreen ? '退出全屏' : '全屏'"
              >
                <ArrowsPointingOutIcon v-if="!isFullscreen" class="w-4 h-4" />
                <ArrowsPointingInIcon v-else class="w-4 h-4" />
              </button>

              <button
                v-if="selectedPrompt?.source === 'system_config'"
                type="button"
                @click="showHistory ? (showHistory = false) : fetchHistory()"
                class="px-3 py-1.5 text-xs font-semibold rounded-lg border transition-all"
                :class="showHistory ? 'bg-gray-200 text-gray-700 border-gray-300' : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'"
              >
                历史
              </button>

              <button
                v-if="canEdit && selectedPrompt?.source !== 'agent'"
                type="button"
                @click="requestSave"
                :disabled="saving || !isDirty"
                class="flex items-center px-3.5 py-1.5 bg-primary text-white text-xs font-bold rounded-lg hover:bg-primary-dark disabled:opacity-50 shadow-sm"
              >
                <CloudArrowUpIcon v-if="!saving" class="w-3.5 h-3.5 mr-1" />
                <ArrowPathIcon v-else class="w-3.5 h-3.5 mr-1 animate-spin" />
                保存
              </button>
              <button
                v-if="canEdit && selectedPrompt?.source === 'agent'"
                type="button"
                @click="requestPublish"
                class="flex items-center px-3.5 py-1.5 bg-primary text-white text-xs font-bold rounded-lg hover:bg-primary-dark shadow-sm"
              >
                <RocketLaunchIcon class="w-3.5 h-3.5 mr-1" />
                发布版本
              </button>
            </div>
          </div>

          <!-- Body: editor + optional playground -->
          <div class="flex-1 flex min-h-0 relative overflow-hidden">
            <!-- History drawer -->
            <transition name="slide">
              <div
                v-if="showHistory"
                class="absolute inset-y-0 right-0 w-full max-w-xl z-30 bg-white border-l border-gray-200 shadow-2xl flex flex-col"
              >
                <div class="px-4 py-3 border-b border-gray-100 flex justify-between items-center bg-gray-50 shrink-0">
                  <span class="text-sm font-bold text-gray-900">变更历史</span>
                  <button type="button" @click="showHistory = false" class="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
                  </button>
                </div>
                <div class="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar">
                  <div v-if="loadingHistory" class="text-center py-10 text-xs text-gray-400">加载中...</div>
                  <div v-else-if="historyList.length === 0" class="text-center py-10 text-xs text-gray-400">暂无审计记录</div>
                  <div
                    v-for="log in historyList"
                    :key="log.id"
                    class="border border-gray-100 rounded-xl p-3 hover:bg-gray-50"
                  >
                    <div class="flex justify-between items-start mb-2 gap-2">
                      <div class="flex items-center gap-2 flex-wrap">
                        <span
                          class="px-2 py-0.5 rounded text-[10px] font-bold"
                          :class="log.change_type === 'CREATE' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'"
                        >{{ log.change_type }}</span>
                        <span class="text-xs font-bold text-gray-800">{{ log.changed_by }}</span>
                      </div>
                      <span class="text-[10px] text-gray-400 font-mono shrink-0">{{ log.created_at }}</span>
                    </div>
                    <div v-if="log.description" class="mb-2 text-xs text-blue-800 bg-blue-50 border border-blue-100 rounded-lg p-2">
                      {{ log.description }}
                    </div>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      <div v-if="log.old_value">
                        <div class="flex justify-between items-center mb-1">
                          <span class="text-[10px] text-gray-400">变更前</span>
                          <div class="flex gap-1">
                            <button type="button" @click="copyToClipboard(log.old_value)" class="p-1 text-gray-400 hover:text-primary"><ClipboardDocumentIcon class="w-3.5 h-3.5" /></button>
                            <button type="button" @click="restoreContent(log.old_value)" class="p-1 text-gray-400 hover:text-green-600"><ArrowUturnLeftIcon class="w-3.5 h-3.5" /></button>
                          </div>
                        </div>
                        <pre class="p-2 bg-gray-50 rounded text-[10px] text-gray-400 whitespace-pre-wrap max-h-40 overflow-y-auto" v-html="computeDiffHtml(log.old_value, log.new_value, 'old')"></pre>
                      </div>
                      <div>
                        <div class="flex justify-between items-center mb-1">
                          <span class="text-[10px] text-gray-400">变更后</span>
                          <div class="flex gap-1">
                            <button type="button" @click="copyToClipboard(log.new_value)" class="p-1 text-gray-400 hover:text-primary"><ClipboardDocumentIcon class="w-3.5 h-3.5" /></button>
                            <button type="button" @click="restoreContent(log.new_value)" class="p-1 text-gray-400 hover:text-green-600"><ArrowUturnLeftIcon class="w-3.5 h-3.5" /></button>
                          </div>
                        </div>
                        <pre class="p-2 bg-gray-50 rounded text-[10px] text-gray-700 whitespace-pre-wrap max-h-40 overflow-y-auto" v-html="computeDiffHtml(log.old_value, log.new_value, 'new')"></pre>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </transition>

            <!-- Editor pane -->
            <div
              class="flex flex-col min-w-0 min-h-0 border-r border-gray-100"
              :class="showPlayground ? 'flex-[1.2]' : 'flex-1'"
            >
              <div class="px-3 py-2 bg-gray-50 border-b border-gray-100 flex justify-between items-center gap-2 shrink-0">
                <div class="flex items-center gap-1">
                  <div class="relative">
                    <button
                      v-if="canEdit"
                      type="button"
                      @click="showSnippets = !showSnippets"
                      class="p-1.5 text-gray-500 hover:text-gray-800 hover:bg-white rounded-md"
                      title="插入常用片段"
                    >
                      <QueueListIcon class="w-3.5 h-3.5" />
                    </button>
                    <div
                      v-if="showSnippets"
                      class="absolute top-full left-0 mt-1 w-52 bg-white rounded-lg shadow-xl border border-gray-100 z-50 overflow-hidden"
                    >
                      <button
                        v-for="(snip, idx) in snippets"
                        :key="idx"
                        type="button"
                        @click="insertSnippet(snip.content)"
                        class="w-full text-left px-3 py-2 text-xs text-gray-700 hover:bg-primary/5 hover:text-primary border-b border-gray-50 last:border-0"
                      >
                        {{ snip.label }}
                      </button>
                    </div>
                  </div>
                  <div class="flex p-0.5 rounded-lg bg-gray-200/50">
                    <button
                      type="button"
                      @click="viewMode = 'edit'"
                      class="flex items-center px-2.5 py-1 text-[11px] font-semibold rounded-md"
                      :class="viewMode === 'edit' ? 'bg-white text-primary shadow-sm' : 'text-gray-500'"
                    >
                      <PencilSquareIcon class="w-3.5 h-3.5 mr-1" />编辑
                    </button>
                    <button
                      type="button"
                      @click="viewMode = 'preview'"
                      class="flex items-center px-2.5 py-1 text-[11px] font-semibold rounded-md"
                      :class="viewMode === 'preview' ? 'bg-white text-primary shadow-sm' : 'text-gray-500'"
                    >
                      <EyeIcon class="w-3.5 h-3.5 mr-1" />预览
                    </button>
                  </div>
                  <button
                    v-if="canEdit && viewMode === 'edit'"
                    v-has-perm="'element:prompts:optimize'"
                    type="button"
                    @click="showOptimizeConfirm = true"
                    :disabled="optimizing"
                    class="ml-1 flex items-center px-2.5 py-1 text-[11px] font-semibold rounded-md text-indigo-600 hover:bg-indigo-50 disabled:opacity-50"
                  >
                    <SparklesIcon class="w-3.5 h-3.5 mr-1" :class="{ 'animate-spin': optimizing }" />
                    AI 润色
                  </button>
                </div>
                <button
                  type="button"
                  @click="showPlayground = !showPlayground"
                  class="flex items-center px-2.5 py-1 text-[11px] font-semibold rounded-md border"
                  :class="showPlayground ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-white text-gray-600 border-gray-200'"
                >
                  <BeakerIcon class="w-3.5 h-3.5 mr-1" />
                  {{ showPlayground ? '关闭测试' : '打开测试' }}
                </button>
              </div>

              <div class="flex-1 flex flex-col min-h-0 bg-white overflow-hidden">
                <div v-if="viewMode === 'edit'" class="flex-1 flex overflow-hidden min-h-0">
                  <div
                    ref="lineNumbersRef"
                    class="w-10 bg-gray-50 text-gray-400 text-right pr-2 py-4 select-none font-mono text-[11px] leading-6 border-r border-gray-100 overflow-hidden shrink-0"
                  >
                    <div v-for="n in lineCount" :key="n" class="h-6">{{ n }}</div>
                  </div>
                  <textarea
                    ref="editorTextareaRef"
                    v-model="currentDetail.content"
                    :readonly="!canEdit"
                    @scroll="syncLineNumberScroll"
                    class="flex-1 px-4 py-4 font-mono text-[13px] leading-6 text-gray-800 focus:outline-none resize-none bg-white overflow-y-auto custom-scrollbar"
                    :class="{ 'cursor-not-allowed opacity-80': !canEdit }"
                    placeholder="在此输入提示词内容，支持 {variable} 占位符..."
                    spellcheck="false"
                  ></textarea>
                </div>
                <div v-else class="flex-1 p-6 overflow-y-auto custom-scrollbar bg-white">
                  <div
                    class="markdown-body prose prose-sm max-w-none"
                    v-html="renderMarkdown(currentDetail.content)"
                  ></div>
                </div>
              </div>

              <!-- Status + note -->
              <div class="border-t border-gray-100 bg-gray-50 shrink-0">
                <div class="px-4 py-1.5 flex items-center justify-between text-[10px] font-mono text-gray-400">
                  <div class="flex items-center gap-2">
                    <span>{{ lineCount }} 行</span>
                    <span>·</span>
                    <span>{{ charCount }} 字符</span>
                    <span>·</span>
                    <span class="inline-flex items-center"><CalculatorIcon class="w-3 h-3 mr-0.5" />≈ {{ tokenCount }} token</span>
                    <span v-if="isDirty" class="text-amber-600">· 已修改</span>
                  </div>
                  <span>支持 {variable} 占位符</span>
                </div>
                <div v-if="canEdit" class="px-4 pb-3">
                  <label class="block text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">变更备注</label>
                  <textarea
                    v-model="versionNote"
                    rows="2"
                    placeholder="简要描述此次变更..."
                    class="w-full text-xs border border-gray-200 rounded-lg focus:border-primary focus:ring-1 focus:ring-primary/20 resize-none p-2.5 bg-white outline-none"
                  ></textarea>
                </div>
              </div>
            </div>

            <!-- Playground split pane -->
            <div
              v-if="showPlayground"
              class="w-full max-w-[420px] flex flex-col min-h-0 bg-slate-50/50 shrink-0"
            >
              <div class="px-4 py-3 border-b border-gray-100 bg-white flex items-center justify-between shrink-0">
                <div class="flex items-center gap-2">
                  <BeakerIcon class="w-4 h-4 text-primary" />
                  <span class="text-sm font-bold text-gray-800">测试沙盒</span>
                </div>
                <div class="flex items-center gap-1.5">
                  <button type="button" @click="saveTestCase" class="p-1.5 text-gray-400 hover:text-primary rounded-lg hover:bg-gray-50" title="保存测试用例">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"/></svg>
                  </button>
                  <button
                    type="button"
                    @click="runTest"
                    :disabled="testing"
                    class="flex items-center px-3 py-1.5 bg-primary text-white text-xs font-bold rounded-lg hover:bg-primary-dark disabled:opacity-50"
                  >
                    <PlayIcon v-if="!testing" class="w-3.5 h-3.5 mr-1" />
                    <ArrowPathIcon v-else class="w-3.5 h-3.5 mr-1 animate-spin" />
                    运行
                  </button>
                  <button type="button" @click="showPlayground = false" class="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
                  </button>
                </div>
              </div>

              <div class="px-4 py-2 border-b border-gray-100 bg-white shrink-0">
                <div class="flex items-center bg-gray-50 border border-gray-200 rounded-lg px-2.5 py-1.5">
                  <CpuChipIcon class="w-3.5 h-3.5 text-gray-400 mr-1.5" />
                  <select v-model="selectedModel" class="flex-1 text-xs font-medium text-gray-700 bg-transparent border-none focus:ring-0 p-0 cursor-pointer">
                    <option value="" disabled>选择模型...</option>
                    <option v-for="m in models" :key="m.id" :value="m.model_id">{{ m.name }}</option>
                  </select>
                </div>
              </div>

              <div class="flex-1 overflow-y-auto p-4 space-y-5 custom-scrollbar">
                <div>
                  <label class="text-[11px] font-bold text-gray-500 mb-1.5 block">用户模拟输入</label>
                  <textarea
                    v-model="userInput"
                    rows="3"
                    class="w-full text-sm border border-gray-200 rounded-xl bg-white focus:border-primary focus:ring-1 focus:ring-primary/20 p-3 outline-none"
                    placeholder="模拟用户发给智能体的请求..."
                  ></textarea>
                </div>

                <div>
                  <div class="flex items-center justify-between mb-1.5">
                    <label class="text-[11px] font-bold text-gray-500">变量</label>
                    <span v-if="Object.keys(testVariables).length" class="text-[10px] text-gray-400">{{ Object.keys(testVariables).length }} 个</span>
                  </div>
                  <div v-if="Object.keys(testVariables).length === 0" class="text-xs text-gray-400 border border-dashed border-gray-200 rounded-xl py-6 text-center">
                    未检测到 {变量} 占位符
                  </div>
                  <div v-else class="space-y-2">
                    <div v-for="(_, key) in testVariables" :key="key" class="bg-white border border-gray-200 rounded-xl p-3">
                      <div class="text-[11px] font-bold text-gray-600 mb-1 font-mono">{{ key }}</div>
                      <textarea
                        v-model="testVariables[key]"
                        rows="2"
                        class="w-full text-xs border-0 bg-gray-50 rounded-lg p-2 font-mono outline-none focus:ring-1 focus:ring-primary/20"
                        :placeholder="'输入 ' + key"
                      ></textarea>
                    </div>
                  </div>
                </div>

                <div>
                  <div class="flex items-center justify-between mb-1.5">
                    <label class="text-[11px] font-bold text-gray-500">推理输出</label>
                    <span v-if="testResult" class="text-[10px] font-bold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">
                      {{ Math.round(testResult.latency_ms) }}ms
                    </span>
                  </div>
                  <div v-if="!testResult && !testing" class="bg-white border border-gray-200 rounded-xl py-10 text-center text-xs text-gray-400">
                    配置后点击「运行」开始测试
                  </div>
                  <div v-else-if="testing" class="bg-white border border-gray-200 rounded-xl py-10 text-center text-xs text-gray-400">
                    推理中...
                  </div>
                  <div v-else-if="testResult" class="space-y-3">
                    <div class="bg-white border border-gray-200 rounded-xl p-3">
                      <pre class="text-xs text-gray-800 whitespace-pre-wrap font-sans leading-relaxed">{{ streamedOutput }}</pre>
                      <div class="mt-2 flex justify-end">
                        <button type="button" @click="copyToClipboard(testResult.raw_output || streamedOutput)" class="text-[10px] text-gray-400 hover:text-primary flex items-center">
                          <ClipboardDocumentIcon class="w-3.5 h-3.5 mr-1" />复制
                        </button>
                      </div>
                    </div>
                    <details class="bg-white border border-gray-200 rounded-xl overflow-hidden">
                      <summary class="px-3 py-2 text-[10px] font-bold text-gray-400 cursor-pointer hover:bg-gray-50">查看完整 Prompt</summary>
                      <pre class="px-3 pb-3 text-[10px] text-gray-600 font-mono whitespace-pre-wrap max-h-48 overflow-y-auto">{{ testResult.interpolated_prompt }}</pre>
                    </details>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div
          v-else
          class="flex-1 flex flex-col items-center justify-center bg-white rounded-xl shadow-sm border border-gray-200 text-gray-400"
        >
          <DocumentTextIcon class="w-12 h-12 mb-3 opacity-25" />
          <h2 class="text-base font-medium text-gray-600">从左侧选择一个提示词</h2>
          <p class="mt-1 text-xs text-gray-400">可管理编辑系统配置或智能体角色设定</p>
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
      title="确认保存"
      :message="
        selectedPrompt?.source === 'agent'
          ? '确定要为该智能体发布新版本吗？'
          : '确定要更新此系统级提示词配置吗？此操作将立即生效。'
      "
      type="primary"
      confirm-text="确认保存"
      @confirm="performSave"
      @cancel="showConfirm = false"
    />
    <ConfirmModal
      v-if="showUnsavedConfirm"
      title="未保存的修改"
      message="当前提示词有未保存的修改，切换后将丢失这些修改。确定要继续吗？"
      type="warning"
      confirm-text="放弃修改"
      cancel-text="继续编辑"
      @confirm="confirmDiscardAndSwitch"
      @cancel="cancelPromptSwitch"
    />
    <!-- AI Optimize Confirm Modal -->
    <ConfirmModal
      v-if="showOptimizeConfirm"
      title="AI 提示词润色"
      message="AI 将针对当前内容生成多个侧重点不同的优化方案，大约需要几秒钟。是否开始？"
      confirmText="开始润色"
      cancelText="取消"
      @confirm="runOptimize"
      @cancel="showOptimizeConfirm = false"
    />

    <!-- Publish Confirm Modal -->
    <ConfirmModal
      v-if="showPublishConfirm"
      title="确认发布"
      message="确定要保存并发布为线上版本吗？此操作将立即覆盖线上提示词。"
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
                AI 优化建议
              </h3>
              <p class="text-[10px] text-gray-400 font-medium">
                共生成了多个侧重点不同的提示词方案
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
                    应用此方案
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
  transition: transform 0.25s ease, opacity 0.25s ease;
}
.slide-enter-from,
.slide-leave-to {
  transform: translateX(24px);
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
