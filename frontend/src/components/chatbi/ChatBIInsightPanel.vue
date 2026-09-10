<template>
  <div
    v-if="visible"
    class="mt-3 mb-3 border-t border-gray-100 pt-3 dark:border-gray-700/50"
  >
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      <span
        v-if="meta"
        class="inline-flex items-center gap-1 text-emerald-600/80 dark:text-emerald-400/80"
      >
        <span class="text-[10px]">✓</span>
        <span>查询成功 · {{ resultCountLabel }}</span>
      </span>
      <div class="flex min-w-0 flex-1 items-center gap-1.5">
        <svg
          class="h-3.5 w-3.5 shrink-0 text-gray-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"
          />
        </svg>
        <div class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            type="button"
            class="group/tab relative inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider transition-colors"
            :class="
              activeTab === tab.id
                ? 'text-gray-600 dark:text-gray-200'
                : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
            "
            @click="toggleTab(tab.id)"
          >
            {{ tab.label }}
            <!-- 首次使用引导气泡：挂在「明细」页签上，提示用户这里可以看/导明细 -->
            <transition
              enter-active-class="transition-all duration-300 ease-out"
              enter-from-class="opacity-0 -translate-y-2 scale-95"
              enter-to-class="opacity-100 translate-y-0 scale-100"
              leave-active-class="transition-all duration-200 ease-in"
              leave-from-class="opacity-100 translate-y-0 scale-100"
              leave-to-class="opacity-0 -translate-y-1 scale-95"
            >
              <div
                v-if="showExportHint && tab.id === 'table'"
                class="absolute left-1/2 top-full z-[5] mt-2 whitespace-nowrap rounded-xl border border-primary/20 bg-primary/95 px-3 py-2 text-[12px] text-white shadow-2xl backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-900/95"
                style="transform: translateX(-50%)"
                role="status"
              >
                <div class="absolute -top-1.5 left-1/2 h-3 w-3 -translate-x-1/2 rotate-45 border-l border-t border-primary/20 bg-primary/95 dark:border-slate-700/60 dark:bg-slate-900/95" />
                <div class="flex items-center gap-2.5">
                  <svg class="h-4 w-4 shrink-0 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  <div class="flex flex-col text-left">
                    <span class="font-bold leading-tight">查看并导出完整明细</span>
                    <span class="mt-0.5 text-[11px] leading-tight text-white/85 dark:text-slate-300">这里可看数据，也能一次导最多 10 万行</span>
                  </div>
                  <button
                    type="button"
                    @click.stop="dismissExportHint"
                    class="ml-1 rounded-md p-1 text-white/70 transition-colors hover:bg-white/20 hover:text-white dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
                    title="知道了"
                  >
                    <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              </div>
            </transition>
          </button>
        </div>
        <span
          v-if="sampleChip"
          class="text-[10px] text-gray-400 dark:text-gray-500"
          :title="sampleNotice"
        >
          {{ sampleChip }}
        </span>
        <svg
          class="ml-auto h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform duration-200"
          :class="{ 'rotate-180': !!activeTab }"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
        </svg>
      </div>
    </div>

    <div v-if="activeTab === 'citations' && hasCitations" class="mt-2">
      <div class="flex flex-wrap gap-2 py-1">
        <button
          v-for="(cite, cIdx) in citations"
          :key="cIdx"
          type="button"
          class="citation-chip group/cite relative flex max-w-full items-center space-x-2 overflow-hidden rounded-lg px-2.5 py-1.5 transition-all"
          :class="
            cite.similarity && cite.similarity < 0.5
              ? 'border border-amber-200/80 bg-amber-50/80 hover:border-amber-400/60 dark:border-amber-700/50 dark:bg-amber-900/20'
              : 'border border-gray-100 bg-gray-50 hover:border-primary/40 dark:border-gray-700 dark:bg-gray-800/80 dark:hover:border-primary/40'
          "
          @click.stop="emitOpenCitation(cite, $event)"
        >
          <svg class="h-3.5 w-3.5 shrink-0 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span
            class="max-w-[120px] truncate text-[11px] font-medium text-gray-600 dark:text-gray-300 sm:max-w-[150px]"
            :title="cite.doc_name"
          >
            {{ cite.doc_name }}
          </span>
          <span
            v-if="cite.similarity"
            class="rounded px-1 font-mono text-[9px]"
            :class="
              cite.similarity < 0.5
                ? 'bg-amber-100/80 text-amber-600 dark:bg-amber-900/40 dark:text-amber-400'
                : 'bg-gray-100 text-gray-400 dark:bg-gray-700'
            "
            :title="cite.similarity < 0.5 ? '相似度较低，请结合原文核对' : undefined"
          >
            {{ (cite.similarity * 100).toFixed(0) }}%
          </span>
        </button>
      </div>
    </div>

    <div v-else-if="activeTab === 'table' && hasTable" class="mt-2">
      <p v-if="sampleNotice" class="mb-1.5 text-[10px] leading-relaxed text-gray-400 dark:text-gray-500">
        {{ sampleNotice }}
      </p>
      <p
        v-if="federatedNotice"
        class="mb-1.5 text-[10px] leading-relaxed text-amber-600/90 dark:text-amber-400/90"
      >
        {{ federatedNotice }}
      </p>
      <div class="mb-1 flex items-center justify-between gap-2 text-[10px] text-gray-400">
        <div class="flex items-center gap-2.5">
          <div ref="exportMenuRef" class="relative inline-flex">
          <button
            type="button"
            class="inline-flex items-center gap-1 font-semibold text-primary/80 transition-colors hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
            :title="exportDisabledReason || '数据库完整明细直链导出（不受 AI 分析样例上限限制），不占用模型上下文'"
            :disabled="exportingDetail || !canExportFull"
            @click="openExportMenu"
          >
            <svg v-if="!exportingDetail" class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            <svg v-else class="h-3 w-3 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <span>{{ exportingDetail ? "导出中…" : "导出完整明细" }}</span>
          </button>

          <!-- 格式选择：紧贴按钮的下拉浮层（带向上小尖角） -->
          <transition
            enter-active-class="transition-all duration-200 ease-out"
            enter-from-class="opacity-0 translate-y-1 scale-95"
            enter-to-class="opacity-100 translate-y-0 scale-100"
            leave-active-class="transition-all duration-150 ease-in"
            leave-from-class="opacity-100 translate-y-0 scale-100"
            leave-to-class="opacity-0 translate-y-1 scale-95"
          >
            <div
              v-if="exportMenuOpen"
              class="absolute left-0 top-full z-[20] mt-2 w-80 origin-top-left rounded-xl border border-gray-200 bg-white py-1 shadow-2xl dark:border-gray-700 dark:bg-gray-800"
              role="menu"
              aria-label="选择导出格式"
            >
              <div class="absolute -top-1.5 left-6 h-3 w-3 rotate-45 border-l border-t border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800" />
              <p class="px-3.5 pb-1.5 pt-1 text-[10px] leading-relaxed text-gray-400 dark:text-gray-500">
                直链重跑当前查询，单次最多 {{ exportLimitLabel }} 行，不占用模型上下文。
              </p>
              <button
                v-for="opt in exportFormatOptions"
                :key="opt.value"
                type="button"
                role="menuitem"
                class="flex w-full items-center gap-3 px-3.5 py-2 text-left transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/60"
                :disabled="exportingDetail"
                @click="pickExportFormat(opt.value)"
              >
                <span class="text-sm font-bold text-gray-700 dark:text-gray-200">{{ opt.label }}</span>
                <span class="text-[11px] text-gray-400 dark:text-gray-500">{{ opt.desc }}</span>
              </button>
              <div class="mt-1 border-t border-gray-100 px-3.5 pt-1.5 pb-0.5 dark:border-gray-700">
                <span class="text-[10px] text-gray-300 dark:text-gray-600">点击格式后立即导出，最多 {{ exportLimitLabel }} 行</span>
              </div>
            </div>
          </transition>
        </div>
        </div>
        <div v-if="pageCount > 1" class="flex items-center gap-2">
          <button
            type="button"
            class="disabled:opacity-40"
            :disabled="page <= 1"
            @click="page = Math.max(1, page - 1)"
          >
            上一页
          </button>
          <span class="tabular-nums">{{ page }} / {{ pageCount }}</span>
          <button
            type="button"
            class="disabled:opacity-40"
            :disabled="page >= pageCount"
            @click="page = Math.min(pageCount, page + 1)"
          >
            下一页
          </button>
        </div>
      </div>
      <div class="max-h-72 overflow-auto rounded-md bg-gray-50/60 dark:bg-gray-900/20">
        <table class="min-w-full border-collapse text-left text-[11px]">
          <thead class="sticky top-0 z-[1] bg-gray-50/95 dark:bg-gray-900/80">
            <tr>
              <th
                v-for="col in meta!.table!.columns"
                :key="col"
                class="whitespace-nowrap px-2.5 py-1.5 font-medium text-gray-500 dark:text-gray-400"
              >
                {{ col }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(row, rowIndex) in pageRows"
              :key="`${page}-${rowIndex}`"
              class="odd:bg-transparent even:bg-white/40 dark:even:bg-white/[0.02]"
            >
              <td
                v-for="(cell, cellIndex) in row"
                :key="cellIndex"
                class="max-w-[14rem] truncate px-2.5 py-1 text-gray-600 dark:text-gray-300"
                :title="formatCell(cell)"
              >
                {{ formatCell(cell) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div
      v-else-if="activeTab === 'evidence' && meta"
      class="mt-2 space-y-2 text-[11px] leading-relaxed text-gray-500 dark:text-gray-400"
    >
      <div class="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        <div>证据状态：{{ evidenceStatusLabel }}</div>
        <div class="break-all">来源：{{ meta.evidence?.source_ref || "未识别" }}</div>
        <div>观测时间：{{ formatEvidenceTime(meta.evidence?.observed_at) }}</div>
        <div>数据截至：{{ formatEvidenceTime(meta.evidence?.source_as_of) }}</div>
        <div>时效：{{ freshnessLabel }}</div>
        <div>执行：{{ executionLabel }}</div>
      </div>
      <div v-if="meta.sources?.length">
        <div v-for="(source, index) in meta.sources" :key="index">
          {{ source.dataset_name || "授权数据集" }}
          <span class="text-gray-400">· {{ source.tables.map((item) => item.physical_name).join("、") }}</span>
        </div>
      </div>
      <div v-if="meta.permission?.row_filter_applied">
        {{ meta.permission.message || "已按你的数据权限自动过滤结果" }}
        <span v-if="meta.permission.rule_count">（{{ meta.permission.rule_count }} 条规则）</span>
      </div>
      <div v-if="meta.final_sql">
        <button type="button" class="text-gray-500 underline-offset-2 hover:underline" @click="showSql = !showSql">
          {{ showSql ? "收起 SQL" : "查看 SQL" }}
        </button>
        <pre
          v-if="showSql"
          class="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-gray-50/80 p-2 font-mono text-[10px] text-gray-600 dark:bg-gray-900/40 dark:text-gray-300"
        >{{ meta.final_sql }}</pre>
      </div>
    </div>
  </div>


</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import type { ChatBIInsightMeta } from "@/types/chatbiInsight";
import axios from "@/utils/axios";
import { resolveGeneratedFileHref } from "@/utils/generatedFileUrl";
import { useToast } from "@/composables/useToast";

const { showToast } = useToast();

const props = defineProps<{
  meta?: ChatBIInsightMeta | null;
  citations?: any[] | null;
}>();

const emit = defineEmits<{
  (e: "open-citation", payload: { citation: any; event: MouseEvent }): void;
}>();

type TabId = "citations" | "table" | "evidence" | null;

const activeTab = ref<TabId>(null);
const page = ref(1);
const showSql = ref(false);
const exportingDetail = ref(false);

/* ── 完整明细导出：格式选择下拉 + 首次使用引导气泡 ── */
const EXPORT_HINT_STORAGE_KEY = "chatbi_full_export_hint_seen";
const exportMenuOpen = ref(false);
const exportMenuRef = ref<HTMLElement | null>(null);
type ExportFormat = "xlsx" | "csv" | "md";
const exportFormatOptions: Array<{ value: ExportFormat; label: string; desc: string }> = [
  { value: "xlsx", label: "Excel (.xlsx)", desc: "多工作表，适合二次分析" },
  { value: "csv", label: "CSV (.csv)", desc: "轻量通用，Excel 打开不乱码" },
  { value: "md", label: "Markdown (.md)", desc: "表格文本，便于贴进文档" },
];
const exportLimitLabel = ref<string>("10 万");
const showExportHint = ref(false);
let exportHintTimer: ReturnType<typeof setTimeout> | null = null;

const citations = computed(() => (Array.isArray(props.citations) ? props.citations : []));
const hasCitations = computed(() => citations.value.length > 0);
const hasTable = computed(
  () => !!(props.meta?.table?.columns?.length && props.meta?.table?.rows?.length),
);
const visible = computed(() => !!(props.meta || hasCitations.value));

const tabs = computed(() => {
  const items: Array<{ id: Exclude<TabId, null>; label: string }> = [];
  if (hasCitations.value) {
    items.push({ id: "citations", label: `引用来源 (${citations.value.length})` });
  }
  if (hasTable.value) items.push({ id: "table", label: "明细" });
  if (props.meta) items.push({ id: "evidence", label: "依据" });
  return items;
});

const exactTotalCount = computed<number | null>(() => {
  const executionTotal = props.meta?.execution?.total_row_count;
  if (typeof executionTotal === "number" && Number.isFinite(executionTotal)) return executionTotal;
  const tableTotal = props.meta?.table?.total_row_count;
  return typeof tableTotal === "number" && Number.isFinite(tableTotal) ? tableTotal : null;
});

const returnedRowCount = computed(() => {
  const returned = props.meta?.execution?.returned_row_count;
  if (typeof returned === "number" && Number.isFinite(returned)) return returned;
  const executionRows = props.meta?.execution?.row_count;
  if (typeof executionRows === "number" && Number.isFinite(executionRows)) return executionRows;
  return embeddedRows.value.length;
});

const resultCountLabel = computed(() => {
  if (exactTotalCount.value !== null) {
    const total = formatCount(exactTotalCount.value);
    const returned = formatCount(returnedRowCount.value);
    const truncated = props.meta?.execution?.truncated ?? props.meta?.table?.truncated;
    return truncated ? `匹配总数 ${total} 条 · 已返回 ${returned} 行` : `匹配总数 ${total} 条`;
  }
  return `已返回 ${formatCount(returnedRowCount.value)} 行 · 总数未统计`;
});

const sampleNotice = computed(() => {
  const scope = props.meta?.analysis_scope;
  if (!scope || scope.mode !== "sample") return "";
  const totalLabel = scope.total_row_count == null ? "总数未知" : `${scope.total_row_count} 行`;
  const basis = scope.total_row_count == null
    ? `已返回 ${scope.model_row_count} 行`
    : `全部 ${totalLabel}中的前 ${scope.model_row_count} 行`;
  return (
    String(scope.user_notice || "").trim() ||
    `AI 解读基于${basis}样例${scope.total_row_count == null ? "，数据库总数未统计" : "，并非逐行全量分析"}。`
  );
});

const sampleChip = computed(() => {
  const scope = props.meta?.analysis_scope;
  if (!scope || scope.mode !== "sample") return "";
  return `AI 样例 ${scope.model_row_count}/${scope.total_row_count == null ? "总数未知" : scope.total_row_count}`;
});

const pageSize = computed(() => {
  const size = Number(props.meta?.table?.page_size || 50);
  return Number.isFinite(size) && size > 0 ? Math.floor(size) : 50;
});

const embeddedRows = computed(() => {
  const rows = props.meta?.table?.rows;
  return Array.isArray(rows) ? rows : [];
});

const pageCount = computed(() => Math.max(1, Math.ceil(embeddedRows.value.length / pageSize.value)));

const pageRows = computed(() => {
  const start = (page.value - 1) * pageSize.value;
  return embeddedRows.value.slice(start, start + pageSize.value);
});

const evidenceStatusLabel = computed(() => {
  const labels: Record<string, string> = {
    success_non_empty: "查询成功 · 有数据",
    success_empty: "查询成功 · 空结果",
    failed: "查询失败",
    unavailable: "数据源不可用",
    denied: "访问被拒绝",
    unknown: "未知",
  };
  return labels[props.meta?.evidence?.result_status || "unknown"] || props.meta?.evidence?.result_status || "未知";
});

const freshnessLabel = computed(() => {
  const labels: Record<string, string> = {
    realtime: "实时",
    dynamic: "动态",
    historical: "历史",
    reuse_previous: "复用上一结果",
    static: "静态",
    unknown: "未知",
  };
  const value = props.meta?.evidence?.freshness || "unknown";
  return labels[value] || value;
});

const executionLabel = computed(() => {
  if (!props.meta) return "";
  if (props.meta.execution.mode === "federated") return "跨数据集联邦查询";
  if (props.meta.execution.mode === "repaired") return `修复后成功（${props.meta.execution.repair_count || 1} 次）`;
  return "SQL 直接执行成功";
});

watch(
  () => [props.meta?.result_id, props.meta?.table, citations.value.length] as const,
  () => {
    activeTab.value = null;
    page.value = 1;
    showSql.value = false;
  },
);

function toggleTab(tabId: Exclude<TabId, null>) {
  activeTab.value = activeTab.value === tabId ? null : tabId;
}

/** 完整明细导出所需的最小字段。 */
const exportDataSource = computed(() => props.meta?.sources?.[0]?.data_source || "");
const canExportFull = computed(() => {
  const m = props.meta;
  if (!m) return false;
  if (m.execution?.mode === "federated") return false;
  if (!m.final_sql || !m.final_sql.trim()) return false;
  return Boolean(exportDataSource.value);
});

/** 按钮禁用时的悬停说明：让用户知道为什么不能导出 */
const exportDisabledReason = computed<string>(() => {
  const m = props.meta;
  if (exportingDetail.value) return "";
  if (!m) return "当前没有查询结果明细，无法导出";
  if (m.execution?.mode === "federated") return "联邦查询暂不支持完整明细导出";
  if (!m.final_sql || !m.final_sql.trim()) return "缺少可执行的查询 SQL，无法导出完整明细";
  if (!exportDataSource.value) return "缺少数据源标识，无法导出完整明细";
  return "";
});

/** 联邦查询在明细页签顶部的一行说明文案 */
const federatedNotice = computed<string>(() => {
  const m = props.meta;
  if (!m || m.execution?.mode !== "federated") return "";
  return "当前为跨数据集联邦查询，暂不支持完整明细导出；如需导出明细，请改为对单个数据集查询后操作。";
});

function openExportMenu() {
  const m = props.meta;
  if (!m?.final_sql || exportingDetail.value) return;
  if (!exportDataSource.value) {
    showToast("缺少数据源标识，无法导出完整明细", "warning");
    return;
  }
  if (m.execution?.mode === "federated") {
    showToast("联邦查询暂不支持完整明细导出", "warning");
    return;
  }
  exportMenuOpen.value = !exportMenuOpen.value;
}

function closeExportMenu() {
  exportMenuOpen.value = false;
}

async function pickExportFormat(fmt: ExportFormat) {
  const m = props.meta;
  if (!m?.final_sql || exportingDetail.value) return;
  exportMenuOpen.value = false;
  exportingDetail.value = true;
  try {
    const response = await axios.post("/api/portal/chatbi-export/result", {
      sql: m.final_sql,
      data_source: exportDataSource.value,
      dataset_name: m.sources?.[0]?.dataset_name || null,
      format: fmt,
      execution_mode: m.execution?.mode || "direct",
      result_id: m.result_id || null,
    });
    const data = response.data?.data;
    const downloadUrl: string = data?.download_url;
    if (!downloadUrl) throw new Error("missing download_url");
    const filename: string =
      data?.filename || `chatbi_export_${(m.result_id || "result").slice(0, 8)}.${fmt}`;
    const href = resolveGeneratedFileHref(downloadUrl);
    const link = document.createElement("a");
    link.href = href;
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    link.remove();
    const rowCount = data?.row_count;
    showToast(
      rowCount ? `已导出 ${Number(rowCount).toLocaleString("zh-CN")} 行明细数据` : "完整明细导出成功",
      "success",
    );
  } catch (e: any) {
    console.error("ChatBI 完整明细导出失败", e);
    const detail = e?.response?.data?.detail || e?.message || "导出失败";
    showToast(`导出失败：${detail}`, "error");
  } finally {
    exportingDetail.value = false;
  }
}

function onDocumentMouseDown(event: MouseEvent) {
  if (!exportMenuOpen.value) return;
  const target = event.target as Node | null;
  if (exportMenuRef.value && exportMenuRef.value.contains(target)) return;
  exportMenuOpen.value = false;
}

onMounted(() => {
  document.addEventListener("mousedown", onDocumentMouseDown);
});

onUnmounted(() => {
  document.removeEventListener("mousedown", onDocumentMouseDown);
  if (exportHintTimer) {
    clearTimeout(exportHintTimer);
    exportHintTimer = null;
  }
});

function dismissExportHint() {
  showExportHint.value = false;
  if (exportHintTimer) {
    clearTimeout(exportHintTimer);
    exportHintTimer = null;
  }
  try {
    localStorage.setItem(EXPORT_HINT_STORAGE_KEY, "1");
  } catch {
    /* ignore */
  }
}

function triggerExportHint() {
  let seen = false;
  try {
    seen = localStorage.getItem(EXPORT_HINT_STORAGE_KEY) === "1";
  } catch {
    /* ignore */
  }
  if (seen) return;
  dismissExportHint();
  showExportHint.value = true;
  if (exportHintTimer) {
    clearTimeout(exportHintTimer);
    exportHintTimer = null;
  }
  exportHintTimer = setTimeout(() => {
    showExportHint.value = false;
    exportHintTimer = null;
  }, 4500);
}

watch(
  () => [props.meta?.result_id, hasTable.value, activeTab.value, canExportFull.value] as const,
  () => {
    // 气泡挂在「明细」页签上：有明细且可导完整明细时，提示用户点这里查看/导出
    if (hasTable.value && canExportFull.value) {
      triggerExportHint();
    }
  },
);

function emitOpenCitation(citation: any, event: MouseEvent) {
  emit("open-citation", { citation, event });
}

function formatEvidenceTime(value?: string | null) {
  if (!value) return "未提供";
  return value.replace("T", " ").replace(/\.\d{3,6}(?=[+-]\d{2}:?\d{2}|Z$)/, "");
}

function formatCount(value: number): string {
  return value.toLocaleString("zh-CN");
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}
</script>
