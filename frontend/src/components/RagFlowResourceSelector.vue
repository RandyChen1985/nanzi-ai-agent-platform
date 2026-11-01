<script setup lang="ts">
import { computed, ref, onMounted, watch } from "vue";
import {
  ragflowApi,
  type RagFlowAgent,
  type RagFlowConfigSummary,
  type RagFlowDataset,
} from "@/api/ragflow";
import Modal from "@/components/Modal.vue";
import {
  countByScope,
  docCountOf,
  filterAndSortDatasets,
  hasAnyFilter,
  nameOf,
  type KbDocsSize,
  type KbFilters,
  type KbScope,
  type KbSortBy,
  type KbUpdatedWithin,
  type KbWriteAccess,
} from "@/utils/kbDatasetFilter";

const props = defineProps<{
  modelValue: boolean; // v-model for visibility
  type: "agent" | "dataset"; // Select Agent (Single) or Dataset (Multiple)
  initialSelected?: string | string[]; // For pre-selecting
  overrideUrl?: string;
  overrideKey?: string;
  includeMissing?: boolean;
  /**
   * 隐藏「个人私有」知识库。仅用于"配了要给所有人用"的场景（智能体绑定、系统默认库）：
   * 智能体绑定的库在检索时会跳过使用者权限校验（见 knowledge_tool 的 agent_granted_ids），
   * 所以把私有库绑上去 = 把个人资料扩散给所有使用者，不该给这个机会。
   * 知识检索测试页不传此参数，否则用户没法测自己的私有库。
   */
  excludePrivate?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", val: boolean): void;
  (e: "select", val: string | string[]): void;
  (e: "select-details", val: DatasetSelectionDetail[]): void;
}>();

const loading = ref(false);
const agents = ref<RagFlowAgent[]>([]);
const datasets = ref<RagFlowDataset[]>([]);
const errorMsg = ref("");
const engineStatus = ref<"checking" | "connected" | "disconnected">("checking");
const ragflowConfig = ref<RagFlowConfigSummary | null>(null);

const selectedIds = ref<string[]>([]); // Always array for internal logic

/** 可见范围归一：未知/空值按 public 兜底，与后端判定一致 */
const visibilityKey = (ds: RagFlowDataset): "public" | "team" | "private" => {
  const token = String(ds.visibility || "").trim().toLowerCase();
  return token === "team" || token === "private" ? token : "public";
};

const SCOPE_BADGE: Record<
  "public" | "team" | "private",
  { label: string; cls: string }
> = {
  public: { label: "授权可见", cls: "bg-blue-50 text-blue-600 border-blue-100" },
  team: { label: "TEAM 级", cls: "bg-violet-50 text-violet-600 border-violet-100" },
  private: { label: "个人私有", cls: "bg-amber-50 text-amber-600 border-amber-100" },
};

const scopeBadge = (ds: RagFlowDataset) => SCOPE_BADGE[visibilityKey(ds)];

/** 列表渲染用的数据集（按需剔除私有库） */
const visibleDatasets = computed(() =>
  props.excludePrivate
    ? datasets.value.filter((ds) => visibilityKey(ds) !== "private")
    : datasets.value
);

/** 被隐藏的私有库数量：用来在底部解释"我的库怎么不见了" */
const hiddenPrivateCount = computed(() =>
  props.excludePrivate
    ? datasets.value.filter((ds) => visibilityKey(ds) === "private").length
    : 0
);

/**
 * 已绑定、但因 excludePrivate 不在可选列表里的私有库。
 *
 * 这些必须显式渲染出来：只给一句文字提示的话，用户看得到风险却取消不掉，
 * 于是"配置里有、界面里没有"的绑定会一直留着。渲染成单独一段，只能解绑、
 * 不能新增——把「能选什么」限制在设计意图内，同时让提交内容与界面完全一致。
 */
const pinnedPrivateDatasets = computed(() =>
  props.excludePrivate
    ? datasets.value.filter(
        (ds) =>
          selectedIds.value.includes(ds.id) && visibilityKey(ds) === "private"
      )
    : []
);

/** 已选中但仍留在配置里的私有库：不能静默丢弃，否则保存后配置悄悄消失 */
const selectedPrivateNames = computed(() =>
  datasets.value
    .filter(
      (ds) =>
        selectedIds.value.includes(ds.id) && visibilityKey(ds) === "private"
    )
    .map((ds) => ds.platform_name || ds.name)
);

type DatasetSelectionDetail = { id: string; name: string };

const ragflowApiUrl = computed(() => props.overrideUrl || ragflowConfig.value?.api_url || "未配置");
const engineStatusText = computed(() => {
  if (engineStatus.value === "checking") return "知识库引擎检测中 ...";
  if (engineStatus.value === "connected") return "知识库引擎已连接";
  return "知识库引擎未连接";
});

/** API Key 缺失时连接照样是「已连接」，但检索会失败，必须继续用横幅提醒 */
const apiKeyMissing = computed(() =>
  Boolean(ragflowConfig.value && !ragflowConfig.value.api_key_configured)
);

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
      // page_size 必须 > 100：客户端只在超过 100 时才自动翻页聚合，
      // 传 100 会静默截断——库多了以后排在后面的库"凭空消失"，选都选不到
      const res = await ragflowApi.listDatasets(1, 1000, props.overrideUrl, props.overrideKey, props.includeMissing);
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
    const selectedDetails = selectedIds.value.map((id) => {
      const dataset = datasets.value.find((item) => item.id === id);
      return { id, name: dataset?.platform_name || dataset?.name || id };
    });
    emit("select-details", selectedDetails);
  }
  close();
};

const close = () => emit("update:modelValue", false);

const formatDate = (ts?: number | string) => {
  if (!ts) return "-";
  // RAGFlow usually uses ms
  return new Date(ts).toLocaleDateString();
};

/* ------------------------------------------------------------------
 * 搜索与筛选
 *
 * 知识库到百级之后，靠滚动找库不现实。这里全部在前端过滤：接口本来就
 * 一次返回全量，再加一个后端搜索接口反而要处理分页与已选状态的同步。
 * 过滤规则本身在 utils/kbDatasetFilter.ts，便于单独跑测试。
 * 只在 type === "dataset" 时渲染这些控件（智能体列表短，不需要）。
 * ------------------------------------------------------------------ */

const keyword = ref("");
const scopeFilter = ref<KbScope[]>([]);
const updatedWithin = ref<KbUpdatedWithin | null>(null);
const docsSize = ref<KbDocsSize | null>(null);
const writeAccess = ref<KbWriteAccess | null>(null);
const sortBy = ref<KbSortBy>("updated");
const moreFiltersOpen = ref(false);

/** 每次求值都取一次当前时间，避免长开页面里「近 7 天」逐渐失真 */
const currentFilters = computed<KbFilters>(() => ({
  keyword: keyword.value,
  scopes: scopeFilter.value,
  updatedWithin: updatedWithin.value,
  docsSize: docsSize.value,
  writeAccess: writeAccess.value,
}));

/** 过滤 + 排序后的列表 */
const filteredDatasets = computed(() =>
  filterAndSortDatasets(visibleDatasets.value, currentFilters.value, sortBy.value),
);

const scopeCounts = computed(() => countByScope(visibleDatasets.value, currentFilters.value));

/** 已选区展示：按选中顺序取回实体，找不到的（已删除）跳过 */
const selectedDatasets = computed(() =>
  selectedIds.value
    .map((id) => datasets.value.find((ds) => ds.id === id))
    .filter((ds): ds is RagFlowDataset => Boolean(ds))
);

/** 可见范围选项：隐藏私有库时不给「个人私有」这一项，选了也筛不出东西 */
const scopeOptions = computed(
  () =>
    (props.excludePrivate ? ["public", "team"] : ["public", "team", "private"]) as KbScope[],
);

const hasActiveFilters = computed(() => hasAnyFilter(currentFilters.value));

/** 高级条件里已生效的条数，用于在「更多筛选」按钮上提示 */
const advancedFilterCount = computed(
  () =>
    Number(updatedWithin.value !== null) +
    Number(docsSize.value !== null) +
    Number(writeAccess.value !== null),
);

const toggleScope = (scope: "public" | "team" | "private") => {
  scopeFilter.value = scopeFilter.value.includes(scope)
    ? scopeFilter.value.filter((item) => item !== scope)
    : [...scopeFilter.value, scope];
};

/** 组内单选：再点一次取消 */
const toggleUpdatedWithin = (value: "7" | "30" | "older") => {
  updatedWithin.value = updatedWithin.value === value ? null : value;
};
const toggleDocsSize = (value: "empty" | "small" | "large") => {
  docsSize.value = docsSize.value === value ? null : value;
};
const toggleWriteAccess = (value: "w" | "ro") => {
  writeAccess.value = writeAccess.value === value ? null : value;
};

const resetFilters = () => {
  keyword.value = "";
  scopeFilter.value = [];
  updatedWithin.value = null;
  docsSize.value = null;
  writeAccess.value = null;
};

/** 全选只作用于当前筛选结果：不做全库全选，避免误选上百个 */
const selectAllFiltered = () => {
  filteredDatasets.value.forEach((ds) => {
    if (!selectedIds.value.includes(ds.id)) selectedIds.value.push(ds.id);
  });
};

const clearSelected = () => {
  selectedIds.value = [];
};

const removeSelected = (id: string) => {
  selectedIds.value = selectedIds.value.filter((item) => item !== id);
};
</script>

<template>
  <Modal
    :show="modelValue"
    :title="type === 'agent' ? '选择 RAGFlow 智能体' : '选择知识库 (Datasets)'"
    :z-index="10050"
    @close="close"
    size="max-w-3xl"
  >
    <div class="flex flex-col max-h-[calc(85vh-10rem)] min-h-[320px]">
      <!--
        引擎状态：连接正常时压成一行小字（约 14px 高），地址收进 tooltip。
        原来是一整条 60px 横幅，每次打开都白占一截列表高度——只有检查中、
        未连接、API Key 缺失这类需要用户处理的情况才值得展开。
      -->
      <div
        v-if="engineStatus === 'connected' && !apiKeyMissing"
        class="mb-1.5 flex flex-shrink-0 items-center gap-1 text-[10.5px] leading-none"
      >
        <span class="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"></span>
        <span
          class="cursor-help whitespace-nowrap text-gray-400 hover:text-gray-600"
          :title="`RAGFlow 地址：${ragflowApiUrl}`"
          >知识库引擎已连接</span
        >
      </div>
      <div
        v-else
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
          <!--
            搜索与筛选：sticky 吸顶，列表滚动时始终可见。
            用 sticky 而不是把筛选区提到滚动容器外面，是为了不动外层 flex 结构。
          -->
          <div class="sticky top-0 z-10 -mx-1 space-y-2 bg-white px-1 pb-2">
            <div class="relative">
              <svg class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.4">
                <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5-5m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <!-- 用 type="search" 走全局统一的原生清空按钮样式，不再自绘 -->
              <input
                v-model="keyword"
                type="search"
                placeholder="搜索知识库名称、描述或创建人…"
                autocomplete="off"
                class="h-9 w-full rounded-lg border border-gray-200 bg-gray-50 py-0 pl-8 pr-8 text-xs text-gray-800 outline-none transition-colors placeholder:text-gray-400 focus:border-primary focus:bg-white focus:ring-2 focus:ring-primary/10"
              />
            </div>

            <div class="flex flex-wrap items-center gap-1.5">
              <span class="mr-0.5 text-[10px] font-bold tracking-wide text-gray-400">可见范围</span>
              <button
                v-for="scope in scopeOptions"
                :key="scope"
                type="button"
                @click="toggleScope(scope)"
                class="flex h-[25px] items-center gap-1 rounded-md border px-2 text-[11px] font-semibold transition-colors"
                :class="
                  scopeFilter.includes(scope)
                    ? 'border-primary/30 bg-primary/5 text-primary'
                    : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50'
                "
              >
                {{ SCOPE_BADGE[scope].label }}
                <span class="text-[9.5px] font-bold opacity-60">{{ scopeCounts[scope] }}</span>
              </button>

              <button
                type="button"
                @click="moreFiltersOpen = !moreFiltersOpen"
                class="flex h-[25px] items-center gap-1 rounded-md border border-dashed border-gray-300 bg-white px-2 text-[11px] font-bold text-gray-500 transition-colors hover:border-primary hover:text-primary"
              >
                更多筛选
                <span v-if="advancedFilterCount" class="rounded-full bg-primary px-1 text-[9px] leading-4 text-white">{{ advancedFilterCount }}</span>
                <svg class="h-2.5 w-2.5 transition-transform" :class="{ 'rotate-180': moreFiltersOpen }" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6" />
                </svg>
              </button>

              <span class="ml-auto"></span>

              <select
                v-model="sortBy"
                aria-label="排序方式"
                class="h-[25px] cursor-pointer appearance-none rounded-md border border-gray-200 bg-white py-0 pl-2 pr-5 text-[11px] font-semibold text-gray-600 outline-none"
                style="background-image: url(&quot;data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af' stroke-width='2.5'><path stroke-linecap='round' stroke-linejoin='round' d='M6 9l6 6 6-6'/></svg>&quot;); background-repeat: no-repeat; background-position: right 4px center; background-size: 11px"
              >
                <option value="updated">最近更新</option>
                <option value="docs">文档最多</option>
                <option value="name">名称 A→Z</option>
              </select>
            </div>

            <div v-if="moreFiltersOpen" class="flex flex-wrap items-center gap-1.5 border-t border-dashed border-gray-200 pt-2">
              <span class="text-[10px] font-bold tracking-wide text-gray-400">更新时间</span>
              <button type="button" @click="toggleUpdatedWithin('7')" :class="updatedWithin === '7' ? 'border-primary/30 bg-primary/5 text-primary' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'" class="h-[25px] rounded-md border px-2 text-[11px] font-semibold transition-colors">近 7 天</button>
              <button type="button" @click="toggleUpdatedWithin('30')" :class="updatedWithin === '30' ? 'border-primary/30 bg-primary/5 text-primary' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'" class="h-[25px] rounded-md border px-2 text-[11px] font-semibold transition-colors">近 30 天</button>
              <button type="button" @click="toggleUpdatedWithin('older')" :class="updatedWithin === 'older' ? 'border-primary/30 bg-primary/5 text-primary' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'" class="h-[25px] rounded-md border px-2 text-[11px] font-semibold transition-colors">30 天以上</button>

              <span class="ml-1.5 text-[10px] font-bold tracking-wide text-gray-400">文档规模</span>
              <button type="button" @click="toggleDocsSize('empty')" :class="docsSize === 'empty' ? 'border-primary/30 bg-primary/5 text-primary' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'" class="h-[25px] rounded-md border px-2 text-[11px] font-semibold transition-colors">空库</button>
              <button type="button" @click="toggleDocsSize('small')" :class="docsSize === 'small' ? 'border-primary/30 bg-primary/5 text-primary' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'" class="h-[25px] rounded-md border px-2 text-[11px] font-semibold transition-colors">1–50</button>
              <button type="button" @click="toggleDocsSize('large')" :class="docsSize === 'large' ? 'border-primary/30 bg-primary/5 text-primary' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'" class="h-[25px] rounded-md border px-2 text-[11px] font-semibold transition-colors">50+</button>

              <span class="ml-1.5 text-[10px] font-bold tracking-wide text-gray-400">权限</span>
              <button type="button" @click="toggleWriteAccess('w')" :class="writeAccess === 'w' ? 'border-primary/30 bg-primary/5 text-primary' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'" class="h-[25px] rounded-md border px-2 text-[11px] font-semibold transition-colors">可写</button>
              <button type="button" @click="toggleWriteAccess('ro')" :class="writeAccess === 'ro' ? 'border-primary/30 bg-primary/5 text-primary' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'" class="h-[25px] rounded-md border px-2 text-[11px] font-semibold transition-colors">只读</button>
            </div>

            <div class="flex items-center gap-2 text-[11px] text-gray-500">
              <span>
                筛选出 <b class="tabular-nums text-gray-800">{{ filteredDatasets.length }}</b>
                / 共 <b class="tabular-nums text-gray-800">{{ visibleDatasets.length }}</b> 个
              </span>
              <span class="ml-auto"></span>
              <button type="button" @click="selectAllFiltered" class="rounded px-1 py-0.5 text-[11px] font-bold text-primary hover:bg-primary/5">全选当前结果</button>
              <button type="button" @click="clearSelected" class="rounded px-1 py-0.5 text-[11px] font-bold text-gray-400 hover:bg-gray-100 hover:text-gray-600">清空已选</button>
            </div>
          </div>

          <!-- 已选区：多选时最容易选错却找不到，固定在列表上方，点 × 直接移除 -->
          <div v-if="selectedDatasets.length" class="flex-shrink-0 rounded-lg border border-emerald-100 bg-emerald-50/60 px-2 py-1.5">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-[10px] font-bold text-emerald-700">已选 {{ selectedDatasets.length }}</span>
              <span
                v-for="ds in selectedDatasets.slice(0, 6)"
                :key="'sel-' + ds.id"
                class="flex max-w-[150px] items-center gap-1 rounded-md border border-emerald-100 bg-white pl-1.5 pr-0.5 text-[10.5px] font-semibold text-emerald-700"
                :title="nameOf(ds)"
              >
                <span class="truncate">{{ nameOf(ds) }}</span>
                <button
                  type="button"
                  :aria-label="`移除 ${nameOf(ds)}`"
                  @click="removeSelected(ds.id)"
                  class="flex h-3.5 w-3.5 items-center justify-center rounded text-emerald-600 opacity-60 hover:bg-emerald-50 hover:opacity-100"
                >
                  <svg class="h-2 w-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3.4">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </span>
              <span v-if="selectedDatasets.length > 6" class="text-[10px] font-bold text-gray-400">
                +{{ selectedDatasets.length - 6 }} 个
              </span>
            </div>
          </div>

          <!-- 已绑定的个人私有库：只可解绑，不可新增 -->
          <div
            v-for="ds in pinnedPrivateDatasets"
            :key="'pinned-' + ds.id"
            class="mb-2 flex items-center p-3 border rounded-lg cursor-pointer border-amber-300 bg-amber-50/60 transition-colors hover:bg-amber-50"
            role="button"
            :aria-label="`解除绑定 ${ds.platform_name || ds.name}`"
            title="已绑定的个人私有库：只有归属人能检索到，点击可解除绑定"
            @click="toggleSelection(ds.id)"
          >
            <div class="mr-3">
              <div class="flex h-5 w-5 items-center justify-center rounded border border-amber-500 bg-amber-500">
                <svg class="h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                </svg>
              </div>
            </div>
            <div class="mr-3 flex h-10 w-10 items-center justify-center rounded border border-amber-200 bg-amber-100 text-amber-700 shadow-sm">
              <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4" />
              </svg>
            </div>
            <div class="flex-1">
              <h4 class="text-sm font-bold text-gray-900">{{ ds.platform_name || ds.name }}</h4>
              <div class="mt-0.5 flex items-center space-x-2">
                <span class="rounded border border-amber-200 bg-white px-1.5 text-[10px] font-semibold text-amber-700">个人私有</span>
                <span class="text-[10px] text-amber-700">已绑定 · 仅归属人可检索 · 点击取消</span>
              </div>
            </div>
            <span class="text-[10px] font-mono text-gray-400">{{ ds.id.slice(0, 8) }}...</span>
          </div>

          <div
            v-for="ds in filteredDatasets"
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
                  class="text-[10px] px-1.5 rounded border font-semibold"
                  :class="scopeBadge(ds).cls"
                  >{{ scopeBadge(ds).label }}</span
                >
                <span
                  class="text-[10px] bg-gray-100 px-1.5 rounded text-gray-500"
                  >{{ docCountOf(ds) }} Docs</span
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
            v-if="filteredDatasets.length === 0"
            class="flex flex-col items-center justify-center py-8 text-center text-gray-400"
          >
            <svg class="mb-2 h-7 w-7 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.6">
              <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5-5m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <p class="text-xs font-semibold text-gray-500">
              {{ hasActiveFilters ? "没有匹配的知识库" : hiddenPrivateCount ? "没有可选的知识库（个人私有库已隐藏，见下方说明）" : "暂无知识库数据" }}
            </p>
            <p v-if="hasActiveFilters" class="mt-0.5 text-[11px] text-gray-400">换个关键词，或放宽筛选条件</p>
            <button
              v-if="hasActiveFilters"
              type="button"
              @click="resetFilters"
              class="mt-2 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-bold text-primary hover:border-primary/30 hover:bg-primary/5"
            >
              清除全部筛选
            </button>
          </div>
        </template>
      </div>

      <!-- 私有库说明：解释被隐藏的库，以及仍留在配置里的私有库 -->
      <p
        v-if="excludePrivate && hiddenPrivateCount > pinnedPrivateDatasets.length"
        class="flex-shrink-0 mt-3 text-[11px] leading-snug text-gray-400"
      >
        已隐藏 {{ hiddenPrivateCount - pinnedPrivateDatasets.length }} 个未绑定的个人私有知识库：
        它们只对归属人可见，其他使用者检索不到，也不能绑定到智能体。
      </p>
      <p
        v-if="excludePrivate && selectedPrivateNames.length"
        class="flex-shrink-0 mt-1 text-[11px] leading-snug text-amber-600"
      >
        上方已列出 {{ selectedPrivateNames.length }} 个已绑定的个人私有知识库（{{
          selectedPrivateNames.join("、")
        }}）：只有归属人能检索到，其他使用者仍会被权限校验剔除；但它会占用检索范围统计，
        建议点击取消后改选授权可见或 TEAM 级。
      </p>

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
