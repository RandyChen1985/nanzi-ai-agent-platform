<template>
  <div class="space-y-6">
    <!-- 口径说明必须先出现：这张表最容易被读成「图片没花钱」或「额度漏扣了」 -->
    <div class="rounded-2xl border border-amber-200 bg-amber-50/60 px-4 py-3 text-xs leading-relaxed text-amber-800">
      这里统计生图 / 改图的出图张数与上游折算 Token，它<strong>不并入</strong>大模型
      Token 总量，额度页也不会因为出图而扣减。上游接口未返回 usage 的调用单独计数，
      <strong>不按 0 计算</strong>——0 会被读成「这次没花钱」。
    </div>

    <!-- 首屏加载态：不能落到「暂无数据」分支 -->
    <div
      v-if="loading && !loaded"
      class="rounded-2xl border border-gray-100 bg-white px-4 py-16 text-center text-sm text-gray-400 shadow-sm"
    >
      加载中…
    </div>

    <template v-else>
      <!-- 汇总卡 -->
      <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <div class="bg-white rounded-2xl p-5 border border-amber-100 shadow-sm">
          <span class="text-sm font-medium text-gray-500">出图张数</span>
          <div class="mt-1 flex items-baseline gap-2">
            <h3 class="text-2xl sm:text-3xl font-bold text-gray-900 tabular-nums leading-none">
              {{ summary.images }}
            </h3>
            <span class="text-xs text-gray-400">张</span>
          </div>
          <p class="mt-1.5 text-xs text-gray-400">
            请求 {{ summary.requested }} 张
            <span v-if="summary.failed > 0" class="font-semibold text-rose-600">
              · 失败 {{ summary.failed }} 张
            </span>
          </p>
        </div>

        <div class="bg-white rounded-2xl p-5 border border-amber-100 shadow-sm">
          <span class="text-sm font-medium text-gray-500">上游折算 Token</span>
          <div class="mt-1 flex items-baseline gap-2">
            <h3
              class="text-2xl sm:text-3xl font-bold text-gray-900 tabular-nums leading-none"
              :title="formatTokenFull(summary.upstream_tokens)"
            >
              {{ formatTokenCompact(summary.upstream_tokens) }}
            </h3>
          </div>
          <p class="mt-1.5 text-xs text-gray-400">按网关 usage 原样累加，非金额</p>
          <p v-if="summary.tokens_missing_calls > 0" class="mt-1 text-xs font-semibold text-amber-600">
            上游未返回：{{ summary.tokens_missing_calls }} 次调用
          </p>
        </div>

        <div class="bg-white rounded-2xl p-5 border border-gray-100 shadow-sm">
          <span class="text-sm font-medium text-gray-500">工具调用次数</span>
          <div class="mt-1 flex items-baseline gap-2">
            <h3 class="text-2xl sm:text-3xl font-bold text-gray-900 tabular-nums leading-none">
              {{ summary.calls }}
            </h3>
            <span class="text-xs text-gray-400">次</span>
          </div>
          <p class="mt-1.5 text-xs text-gray-400">生图 + 改图合计</p>
        </div>

        <div class="bg-white rounded-2xl p-5 border border-gray-100 shadow-sm">
          <span class="text-sm font-medium text-gray-500">平均单次耗时</span>
          <div class="mt-1 flex items-baseline gap-2">
            <h3 class="text-2xl sm:text-3xl font-bold text-gray-900 tabular-nums leading-none">
              {{ formatElapsed(summary.avg_elapsed_ms) }}
            </h3>
          </div>
          <p class="mt-1.5 text-xs text-gray-400">含上游生成与回传</p>
        </div>
      </div>

      <!-- 按天趋势 -->
      <div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6 border border-gray-100">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-base sm:text-lg font-bold text-gray-900 flex items-center">
            <span class="w-1.5 h-1.5 rounded-full bg-amber-500 mr-2"></span>
            按天出图
          </h2>
          <span class="hidden sm:inline-flex rounded-full bg-gray-50 px-2.5 py-1 text-[11px] font-semibold text-gray-500 tabular-nums">
            {{ rangeLabel }}
          </span>
        </div>
        <div class="h-64 sm:h-80 w-full relative flex items-center justify-center">
          <div v-if="loading" class="text-gray-400 text-sm">加载中…</div>
          <div v-else-if="!hasTrend" class="text-gray-400 text-sm">暂无图片用量记录</div>
          <v-chart v-else class="h-full w-full" :option="trendChartOption" autoresize />
        </div>
      </div>

      <!-- 明细 -->
      <div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6 border border-gray-100">
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
          <div>
            <h2 class="text-base sm:text-lg font-bold text-gray-900 flex items-center">
              <span class="w-1.5 h-1.5 rounded-full bg-amber-500 mr-2"></span>
              用量明细
            </h2>
            <p class="text-xs text-gray-400 mt-1">
              数据源为执行轨迹的 image_usage，按{{ groupBy === 'user' ? '用户' : '智能体' }}聚合
            </p>
          </div>
          <div class="flex items-center gap-2">
            <div class="inline-flex rounded-xl border border-gray-200 bg-white p-0.5 text-xs font-semibold">
              <button
                type="button"
                class="rounded-lg px-3 py-1.5 transition-colors"
                :class="groupBy === 'agent' ? 'bg-gray-900 text-white' : 'text-gray-500 hover:text-gray-800'"
                @click="setGroupBy('agent')"
              >
                按智能体
              </button>
              <!-- 后端对普通用户的 group_by=user 直接 400，前端就不该给出这个选项 -->
              <button
                v-if="isAdmin"
                type="button"
                class="rounded-lg px-3 py-1.5 transition-colors"
                :class="groupBy === 'user' ? 'bg-gray-900 text-white' : 'text-gray-500 hover:text-gray-800'"
                @click="setGroupBy('user')"
              >
                按用户
              </button>
            </div>
            <button
              type="button"
              class="inline-flex h-9 items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-3 text-xs font-bold text-gray-700 shadow-sm transition-all hover:bg-gray-50 active:scale-95 disabled:opacity-50"
              :disabled="exporting"
              @click="exportCsv"
            >
              <svg class="h-4 w-4 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v12m0 0l-4-4m4 4l4-4M4 19h16" />
              </svg>
              {{ exporting ? '导出中…' : '导出 CSV' }}
            </button>
          </div>
        </div>

        <div class="overflow-x-auto">
          <table class="min-w-full divide-y divide-gray-100 text-sm">
            <thead>
              <tr class="text-left text-xs font-medium text-gray-500 whitespace-nowrap">
                <th class="py-3 px-4">{{ groupBy === 'user' ? '账号' : '智能体' }}</th>
                <th class="py-3 px-4 text-right">出图 / 请求</th>
                <th class="py-3 px-4 text-right">上游 Token</th>
                <th class="py-3 px-4 text-right">调用</th>
                <th class="py-3 px-4 text-right">平均耗时</th>
                <th class="py-3 px-4 text-right">最近一次</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-50 text-gray-700">
              <tr v-if="loading">
                <td colspan="6" class="py-10 text-center text-gray-400">加载中…</td>
              </tr>
              <tr v-else-if="!hasRows">
                <td colspan="6" class="py-10 text-center text-gray-400">暂无图片用量明细</td>
              </tr>
              <tr
                v-for="row in groups"
                :key="row.key"
                class="hover:bg-gray-50/50 transition-colors"
              >
                <td class="py-3 px-4 font-medium text-gray-900 max-w-[14rem] truncate" :title="row.label">
                  {{ row.label }}
                </td>
                <td class="py-3 px-4 text-right tabular-nums">
                  <span class="font-semibold">{{ row.images }}</span>
                  <span class="text-gray-400"> / {{ row.requested }}</span>
                  <span v-if="row.failed > 0" class="ml-1.5 text-xs text-rose-600">失败 {{ row.failed }}</span>
                </td>
                <td class="py-3 px-4 text-right tabular-nums">
                  <span v-if="row.upstream_tokens > 0" :title="formatTokenFull(row.upstream_tokens)">
                    {{ formatTokenCompact(row.upstream_tokens) }}
                  </span>
                  <span v-else-if="row.tokens_missing_calls > 0" class="text-xs font-semibold text-amber-600">
                    上游未返回
                  </span>
                  <span v-else class="text-gray-300">-</span>
                </td>
                <td class="py-3 px-4 text-right tabular-nums">
                  {{ row.calls }}
                  <span v-if="row.tokens_missing_calls > 0" class="ml-1 text-[11px] text-amber-600">
                    （{{ row.tokens_missing_calls }} 次无用量）
                  </span>
                </td>
                <td class="py-3 px-4 text-right tabular-nums text-gray-500">{{ formatElapsed(row.avg_elapsed_ms) }}</td>
                <td class="py-3 px-4 text-right tabular-nums text-gray-500 whitespace-nowrap">
                  {{ formatStamp(row.last_at) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import axios from "@/utils/axios";
import { useToast } from "@/composables/useToast";
import { formatTokenCompact, formatTokenFull } from "@/utils/tokenFormat";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { BarChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";

use([CanvasRenderer, BarChart, GridComponent, LegendComponent, TooltipComponent]);

const API_BASE = "";
const ENDPOINT = "/api/portal/dashboard/token-stats/image-usage";

const props = defineProps<{
  /** 与「大模型 Token」Tab 共用的同一个区间：切 Tab 不能重置日期 */
  dateRange: { start: string; end: string };
  rangeLabel: string;
  /** 页头「刷新」按钮递增这个计数，两个 Tab 一起重取 */
  refreshTick: number;
  isAdmin: boolean;
}>();

const { showToast } = useToast();

type ImageUsageSummary = {
  images: number;
  requested: number;
  failed: number;
  calls: number;
  upstream_tokens: number;
  tokens_missing_calls: number;
  avg_elapsed_ms: number;
  last_at?: string | null;
};

type ImageUsageTrend = {
  date: string;
  images: number;
  failed: number;
};

type ImageUsageGroup = ImageUsageSummary & {
  key: string;
  label: string;
};

const emptySummary = (): ImageUsageSummary => ({
  images: 0,
  requested: 0,
  failed: 0,
  calls: 0,
  upstream_tokens: 0,
  tokens_missing_calls: 0,
  avg_elapsed_ms: 0,
  last_at: null,
});

const loading = ref(false);
/** 首屏是否已经成功取过一次：用来把「加载中」和「确实没有」分开 */
const loaded = ref(false);
const exporting = ref(false);
const groupBy = ref<"agent" | "user">("agent");
const summary = ref<ImageUsageSummary>(emptySummary());
const trends = ref<ImageUsageTrend[]>([]);
const groups = ref<ImageUsageGroup[]>([]);

const hasRows = computed(() => groups.value.length > 0);
const hasTrend = computed(() => trends.value.length > 0);

// 连点时段/刷新时先发的请求可能后到，按版本号丢弃，避免旧区间覆盖新区间
let requestVersion = 0;

const queryParams = () => ({
  start_date: props.dateRange.start,
  end_date: props.dateRange.end,
  group_by: groupBy.value,
});

const fetchData = async () => {
  const version = ++requestVersion;
  loading.value = true;
  try {
    const res = await axios.get(`${API_BASE}${ENDPOINT}`, { params: queryParams() });
    if (version !== requestVersion) return;
    const data = res.data ?? {};
    summary.value = { ...emptySummary(), ...(data.summary ?? {}) };
    trends.value = data.trends ?? [];
    groups.value = data.groups ?? [];
    loaded.value = true;
  } catch (error) {
    if (version !== requestVersion) return;
    // 失败必须清空：留着上一个区间的数字会被当成当前区间的结果
    summary.value = emptySummary();
    trends.value = [];
    groups.value = [];
    console.error("Failed to load image usage stats:", error);
    showToast("加载图片用量失败，请稍后重试", "error");
  } finally {
    if (version === requestVersion) loading.value = false;
  }
};

const setGroupBy = (next: "agent" | "user") => {
  if (groupBy.value === next) return;
  groupBy.value = next;
  fetchData();
};

watch(
  () => [props.dateRange.start, props.dateRange.end, props.refreshTick],
  () => fetchData(),
  { immediate: true },
);

const trendChartOption = computed(() => ({
  tooltip: { trigger: "axis" },
  legend: { data: ["成功", "失败"], top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10, color: "#6b7280" } },
  grid: { left: 8, right: 12, top: 34, bottom: 8, containLabel: true },
  xAxis: {
    type: "category",
    data: trends.value.map((row) => (row.date || "").slice(5)),
    axisLine: { lineStyle: { color: "#e5e7eb" } },
    axisTick: { show: false },
    axisLabel: { color: "#9ca3af", fontSize: 10 },
  },
  yAxis: {
    type: "value",
    splitLine: { lineStyle: { color: "#f3f4f6" } },
    axisLabel: { color: "#9ca3af", fontSize: 10 },
  },
  series: [
    {
      name: "成功",
      type: "bar",
      stack: "image",
      barMaxWidth: 22,
      itemStyle: { color: "#f59e0b", borderRadius: [3, 3, 0, 0] },
      data: trends.value.map((row) => row.images ?? 0),
    },
    {
      name: "失败",
      type: "bar",
      stack: "image",
      barMaxWidth: 22,
      itemStyle: { color: "#fca5a5" },
      data: trends.value.map((row) => row.failed ?? 0),
    },
  ],
}));

const formatElapsed = (ms?: number | null): string => {
  const value = Number(ms || 0);
  if (!value) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
};

const formatStamp = (iso?: string | null): string => {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

const exportCsv = async () => {
  exporting.value = true;
  try {
    const res = await axios.get(`${API_BASE}${ENDPOINT}/export`, {
      params: queryParams(),
      responseType: "blob",
    });
    const blob = new Blob([res.data], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `image-usage-${props.dateRange.start}_${props.dateRange.end}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  } catch (error) {
    console.error("Failed to export image usage:", error);
    showToast("导出失败，请稍后重试", "error");
  } finally {
    exporting.value = false;
  }
};
</script>
