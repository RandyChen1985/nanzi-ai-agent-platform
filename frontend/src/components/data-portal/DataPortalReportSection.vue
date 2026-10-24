<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-2">
        <h2 class="text-base font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
          <span>固化报表</span>
          <span v-if="!compact" class="text-xs font-normal text-gray-400">已沉淀与手工开发的标准化 SQL 报表</span>
        </h2>
        <button
          v-if="!compact"
          type="button"
          class="hidden sm:flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 cursor-pointer"
          title="固化报表使用指南与规范"
          @click="emit('open-specs')"
        >
          <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093M12 17h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </button>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <label v-if="!compact" class="relative">
          <span class="sr-only">搜索固化报表</span>
          <input
            v-model="searchQuery"
            type="search"
            class="w-48 rounded-lg border border-gray-200 bg-white px-3 py-1.5 pr-7 text-xs text-gray-700 outline-none placeholder:text-gray-400 focus:border-blue-400 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
            placeholder="搜索报表名称、标签"
          />
          <span class="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-gray-400">⌕</span>
        </label>
        <div class="flex flex-wrap gap-1.5">
          <button
            v-for="option in visibleFilters"
            :key="option.value"
            type="button"
            class="rounded-lg border px-2.5 py-1 text-xs font-medium transition cursor-pointer"
            :class="activeFilter === option.value ? 'border-blue-200 bg-blue-50 text-blue-700 shadow-2xs dark:border-blue-900/60 dark:bg-blue-950/40 dark:text-blue-300' : 'border-transparent bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-400'"
            @click="setFilter(option.value)"
          >
            {{ option.label }} {{ option.value === 'all' ? reports.length : (summary[option.value] || '') }}
          </button>
        </div>

        <div v-if="!compact && manage" class="flex shrink-0 items-center rounded-lg border border-blue-100 bg-blue-50/70 p-0.5" aria-label="固化报表视图切换">
          <button
            type="button"
            class="rounded-md px-2 py-1 text-xs font-semibold transition-colors"
            :class="reportViewMode === 'card' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-blue-600'"
            :aria-pressed="reportViewMode === 'card'"
            aria-label="切换到卡片视图"
            title="切换到卡片视图"
            @click="setReportViewMode('card')"
          >
            ▦ 卡片
          </button>
          <button
            type="button"
            class="rounded-md px-2 py-1 text-xs font-semibold transition-colors"
            :class="reportViewMode === 'list' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-blue-600'"
            :aria-pressed="reportViewMode === 'list'"
            aria-label="切换到列表视图"
            title="切换到列表视图"
            @click="setReportViewMode('list')"
          >
            ☷ 列表
          </button>
        </div>

        <!-- 非 compact 模式下的新建主操作按钮 -->
        <button
          v-if="!compact"
          type="button"
          class="inline-flex items-center gap-1.5 px-3 py-1 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-lg shadow-sm shadow-blue-500/20 transition-all cursor-pointer"
          @click="emit('create-report')"
        >
          <span class="inline-flex items-center gap-1.5">
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 5v14M5 12h14" />
            </svg>
            <span>新建固化报表</span>
          </span>
        </button>
      </div>
    </div>

    <!-- 快捷视图：最近运行 / 常用报表 / 订阅中；完整列表仍保留在下方。 -->
    <SavedReportQuickViews
      v-if="!compact && reports.length"
      :reports="reports"
      :format-date="formatDate"
      @select="emit('execute', $event)"
    />

    <div v-if="filteredReports.length && manage" :class="reportViewMode === 'list' ? 'space-y-2' : 'grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3'">
      <SavedReportItemCard
        v-for="report in pagedReports"
        :key="report.id"
        :report="report"
        :format-date="formatDate"
        :variant="reportViewMode"
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
    <div v-else-if="filteredReports.length" class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      <button
        v-for="report in pagedReports"
        :key="report.id"
        type="button"
        class="min-h-[116px] rounded-2xl border border-gray-100 bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-md dark:border-gray-800 dark:bg-gray-900 group cursor-pointer"
        @click="emit('open-report', report)"
      >
        <div class="flex items-start justify-between gap-2">
          <strong class="line-clamp-2 text-sm text-gray-900 dark:text-gray-100 group-hover:text-blue-600 transition-colors">
            {{ report.title }}
          </strong>
          <span
            v-if="report.pinned || !!report.pinned_at"
            class="shrink-0 text-amber-500 text-xs font-bold"
            title="已置顶"
          >
            📌
          </span>
        </div>
        <div class="mt-1 text-xs text-gray-400">
          {{ report.is_owner ? '我的报表' : `共享自 ${report.owner_name || '其他用户'}` }}
        </div>
        <div class="mt-5 flex items-center justify-between gap-2 text-xs">
          <span :class="report.last_error ? 'text-red-500' : 'text-emerald-500'">
            {{ report.last_error ? '最近运行失败' : report.subscription_status ? '订阅运行中' : '可运行' }}
          </span>
          <span class="text-gray-400">{{ formatTime(report.last_run_at) }}</span>
        </div>
      </button>
    </div>
    <div v-if="!compact && filteredReports.length > pageSize" class="flex items-center justify-between rounded-xl border border-gray-100 bg-white px-3 py-2 text-xs text-gray-500 dark:border-gray-800 dark:bg-gray-900">
      <span>共 {{ filteredReports.length }} 个报表 · 第 {{ currentPage }} / {{ pageCount }} 页</span>
      <div class="flex items-center gap-1.5">
        <button type="button" class="rounded-lg border border-gray-200 px-2.5 py-1 hover:border-blue-300 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-gray-700" :disabled="currentPage <= 1" @click="currentPage -= 1">上一页</button>
        <button type="button" class="rounded-lg border border-gray-200 px-2.5 py-1 hover:border-blue-300 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-gray-700" :disabled="currentPage >= pageCount" @click="currentPage += 1">下一页</button>
      </div>
    </div>
    <div
      v-if="!filteredReports.length"
      class="rounded-2xl border border-dashed border-gray-200 p-8 text-center text-sm text-gray-400 dark:border-gray-800 space-y-2"
    >
      <p>当前分类下还没有固化报表</p>
      <p v-if="!compact" class="text-xs text-gray-500">
        您可以点击右上角「新建固化报表」手工录入 SQL，或在 ChatBI 智能对话查数成功后点击「添加固化报表」
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import SavedReportItemCard from "@/components/chatbi/SavedReportItemCard.vue";
import SavedReportQuickViews from "@/components/chatbi/SavedReportQuickViews.vue";
import type { DataPortalHomePayload, DataPortalReportFilter, DataPortalReportItem } from "@/types/dataPortal";

const props = withDefaults(
  defineProps<{
    reports: DataPortalReportItem[];
    summary: DataPortalHomePayload["report_summary"];
    compact?: boolean;
    manage?: boolean;
    initialFilter?: DataPortalReportFilter;
  }>(),
  { compact: false, manage: false, initialFilter: "all" }
);

const emit = defineEmits<{
  (event: "open-report", report: DataPortalReportItem): void;
  (event: "filter-change", filter: DataPortalReportFilter): void;
  (event: "create-report"): void;
  (event: "open-specs"): void;
  (event: "execute", report: DataPortalReportItem): void;
  (event: "edit", report: DataPortalReportItem): void;
  (event: "detail", report: DataPortalReportItem): void;
  (event: "favorite", report: DataPortalReportItem): void;
  (event: "pin", report: DataPortalReportItem): void;
  (event: "share", report: DataPortalReportItem): void;
  (event: "copy", report: DataPortalReportItem): void;
  (event: "delete", report: DataPortalReportItem): void;
  (event: "subscription", report: DataPortalReportItem): void;
}>();

const activeFilter = ref<DataPortalReportFilter>(props.compact ? "subscribed" : props.initialFilter);
const searchQuery = ref("");
const currentPage = ref(1);
const pageSize = 12;
const REPORT_VIEW_STORAGE_KEY = "nanzi_saved_report_portal_view";
const isReportViewMode = (value: string | null): value is "card" | "list" => value === "card" || value === "list";
const readReportViewMode = (): "card" | "list" => {
  try {
    const stored = typeof window !== "undefined" ? window.localStorage.getItem(REPORT_VIEW_STORAGE_KEY) : null;
    return isReportViewMode(stored) ? stored : "list";
  } catch {
    return "list";
  }
};
const reportViewMode = ref<"card" | "list">(readReportViewMode());
const setReportViewMode = (mode: "card" | "list") => {
  reportViewMode.value = mode;
  try {
    window.localStorage.setItem(REPORT_VIEW_STORAGE_KEY, mode);
  } catch {
    // 隐私模式或禁用存储时仍保留当前会话内的切换结果。
  }
};
const filters: Array<{ value: DataPortalReportFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "subscribed", label: "已订阅" },
  { value: "pinned", label: "置顶" },
  { value: "favorite", label: "收藏" },
  { value: "shared", label: "共享给我" },
  { value: "recent", label: "最近运行" },
];

const visibleFilters = computed(() => (props.compact ? filters.filter((item) => item.value !== "all") : filters));
const filteredReports = computed(() => {
  const keyword = searchQuery.value.trim().toLocaleLowerCase();
  return props.reports.filter((report) => {
    if (activeFilter.value === "all") return true;
    if (activeFilter.value === "subscribed") return !!report.subscription_status;
    if (activeFilter.value === "pinned") return report.pinned || !!report.pinned_at;
    if (activeFilter.value === "favorite") return report.is_favorite;
    if (activeFilter.value === "shared") return !report.is_owner;
    return !!report.last_run_at;
  }).filter((report) => {
    if (!keyword) return true;
    const haystack = [report.title, report.description, report.owner_name, ...(report.tags || [])]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase();
    return haystack.includes(keyword);
  });
});

const pageCount = computed(() => Math.max(1, Math.ceil(filteredReports.value.length / pageSize)));
const pagedReports = computed(() => {
  if (props.compact) return filteredReports.value.slice(0, 6);
  const start = (currentPage.value - 1) * pageSize;
  return filteredReports.value.slice(start, start + pageSize);
});

const setFilter = (filter: DataPortalReportFilter) => {
  activeFilter.value = filter;
  currentPage.value = 1;
  emit("filter-change", filter);
};

watch([searchQuery, activeFilter, () => props.reports], () => {
  currentPage.value = 1;
});

watch(
  () => props.initialFilter,
  (filter) => {
    if (!props.compact) activeFilter.value = filter;
  }
);

const formatTime = (value?: string | null) =>
  value ? new Date(value).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" }) : "尚未运行";

const formatDate = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN", { dateStyle: "medium", timeStyle: "short" }) : "";
</script>
