<template>
  <teleport to="body">
    <div v-if="modelValue" class="fixed inset-0 z-[245] flex items-center justify-center p-4 sm:p-6">
      <div class="absolute inset-0 bg-black/45 backdrop-blur-[2px]" @click="modelValue = false" />
      <div class="relative w-full max-w-3xl h-[min(92vh,880px)] flex flex-col rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-950 shadow-2xl overflow-hidden">
        <div class="shrink-0 px-5 py-4 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between gap-3 bg-gray-50/80 dark:bg-gray-900/80">
          <div class="min-w-0">
            <h3 class="text-sm font-black text-gray-800 dark:text-gray-100">浏览黄金报表</h3>
            <p class="text-[11px] text-gray-400 mt-0.5">
              共 {{ reports.length }} 个 · 当前显示 {{ displayedReports.length }} 个
            </p>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              class="p-2 rounded-lg text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-950/40 transition-colors"
              title="刷新列表"
              @click="fetchReports()"
            >
              <svg class="w-4 h-4" :class="{ 'animate-spin': loading || refreshing }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
            <button
              type="button"
              class="p-2 rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              title="关闭"
              @click="modelValue = false"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <div class="shrink-0 px-5 py-3 space-y-2.5 border-b border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-950">
          <div class="relative">
            <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z" />
            </svg>
            <input
              v-model="searchQuery"
              type="search"
              placeholder="搜索报表名称、描述、标签或原始提问..."
              class="w-full pl-9 pr-8 py-2 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/60 text-xs text-gray-800 dark:text-gray-200 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400"
            />
            <button
              v-if="searchQuery"
              type="button"
              class="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
              title="清除搜索"
              @click="searchQuery = ''"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div class="grid grid-cols-3 p-0.5 rounded-lg bg-gray-100/80 dark:bg-gray-800/40 text-gray-500 dark:text-gray-400 text-[10px] font-bold border border-gray-200/30 dark:border-gray-800/30">
            <button
              v-for="scope in scopes"
              :key="scope.value"
              type="button"
              class="py-1 rounded-md transition-colors text-center min-h-[1.75rem]"
              :class="browserScope === scope.value ? 'bg-white dark:bg-gray-700 text-blue-600 dark:text-white shadow-sm font-black' : 'hover:text-gray-800 dark:hover:text-gray-200'"
              @click="setBrowserScope(scope.value)"
            >
              {{ scope.label }}
            </button>
          </div>
          <div class="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-0.5 scroll-smooth min-h-[1.625rem]">
            <button
              v-for="filter in smartFilters"
              :key="filter.value"
              type="button"
              class="flex-shrink-0 px-2.5 py-0.5 rounded-full text-[10px] transition-colors border font-bold"
              :class="browserSmartFilter === filter.value
                ? 'bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-300 border-blue-200 dark:border-blue-900/40'
                : 'bg-gray-50 dark:bg-gray-900 text-gray-500 border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'"
              @click="setBrowserSmartFilter(filter.value)"
            >
              <span class="flex items-center gap-1">
                <span v-if="filter.value === 'pinned'">📌</span>
                <span v-else-if="filter.value === 'favorite'">⭐️</span>
                <span v-else-if="filter.value === 'subscribed'">🔔</span>
                <span v-else-if="filter.value === 'recent'">🕒</span>
                <span v-else-if="filter.value === 'frequent'">🔥</span>
                <span>{{ filter.label }}</span>
              </span>
            </button>
            <span v-if="allTags.length > 0" class="flex-shrink-0 w-px h-3.5 bg-gray-200 dark:bg-gray-800 mx-0.5" />
            <button
              v-if="allTags.length > 0"
              type="button"
              class="flex-shrink-0 px-2.5 py-0.5 rounded-full text-[10px] transition-colors border font-bold"
              :class="browserSelectedTag === ''
                ? 'bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-300 border-blue-200 dark:border-blue-900/40'
                : 'bg-gray-50 dark:bg-gray-900 text-gray-500 border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'"
              @click="browserSelectedTag = ''"
            >
              🏷️ 全部标签
            </button>
            <button
              v-for="tag in allTags"
              :key="tag"
              type="button"
              class="flex-shrink-0 px-2.5 py-0.5 rounded-full text-[10px] transition-colors border font-bold"
              :class="browserSelectedTag === tag
                ? 'bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-300 border-blue-200 dark:border-blue-900/40'
                : 'bg-gray-50 dark:bg-gray-900 text-gray-500 border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'"
              @click="browserSelectedTag = tag"
            >
              # {{ tag }}
            </button>
          </div>
        </div>

        <div class="flex-1 min-h-0 overflow-y-auto px-5 py-4 custom-scrollbar relative">
          <div
            v-if="refreshing"
            class="absolute inset-0 z-10 bg-white/55 dark:bg-gray-950/55 backdrop-blur-[1px] flex items-center justify-center pointer-events-none"
          >
            <span class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 text-[11px] text-gray-500 shadow-sm">
              <svg class="w-3.5 h-3.5 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              切换中...
            </span>
          </div>

          <div v-if="loading && !reports.length" class="flex items-center justify-center py-12 text-xs text-gray-400">
            <svg class="w-4 h-4 animate-spin text-blue-500 mr-2" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            正在加载报表...
          </div>
          <div v-else-if="displayedReports.length === 0" class="text-center py-12 border border-dashed border-gray-200 dark:border-gray-800 rounded-xl text-gray-400 text-xs">
            {{ searchQuery.trim() ? '没有匹配的报表，试试换个关键词' : '暂无符合条件的报表' }}
          </div>
          <div v-else class="grid gap-2 sm:grid-cols-2">
            <SavedReportItemCard
              v-for="report in displayedReports"
              :key="report.id"
              :report="report"
              :format-date="formatDate"
              @execute="emit('execute', $event)"
              @edit="emit('edit', $event)"
              @detail="emit('detail', $event)"
              @favorite="emit('favorite', $event)"
              @pin="emit('pin', $event)"
              @share="emit('share', $event)"
              @copy="emit('copy', $event)"
              @delete="emit('delete', $event)"
              @subscription="emit('subscription', $event)"
            />
          </div>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import axios from "@/utils/axios";
import SavedReportItemCard from "@/components/chatbi/SavedReportItemCard.vue";

type ReportScope = "all" | "my" | "shared";
type SmartFilter = "all" | "pinned" | "favorite" | "subscribed" | "recent" | "frequent";

defineProps<{
  formatDate: (iso?: string | null) => string;
}>();

const modelValue = defineModel<boolean>({ default: false });

const scopes = [
  { value: "all" as const, label: "全部" },
  { value: "my" as const, label: "我的" },
  { value: "shared" as const, label: "共享给我" },
];
const smartFilters = [
  { value: "all" as const, label: "全部报表" },
  { value: "pinned" as const, label: "置顶" },
  { value: "favorite" as const, label: "收藏" },
  { value: "subscribed" as const, label: "已订阅" },
  { value: "recent" as const, label: "最近运行" },
  { value: "frequent" as const, label: "常用" },
];

const reports = ref<any[]>([]);
const loading = ref(false);
const refreshing = ref(false);
const browserScope = ref<ReportScope>("all");
const browserSmartFilter = ref<SmartFilter>("all");
const browserSelectedTag = ref("");
const searchQuery = ref("");

const allTags = computed(() => {
  const tags = new Set<string>();
  for (const report of reports.value) {
    for (const tag of report.tags || []) {
      const cleaned = String(tag || "").trim();
      if (cleaned) tags.add(cleaned);
    }
  }
  return Array.from(tags);
});

const filteredReports = computed(() => {
  let list = reports.value;
  if (browserSelectedTag.value) {
    list = list.filter((report) => (report.tags || []).includes(browserSelectedTag.value));
  }
  if (browserSmartFilter.value === "pinned") {
    list = list.filter((report) => !!report.pinned_at);
  } else if (browserSmartFilter.value === "favorite") {
    list = list.filter((report) => !!report.is_favorite);
  } else if (browserSmartFilter.value === "subscribed") {
    list = list.filter((report) => !!report.subscription_status);
  } else if (browserSmartFilter.value === "recent") {
    list = list
      .filter((report) => !!(report.user_last_run_at || report.last_success_at))
      .slice()
      .sort((a, b) => String(b.user_last_run_at || b.last_success_at).localeCompare(String(a.user_last_run_at || a.last_success_at)));
  } else if (browserSmartFilter.value === "frequent") {
    list = list
      .filter((report) => Number(report.user_run_count || 0) > 0)
      .slice()
      .sort((a, b) => Number(b.user_run_count || 0) - Number(a.user_run_count || 0));
  }
  return list;
});

const displayedReports = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase();
  if (!keyword) return filteredReports.value;
  return filteredReports.value.filter((report) => {
    const haystack = [
      report.title,
      report.description,
      report.original_query,
      report.owner_name,
      ...(report.tags || []),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(keyword);
  });
});

const fetchReports = async () => {
  const hasExisting = reports.value.length > 0;
  if (hasExisting) refreshing.value = true;
  else loading.value = true;
  try {
    const res = await axios.get("/api/portal/saved-reports", {
      params: { scope: browserScope.value },
    });
    if (res.data?.data) {
      reports.value = res.data.data;
    }
  } catch (error) {
    console.error("Failed to fetch saved reports in browser modal:", error);
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
};

const setBrowserScope = async (scope: ReportScope) => {
  if (browserScope.value === scope) return;
  browserScope.value = scope;
  if (scope === "shared" && browserSmartFilter.value === "subscribed") browserSmartFilter.value = "all";
  browserSelectedTag.value = "";
  await fetchReports();
};

const setBrowserSmartFilter = async (filter: SmartFilter) => {
  browserSmartFilter.value = filter;
  browserSelectedTag.value = "";
  if (filter === "subscribed" && browserScope.value !== "my") {
    browserScope.value = "my";
    await fetchReports();
  }
};

const refresh = () => fetchReports();

watch(modelValue, (open) => {
  if (open) {
    void fetchReports();
    return;
  }
  searchQuery.value = "";
});

watch(browserSmartFilter, () => {
  browserSelectedTag.value = "";
});

defineExpose({ refresh });

const emit = defineEmits<{
  (event: "execute", report: any): void;
  (event: "edit", report: any): void;
  (event: "detail", report: any): void;
  (event: "favorite", report: any): void;
  (event: "pin", report: any): void;
  (event: "share", report: any): void;
  (event: "copy", report: any): void;
  (event: "delete", report: any): void;
  (event: "subscription", report: any): void;
}>();
</script>
