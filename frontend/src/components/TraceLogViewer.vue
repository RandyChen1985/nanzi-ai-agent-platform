<script setup lang="ts">
import { ref, watch, computed } from "vue";
import axios from "@/utils/axios";
import {
  CommandLineIcon,
  CpuChipIcon,
  ChatBubbleBottomCenterTextIcon,
  ExclamationCircleIcon,
  CheckCircleIcon,
  ClipboardIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  XMarkIcon,
  ChatBubbleLeftRightIcon,
  ClockIcon,
  ListBulletIcon,
} from "@heroicons/vue/24/outline";
import { copyToClipboard as copyText } from "@/utils/clipboard";
import {
  formatSubagentTraceSummary,
  normalizeSubagentTraceMeta,
  subagentDisplayName,
} from "@/utils/subagentTrace";

const props = defineProps<{
  traceId: string;
  visible: boolean;
}>();

const emit = defineEmits(["close"]);

const loading = ref(false);
const traceData = ref<any>(null);
const error = ref("");
const copiedId = ref<string | null>(null);
const expandedSteps = ref<Record<string, boolean>>({});
const viewMode = ref<"list" | "timeline">("list");

// Initialize expanded state for all steps (default expanded or collapsed?)
// Let's default to expanded for short logs, but maybe collapsible individual sections.
const toggleSection = (id: string) => {
  expandedSteps.value[id] = !expandedSteps.value[id];
};

const copyToClipboard = async (text: any, id: string) => {
  const content =
    typeof text === "string" ? text : JSON.stringify(text, null, 2);
  const ok = await copyText(content);
  if (!ok) {
    console.error("Failed to copy");
    return;
  }
  copiedId.value = id;
  setTimeout(() => {
    copiedId.value = null;
  }, 2000);
};

const fetchLogs = async () => {
  if (!props.traceId) {
    return;
  }

  loading.value = true;
  error.value = "";
  traceData.value = null;

  try {
    const res = await axios.get(`/api/portal/audit/traces/${props.traceId}/spans`);
    const payload = res.data.data || res.data;

    const stepsList: any[] = [];
    const flatten = (arr: any[], depth = 0) => {
      if (!arr) return;
      arr.forEach((item) => {
        stepsList.push({
          ...item,
          _depth: depth,
        });
        if (item.children && item.children.length) {
          flatten(item.children, depth + 1);
        }
      });
    };
    flatten(payload.spans || []);

    traceData.value = {
      trace_id: payload.trace_id,
      steps: stepsList,
      raw_spans: payload.spans
    };

    if (traceData.value && traceData.value.steps) {
      traceData.value.steps.forEach((step: any, idx: number) => {
        expandedSteps.value[`input-${idx}`] = true;
        expandedSteps.value[`output-${idx}`] = !step.tool_output?.__audit_trace?.truncated;
        expandedSteps.value[`raw-${idx}`] = false;
      });
    }
  } catch (e: any) {
    console.error("fetchLogs: Error occurred:", e);
    error.value = e.message || "获取日志失败";
  } finally {
    loading.value = false;
  }
};

watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      fetchLogs();
    }
  }
);

const timelineSteps = computed(() => {
  if (!traceData.value?.steps?.length) return [];

  const steps = traceData.value.steps;

  const parsedSteps = steps.map((step: any) => {
    const timeVal = step.created_at || step.timestamp;
    const startTime = timeVal ? new Date(timeVal).getTime() : Date.now();
    const duration = step.execution_time_ms || 0;
    const endTime = startTime + duration;
    return {
      ...step,
      _startTime: startTime,
      _endTime: endTime,
      _duration: duration,
    };
  });

  const minTime = Math.min(...parsedSteps.map((s: any) => s._startTime));
  const maxTime = Math.max(...parsedSteps.map((s: any) => s._endTime));
  const totalDuration = maxTime - minTime || 1;

  return parsedSteps.map((step: any) => {
    const startOffset = step._startTime - minTime;
    const leftPercent = (startOffset / totalDuration) * 100;
    const widthPercent = Math.max((step._duration / totalDuration) * 100, 0.5);

    return {
      ...step,
      _style: {
        left: `${leftPercent}%`,
        width: `${widthPercent}%`,
      },
    };
  });
});

const totalExecutionTime = computed(() => {
  if (!timelineSteps.value.length) return 0;
  const minTime = Math.min(
    ...timelineSteps.value.map((s: any) => s._startTime)
  );
  const maxTime = Math.max(...timelineSteps.value.map((s: any) => s._endTime));
  return maxTime - minTime;
});

const escapeHtml = (unsafe: unknown): string => {
  return String(unsafe ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

const highlightJsonHtml = (val: unknown): string => {
  if (val == null) return '<span class="text-slate-400">null</span>'
  let obj = val
  if (typeof val === 'string') {
    const trimmed = val.trim()
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        obj = JSON.parse(trimmed)
      } catch {
        return escapeHtml(val)
      }
    } else {
      return escapeHtml(val)
    }
  }

  const jsonStr = JSON.stringify(obj, null, 2)
  const escaped = escapeHtml(jsonStr)
  return escaped.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      let cls = 'text-amber-300' // number
      if (/^&quot;/.test(match) || /^"/.test(match)) {
        if (/:$/.test(match) || /&quot;:$/.test(match)) {
          cls = 'text-cyan-300 font-semibold' // key
        } else {
          cls = 'text-emerald-300' // string
        }
      } else if (/true|false/.test(match)) {
        cls = 'text-purple-300 font-semibold' // boolean
      } else if (/null/.test(match)) {
        cls = 'text-slate-500 italic' // null
      }
      return `<span class="${cls}">${match}</span>`
    }
  )
}

const getEventIcon = (type: string) => {
  switch (type) {
    case "intent_recognition":
    case "agent_execution":
      return CpuChipIcon;
    case "tool_call":
      return CommandLineIcon;
    case "final_answer":
      return ChatBubbleBottomCenterTextIcon;
    case "error":
      return ExclamationCircleIcon;
    default:
      return CheckCircleIcon;
  }
};

const getEventLabel = (type: string) => {
  const map: Record<string, string> = {
    intent_recognition: "意图识别",
    thought: "思考过程",
    tool_call: "调用工具",
    tool_result: "工具响应",
    synthesis: "总结生成",
    final_answer: "最终回复",
    error: "发生错误",
    agent_execution: "智能体执行",
  };
  return map[type] || type;
};

const localizeToolName = (name: string) => {
  const map: Record<string, string> = {
    get_dataset_schema: "检索数据集定义",
    execute_sql_query: "执行 SQL 查询",
    update_dashboard_context: "更新看板关联状态",
  };
  return map[name] || name;
};

const formatStepTime = (iso?: string) => {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
};

const getSubagentMeta = (step: any) =>
  normalizeSubagentTraceMeta(step?.meta_info?.subagent);
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-[100] overflow-hidden text-left"
      style="z-index: 9999"
    >
      <div
        class="absolute inset-0 bg-gray-900/40 backdrop-blur-sm transition-opacity"
        @click="emit('close')"
      ></div>

      <div
        class="absolute inset-y-0 right-0 max-w-3xl w-full bg-white shadow-2xl flex flex-col transform transition-transform duration-300 ease-in-out slide-in-right border-l border-gray-100"
      >
        <!-- Header -->
        <div
          class="px-6 py-4 border-b border-gray-100 flex items-center justify-between bg-white z-10"
        >
          <div>
            <h2 class="text-xl font-bold text-gray-900 tracking-tight">
              执行链路追踪
            </h2>
            <p
              class="text-xs text-gray-500 font-mono mt-1 flex items-center space-x-2"
            >
              <span
                class="bg-gray-100 px-2 py-0.5 rounded text-gray-600 select-all"
                >{{ traceId }}</span
              >
              <span v-if="traceData" class="text-gray-400">|</span>
              <span v-if="traceData" class="flex items-center text-gray-500">
                <ClockIcon class="w-3 h-3 mr-1" />
                {{ totalExecutionTime.toFixed(0) }}ms
              </span>
            </p>
          </div>

          <div class="flex items-center space-x-3">
            <!-- View Toggle -->
            <div
              class="bg-gray-100 p-1 rounded-lg flex items-center text-sm font-medium text-gray-500"
            >
              <button
                @click="viewMode = 'list'"
                class="px-3 py-1.5 rounded-md flex items-center space-x-1.5 transition-all"
                :class="
                  viewMode === 'list'
                    ? 'bg-white text-gray-900 shadow-sm'
                    : 'hover:text-gray-700'
                "
              >
                <ListBulletIcon class="w-4 h-4" />
                <span>列表</span>
              </button>
              <button
                @click="viewMode = 'timeline'"
                class="px-3 py-1.5 rounded-md flex items-center space-x-1.5 transition-all"
                :class="
                  viewMode === 'timeline'
                    ? 'bg-white text-gray-900 shadow-sm'
                    : 'hover:text-gray-700'
                "
              >
                <ClockIcon class="w-4 h-4" />
                <span>时间轴</span>
              </button>
            </div>

            <div class="h-6 w-px bg-gray-200"></div>

            <button
              @click="emit('close')"
              class="p-2 hover:bg-gray-100 rounded-full transition-colors text-gray-500 hover:text-gray-900"
            >
              <XMarkIcon class="w-6 h-6" />
            </button>
          </div>
        </div>

        <!-- Content -->
        <div
          class="flex-1 overflow-y-auto bg-gray-50/50 custom-scrollbar relative"
        >
          <div
            v-if="loading"
            class="flex flex-col items-center justify-center h-full text-gray-400 space-y-3"
          >
            <div
              class="w-10 h-10 border-3 border-indigo-600 border-t-transparent rounded-full animate-spin"
            ></div>
            <p class="text-sm font-medium text-gray-500">正在分析执行链路...</p>
          </div>

          <div
            v-else-if="error"
            class="flex flex-col items-center justify-center h-full text-red-500 space-y-4"
          >
            <div class="bg-red-50 p-4 rounded-full">
              <ExclamationCircleIcon class="w-10 h-10" />
            </div>
            <div class="text-center">
              <p class="font-medium">无法加载日志</p>
              <p class="text-sm opacity-80 mt-1">{{ error }}</p>
            </div>
            <button
              @click="fetchLogs"
              class="px-4 py-2 bg-white border border-red-200 text-red-600 rounded-lg hover:bg-red-50 transition-colors text-sm font-medium shadow-sm"
            >
              重试
            </button>
          </div>

          <template v-else-if="traceData">
            <!-- LIST VIEW -->
            <div v-if="viewMode === 'list'" class="p-8 relative pl-6 pr-6">
              <!-- Timeline Line -->
              <div
                class="absolute left-[35px] top-6 bottom-6 w-0.5 bg-gray-200/70"
              ></div>

              <div
                v-for="(step, index) in traceData.steps"
                :key="index"
                class="relative mb-8 group last:mb-0"
                :class="{ 'animate-pulse-subtle': step.status === 'pending' }"
                :style="{ marginLeft: `${(step._depth || 0) * 24}px` }"
              >
                <!-- Timeline Icon -->
                <div
                  class="absolute -left-[32px] top-0 w-10 h-10 rounded-full border-4 border-gray-50 bg-white shadow-sm flex items-center justify-center z-10 transition-all group-hover:scale-110 group-hover:shadow-md ring-1"
                  :class="[
                    step.status === 'error' ? 'text-red-500 ring-red-100' :
                    step.status === 'pending' ? 'text-amber-500 ring-amber-100 animate-spin-slow' :
                    'text-indigo-600 ring-indigo-50',
                    step.event_type === 'thought' ? 'bg-slate-50' : 'bg-white'
                  ]"
                >
                  <component
                    :is="getEventIcon(step.event_type)"
                    class="w-5 h-5"
                  />
                </div>

                <!-- Shimmer Effect for Pending -->
                <div
                  v-if="step.status === 'pending'"
                  class="absolute inset-0 pointer-events-none z-20 overflow-hidden rounded-xl"
                >
                  <div class="shimmer-overlay"></div>
                </div>

                <!-- Card -->
                <div
                  class="bg-white rounded-xl border transition-all duration-300 overflow-hidden ml-4 relative"
                  :class="[
                    step.event_type === 'thought' ? 'border-dashed border-gray-200 shadow-none opacity-90' : 'border-gray-200/75 shadow-sm hover:shadow-md',
                    step.event_type === 'synthesis' ? 'ring-2 ring-emerald-500/10 border-emerald-100' : '',
                    step.status === 'error' ? 'border-red-100 ring-4 ring-red-500/5' : ''
                  ]"
                >
                  <!-- Card Header -->
                  <div
                    class="px-5 py-3.5 border-b border-gray-100 flex items-center justify-between"
                    :class="step.event_type === 'thought' ? 'bg-gray-50/50' : 'bg-white'"
                  >
                    <div class="flex items-center space-x-3">
                      <div class="flex items-center space-x-2">
                        <span
                          v-if="step.event_type === 'synthesis'"
                          class="px-1.5 py-0.5 rounded bg-emerald-500 text-white text-[9px] font-black uppercase tracking-wider"
                        >
                          Final
                        </span>
                        <span
                          v-else-if="step.event_type === 'thought'"
                          class="px-1.5 py-0.5 rounded bg-slate-200 text-slate-600 text-[9px] font-black uppercase tracking-wider"
                        >
                          Thought
                        </span>
                        <span
                          v-else
                          class="px-1.5 py-0.5 rounded bg-indigo-600 text-white text-[9px] font-black uppercase tracking-wider"
                        >
                          Action
                        </span>
                        <span class="text-sm font-bold flex items-center" :class="step.event_type === 'thought' ? 'text-slate-500' : 'text-gray-900'">
                          <span v-if="(step._depth || 0) > 0" class="text-gray-400 font-normal mr-1">↳</span>
                          <span>{{ getEventLabel(step.event_type) }}</span>
                        </span>
                      </div>

                      <span
                        v-if="step.tool_name"
                        class="flex items-center space-x-1.5 px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded-md text-xs font-bold border border-indigo-100 shadow-sm"
                      >
                        <CommandLineIcon class="w-3 h-3" />
                        <span>{{ localizeToolName(step.tool_name) }}</span>
                      </span>

                      <span
                        v-if="getSubagentMeta(step)"
                        class="flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border bg-indigo-50 text-indigo-600 border-indigo-100"
                        :title="formatSubagentTraceSummary(getSubagentMeta(step))"
                      >
                        <span>子代理 · {{ subagentDisplayName(getSubagentMeta(step)) }}</span>
                      </span>

                      <!-- Model Badge -->
                      <span
                        v-if="step.model"
                        class="flex items-center space-x-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium border bg-gray-50 text-gray-500 border-gray-100"
                      >
                        <span>{{ step.model }}</span>
                      </span>
                    </div>

                    <div class="flex items-center space-x-2.5">
                      <span
                        v-if="formatStepTime(step.timestamp || step.created_at)"
                        class="text-xs text-gray-400 font-mono font-medium"
                        :title="step.timestamp || step.created_at"
                      >
                        {{ formatStepTime(step.timestamp || step.created_at) }}
                      </span>
                      <span
                        v-if="formatStepTime(step.timestamp || step.created_at) && step.execution_time_ms"
                        class="text-gray-300 text-xs"
                      >·</span>
                      <div v-if="step.status === 'pending'" class="flex items-center space-x-1 text-amber-500">
                        <div class="w-1.5 h-1.5 bg-amber-500 rounded-full animate-ping"></div>
                        <span class="text-[10px] font-bold uppercase tracking-widest">Processing</span>
                      </div>
                      <span
                        v-if="step.execution_time_ms"
                        class="text-xs text-gray-400 font-mono"
                      >
                        {{ step.execution_time_ms.toFixed(0) }}ms
                      </span>
                    </div>
                  </div>

                  <!-- Card Body -->
                  <div class="p-5 space-y-4">
                    <div
                      v-if="getSubagentMeta(step)"
                      class="rounded-lg border border-indigo-100 bg-indigo-50/60 p-3 space-y-2"
                    >
                      <div class="flex items-center justify-between gap-2">
                        <p class="text-xs font-bold text-indigo-700">子代理委派</p>
                        <span class="text-[10px] text-indigo-500">
                          {{ formatSubagentTraceSummary(getSubagentMeta(step)) }}
                        </span>
                      </div>
                      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px] text-indigo-900/70">
                        <template v-if="getSubagentMeta(step)?.run_id">
                          <dt class="font-semibold">Run ID</dt>
                          <dd class="font-mono break-all">{{ getSubagentMeta(step)?.run_id }}</dd>
                        </template>
                        <template v-if="getSubagentMeta(step)?.parent_trace_id">
                          <dt class="font-semibold">父 Trace</dt>
                          <dd class="font-mono break-all">{{ getSubagentMeta(step)?.parent_trace_id }}</dd>
                        </template>
                        <template v-if="getSubagentMeta(step)?.child_trace_id">
                          <dt class="font-semibold">子 Trace</dt>
                          <dd class="font-mono break-all">{{ getSubagentMeta(step)?.child_trace_id }}</dd>
                        </template>
                        <template v-if="getSubagentMeta(step)?.stop_reason">
                          <dt class="font-semibold">停止原因</dt>
                          <dd>{{ getSubagentMeta(step)?.stop_reason }}</dd>
                        </template>
                        <template v-if="getSubagentMeta(step)?.tool_filter?.length">
                          <dt class="font-semibold">工具过滤</dt>
                          <dd class="break-all">{{ getSubagentMeta(step)?.tool_filter?.join('、') }}</dd>
                        </template>
                      </dl>
                    </div>
                    <!-- Tool Input -->
                    <div v-if="step.tool_input" class="space-y-2">
                      <div
                        class="flex items-center justify-between cursor-pointer group/header select-none"
                        @click="toggleSection(`input-${index}`)"
                      >
                        <p
                          class="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center space-x-1"
                        >
                          <ChevronRightIcon :class="['w-3 h-3 transition-transform', expandedSteps[`input-${index}`] ? 'rotate-90' : '']" />
                          <span>Parameters</span>
                        </p>
                        <button
                          @click.stop="copyToClipboard(step.tool_input, `input-${index}`)"
                          class="text-gray-400 hover:text-indigo-600 transition-colors"
                        >
                          <ClipboardIcon v-if="copiedId !== `input-${index}`" class="w-3.5 h-3.5" />
                          <span v-else class="text-[10px] text-green-600 font-bold uppercase">Copied</span>
                        </button>
                      </div>

                      <div v-if="expandedSteps[`input-${index}`]" class="rounded-lg overflow-hidden border border-gray-900 bg-[#0d1117]">
                        <pre class="text-[11px] font-mono p-3 text-slate-100 overflow-x-auto leading-relaxed" v-html="highlightJsonHtml(step.tool_input)" />
                      </div>
                    </div>

                    <!-- Tool Output -->
                    <div v-if="step.tool_output" class="space-y-2">
                      <div
                        class="flex items-center justify-between cursor-pointer group/header select-none"
                        @click="toggleSection(`output-${index}`)"
                      >
                        <p
                          class="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center space-x-1"
                        >
                          <ChevronRightIcon :class="['w-3 h-3 transition-transform', expandedSteps[`output-${index}`] ? 'rotate-90' : '']" />
                          <span>Result</span>
                        </p>
                        <button
                          @click.stop="copyToClipboard(step.tool_output, `output-${index}`)"
                          class="text-gray-400 hover:text-emerald-600 transition-colors"
                        >
                          <ClipboardIcon v-if="copiedId !== `output-${index}`" class="w-3.5 h-3.5" />
                          <span v-else class="text-[10px] text-green-600 font-bold uppercase">Copied</span>
                        </button>
                      </div>

                      <div v-if="expandedSteps[`output-${index}`]" class="rounded-lg overflow-hidden border border-gray-900 bg-[#0d1117] shadow-inner relative group">
                        <pre class="text-[11px] font-mono p-4 text-slate-100 overflow-x-auto max-h-[400px] leading-relaxed custom-dark-scrollbar" v-html="highlightJsonHtml(step.tool_output)" />
                        <!-- Subtle dark overlay on top/bottom for long content -->
                        <div class="absolute inset-x-0 top-0 h-4 bg-gradient-to-b from-[#0d1117] to-transparent pointer-events-none opacity-50"></div>
                        <div class="absolute inset-x-0 bottom-0 h-4 bg-gradient-to-t from-[#0d1117] to-transparent pointer-events-none opacity-50"></div>
                      </div>
                    </div>

                    <!-- Raw LLM Log (Debugging) -->
                    <div v-if="step.raw_log" class="space-y-2 pt-2 border-t border-gray-50">
                      <div
                        class="flex items-center justify-between cursor-pointer group/header select-none"
                        @click="toggleSection(`raw-${index}`)"
                      >
                        <p
                          class="text-[10px] font-black text-amber-500 uppercase tracking-widest flex items-center space-x-1"
                        >
                          <CpuChipIcon class="w-3 h-3" />
                          <span>Raw LLM Response</span>
                          <component
                            :is="
                              expandedSteps[`raw-${index}`]
                                ? ChevronDownIcon
                                : ChevronRightIcon
                            "
                            class="w-2.5 h-2.5"
                          />
                        </p>
                        <button
                          @click.stop="
                            copyToClipboard(step.raw_log, `raw-${index}`)
                          "
                          class="p-1 rounded text-gray-400 hover:text-amber-600 hover:bg-amber-50 transition-colors opacity-0 group-hover/header:opacity-100"
                        >
                          <span
                            v-if="copiedId === `raw-${index}`"
                            class="text-[10px] text-green-600 font-medium"
                            >Copied!</span
                          >
                          <ClipboardIcon v-else class="w-3 h-3" />
                        </button>
                      </div>

                      <div
                        v-if="expandedSteps[`raw-${index}`]"
                        class="relative"
                      >
                        <pre
                          class="text-[11px] font-mono bg-amber-50/30 p-3 rounded-lg border border-amber-100/50 text-amber-900/80 overflow-x-auto max-h-60 leading-relaxed custom-scrollbar shadow-sm"
                          >{{ step.raw_log }}</pre
                        >
                      </div>
                    </div>

                    <!-- Error Message -->
                    <div
                      v-if="step.error_message"
                      class="bg-red-50 p-4 rounded-lg border border-red-100 text-red-700 text-sm flex items-start space-x-3"
                    >
                      <ExclamationCircleIcon class="w-5 h-5 shrink-0 mt-0.5" />
                      <div class="space-y-1">
                        <p class="font-bold">Execution Failed</p>
                        <p class="text-red-600 opacity-90 break-all">
                          {{ step.error_message }}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- TIMELINE VIEW -->
            <div v-else class="p-8 h-full flex flex-col">
              <div class="relative flex-1 min-h-[300px] space-y-6">
                <!-- Steps -->
                <div
                  v-for="(step, index) in timelineSteps"
                  :key="index"
                  class="relative flex items-bottom group"
                >
                  <!-- Label -->
                  <div
                    class="w-48 shrink-0 pr-4 text-left font-sans transition-all flex flex-col justify-center"
                    :style="{ paddingLeft: `${(step._depth || 0) * 16}px` }"
                  >
                    <div
                      class="text-xs font-bold text-gray-800 truncate flex items-center space-x-1"
                      :title="getEventLabel(step.event_type)"
                    >
                      <span v-if="(step._depth || 0) > 0" class="text-gray-400 font-normal shrink-0">↳</span>
                      <span class="truncate">{{ getEventLabel(step.event_type) }}</span>
                    </div>
                    <div
                      v-if="step.tool_name"
                      class="text-[10px] text-indigo-600 truncate pl-3"
                      :title="localizeToolName(step.tool_name)"
                    >
                      {{ localizeToolName(step.tool_name) }}
                    </div>
                    <div
                      v-if="getSubagentMeta(step)"
                      class="text-[10px] text-indigo-600 truncate pl-3"
                      :title="formatSubagentTraceSummary(getSubagentMeta(step))"
                    >
                      子代理 · {{ subagentDisplayName(getSubagentMeta(step)) }}
                    </div>
                    <div
                      v-if="formatStepTime(step.timestamp || step.created_at)"
                      class="text-[10px] text-gray-400 font-mono pl-3"
                    >
                      {{ formatStepTime(step.timestamp || step.created_at) }}
                    </div>
                  </div>

                  <!-- Timeline Bar Container -->
                  <div
                    class="flex-1 h-8 bg-gray-50 rounded-lg relative overflow-hidden border border-gray-100"
                  >
                    <!-- Grid Lines (Background) -->
                    <div
                      class="absolute inset-0 grid grid-cols-12 gap-0 opacity-10 pointer-events-none"
                    >
                      <div
                        v-for="i in 12"
                        :key="i"
                        class="border-l border-gray-900 h-full"
                      ></div>
                    </div>

                    <!-- The Bar -->
                    <div
                      class="absolute top-1.5 bottom-1.5 rounded-md shadow-sm transition-all duration-300 flex items-center px-2 cursor-help group-hover:brightness-95"
                      :class="
                        step.status === 'error' ? 'bg-red-400' : 'bg-indigo-500'
                      "
                      :style="step._style"
                    ></div>

                    <!-- Hover Tooltip (Simple) -->
                    <div
                      class="absolute top-0 bottom-0 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity flex items-center"
                      :style="{
                        left: `calc(${
                          parseFloat(step._style.left) +
                          parseFloat(step._style.width)
                        }% + 8px)`,
                      }"
                    >
                      <span
                        class="bg-gray-800 text-white text-xs px-2 py-1 rounded shadow-lg whitespace-nowrap z-20"
                      >
                        {{ step._duration.toFixed(0) }}ms
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <div
                class="mt-8 pt-4 border-t border-gray-100 flex justify-between text-xs text-gray-400 font-mono"
              >
                <span>0ms</span>
                <span>Total: {{ totalExecutionTime.toFixed(0) }}ms</span>
              </div>
            </div>
          </template>

          <!-- Empty State -->
          <div
            v-else
            class="flex flex-col items-center justify-center h-full text-gray-400 space-y-4"
          >
            <div class="bg-gray-100 p-4 rounded-full">
              <ChatBubbleLeftRightIcon class="w-10 h-10 text-gray-400" />
            </div>
            <div class="text-center">
              <p class="font-medium text-gray-600">本次交互为直接闲聊</p>
              <p class="text-sm opacity-60 mt-1">未触发复杂的工具调用链路</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.slide-in-right {
  animation: slideIn 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes slideIn {
  from { transform: translateX(100%); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}

/* Shimmer Animation */
.shimmer-overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.4) 50%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: shimmer 2s infinite linear;
}

@keyframes shimmer {
  from { background-position: -200% 0; }
  to { background-position: 200% 0; }
}

.animate-pulse-subtle {
  animation: pulse-subtle 2s infinite ease-in-out;
}

@keyframes pulse-subtle {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.98; transform: scale(0.998); }
}

.animate-spin-slow {
  animation: spin 3s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.custom-scrollbar::-webkit-scrollbar {
  width: 5px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.2);
  border-radius: 10px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(156, 163, 175, 0.4);
}

.custom-dark-scrollbar::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.custom-dark-scrollbar::-webkit-scrollbar-track {
  background: #0d1117;
}
.custom-dark-scrollbar::-webkit-scrollbar-thumb {
  background-color: #30363d;
  border-radius: 10px;
  border: 1px solid #0d1117;
}
.custom-dark-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: #484f58;
}
</style>
