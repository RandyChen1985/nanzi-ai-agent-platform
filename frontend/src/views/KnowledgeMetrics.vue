<template>
  <div class="space-y-6 pb-12">
    <!-- Header -->
    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
      <div>
        <h1 class="text-xl sm:text-2xl font-bold text-gray-900 tracking-tight flex items-center gap-2">
          <svg class="h-7 w-7 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 3.055A9.003 9.003 0 1020.945 13H11V3.055z" />
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z" />
          </svg>
          知识库运营分析
        </h1>
        <p class="text-sm text-gray-500 mt-1">统计各知识库和具体文档被智能体调用、检索与最终引用的全生命周期行为指标</p>
      </div>
      <div class="flex items-center gap-3">
        <select
          v-model="period"
          @change="onPeriodChange"
          class="rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm font-bold text-gray-700 shadow-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary cursor-pointer active:scale-95 transition-all"
        >
          <option value="week">最近 7 天</option>
          <option value="month">最近 30 天</option>
        </select>
        <button
          @click="fetchMetrics"
          :disabled="loading"
          class="inline-flex items-center justify-center p-2 sm:px-4 sm:py-2 border border-gray-200 rounded-xl shadow-sm text-sm font-bold text-gray-700 bg-white hover:bg-gray-50 active:scale-95 transition-all disabled:opacity-50"
        >
          <svg
            class="h-5 w-5 text-primary"
            :class="{ 'animate-spin': loading, 'sm:mr-2': true }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2.5"
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
          <span class="hidden sm:inline">刷新</span>
        </button>
      </div>
    </div>

    <!-- Overview Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-white rounded-2xl p-5 border border-gray-100 shadow-sm flex items-center justify-between gap-3">
        <div class="min-w-0 flex-1 space-y-1">
          <span class="text-sm font-medium text-gray-500">累计检索 (RAG)</span>
          <h3 class="text-2xl sm:text-3xl font-black text-gray-900 tabular-nums leading-none">{{ formatNumber(summary.totalSearch) }}</h3>
          <p class="text-xs text-gray-400">召回匹配的切片次数</p>
        </div>
        <div class="flex-shrink-0 p-3.5 rounded-2xl bg-blue-50/80 text-blue-600">
          <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </div>
      </div>

      <div class="bg-white rounded-2xl p-5 border border-gray-100 shadow-sm flex items-center justify-between gap-3">
        <div class="min-w-0 flex-1 space-y-1">
          <span class="text-sm font-medium text-gray-500">累计引用</span>
          <h3 class="text-2xl sm:text-3xl font-black text-gray-900 tabular-nums leading-none">{{ formatNumber(summary.totalCitation) }}</h3>
          <p class="text-xs text-gray-400">最终出现在模型回答中的次数</p>
        </div>
        <div class="flex-shrink-0 p-3.5 rounded-2xl bg-indigo-50/80 text-indigo-600">
          <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
        </div>
      </div>

      <div class="bg-white rounded-2xl p-5 border border-gray-100 shadow-sm flex items-center justify-between gap-3">
        <div class="min-w-0 flex-1 space-y-1">
          <span class="text-sm font-medium text-gray-500">平均引用率</span>
          <h3 class="text-2xl sm:text-3xl font-black text-gray-900 tabular-nums leading-none">{{ citationRate }}%</h3>
          <p class="text-xs text-gray-400">被检索文献的实际利用率</p>
        </div>
        <div class="flex-shrink-0 p-3.5 rounded-2xl bg-emerald-50/80 text-emerald-600">
          <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        </div>
      </div>

      <div class="bg-white rounded-2xl p-5 border border-gray-100 shadow-sm flex items-center justify-between gap-3">
        <div class="min-w-0 flex-1 space-y-1">
          <span class="text-sm font-medium text-gray-500">活跃文献源</span>
          <h3 class="text-2xl sm:text-3xl font-black text-gray-900 tabular-nums leading-none">{{ summary.activeDocs }}</h3>
          <p class="text-xs text-gray-400">有检索记录的文档数</p>
        </div>
        <div class="flex-shrink-0 p-3.5 rounded-2xl bg-amber-50/80 text-amber-600">
          <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.168.477 4 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4 1.253" />
          </svg>
        </div>
      </div>
    </div>

    <!-- Trend Analysis -->
    <div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6 border border-gray-100">
      <div class="mb-4">
        <h2 class="text-lg font-bold text-gray-900">每日检索与引用趋势</h2>
        <p class="text-xs text-gray-500 mt-1">展示每日知识库检索量与大模型最终采纳引用量的动态趋势对比</p>
      </div>
      <div class="h-72 w-full">
        <div v-if="trendData.length === 0" class="h-full w-full flex items-center justify-center text-sm text-gray-400">
          暂无趋势数据
        </div>
        <v-chart v-else class="h-full w-full" :option="trendChartOption" autoresize />
      </div>
    </div>

    <!-- Top Ranking Section -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Top 10 Knowledge Bases -->
      <div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6 border border-gray-100 flex flex-col">
        <div class="mb-4">
          <h2 class="text-lg font-bold text-gray-900">知识库引用排行榜 (Top 10)</h2>
          <p class="text-xs text-gray-500 mt-1">基于各知识库被引用的累计次数排行</p>
        </div>
        <div class="h-80 w-full flex-1">
          <div v-if="datasetsData.length === 0" class="h-full w-full flex items-center justify-center text-sm text-gray-400">
            暂无知识库统计
          </div>
          <v-chart v-else class="h-full w-full" :option="datasetChartOption" autoresize />
        </div>
      </div>

      <!-- Top 10 Documents -->
      <div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6 border border-gray-100 flex flex-col">
        <div class="mb-4">
          <h2 class="text-lg font-bold text-gray-900">核心文档引用排行榜 (Top 10)</h2>
          <p class="text-xs text-gray-500 mt-1">基于被引用的具体物理文件名排行</p>
        </div>
        <div class="h-80 w-full flex-1">
          <div v-if="documentsData.length === 0" class="h-full w-full flex items-center justify-center text-sm text-gray-400">
            暂无文档统计
          </div>
          <v-chart v-else class="h-full w-full" :option="documentChartOption" autoresize />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import axios from "axios";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { LineChart, BarChart } from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
} from "echarts/components";

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
]);

const loading = ref(false);
const period = ref("week");

// 数据存储
const trendData = ref<any[]>([]);
const datasetsData = ref<any[]>([]);
const documentsData = ref<any[]>([]);

// 聚合数据卡片
const summary = ref({
  totalSearch: 0,
  totalCitation: 0,
  activeDocs: 0,
});

const citationRate = computed(() => {
  if (summary.value.totalSearch === 0) return "0.0";
  return ((summary.value.totalCitation / summary.value.totalSearch) * 100).toFixed(1);
});

const onPeriodChange = () => {
  fetchMetrics();
};

const getQueryDates = () => {
  const end = new Date();
  const start = new Date();
  if (period.value === "week") {
    start.setDate(end.getDate() - 7);
  } else {
    start.setDate(end.getDate() - 30);
  }
  
  const formatDate = (date: Date) => {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  };

  return {
    startDate: formatDate(start),
    endDate: formatDate(end),
  };
};

const fetchMetrics = async () => {
  loading.value = true;
  const { startDate, endDate } = getQueryDates();
  try {
    const res = await axios.get("/api/portal/ragflow/metrics/summary", {
      params: {
        start_date: startDate,
        end_date: endDate,
      },
    });
    if (res.data && res.data.code === 0) {
      const data = res.data.data;
      trendData.value = data.trend || [];
      datasetsData.value = data.datasets || [];
      documentsData.value = data.documents || [];

      // 计算统计看板数据
      let totSearch = 0;
      let totCitation = 0;
      trendData.value.forEach((t) => {
        totSearch += t.search_count || 0;
        totCitation += t.citation_count || 0;
      });

      summary.value = {
        totalSearch: totSearch,
        totalCitation: totCitation,
        activeDocs: documentsData.value.length,
      };
    }
  } catch (e: any) {
    console.error("加载知识库分析数据失败", e);
  } finally {
    loading.value = false;
  }
};

const formatNumber = (num: number) => {
  return new Intl.NumberFormat().format(num);
};

// 趋势线图表配置
const trendChartOption = computed(() => {
  const dates = trendData.value.map((d) => d.date);
  const searchCounts = trendData.value.map((d) => d.search_count);
  const citationCounts = trendData.value.map((d) => d.citation_count);

  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(255, 255, 255, 0.95)",
      borderColor: "#e5e7eb",
      borderWidth: 1,
      textStyle: { color: "#1f2937", fontSize: 12 },
    },
    legend: {
      data: ["检索量 (RAG)", "引用量"],
      top: "0%",
      right: "0%",
      icon: "circle",
      textStyle: { color: "#4b5563" },
    },
    grid: {
      left: "2%",
      right: "2%",
      bottom: "2%",
      top: "12%",
      containLabel: true,
    },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: dates,
      axisLine: { lineStyle: { color: "#d1d5db" } },
      axisLabel: { color: "#6b7280" },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { type: "dashed", color: "#f3f4f6" } },
      axisLabel: { color: "#6b7280" },
    },
    series: [
      {
        name: "检索量 (RAG)",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: searchCounts,
        itemStyle: { color: "#3b82f6" },
        areaStyle: {
          color: {
            type: "linear",
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(59, 130, 246, 0.2)" },
              { offset: 1, color: "rgba(59, 130, 246, 0)" }
            ]
          }
        },
      },
      {
        name: "引用量",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: citationCounts,
        itemStyle: { color: "#6366f1" },
        areaStyle: {
          color: {
            type: "linear",
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(99, 102, 241, 0.2)" },
              { offset: 1, color: "rgba(99, 102, 241, 0)" }
            ]
          }
        },
      },
    ],
  };
});

// 知识库 Top 10 图表配置
const datasetChartOption = computed(() => {
  const sortedData = [...datasetsData.value].reverse().slice(0, 10);
  const names = sortedData.map((d) => d.name || d.id);
  const searchCounts = sortedData.map((d) => d.search_count);
  const citationCounts = sortedData.map((d) => d.citation_count);

  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(255, 255, 255, 0.95)",
      borderColor: "#e5e7eb",
      borderWidth: 1,
      textStyle: { color: "#1f2937", fontSize: 12 },
    },
    legend: {
      data: ["检索量", "引用量"],
      top: "0%",
      right: "0%",
      icon: "circle",
      textStyle: { color: "#4b5563" },
    },
    grid: {
      left: "3%",
      right: "4%",
      bottom: "3%",
      top: "12%",
      containLabel: true,
    },
    xAxis: {
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { type: "dashed", color: "#f3f4f6" } },
      axisLabel: { color: "#6b7280" },
    },
    yAxis: {
      type: "category",
      data: names,
      axisLine: { lineStyle: { color: "#d1d5db" } },
      axisLabel: {
        color: "#6b7280",
        formatter: (val: string) => {
          return val.length > 8 ? val.substring(0, 8) + "..." : val;
        },
      },
    },
    series: [
      {
        name: "检索量",
        type: "bar",
        data: searchCounts,
        itemStyle: { color: "#93c5fd", borderRadius: [0, 4, 4, 0] },
      },
      {
        name: "引用量",
        type: "bar",
        data: citationCounts,
        itemStyle: { color: "#3b82f6", borderRadius: [0, 4, 4, 0] },
      },
    ],
  };
});

// 文档 Top 10 图表配置
const documentChartOption = computed(() => {
  const sortedData = [...documentsData.value].reverse().slice(0, 10);
  const names = sortedData.map((d) => d.name || d.id);
  const citationCounts = sortedData.map((d) => d.citation_count);

  return {
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(255, 255, 255, 0.95)",
      borderColor: "#e5e7eb",
      borderWidth: 1,
      textStyle: { color: "#1f2937", fontSize: 12 },
    },
    grid: {
      left: "3%",
      right: "4%",
      bottom: "3%",
      top: "5%",
      containLabel: true,
    },
    xAxis: {
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { type: "dashed", color: "#f3f4f6" } },
      axisLabel: { color: "#6b7280" },
    },
    yAxis: {
      type: "category",
      data: names,
      axisLine: { lineStyle: { color: "#d1d5db" } },
      axisLabel: {
        color: "#6b7280",
        formatter: (val: string) => {
          return val.length > 12 ? val.substring(0, 12) + "..." : val;
        },
      },
    },
    series: [
      {
        name: "引用量",
        type: "bar",
        data: citationCounts,
        itemStyle: { color: "#6366f1", borderRadius: [0, 4, 4, 0] },
      },
    ],
  };
});

onMounted(() => {
  fetchMetrics();
});
</script>

<style scoped>
.text-primary {
  color: var(--color-primary, #3b82f6);
}
</style>
