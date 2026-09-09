<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  visible: boolean;
  loading: boolean;
  stats: any[];
  expanded: Record<string, boolean>;
}>();

const emit = defineEmits<{
  (event: "close"): void;
  (event: "toggle", callIndex: number): void;
}>();

const formatToolArgs = (args: any): string => {
  if (!args) return "{}";
  if (typeof args === "string") return args;
  try {
    return JSON.stringify(args);
  } catch (error) {
    return String(args);
  }
};

const formatModelCallTime = (isoStr: string): string => {
  try {
    const date = new Date(isoStr);
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    const hours = String(date.getHours()).padStart(2, "0");
    const minutes = String(date.getMinutes()).padStart(2, "0");
    const seconds = String(date.getSeconds()).padStart(2, "0");
    return `${y}-${m}-${d} ${hours}:${minutes}:${seconds}`;
  } catch (error) {
    return isoStr || "";
  }
};

const calcContextUsage = (stat: any): number => {
  const ctx = Number(stat.physical_window || stat.context_size || 0);
  const inp = Number(stat.input_tokens || 0);
  if (!ctx || ctx <= 0) return 0;
  return Math.min((inp / ctx) * 100, 100);
};

const contextUsageBarClass = (usage: number, stat: any = null): string => {
  // 进度条统计的是完整请求输入，因此这里也使用同一口径的请求输入上限。
  const budgetPct = requestInputBudgetPct(stat);
  if (budgetPct !== null && usage > budgetPct) return "bg-red-500";
  if (usage < 70) return "bg-emerald-500";
  if (usage < 90) return "bg-amber-500";
  return "bg-red-500";
};

// 总请求输入安全线在进度条上的百分比位置（request_input_budget / physical_window）。
// 进度条的当前值是完整 input_tokens，不能直接和只针对历史的 history_budget 比较。
const requestInputBudgetPct = (stat: any): number | null => {
  const ctx = Number(stat.physical_window || stat.context_size || 0);
  const budget = Number(stat.request_input_budget || 0);
  if (ctx > 0 && budget > 0 && budget < ctx) {
    return Math.min((budget / ctx) * 100, 100);
  }
  return null;
};

// 1000+ 显示的简洁单位：例如 65536 -> 64k。
const formatTokens = (n: number): string => {
  if (!n || n <= 0) return "0";
  if (n >= 1000000) return `${(n / 1000000).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
};

const contextBreakdownItems = (stat: any) => [
  {
    label: "系统提示词",
    value: Number(stat.context_breakdown?.system_prompt_tokens || 0),
    color: "bg-slate-400",
  },
  {
    label: "工具 schema",
    value: Number(stat.context_breakdown?.tools_tokens || 0),
    color: "bg-violet-400",
  },
  {
    label: "对话消息",
    value: Number(stat.context_breakdown?.conversation_tokens || 0),
    color: "bg-blue-400",
  },
];

const statsSummary = computed(() => {
  const totalDuration = props.stats.reduce((acc: number, cur: any) => acc + (cur.elapsed_ms || 0), 0);
  const totalIn = props.stats.reduce((acc: number, cur: any) => acc + (cur.input_tokens || 0), 0);
  const totalOut = props.stats.reduce((acc: number, cur: any) => acc + (cur.output_tokens || 0), 0);
  const totalCacheIn = props.stats.reduce((acc: number, cur: any) => acc + (cur.cache_input_tokens || 0), 0);
  const hitRate = totalIn > 0 && totalCacheIn > 0 ? Math.round((totalCacheIn / totalIn) * 100) : 0;
  return {
    totalCalls: props.stats.length,
    totalDuration: (totalDuration / 1000).toFixed(2),
    totalIn,
    totalOut,
    totalCacheIn,
    hitRate,
    hasCacheHit: totalCacheIn > 0,
  };
});
</script>

<template>
<!-- Model Call Stats Modal -->
<div
  v-if="visible"
  class="absolute inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
  @click.self="emit('close')"
>
  <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-lg overflow-hidden animate-fade-in-up border border-gray-200 dark:border-gray-700 flex flex-col max-h-[85%]">
    <!-- Header -->
    <div class="px-4 py-3.5 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between bg-gray-50 dark:bg-gray-800/50 shrink-0">
      <div class="flex items-center space-x-2">
        <svg class="w-4 h-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24" :style="{ color: 'var(--primary-color, #1677ff)' }">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 002 2h2a2 2 0 002-2" />
        </svg>
        <h3 class="text-sm font-bold text-gray-800 dark:text-gray-200">大模型调用明细指标</h3>
      </div>
      <button @click="emit('close')" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors cursor-pointer">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- Body -->
    <div class="p-4 overflow-y-auto space-y-4 flex-1">
      <!-- Loading skeleton -->
      <div v-if="loading" class="space-y-3 py-6">
        <div class="h-4 bg-gray-100 dark:bg-gray-700 rounded w-2/3 animate-pulse"></div>
        <div class="space-y-2">
          <div class="h-3 bg-gray-100 dark:bg-gray-700 rounded animate-pulse"></div>
          <div class="h-3 bg-gray-100 dark:bg-gray-700 rounded animate-pulse w-5/6"></div>
          <div class="h-3 bg-gray-100 dark:bg-gray-700 rounded animate-pulse w-4/5"></div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-else-if="stats.length === 0" class="text-center py-8 text-gray-400 dark:text-gray-500 text-sm">
        <svg class="w-12 h-12 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
        </svg>
        暂无此消息的大模型调用明细记录
      </div>

      <!-- Content -->
      <div v-else class="space-y-4">
        <!-- Summary stats -->
        <div class="grid grid-cols-5 gap-1.5 sm:gap-2 text-center">
          <div class="bg-gray-50 dark:bg-gray-900/40 p-2 rounded-lg border border-gray-100/50 dark:border-gray-700/30">
            <div class="text-[10px] text-gray-400 dark:text-gray-500">调用次数</div>
            <div class="text-xs font-bold text-gray-700 dark:text-gray-200 mt-0.5">{{ statsSummary.totalCalls }}</div>
          </div>
          <div class="bg-gray-50 dark:bg-gray-900/40 p-2 rounded-lg border border-gray-100/50 dark:border-gray-700/30">
            <div class="text-[10px] text-gray-400 dark:text-gray-500">总耗时</div>
            <div class="text-xs font-bold text-gray-700 dark:text-gray-200 mt-0.5">{{ statsSummary.totalDuration }}s</div>
          </div>
          <div class="bg-gray-50 dark:bg-gray-900/40 p-2 rounded-lg border border-gray-100/50 dark:border-gray-700/30">
            <div class="text-[10px] text-gray-400 dark:text-gray-500">总输入</div>
            <div class="text-xs font-bold text-gray-700 dark:text-gray-200 mt-0.5">{{ statsSummary.totalIn }}</div>
          </div>
          <div class="bg-gray-50 dark:bg-gray-900/40 p-2 rounded-lg border border-gray-100/50 dark:border-gray-700/30">
            <div class="text-[10px] text-gray-400 dark:text-gray-500">总输出</div>
            <div class="text-xs font-bold text-gray-700 dark:text-gray-200 mt-0.5">{{ statsSummary.totalOut }}</div>
          </div>
          <div
            class="p-2 rounded-lg border transition-colors"
            :class="statsSummary.hasCacheHit
              ? 'bg-emerald-50/60 dark:bg-emerald-950/30 border-emerald-200/80 dark:border-emerald-800/40 text-emerald-600 dark:text-emerald-400'
              : 'bg-gray-50 dark:bg-gray-900/40 border-gray-100/50 dark:border-gray-700/30 text-gray-700 dark:text-gray-200'"
          >
            <div class="text-[10px]" :class="statsSummary.hasCacheHit ? 'text-emerald-600 dark:text-emerald-400 font-medium' : 'text-gray-400 dark:text-gray-500'">缓存命中</div>
            <div class="text-xs font-bold mt-0.5">
              {{ statsSummary.totalCacheIn }}
              <span v-if="statsSummary.hasCacheHit" class="text-[10px] font-normal">({{ statsSummary.hitRate }}%)</span>
            </div>
          </div>
        </div>

        <!-- Detailed logs list -->
        <div class="space-y-3">
          <div
            v-for="(stat, index) in stats"
            :key="index"
            class="bg-gray-50/50 dark:bg-gray-900/20 border border-gray-100 dark:border-gray-700/50 rounded-xl p-3 space-y-2 transition-all hover:shadow-sm"
          >
            <!-- Log header -->
            <div class="flex items-start justify-between">
              <div class="flex flex-col">
                <div class="flex items-center space-x-1.5">
                  <span class="inline-flex items-center justify-center w-5 h-5 text-[10px] font-bold text-white rounded bg-primary/80 shrink-0" :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }">
                    #{{ stat.call_index }}
                  </span>
                  <span class="text-xs font-bold text-gray-700 dark:text-gray-300 max-w-[150px] truncate" :title="stat.agent_name">
                    {{ stat.agent_name }}
                  </span>
                </div>
                <span v-if="stat.timestamp" class="text-[9px] text-gray-400 dark:text-gray-500 mt-1 font-mono">
                  调用时间: {{ formatModelCallTime(stat.timestamp) }}
                </span>
              </div>
              <span class="text-[10px] text-gray-400 dark:text-gray-500 font-mono text-right shrink-0">
                {{ (stat.elapsed_ms / 1000).toFixed(2) }}s ({{ stat.elapsed_ms }}ms)
              </span>
            </div>

            <!-- Log parameters -->
            <div class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              <div class="flex justify-between border-b border-gray-100/50 dark:border-gray-700/20 pb-1">
                <span class="text-gray-400">大模型名称:</span>
                <span class="font-medium text-gray-700 dark:text-gray-300 font-mono text-[11px] truncate max-w-[130px]" :title="stat.model_name">
                  {{ stat.model_name }}
                </span>
              </div>
              <div class="flex justify-between border-b border-gray-100/50 dark:border-gray-700/20 pb-1">
                <span class="text-gray-400">输入信息数:</span>
                <span class="font-medium text-gray-700 dark:text-gray-300">
                  {{ stat.input_message_count }}
                </span>
              </div>
              <div class="flex justify-between items-center border-b border-gray-100/50 dark:border-gray-700/20 pb-1">
                <span class="text-gray-400">输入 Token:</span>
                <span class="font-medium text-gray-700 dark:text-gray-300 font-mono flex items-center">
                  {{ stat.input_tokens }}
                  <span
                    v-if="stat.cache_input_tokens > 0"
                    class="inline-flex items-center px-1.5 py-0.2 rounded text-[10px] font-sans font-medium bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-800/40 ml-1.5"
                    :title="'命中上下文缓存 Token: ' + stat.cache_input_tokens"
                  >
                    命中: {{ stat.cache_input_tokens }} ({{ Math.round((stat.cache_input_tokens / stat.input_tokens) * 100) }}%)
                  </span>
                </span>
              </div>
              <div class="flex justify-between border-b border-gray-100/50 dark:border-gray-700/20 pb-1">
                <span class="text-gray-400">输出 Token:</span>
                <span class="font-medium text-gray-700 dark:text-gray-300 font-mono">
                  {{ stat.output_tokens }}
                </span>
              </div>
            </div>

            <!-- Tool Calls -->
            <div class="pt-1 text-[11px] space-y-1.5">
              <div class="flex items-center space-x-1">
                <span class="text-gray-400 shrink-0">工具调用:</span>
                <span
                  v-if="stat.has_tool_calls && stat.tool_names && stat.tool_names.length > 0"
                  class="inline-flex flex-wrap gap-1"
                >
                  <span
                    v-for="tName in stat.tool_names"
                    :key="tName"
                    class="bg-blue-50 dark:bg-blue-900/30 text-blue-500 border border-blue-100/50 dark:border-blue-800/30 px-1 py-0.5 rounded text-[9px] font-mono"
                  >
                    {{ tName }}
                  </span>
                </span>
                <span v-else-if="stat.has_tools_bound" class="text-gray-400 italic">
                  无（已绑定工具但未调用）
                </span>
                <span v-else class="text-gray-400 italic">
                  无（未绑定工具）
                </span>
              </div>
              <!-- Tool Call Arguments Details -->
              <div v-if="stat.tool_calls && stat.tool_calls.length > 0" class="bg-gray-100/60 dark:bg-gray-950/40 p-2 rounded-lg text-[10px] font-mono text-gray-600 dark:text-gray-400 border border-gray-100 dark:border-gray-800 space-y-1 max-h-[100px] overflow-y-auto">
                <div v-for="(call, cIdx) in stat.tool_calls" :key="cIdx" class="break-all whitespace-pre-wrap">
                  <span class="text-blue-500 dark:text-blue-400 font-bold">{{ call.name }}</span>(<span class="text-gray-600 dark:text-gray-400">{{ formatToolArgs(call.arguments) }}</span>)
                </div>
              </div>
            </div>

            <!-- Context Window Occupancy -->
            <div v-if="stat.physical_window || stat.context_size" class="pt-2 border-t border-gray-100/50 dark:border-gray-700/20">
              <div class="flex items-center justify-between">
                <span class="text-[10px] text-gray-400 dark:text-gray-500">上下文窗口占用</span>
                <span class="text-[10px] font-mono text-gray-500 dark:text-gray-400"
                  >{{ stat.input_tokens || 0 }} / {{ stat.physical_window || stat.context_size }}</span
                >
              </div>
              <div
                class="mt-1 h-2 w-full bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden relative"
                role="progressbar"
                :aria-valuenow="calcContextUsage(stat)"
                aria-valuemin="0"
                aria-valuemax="100"
              >
                <!-- 请求输入安全线：与当前 input_tokens 使用同一条总请求输入口径 -->
                <div
                  v-if="requestInputBudgetPct(stat) !== null"
                  class="absolute top-0 bottom-0 w-[2px] bg-red-500/70 dark:bg-red-400/80 z-10"
                  :style="{ left: requestInputBudgetPct(stat) + '%' }"
                  :title="`请求输入线 ${formatTokens(stat.request_input_budget)}（接近此处将进入历史压缩/输出保护区）`"
                ></div>
                <div
                  class="h-full rounded-full transition-all"
                  :class="contextUsageBarClass(calcContextUsage(stat), stat)"
                  :style="{ width: Math.min(calcContextUsage(stat), 100) + '%' }"
                ></div>
              </div>
              <div class="mt-0.5 flex items-center justify-between text-[9px] font-mono text-gray-400 dark:text-gray-500">
                <span>上下文占用 {{ calcContextUsage(stat).toFixed(2) }}%</span>
                <span
                  v-if="requestInputBudgetPct(stat) !== null"
                  class="text-red-500 dark:text-red-400 font-bold"
                  :title="`完整请求输入安全线 ${formatTokens(stat.request_input_budget)} tokens`"
                  >请求输入线 {{ formatTokens(stat.request_input_budget) }}</span
                >
              </div>
              <div
                v-if="stat.history_budget || stat.context_budget || stat.contains_compaction"
                class="mt-0.5 flex items-center justify-between text-[9px] font-mono text-gray-400 dark:text-gray-500"
              >
                <span
                  v-if="stat.history_budget || stat.context_budget"
                  :title="`只针对历史消息的 compact 判定预算 ${formatTokens(stat.history_budget || stat.context_budget)} tokens`"
                >
                  历史 compact 预算 {{ formatTokens(stat.history_budget || stat.context_budget) }}
                </span>
                <span v-if="stat.contains_compaction" class="text-amber-500 dark:text-amber-400 font-bold">
                  含早前对话裁剪
                </span>
              </div>
              <div
                v-if="stat.completion_reserve_tokens || stat.request_input_budget"
                class="mt-1 flex items-center justify-between text-[9px] font-mono text-gray-400 dark:text-gray-500"
              >
                <span v-if="stat.completion_reserve_tokens">
                  输出预留 {{ formatTokens(stat.completion_reserve_tokens) }}
                </span>
                <span v-if="stat.request_input_budget">
                  请求输入上限 {{ formatTokens(stat.request_input_budget) }}
                </span>
              </div>
            </div>

            <!-- Context composition estimate -->
            <div
              v-if="stat.context_breakdown && stat.context_breakdown.total_tokens > 0"
              class="pt-2 border-t border-gray-100/50 dark:border-gray-700/20"
            >
              <div class="flex items-center justify-between text-[10px] text-gray-400 dark:text-gray-500">
                <span>最近一次请求构成</span>
                <span class="font-mono text-gray-500 dark:text-gray-400">
                  估算 {{ formatTokens(stat.context_breakdown.total_tokens) }}
                  <span v-if="stat.input_tokens" class="text-gray-400">· 实际输入 {{ formatTokens(stat.input_tokens) }}</span>
                </span>
              </div>
              <div class="mt-1.5 space-y-1 text-[10px]">
                <div
                  v-for="item in contextBreakdownItems(stat)"
                  :key="item.label"
                  class="flex items-center justify-between gap-3 text-gray-500 dark:text-gray-400"
                >
                  <span class="flex items-center gap-1.5">
                    <span class="h-1.5 w-1.5 rounded-sm" :class="item.color" aria-hidden="true" />
                    <span>{{ item.label }}</span>
                  </span>
                  <span class="font-mono tabular-nums">{{ formatTokens(item.value) }}</span>
                </div>
              </div>
            </div>

            <!-- Thoughts and Output Text Expansion Panel -->
            <div v-if="stat.reasoning_content || stat.response_text" class="pt-1 border-t border-gray-100/50 dark:border-gray-700/20">
              <button
                @click="emit('toggle', stat.call_index)"
                class="text-[10px] text-primary dark:text-blue-400 hover:underline flex items-center space-x-1 font-bold focus:outline-none cursor-pointer"
              >
                <span>{{ expanded[stat.call_index] ? '收起思考与输出' : '展开思考与输出' }}</span>
                <svg class="w-3 h-3 transform transition-transform" :class="expanded[stat.call_index] ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
                <div v-if="expanded[stat.call_index]" class="mt-2 space-y-2 text-[10px] font-mono">
                <!-- Reasoning/Thought -->
                <div v-if="stat.reasoning_content" class="bg-amber-50/40 dark:bg-amber-950/10 border border-amber-100/50 dark:border-amber-900/20 p-2 rounded-lg text-amber-800 dark:text-amber-300">
                  <div class="font-bold text-[9px] uppercase text-amber-500 mb-1">思考过程 (Thought)</div>
                  <div class="whitespace-pre-wrap leading-relaxed">{{ stat.reasoning_content }}</div>
                </div>
                <!-- Final text output -->
                <div v-if="stat.response_text" class="bg-gray-100/80 dark:bg-gray-950/60 border border-gray-200/50 dark:border-gray-800/40 p-2 rounded-lg text-gray-700 dark:text-gray-300">
                  <div class="font-bold text-[9px] uppercase text-gray-400 mb-1">大模型输出 (Output)</div>
                  <div class="whitespace-pre-wrap leading-relaxed max-h-[200px] overflow-y-auto break-all">{{ stat.response_text }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

</template>
