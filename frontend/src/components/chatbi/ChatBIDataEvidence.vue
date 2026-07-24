<template>
  <div class="mt-3 rounded-xl border border-emerald-100 dark:border-emerald-900/40 bg-emerald-50/30 dark:bg-emerald-950/10 text-xs">
    <button type="button" class="w-full flex items-center justify-between gap-3 px-3 py-2 text-left" @click="showDetails = !showDetails">
      <span class="min-w-0 flex items-center gap-2 text-emerald-700 dark:text-emerald-300 font-semibold">
        <span>✓</span>
        <span class="truncate">数据查询成功 · {{ meta.execution.row_count }} 行{{ permissionLabel }}</span>
      </span>
      <span class="shrink-0 text-[11px] text-emerald-600 dark:text-emerald-400">查看数据依据</span>
    </button>
    <div v-if="showDetails" class="border-t border-emerald-100 dark:border-emerald-900/40 px-3 py-3 space-y-3 text-gray-600 dark:text-gray-300">
      <section class="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div>
          <div class="font-bold text-gray-700 dark:text-gray-200 mb-1">证据状态</div>
          <div>{{ evidenceStatusLabel }}</div>
        </div>
        <div>
          <div class="font-bold text-gray-700 dark:text-gray-200 mb-1">来源标识</div>
          <div class="break-all">{{ meta.evidence?.source_ref || '未识别' }}</div>
        </div>
        <div>
          <div class="font-bold text-gray-700 dark:text-gray-200 mb-1">观测时间</div>
          <div>{{ formatEvidenceTime(meta.evidence?.observed_at) }}</div>
        </div>
        <div>
          <div class="font-bold text-gray-700 dark:text-gray-200 mb-1">数据截至</div>
          <div>{{ formatEvidenceTime(meta.evidence?.source_as_of) }}</div>
        </div>
        <div>
          <div class="font-bold text-gray-700 dark:text-gray-200 mb-1">数据时效</div>
          <div>{{ freshnessLabel }}</div>
        </div>
      </section>
      <section v-if="meta.sources.length">
        <div class="font-bold text-gray-700 dark:text-gray-200 mb-1">数据来源</div>
        <div v-for="(source, index) in meta.sources" :key="index" class="leading-relaxed">
          {{ source.dataset_name || '授权数据集' }}
          <span class="text-gray-400">· {{ source.tables.map(item => item.physical_name).join('、') }}</span>
        </div>
      </section>
      <section v-if="meta.permission?.row_filter_applied">
        <div class="font-bold text-gray-700 dark:text-gray-200 mb-1">权限说明</div>
        <div>{{ meta.permission.message || '已按你的数据权限自动过滤结果' }}<span v-if="meta.permission.rule_count">（{{ meta.permission.rule_count }} 条规则）</span></div>
      </section>
      <section>
        <div class="font-bold text-gray-700 dark:text-gray-200 mb-1">执行说明</div>
        <div>{{ executionLabel }}</div>
      </section>
      <section v-if="meta.final_sql">
        <button type="button" class="font-semibold text-primary hover:underline" @click="showSql = !showSql">{{ showSql ? '收起 SQL' : '查看 SQL' }}</button>
        <pre v-if="showSql" class="mt-2 max-h-48 overflow-auto rounded-lg bg-gray-950 p-3 text-[11px] leading-relaxed text-emerald-200 whitespace-pre-wrap break-all">{{ meta.final_sql }}</pre>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import type { ChatBIInsightMeta } from "@/types/chatbiInsight";

const props = defineProps<{ meta: ChatBIInsightMeta }>();
const showDetails = ref(false);
const showSql = ref(false);
const permissionLabel = computed(() => props.meta.permission?.row_filter_applied ? " · 已按权限过滤" : "");
const evidenceStatusLabel = computed(() => {
  const labels: Record<string, string> = {
    success_non_empty: "查询成功 · 有数据",
    success_empty: "查询成功 · 空结果",
    failed: "查询失败",
    unavailable: "数据源不可用",
    denied: "访问被拒绝",
    unknown: "未知",
  };
  return labels[props.meta.evidence?.result_status || "unknown"] || props.meta.evidence?.result_status || "未知";
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
  const value = props.meta.evidence?.freshness || "unknown";
  return labels[value] || value;
});
const formatEvidenceTime = (value?: string | null) => {
  if (!value) return "未提供";
  return value.replace("T", " ").replace(/\.\d{3,6}(?=[+-]\d{2}:?\d{2}|Z$)/, "");
};
const executionLabel = computed(() => {
  if (props.meta.execution.mode === "federated") return "跨数据集联邦查询成功";
  if (props.meta.execution.mode === "repaired") return `SQL 修复后成功（${props.meta.execution.repair_count || 1} 次）`;
  return "SQL 直接执行成功";
});
</script>
