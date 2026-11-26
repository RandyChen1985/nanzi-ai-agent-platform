<script setup lang="ts">
import { computed } from "vue";
import { ArrowPathIcon, ExclamationCircleIcon } from "@heroicons/vue/24/outline";
import { format } from "date-fns";
import { zhCN } from "date-fns/locale";
import type { ContextCompactionRecord } from "@/api/agent";

const props = withDefaults(defineProps<{
  records: ContextCompactionRecord[];
  loading?: boolean;
  error?: boolean;
  showRefresh?: boolean;
}>(), {
  loading: false,
  error: false,
  showRefresh: true,
});

const emit = defineEmits<{
  (event: "refresh"): void;
}>();

const contextEventLabel = (eventType: string) =>
  eventType === "context_compression"
    ? "AgentScope 压缩"
    : eventType === "context_summarized"
      ? "平台上下文摘录"
      : "上下文压缩";

const contextSourceLabel = (source: string) =>
  source === "agentscope" ? "AgentScope" : "平台";

const contextStageLabel = (stage: string) => {
  if (stage === "pre_route") return "路由前";
  if (stage === "resolved_model") return "目标模型重建";
  if (stage === "agent_runtime") return "Agent 运行时";
  return stage || "-";
};

const contextMetricText = (record: ContextCompactionRecord) => {
  const parts: string[] = [];
  if (record.dropped != null || record.kept != null) {
    parts.push(`消息 ${record.dropped ?? 0} → 保留 ${record.kept ?? 0}`);
  }
  if (record.summary_chars != null) parts.push(`${record.summary_chars} 字符摘要`);
  if (record.token_used != null || record.token_budget != null) {
    parts.push(`Token ${record.token_used ?? 0}/${record.token_budget ?? "-"}`);
  }
  return parts.join(" · ");
};

const formatContextDate = (dateStr?: string) => {
  if (!dateStr) return "-";
  const date = new Date(dateStr);
  return Number.isNaN(date.getTime())
    ? "-"
    : format(date, "yyyy-MM-dd HH:mm:ss", { locale: zhCN });
};

const recordCountLabel = computed(() => `${props.records.length} 条记录`);
</script>

<template>
  <div class="flex h-full min-h-0 flex-1 overflow-y-auto custom-scrollbar p-5">
    <div class="mx-auto max-w-4xl">
      <div class="mb-4 flex items-center justify-between gap-3">
        <div class="text-[11px] text-gray-400">
          按发生时间顺序展示平台摘录和 AgentScope 内部压缩事件；摘要只保留预览内容。
          <span v-if="records.length" class="ml-1 font-mono">{{ recordCountLabel }}</span>
        </div>
        <button
          v-if="showRefresh"
          type="button"
          class="inline-flex shrink-0 items-center gap-1 text-[11px] text-gray-400 transition-colors hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="loading"
          title="重新加载上下文压缩记录"
          @click="emit('refresh')"
        >
          <ArrowPathIcon class="h-3.5 w-3.5" :class="{ 'animate-spin': loading }" />
          刷新
        </button>
      </div>

      <div v-if="loading" class="py-20 text-center text-sm text-gray-400">
        <ArrowPathIcon class="mx-auto mb-2 h-6 w-6 animate-spin text-primary" />
        正在加载上下文压缩记录...
      </div>
      <div v-else-if="error" class="py-16 text-center text-sm text-red-500">
        <ExclamationCircleIcon class="mx-auto mb-2 h-8 w-8 text-red-200" />
        上下文记录加载失败
        <button
          type="button"
          class="ml-1 font-medium text-primary hover:underline"
          @click="emit('refresh')"
        >
          重试
        </button>
      </div>
      <div v-else-if="!records.length" class="py-16 text-center text-sm text-gray-400">
        <ExclamationCircleIcon class="mx-auto mb-2 h-8 w-8 text-gray-200" />
        暂无上下文压缩记录
        <p class="mt-1 text-[11px]">记录保留最近 7 天，仅在实际发生压缩时生成</p>
      </div>
      <div v-else class="space-y-3">
        <article
          v-for="record in records"
          :key="record.event_id"
          class="space-y-3 rounded-xl border border-gray-100 bg-gray-50/60 p-4"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-xs font-bold text-gray-800">
                {{ contextEventLabel(record.event_type) }}
              </span>
              <span class="rounded-full border border-primary/10 bg-primary/5 px-2 py-0.5 text-[10px] font-semibold text-primary">
                {{ contextSourceLabel(record.source) }}
              </span>
              <span class="rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[10px] font-semibold text-gray-600">
                {{ contextStageLabel(record.stage) }}
              </span>
              <span
                v-if="record.origin"
                class="rounded-full border border-amber-100 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700"
              >
                {{ record.origin === "llm" ? "LLM 摘要" : "确定性摘录" }}
              </span>
            </div>
            <span class="font-mono text-[10px] text-gray-400">
              {{ formatContextDate(record.occurred_at) }}
            </span>
          </div>

          <div v-if="contextMetricText(record)" class="text-[11px] font-medium text-gray-600">
            {{ contextMetricText(record) }}
          </div>

          <div class="grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4">
            <div v-if="record.dropped != null" class="rounded-lg border border-gray-100 bg-white px-2.5 py-2">
              <div class="text-gray-400">丢弃消息</div>
              <div class="mt-0.5 font-mono font-semibold text-gray-700">{{ record.dropped }}</div>
            </div>
            <div v-if="record.kept != null" class="rounded-lg border border-gray-100 bg-white px-2.5 py-2">
              <div class="text-gray-400">保留消息</div>
              <div class="mt-0.5 font-mono font-semibold text-gray-700">{{ record.kept }}</div>
            </div>
            <div v-if="record.token_used != null || record.token_budget != null" class="rounded-lg border border-gray-100 bg-white px-2.5 py-2">
              <div class="text-gray-400">Token 使用</div>
              <div class="mt-0.5 whitespace-nowrap font-mono font-semibold text-gray-700">{{ record.token_used ?? "-" }} / {{ record.token_budget ?? "-" }}</div>
            </div>
            <div v-if="record.summary_chars != null" class="rounded-lg border border-gray-100 bg-white px-2.5 py-2">
              <div class="text-gray-400">摘要长度</div>
              <div class="mt-0.5 font-mono font-semibold text-gray-700">{{ record.summary_chars }} 字符</div>
            </div>
          </div>

          <details v-if="record.preview" class="rounded-lg border border-gray-100 bg-white">
            <summary class="cursor-pointer select-none px-3 py-2 text-[11px] font-semibold text-gray-500 hover:text-primary">
              查看摘要预览
            </summary>
            <pre class="whitespace-pre-wrap break-words px-3 pb-3 text-[11px] leading-relaxed text-gray-600">{{ record.preview }}</pre>
          </details>
        </article>
      </div>
    </div>
  </div>
</template>
