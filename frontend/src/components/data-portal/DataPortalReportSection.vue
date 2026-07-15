<template>
  <section>
    <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 class="text-base font-bold text-gray-900 dark:text-gray-100">我的报表</h2>
      <div class="flex flex-wrap gap-1.5">
        <button v-for="option in visibleFilters" :key="option.value" type="button" class="rounded-lg px-2.5 py-1 text-xs font-medium transition" :class="activeFilter === option.value ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'" @click="setFilter(option.value)">
          {{ option.label }} {{ option.value === 'all' ? reports.length : (summary[option.value] || '') }}
        </button>
      </div>
    </div>
    <div v-if="filteredReports.length" class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      <button v-for="report in filteredReports" :key="report.id" type="button" class="min-h-[116px] rounded-2xl border border-gray-100 bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-md dark:border-gray-800 dark:bg-gray-900" @click="emit('open-report', report)">
        <strong class="line-clamp-2 text-sm text-gray-900 dark:text-gray-100">{{ report.title }}</strong>
        <div class="mt-1 text-xs text-gray-400">{{ report.is_owner ? '我的报表' : `共享自 ${report.owner_name || '其他用户'}` }}</div>
        <div class="mt-5 flex items-center justify-between gap-2 text-xs">
          <span :class="report.last_error ? 'text-red-500' : 'text-emerald-500'">{{ report.last_error ? '最近运行失败' : report.subscription_status ? '订阅运行中' : '可运行' }}</span>
          <span class="text-gray-400">{{ formatTime(report.last_run_at) }}</span>
        </div>
      </button>
    </div>
    <div v-else class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-sm text-gray-400 dark:border-gray-800">当前分类下还没有报表</div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { DataPortalHomePayload, DataPortalReportFilter, DataPortalReportItem } from "@/types/dataPortal";

const props = withDefaults(defineProps<{ reports: DataPortalReportItem[]; summary: DataPortalHomePayload["report_summary"]; compact?: boolean; initialFilter?: DataPortalReportFilter }>(), { compact: false, initialFilter: "all" });
const emit = defineEmits<{ (event: "open-report", report: DataPortalReportItem): void; (event: "filter-change", filter: DataPortalReportFilter): void }>();
const activeFilter = ref<DataPortalReportFilter>(props.compact ? "subscribed" : props.initialFilter);
const filters: Array<{ value: DataPortalReportFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "subscribed", label: "已订阅" }, { value: "pinned", label: "置顶" },
  { value: "favorite", label: "收藏" }, { value: "shared", label: "共享给我" },
  { value: "recent", label: "最近运行" },
];
const visibleFilters = computed(() => props.compact ? filters.filter((item) => item.value !== "all") : filters);
const filteredReports = computed(() => {
  const reports = props.reports.filter((report) => {
  if (activeFilter.value === "all") return true;
  if (activeFilter.value === "subscribed") return !!report.subscription_status;
  if (activeFilter.value === "pinned") return report.pinned || !!report.pinned_at;
  if (activeFilter.value === "favorite") return report.is_favorite;
  if (activeFilter.value === "shared") return !report.is_owner;
    return !!report.last_run_at;
  });
  return props.compact ? reports.slice(0, 6) : reports;
});
const setFilter = (filter: DataPortalReportFilter) => { activeFilter.value = filter; emit("filter-change", filter); };
watch(() => props.initialFilter, (filter) => { if (!props.compact) activeFilter.value = filter; });
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" }) : "尚未运行";
</script>
