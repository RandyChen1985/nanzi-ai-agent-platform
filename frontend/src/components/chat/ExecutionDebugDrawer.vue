<template>
  <teleport to="body">
    <div
      class="fixed top-0 right-0 h-full w-full sm:w-[52%] lg:w-[46%] bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-800 shadow-2xl z-[260] flex flex-col transition-transform duration-300 ease-in-out transform"
      :class="visible ? 'translate-x-0' : 'translate-x-full'"
    >
      <!-- Header -->
      <div class="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-gray-800 flex-shrink-0 bg-gray-50/50 dark:bg-gray-800/40">
        <div class="min-w-0">
          <h3 class="text-sm font-bold text-gray-800 dark:text-gray-100 truncate">执行详情 · 调试观察台</h3>
          <p class="text-[11px] text-gray-400 mt-0.5 truncate font-mono">{{ traceId || "未关联 Trace" }}</p>
        </div>
        <button
          type="button"
          class="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors flex-shrink-0"
          title="关闭"
          @click="emit('update:visible', false)"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <!-- Tabs -->
      <div class="flex items-center gap-1 px-3 pt-3 pb-0 border-b border-gray-100 dark:border-gray-800 flex-shrink-0">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          @click="activeTab = tab.key"
          class="px-3 py-1.5 text-xs font-medium rounded-lg transition-colors"
          :class="activeTab === tab.key
            ? 'bg-primary/10 text-primary'
            : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'"
        >
          {{ tab.label }}
        </button>
      </div>

      <!-- Body -->
      <div class="flex-1 min-h-0 overflow-auto custom-scrollbar">
        <!-- Tab: 执行步骤 -->
        <div v-if="activeTab === 'steps'" class="p-4 space-y-3">
          <div v-if="loading" class="py-8 text-center text-xs text-gray-400 italic">正在加载执行步骤…</div>
          <div v-else-if="error" class="py-6 text-center text-xs text-red-500">{{ error }}</div>
          <template v-else-if="steps.length > 0">
            <p class="text-[11px] text-gray-400">共 {{ steps.length }} 步 · 数据来自 /chat/logs 执行日志</p>
            <div
              v-for="(step, idx) in steps"
              :key="idx"
              class="border border-gray-100 dark:border-gray-800 rounded-xl overflow-hidden"
            >
              <!-- Step 摘要头 -->
              <button
                type="button"
                @click="toggleStep(idx)"
                class="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
              >
                <svg
                  class="w-3.5 h-3.5 text-gray-400 transition-transform"
                  :class="expanded.steps[idx] ? 'rotate-90' : ''"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                </svg>
                <span class="text-[9px] font-black px-1.5 py-0.5 rounded uppercase shrink-0" :class="eventBadgeClass(step.event_type)">
                  {{ step.event_type }}
                </span>
                <span v-if="step.tool_name" class="text-xs font-semibold text-gray-700 dark:text-gray-200 truncate">{{ step.tool_name }}</span>
                <span v-else-if="step.agent_name" class="text-xs text-gray-500 truncate">{{ step.agent_name }}</span>
                <span class="ml-auto flex items-center gap-2 shrink-0">
                  <span v-if="step.status === 'error'" class="text-[9px] px-1.5 py-0.5 rounded bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-300 font-bold">ERROR</span>
                  <span class="text-[10px] text-gray-400 font-mono">{{ step.execution_time_ms ? `${step.execution_time_ms.toFixed(0)}ms` : '' }}</span>
                </span>
              </button>

              <div v-show="expanded.steps[idx]" class="border-t border-gray-100 dark:border-gray-800 p-3 space-y-3">
                <!-- 工具入参 -->
                <div v-if="hasPayload(step.tool_input)">
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-[10px] font-bold text-blue-600 dark:text-blue-400 uppercase tracking-wide">工具入参 Input</span>
                    <button type="button" @click="copyText(step.tool_input)" class="text-[10px] text-gray-400 hover:text-gray-600 flex items-center gap-1">
                      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3"/></svg>
                      复制
                    </button>
                  </div>
                  <pre class="text-[11px] font-mono text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-all bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-800 rounded-lg p-2 max-h-72 overflow-auto">{{ formatJson(step.tool_input) }}</pre>
                </div>

                <!-- 工具出参 -->
                <div v-if="hasPayload(step.tool_output)">
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-[10px] font-bold text-green-600 dark:text-green-400 uppercase tracking-wide">工具出参 Output</span>
                    <span v-if="isTruncated(step.tool_output)" class="text-[9px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-600 dark:bg-amber-900/40 dark:text-amber-300 font-bold">已截断 &gt;64KB</span>
                    <button type="button" @click="copyText(step.tool_output)" class="text-[10px] text-gray-400 hover:text-gray-600 flex items-center gap-1">
                      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3"/></svg>
                      复制
                    </button>
                  </div>
                  <pre class="text-[11px] font-mono text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-all bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-800 rounded-lg p-2 max-h-72 overflow-auto">{{ formatJson(step.tool_output) }}</pre>
                </div>

                <!-- 出参亲和文本（dict 含 content 时优先展示文本） -->
                <div v-if="outputText(step.tool_output) && !formatJson(step.tool_output).startsWith('{')" class="text-xs text-gray-600 dark:text-gray-300 leading-relaxed break-words">
                  {{ outputText(step.tool_output) }}
                </div>

                <!-- 错误信息 -->
                <div v-if="step.error_message" class="text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-100 dark:border-red-900/40 rounded-lg p-2 break-words">
                  {{ step.error_message }}
                </div>

                <div class="flex items-center gap-2 text-[10px] text-gray-400 flex-wrap">
                  <span v-if="step.model" class="font-mono">model: {{ step.model }}</span>
                  <span v-if="step.step_number" class="font-mono">#{{ step.step_number }}</span>
                  <span v-if="step.status" class="font-mono">status: {{ step.status }}</span>
                </div>
              </div>
            </div>
          </template>
          <div v-else class="py-6 text-center text-xs text-gray-400 italic">暂无执行步骤</div>
        </div>

        <!-- Tab: 组装 Prompt -->
        <div v-if="activeTab === 'prompt'" class="p-4 space-y-3">
          <div class="flex items-start gap-2 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-900/40 px-3 py-2">
            <span class="text-xs text-blue-700 dark:text-blue-300 leading-relaxed">
              ⚠️ 此为管道「组装阶段」快照：首条 System 为平台/Agent 组装完成的<b>全量系统提示词</b>（含历史边界），其后为对话消息序列。不含本轮实时工具结果与后续 ReAct 动态追加块。
            </span>
          </div>

          <template v-if="Array.isArray(promptMessages) && promptMessages.length">
            <div
              v-for="(pm, idx) in promptMessages"
              :key="idx"
              class="border border-gray-100 dark:border-gray-800 rounded-lg overflow-hidden"
            >
              <div class="flex items-center gap-1 px-3 py-1.5 bg-gray-50 dark:bg-gray-800/50 border-b border-gray-100 dark:border-gray-800">
                <button
                  type="button"
                  class="flex flex-1 items-center gap-2 min-w-0 text-left rounded hover:opacity-80 transition-opacity"
                  @click="togglePrompt(idx)"
                  :title="isPromptExpanded(idx) ? '点击收起' : '点击展开'"
                >
                  <svg
                    class="w-3.5 h-3.5 text-gray-400 transition-transform shrink-0"
                    :class="isPromptExpanded(idx) ? 'rotate-90' : ''"
                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                  </svg>
                  <span class="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0" :class="roleBadgeClass(pm.role)">
                    {{ roleLabel(pm.role) }}
                  </span>
                  <span v-if="pm._isFullSystemPrev" class="text-[9px] font-bold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300 shrink-0">全量系统提示词</span>
                </button>
                <span class="text-[10px] text-gray-400 font-mono shrink-0">#{{ idx }}</span>
                <span
                  class="text-[10px] text-gray-400 font-mono shrink-0"
                  :title="'此条消息估算 ' + promptTokens(pm).toLocaleString() + ' token（中文字符 ×0.8 + 其余字符 ×0.25）'"
                >
                  ≈{{ promptTokens(pm).toLocaleString() }} tok
                </span>
                <button
                  type="button"
                  class="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors shrink-0"
                  :title="copyLabel(pm)"
                  @click.stop="copyPromptMsg(pm)"
                >
                  <svg v-if="!copiedIdx.has(idx)" class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h9a2 2 0 002-2v-1m-2-4h3a2 2 0 002-2V6a2 2 0 00-2-2h-3a2 2 0 00-2 2v3m0 0a2 2 0 01-2 2H9a2 2 0 01-2-2"/></svg>
                  <svg v-else class="w-3 h-3 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                  <span>{{ copiedIdx.has(idx) ? "已复制" : "复制" }}</span>
                </button>
              </div>
              <pre v-if="isPromptExpanded(idx)" class="text-[11px] font-mono text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-all p-3 max-h-80 overflow-auto">{{ formatPromptContent(pm) }}</pre>
              <div v-else @click="togglePrompt(idx)" class="px-3 py-2 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/40 transition-colors" title="点击展开">
                <p class="text-[11px] font-mono text-gray-400 dark:text-gray-500 truncate">{{ promptPreview(pm) }}</p>
              </div>
            </div>
          </template>
          <div v-else class="py-6 text-center text-xs text-gray-400 italic">该轮未捕获组装 Prompt（可开启「返回原始 Prompt」后重新提问）</div>
        </div>

        <!-- Tab: 运行时上下文 -->
        <div v-if="activeTab === 'context'" class="p-4 space-y-3">
          <p class="text-[11px] text-gray-400">运行中由流式事件推送的上下文/实体快照（仅当前轮存在，刷新后需重新提问复现）。</p>

          <template v-if="contextEntries.length">
            <div
              v-for="(entry, idx) in contextEntries"
              :key="idx"
              class="border border-gray-100 dark:border-gray-800 rounded-lg overflow-hidden"
            >
              <button
                type="button"
                @click="expanded.context[idx] = !expanded.context[idx]"
                class="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
              >
                <svg class="w-3.5 h-3.5 text-gray-400 transition-transform" :class="expanded.context[idx] ? 'rotate-90' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                </svg>
                <span class="text-xs font-semibold text-gray-700 dark:text-gray-200">{{ entry.key }}</span>
                <span class="text-[10px] text-gray-400 ml-auto">{{ entry.typeLabel }}</span>
              </button>
              <div v-show="expanded.context[idx]" class="border-t border-gray-100 dark:border-gray-800 p-3">
                <pre class="text-[11px] font-mono text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-all bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-800 rounded-lg p-2 max-h-72 overflow-auto">{{ formatJson(entry.value) }}</pre>
              </div>
            </div>
          </template>
          <div v-else class="py-6 text-center text-xs text-gray-400 italic">暂无运行时上下文数据</div>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, reactive } from "vue";
import axios from "axios";

interface ExecutionStep {
  step_number?: number;
  event_type?: string;
  agent_name?: string;
  model?: string;
  tool_name?: string;
  tool_input?: unknown;
  tool_output?: unknown;
  execution_time_ms?: number;
  status?: string;
  error_message?: string;
}

const props = defineProps<{
  visible: boolean;
  traceId?: string;
  rawPrompt?: unknown;
  rawPromptSystem?: string;
  agentContext?: Record<string, unknown>;
}>();

const emit = defineEmits<{
  (e: "update:visible", value: boolean): void;
}>();

const activeTab = ref<"steps" | "prompt" | "context">("steps");
const expanded = reactive<{ steps: Record<number, boolean>; context: Record<number, boolean> }>({
  steps: {},
  context: {},
});

const tabs = [
  { key: "steps", label: "执行步骤" },
  { key: "prompt", label: "组装 Prompt" },
  { key: "context", label: "运行时上下文" },
] as const;

const steps = ref<ExecutionStep[]>([]);
const loading = ref(false);
const error = ref("");

const fetchSteps = async () => {
  if (!props.traceId) return;
  loading.value = true;
  error.value = "";
  try {
    const key = localStorage.getItem("api_key");
    const res = await axios.get(`/api/v1/chat/logs/${encodeURIComponent(props.traceId)}`, {
      headers: key ? { "X-API-Key": key } : undefined,
    });
    const data = res.data?.data ?? res.data;
    if (data && Array.isArray(data.steps)) {
      steps.value = data.steps;
      expanded.steps = {};
      data.steps.forEach((_: unknown, i: number) => {
        expanded.steps[i] = i < 6;
      });
    } else {
      steps.value = [];
    }
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || "加载执行步骤失败";
    steps.value = [];
  } finally {
    loading.value = false;
  }
};

watch(
  () => [props.visible, props.traceId] as const,
  ([visible, traceId]) => {
    if (visible && traceId) {
      void fetchSteps();
    }
    if (!visible) {
      activeTab.value = "steps";
    }
    if (visible && promptMessages.value.length > 0) {
      // 每次打开抽屉，默认展开首条组装 Prompt（通常即全量系统提示词）
      expandedPrompt.value = new Set([0]);
    } else if (!visible) {
      expandedPrompt.value = new Set();
    }
  },
  { immediate: true },
);

const toggleStep = (idx: number) => {
  expanded.steps[idx] = !expanded.steps[idx];
};

const hasPayload = (p: unknown): boolean => {
  if (p == null) return false;
  if (typeof p === "object") return Object.keys(p as object).length > 0;
  return String(p).length > 0;
};

const formatJson = (p: unknown): string => {
  if (p == null) return "";
  if (typeof p === "string") return p;
  try {
    return JSON.stringify(p, null, 2);
  } catch {
    return String(p);
  }
};

const isTruncated = (p: unknown): boolean => {
  return !!(
    p && typeof p === "object" &&
    (p as any).__audit_trace &&
    (p as any).__audit_trace.truncated
  );
};

const outputText = (p: unknown): string => {
  if (p && typeof p === "object") {
    const anyP = p as any;
    // 非 dict/list 被包成 {"raw": "..."}
    if ("raw" in anyP && Object.keys(anyP).length === 1 && typeof anyP.raw === "string") {
      return anyP.raw;
    }
    if (typeof anyP.content === "string") return anyP.content;
    if (typeof anyP.text === "string") return anyP.text;
  }
  return "";
};

const copyText = async (p: unknown) => {
  const txt = formatJson(p);
  try {
    await navigator.clipboard.writeText(txt);
  } catch {
    /* noop */
  }
};

const copiedIdx = ref<Set<number>>(new Set());
let copyTimer: ReturnType<typeof setTimeout> | null = null;
const copyPromptMsg = async (pm: any) => {
  const txt = formatPromptContent(pm);
  try {
    await navigator.clipboard.writeText(txt);
  } catch {
    /* noop */
  }
  const key = promptMessages.value.indexOf(pm);
  if (key > -1) {
    copiedIdx.value = new Set([...copiedIdx.value, key]);
    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(() => {
      copiedIdx.value = new Set();
    }, 1500);
  }
};
const copyLabel = (pm: any): string => {
  return copiedIdx.value.has(promptMessages.value.indexOf(pm)) ? "已复制到剪贴板" : "复制此条内容";
};

const promptPreview = (pm: any): string => {
  const raw = formatPromptContent(pm);
  const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  return lines[0] || raw.slice(0, 60) || "(空内容)";
};

// 与 PromptStudio 保持一致的估算：中文字符 ~0.8 token，其余 ~0.25 token
const promptTokens = (pm: any): number => {
  const text = formatPromptContent(pm);
  const cnChars = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
  const enChars = text.length - cnChars;
  return Math.ceil(cnChars * 0.8 + enChars * 0.25);
};

const expandedPrompt = ref<Set<number>>(new Set());
const togglePrompt = (idx: number) => {
  const next = new Set(expandedPrompt.value);
  if (next.has(idx)) next.delete(idx);
  else next.add(idx);
  expandedPrompt.value = next;
};
const isPromptExpanded = (idx: number): boolean => expandedPrompt.value.has(idx);

const eventBadgeClass = (ev?: string): string => {
  switch (ev) {
    case "tool_call":
      return "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300";
    case "tool_result":
      return "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300";
    case "model_call":
      return "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300";
    case "thought":
      return "bg-gray-100 text-gray-600 dark:bg-gray-800/60 dark:text-gray-300";
    case "error":
      return "bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-300";
    case "final_answer":
      return "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
    default:
      return "bg-gray-100 text-gray-600 dark:bg-gray-800/60 dark:text-gray-300";
  }
};

const roleBadgeClass = (role?: string): string => {
  switch (role) {
    case "system":
      return "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300";
    case "user":
      return "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300";
    case "tool":
      return "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300";
    default:
      return "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300";
  }
};

const roleLabel = (role?: string): string => {
  const map: Record<string, string> = {
    system: "System",
    user: "User",
    assistant: "Assistant",
    tool: "Tool",
  };
  return map[role || ""] || role || "message";
};

const formatPromptContent = (pm: any): string => {
  if (typeof pm?.content === "string") return pm.content;
  if (pm?.content != null) return formatJson(pm.content);
  if (pm?.tool_calls != null) return formatJson(pm.tool_calls);
  return formatJson(pm);
};

const promptMessages = computed(() => {
  const messages = Array.isArray(props.rawPrompt) ? [...props.rawPrompt] : [];
  const system = props.rawPromptSystem;
  if (typeof system === "string" && system.trim().length > 0) {
    return [
      { role: "system", content: system, _isFullSystemPrev: true },
      ...messages,
    ];
  }
  return messages;
});

const contextEntries = computed(() => {
  const ctx = props.agentContext;
  if (!ctx || typeof ctx !== "object") return [];
  return Object.entries(ctx).map(([key, value]) => ({
    key,
    value,
    typeLabel: typeof value,
  }));
});
</script>