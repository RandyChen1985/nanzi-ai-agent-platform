<script setup lang="ts">
import { ref, reactive, nextTick, computed, watch, onMounted, onUnmounted, type ComponentPublicInstance } from "vue";
import MentionList from "@/components/agent/MentionList.vue";
import AttachmentImageThumb from "@/components/embed/AttachmentImageThumb.vue";
import SkillCascadeMenu from "@/components/embed/SkillCascadeMenu.vue";
import GeneratingWalker from "@/components/embed/GeneratingWalker.vue";
import type { SkillItem } from "@/components/embed/SkillCascadeMenu.vue";
import McpCascadeMenu from "@/components/embed/McpCascadeMenu.vue";
import type { McpToolItem } from "@/components/embed/McpCascadeMenu.vue";
import ExpertCascadeMenu from "@/components/embed/ExpertCascadeMenu.vue";
import DataKnowledgeCascadeMenu from "@/components/embed/DataKnowledgeCascadeMenu.vue";
import {
  readKnowledgeBaseIds,
  readMetadataDatasetIds,
  syncKnowledgeBaseChip,
  syncMetadataDatasetChips,
  toggleMountId,
} from "@/utils/sessionMountChips";
import type { ReasoningEffort } from "@/api/model";
import type { ContextCompactionRecord } from "@/api/agent";
import ContextCompactionTimeline from "@/components/chat/ContextCompactionTimeline.vue";
import { formatContextTokens, type ContextUsage } from "@/composables/useContextUsage";
import { isUserVisibleContextCompaction } from "@/composables/useContextCompactions";
import { isImageAttachment } from "@/utils/attachmentImages";
import { DATASET_PORTAL_SYSTEM_COMMAND_ID, KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID } from "@/constants/datasetPortalCommand";
import { getTemperatureGuidance } from "@/utils/temperatureGuidance";
import {
  ArchiveBoxIcon,
  ArrowPathIcon,
  BoltIcon,
  BookOpenIcon,
  ChartBarIcon,
  ChatBubbleLeftRightIcon,
  ChevronDownIcon,
  CircleStackIcon,
  ClockIcon,
  CloudIcon,
  CommandLineIcon,
  ComputerDesktopIcon,
  Cog6ToothIcon,
  CubeIcon,
  CpuChipIcon,
  DocumentTextIcon,
  FolderIcon,
  PhotoIcon,
  PowerIcon,
  PuzzlePieceIcon,
  ServerIcon,
  ServerStackIcon,
  SparklesIcon,
  XMarkIcon,
} from "@heroicons/vue/24/outline";

type ApprovalMode = "ask" | "allow" | "deny";

const APPROVAL_MODE_OPTIONS: {
  value: ApprovalMode;
  label: string;
  description: string;
}[] = [
  {
    value: "ask",
    label: "请求批准",
    description: "写操作与需授权的工具调用前，均需你确认后才会执行",
  },
  {
    value: "allow",
    label: "自动批准",
    description: "自动执行工具调用，仅在系统判定为危险操作时拦截",
  },
  {
    value: "deny",
    label: "拒绝执行",
    description: "禁止所有需确认的工具调用（只读查询仍可执行）",
  },
];

type ModelOption = {
  id?: string;
  name?: string;
  model_id: string;
  type?: string;
  temperature?: number | null;
  thinking_enable?: boolean;
  thinking_only?: boolean;
  allow_disable_thinking?: boolean;
  reasoning_effort?: ReasoningEffort | null;
  supported_reasoning_efforts?: ReasoningEffort[];
};

const REASONING_EFFORT_OPTIONS: Array<{ value: ReasoningEffort; label: string; description: string }> = [
  { value: "none", label: "无", description: "不额外增加思考预算" },
  { value: "minimal", label: "极简", description: "快速完成简单推理" },
  { value: "low", label: "低", description: "常规代码、一般分析" },
  { value: "medium", label: "中", description: "需要一定推理的任务" },
  { value: "high", label: "高", description: "Debug、SQL、复杂分析、Agent" },
  { value: "xhigh", label: "极高", description: "极难 Coding Agent、长任务" },
];

const props = withDefaults(defineProps<{
  modelValue: string;
  isProcessing: boolean;
  /** 发送前置检查/历史同步阶段；此时不能显示“停止生成”。 */
  isSubmitting?: boolean;
  showShortcuts: boolean;
  slashCommands: any[];
  allowedAgents: any[];
  /**
   * 当前选定智能体已绑定的数据集数量。>0 时「数据&知识库」里的数据集页签要置灰：
   * 查询范围已由智能体决定，手动挂载的数据集不会生效。
   */
  boundDatasetCount?: number;
  currentUser: any;
  windowWidth: number;
  approvalMode?: ApprovalMode;
  selectedModel?: string;
  availableModels?: ModelOption[];
  /** 当前会话的上下文使用情况，由页面通过只读接口刷新，不依赖 SSE。 */
  contextUsage?: ContextUsage | null;
  /** 是否存在当前会话，用于显示压缩次数入口，即使当前次数为 0。 */
  contextCompactionEnabled?: boolean;
  /** 当前会话已记录的上下文压缩事件。 */
  contextCompactionRecords?: ContextCompactionRecord[];
  /** 当前会话压缩记录总数（平台摘录 + AgentScope 内部压缩）。 */
  contextCompactionCount?: number;
  contextCompactionLoading?: boolean;
  contextCompactionError?: boolean;
  contextCompactionActionLoading?: boolean;
  thinkingEnableOverride?: boolean | null;
  reasoningEffortOverride?: ReasoningEffort | null;
  temperatureOverride?: number | null;
  activeLtmPreference?: any;
  /** 当前会话有效智能体 ID，用于过滤平台技能列表 */
  agentId?: string | null;
  /** 会话已挂载的 MCP 工具名 */
  attachedMcpToolNames?: string[];
  /**
   * 项目会话资源范围：非空时「数据&知识库」浮层只列范围内的资源，
   * 与知识库面板 / 数据门户同口径。`null` / 未传表示不限制。
   */
  allowedDatasetIds?: string[] | null;
  allowedKnowledgeBaseIds?: string[] | null;
  /** 路由模式：auto | expert */
  routingMode?: string;
  /** 专家模式下选中的智能体 ID */
  expertAgentId?: string;
  /** 专家列表加载中 */
  isLoadingAgents?: boolean;
  /** URL agent_id 深链锁定：隐藏专家切换/@，禁止切自动路由 */
  lockExpertAgent?: boolean;
  /** 沙箱工作区运行状态 */
  sandboxWorkspaceStatus?: "idle" | "starting" | "stopping" | "running" | "error";
  /** 当前用户分配的沙箱实例标识（Docker 容器 ID / K8s Pod 名） */
  sandboxWorkspaceInstanceId?: string | null;
  /** 沙箱启动时间 (ISO 8601) */
  sandboxWorkspaceStartedAt?: string | null;
  /** 沙箱运行时长秒数 */
  sandboxWorkspaceUptimeSeconds?: number | null;
  /** 沙箱错误信息 */
  sandboxWorkspaceError?: string;
  /** 沙箱空闲回收分钟数（后端按 sandbox_idle_time 解析，与回收器同源；缺省时只说「空闲超时」不报数字） */
  sandboxIdleMinutes?: number | null;
  /** 沙箱状态手动刷新进行中（刷新按钮转圈并禁用；与容器 starting/stopping 状态无关） */
  sandboxRefreshing?: boolean;
  /** 当前会话是否开启反幻觉校验 */
  enableGrounding?: boolean;
  /** 反幻觉阻断模式：严格缓冲 | 实时撤回 */
  groundingBlockMode?: "strict_buffer" | "stream_with_retraction";
  /** 沙箱后端：docker | k8s（决定浮标术语与「操作」菜单项） */
  sandboxBackend?: "docker" | "k8s";
  /** 生成中是否展示输入框内的跑道动画（设置面板可关，默认开） */
  showGeneratingAnimation?: boolean;
  /** 当前生效智能体是否允许手动切换模型；false 时隐藏模型切换入口（历史数据默认允许） */
  allowModelSwitch?: boolean;
  /** 候选为空时占用切换器位置的只读提示（例如「你的可用模型已全部停用，请联系管理员」） */
  modelSwitchHint?: string;
  /** 自动预热完成：在上下文用量浮标上挂气泡，引导用户去那里看沙箱状态 */
  sandboxWarmHintVisible?: boolean;
  /** 气泡标题：自动预热与 Bash 按需拉起用不同文案 */
  sandboxWarmHintTitle?: string;
  /** 气泡副标题 */
  sandboxWarmHintSubtitle?: string;
}>(), {
  showGeneratingAnimation: true,
  allowModelSwitch: true,
  boundDatasetCount: 0,
  allowedDatasetIds: null,
  allowedKnowledgeBaseIds: null,
  sandboxWarmHintTitle: "沙箱已预热就绪",
  sandboxWarmHintSubtitle: "点这里查看运行状态",
});

const textareaPaddingRightClass = computed(() => {
  const hasContext = Boolean(props.contextUsage && props.contextUsage.physical_window);
  const hasGrounding = Boolean(props.enableGrounding);
  if (hasContext && hasGrounding) return "pr-48";
  if (hasContext || hasGrounding) return "pr-28";
  return "";
});

const isInteractionLocked = computed(() => props.isProcessing || props.isSubmitting === true);

/**
 * 展示用当前占用：后端用最近一次模型调用的实测样本校准后给 current_tokens，
 * 未校准 / 无样本时它等于 estimated_current_tokens，这里再兜一层 ?? 以兼容旧响应。
 */
const contextCurrentTokens = computed(() =>
  Number(props.contextUsage?.current_tokens ?? props.contextUsage?.estimated_current_tokens ?? 0),
);

/** 浮标与面板顶部的基础读数（校准说明另起一行，避免 tooltip 过长）。 */
const contextUsageReadingLabel = computed(
  () =>
    `上下文使用 ${formatContextTokens(contextCurrentTokens.value)} / ${formatContextTokens(props.contextUsage?.physical_window)}`,
);

/** 校准说明：只有后端确实用实测样本校准过才展示，避免把纯估算说成"实测"。 */
const contextCalibrationNote = computed(() => {
  const calibration = props.contextUsage?.calibration;
  if (!calibration?.applied) return "";
  const estimated = formatContextTokens(calibration.sample_estimated_tokens);
  const actual = formatContextTokens(calibration.sample_actual_tokens);
  return `已按最近一次模型调用实测校准（估算 ${estimated} · 实测 ${actual}）`;
});

const contextUsagePercent = computed(() => {
  const current = contextCurrentTokens.value;
  const physicalWindow = Number(props.contextUsage?.physical_window || 0);
  if (!physicalWindow) return 0;
  const usage = (current / physicalWindow) * 100;
  return Math.min(100, Math.max(0, Number.isFinite(usage) ? usage : 0));
});

const contextUsagePercentLabel = computed(() => `${Math.round(contextUsagePercent.value)}%`);

const sandboxRuntimeEnvLabel = computed(() => {
  const runtimeEnv = String(props.contextUsage?.sandbox_runtime_env || "").trim().toLowerCase();
  if (runtimeEnv === "docker") return "平台 Docker 容器内";
  if (runtimeEnv === "host") return "宿主机";
  return "";
});

const sandboxPolicyLabel = computed(() => {
  const policy = String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase();
  if (!policy) return "";
  if (policy === "local") {
    return sandboxRuntimeEnvLabel.value
      ? `local（${sandboxRuntimeEnvLabel.value}）`
      : "local（本地执行）";
  }
  const labels: Record<string, string> = {
    docker: "docker（Docker 容器）",
    k8s: "k8s（Kubernetes 沙箱）",
    e2b: "e2b（E2B 云端）",
    ssh: "ssh（SSH 远程主机）",
  };
  return labels[policy] || policy;
});

const sandboxPolicyIcon = computed(() => {
  const policy = String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase();
  const runtimeEnv = String(props.contextUsage?.sandbox_runtime_env || "").trim().toLowerCase();
  if (policy === "e2b") return CloudIcon;
  if (policy === "ssh") return ServerIcon;
  if (policy === "k8s") return ServerStackIcon;
  if (policy === "docker" || (policy === "local" && runtimeEnv === "docker")) {
    return CubeIcon;
  }
  return ComputerDesktopIcon;
});

const sandboxPolicyBadgeClass = computed(() => {
  const policy = String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase();
  const runtimeEnv = String(props.contextUsage?.sandbox_runtime_env || "").trim().toLowerCase();
  if (policy === "e2b") return "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-800/60 dark:bg-violet-950/30 dark:text-violet-300";
  if (policy === "ssh") return "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-800/60 dark:bg-sky-950/30 dark:text-sky-300";
  if (policy === "k8s") return "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800/60 dark:bg-emerald-950/30 dark:text-emerald-300";
  if (policy === "docker" || (policy === "local" && runtimeEnv === "docker")) {
    return "border-indigo-200 bg-indigo-50 text-indigo-700 dark:border-indigo-800/60 dark:bg-indigo-950/30 dark:text-indigo-300";
  }
  return "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-300";
});

const contextRequestInputPercent = computed(() => {
  const physicalWindow = Number(props.contextUsage?.physical_window || 0);
  const requestInputBudget = Number(props.contextUsage?.request_input_budget || 0);
  if (!physicalWindow || !requestInputBudget) return null;
  return Math.min(100, Math.max(0, (requestInputBudget / physicalWindow) * 100));
});

const contextUsageTone = computed(() => {
  const current = contextCurrentTokens.value;
  const requestInputBudget = Number(props.contextUsage?.request_input_budget || 0);
  if (requestInputBudget > 0 && current >= requestInputBudget) {
    return {
      text: "text-red-600 dark:text-red-400",
      track: "bg-red-500",
      marker: "bg-red-400",
      dot: "bg-red-400",
      badge: "border-red-200/70 bg-red-50/70 text-red-600 hover:bg-red-100/80 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-400 dark:hover:bg-red-950/35",
    };
  }
  if (requestInputBudget > 0 && current >= requestInputBudget * 0.9) {
    return {
      text: "text-amber-600 dark:text-amber-400",
      track: "bg-amber-500",
      marker: "bg-amber-400",
      dot: "bg-amber-400",
      badge: "border-amber-200/70 bg-amber-50/70 text-amber-600 hover:bg-amber-100/80 dark:border-amber-900/40 dark:bg-amber-950/20 dark:text-amber-400 dark:hover:bg-amber-950/35",
    };
  }
  return {
    text: "text-emerald-600 dark:text-emerald-400",
    track: "bg-emerald-500",
    marker: "bg-rose-400",
    dot: "bg-emerald-400/80 dark:bg-emerald-400/70",
    badge: "border-slate-200/80 bg-slate-50/80 text-slate-600 hover:bg-slate-100 dark:border-slate-700/70 dark:bg-slate-800/70 dark:text-slate-300 dark:hover:bg-slate-700/80",
  };
});

const contextUsageStatusLabel = computed(() => {
  const current = contextCurrentTokens.value;
  const requestInputBudget = Number(props.contextUsage?.request_input_budget || 0);
  if (!requestInputBudget) return "暂无输入上限";
  if (current >= requestInputBudget) return "已达输入上限";
  if (current >= requestInputBudget * 0.9) return "接近上限";
  return "使用正常";
});

const sessionContextBreakdown = computed(() => {
  const breakdown = props.contextUsage?.context_breakdown;
  return breakdown && Number(breakdown.total_tokens || 0) > 0 ? breakdown : null;
});

const sessionContextBreakdownItems = computed(() => {
  const breakdown = sessionContextBreakdown.value;
  if (!breakdown) return [];
  return [
    {
      label: "系统提示词",
      value: Number(breakdown.system_prompt_tokens || 0),
      color: "bg-slate-400",
      key: "system",
    },
    {
      label: "工具 schema",
      value: Number(breakdown.tools_tokens || 0),
      color: "bg-violet-400",
      key: "tools",
    },
    {
      label: "对话消息",
      value: Number(breakdown.conversation_tokens || 0),
      color: "bg-blue-400",
      key: "conversation",
    },
  ];
});

const latestContextCompaction = computed(() => {
  return [...(props.contextCompactionRecords || [])]
    .filter(isUserVisibleContextCompaction)
    .sort((left, right) => String(right.occurred_at).localeCompare(String(left.occurred_at)))[0] || null;
});

const latestContextCompactionSavings = computed(() => {
  const record = latestContextCompaction.value;
  if (!record || record.saved_tokens == null) return "";
  const savedPercent = record.saved_percent == null ? "" : ` · ${Number(record.saved_percent)}%`;
  return `本次节省 ${formatContextTokens(record.saved_tokens)}${savedPercent}`;
});

const contextBreakdownSegmentWidth = (value: number) => {
  const total = Number(sessionContextBreakdown.value?.total_tokens || 0);
  if (!total) return "0%";
  return `${Math.min(100, Math.max(0, (value / total) * 100))}%`;
};

const emit = defineEmits<{
  (e: 'update:modelValue', val: string): void;
  (e: 'update:approvalMode', val: ApprovalMode): void;
  (e: 'update:selectedModel', val: string): void;
  (e: 'update:thinking-enable-override', val: boolean | null): void;
  (e: 'update:reasoning-effort-override', val: ReasoningEffort | null): void;
  (e: 'update:temperature-override', val: number | null): void;
  /** 出图尺寸（白名单原值，如 512x512）；null = 不指定。父组件把它塞进
   *  execution_options.image_size 下发，工具侧据此锁定本次出图尺寸。 */
  (e: 'update:image-size', val: string | null): void;
  (e: 'send'): void;
  (e: 'stop'): void;
  (e: 'system-command', cmd: string): void;
  (e: 'toggle-shortcuts'): void;
  (e: 'open-command-manager'): void;
  (e: 'upload-image'): void;
  (e: 'edit-command', cmd: any): void;
  (e: 'delete-command', cmd: any, event: Event): void;
  (e: 'switch-mode', agent: any): void;
  (e: 'switch-to-auto'): void;
  (e: 'switch-to-expert', agentId: string): void;
  (e: 'refresh-agents'): void;
  (e: 'drag-start', event: DragEvent, index: number): void;
  (e: 'drop-cmd', event: DragEvent, index: number): void;
  (e: 'reorder-commands', data: any[]): void;
  (e: 'select-knowledge-base'): void;
  (e: 'select-local-fs'): void;
  (e: 'select-memory'): void;
  (e: 'select-mcp-tool', tools: McpToolItem[]): void;
  (e: 'ignore-ltm'): void;
  (e: 'dismiss-ltm'): void;
  (e: 'refresh-context-compactions'): void;
  (e: 'dismiss-sandbox-warm-hint'): void;
  (e: 'manual-context-compaction', retainRatio: 0.25 | 0.5 | 0.75, mode: "fast" | "smart"): void;
  (e: 'start-sandbox-workspace'): void;
  (e: 'refresh-sandbox-workspace', manualFeedback?: boolean): void;
  (e: 'stop-sandbox-workspace'): void;
  (e: 'restart-sandbox-workspace'): void;
  (e: 'open-docker-terminal'): void;
  /** 让 AI 对当前沙箱做一次系统体检并产出 HTML 报告 */
  (e: 'sandbox-health-report'): void;
  (e: 'disable-grounding'): void;
  (e: 'open-grounding-settings'): void;
}>();

const isDockerSandboxPolicy = computed(() => {
  const policy = String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase();
  return policy === "docker";
});
void isDockerSandboxPolicy;

const isSandboxBackendPolicy = computed(() => {
  const policy = String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase();
  return policy === "docker" || policy === "k8s";
});

const sandboxBackend = computed<"docker" | "k8s">(
  () => props.sandboxBackend || (
    String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase() === "k8s" ? "k8s" : "docker"
  ),
);

const sandboxStatusText = computed(() => {
  const status = props.sandboxWorkspaceStatus || "idle";
  if (sandboxBackend.value === "k8s") {
    switch (status) {
      case "running": return "Pod 已运行";
      case "starting": return "Pod 创建中...";
      case "stopping": return "Pod 终止中...";
      case "error": return "Pod 启动失败";
      default: return "Pod 未启动";
    }
  }
  switch (status) {
    case "running": return "容器已运行";
    case "starting": return "容器启动中...";
    case "stopping": return "容器关机中...";
    case "error": return "容器启动失败";
    default: return "容器未启动";
  }
});

const sandboxInstanceLabel = computed(() => {
  const instance = props.sandboxWorkspaceInstanceId;
  if (!instance) return "";
  return sandboxBackend.value === "k8s"
    ? `Pod: ${instance}`
    : `容器 ID: ${instance}`;
});

const sandboxStartButtonLabel = computed(() =>
  props.sandboxWorkspaceStatus === "error"
    ? "重试启动"
    : (sandboxBackend.value === "k8s" ? "启动沙箱 Pod" : "启动容器"),
);

const sandboxReclaimHint = computed(() => {
  // 回收时长必须来自后端按 sandbox_idle_time 的解析结果，绝不写死：
  // 这里曾经硬编码「空闲 30m」，管理台改成 15 分钟后浮标仍在说 30m。
  const raw = props.sandboxIdleMinutes;
  const minutes =
    typeof raw === "number" && Number.isFinite(raw) && raw > 0
      ? String(Math.round(raw * 10) / 10)
      : "";
  const isK8s = sandboxBackend.value === "k8s";
  if (!minutes) {
    // 拿不到配置时只说「超时回收」，不编造具体数字
    return isK8s ? "空闲超时自动回收沙箱" : "空闲超时自动回收";
  }
  return isK8s ? `空闲 ${minutes}m 自动回收沙箱` : `空闲 ${minutes}m 自动回收`;
});

const showSandboxActionsMenu = ref(false);
const sandboxActionsDropdownRef = ref<HTMLElement | null>(null);

const sandboxUptimeNow = ref(Date.now());
let sandboxUptimeTimer: ReturnType<typeof setInterval> | null = null;

const sandboxUptimeSeconds = computed(() => {
  if (props.sandboxWorkspaceStatus !== 'running') return 0;
  if (props.sandboxWorkspaceStartedAt) {
    try {
      const started = new Date(props.sandboxWorkspaceStartedAt).getTime();
      if (!Number.isNaN(started) && started > 0) {
        return Math.max(0, Math.floor((sandboxUptimeNow.value - started) / 1000));
      }
    } catch {}
  }
  if (typeof props.sandboxWorkspaceUptimeSeconds === 'number') {
    return Math.max(0, props.sandboxWorkspaceUptimeSeconds);
  }
  return 0;
});

const sandboxUptimeFormatted = computed(() => {
  if (props.sandboxWorkspaceStatus !== 'running') return '';
  if (!props.sandboxWorkspaceStartedAt && typeof props.sandboxWorkspaceUptimeSeconds !== 'number') {
    // 无启动时刻也无后端时长信息时不显示时长行
    return '';
  }
  const seconds = sandboxUptimeSeconds.value;
  if (seconds < 60) {
    return `${Math.max(1, seconds)}秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSecs = seconds % 60;
  if (minutes < 60) {
    return remainingSecs > 0 ? `${minutes}分${remainingSecs}秒` : `${minutes}分钟`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMins = minutes % 60;
  return remainingMins > 0 ? `${hours}小时${remainingMins}分` : `${hours}小时`;
});

const formatLtmText = (pref: any): string => {
  if (!pref) return '';
  if (typeof pref === 'string') {
    try {
      const parsed = JSON.parse(pref);
      return formatLtmText(parsed);
    } catch {
      return pref;
    }
  }
  if (Array.isArray(pref)) {
    return pref.map(item => formatLtmText(item)).filter(Boolean).join(', ');
  }
  if (typeof pref === 'object') {
    return Object.entries(pref)
      .map(([key, val]) => {
        if (!val) return '';
        if (typeof val === 'object') {
          const obj: any = val;
          if (obj.name) return String(obj.name);
          if (obj.title) return String(obj.title);
          if (obj.label) return String(obj.label);
          return key;
        }
        if (typeof val === 'string' && (val.startsWith('{') || val.startsWith('['))) {
          try {
            const parsedVal = JSON.parse(val);
            return formatLtmText({ [key]: parsedVal });
          } catch {
            // fallback
          }
        }
        return String(val);
      })
      .filter(Boolean)
      .join(', ');
  }
  return String(pref);
};

const inputRef = ref<HTMLTextAreaElement | null>(null);
const isComposing = ref(false);
const showMentionList = ref(false);
const mentionKeyword = ref("");
const mentionInitialTab = ref<"expert" | "file">("expert");
const mentionPosition = reactive({ top: 0, left: 0 });
const showCommandMenu = ref(false);
const showNewConversationMenu = ref(false);
const newConversationMenuRef = ref<HTMLElement | HTMLElement[] | null>(null);
const newConversationMenuPanelRef = ref<HTMLElement | null>(null);
const newConversationMenuPosition = reactive({ top: 0, left: 12 });
const setNewConversationMenuRef = (el: Element | ComponentPublicInstance | null) => {
  newConversationMenuRef.value = el instanceof HTMLElement ? el : null;
};
const getNewConversationTriggerEl = (): HTMLElement | null => {
  const root = newConversationMenuRef.value;
  return (Array.isArray(root) ? root[0] : root) || null;
};
const updateNewConversationMenuPosition = () => {
  const el = getNewConversationTriggerEl();
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const menuWidth = 160;
  const gutter = 8;
  let left = rect.left;
  if (left + menuWidth > window.innerWidth - gutter) {
    left = Math.max(gutter, window.innerWidth - menuWidth - gutter);
  }
  newConversationMenuPosition.top = Math.round(rect.bottom + 8);
  newConversationMenuPosition.left = Math.round(Math.max(gutter, left));
};
const activeCommandIndex = ref(0);
const commandListContainerRef = ref<HTMLElement | null>(null);

const scrollActiveCommandIntoView = () => {
  nextTick(() => {
    const container = commandListContainerRef.value;
    if (!container) return;
    const activeItem = container.children[activeCommandIndex.value] as HTMLElement | undefined;
    if (activeItem && typeof activeItem.scrollIntoView === 'function') {
      activeItem.scrollIntoView({ block: 'nearest' });
    }
  });
};

watch(activeCommandIndex, () => {
  scrollActiveCommandIntoView();
});

watch(
  () => [showCommandMenu.value, filteredCommands.value.length] as const,
  ([visible]) => {
    if (visible) {
      scrollActiveCommandIntoView();
    }
  },
);

const mentionListRef = ref<any>(null);
const isDrawerExpanded = ref(false);
const shortcutBarRef = ref<HTMLElement | null>(null);
const desktopCommandDrawerPanelRef = ref<HTMLElement | null>(null);
const desktopCommandDrawerPosition = reactive({
  left: 12,
  bottom: 96,
  width: 360,
  maxHeight: 384,
});

const updateDesktopCommandDrawerPosition = () => {
  const bar = shortcutBarRef.value;
  if (!bar) return;
  const rect = bar.getBoundingClientRect();
  const gutter = 12;
  const topReserve = 56; // 避开顶栏
  const available = Math.max(160, rect.top - topReserve - 8);
  desktopCommandDrawerPosition.bottom = Math.round(window.innerHeight - rect.top + 8);
  desktopCommandDrawerPosition.left = Math.round(Math.max(gutter, rect.left));
  desktopCommandDrawerPosition.width = Math.round(
    Math.max(280, Math.min(rect.width + 48, window.innerWidth - gutter * 2)),
  );
  desktopCommandDrawerPosition.maxHeight = Math.round(Math.min(384, available));
};

const openCommandDrawer = async () => {
  isDrawerExpanded.value = true;
  await nextTick();
  updateDesktopCommandDrawerPosition();
  await nextTick();
  if (desktopCommandDrawerPanelRef.value) {
    desktopCommandDrawerPanelRef.value.scrollTop = 0;
  }
};

const closeCommandDrawer = () => {
  isDrawerExpanded.value = false;
};

const toggleCommandDrawer = async () => {
  if (isDrawerExpanded.value) {
    closeCommandDrawer();
    return;
  }
  await openCommandDrawer();
};

const toggleNewConversationMenu = async () => {
  showNewConversationMenu.value = !showNewConversationMenu.value;
  if (showNewConversationMenu.value) {
    await nextTick();
    updateNewConversationMenuPosition();
  }
};

const selectNewConversationType = (command: string) => {
  showNewConversationMenu.value = false;
  emit('system-command', command);
};

const showShortcutBar = computed(() => props.showShortcuts && props.windowWidth >= 640);

const handleCompositionStart = () => {
  isComposing.value = true;
};

const handleCompositionEnd = () => {
  // Delay setting isComposing to false to allow the last keydown (like Enter) to be handled correctly
  setTimeout(() => {
    isComposing.value = false;
  }, 100);
};

const filteredCommands = computed(() => {
  if (!props.modelValue.startsWith('/')) return props.slashCommands;
  const query = props.modelValue.slice(1).toLowerCase();
  if (!query) return props.slashCommands;
  return props.slashCommands.filter(cmd => (cmd.command?.toLowerCase().includes(query)) || (cmd.label?.toLowerCase().includes(query)));
});

const filteredUserCommands = computed(() => filteredCommands.value.filter(c => !String(c.id).startsWith('sys_')));
const filteredSystemCommands = computed(() => filteredCommands.value.filter(c => String(c.id).startsWith('sys_')));

const systemCommandIconById: Record<string, any> = {
  sys_clear: ChatBubbleLeftRightIcon,
  sys_project: FolderIcon,
  sys_history: ClockIcon,
  [DATASET_PORTAL_SYSTEM_COMMAND_ID]: ChartBarIcon,
  sys_knowledge_portal: BookOpenIcon,
  sys_workspace: ComputerDesktopIcon,
  sys_my_artifacts: DocumentTextIcon,
  sys_quota: ChartBarIcon,
  sys_compact: ArchiveBoxIcon,
  sys_settings: Cog6ToothIcon,
};

const getSystemCommandIcon = (cmd: any) => systemCommandIconById[String(cmd?.id || '')] || null;

/** 与 AgentDebug 快捷指令管理一致：本人创建或 admin 可删（不含内置 sys_ 虚拟指令） */
const canDeleteCommand = (cmd: { id?: unknown; created_by?: string }) => {
  if (!props.currentUser || String(cmd.id).startsWith('sys_')) return false;
  if (props.currentUser.role === 'admin') return true;
  return cmd.created_by === props.currentUser.user_name;
};

watch(() => filteredCommands.value, () => {
  activeCommandIndex.value = 0;
});

let draggedItem: any = null;
const handleDragStart = (e: DragEvent, cmd: any, type: string) => {
    if (type !== 'user') return;
    draggedItem = cmd;
    if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(cmd.id));
    }
};

const handleDrop = (_e: DragEvent, targetCmd: any, type: string) => {
    if (type !== 'user' || !draggedItem || draggedItem.id === targetCmd.id) return;
    const items = [...props.slashCommands.filter(c => !String(c.id).startsWith('sys_'))];
    const fromIndex = items.findIndex(i => i.id === draggedItem.id);
    const toIndex = items.findIndex(i => i.id === targetCmd.id);
    if (fromIndex !== -1 && toIndex !== -1) {
        items.splice(fromIndex, 1);
        items.splice(toIndex, 0, draggedItem);
        const reorderData = items.map((item, index) => ({ id: item.id, sort_order: (index + 1) * 10 }));
        emit('reorder-commands', reorderData);
    }
    draggedItem = null;
};

watch(() => props.modelValue, (val) => { if (!val && inputRef.value) inputRef.value.style.height = "auto"; });

watch(isInteractionLocked, (processing) => {
  if (processing) {
    showPlusMenu.value = false;
    showImageParamPop.value = false;
    showApprovalMenu.value = false;
    closeContextUsageDetails();
    isDrawerExpanded.value = false;
    showCommandMenu.value = false;
    showMentionList.value = false;
  }
});

const toggleApprovalMenu = () => {
  if (isInteractionLocked.value) return;
  const next = !showApprovalMenu.value;
  showApprovalMenu.value = next;
  if (next) {
    showPlusMenu.value = false;
    showExpertSelector.value = false;
    nextTick(() => updateApprovalMenuPosition());
  }
};

const selectApprovalMode = (mode: ApprovalMode) => {
  emit("update:approvalMode", mode);
  showApprovalMenu.value = false;
};

const handleFocus = () => {
  if (props.activeLtmPreference) {
    emit('dismiss-ltm');
  }
};

const handleInput = (e: Event) => {
  if (isInteractionLocked.value) return;
  const target = e.target as HTMLTextAreaElement;
  const val = target.value;
  emit('update:modelValue', val);
  if (props.activeLtmPreference && val) {
    emit('dismiss-ltm');
  }
  const cursor = target.selectionStart;
  if (inputRef.value) {
    inputRef.value.style.height = "auto";
    inputRef.value.style.height = Math.min(inputRef.value.scrollHeight, 120) + "px";
  }
  // Don't show command menu during IME composition
  if (val.startsWith("/") && !isComposing.value) {
    showCommandMenu.value = true;
    activeCommandIndex.value = 0;
  } else {
    showCommandMenu.value = false;
  }
  const atMatch = val.slice(0, cursor).match(/[@＠]([^\s@＠]*)$/);
  // 组合态不弹层：中文输入法打全角 ＠ 时会先经过组合态，误弹会打断输入
  if (atMatch && !isComposing.value) {
    const atIndex = val.slice(0, cursor).lastIndexOf(atMatch[0][0] || '@');
    const isStartOfWord = atIndex === 0 || val[atIndex - 1] === ' ' || val[atIndex - 1] === '\n';
    if (isStartOfWord) {
        mentionKeyword.value = atMatch[1] || '';
        // 没有可切换的专家（或链接锁定专家）时直接落在文件 tab，避免弹出只有空壳的专家列表
        mentionInitialTab.value = props.allowedAgents.length > 0 ? 'expert' : 'file';
        showMentionList.value = true;
        const rect = target.getBoundingClientRect();
        mentionPosition.top = rect.top - 220; 
        mentionPosition.left = rect.left + 20;
        return;
    }
  }
  showMentionList.value = false;
};

const handleKeydown = (e: KeyboardEvent) => {
  if (isInteractionLocked.value) return;
  if (e.isComposing || isComposing.value) return;
  if (showMentionList.value && mentionListRef.value && mentionListRef.value.handleKeydown(e)) return;
  if (showCommandMenu.value) {
    if (e.key === "ArrowUp") { e.preventDefault(); activeCommandIndex.value = (activeCommandIndex.value - 1 + filteredCommands.value.length) % filteredCommands.value.length; return; }
    if (e.key === "ArrowDown") { e.preventDefault(); activeCommandIndex.value = (activeCommandIndex.value + 1) % filteredCommands.value.length; return; }
    if (e.key === "Enter") { e.preventDefault(); selectCommand(filteredCommands.value[activeCommandIndex.value]); return; }
    if (e.key === "Escape") { showCommandMenu.value = false; return; }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    // 先拦默认行为再判能不能发：否则没内容可发时浏览器会照常插入一个换行，
    // 空输入框里敲回车就多出一行空——想换行的人本来就该用 Shift+Enter。
    e.preventDefault();
    if (!canSend.value) return;
    sendWithImageParams();
  }
};

const selectCommand = (cmd: any) => {
  if (isInteractionLocked.value || !cmd) return;
  if (cmd.disabled) return;
  if (String(cmd.id).startsWith('sys_')) {
    emit('system-command', cmd.command);
    emit('update:modelValue', '');
    showCommandMenu.value = false;
  } else {
    emit('update:modelValue', cmd.command);
    showCommandMenu.value = false;
    emit('send');
  }
};

/** 清除输入中的 @关键字片段，并可选触发专家切换 */
const clearMentionTrigger = () => {
  const target = inputRef.value;
  if (!target) return false;
  const val = props.modelValue;
  const cursor = target.selectionStart;
  const lastAt = Math.max(val.lastIndexOf('@', cursor - 1), val.lastIndexOf('＠', cursor - 1));
  if (lastAt === -1) return false;
  const before = val.slice(0, lastAt);
  const after = val.slice(cursor);
  emit('update:modelValue', before + after);
  nextTick(() => {
    target.selectionStart = target.selectionEnd = before.length;
    target.focus();
  });
  return true;
};

const handleMentionSelect = (agent: any) => {
  if (clearMentionTrigger()) emit('switch-mode', agent);
  showMentionList.value = false;
};

const handleMentionSelectAuto = () => {
  if (clearMentionTrigger()) emit('switch-to-auto');
  showMentionList.value = false;
};

/** 把一个「服务器本地文件」挂到输入框附件区；抽屉与 @ 文件 tab 共用这一处去重。 */
const pushLocalFile = (payload: {
  type: 'local_file' | 'local_dir';
  path: string;
  name: string;
  size: number;
  ext: string;
}) => {
  if (!payload?.path) return false;
  const exists = uploadedFiles.value.some(
    (file: any) => file.type === payload.type && file.url === payload.path,
  );
  if (exists) return false;
  uploadedFiles.value.push({
    type: payload.type,
    url: payload.path,
    filename: payload.name,
    size: payload.size,
    ext: payload.ext,
  });
  return true;
};

const mountedFilePaths = computed(() =>
  uploadedFiles.value
    .filter((file: any) => file.type === 'local_file' || file.type === 'local_dir')
    .map((file: any) => String(file.url || '')),
);

const handleMentionFileSelect = (payload: {
  type: 'local_file';
  path: string;
  name: string;
  size: number;
  ext: string;
}) => {
  clearMentionTrigger();
  pushLocalFile(payload);
  handleMentionClose();
};

const handleMentionClose = () => {
  showMentionList.value = false;
  nextTick(() => inputRef.value?.focus());
};

/** 左右键切 tab 后把焦点带回去：文件 tab 由 picker 自己聚焦搜索框，回到专家 tab 时焦点要给输入框 */
const handleMentionTabChange = (tab: 'expert' | 'file') => {
  if (tab === 'expert') nextTick(() => inputRef.value?.focus());
};

const handleOpenWorkspaceFromMention = () => {
  showMentionList.value = false;
  // 复用加号菜单里「工作区文件」的既有事件，落地是同一个抽屉
  emit('select-local-fs');
};

/** 生成中也保持可用的快捷指令。
 *
 * `/history` 只是开关历史侧栏，`/new` 会先弹二次确认再切换会话——两者都不会把消息
 * 投进正在运行的会话，所以在「生成中锁定」的范围之外。
 * 其余指令（含用户自定义指令的「填充 + 直接发送」）仍按原样拦截，避免撞上会话运行锁。
 */
const PROCESSING_SAFE_SHORTCUT_IDS = new Set(["sys_clear", "sys_history"]);
const isShortcutAvailableWhileProcessing = (cmd: any): boolean =>
    PROCESSING_SAFE_SHORTCUT_IDS.has(String(cmd?.id ?? ""));

const handleShortcutClick = (cmd: any) => {
    if (!cmd) return;
    if (isInteractionLocked.value && !isShortcutAvailableWhileProcessing(cmd)) return;
    if (cmd.disabled) return;
    if (String(cmd.id).startsWith('sys_')) {
        emit('system-command', cmd.command);
        emit('update:modelValue', '');
    } else {
        emit('update:modelValue', cmd.command);
        emit('send');
    }
};

const openDataPortalFromPlusMenu = () => {
    if (isInteractionLocked.value) return;
    showPlusMenu.value = false;
    showSkillCascade.value = false;
    showExpertCascade.value = false;
    showDataKnowledgeCascade.value = false;
    const cmd = filteredSystemCommands.value.find((c) => c.id === DATASET_PORTAL_SYSTEM_COMMAND_ID);
    handleShortcutClick(cmd);
};

import axios from "@/utils/axios";
import { useToast } from '../../composables/useToast'

const { showToast } = useToast()

// 附件上传状态
const uploadedFiles = ref<any[]>([]);

const isKnowledgePortalDisabled = computed(() => {
  return !!props.slashCommands?.find(c => c.id === 'sys_knowledge_portal')?.disabled;
});

const canSend = computed(
  () =>
    (!!props.modelValue.trim() ||
      uploadedFiles.value.filter((f) => f.type !== "knowledge_settings").length > 0) &&
    // 改图正在上传时不允许发送：否则消息先发出去（且不含刚选的那张图），
    // 图片几秒后才落地、挂在这一轮之外。移动端有全屏遮罩挡着，桌面端没有。
    !editSubmitting.value,
);

const modelLabel = computed(() => {
  if (!props.selectedModel) return "默认模型";
  const model = props.availableModels?.find((item) => item.model_id === props.selectedModel);
  return model?.name || props.selectedModel;
});

const selectedModelConfig = computed(() => {
  if (!props.selectedModel) return null;
  return props.availableModels?.find((item) => item.model_id === props.selectedModel) || null;
});

const thinkingEnabledForSession = computed(() => {
  if (!selectedModelConfig.value?.thinking_enable) return false;
  return props.thinkingEnableOverride ?? Boolean(selectedModelConfig.value.thinking_only);
});

const canToggleThinking = computed(() => Boolean(
  selectedModelConfig.value?.thinking_enable
  && (!thinkingEnabledForSession.value || selectedModelConfig.value.allow_disable_thinking),
));

const supportedReasoningEfforts = computed(() => {
  const supported = selectedModelConfig.value?.supported_reasoning_efforts || [];
  return REASONING_EFFORT_OPTIONS.filter((option) => supported.includes(option.value));
});

const selectedReasoningEffort = computed(() => {
  if (props.reasoningEffortOverride !== undefined && props.reasoningEffortOverride !== null) {
    return props.reasoningEffortOverride;
  }
  return selectedModelConfig.value?.reasoning_effort ?? null;
});

const reasoningEffortLabel = computed(() => {
  if (props.reasoningEffortOverride === null || props.reasoningEffortOverride === undefined) {
    return selectedModelConfig.value?.reasoning_effort
      ? REASONING_EFFORT_OPTIONS.find((option) => option.value === selectedModelConfig.value?.reasoning_effort)?.label || "默认"
      : "默认";
  }
  return REASONING_EFFORT_OPTIONS.find((option) => option.value === props.reasoningEffortOverride)?.label || "默认";
});

/** 触发器上的短徽章：优先短标签，避免挤掉模型名 */
const thinkingSummaryLabel = computed(() => {
  if (!selectedModelConfig.value?.thinking_enable) return "";
  if (!thinkingEnabledForSession.value) return "关";
  if (props.reasoningEffortOverride === null || props.reasoningEffortOverride === undefined) {
    return selectedModelConfig.value.reasoning_effort
      ? reasoningEffortLabel.value
      : "思考";
  }
  return reasoningEffortLabel.value;
});

const thinkingPanelSubtitle = computed(() => {
  const name = selectedModelConfig.value?.name || selectedModelConfig.value?.model_id || "当前模型";
  return `${name} · 本次会话`;
});

const modelSearchQuery = ref("");
const showModelSearch = computed(() => (props.availableModels?.length || 0) >= 6);
const filteredAvailableModels = computed(() => {
  const list = props.availableModels || [];
  const q = modelSearchQuery.value.trim().toLowerCase();
  if (!q) return list;
  return list.filter((model) => {
    const name = (model.name || "").toLowerCase();
    const id = (model.model_id || "").toLowerCase();
    return name.includes(q) || id.includes(q);
  });
});

const showThinkingPanel = ref(false);

const closeModelMenu = () => {
  showThinkingPanel.value = false;
  showModelDropdown.value = false;
  modelSearchQuery.value = "";
  activeModelIndex.value = 0;
};

const backFromThinkingPanel = () => {
  showThinkingPanel.value = false;
};

/** 模型默认温度（未配置则为 0.7） */
const defaultModelTemperature = computed(() => {
  const t = selectedModelConfig.value?.temperature;
  return typeof t === "number" && Number.isFinite(t) ? t : 0.7;
});

/** 实际生效温度 */
const effectiveTemperature = computed(() => {
  if (props.temperatureOverride !== null && props.temperatureOverride !== undefined) {
    return props.temperatureOverride;
  }
  return defaultModelTemperature.value;
});

/** 是否跟随模型默认温度 */
const isFollowingDefaultTemperature = computed(() => {
  return props.temperatureOverride === null || props.temperatureOverride === undefined;
});

/** 触发器按钮上的温度徽章：仅在手动调整过温度时显示 */
const temperatureSummaryLabel = computed(() => {
  if (isFollowingDefaultTemperature.value) return "";
  const val = Number(effectiveTemperature.value.toFixed(2));
  return `T:${val}`;
});

/** 触发器按钮鼠标悬停完整提示 */
const modelTriggerTooltip = computed(() => {
  if (!props.selectedModel) return "使用智能体默认模型";
  const parts = [`覆盖模型: ${modelLabel.value}`];
  if (thinkingSummaryLabel.value) {
    parts.push(`思考: ${thinkingSummaryLabel.value}`);
  }
  if (!isFollowingDefaultTemperature.value) {
    const valStr = effectiveTemperature.value.toFixed(2);
    parts.push(`自定义温度: ${valStr}${effectiveTemperature.value > 1 ? " (超出1.0)" : ""}`);
  }
  return parts.join(" · ");
});

const PRESET_TEMPERATURES = [
  { label: "严谨", value: 0.2, desc: "更稳定严谨，适合查数、代码和规则问答" },
  { label: "均衡", value: 0.7, desc: "准确性和多样性较均衡，适合日常对话" },
  { label: "发散", value: 1.0, desc: "回答更灵活，措辞变化更多" },
];

const setCustomTemperature = (val: number) => {
  const clamped = Math.min(2.0, Math.max(0.0, Math.round(val * 100) / 100));
  emit("update:temperature-override", clamped);
};

const resetTemperatureToDefault = () => {
  emit("update:temperature-override", null);
};

/** 点模型行：移动端进思考/参数二级；桌面端选中即关菜单 */
const selectModel = (model: ModelOption) => {
  emit("update:selectedModel", model.model_id);
  emit("update:thinking-enable-override", null);
  emit("update:reasoning-effort-override", null);
  emit("update:temperature-override", null);
  if (isMobileViewport.value && model.thinking_enable) {
    showThinkingPanel.value = true;
    return;
  }
  closeModelMenu();
};

/** 点思考/参数标签 / 箭头：打开思考与参数设置（桌面侧栏 / 移动二级） */
const openThinkingSettings = (model: ModelOption, event?: Event) => {
  event?.stopPropagation();
  event?.preventDefault();
  emit("update:selectedModel", model.model_id);
  if (props.selectedModel !== model.model_id) {
    emit("update:thinking-enable-override", null);
    emit("update:reasoning-effort-override", null);
    emit("update:temperature-override", null);
  }
  showModelDropdown.value = true;
  showThinkingPanel.value = true;
  if (isMobileViewport.value) nextTick(updateModelDropdownPosition);
};

const resetModelSelection = () => {
  emit("update:selectedModel", "");
  emit("update:thinking-enable-override", null);
  emit("update:reasoning-effort-override", null);
  emit("update:temperature-override", null);
  closeModelMenu();
};

const toggleThinkingForSession = () => {
  if (!canToggleThinking.value) return;
  const nextEnabled = !thinkingEnabledForSession.value;
  emit("update:thinking-enable-override", nextEnabled);
  if (nextEnabled) {
    emit("update:reasoning-effort-override", props.reasoningEffortOverride ?? null);
  } else {
    emit("update:reasoning-effort-override", null);
  }
};

const selectReasoningEffort = (effort: ReasoningEffort | null) => {
  emit("update:reasoning-effort-override", effort);
};

/** 未覆盖时高亮「跟随模型默认」，而不是落到模型注册档位 */
const isFollowingModelEffort = computed(
  () => props.reasoningEffortOverride === null || props.reasoningEffortOverride === undefined,
);


const showModelDropdown = ref(false);
const modelDropdownRef = ref<HTMLElement | null>(null);
const modelDropdownTriggerRef = ref<HTMLButtonElement | null>(null);
const modelListScrollRef = ref<HTMLElement | null>(null);
const modelSearchInputRef = ref<HTMLInputElement | null>(null);
const activeModelIndex = ref(0);

const modelDropdownPosition = reactive({
  bottom: 12,
  left: 12,
  width: 0,
});

const initActiveModelIndex = () => {
  if (!props.selectedModel) {
    activeModelIndex.value = 0;
  } else {
    const idx = filteredAvailableModels.value.findIndex(
      (m) => m.model_id === props.selectedModel
    );
    activeModelIndex.value = idx >= 0 ? idx + 1 : 0;
  }
};

const scrollActiveModelIntoView = () => {
  nextTick(() => {
    const list = modelListScrollRef.value;
    if (!list) return;
    const activeEl = list.querySelector(`[data-model-index="${activeModelIndex.value}"]`) as HTMLElement | null;
    if (activeEl) {
      activeEl.scrollIntoView({ block: 'nearest' });
    }
  });
};

const scrollSelectedModelIntoView = () => {
  const list = modelListScrollRef.value;
  if (!list) return;
  const selected = list.querySelector('[data-model-current="true"]') as HTMLElement | null;
  if (!selected) return;
  const listRect = list.getBoundingClientRect();
  const itemRect = selected.getBoundingClientRect();
  list.scrollTop += itemRect.top - listRect.top - (list.clientHeight - itemRect.height) / 2;
};

const navigateModelList = (direction: 1 | -1) => {
  const total = 1 + filteredAvailableModels.value.length;
  if (total <= 0) return;
  let next = activeModelIndex.value + direction;
  if (next < 0) {
    next = total - 1;
  } else if (next >= total) {
    next = 0;
  }
  activeModelIndex.value = next;
  scrollActiveModelIntoView();
};

const selectActiveModel = () => {
  if (!showModelDropdown.value) return;
  if (activeModelIndex.value === 0) {
    resetModelSelection();
    closeModelMenu();
  } else {
    const model = filteredAvailableModels.value[activeModelIndex.value - 1];
    if (model) {
      selectModel(model);
    }
  }
};

watch(modelSearchQuery, () => {
  const total = 1 + filteredAvailableModels.value.length;
  if (activeModelIndex.value >= total) {
    activeModelIndex.value = 0;
  }
  scrollActiveModelIntoView();
});

const updateModelDropdownPosition = () => {
  const el = modelDropdownTriggerRef.value;
  if (!el || !isMobileViewport.value) return;
  const rect = el.getBoundingClientRect();
  const gutter = 12;
  modelDropdownPosition.bottom = Math.max(gutter, window.innerHeight - rect.top + 8);
  modelDropdownPosition.left = gutter;
  modelDropdownPosition.width = Math.max(0, window.innerWidth - gutter * 2);
};

const toggleModelDropdown = () => {
  if (isInteractionLocked.value) return;
  const willOpen = !showModelDropdown.value;
  if (willOpen) {
    // 每次打开先回模型列表；桌面不自动展开思考侧栏
    showThinkingPanel.value = false;
    modelSearchQuery.value = "";
    initActiveModelIndex();
    updateModelDropdownPosition();
  } else {
    showThinkingPanel.value = false;
    modelSearchQuery.value = "";
    activeModelIndex.value = 0;
  }
  showModelDropdown.value = willOpen;
  if (willOpen) {
    nextTick(() => {
      if (isMobileViewport.value) updateModelDropdownPosition();
      scrollSelectedModelIntoView();
      scrollActiveModelIntoView();
      if (showModelSearch.value) {
        modelSearchInputRef.value?.focus();
      }
    });
  }
};

const activeApprovalMode = computed(
  () => props.approvalMode || "ask",
);

const activeApprovalLabel = computed(() => {
  const option = APPROVAL_MODE_OPTIONS.find((item) => item.value === activeApprovalMode.value);
  return option?.label || "请求批准";
});

const approvalTriggerToneClass = computed(() => {
  switch (activeApprovalMode.value) {
    case "allow":
      return "bg-blue-50 text-blue-700 hover:bg-blue-100 dark:bg-blue-950/45 dark:text-blue-300 dark:hover:bg-blue-950/60";
    case "deny":
      return "bg-red-50 text-red-700 hover:bg-red-100 dark:bg-red-950/45 dark:text-red-300 dark:hover:bg-red-950/60";
    default:
      return "text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700/70";
  }
});

const plusMenuContainerRef = ref<HTMLElement | null>(null);
const contextUsageContainerRef = ref<HTMLElement | null>(null);
const approvalTriggerWrapperRef = ref<HTMLElement | null>(null);
const approvalMenuPanelRef = ref<HTMLElement | null>(null);
const approvalTriggerRef = ref<HTMLButtonElement | null>(null);
const showApprovalMenu = ref(false);
const showContextUsageDetails = ref(false);
const contextUsageDetailsRef = ref<HTMLElement | null>(null);
const contextUsageDetailsPlacement = ref<'above' | 'below'>('above');
const showContextCompactionDetails = ref(false);
const contextCompactionDetailsRef = ref<HTMLElement | null>(null);
const contextCompactionDetailsPlacement = ref<'above' | 'below'>('above');

const contextCompactionCount = computed(() => Math.max(0, Number(props.contextCompactionCount || 0)));
const contextCompactionRetainRatio = ref<0.25 | 0.5 | 0.75>(0.5);
const contextCompactionMode = ref<"fast" | "smart">("fast");

const closeContextUsageDetails = () => {
  showContextUsageDetails.value = false;
  contextUsageDetailsPlacement.value = 'above';
};

/** 引导专用：确保上下文用量弹层打开；返回「是否是本次调用打开的」，便于退出时只收自己那份 */
const openContextUsageForTour = async (): Promise<boolean> => {
  if (showContextUsageDetails.value) return false;
  closeContextCompactionDetails();
  showContextUsageDetails.value = true;
  await nextTick();
  updateContextUsageDetailsPlacement();
  return true;
};

const closeContextUsageForTour = () => {
  closeContextUsageDetails();
};

const closeContextCompactionDetails = () => {
  showContextCompactionDetails.value = false;
  contextCompactionDetailsPlacement.value = 'above';
};

const updateContextUsageDetailsPlacement = () => {
  const container = contextUsageContainerRef.value;
  const panel = contextUsageDetailsRef.value;
  if (!container || !panel) return;

  const containerRect = container.getBoundingClientRect();
  const panelHeight = panel.getBoundingClientRect().height;
  const gap = 8;
  const aboveSpace = containerRect.top;
  const belowSpace = window.innerHeight - containerRect.bottom;
  const hasAboveSpace = aboveSpace >= panelHeight + gap;
  const hasBelowSpace = belowSpace >= panelHeight + gap;

  contextUsageDetailsPlacement.value = hasAboveSpace || !hasBelowSpace ? 'above' : 'below';
};

const toggleContextUsageDetails = async () => {
  // 刻意不挡 isInteractionLocked：上下文用量是**只读信息**，不参与发送、也不撞会话锁，
  // 而生成中恰恰是用户最想看它的时候（还能再塞多少进去）。跟着输入框一起锁只会让人以为
  // 按钮坏了——按钮本身还是可点样式，点了却没反应，比明确置灰更难判断。
  closeContextCompactionDetails();
  showContextUsageDetails.value = !showContextUsageDetails.value;
  if (showContextUsageDetails.value) {
    await nextTick();
    updateContextUsageDetailsPlacement();
  }
};

watch(showContextUsageDetails, (open) => {
  if (open) {
    if (isSandboxBackendPolicy.value) {
      void nextTick(() => {
        emit('refresh-sandbox-workspace', false);
      });
    }
  }
});

const updateContextCompactionDetailsPlacement = () => {
  const container = contextUsageContainerRef.value;
  const panel = contextCompactionDetailsRef.value;
  if (!container || !panel) return;

  const containerRect = container.getBoundingClientRect();
  const panelHeight = panel.getBoundingClientRect().height;
  const gap = 8;
  const aboveSpace = containerRect.top;
  const belowSpace = window.innerHeight - containerRect.bottom;
  const hasAboveSpace = aboveSpace >= panelHeight + gap;
  const hasBelowSpace = belowSpace >= panelHeight + gap;

  contextCompactionDetailsPlacement.value = hasAboveSpace || !hasBelowSpace ? 'above' : 'below';
};

const toggleContextCompactionDetails = async () => {
  if (isInteractionLocked.value || props.contextCompactionEnabled !== true) return;
  closeContextUsageDetails();
  showContextCompactionDetails.value = !showContextCompactionDetails.value;
  if (showContextCompactionDetails.value) {
    await nextTick();
    updateContextCompactionDetailsPlacement();
  }
};

watch(() => props.contextUsage?.physical_window, (window) => {
  if (!window) closeContextUsageDetails();
});

watch(() => props.contextCompactionEnabled, (enabled) => {
  if (!enabled) closeContextCompactionDetails();
});

const approvalMenuPosition = reactive({
  bottom: 0,
  left: 0,
  width: 320,
});

const isMobileViewport = computed(() => props.windowWidth < 640);

const shortcutScrollRef = ref<HTMLElement | null>(null);
const desktopCommandDrawerRef = ref<HTMLElement | null>(null);

/** 行内展示全部指令（超出横向滚动）；「更多」打开完整指令库 */
const visibleRowSystemCommands = computed(() =>
  filteredSystemCommands.value.filter((cmd) => cmd.id !== 'sys_project'),
);
const visibleRowUserCommands = computed(() => filteredUserCommands.value);
const hasShortcutChips = computed(
  () => visibleRowSystemCommands.value.length > 0 || visibleRowUserCommands.value.length > 0,
);
const showShortcutDivider = computed(
  () =>
    visibleRowSystemCommands.value.length > 0
    && visibleRowUserCommands.value.length > 0,
);

/** 纵向滚轮在快捷指令条上转为横向滚动，方便桌面端浏览 */
const onShortcutWheel = (event: WheelEvent) => {
  const el = shortcutScrollRef.value;
  if (!el) return;
  if (el.scrollWidth <= el.clientWidth + 1) return;
  if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
  el.scrollLeft += event.deltaY;
  event.preventDefault();
};

const updateApprovalMenuPosition = () => {
  const el = approvalTriggerRef.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const gutter = 12;
  const maxWidth = 320;
  const menuWidth = isMobileViewport.value
    ? window.innerWidth - gutter * 2
    : maxWidth;
  let left = rect.left;
  if (isMobileViewport.value) {
    left = gutter;
  } else if (left + menuWidth > window.innerWidth - gutter) {
    left = Math.max(gutter, rect.right - menuWidth);
  }
  if (left < gutter) left = gutter;
  approvalMenuPosition.bottom = Math.max(gutter, window.innerHeight - rect.top + 8);
  approvalMenuPosition.left = left;
  approvalMenuPosition.width = menuWidth;
};

const handleGlobalClick = (event: MouseEvent) => {
  if (showPlusMenu.value && plusMenuContainerRef.value && !plusMenuContainerRef.value.contains(event.target as Node)) {
    showPlusMenu.value = false;
    showSkillCascade.value = false;
    showImageParamPop.value = false;
  }
  if (showContextUsageDetails.value && contextUsageContainerRef.value && !contextUsageContainerRef.value.contains(event.target as Node)) {
    closeContextUsageDetails();
  }
  if (showContextCompactionDetails.value && contextUsageContainerRef.value && !contextUsageContainerRef.value.contains(event.target as Node)) {
    closeContextCompactionDetails();
  }
  if (showSandboxActionsMenu.value && sandboxActionsDropdownRef.value && !sandboxActionsDropdownRef.value.contains(event.target as Node)) {
    showSandboxActionsMenu.value = false;
  }
  if (showApprovalMenu.value) {
    const target = event.target as Node;
    const inTrigger = approvalTriggerWrapperRef.value?.contains(target);
    const inPanel = approvalMenuPanelRef.value?.contains(target);
    if (!inTrigger && !inPanel) {
      showApprovalMenu.value = false;
    }
  }
  if (showModelDropdown.value && modelDropdownRef.value && !modelDropdownRef.value.contains(event.target as Node)) {
    closeModelMenu();
  }
  // 新会话类型菜单：点击外部关闭（触发器 + Teleport 面板）
  if (showNewConversationMenu.value) {
    const target = event.target as Node;
    const el = getNewConversationTriggerEl();
    const panel = newConversationMenuPanelRef.value;
    if (!(el && el.contains(target)) && !(panel && panel.contains(target))) {
      showNewConversationMenu.value = false;
    }
  }
  // 快捷指令「更多」桌面弹框：点击外部关闭
  if (isDrawerExpanded.value && props.windowWidth >= 640) {
    const target = event.target as Node;
    const panel = desktopCommandDrawerRef.value;
    const moreBtn = (event.target as HTMLElement | null)?.closest?.("[data-shortcut-more]");
    if (moreBtn) return;
    if (panel && !panel.contains(target)) {
      isDrawerExpanded.value = false;
    }
  }
};

const handleGlobalKeydown = (event: KeyboardEvent) => {
  if (showModelDropdown.value && !showThinkingPanel.value) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      navigateModelList(1);
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      navigateModelList(-1);
      return;
    }
    if (event.key === 'Enter') {
      const target = event.target as HTMLElement | null;
      if (target?.tagName !== 'BUTTON' || target === modelDropdownTriggerRef.value) {
        event.preventDefault();
        selectActiveModel();
        return;
      }
    }
  }
  if (event.key === 'Escape') {
    if (showContextUsageDetails.value) {
      closeContextUsageDetails();
      return;
    }
    if (showContextCompactionDetails.value) {
      closeContextCompactionDetails();
      return;
    }
    if (isDrawerExpanded.value) {
      isDrawerExpanded.value = false;
      return;
    }
    if (showExpertSelector.value) {
      showExpertSelector.value = false;
      return;
    }
    if (showExpertCascade.value) {
      showExpertCascade.value = false;
      // 悬停切换级联时面板要一起收，否则会并排站着两个浮层
      showImageParamPop.value = false;
      return;
    }
    if (showImageParamPop.value) {
      showImageParamPop.value = false;
      return;
    }
    if (showSkillCascade.value) {
      showSkillCascade.value = false;
      return;
    }
    if (showDataKnowledgeCascade.value) {
      closeDataKnowledgeCascade();
      return;
    }
    if (showNewConversationMenu.value) {
      showNewConversationMenu.value = false;
      return;
    }
    if (showThinkingPanel.value) {
      showThinkingPanel.value = false;
      return;
    }
    showPlusMenu.value = false;
    showApprovalMenu.value = false;
    showModelDropdown.value = false;
  }
};

const handleApprovalMenuLayout = () => {
  if (showApprovalMenu.value) updateApprovalMenuPosition();
  if (showContextUsageDetails.value) updateContextUsageDetailsPlacement();
  if (showContextCompactionDetails.value) updateContextCompactionDetailsPlacement();
  if (showModelDropdown.value && isMobileViewport.value) updateModelDropdownPosition();
  if (showExpertSelector.value && !isMobileViewport.value) updateExpertMenuPosition();
  if (showNewConversationMenu.value) updateNewConversationMenuPosition();
  if (isDrawerExpanded.value && props.windowWidth >= 640) updateDesktopCommandDrawerPosition();
};

onMounted(() => {
  document.addEventListener('click', handleGlobalClick);
  document.addEventListener('keydown', handleGlobalKeydown);
  window.addEventListener('resize', handleApprovalMenuLayout);
  window.addEventListener('scroll', handleApprovalMenuLayout, true);
  sandboxUptimeTimer = setInterval(() => {
    if (showContextUsageDetails.value && props.sandboxWorkspaceStatus === 'running') {
      sandboxUptimeNow.value = Date.now();
    }
  }, 1000);
});

onUnmounted(() => {
  if (sandboxUptimeTimer) {
    clearInterval(sandboxUptimeTimer);
    sandboxUptimeTimer = null;
  }
  closeContextUsageDetails();
  closeContextCompactionDetails();
  document.removeEventListener('click', handleGlobalClick);
  document.removeEventListener('keydown', handleGlobalKeydown);
  window.removeEventListener('resize', handleApprovalMenuLayout);
  window.removeEventListener('scroll', handleApprovalMenuLayout, true);
  clearEditImage();
});
const showPlusMenu = ref(false);
const showSkillCascade = ref(false);
const showMcpCascade = ref(false);
const showExpertCascade = ref(false);

// ── 图像参数：对话里发起生图的可视化参数入口 ────────────────────────────────
//
// 面板给三样：分辨率 / 风格 / 张数。分辨率直接给具体像素（值全部来自后台白名单），
// 走 execution_options.image_size 结构化下发——尺寸是白名单精确值，让模型转述一遍再落地，
// 猜错就整次请求作废，所以这一项必须由用户直接给。风格与张数只是提示词词汇，拼进正文
// 让模型自己领会即可。
// 曾经的「画面比例」已删掉：分辨率卡片自带 1:1 / 16:9 小字，两套控件表达同一件事，
// 而且能互相打架（选「1:1」+「1664x928」，正文说方图、下发的却是横图）。
// 选中的项在输入框上方显示成标签，正文保持干净；真正并进正文只发生在按下发送的那一刻。
const showImageParamPop = ref(false);
const imageParamStyle = ref("");
/** 用户选的出图尺寸（白名单原值，如 512x512）。空串 = 不指定，用后台默认尺寸。
 *  它不走提示词，而是作为 execution_options.image_size 结构化下发：尺寸必须是白名单里的
 *  精确值，让模型转述一遍再落地，猜错就整次请求作废——所以这一项必须由用户直接给。 */
const imageParamSize = ref("");
const imageParamCount = ref(1);
const imageStatusLoading = ref(false);
const imageFeature = ref<{
  system_enabled: boolean;
  allowed_sizes: string[];
  default_size: string;
  max_images: number;
  // 改图能力只看系统层（总开关）。接口格式不参与——改图固定走聊天接口。
  // 不再回智能体是否勾了 edit_image：委派架构下那个判定不可靠。
  //
  // 这两个字段写成必填而不是可选：曾经标成 `?`，于是下面映射时漏掉也没人报错，
  // 运行期拿到 undefined → 「改图」tab 恒灰，悬停还写「平台还没开启改图能力」。
  // 现在 vue-tsc 会直接把这个漏赋值拦下来。
  edit_system_enabled: boolean;
  edit_unavailable_reason: string;
} | null>(null);

// 「不指定」显式列出来：按钮全亮着的面板里，没有它就看不出「我没选」还是「选了没生效」。
/** 出图尺寸卡片的示意框尺寸：按真实长宽比等比缩放，最长边 14px。
 *  没有它，一排 `1664x928` / `928x1664` 看着差不多，用户得在脑子里换算谁横谁竖。 */
const imageSizeBoxStyle = (size: string) => {
  const m = /^(\d+)x(\d+)$/.exec(String(size || "").trim());
  if (!m) return { width: "14px", height: "14px" };
  const w = Number(m[1]) || 1;
  const h = Number(m[2]) || 1;
  const scale = 14 / Math.max(w, h);
  return {
    width: `${Math.max(6, Math.round(w * scale))}px`,
    height: `${Math.max(6, Math.round(h * scale))}px`,
  };
};

/** 比例标签（18:9 这种不常见的也照样算），卡片上只显示一小行。 */
const imageSizeRatioLabel = (size: string) => {
  const m = /^(\d+)x(\d+)$/.exec(String(size || "").trim());
  if (!m) return "";
  const w = Number(m[1]);
  const h = Number(m[2]);
  if (!w || !h) return "";
  const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a);
  const g = gcd(w, h);
  const rw = w / g;
  const rh = h / g;
  // 比例数字太大时（1000x998 之类）读不出含义，直接给宽高比近似值。
  if (rw > 40 || rh > 40) return (w / h).toFixed(2).replace(/0+$/, "").replace(/\.$/, "") + ":1";
  return `${rw}:${rh}`;
};

/** 后台白名单里的可选尺寸。界面不写死任何尺寸：白名单改了这里跟着变，
 *  避免出现「界面能选、后端不认」的错位。 */
const imageSizeChoices = computed(() =>
  (imageFeature.value?.allowed_sizes || [])
    .map((size) => String(size || "").trim())
    .filter(Boolean)
    .map((size) => ({
      size,
      ratio: imageSizeRatioLabel(size),
      // 小尺寸不是所有上游都支持（如 gpt-image-1 最小只到 1024），悬停时说明一句。
      // 判据用边长而不是写死某个值：白名单是管理员配的，写死一个尺寸迟早对不上。
      hint: (() => {
        const [w, h] = size.split(/[x*X×]/).map((n) => Number(n));
        return Number.isFinite(w) && Number.isFinite(h) && Math.min(w, h) < 1024
          ? "边长小于 1024 的尺寸需要上游模型支持"
          : "";
      })(),
    })),
);

/** 后端配置的默认尺寸。第一格直接把具体像素显示出来，不再用笼统的「自动 / 默认」——
 *  「默认 512x512」和「自动」是两种确定性，后者要用户点开面板挨个比才知道出多大。 */
const imageDefaultSize = computed(() => String(imageFeature.value?.default_size || "").trim());

/** 默认尺寸已经独占第一格，列表里不再重复出现同一个值。 */
const otherImageSizeChoices = computed(() =>
  imageSizeChoices.value.filter((opt) => opt.size !== imageDefaultSize.value),
);

/** 3 列网格，折叠态固定两行 6 格：「默认」1 + 尺寸 4 + 「更多」1。
 *  不折叠时最多摆 5 个尺寸（默认 + 5 = 6 格，也是两行），所以超过 5 个才值得折。
 *  管理员把预设全勾上时白名单能有八个十个，全摊开会把「张数」和底部按钮挤出面板。 */
const IMAGE_SIZE_VISIBLE_COUNT = 4;
const IMAGE_SIZE_COLLAPSE_THRESHOLD = 5;
const imageSizeCollapsed = ref(true);

const imageSizeNeedsCollapse = computed(
  () => otherImageSizeChoices.value.length > IMAGE_SIZE_COLLAPSE_THRESHOLD,
);

const visibleImageSizeChoices = computed(() => {
  const all = otherImageSizeChoices.value;
  if (!imageSizeNeedsCollapse.value || !imageSizeCollapsed.value) return all;
  const head = all.slice(0, IMAGE_SIZE_VISIBLE_COUNT);
  // 选中的尺寸落在折叠区时，用最后一格把它顶出来：面板收着却看不见自己选了什么，
  // 用户只会以为没生效，然后再点一次。
  const selected = all.find((opt) => opt.size === imageParamSize.value);
  if (selected && !head.some((opt) => opt.size === selected.size)) {
    return [...head.slice(0, IMAGE_SIZE_VISIBLE_COUNT - 1), selected];
  }
  return head;
});

const hiddenImageSizeCount = computed(
  () => otherImageSizeChoices.value.length - visibleImageSizeChoices.value.length,
);


/** 出图风格。本质是往提示词里塞修饰词，不是接口参数——实测网关对 quality/style
 *  这类字段一律忽略，所以扩充这里不产生任何接口约束，只是让用户少打几个字。
 *  上限控制在 10 个：再多就要三行，面板会把下面的说明和按钮顶出视野。 */
const IMAGE_STYLE_OPTIONS = [
  "不指定",
  "扁平插画",
  "写实摄影",
  "3D 渲染",
  "极简线条",
  "动漫二次元",
  "水彩手绘",
  "油画质感",
  "赛博霓虹",
  "国潮插画",
];

/** 是否已经拿到过一次状态。未拿到前不下结论，否则会先亮「未开启」再翻成可用。 */
const imageStatusKnown = ref(false);
/** 上次成功查询的时间戳：3 秒内不重复打接口，鼠标在菜单上扫过时不该反复翻状态。 */
let imageStatusFetchedAt = 0;

/** 不可用时的说法。只剩系统层一种原因——智能体勾没勾工具不再参与判断。 */
const imageFeatureBlockReason = computed(() => {
  // 还没查过：先不给结论。直接当「未开启」会先亮一个徽章再翻成可用，白闪一下。
  if (!imageStatusKnown.value) return "";
  const status = imageFeature.value;
  if (!status) return "未开启";
  if (!status.system_enabled) return "未开启";
  return "";
});
const imageFeatureAvailable = computed(
  () => imageStatusKnown.value && !imageFeatureBlockReason.value
);

const loadImageFeatureStatus = async (options: { force?: boolean } = {}) => {
  if (imageStatusLoading.value) return;
  if (
    !options.force &&
    imageStatusKnown.value &&
    Date.now() - imageStatusFetchedAt < 3000
  ) {
    return;
  }
  // 已有结果时静默刷新：不显示 loading，也不动已经渲染出来的状态。
  // 否则每次悬停都会「徽章 → 加载圈 → 徽章」来回翻一次，看起来就是页面抖一下。
  if (!imageStatusKnown.value) imageStatusLoading.value = true;
  try {
    const { data } = await axios.get(
      "/api/portal/system/configs/image-generation/status",
      { params: props.agentId ? { agent_id: props.agentId } : {} }
    );
    imageFeature.value = {
      system_enabled: !!data?.system_enabled,
      allowed_sizes: Array.isArray(data?.allowed_sizes) ? data.allowed_sizes : [],
      default_size: data?.default_size || "",
      max_images: Math.max(1, Number(data?.max_images) || 1),
      // 改图能力与置灰原因必须一起搬过来。少了这两行，「改图」tab 会一直灰着，
      // 而给出理由永远是兜底那句「平台还没开启改图能力」——和后端实际状态无关。
      edit_system_enabled: !!data?.edit_system_enabled,
      edit_unavailable_reason: String(data?.edit_unavailable_reason || ""),
    };
    // 「默认」那一格就是后台配的尺寸，用户还没动过就让它直接亮起来——
    // 否则第一眼看不出这次会出多大，还得先点一下才知道。
    if (!imageParamSize.value) {
      imageParamSize.value = String(data?.default_size || "").trim();
    } else if (
      Array.isArray(data?.allowed_sizes) &&
      data.allowed_sizes.length > 0 &&
      !data.allowed_sizes.includes(imageParamSize.value)
    ) {
      // 管理员删掉了正在用的这一档（或换了默认值）：必须归位。
      // 不归位的话，面板里没有任何一格高亮，chip 却还挂着那个尺寸，
      // 发出去却按默认尺寸出图——界面上看起来一切正常，结果对不上。
      const fallback = String(data?.default_size || "").trim() || String(data.allowed_sizes[0]);
      showToast(`原来选的 ${imageParamSize.value} 已不可用，已改回 ${fallback}`, "warning");
      imageParamSize.value = fallback;
    }
  } catch {
    // 拿不到状态就按「未开启」处理：宁可少一个入口，也不要让用户点了没反应。
    imageFeature.value = null;
  } finally {
    imageStatusLoading.value = false;
    imageStatusKnown.value = true;
    imageStatusFetchedAt = Date.now();
  }
};

/** 刚手动关掉面板的时间，用于挡住紧随其后的悬停重开（见 hoverOpenImageParamPop）。 */
let imagePopClosedAt = 0;

/** 悬停展开的计时器：手停下来才开面板，路过不算（见 hoverOpenImageParamPop）。 */
let hoverOpenTimer: ReturnType<typeof setTimeout> | null = null;

const cancelHoverOpen = () => {
  if (hoverOpenTimer) {
    clearTimeout(hoverOpenTimer);
    hoverOpenTimer = null;
  }
};

/**
 * 打开面板时的参数快照，供「取消」回滚。
 *
 * 面板里的选择是即时生效的（点尺寸就点亮、标签立刻出现在输入框上方），
 * 所以「取消」如果只是关面板，等于什么都没撤——两个按钮做同一件事，
 * 而用户以为自己反悔成功了，下一张图就按他没想要的尺寸出。
 */
const imageParamSnapshot = ref<{ style: string; size: string; count: number } | null>(null);

const snapshotImageParams = () => {
  imageParamSnapshot.value = {
    style: imageParamStyle.value,
    size: imageParamSize.value,
    count: imageParamCount.value,
  };
};

const restoreImageParams = () => {
  const snap = imageParamSnapshot.value;
  if (!snap) return;
  imageParamStyle.value = snap.style;
  imageParamSize.value = snap.size;
  imageParamCount.value = snap.count;
};

/** 生成页「取消」：把参数回滚到打开面板那一刻，再收起面板。 */
const cancelGenParams = () => {
  restoreImageParams();
  closeImageParamPop();
};

const openImageParamPop = () => {
  snapshotImageParams();
  // 移动端不能收加号菜单：图像参数面板是挂在菜单下面的（Teleport 只改渲染位置，
  // 不改组件归属），菜单一卸载面板就跟着没了——点了像没反应。菜单列表在面板打开时
  // 由 v-show 隐藏，收起统一走 closeImageParamPop。
  showSkillCascade.value = false;
  showMcpCascade.value = false;
  showExpertCascade.value = false;
  showDataKnowledgeCascade.value = false;
  // 悬停就刷新一次，管理员刚打开开关时用户不用刷新页面就能看到入口可用。
  // 已有结果时内部走静默刷新 + 3 秒节流，不会把菜单上的状态来回翻。
  void loadImageFeatureStatus();
  if (!imageFeatureAvailable.value) {
    // 不可用时仍然把面板打开——里面写着为什么点不了、该找谁，比一个死按钮有用。
    showImageParamPop.value = true;
    return;
  }
  showImageParamPop.value = true;
};

/**
 * 悬停打开。刚手动关掉的这几百毫秒内不再顶开：鼠标还停在菜单项上时，
 * 菜单项的一次重渲染就会重新触发 mouseenter，面板立刻又弹出来——
 * 用户点完「完成」看着像根本没关掉。显式点击走 openImageParamPop，不受此限。
 */
const hoverOpenImageParamPop = () => {
  if (Date.now() - imagePopClosedAt < 400) return;
  // 悬停即开在触控板/鼠标上太吵：光标从「+」移向下面几个菜单项时会擦过这一行，
  // 360px 的面板立刻盖住半屏对话，还得点空白处才收得掉。给一段短延时，
  // 手真的停在这里才展开；显式点击走 openImageParamPop，不受延时影响。
  cancelHoverOpen();
  hoverOpenTimer = setTimeout(() => {
    hoverOpenTimer = null;
    openImageParamPop();
  }, 260);
};

const closeImageParamPop = () => {
  showImageParamPop.value = false;
  imagePopClosedAt = Date.now();
  // 面板是挂在加号菜单右侧的 flyout：只收面板、把菜单留在屏幕上等于只关了一半，
  // 用户还得再点一次空白处才收得干净。X / 取消 / 知道了 和「完成」统一按整体收起处理。
  showPlusMenu.value = false;
};

/**
 * 把面板选择收敛成一句出图要求，发送时并进正文。全空则返回空串。
 *
 * 措辞必须是「一句人话」而不是 `[生成参数] 风格=扁平插画` 这种键值串：模型拿到键值串
 * 有一定概率把它当成要画进图里的文字，出一张印着「生成参数」的图。
 */
const buildImageParamPrefix = () => {
  const parts: string[] = [];
  if (imageParamStyle.value) parts.push(`风格 ${imageParamStyle.value}`);
  // 尺寸不在这里：它必须是白名单精确值，走 execution_options.image_size 结构化下发。
  // 比例也不在这里：分辨率已经决定了形状，再写一句比例只会在两者不一致时误导模型。
  if (imageParamCount.value > 1) parts.push(`数量 ${imageParamCount.value} 张`);
  return parts.length ? `（本次出图要求：${parts.join("，")}）` : "";
};

/**
 * 输入框上方展示的参数标签。
 *
 * 参数不进输入框正文：正文是用户自己写的话，混进「风格=扁平插画」这种机器味的前缀，
 * 改稿时得先手动删掉才能接着说。标签既让人看见「这次带了什么参数」，也能单独摘掉某一项。
 */
const imageParamChips = computed(() => {
  const chips: Array<{ key: string; label: string; clear: () => void }> = [];
  if (imageParamStyle.value) {
    chips.push({ key: "style", label: imageParamStyle.value, clear: () => { imageParamStyle.value = ""; } });
  }
  // 尺寸与张数只属于「生成」页：改图页没有这两个控件（改图默认跟随原图、一次一张）。
  // 把它们挂到输入框上方，用户只会问「我明明选了 4 张，怎么只出一张」。
  if (imagePanelTab.value === "gen") {
    if (imageParamSize.value && imageParamSize.value !== imageDefaultSize.value) {
      chips.push({ key: "size", label: imageParamSize.value, clear: () => { imageParamSize.value = imageDefaultSize.value; } });
    }
    if (imageParamCount.value > 1) {
      chips.push({ key: "count", label: `${imageParamCount.value} 张`, clear: () => { imageParamCount.value = 1; } });
    }
  }
  return chips;
});

/* ───────────── 改图（图生图）面板 ───────────── */

/** 面板当前在哪一页。记住上次选择对反复改图的用户更顺，所以不做「每次重置成生成」。 */
const imagePanelTab = ref<"gen" | "edit">("gen");

/**
 * 切到改图页时把尺寸与张数归位。
 *
 * 这两项是「生成」页专属的（改图页没有对应控件，改图默认跟随原图、一次一张），
 * 但状态是两个 tab 共用的。不归位的话：用户在生成页试点了 1664x928 → 切到改图页
 * 把一张竖图改背景 → 出图被压成横图，而改图页上找不到任何一处显示这个尺寸。
 * 后端 `_pick_target_size` 里用户尺寸优先于「跟随原图」，于是界面上的不可见状态
 * 真的决定了结果——这类问题用户报不清楚，我们也难复现。
 */
watch(imagePanelTab, (tab) => {
  if (tab !== "edit") return;
  imageParamSize.value = imageDefaultSize.value || "";
  imageParamCount.value = 1;
});

/**
 * 改图原图体积上限，必须与后端 `MAX_SOURCE_IMAGE_BYTES` 保持一致（10 MB）。
 *
 * 上传接口本身允许 20MB，但改图要把图 base64 后塞进请求体（约 1.33 倍），
 * 超过 10MB 的图后端会拒绝。在这里先拦一道，避免出现「图传上去了、
 * 点到完成才失败」这种走到一半才掉链子的体验。
 */
const MAX_EDIT_IMAGE_BYTES = 10 * 1024 * 1024;

/** 改图页里用户挑的原图。还没提交，所以只留在面板内，不动附件列表。 */
const imageEditFile = ref<File | null>(null);
const imageEditFileName = ref("");
const imageEditPreviewUrl = ref("");
const imageEditFileInput = ref<HTMLInputElement | null>(null);

/**
 * 可选的参考图（第二张）。
 *
 * 只提供配色/风格/构图，本身不会被改。上限两张是刻意的：网关虽能收 4 张，
 * 但实测 3 张开始走样、4 张会静默漏内容——与其让用户拿到一张缺了东西的图，
 * 不如一开始就说清只收两张。
 */
const imageEditRefFile = ref<File | null>(null);
const imageEditRefName = ref("");
const imageEditRefPreviewUrl = ref("");
const imageEditRefFileInput = ref<HTMLInputElement | null>(null);

/** 这一轮改图是否带了参考图。发送时据此决定要不要在正文里交代「第二张是参考图」。 */
const imageEditHasReference = ref(false);

/**
 * 本次消息是不是走改图。
 *
 * 它不是「用户选过的模式」这么虚的东西，而是**发送时要给正文加前缀**的开关：
 * 平台本来就有读图能力，同一张图配「背景换成蓝色」是改图，配「这张图讲了什么」是分析。
 * 加上「沿用所附图片进行修改：」之后，模型没有误读这句指令的余地。
 */
const imageEditMode = ref(false);

/**
 * 改图 tab 能不能点：**只看系统开关，不看智能体勾了哪些工具**。
 *
 * 早先按 agent 已发布版本的工具清单判过一次，实践中必然误伤——「智能委派」这类
 * 入口智能体只负责往下分发，它本身不可能勾 edit_image；而真正执行改图的可能是
 * 任意一个被委派的子智能体，前端手里那个 agent_id 根本代表不了它。
 *
 * 所以这里放行：设置参数是用户的事，工具在不在由运行时说了算。真没工具，
 * 模型会回一句改不了——比一个永远点不亮的 tab 强，至少用户知道发生了什么。
 */
const editPanelEnabled = computed(() => {
  const status = imageFeature.value;
  if (!status) return false;
  return !!status.edit_system_enabled;
});

/** 改图 tab 置灰的原因，直接展示给用户，省得他对着灰按钮猜。 */
const editPanelBlockReason = computed(() => {
  const status = imageFeature.value;
  if (!status) return "正在检查可用状态…";
  if (!status.system_enabled) return "平台还没开启图像生成，请联系管理员打开开关。";
  if (!status.edit_system_enabled) {
    return status.edit_unavailable_reason || "平台还没开启改图能力，请联系管理员。";
  }
  return "";
});

// 能力变了（换智能体、管理员改接口格式）就把停在改图页的面板拉回生成页，
// 否则用户下次打开面板看到的是一屏置灰提示，还得自己找回去。
watch(editPanelEnabled, (enabled) => {
  if (!enabled && imagePanelTab.value === "edit") {
    imagePanelTab.value = "gen";
  }
});

const pickEditImage = () => {
  imageEditFileInput.value?.click();
};

/**
 * 主图 / 参考图共用的选取校验。
 *
 * 失败原因要同时写进面板的错误区：只发 toast 的话，槽位仍是虚线圈 +「必选」，
 * 用户会以为「我点了但没选上」，把同一张图再选两三次。而 accept 只是软约束——
 * 手机「所有文件」和拖拽都能把 PDF、HEIC 塞进来，后端要到真正的改图调用才拒绝，
 * 那时用户已经在上传上花了几秒。
 */
const rejectEditImage = (file: File, slot: "主图" | "参考图"): string => {
  const limit = Math.round(MAX_EDIT_IMAGE_BYTES / 1024 / 1024);
  if (!file.type.startsWith("image/")) {
    return `${slot}必须是图片文件（PNG / JPG / WEBP），当前是 ${file.type || "未知类型"}`;
  }
  if (file.size > MAX_EDIT_IMAGE_BYTES) {
    return `${slot}不能超过 ${limit}MB（这张 ${(file.size / 1024 / 1024).toFixed(1)}MB），请先压缩或换一张`;
  }
  return "";
};

/** 校验通过后写入主图槽位（旧的 objectURL 必须先撤销，否则每换一次图泄一个）。 */
const setEditImageFile = (file: File) => {
  if (imageEditPreviewUrl.value) URL.revokeObjectURL(imageEditPreviewUrl.value);
  imageEditFile.value = file;
  imageEditFileName.value = file.name;
  imageEditPreviewUrl.value = URL.createObjectURL(file);
  imageEditError.value = "";
};

const setEditRefImageFile = (file: File) => {
  if (imageEditRefPreviewUrl.value) URL.revokeObjectURL(imageEditRefPreviewUrl.value);
  imageEditRefFile.value = file;
  imageEditRefName.value = file.name;
  imageEditRefPreviewUrl.value = URL.createObjectURL(file);
  imageEditError.value = "";
};

/** 校验 → 要么把原因写进面板并 toast，要么返回空串。三个入口共用。 */
const acceptEditImage = (file: File, slot: "主图" | "参考图"): boolean => {
  const reason = rejectEditImage(file, slot);
  if (!reason) return true;
  imageEditError.value = reason;
  showToast(reason, "error");
  return false;
};

const onEditImageChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  target.value = ""; // 允许重复选同一个文件
  if (!file || !acceptEditImage(file, "主图")) return;
  setEditImageFile(file);
};

/** 面板开着时把图拖到「主图」虚线上：复用同一套校验，别让它变成普通附件。 */
const onEditImageDrop = (event: DragEvent) => {
  const file = event.dataTransfer?.files?.[0];
  if (!file || !acceptEditImage(file, "主图")) return;
  setEditImageFile(file);
};

/**
 * 在「要改哪里」输入框里直接粘贴一张图。
 *
 * 截个图就 Cmd+V 是最自然的动作，比「点槽位 → 文件选择器」快得多。
 * 主图位空着就给主图；已经有一张了，粘贴的第二张按参考图处理——
 * 「再贴一张」的预期是参考，而不是把主图顶掉。
 */
const onEditInstructionPaste = (event: ClipboardEvent) => {
  const items = Array.from(event.clipboardData?.items || []);
  const imageItem = items.find(
    (item) => item.kind === "file" && item.type.startsWith("image/")
  );
  const file = imageItem?.getAsFile();
  if (!file) return; // 纯文本粘贴：交给默认行为，别拦
  // 有图就别把文件名当文字插进指令框
  event.preventDefault();
  const slot = imageEditFile.value ? "参考图" : "主图";
  if (!acceptEditImage(file, slot)) return;
  if (slot === "参考图") setEditRefImageFile(file);
  else setEditImageFile(file);
};

const clearEditImage = () => {
  if (imageEditPreviewUrl.value) URL.revokeObjectURL(imageEditPreviewUrl.value);
  imageEditFile.value = null;
  imageEditFileName.value = "";
  imageEditPreviewUrl.value = "";
  clearEditRefImage();
};

// ── 参考图槽位：与主图同一套体积校验，只是不强制必填 ──
const pickEditRefImage = () => {
  imageEditRefFileInput.value?.click();
};

const onEditRefImageChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  target.value = "";
  if (!file || !acceptEditImage(file, "参考图")) return;
  setEditRefImageFile(file);
};

const onEditRefImageDrop = (event: DragEvent) => {
  const file = event.dataTransfer?.files?.[0];
  if (!file || !acceptEditImage(file, "参考图")) return;
  setEditRefImageFile(file);
};

const clearEditRefImage = () => {
  if (imageEditRefPreviewUrl.value) URL.revokeObjectURL(imageEditRefPreviewUrl.value);
  imageEditRefFile.value = null;
  imageEditRefName.value = "";
  imageEditRefPreviewUrl.value = "";
};

/**
 * 改图页「完成」：把图交成普通附件、把要求写进输入框，剩下的交给用户按发送键。
 *
 * 这里刻意**不直接发出去**。面板里一个「发送」按钮意味着手滑一次就扣一次钱，
 * 而且用户看不见图有没有挂上去；灌进输入框后他还能改词、加话，或者干脆不发。
 */
const closeImageParamPopSafely = () => {
  // 取消离开时把主图、参考图与两个 objectURL 一起清干净：
  // 曾经只 revoke 主图 previewUrl 却留着 imageEditFile，下次打开面板会直接显示裂图。
  clearEditImage();
  imageEditError.value = "";
  // 生成页的参数也一起回滚到打开面板那一刻：面板里的改动是即时的，
  // 「取消」若不撤销，用户就没法反悔。
  restoreImageParams();
  closeImageParamPop();
};

/**
 * 改图提交中。上传是网络请求，图大时要几秒；没有这个状态，按钮点下去
 * 外观毫无变化，用户只会再点几次，然后报「点完成没反应」。
 */
const editSubmitting = ref(false);
/** 改图页的错误提示：失败原因就地留在面板里，不用追着 toast 看。 */
const imageEditError = ref("");

/**
 * 每次提交的代号。面板关闭时 +1，`await` 回来发现代号变了就整体丢弃这次提交。
 *
 * 没有它会这样：点「完成」→ 觉得选错，点 X 关掉面板接着打字 → 两三秒后两张图
 * 突然出现在附件区，刚写的那句话被面板里的指令替换掉。面板已经卸载，
 * 「处理中」这个唯一可见的证据也没了，用户只会觉得界面自己动了起来。
 */
const editSubmitToken = ref(0);
const cancelEditSubmit = () => {
  editSubmitToken.value += 1;
};

// 面板一关（X / 遮罩 / Esc / 取消 / 菜单收起 / 交互锁）就作废进行中的上传。
// 用一个 watch 覆盖所有关闭入口，省得每处 `showImageParamPop = false` 都要记得加一行。
watch(showImageParamPop, (open) => {
  if (!open) cancelEditSubmit();
});

/**
 * 改图页「要改哪里」输入框的内容。
 *
 * 必须显式声明：模板 v-model 和 applyEditParams 都在读写它。曾经漏声明，
 * `imageEditInstruction.value` 在 async 函数里抛 ReferenceError，而这行在 try 之外，
 * 于是 Promise 静默 reject——按钮既不转圈也不报错，就是「点完成没反应」。
 */
const imageEditInstruction = ref("");

const applyEditParams = async () => {
  // 连点保护：第一次还在 await 上传时，第二次点击不该再发一份。
  if (editSubmitting.value) return;
  imageEditError.value = "";

  const file = imageEditFile.value;
  if (!file) {
    // 按钮本来就该是禁用的，走到这里说明状态错位了——给句人话，别静默。
    imageEditError.value = "请先选择要修改的主图";
    showToast("请先选择要修改的主图", "warning");
    return;
  }

  // 生成中不能上传：uploadSingleFile 会被交互锁挡掉。早先这里直接 `return false`
  // 什么提示都没有，可面板在加号菜单上，锁上时菜单会自动收起、面板却还浮着——
  // 用户对着一屏完好的界面点「完成」，看不出任何变化，只能来报「点了没反应」。
  if (isInteractionLocked.value) {
    imageEditError.value = "正在生成回复，等这一轮结束后再改图";
    showToast("正在生成回复，等这一轮结束后再改图", "warning");
    return;
  }

  const refFile = imageEditRefFile.value;
  const instruction = imageEditInstruction.value.trim();
  // 上传会往附件列表 push。回滚按「上传前的长度」截断而不是 pop()——pop() 假定
  // 「刚 push 的一定是最后一个」，而上传是异步的，这中间别处也可能 push/删除。
  const beforeCount = uploadedFiles.value.length;
  const rollbackUploads = () => {
    uploadedFiles.value.splice(beforeCount);
  };
  // 本次提交的代号：面板一旦关闭（任何入口）它就会变，await 回来据此整体作废。
  const myToken = ++editSubmitToken.value;
  const cancelled = () => myToken !== editSubmitToken.value;

  editSubmitting.value = true;
  try {
    const uploaded = await uploadSingleFile(file);
    if (cancelled()) {
      rollbackUploads();
      return;
    }
    if (!uploaded) {
      // 上传失败：面板里选的图和写好的要求都留着，让用户换一张或重试，
      // 而不是把他刚挑好的东西一起清掉。原因同时落到面板里，
      // 不用去追那条几秒就消失的 toast。
      imageEditError.value = lastUploadError.value || "主图上传失败，请重试";
      return;
    }

    if (refFile) {
      const refUploaded = await uploadSingleFile(refFile);
      if (cancelled()) {
        rollbackUploads();
        return;
      }
      if (!refUploaded) {
        // 参考图没传上去：主图已经进了附件列表，留着会让用户以为成了。
        rollbackUploads();
        imageEditError.value = lastUploadError.value || "参考图上传失败，请重试";
        return;
      }
    }
    // 主图先传、参考图后传，附件顺序就是模型看到的顺序——
    // 顺序反了，模型会把参考图当成要改的那张。
    imageEditHasReference.value = !!refFile;
    imageEditMode.value = true;
    clearEditImage();
    imageEditInstruction.value = "";
    closeImageParamPop();

    const current = String(props.modelValue || "").trim();
    if (instruction) {
      // 追加而不是覆盖：用户可能已经在输入框里写了半句要求，整体赋值会把他写的东西
      // 悄悄删掉，而且没有任何提示。生成页那边一直是 `${prefix}\n${current}` 的拼接语义。
      emit("update:modelValue", current ? `${instruction}\n${current}` : instruction);
    }
    // 「完成」不发送，只准备。而准备成功的证据（面板收起、附件多一张图）都在用户
    // 视线之外——尤其没写「要改哪里」时输入框是空的，看起来就像什么都没发生。
    // 所以这里必须说一句下一步动作，否则用户只能猜「我是不是没点上」。
    showToast(
      instruction
        ? current
          ? "要求已写进输入框（原有内容保留），按发送开始改图"
          : "要求已写进输入框，按发送开始改图"
        : "图片已挂到输入框，写上要改哪里再发送",
      "success"
    );
    nextTick(() => {
      const el = inputRef.value;
      if (!el) return;
      el.focus();
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 120) + "px";
    });
  } catch (error: any) {
    // 兜底：上传之外的意外（比如附件列表被并发改动）也不能静默吞掉，
    // 否则又变成一次「点了没反应」。图和要求都留着，重试即可。
    console.error("Apply edit params error:", error);
    imageEditError.value = error?.message || "改图准备失败，请重试";
    showToast(imageEditError.value, "error");
  } finally {
    editSubmitting.value = false;
  }
};

/** 关掉改图模式（用户摘掉了附件、或者这轮已经发完）。 */
const exitImageEditMode = () => {
  imageEditMode.value = false;
  imageEditHasReference.value = false;
};

const applyImageParams = () => {
  // 参数已经在 chips 里了，这里只负责收起面板、把焦点交回输入框。
  // 面板与加号菜单一起收（closeImageParamPop 内部统一处理）——「完成」= 这次设置结束，
  // 该回输入框打字了，菜单不该还挂在屏幕上。
  closeImageParamPop();
  nextTick(() => {
    const el = inputRef.value;
    if (!el) return;
    el.focus();
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  });
};

/**
 * 发送时把参数并进正文最前面，让模型看得到。
 *
 * 只有发送这一刻才落地成文本：平时输入框里是干净的，用户想继续说「再好一点」
 * 不用先清理参数行。发完即清空 chips，下一张图重新选。
 */
const sendWithImageParams = () => {
  // 改图模式：前缀不是参数而是**动作**——「沿用所附图片进行修改：」。
  // 平台有读图能力，同一句话配一张图既可能被理解成改图、也可能被理解成分析，
  // 这句前缀把歧义钉死，比在界面上下赌注可靠得多。
  if (imageEditMode.value) {
    // 兜底：用户可能在改图模式下把附件 chip 删了。这时若还带前缀，
    // 模型会去找一张并不存在的图。没有图片附件就退回普通发送。
    const hasImageAttachment = uploadedFiles.value.some((f) => isImageAttachment(f));
    if (!hasImageAttachment) {
      imageEditMode.value = false;
      imageEditHasReference.value = false;
    }
  }
  if (imageEditMode.value) {
    const body = String(props.modelValue || "").trim();
    // 改图页也有风格选项（实测「保持构图和主体，改成 3D 渲染质感」效果很好），
    // 但这条分支不走 buildImageParamPrefix，所以得自己把风格带上——
    // 漏了这一步，用户在改图页点的风格会静默失效，且没有任何提示。
    const styleNote = imageParamStyle.value ? `（风格 ${imageParamStyle.value}）` : "";
    // 带参考图时必须点名哪张是哪张，否则模型可能拿参考图当主图改。
    const head = imageEditHasReference.value
      ? "沿用所附两张图片进行修改：第一张是要改的主图，第二张只是参考（只参考它的风格、配色或构图，那张本身不用改）。"
      : "沿用所附图片进行修改：";
    emit("update:modelValue", `${head}${body}${styleNote}`);
    // 必须等 emit("send") 同步快照完 imageSizeOverride 之后再重置 chips，
    // 否则 resetImageParams() 触发 watch(imageParamSize) 会抢在 send 之前把父组件的
    // imageSizeOverride 清回默认，导致用户选的分辨率永远传不过去。
    // 但不清 imageEditMode：发送可能被父组件拒绝（内容仍在），
    // 那时清掉前缀标记会让用户重发一次变成「问答」而不是「改图」。
    // 统一由下面那个 watch 在输入框真被清空时收口。
    nextTick(() => {
      emit("send");
      resetImageParams();
    });
    return;
  }
  const prefix = buildImageParamPrefix();
  if (!prefix) {
    emit("send");
    return;
  }
  const current = String(props.modelValue || "");
  emit("update:modelValue", `${prefix}\n${current}`);
  // 同上：在 emit("send") 之后再收 chips，既不抢在父组件快照前清掉 imageSizeOverride，
  // 也能在纯附件发送（modelValue 不变）时把 chips 收干净。
  nextTick(() => {
    emit("send");
    resetImageParams();
  });
};

// 输入框被清空 = 这次输入确实发出去了（父组件发送成功后会清空 v-model），
// 这时参数任务才算结束，标签一起收掉。父组件拒绝发送时内容仍在，标签也留着，
// 不会出现「正文里有参数行、标签却消失了」的错位。
//
// 必须是「从有到无」才算清空。只判断当前值为空会误伤：用户在空输入框里敲个回车，
// modelValue 从 "" 变成 "\n"，trim 之后仍是空串，于是刚选好的「水彩手绘 / 1024x1024」
// 被当成「这轮发完了」整组清掉——人只是想换行。
watch(() => props.modelValue, (value, previous) => {
  const wasFilled = !!String(previous || "").trim();
  const nowEmpty = !String(value || "").trim();
  if (!wasFilled || !nowEmpty) return;
  if (imageParamChips.value.length) {
    resetImageParams();
  }
  // 改图模式同理：正文被清空 = 这轮确实发出去了，标记可以撤。
  if (imageEditMode.value) {
    imageEditMode.value = false;
    imageEditHasReference.value = false;
  }
});

const resetImageParams = () => {
  imageParamStyle.value = "";
  imageParamSize.value = imageDefaultSize.value || "";
  imageParamCount.value = 1;
  // 下一轮重新进入面板时回到折叠态：展开状态是「正在挑尺寸」的临时态，不该跨轮留着。
  imageSizeCollapsed.value = true;
};

// 尺寸要单独同步给父组件：它不在正文里，父组件组装请求体时得从自己那份状态里取。
// 只有用户显式选了「非默认」尺寸时才上报覆盖值；默认尺寸报 null：
// 1) 生成页没选时后端本来就按系统默认出图，同时保留模型按口头要求（如「竖版壁纸」）挑尺寸的能力；
// 2) 改图页没选时后端不传 size 才能跟随原图比例，不会被默认正方形压扁。
watch(imageParamSize, (size) => {
  const explicit = size && size !== imageDefaultSize.value ? size : null;
  emit("update:image-size", explicit);
});

// 进对话就探一次：菜单一打开就能显示「未开启」，不用等用户先点一下才知道。
onMounted(() => {
  void loadImageFeatureStatus();
});

// 切换智能体后「有没有勾 generate_image」会变，必须强制重探——
// 走普通路径会被 3 秒节流挡掉，用户看到的是上一个智能体的状态。
watch(() => props.agentId, () => {
  void loadImageFeatureStatus({ force: true });
  // 上一个智能体选的出图参数不该带过来：新智能体可能根本没勾 generate_image，
  // 那些标签发出去只会变成一句没人理会的「本次出图要求」。
  resetImageParams();
});


const showDataKnowledgeCascade = ref(false);
const dataKnowledgeCascadeRef = ref<InstanceType<typeof DataKnowledgeCascadeMenu> | null>(null);
const skillCascadeRef = ref<InstanceType<typeof SkillCascadeMenu> | null>(null);
const mcpCascadeRef = ref<InstanceType<typeof McpCascadeMenu> | null>(null);
const showExpertSelector = ref(false);
const expertSelectorRef = ref<HTMLElement | null>(null);
const expertMenuPosition = reactive({
  bottom: 0,
  left: 12,
});

const updateExpertMenuPosition = () => {
  const el = expertSelectorRef.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const menuWidth = 280;
  const gutter = 12;
  let left = rect.left;
  if (left + menuWidth > window.innerWidth - gutter) {
    left = Math.max(gutter, window.innerWidth - menuWidth - gutter);
  }
  expertMenuPosition.bottom = Math.max(gutter, window.innerHeight - rect.top + 8);
  expertMenuPosition.left = Math.max(gutter, left);
};
const isUploading = ref(false);
/** 最近一次上传失败的原因（人话），供改图页就地展示，不只靠一闪而过的 toast。 */
const lastUploadError = ref("");
const fileInputRef = ref<HTMLInputElement | null>(null);

const attachedSkillIds = computed(() =>
  uploadedFiles.value
    .filter((f) => f.type === "skill")
    .map((f) => String(f.url)),
);

/** 本轮已挂载的元数据集 / 知识库：只读派生，写入统一走 sessionMountChips */
const attachedMetadataDatasetIds = computed(() => readMetadataDatasetIds(uploadedFiles.value));
const attachedKnowledgeBaseIds = computed(() => readKnowledgeBaseIds(uploadedFiles.value));

/** 智能体是否具备数据查询 / 知识库能力（与「打开数据门户」「打开知识库中心」同一判据） */
const canMountDatasets = computed(() =>
  filteredSystemCommands.value.some((c) => c.id === DATASET_PORTAL_SYSTEM_COMMAND_ID),
);
const canMountKnowledgeBases = computed(() =>
  filteredSystemCommands.value.some((c) => c.id === KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID)
  && !isKnowledgePortalDisabled.value,
);
/** 两个 tab 都不可用时不渲染入口，避免点开一个全灰的浮层 */
const showDataKnowledgeEntry = computed(() => canMountDatasets.value || canMountKnowledgeBases.value);

const isExpertMode = computed(
  () => props.routingMode === "expert" && !!props.expertAgentId,
);

const currentExpertAgent = computed(() => {
  if (!isExpertMode.value) return null;
  return props.allowedAgents?.find((a: any) => a.id === props.expertAgentId) || null;
});

/**
 * 当前专家是不是团主专家：胶囊图标要换成「团队」。
 * 用单人图标会让人以为只跟一个专家在聊，可背后是一整个团在调度。
 */
const currentExpertIsTeamLead = computed(
  () => currentExpertAgent.value?.is_expert_team_lead === true,
);

const expertCapsuleLabel = computed(() => {
  if (isExpertMode.value) {
    return currentExpertAgent.value?.display_name
      || currentExpertAgent.value?.name
      || "专家";
  }
  return "智能委派";
});

const approvalCapsuleLabel = computed(() => activeApprovalLabel.value);

const inputPlaceholder = computed(() => {
  if (isInteractionLocked.value) return "";
  if (isMobileViewport.value) return "今天帮你做些什么？";
  if (props.lockExpertAgent) {
    // 锁定专家时 @ 仍可用，只是不能切专家——文案不能再写「选择专家」
    return "今天帮你做些什么？ @ 选择文件， / 调用技能与指令";
  }
  return "今天帮你做些什么？ @ 选择专家或文件， / 调用技能与指令";
});

const togglePlusMenu = () => {
  if (isInteractionLocked.value) return;
  showPlusMenu.value = !showPlusMenu.value;
  if (showPlusMenu.value) {
    showApprovalMenu.value = false;
    showExpertSelector.value = false;
    // 移动端抽屉与加号菜单互斥
    if (isMobileViewport.value) {
      showSkillCascade.value = false;
      showMcpCascade.value = false;
      showExpertCascade.value = false;
      showDataKnowledgeCascade.value = false;
    }
  } else if (!isMobileViewport.value) {
    showSkillCascade.value = false;
    showMcpCascade.value = false;
    showExpertCascade.value = false;
    showDataKnowledgeCascade.value = false;
  }
};

const toggleExpertSelector = () => {
  if (isInteractionLocked.value || props.lockExpertAgent) return;
  showExpertSelector.value = !showExpertSelector.value;
  if (showExpertSelector.value) {
    showPlusMenu.value = false;
    showSkillCascade.value = false;
    showMcpCascade.value = false;
    showExpertCascade.value = false;
    showDataKnowledgeCascade.value = false;
    showApprovalMenu.value = false;
    emit("refresh-agents");
    nextTick(() => {
      if (!isMobileViewport.value) updateExpertMenuPosition();
    });
  }
};

const selectAutoRouting = () => {
  if (props.lockExpertAgent) return;
  emit("switch-to-auto");
  showExpertSelector.value = false;
  showExpertCascade.value = false;
  showPlusMenu.value = false;
};

const selectExpertAgent = (agentId: string) => {
  if (props.lockExpertAgent) return;
  emit("switch-to-expert", agentId);
  showExpertSelector.value = false;
  showExpertCascade.value = false;
  showPlusMenu.value = false;
};

const openSkillCascade = () => {
  showImageParamPop.value = false;
  showExpertCascade.value = false;
  showMcpCascade.value = false;
  showDataKnowledgeCascade.value = false;
  showSkillCascade.value = true;
  if (isMobileViewport.value) {
    showPlusMenu.value = false;
  }
  nextTick(() => {
    skillCascadeRef.value?.resetSearch?.();
  });
};

const openMcpCascade = () => {
  showImageParamPop.value = false;
  showExpertCascade.value = false;
  showSkillCascade.value = false;
  showDataKnowledgeCascade.value = false;
  showMcpCascade.value = true;
  if (isMobileViewport.value) {
    showPlusMenu.value = false;
  }
  nextTick(() => {
    mcpCascadeRef.value?.resetSearch?.();
  });
};

const openExpertCascade = () => {
  showImageParamPop.value = false;
  if (props.lockExpertAgent) return;
  showSkillCascade.value = false;
  showMcpCascade.value = false;
  showDataKnowledgeCascade.value = false;
  showExpertCascade.value = true;
  if (isMobileViewport.value) {
    showPlusMenu.value = false;
  }
  emit("refresh-agents");
};

const openDataKnowledgeCascade = () => {
  if (!showDataKnowledgeEntry.value) return;
  showImageParamPop.value = false;
  showSkillCascade.value = false;
  showMcpCascade.value = false;
  showExpertCascade.value = false;
  showDataKnowledgeCascade.value = true;
  if (isMobileViewport.value) {
    showPlusMenu.value = false;
  }
  nextTick(() => {
    dataKnowledgeCascadeRef.value?.resetSearch?.();
  });
};

/**
 * 勾选 / 取消一个元数据集：chip 由 `sessionMountChips` 统一重建，
 * 与数据门户里的 📊 勾选写出的格式完全一致。
 */
const toggleDatasetMountFromCascade = (datasetId: string, datasetName?: string) => {
  const files = uploadedFiles.value;
  const nextIds = toggleMountId(readMetadataDatasetIds(files), datasetId);
  uploadedFiles.value = syncMetadataDatasetChips(files, nextIds, (id) =>
    id === datasetId ? datasetName : undefined,
  );
};

/** 勾选 / 取消一个知识库：汇总到同一个 `knowledge_base` chip */
const toggleKnowledgeBaseMountFromCascade = (datasetId: string) => {
  const files = uploadedFiles.value;
  const nextIds = toggleMountId(readKnowledgeBaseIds(files), datasetId);
  uploadedFiles.value = syncKnowledgeBaseChip(files, nextIds);
};

const closeSkillCascade = () => {
  showSkillCascade.value = false;
};

const closeMcpCascade = () => {
  showMcpCascade.value = false;
};

const closeExpertCascade = () => {
  showExpertCascade.value = false;
};

const closeDataKnowledgeCascade = () => {
  showDataKnowledgeCascade.value = false;
};

/** 桌面端：悬停到加号菜单的非级联项时，收起技能/MCP/专家/数据&知识库飞出层 */
const closePlusCascadesOnHover = () => {
  if (isMobileViewport.value) return;
  // 图像参数面板也是加号菜单的一个浮层，统一在这里一起收。
  showImageParamPop.value = false;
  showSkillCascade.value = false;
  showMcpCascade.value = false;
  showExpertCascade.value = false;
  showDataKnowledgeCascade.value = false;
};

const mountSkillFromCascade = (skill: SkillItem) => {
  if (attachedSkillIds.value.includes(skill.id)) {
    return;
  }
  const scope = skill.scope === "personal" ? "personal" : "global";
  uploadedFiles.value.push({
    type: "skill",
    url: skill.id,
    filename: `${skill.name} (技能)`,
    size: 0,
    ext: "skill",
    scope,
    skillMeta: {
      id: skill.id,
      name: skill.name,
      description: skill.description || "",
      scope,
      // 把「已禁用」随挂载一起带到附件上：后端会在写历史前清洗被禁技能的挂载提示，
      // 前端也要据此不再生成「绝对路径可直接读」的提示（见 useChatAttachments）。
      // 个人技能不带该字段，缺失一律按未禁用处理。
      forbidden: skill.forbidden === true,
    },
  });
  showSkillCascade.value = false;
  showMcpCascade.value = false;
  showExpertCascade.value = false;
  showPlusMenu.value = false;
};

const mountMcpFromCascade = (tools: McpToolItem[]) => {
  const list = Array.isArray(tools) ? tools.filter((t) => t?.id && t?.name) : [];
  if (!list.length) return;
  emit("select-mcp-tool", list);
  showMcpCascade.value = false;
  showSkillCascade.value = false;
  showExpertCascade.value = false;
  showPlusMenu.value = false;
};

const triggerFileInput = () => {
  showPlusMenu.value = false;
  showSkillCascade.value = false;
  showMcpCascade.value = false;
  showExpertCascade.value = false;
  showDataKnowledgeCascade.value = false;
  fileInputRef.value?.click();
};

watch(showPlusMenu, (open) => {
  if (!open) {
    showImageParamPop.value = false;
  }
  if (!open && !isMobileViewport.value) {
    showSkillCascade.value = false;
    showMcpCascade.value = false;
    showExpertCascade.value = false;
    showDataKnowledgeCascade.value = false;
  }
});

const isImage = isImageAttachment;

const openImagePreview = (url: string) => {
  window.open(url, "_blank");
};

const formatSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const removeFile = (index: number) => {
  uploadedFiles.value.splice(index, 1);
  if (imageEditMode.value) {
    const remainingImages = uploadedFiles.value.filter((f) => isImageAttachment(f)).length;
    if (remainingImages === 0) {
      exitImageEditMode();
    } else if (remainingImages < 2) {
      imageEditHasReference.value = false;
    }
  }
};

// 核心文件上传逻辑
/**
 * 上传一个文件到工作区，返回**是否真的传上去了**。
 *
 * 返回值不是可有可无的：调用方（改图页的「完成」）必须知道图有没有挂上去，
 * 否则上传失败后仍然进入改图模式，发送出去就是一句「沿用所附图片进行修改」
 * 配着一片空白——模型找不到图，用户白等一轮还丢了自己选的图。
 */
const uploadSingleFile = async (file: File): Promise<boolean> => {
  lastUploadError.value = "";
  if (isInteractionLocked.value) {
    // 不能只 `return false`：调用方（改图页「完成」）拿到的是一句「没成功」，
    // 但屏幕上什么都不会发生，用户只会以为按钮坏了。锁的原因要说出来。
    lastUploadError.value = "正在生成回复，等这一轮结束后再试";
    showToast("正在生成回复，等这一轮结束后再上传文件", "warning");
    return false;
  }
  if (file.size > 20 * 1024 * 1024) {
    lastUploadError.value = "文件大小不能超过 20MB";
    showToast("文件大小不能超过 20MB", "error");
    return false;
  }
  const name = file.name;
  const ext = '.' + name.split('.').pop()?.toLowerCase();
  const forbiddenExts = ['.exe', '.bat', '.sh', '.cmd', '.msi', '.php', '.js', '.html'];
  if (forbiddenExts.includes(ext)) {
    lastUploadError.value = "暂不支持上传该类型的危险脚本文件";
    showToast("暂不支持上传该类型的危险脚本文件", "warning");
    return false;
  }

  isUploading.value = true;
  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await axios.post("/api/v1/chat/upload", formData);
    if (res.data && res.data.data) {
      uploadedFiles.value.push(res.data.data);
      return true;
    }
    throw new Error("上传失败");
  } catch (error: any) {
    console.error("Upload error:", error);
    // 后端报错是 FastAPI 的 `{"detail": "..."}`，不是 `message`。以前只读 message，
    // 于是「文件数已达上限」「禁止上传该类型文件」这些中文原因全都变成了
    // 「Request failed with status code 413」——排障时等于没说。
    const reason =
      error.response?.data?.detail ||
      error.response?.data?.message ||
      error.message ||
      "上传文件时出错，请重试";
    lastUploadError.value = String(reason);
    showToast(String(reason), "error");
    return false;
  } finally {
    isUploading.value = false;
  }
};

const handleFileChange = async (e: Event) => {
  const target = e.target as HTMLInputElement;
  if (!target.files) return;
  const filesArray = Array.from(target.files);
  for (const file of filesArray) {
    await uploadSingleFile(file);
  }
  target.value = ''; // 清空 input 避免无法重复选择同一文件
};

// 拖拽与粘贴
const handlePaste = async (e: ClipboardEvent) => {
  if (isInteractionLocked.value) return;
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of Array.from(items)) {
    if (item.kind === 'file') {
      const file = item.getAsFile();
      if (file) {
        e.preventDefault();
        await uploadSingleFile(file);
      }
    }
  }
};

const handleDropFile = async (e: DragEvent) => {
  if (isInteractionLocked.value) return;
  e.preventDefault();
  const files = e.dataTransfer?.files;
  if (!files) return;
  for (const file of Array.from(files)) {
    await uploadSingleFile(file);
  }
};

const addBase64Image = async (dataUrl: string, filename = 'crop_image.png') => {
  try {
    const res = await fetch(dataUrl);
    const blob = await res.blob();
    const file = new File([blob], filename, { type: blob.type || 'image/png' });
    await uploadSingleFile(file);
  } catch (err) {
    console.error('Failed to convert and upload base64 image', err);
  }
};

// 暴露属性给父组件
const focus = () => {
  inputRef.value?.focus();
};

defineExpose({
  uploadedFiles,
  uploadSingleFile,
  addBase64Image,
  focus,
  openCommandDrawer,
  closeCommandDrawer,
  pushLocalFile,
  mountedFilePaths,
  openContextUsageForTour,
  closeContextUsageForTour,
});
</script>

<template>
    <div class="flex-shrink-0 bg-white dark:bg-gray-900 flex flex-col relative z-20">
      <slot name="banner"></slot>

      <div
        :class="isMobileViewport
          ? 'px-3 pt-1 pb-[calc(env(safe-area-inset-bottom,0px)+0.625rem)]'
          : 'px-3 pt-1 pb-2'"
      >
        <!-- Shortcut Bar (desktop only) -->
        <div
          v-if="showShortcutBar"
          ref="shortcutBarRef"
          data-shortcut-bar
          class="flex items-center space-x-2 mb-2 px-1 relative h-8"
          :class="{ 'select-none': isProcessing }"
        >
            <!-- 生成中不再整条禁用：/new 与 /history 保持可用，其余逐项置灰并挡指针，
                 判定口径见 isShortcutAvailableWhileProcessing。 -->
            <!-- 1. Left Toggle Button (Visible on all devices now) -->
            <div
              @click="emit('toggle-shortcuts')"
              class="flex items-center space-x-1 cursor-pointer select-none group flex-shrink-0 bg-white dark:bg-gray-900 pr-2 z-10"
              :class="{ 'pointer-events-none opacity-50': isProcessing }"
            >
                <CommandLineIcon class="h-3.5 w-3.5 shrink-0 text-gray-400 group-hover:text-primary" aria-hidden="true" />
                <span class="text-[10px] font-black text-gray-400 group-hover:text-primary transition-colors tracking-tighter">快捷指令</span>
                <svg class="w-3 h-3 text-gray-300 group-hover:text-primary transition-transform duration-200 rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M19 9l-7 7-7-7" /></svg>
            </div>

            <!-- 2. Middle Content -->
            <div class="flex-1 min-w-0 relative">
                <div class="flex flex-1 min-w-0 items-center gap-2">
                            <div class="relative flex-1 min-w-0 overflow-hidden">
                                <div
                                  ref="shortcutScrollRef"
                                  class="flex items-center gap-2 min-w-0 overflow-x-auto overflow-y-hidden overscroll-x-contain no-scrollbar touch-pan-x"
                                  @wheel="onShortcutWheel"
                                >
                                    <template v-for="cmd in visibleRowSystemCommands" :key="'row-sys-'+cmd.id">
                                        <!-- 新会话（含右侧 ⌄ 会话类型菜单）与历史在生成中保持可用：
                                             前者先弹二次确认、后者只开关侧栏，都不会把消息投进正在运行的会话。 -->
                                        <div
                                          v-if="cmd.id === 'sys_clear'"
                                          :ref="setNewConversationMenuRef"
                                          class="relative flex items-center shrink-0"
                                        >
                                          <button :disabled="cmd.disabled" @click="handleShortcutClick(cmd)" class="inline-flex items-center gap-1 px-2.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-l-full whitespace-nowrap hover:bg-gray-200 transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"><component :is="getSystemCommandIcon(cmd)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ cmd.label }}</button>
                                          <button type="button" @mousedown.stop @click.stop="toggleNewConversationMenu" class="px-1.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-r-full border-l border-white/70 dark:border-gray-700 hover:bg-gray-200" title="选择会话类型">⌄</button>
                                        </div>
                                        <button v-else :disabled="cmd.disabled" @click="handleShortcutClick(cmd)" class="inline-flex items-center gap-1 px-2.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-full whitespace-nowrap hover:bg-gray-200 transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-gray-100" :class="{ 'pointer-events-none opacity-40': isProcessing && !isShortcutAvailableWhileProcessing(cmd) }"><component v-if="getSystemCommandIcon(cmd)" :is="getSystemCommandIcon(cmd)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ cmd.label }}</button>
                                    </template>
                                    <div v-if="showShortcutDivider" class="w-px h-3 bg-gray-200 dark:bg-gray-700 flex-shrink-0"></div>
                                    <template v-for="cmd in visibleRowUserCommands" :key="'row-user-'+cmd.id">
                                        <button @click="handleShortcutClick(cmd)" class="inline-flex items-center gap-1 px-2.5 py-1 text-[10px] font-bold bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300 border border-blue-100/50 dark:border-blue-800 rounded-full whitespace-nowrap hover:bg-blue-100 transition-colors flex-shrink-0" :class="{ 'pointer-events-none opacity-40': isProcessing }"><component v-if="getSystemCommandIcon(cmd)" :is="getSystemCommandIcon(cmd)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ cmd.label }}</button>
                                    </template>
                                </div>
                            </div>
                            <button
                                v-if="hasShortcutChips"
                                data-shortcut-more
                                type="button"
                                @click.stop="toggleCommandDrawer"
                                class="flex-shrink-0 inline-flex items-center text-[10px] font-black text-primary hover:opacity-80 transition-all whitespace-nowrap"
                                :class="isProcessing ? 'pointer-events-none opacity-50' : (isDrawerExpanded ? 'opacity-100' : '')"
                            >
                                {{ isDrawerExpanded ? '收起' : '更多' }}
                                <svg class="w-3 h-3 ml-0.5 transition-transform" :class="{ 'rotate-180': isDrawerExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M19 9l-7 7-7-7" /></svg>
                            </button>
                </div>
            </div>

            <!-- Add Button -->
            <button
                @click="emit('open-command-manager')"
                class="flex-shrink-0 p-1.5 text-gray-400 hover:text-primary transition-all rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 group"
                :class="{ 'pointer-events-none opacity-50': isProcessing }"
                title="新建快捷指令"
            >
                <svg class="w-4 h-4 transform group-hover:rotate-90 transition-transform duration-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" /></svg>
            </button>
        </div>

        <!-- Attachments Preview Bar -->
        <div v-if="uploadedFiles.filter(f => f.type !== 'knowledge_settings').length > 0" class="flex flex-wrap gap-2 px-1 mb-2 max-h-36 overflow-y-auto no-scrollbar py-1">
            <template v-for="(file, idx) in uploadedFiles" :key="idx">
              <div v-if="file.type !== 'knowledge_settings'" class="relative flex items-center group bg-gray-100/80 dark:bg-gray-800/80 border border-gray-200/30 dark:border-gray-700/30 rounded-lg p-1.5 pr-8 max-w-[200px] transition-all hover:bg-white dark:hover:bg-gray-800 hover:shadow-sm" :title="file.type === 'metadata_dataset' ? `已选择本轮数据集【${file.filename}】，本次提问将优先锁定在此范围内检索。` : ''">
                  <!-- Image Preview -->
                  <AttachmentImageThumb
                    v-if="isImage(file)"
                    :file="file"
                    clickable
                    class="mr-2"
                    @click="openImagePreview"
                  />
                  <!-- Metadata Dataset Icon -->
                  <div v-else-if="file.type === 'metadata_dataset'" class="w-8 h-8 rounded bg-purple-500/10 dark:bg-purple-500/20 flex items-center justify-center text-purple-500 text-sm flex-shrink-0 mr-2">
                      📊
                  </div>
                  <!-- Knowledge Base Icon -->
                  <div v-else-if="file.type === 'knowledge_base'" class="w-8 h-8 rounded bg-emerald-500/10 dark:bg-emerald-500/20 flex items-center justify-center text-emerald-500 text-sm flex-shrink-0 mr-2">
                      📚
                  </div>
                  <!-- Skill Icon -->
                  <div v-else-if="file.type === 'skill'" class="w-8 h-8 rounded bg-amber-500/10 dark:bg-amber-500/20 flex items-center justify-center text-amber-500 text-sm flex-shrink-0 mr-2 font-mono">
                      ⚙️
                  </div>
                  <!-- Memory Icon -->
                  <div v-else-if="file.type === 'memory'" class="w-8 h-8 rounded bg-indigo-500/10 dark:bg-indigo-500/20 flex items-center justify-center text-indigo-500 text-sm flex-shrink-0 mr-2">
                      🧠
                  </div>
                  <!-- Server File Icon -->
                  <div v-else-if="file.type === 'local_file'" class="w-8 h-8 rounded bg-blue-500/10 dark:bg-blue-500/20 flex items-center justify-center text-blue-500 text-sm flex-shrink-0 mr-2">
                      💻
                  </div>
                  <!-- Server Dir Icon -->
                  <div v-else-if="file.type === 'local_dir'" class="w-8 h-8 rounded bg-yellow-500/10 dark:bg-yellow-500/20 flex items-center justify-center text-yellow-500 text-sm flex-shrink-0 mr-2">
                      📁
                  </div>
                  <!-- File Icon -->
                  <div v-else class="w-8 h-8 rounded bg-primary/10 dark:bg-primary/20 flex items-center justify-center text-primary text-sm flex-shrink-0 mr-2">
                      📄
                  </div>
                  <!-- Metadata -->
                  <div class="flex-1 min-w-0 flex flex-col">
                      <span class="text-xs font-bold text-gray-700 dark:text-gray-200 truncate">{{ file.filename }}</span>
                      <span class="text-[9px] text-gray-400 font-mono">
                          {{ 
                            file.type === 'skill' ? '生态技能' : 
                            file.type === 'knowledge_base' ? '知识库' : 
                            file.type === 'metadata_dataset' ? '数据集' :
                            file.type === 'memory' ? '记忆记录' : 
                            file.type === 'local_file' ? (isImage(file) ? '服务器图片' : '服务器文件') :
                            file.type === 'local_dir' ? '服务器目录' :
                            formatSize(file.size) 
                          }}
                      </span>
                  </div>
                  <!-- Remove Button -->
                  <button @click="removeFile(idx)" class="absolute right-1 top-1/2 -translate-y-1/2 w-5 h-5 flex items-center justify-center rounded-full bg-gray-200/50 hover:bg-red-500 hover:text-white dark:bg-gray-700/50 text-gray-500 dark:text-gray-400 opacity-0 group-hover:opacity-100 transition-all duration-150 focus:outline-none">
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                  </button>
              </div>
            </template>
            
            <!-- Uploading indicator -->
            <div v-if="isUploading" class="flex items-center space-x-2 bg-gray-100/50 dark:bg-gray-800/50 border border-dashed border-gray-300 dark:border-gray-700 rounded-lg px-3 py-1.5 max-w-[200px]">
                <div class="w-3.5 h-3.5 border-2 border-primary border-t-transparent rounded-full animate-spin"></div>
                <span class="text-[10px] text-gray-400 font-medium">正在上传...</span>
            </div>
        </div>

        <!-- Input Box -->
        <div
          data-tour="composer"
          @dragover.prevent
          @drop="handleDropFile"
          class="relative flex flex-col rounded-2xl border bg-white px-3 py-2.5 transition-all duration-300 dark:bg-gray-800"
          :class="isProcessing
            ? 'border-gray-300 dark:border-gray-600'
            : 'border-gray-200 dark:border-gray-700 focus-within:border-gray-400 focus-within:ring-2 focus-within:ring-gray-900/5 dark:focus-within:border-gray-500 dark:focus-within:ring-gray-100/10'"
        >
            <!-- 三点跳动 Loading 指示器 + 生成中跑道动画 -->
            <div v-if="isInteractionLocked" class="absolute top-3 left-3 right-3 pointer-events-none z-20">
                <div class="flex items-center space-x-1.5">
                    <span class="ai-dot" style="animation-delay: 0ms"></span>
                    <span class="ai-dot" style="animation-delay: 150ms"></span>
                    <span class="ai-dot" style="animation-delay: 300ms"></span>
                    <span class="ml-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400 select-none">{{ isProcessing ? (enableGrounding ? 'AI 正在生成并严格核验证据…' : 'AI 正在努力生成回复中，请稍候。') : isSubmitting ? '准备发送…' : '' }}</span>
                </div>
                <!-- 跑道独立成行：小人不能在文案那一行里横穿过去；设置面板可关闭 -->
                <GeneratingWalker v-if="showGeneratingAnimation" class="mt-1" />
            </div>

            <div
              v-if="showCommandMenu && filteredCommands.length > 0 && !isInteractionLocked"
              class="absolute bottom-full left-0 right-0 z-[100] mb-2 flex max-h-72 flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl animate-fade-in-up dark:border-gray-700 dark:bg-gray-800 sm:max-w-sm"
            >
              <div class="flex items-center justify-between border-b border-gray-100 bg-gray-50 px-3 py-2 dark:border-gray-600 dark:bg-gray-700">
                <div class="flex items-center space-x-2">
                  <span class="text-[10px] font-black uppercase tracking-widest text-gray-400">快捷指令库</span>
                  <span class="rounded-md bg-primary/10 px-1.5 py-0.5 text-[9px] font-bold text-primary">{{ filteredCommands.length }} 匹配</span>
                </div>
                <span class="text-[9px] text-gray-400 hidden sm:inline shrink-0">Enter 选择 · Esc 关闭</span>
              </div>
              <div ref="commandListContainerRef" class="overflow-y-auto p-1 custom-scrollbar">
                <div
                  v-for="(cmd, index) in filteredCommands"
                  :key="cmd.id"
                  @click="cmd.disabled ? null : selectCommand(cmd)"
                  class="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 transition-all"
                  :class="[
                    cmd.disabled ? 'opacity-40 cursor-not-allowed' : '',
                    index === activeCommandIndex ? 'bg-primary/10 ring-1 ring-primary/20 dark:bg-primary/20' : 'hover:bg-gray-50 dark:hover:bg-gray-700'
                  ]"
                >
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center space-x-2">
                      <span class="flex items-center gap-1.5 truncate text-sm font-bold text-gray-900 dark:text-gray-100" :class="[index === activeCommandIndex && !cmd.disabled ? 'text-primary' : '', cmd.disabled ? 'text-gray-400 dark:text-gray-500' : '']">
                        <component v-if="getSystemCommandIcon(cmd)" :is="getSystemCommandIcon(cmd)" class="h-4 w-4 shrink-0" aria-hidden="true" />
                        {{ cmd.label }}
                      </span>
                      <span v-if="cmd.disabled" class="rounded border border-yellow-200 bg-yellow-50 px-1 py-0.5 text-[8px] font-bold text-yellow-600 dark:border-yellow-900/30 dark:bg-yellow-950/20">功能未启用</span>
                      <span v-if="String(cmd.id).startsWith('sys_')" class="rounded border border-gray-200 bg-gray-100 px-1 py-0.5 text-[8px] font-black uppercase tracking-tighter text-gray-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-400">SYS</span>
                    </div>
                    <div class="truncate font-mono text-[10px] text-gray-400 opacity-70">
                      {{ cmd.command }}
                    </div>
                  </div>
                  <div class="flex items-center shrink-0">
                    <span
                      v-if="index === activeCommandIndex && !cmd.disabled"
                      class="rounded bg-primary/20 px-1 py-0.5 text-[9px] font-bold text-primary dark:bg-primary/30 leading-none"
                    >
                      ↵
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 本次生成参数 / 改图模式标记：只在这里露出，正文保持干净 -->
            <div v-if="imageEditMode || imageParamChips.length" class="relative z-10 flex flex-wrap items-center gap-1.5 pb-1.5">
              <span
                v-if="imageEditMode"
                class="inline-flex items-center gap-1 rounded-md border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-600 dark:border-rose-700/60 dark:bg-rose-900/30 dark:text-rose-300"
              >
                {{ imageEditHasReference ? '改图 · 含参考图' : '改图' }}
                <button
                  type="button"
                  class="text-rose-400 transition-colors hover:text-rose-700 dark:hover:text-rose-100"
                  title="退出改图模式（改为普通问图）"
                  @click="exitImageEditMode"
                >
                  <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path stroke-linecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
                </button>
              </span>
              <span
                v-for="chip in imageParamChips"
                :key="chip.key"
                class="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[11px] text-gray-600 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200"
                :class="chip.key === 'size' ? 'font-mono' : ''"
              >
                {{ chip.label }}
                <button
                  type="button"
                  class="text-gray-400 transition-colors hover:text-gray-700 dark:hover:text-gray-100"
                  :title="`移除${chip.label}`"
                  @click="chip.clear()"
                >
                  <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path stroke-linecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
                </button>
              </span>
            </div>

            <textarea ref="inputRef" :value="modelValue" :disabled="isInteractionLocked" @input="handleInput" @focus="handleFocus" @keydown="handleKeydown" @compositionstart="handleCompositionStart" @compositionend="handleCompositionEnd" @paste="handlePaste" rows="1" class="w-full bg-transparent border-none outline-none focus:ring-0 text-base sm:text-sm placeholder:text-sm px-0 py-1 resize-none max-h-32 text-gray-900 dark:text-gray-100 placeholder-gray-400 peer z-10 relative disabled:cursor-not-allowed" :class="[
              isInteractionLocked ? 'min-h-[46px] opacity-0 pointer-events-none' : 'min-h-[46px] opacity-100',
              textareaPaddingRightClass,
            ]" :placeholder="inputPlaceholder"></textarea>

            <!-- 输入框内部右上角状态浮标组 (反幻觉浮标 + 上下文用量胶囊，完全对齐且低饱和淡雅配色) -->
            <div data-tour="composer-pills" class="absolute right-2 top-2 z-30 flex items-center gap-1.5 pointer-events-auto">
              <!-- 反幻觉校验浮标 (开启时呈现，极淡灰底+微绿点缀) -->
              <div
                v-if="enableGrounding"
                data-testid="grounding-status-pill"
                class="inline-flex h-[22px] box-border items-center gap-1 rounded-full border border-slate-200/80 bg-slate-50/80 hover:bg-slate-100/90 dark:border-slate-700/70 dark:bg-slate-800/70 dark:hover:bg-slate-700/80 px-2 text-[10px] font-medium leading-none text-slate-600 dark:text-slate-300 transition-colors select-none"
              >
                <button
                  type="button"
                  class="flex items-center gap-1 hover:text-slate-900 dark:hover:text-slate-100 transition-colors focus:outline-none"
                  @click="emit('open-grounding-settings')"
                  :title="`反幻觉校验已开启 (${groundingBlockMode === 'stream_with_retraction' ? '实时撤回' : '严格缓冲'})，点击可调整设置`"
                >
                  <span class="relative flex h-1.5 w-1.5 shrink-0">
                    <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60"></span>
                    <span class="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span>
                  </span>
                  <svg class="h-3 w-3 text-emerald-600/75 dark:text-emerald-400/75 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                  <span>反幻觉</span>
                  <span class="opacity-60 text-slate-400 dark:text-slate-500 text-[9px]">({{ groundingBlockMode === 'stream_with_retraction' ? '实时' : '缓冲' }})</span>
                </button>
                <button
                  type="button"
                  class="ml-0.5 rounded-full p-0.5 text-slate-400 hover:text-slate-600 hover:bg-slate-200/60 dark:text-slate-400 dark:hover:text-slate-200 dark:hover:bg-slate-700/80 transition-colors focus:outline-none"
                  @click.stop="emit('disable-grounding')"
                  title="关闭反幻觉校验"
                  aria-label="关闭反幻觉校验"
                >
                  <svg class="h-2.5 w-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <!-- 上下文使用详情胶囊 -->
              <div
                v-if="contextUsage && contextUsage.physical_window"
                ref="contextUsageContainerRef"
                class="relative flex items-center"
              >
                <!-- 自动预热完成：气泡指向用量浮标，点开即可看到沙箱状态（不再用 toast 打断） -->
                <transition
                  enter-active-class="transition-all duration-300 ease-out"
                  enter-from-class="opacity-0 translate-y-1 scale-95"
                  enter-to-class="opacity-100 translate-y-0 scale-100"
                  leave-active-class="transition-all duration-200 ease-in"
                  leave-from-class="opacity-100 translate-y-0 scale-100"
                  leave-to-class="opacity-0 translate-y-1 scale-95"
                >
                  <div
                    v-if="sandboxWarmHintVisible"
                    data-testid="sandbox-warm-hint"
                    class="absolute bottom-full right-0 z-50 mb-2.5 flex items-center gap-2.5 whitespace-nowrap rounded-xl border border-primary/20 bg-primary/95 px-3 py-2 text-[12px] text-white shadow-2xl backdrop-blur-md dark:border-slate-700/60 dark:bg-slate-900/95"
                  >
                    <!-- 底部小尖角：指向下方的用量浮标 -->
                    <div class="absolute -bottom-1.5 right-4 h-3 w-3 rotate-45 border-b border-r border-primary/20 bg-primary/95 dark:border-slate-700/60 dark:bg-slate-900/95" />

                    <button
                      type="button"
                      class="flex items-center gap-2.5 text-left"
                      title="打开上下文用量面板查看沙箱状态"
                      @click.stop="toggleContextUsageDetails()"
                    >
                      <svg class="h-4 w-4 shrink-0 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
                      </svg>
                      <span class="flex flex-col">
                        <span class="font-bold leading-tight">{{ sandboxWarmHintTitle }}</span>
                        <span class="mt-0.5 text-[11px] leading-tight text-white/85 dark:text-slate-300">{{ sandboxWarmHintSubtitle }}</span>
                      </span>
                    </button>
                    <button
                      type="button"
                      class="ml-1 rounded-md p-1 text-white/70 transition-colors hover:bg-white/20 hover:text-white dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
                      title="关闭提示"
                      @click.stop="emit('dismiss-sandbox-warm-hint')"
                    >
                      <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                </transition>

                <button
                  type="button"
                  data-testid="context-usage-indicator"
                  class="inline-flex h-[22px] box-border items-center gap-1 rounded-full border px-2 text-[10px] font-medium leading-none transition-colors focus:outline-none focus:ring-2 focus:ring-primary/20"
                  :class="contextUsageTone.badge"
                  :aria-expanded="showContextUsageDetails"
                  aria-haspopup="dialog"
                  aria-controls="context-usage-details"
                  :aria-label="contextUsageReadingLabel"
                  :title="contextCalibrationNote ? `${contextUsageReadingLabel} · ${contextCalibrationNote}` : contextUsageReadingLabel"
                  @click.stop="toggleContextUsageDetails"
                >
                    <span aria-hidden="true" class="h-1.5 w-1.5 rounded-full" :class="contextUsageTone.dot" />
                    <span>{{ formatContextTokens(contextCurrentTokens) }}</span>
                    <span class="font-mono tabular-nums opacity-70">/ {{ formatContextTokens(contextUsage.physical_window) }}</span>
                </button>

                <div
                  v-if="showContextUsageDetails"
                  ref="contextUsageDetailsRef"
                  id="context-usage-details"
                  data-testid="context-usage-details"
                  role="dialog"
                  aria-label="上下文使用详情"
                  aria-live="polite"
                  class="absolute right-0 z-40 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-gray-200 bg-white/95 p-3 text-[10px] shadow-xl dark:border-gray-700 dark:bg-gray-800/95"
                  :class="contextUsageDetailsPlacement === 'above'
                    ? 'bottom-[calc(100%+0.5rem)]'
                    : 'top-[calc(100%+0.5rem)]'"
                  @click.stop
                >
                    <div class="flex items-center justify-between gap-2 text-gray-500 dark:text-gray-400">
                        <span class="font-medium">上下文使用</span>
                        <span class="flex items-center gap-1.5">
                            <span
                              class="rounded-full border px-1.5 py-0.5 text-[9px] font-medium"
                              :class="contextUsageTone.badge"
                            >
                              {{ contextUsageStatusLabel }}
                            </span>
                            <span class="flex items-center gap-1.5 font-mono tabular-nums" :class="contextUsageTone.text">
                            <span>
                                {{ formatContextTokens(contextCurrentTokens) }} /
                                {{ formatContextTokens(contextUsage.physical_window) }}
                            </span>
                            <span class="opacity-75">· {{ contextUsagePercentLabel }}</span>
                            </span>
                        </span>
                    </div>
                    <p
                      v-if="contextCalibrationNote"
                      data-testid="context-usage-calibration-note"
                      class="mt-1 text-[9px] leading-tight text-amber-600 dark:text-amber-400"
                    >
                      {{ contextCalibrationNote }}
                    </p>
                    <div
                      class="relative mt-2 h-1 overflow-visible rounded-full bg-gray-100 dark:bg-gray-700"
                      role="progressbar"
                      aria-label="上下文使用量"
                      :aria-valuenow="contextUsagePercent"
                      aria-valuemin="0"
                      aria-valuemax="100"
                    >
                        <div
                          v-if="sessionContextBreakdown"
                          class="flex h-full overflow-hidden rounded-full transition-all duration-300"
                          :style="{ width: `${contextUsagePercent}%` }"
                          data-testid="session-context-breakdown-segment"
                        >
                          <div
                            v-for="item in sessionContextBreakdownItems"
                            :key="item.key"
                            class="h-full transition-all duration-300"
                            :class="item.color"
                            :style="{ width: contextBreakdownSegmentWidth(item.value) }"
                            :title="`${item.label} ${formatContextTokens(item.value)}`"
                          />
                        </div>
                        <div
                          v-else
                          class="h-full rounded-full transition-all duration-300"
                          :class="contextUsageTone.track"
                          :style="{ width: `${contextUsagePercent}%` }"
                        />
                        <span
                          v-if="contextRequestInputPercent !== null"
                          class="absolute -top-0.5 h-2 w-0.5 rounded-full"
                          :class="contextUsageTone.marker"
                          :style="{ left: `${contextRequestInputPercent}%` }"
                          :title="`请求输入上限 ${formatContextTokens(contextUsage.request_input_budget)}`"
                        />
                    </div>
                    <div class="mt-2 space-y-1 text-gray-400 dark:text-gray-500">
                        <div class="flex items-center justify-between gap-3">
                          <span
                            class="flex cursor-help items-center gap-1.5"
                            title="达到此水位后，系统会整理较早对话，优先压缩成摘要。"
                            aria-label="自动压缩触发线：达到此水位后，系统会整理较早对话，优先压缩成摘要。"
                          >
                            <span class="h-1.5 w-1.5 rounded-full bg-rose-400" aria-hidden="true" />
                            <span>自动压缩触发线</span>
                          </span>
                          <span class="font-mono tabular-nums">{{ formatContextTokens(contextUsage.history_budget) }}</span>
                        </div>
                        <div class="flex items-center justify-between gap-3">
                          <span class="flex items-center gap-1.5">
                            <span class="h-1.5 w-1.5 rounded-full bg-red-400" aria-hidden="true" />
                            <span>请求输入上限</span>
                          </span>
                          <span class="font-mono tabular-nums">{{ formatContextTokens(contextUsage.request_input_budget) }}</span>
                        </div>
                    </div>
                    <div
                      v-if="sessionContextBreakdown"
                      class="mt-2 border-t border-gray-100 pt-2 text-gray-500 dark:border-gray-700 dark:text-gray-400"
                    >
                      <div class="flex items-center justify-between gap-3">
                        <span class="font-medium">会话整体构成</span>
                        <span class="font-mono text-[9px]">
                          估算 {{ formatContextTokens(sessionContextBreakdown.total_tokens) }}
                        </span>
                      </div>
                      <div class="mt-1.5 space-y-1">
                        <div
                          v-for="item in sessionContextBreakdownItems"
                          :key="item.key"
                          class="flex items-center justify-between gap-3 text-[10px]"
                        >
                          <span class="flex items-center gap-1.5">
                            <span class="h-1.5 w-1.5 rounded-sm" :class="item.color" aria-hidden="true" />
                            <span>{{ item.label }}</span>
                          </span>
                          <span class="font-mono tabular-nums">{{ formatContextTokens(item.value) }}</span>
                        </div>
                      </div>
                    </div>
                    <div
                      v-if="contextCompactionEnabled"
                      class="mt-2 space-y-2 border-t border-gray-100 pt-2 text-gray-500 dark:border-gray-700 dark:text-gray-400"
                    >
                      <div class="flex items-center justify-between gap-2">
                        <span class="flex min-w-0 items-center gap-1.5">
                          <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-primary/70" aria-hidden="true" />
                          <span class="truncate font-medium text-gray-600 dark:text-gray-300">上下文压缩</span>
                        </span>
                        <button
                          type="button"
                          data-testid="context-compaction-indicator"
                          class="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 font-mono text-[9px] font-medium text-gray-400 transition-colors hover:bg-gray-100 hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-500 dark:hover:bg-gray-700 dark:hover:text-primary"
                          :aria-expanded="showContextCompactionDetails"
                          aria-haspopup="dialog"
                          aria-controls="context-compaction-details"
                          :aria-label="`上下文压缩 ${contextCompactionCount} 次`"
                          :title="`上下文压缩 ${contextCompactionCount} 次`"
                          :disabled="isInteractionLocked"
                          @click.stop="toggleContextCompactionDetails"
                        >
                          {{ contextCompactionCount }} 次记录
                        </button>
                      </div>
                      <div class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 gap-y-1.5 text-[9px]">
                        <span class="text-gray-400 dark:text-gray-500">压缩方式</span>
                        <div class="flex min-w-0 rounded-lg bg-gray-100 p-0.5 dark:bg-gray-700/70" role="group" aria-label="上下文压缩方式">
                          <button
                            type="button"
                            data-testid="context-compaction-mode-fast"
                            class="min-w-0 flex-1 rounded-md px-2 py-1 font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-primary/20"
                            :class="contextCompactionMode === 'fast'
                              ? 'bg-white text-primary shadow-sm dark:bg-gray-800 dark:text-primary'
                              : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'"
                            :disabled="isInteractionLocked || contextCompactionActionLoading"
                            @click.stop="contextCompactionMode = 'fast'"
                          >
                            快速
                          </button>
                          <button
                            type="button"
                            data-testid="context-compaction-mode-smart"
                            class="min-w-0 flex-1 rounded-md px-2 py-1 font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-primary/20"
                            :class="contextCompactionMode === 'smart'
                              ? 'bg-white text-primary shadow-sm dark:bg-gray-800 dark:text-primary'
                              : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'"
                            :disabled="isInteractionLocked || contextCompactionActionLoading"
                            title="智能压缩会调用模型生成语义摘要"
                            @click.stop="contextCompactionMode = 'smart'"
                          >
                            智能 <span class="text-[8px] opacity-70">AI</span>
                          </button>
                        </div>
                        <span class="text-gray-400 dark:text-gray-500">保留比例</span>
                        <select
                          v-model.number="contextCompactionRetainRatio"
                          data-testid="context-compaction-retain-ratio"
                          class="min-w-0 w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-gray-700 outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:opacity-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
                          :disabled="isInteractionLocked || contextCompactionActionLoading"
                          aria-label="上下文压缩保留比例"
                          title="选择压缩强度"
                          @click.stop
                        >
                          <option :value="0.75">轻度 75%</option>
                          <option :value="0.5">标准 50%</option>
                          <option :value="0.25">深度 25%</option>
                        </select>
                      </div>
                      <button
                          type="button"
                          data-testid="context-compaction-manual"
                          class="flex w-full items-center justify-center rounded-lg bg-primary px-3 py-1.5 text-[10px] font-semibold text-white shadow-sm transition-colors hover:bg-primary-hover focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-50"
                          :disabled="isInteractionLocked || contextCompactionActionLoading"
                          title="立即压缩上下文"
                          aria-label="立即压缩上下文"
                          @click.stop="emit('manual-context-compaction', contextCompactionRetainRatio, contextCompactionMode)"
                        >
                          {{ contextCompactionActionLoading ? '压缩中…' : contextCompactionMode === 'smart' ? '立即智能压缩' : '立即快速压缩' }}
                      </button>
                      <p v-if="contextCompactionMode === 'smart'" class="text-[9px] leading-4 text-primary/80">
                        智能压缩会调用模型生成语义摘要，失败时自动回退为快速压缩。
                      </p>
                      <div v-if="latestContextCompactionSavings" class="text-[9px] text-emerald-600 dark:text-emerald-400">
                        {{ latestContextCompactionSavings }}
                      </div>
                    </div>
                    <div
                      v-if="sandboxPolicyLabel"
                      data-tour="sandbox-block"
                      class="mt-2 flex flex-col gap-1.5 border-t border-gray-100 pt-2 text-gray-400 dark:border-gray-700 dark:text-gray-500"
                    >
                        <div class="flex items-center justify-between gap-2">
                          <span class="flex items-center gap-1.5">
                            <component :is="sandboxPolicyIcon" class="h-4 w-4 shrink-0 text-gray-500 dark:text-gray-400" aria-hidden="true" />
                            <span>Sandbox 策略</span>
                          </span>
                          <span
                            class="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[9px] font-medium"
                            :class="sandboxPolicyBadgeClass"
                          >
                            {{ sandboxPolicyLabel }}
                          </span>
                        </div>

                        <!-- 沙箱运行状态与控制明细 -->
                        <div
                          v-if="isSandboxBackendPolicy"
                          class="flex flex-col gap-1.5 rounded-lg bg-gray-50/90 dark:bg-gray-800/70 p-2 text-[10px] font-mono border border-gray-100/90 dark:border-gray-700/70"
                        >
                          <div class="flex items-center justify-between gap-2">
                            <div class="flex items-center gap-1.5 min-w-0">
                              <span
                                class="inline-block h-2 w-2 shrink-0 rounded-full"
                                :class="{
                                  'bg-emerald-500 shadow-sm shadow-emerald-500/50': (sandboxWorkspaceStatus || 'idle') === 'running',
                                  'bg-amber-400 animate-pulse': (sandboxWorkspaceStatus || 'idle') === 'starting' || (sandboxWorkspaceStatus || 'idle') === 'stopping',
                                  'bg-rose-500 shadow-sm shadow-rose-500/50': (sandboxWorkspaceStatus || 'idle') === 'error',
                                  'bg-gray-300 dark:bg-gray-600': (sandboxWorkspaceStatus || 'idle') === 'idle'
                                }"
                              ></span>
                              <span class="shrink-0 whitespace-nowrap font-medium text-gray-700 dark:text-gray-200">
                                {{ sandboxStatusText }}
                              </span>
                              <span
                                v-if="sandboxWorkspaceInstanceId"
                                class="truncate text-[9px] text-gray-400 dark:text-gray-500 max-w-[150px]"
                                :title="sandboxInstanceLabel"
                              >
                                {{ sandboxBackend === 'k8s' ? sandboxWorkspaceInstanceId : sandboxWorkspaceInstanceId.slice(0, 12) }}
                              </span>
                            </div>

                            <div class="shrink-0 flex items-center gap-1">
                              <button
                                v-if="(sandboxWorkspaceStatus || 'idle') === 'idle' || (sandboxWorkspaceStatus || 'idle') === 'error'"
                                type="button"
                                class="rounded bg-indigo-600 px-2 py-0.5 text-[9px] font-medium text-white shadow-sm hover:bg-indigo-500 active:scale-95 transition-all disabled:opacity-50"
                                :disabled="isInteractionLocked"
                                :title="(sandboxWorkspaceStatus || 'idle') === 'error' ? (sandboxWorkspaceError || '重试启动沙箱') : (sandboxBackend === 'k8s' ? '启动当前用户的 Kubernetes 沙箱 Pod' : '启动当前用户的 Docker 沙箱容器')"
                                @click.stop="emit('start-sandbox-workspace')"
                              >
                                {{ sandboxStartButtonLabel }}
                              </button>
                              <button
                                type="button"
                                :disabled="sandboxRefreshing"
                                class="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-gray-400 dark:hover:text-indigo-300 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
                                title="手动检测刷新沙箱状态（创建中/停止中也允许查询）"
                                @click.stop="emit('refresh-sandbox-workspace', true)"
                              >
                                <ArrowPathIcon class="h-3 w-3" :class="{ 'animate-spin': sandboxRefreshing || (sandboxWorkspaceStatus || 'idle') === 'starting' || (sandboxWorkspaceStatus || 'idle') === 'stopping' }" aria-hidden="true" />
                                <span>刷新</span>
                              </button>

                              <!-- 沙箱运行时的操作下拉菜单 -->
                              <div
                                v-if="(sandboxWorkspaceStatus || 'idle') === 'running'"
                                ref="sandboxActionsDropdownRef"
                                class="relative inline-block text-left"
                              >
                                <button
                                  type="button"
                                  class="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-gray-400 dark:hover:text-indigo-300 dark:hover:bg-gray-700 transition-colors" :class="{ 'text-indigo-600 bg-indigo-50 dark:text-indigo-300 dark:bg-gray-700': showSandboxActionsMenu }"
                                  title="沙箱管理操作"
                                  @click.stop="showSandboxActionsMenu = !showSandboxActionsMenu"
                                >
                                  <span>操作</span>
                                  <ChevronDownIcon class="h-2.5 w-2.5 transition-transform duration-200" :class="{ 'rotate-180': showSandboxActionsMenu }" />
                                </button>

                                <div
                                  v-if="showSandboxActionsMenu"
                                  class="absolute right-0 bottom-full mb-1.5 z-50 w-28 rounded-lg bg-white dark:bg-gray-800 py-1 shadow-lg ring-1 ring-black/5 dark:ring-white/10 border border-gray-100 dark:border-gray-700 text-[10px] font-sans"
                                  @click.stop
                                >
                                  <!-- 沙箱体检：让 AI 采集系统状态并产出 HTML 报告 -->
                                  <button
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 dark:hover:text-indigo-300 transition-colors"
                                    title="让 AI 对当前沙箱做一次系统体检，并把结果整理成 HTML 报告"
                                    @click="showSandboxActionsMenu = false; emit('sandbox-health-report')"
                                  >
                                    <SparklesIcon class="h-3.5 w-3.5 text-amber-500" />
                                    <span>体检报告</span>
                                  </button>
                                  <div class="my-0.5 border-t border-gray-100 dark:border-gray-700/60"></div>
                                  <!-- 沙箱终端：Docker 进容器，K8s 进 Pod -->
                                  <button
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 dark:hover:text-indigo-300 transition-colors"
                                    @click="showSandboxActionsMenu = false; emit('open-docker-terminal')"
                                  >
                                    <CommandLineIcon class="h-3.5 w-3.5 text-emerald-500" />
                                    <span>{{ sandboxBackend === 'k8s' ? '进入 Pod' : '进入终端' }}</span>
                                  </button>
                                  <button
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 dark:hover:text-indigo-300 transition-colors"
                                    @click="showSandboxActionsMenu = false; emit('restart-sandbox-workspace')"
                                  >
                                    <ArrowPathIcon class="h-3.5 w-3.5 text-indigo-500" />
                                    <span>{{ sandboxBackend === 'k8s' ? '重启 Pod' : '重启容器' }}</span>
                                  </button>
                                  <div class="my-0.5 border-t border-gray-100 dark:border-gray-700/60"></div>
                                  <button
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors"
                                    @click="showSandboxActionsMenu = false; emit('stop-sandbox-workspace')"
                                  >
                                    <PowerIcon class="h-3.5 w-3.5 text-rose-500" />
                                    <span>停止关机</span>
                                  </button>
                                </div>
                              </div>
                            </div>
                          </div>

                          <!-- 运行时长与自动回收说明（仅在运行中展示） -->
                          <div
                            v-if="(sandboxWorkspaceStatus || 'idle') === 'running' && sandboxUptimeFormatted"
                            class="flex items-center justify-between text-[9px] text-gray-400 dark:text-gray-500 pt-1 border-t border-gray-200/50 dark:border-gray-700/50"
                          >
                            <span class="flex items-center gap-1">
                              <ClockIcon class="h-3 w-3 text-emerald-500/80 shrink-0" aria-hidden="true" />
                              <span>运行时长：<strong class="text-gray-600 dark:text-gray-300 font-medium">{{ sandboxUptimeFormatted }}</strong></span>
                            </span>
                            <span class="text-[8.5px] text-gray-400/80 dark:text-gray-500/80" title="沙箱连续空闲 30 分钟后将自动销毁释放资源">
                              {{ sandboxReclaimHint }}
                            </span>
                          </div>
                        </div>
                    </div>
                </div>

                <div
                  v-if="showContextCompactionDetails"
                  ref="contextCompactionDetailsRef"
                  id="context-compaction-details"
                  data-testid="context-compaction-details"
                  role="dialog"
                  aria-label="上下文压缩记录"
                  aria-live="polite"
                  class="absolute right-0 z-40 h-[min(70vh,32rem)] w-[min(94vw,42rem)] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-violet-100 bg-white/95 text-[10px] shadow-xl dark:border-violet-900/60 dark:bg-gray-800/95"
                  :class="contextCompactionDetailsPlacement === 'above'
                    ? 'bottom-[calc(100%+0.5rem)]'
                    : 'top-[calc(100%+0.5rem)]'"
                  @click.stop
                >
                    <div class="flex h-full flex-col">
                      <div class="flex shrink-0 items-center justify-between border-b border-violet-100 px-4 py-2 text-xs font-semibold text-gray-600 dark:border-violet-900/60 dark:text-gray-300">
                        <span>上下文压缩记录</span>
                        <button
                          type="button"
                          data-testid="context-compaction-close"
                          class="rounded-md p-1 text-gray-400 transition-colors hover:bg-violet-50 hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                          aria-label="关闭上下文压缩记录"
                          title="关闭"
                          @click="closeContextCompactionDetails"
                        >
                          <XMarkIcon class="h-4 w-4" />
                        </button>
                      </div>
                      <div class="min-h-0 flex-1">
                        <ContextCompactionTimeline
                          :records="contextCompactionRecords || []"
                          :loading="contextCompactionLoading"
                          :error="contextCompactionError"
                          @refresh="emit('refresh-context-compactions')"
                        />
                      </div>
                    </div>
                </div>
              </div>
            </div>

            <div
              class="relative mt-1 flex min-h-7 flex-nowrap items-center gap-0.5 sm:gap-1.5"
              :class="showModelDropdown || showPlusMenu ? 'z-50' : 'z-20'"
            >
                <!-- Plus Button & Menu (Premium Glassmorphism Style) -->
                <div ref="plusMenuContainerRef" class="relative flex-shrink-0 z-30">
                    <button data-tour="composer-plus" @click="togglePlusMenu" :disabled="isInteractionLocked" class="w-8 h-8 sm:w-7 sm:h-7 flex items-center justify-center rounded-full text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-700 transition-all duration-200 focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-gray-400" :class="{ 'text-primary bg-gray-100 dark:bg-gray-700 rotate-45': showPlusMenu && !isInteractionLocked }" title="添加附件或上下文">
                        <svg class="w-5 h-5 sm:w-4 sm:h-4 transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" />
                        </svg>
                    </button>

                    <input type="file" multiple ref="fileInputRef" @change="handleFileChange" class="hidden" />

                    <!-- Menu Dropdown -->
                    <transition
                      enter-active-class="transition ease-out duration-100"
                      enter-from-class="transform opacity-0 scale-95"
                      enter-to-class="transform opacity-100 scale-100"
                      leave-active-class="transition ease-in duration-75"
                      leave-from-class="transform opacity-100 scale-100"
                      leave-to-class="transform opacity-0 scale-95"
                    >
                        <div v-if="showPlusMenu" class="absolute bottom-full left-0 mb-2 z-50">
                            <div class="relative">
                                <div v-show="!(isMobileViewport && showImageParamPop)" class="w-52 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-gray-200 dark:border-gray-700 py-1.5 animate-fade-in-up">
                                    <!-- Data Portal -->
                                    <button
                                      v-if="filteredSystemCommands.some(c => c.id === DATASET_PORTAL_SYSTEM_COMMAND_ID)"
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="openDataPortalFromPlusMenu"
                                      class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                    >
                                        <ChartBarIcon class="h-5 w-5 shrink-0 text-blue-500" aria-hidden="true" />
                                        <span class="font-medium text-left">打开数据门户</span>
                                    </button>

                                    <!-- Knowledge Base -->
                                    <button
                                      v-if="filteredSystemCommands.some(c => c.id === 'sys_knowledge_portal')"
                                      :disabled="isKnowledgePortalDisabled"
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="isKnowledgePortalDisabled ? null : (showPlusMenu = false, showSkillCascade = false, showExpertCascade = false, emit('select-knowledge-base'));"
                                      class="w-full flex items-center justify-between px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                                    >
                                        <div class="flex items-center space-x-3">
                                            <BookOpenIcon class="h-5 w-5 shrink-0 text-emerald-500" aria-hidden="true" />
                                            <span class="font-medium text-left">打开知识库中心</span>
                                        </div>
                                    </button>

                                    <!-- Browse Workspace -->
                                    <button
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="showPlusMenu = false; showSkillCascade = false; showMcpCascade = false; showExpertCascade = false; emit('select-local-fs');"
                                      class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                    >
                                        <ComputerDesktopIcon class="h-5 w-5 shrink-0 text-slate-500" aria-hidden="true" />
                                        <span class="font-medium text-left">浏览工作空间</span>
                                    </button>

                                    <!-- Upload File -->
                                    <button
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="triggerFileInput"
                                      class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                    >
                                        <FolderIcon class="h-5 w-5 shrink-0 text-amber-500" aria-hidden="true" />
                                        <span class="font-medium text-left">上传本地文件</span>
                                    </button>

                                    <!-- Memory Records (moved up) -->
                                    <button
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="showPlusMenu = false; showSkillCascade = false; showMcpCascade = false; showExpertCascade = false; emit('select-memory');"
                                      class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                    >
                                        <CpuChipIcon class="h-5 w-5 shrink-0 text-violet-500" aria-hidden="true" />
                                        <span class="font-medium text-left">选择记忆记录</span>
                                    </button>

                                    <!-- 分隔线：上面是「打开页面 / 上传 / 选记忆」这类动作，
                                         下面全是展开二级面板的项（都带 ›），分组后一眼能分清。 -->
                                    <div
                                        class="mx-3 my-1 h-px bg-gray-100 dark:bg-gray-700/60"
                                        role="separator"
                                        aria-hidden="true"
                                    ></div>

                                    <!-- 数据 & 知识库：两个 tab 直接挂载，省去打开两个面板 -->
                                    <button
                                      v-if="showDataKnowledgeEntry"
                                      type="button"
                                      data-testid="plus-menu-data-knowledge"
                                      class="w-full flex items-center justify-between px-3 py-2 text-sm transition-all duration-150"
                                      :class="showDataKnowledgeCascade
                                        ? 'bg-gray-100 dark:bg-gray-700/80 text-gray-900 dark:text-gray-100'
                                        : 'text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary'"
                                      @mouseenter="!isMobileViewport && openDataKnowledgeCascade()"
                                      @click.stop="openDataKnowledgeCascade"
                                    >
                                        <div class="flex items-center space-x-3">
                                            <CircleStackIcon class="h-5 w-5 shrink-0 text-cyan-600" aria-hidden="true" />
                                            <span class="font-medium text-left">数据&amp;知识库</span>
                                        </div>
                                        <svg class="w-3.5 h-3.5 flex-shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                        </svg>
                                    </button>

                                    <!-- Skills cascade -->
                                    <button
                                      type="button"
                                      class="w-full flex items-center justify-between px-3 py-2 text-sm transition-all duration-150"
                                      :class="showSkillCascade
                                        ? 'bg-gray-100 dark:bg-gray-700/80 text-gray-900 dark:text-gray-100'
                                        : 'text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary'"
                                      @mouseenter="!isMobileViewport && openSkillCascade()"
                                      @click.stop="openSkillCascade"
                                    >
                                        <div class="flex items-center space-x-3">
                                            <Cog6ToothIcon class="h-5 w-5 shrink-0 text-slate-500" aria-hidden="true" />
                                            <span class="font-medium text-left">技能中心</span>
                                        </div>
                                        <svg class="w-3.5 h-3.5 flex-shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                        </svg>
                                    </button>

                                    <!-- MCP cascade -->
                                    <button
                                      type="button"
                                      class="w-full flex items-center justify-between px-3 py-2 text-sm transition-all duration-150"
                                      :class="showMcpCascade
                                        ? 'bg-gray-100 dark:bg-gray-700/80 text-gray-900 dark:text-gray-100'
                                        : 'text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary'"
                                      @mouseenter="!isMobileViewport && openMcpCascade()"
                                      @click.stop="openMcpCascade"
                                    >
                                        <div class="flex items-center space-x-3">
                                            <PuzzlePieceIcon class="h-5 w-5 shrink-0 text-slate-500" aria-hidden="true" />
                                            <span class="font-medium text-left">MCP 工具</span>
                                        </div>
                                        <svg class="w-3.5 h-3.5 flex-shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                        </svg>
                                    </button>

                                    <!-- Expert cascade -->
                                    <button
                                      v-if="!lockExpertAgent"
                                      type="button"
                                      class="w-full flex items-center justify-between px-3 py-2 text-sm transition-all duration-150"
                                      :class="showExpertCascade
                                        ? 'bg-gray-100 dark:bg-gray-700/80 text-gray-900 dark:text-gray-100'
                                        : 'text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary'"
                                      @mouseenter="!isMobileViewport && openExpertCascade()"
                                      @click.stop="openExpertCascade"
                                    >
                                        <div class="flex items-center space-x-3">
                                            <ChatBubbleLeftRightIcon class="h-5 w-5 shrink-0 text-indigo-500" aria-hidden="true" />
                                            <span class="font-medium text-left">专家中心</span>
                                        </div>
                                        <svg class="w-3.5 h-3.5 flex-shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                        </svg>
                                    </button>

                                    <!-- 图像参数：直接排在「专家中心」下面，与它同一组交互 -->
                                    <button
                                      type="button"
                                      class="w-full flex items-center justify-between px-3 py-2 text-sm transition-all duration-150"
                                      :class="showImageParamPop && !isMobileViewport
                                        ? 'bg-gray-100 dark:bg-gray-700/80 text-gray-900 dark:text-gray-100'
                                        : (imageFeatureAvailable
                                          ? 'text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary'
                                          : 'text-gray-400 dark:text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-700/40')"
                                      :title="imageFeatureBlockReason
                                        ? '平台未开启图像生成：请联系管理员在「系统设置 → 图像生成」打开开关'
                                        : '选择比例、风格与张数，自动写成一段提示词参数'"
                                      @mouseenter="!isMobileViewport && hoverOpenImageParamPop()"
                                      @mouseleave="cancelHoverOpen"
                                      @click.stop="openImageParamPop"
                                    >
                                        <div class="flex min-w-0 items-center space-x-3">
                                            <PhotoIcon
                                              class="h-5 w-5 shrink-0"
                                              :class="imageFeatureAvailable ? 'text-rose-500' : 'text-gray-300 dark:text-gray-600'"
                                              aria-hidden="true"
                                            />
                                            <span class="truncate font-medium text-left">图像参数</span>
                                        </div>
                                        <!-- 状态区定宽右对齐：加载圈 / 徽章 / 箭头三种状态宽度不同，
                                             不定宽的话每次切换都会把左边标题推一下，看起来像页面在抖。 -->
                                        <span class="flex w-[62px] shrink-0 items-center justify-end">
                                            <span
                                              v-if="imageStatusLoading && !imageStatusKnown"
                                              class="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-transparent"
                                            ></span>
                                            <span
                                              v-else-if="imageFeatureBlockReason"
                                              class="w-full rounded bg-gray-100 px-1.5 py-0.5 text-center text-[10px] font-medium leading-tight text-gray-500 dark:bg-gray-700 dark:text-gray-400"
                                            >{{ imageFeatureBlockReason }}</span>
                                            <svg v-else class="w-3.5 h-3.5 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                            </svg>
                                        </span>
                                    </button>

                                    <!-- 新会话 / 历史：两端都放「新会话」（会清空当前上下文，属于需要找得到
                                         又别误触的操作，所以排在菜单最末尾）。「历史」桌面端顶栏已有入口，
                                         这里只给移动端补一个，免得菜单在桌面上多一项重复的。 -->
                                    <div
                                        class="mx-3 my-1 h-px bg-gray-100 dark:bg-gray-700/60"
                                        role="separator"
                                        aria-hidden="true"
                                    ></div>
                                    <button
                                      type="button"
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="showPlusMenu = false; showSkillCascade = false; showMcpCascade = false; showExpertCascade = false; emit('system-command', '/new');"
                                      class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                    >
                                        <ChatBubbleLeftRightIcon class="h-5 w-5 shrink-0 text-slate-500" aria-hidden="true" />
                                        <span class="font-medium text-left">新会话</span>
                                    </button>
                                    <button
                                      v-if="isMobileViewport"
                                      type="button"
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="showPlusMenu = false; showSkillCascade = false; showMcpCascade = false; showExpertCascade = false; emit('system-command', '/history');"
                                      class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                    >
                                        <ClockIcon class="h-5 w-5 shrink-0 text-slate-500" aria-hidden="true" />
                                        <span class="font-medium text-left">历史</span>
                                    </button>
                                </div>

                                <!-- Desktop flyout: 与左侧加号菜单上下对齐，内部列表滚动 -->
                                <div
                                  v-if="showSkillCascade && !isMobileViewport"
                                  class="absolute z-[60] left-full top-0 bottom-0 ml-1.5 flex flex-col"
                                  @click.stop
                                >
                                  <SkillCascadeMenu
                                    ref="skillCascadeRef"
                                    fill-height
                                    :agent-id="agentId"
                                    :attached-skill-ids="attachedSkillIds"
                                    @select="mountSkillFromCascade"
                                  />
                                </div>

                                <div
                                  v-if="showMcpCascade && !isMobileViewport"
                                  class="absolute z-[60] left-full top-0 bottom-0 ml-1.5 flex flex-col"
                                  @click.stop
                                >
                                  <McpCascadeMenu
                                    ref="mcpCascadeRef"
                                    fill-height
                                    :attached-tool-names="attachedMcpToolNames"
                                    @select="mountMcpFromCascade"
                                  />
                                </div>

                                <div
                                  v-if="showExpertCascade && !isMobileViewport"
                                  class="absolute z-[60] left-full top-0 bottom-0 ml-1.5 flex flex-col"
                                  @click.stop
                                >
                                  <ExpertCascadeMenu
                                    fill-height
                                    :routing-mode="routingMode"
                                    :expert-agent-id="expertAgentId"
                                    :allowed-agents="allowedAgents"
                                    :is-loading-agents="isLoadingAgents"
                                    @select-auto="selectAutoRouting"
                                    @select-expert="selectExpertAgent"
                                    @refresh="emit('refresh-agents')"
                                    @close="closeExpertCascade"
                                  />
                                </div>

                                <!-- 图像参数面板：与其它 flyout 同一套定位（贴着加号菜单右边）
                                     结构固定为「标题 / tab / 滚动区 / 页脚」四段：只有中间那段滚，
                                     页脚里的「完成」永远贴在面板底部看得见。整块一起滚的话，
                                     改图页内容一多，按钮就滚到面板下沿之外，用户以为没有完成键。
                                     桌面端贴 `bottom-0`（而不是 top-0）向上长：加号菜单本身是往上弹的，
                                     面板再从菜单顶部往下铺 85vh，下沿就会伸到窗口外面去；高度上限
                                     再扣掉输入区（输入框最高 120px，加工具栏约 13rem），顶栏也留在窗口内。

                                     移动端：Teleport 到 body，与其它移动端抽屉（技能 / MCP / 专家）同一套做法。
                                     面板原先挂在加号菜单里，而 openImageParamPop 在移动端会先收起加号菜单——
                                     菜单一收，里面的面板跟着卸载，点「图像参数」就像没反应。现在移动端保持菜单挂载
                                     （只隐藏列表），面板脱离菜单渲染并自带遮罩，点遮罩或「取消 / 完成」才收起。
                                     桌面端 disabled，原地渲染，行为不变。 -->
                                <Teleport to="body" :disabled="!isMobileViewport">
                                <div
                                  v-if="showImageParamPop && isMobileViewport"
                                  class="fixed inset-0 z-[1190] bg-black/40"
                                  @click="closeImageParamPop"
                                ></div>
                                <div
                                  v-if="showImageParamPop"
                                  role="dialog"
                                  :aria-modal="isMobileViewport ? 'true' : undefined"
                                  aria-label="图像参数"
                                  class="flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800"
                                  :class="isMobileViewport
                                    ? 'fixed inset-x-3 top-1/2 z-[1200] max-h-[calc(var(--app-height,100dvh)-2rem)] -translate-y-1/2'
                                    : 'absolute bottom-0 left-full z-[60] ml-1.5 max-h-[min(85vh,calc(100vh-13rem))] w-[360px]'"
                                  @click.stop
                                >
                                  <div class="flex shrink-0 items-center gap-2 border-b border-gray-100 px-3.5 py-2.5 dark:border-gray-700">
                                    <PhotoIcon class="h-4 w-4 shrink-0 text-rose-500" />
                                    <span class="text-sm font-semibold text-gray-900 dark:text-gray-100">图像参数</span>
                                    <span class="ml-auto text-[10px] text-gray-400">只影响本次生成</span>
                                    <button
                                      type="button"
                                      class="-mr-1 flex h-5 w-5 items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
                                      title="关闭"
                                      @click="closeImageParamPop"
                                    >
                                      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" d="M6 6l12 12M18 6L6 18"/></svg>
                                    </button>
                                  </div>

                                  <!-- 还没查到状态时单独一支：既不能当成「未开启」，也不能先放出参数控件
                                       （万一其实没开，加载完会整块换掉）。等结果再决定显示哪一支。 -->
                                  <div v-if="!imageStatusKnown" class="flex items-center gap-2 px-3.5 py-3.5 text-[11px] text-gray-500">
                                    <span class="h-3 w-3 animate-spin rounded-full border-2 border-gray-300 border-t-transparent"></span>
                                    正在检查可用状态…
                                  </div>
                                  <div v-else-if="!imageFeatureAvailable" class="space-y-2 px-3.5 py-3.5">
                                    <!-- imageFeatureBlockReason 只会是「未开启」或空串：能力判定早已收敛成
                                         「只看系统总开关」，原来那个「当前智能体没勾 generate_image」的
                                         v-else 分支永远不会渲染，留着只会误导后面改代码的人。 -->
                                    <div class="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-800">
                                      平台还没开启图像生成。请联系管理员到<strong>「系统设置 → 图像生成」</strong>打开开关。
                                      开关打开后若这里仍不可用，再确认当前智能体的工具清单里有 <code class="font-mono">generate_image</code>。
                                    </div>
                                    <div class="flex justify-end">
                                      <button
                                        type="button"
                                        class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
                                        @click="closeImageParamPop"
                                      >知道了</button>
                                    </div>
                                  </div>

                                  <template v-else>
                                    <!-- 面板分两件事：生成（从零画）与改图（改已有的一张）。
                                         两者共用同一套入口与外观，只是改图页多一个传图区，
                                         并且提交时给正文加「沿用所附图片进行修改：」前缀。 -->
                                    <div class="flex shrink-0 gap-1 border-b border-gray-100 px-3 pt-2 dark:border-gray-700" role="tablist" aria-label="图像参数类型">
                                      <button
                                        type="button"
                                        role="tab"
                                        :aria-selected="imagePanelTab === 'gen'"
                                        class="rounded-t-lg px-3 py-1.5 text-[12px] font-medium transition-colors"
                                        :class="imagePanelTab === 'gen'
                                          ? 'border-b-2 border-primary text-primary'
                                          : 'text-gray-500 hover:text-gray-700 dark:text-gray-400'"
                                        @click="imagePanelTab = 'gen'"
                                      >生成</button>
                                      <button
                                        type="button"
                                        role="tab"
                                        :aria-selected="imagePanelTab === 'edit'"
                                        :aria-disabled="!editPanelEnabled"
                                        class="rounded-t-lg px-3 py-1.5 text-[12px] font-medium transition-colors"
                                        :class="imagePanelTab === 'edit'
                                          ? 'border-b-2 border-primary text-primary'
                                          : (editPanelEnabled
                                            ? 'text-gray-500 hover:text-gray-700 dark:text-gray-400'
                                            : 'cursor-not-allowed text-gray-300 dark:text-gray-600')"
                                        :disabled="!editPanelEnabled"
                                        @click="editPanelEnabled && (imagePanelTab = 'edit')"
                                      >改图</button>
                                    </div>
                                    <!-- 改图不可用时，原因必须**看得见**：写在 title 上等于没写——
                                         触屏没有 hover，而 disabled 的表单控件在多数浏览器里连
                                         mouse 事件都不派发，悬停提示根本不会出现。 -->
                                    <p
                                      v-if="!editPanelEnabled"
                                      role="status"
                                      class="shrink-0 border-b border-gray-100 bg-amber-50/70 px-3.5 py-1.5 text-[10.5px] leading-relaxed text-amber-800 dark:border-gray-700 dark:bg-amber-900/20 dark:text-amber-200"
                                    >{{ editPanelBlockReason }}</p>

                                    <!-- ───── 生成页 ───── -->
                                    <template v-if="imagePanelTab === 'gen'">
                                    <div class="min-h-0 flex-auto space-y-3 overflow-y-auto overscroll-contain px-3.5 py-3">
                                      <div>
                                        <div class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">风格</div>
                                        <div class="flex flex-wrap gap-1.5">
                                          <button
                                            v-for="style in IMAGE_STYLE_OPTIONS"
                                            :key="style"
                                            type="button"
                                            class="rounded-full border px-2 py-0.5 text-[11px] transition-colors"
                                            :class="(imageParamStyle || '不指定') === style
                                              ? 'border-primary bg-primary text-white'
                                              : 'border-gray-200 text-gray-600 hover:border-primary/40 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300'"
                                            @click="imageParamStyle = style === '不指定' ? '' : style"
                                          >{{ style }}</button>
                                        </div>
                                      </div>

                                      <div>
                                        <div class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">分辨率</div>
                                        <!-- 用卡片而不是胶囊：尺寸是「几何参数」，卡片画得出长宽形状。
                                             示意框按真实长宽比画，横版竖版不用读数字就能看出来；卡片上的
                                             比例小字（1:1 / 16:9）同时承担「这张图什么形状」的说明，
                                             所以面板里不再单独放一排画面比例——两套控件表达同一件事，
                                             选「1:1」又选「1664x928」时还会互相打架。 -->
                                        <div class="grid grid-cols-3 gap-1.5">
                                          <!-- 第一格直接显示后台配的默认尺寸并默认选中：写「自动」的话，
                                               用户得点开面板挨个比才知道这次到底出多大。 -->
                                          <button
                                            type="button"
                                            class="flex flex-col items-center gap-1 rounded-lg border px-1 py-1.5 transition-colors"
                                            :class="imageDefaultSize && imageParamSize === imageDefaultSize
                                              ? 'border-primary bg-primary/5 text-primary'
                                              : 'border-gray-200 text-gray-600 hover:border-primary/40 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300'"
                                            :title="imageDefaultSize ? `后台配置的默认尺寸：${imageDefaultSize}` : '后台还没配置默认尺寸'"
                                            @click="imageParamSize = imageDefaultSize"
                                          >
                                            <span
                                              class="block rounded-[3px] border"
                                              :class="imageDefaultSize && imageParamSize === imageDefaultSize
                                                ? 'border-primary bg-primary/15'
                                                : 'border-gray-400 dark:border-gray-500'"
                                              :style="imageDefaultSize
                                                ? imageSizeBoxStyle(imageDefaultSize)
                                                : { width: '14px', height: '14px', borderStyle: 'dashed' }"
                                            ></span>
                                            <span class="font-mono text-[10px] font-medium leading-none tracking-tight">{{ imageDefaultSize || '默认' }}</span>
                                            <span class="text-[9px] leading-none opacity-70">默认</span>
                                          </button>
                                          <button
                                            v-for="opt in visibleImageSizeChoices"
                                            :key="opt.size"
                                            type="button"
                                            class="flex flex-col items-center gap-1 rounded-lg border px-1 py-1.5 transition-colors"
                                            :class="imageParamSize === opt.size
                                              ? 'border-primary bg-primary/5 text-primary'
                                              : 'border-gray-200 text-gray-600 hover:border-primary/40 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300'"
                                            :title="opt.hint || `${opt.size}（${opt.ratio}）`"
                                            @click="imageParamSize = opt.size"
                                          >
                                            <span
                                              class="block rounded-[3px] border"
                                              :class="imageParamSize === opt.size ? 'border-primary bg-primary/15' : 'border-gray-400 dark:border-gray-500'"
                                              :style="imageSizeBoxStyle(opt.size)"
                                            ></span>
                                            <span class="font-mono text-[10px] font-medium leading-none tracking-tight">{{ opt.size }}</span>
                                            <span class="text-[9px] leading-none opacity-70">{{ opt.ratio }}</span>
                                          </button>
                                          <!-- 折叠格用虚线框 + chevron，和尺寸卡片同一套骨架（14px 图标位 + 两行小字），
                                               高度才对得齐下面那行；纯文字按钮会比旁边矮一截。 -->
                                          <button
                                            v-if="imageSizeNeedsCollapse"
                                            type="button"
                                            class="flex flex-col items-center gap-1 rounded-lg border border-dashed px-1 py-1.5 transition-colors"
                                            :class="imageSizeCollapsed
                                              ? 'border-gray-300 text-gray-500 hover:border-primary/40 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-400'
                                              : 'border-gray-200 text-gray-500 hover:border-primary/40 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-400'"
                                            :title="imageSizeCollapsed ? `还有 ${hiddenImageSizeCount} 个尺寸` : '收起多余尺寸'"
                                            @click="imageSizeCollapsed = !imageSizeCollapsed"
                                          >
                                            <span class="flex h-[14px] w-[14px] items-center justify-center">
                                              <svg
                                                class="h-3 w-3 transition-transform"
                                                :class="imageSizeCollapsed ? '' : 'rotate-180'"
                                                viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
                                              ><path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6"/></svg>
                                            </span>
                                            <span class="whitespace-nowrap text-[10px] font-medium leading-none">{{ imageSizeCollapsed ? '更多' : '收起' }}</span>
                                            <span class="text-[9px] leading-none opacity-70">
                                              {{ imageSizeCollapsed ? `+${hiddenImageSizeCount}` : `共 ${imageSizeChoices.length}` }}
                                            </span>
                                          </button>
                                        </div>
                                        <p class="mt-1 text-[10px] leading-relaxed text-gray-400">
                                          <template v-if="imageSizeChoices.length">
                                            来自「系统设置 → 图像生成」的白名单；第一格是后台配置的默认尺寸。
                                          </template>
                                          <template v-else>
                                            后台还没配置可选分辨率，只能用默认尺寸。
                                          </template>
                                          <span v-if="imageSizeChoices.some(o => !!o.hint)" class="block">
                                            边长小于 1024 的尺寸并非所有上游模型都支持，报错时换大一点的。
                                          </span>
                                        </p>
                                      </div>

                                      <div v-if="(imageFeature?.max_images || 1) > 1">
                                        <div class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">张数</div>
                                        <div class="flex gap-1.5">
                                          <button
                                            v-for="count in (imageFeature?.max_images || 1)"
                                            :key="count"
                                            type="button"
                                            class="h-7 w-9 rounded-lg border text-[11px] font-medium transition-colors"
                                            :class="imageParamCount === count
                                              ? 'border-primary bg-primary/5 text-primary'
                                              : 'border-gray-200 text-gray-600 hover:border-primary/40 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300'"
                                            @click="imageParamCount = count"
                                          >{{ count }} 张</button>
                                        </div>
                                      </div>
                                    </div>

                                    <div class="shrink-0 border-t border-gray-100 bg-gray-50/60 px-3.5 py-2.5 dark:border-gray-700 dark:bg-gray-800/60">
                                      <div class="flex items-center gap-2">
                                        <button
                                          v-if="imageParamChips.length"
                                          type="button"
                                          class="text-[11px] text-gray-500 hover:text-gray-700"
                                          @click="resetImageParams"
                                        >清空</button>
                                        <button
                                          type="button"
                                          class="ml-auto rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-medium text-gray-600 hover:bg-gray-50"
                                          @click="cancelGenParams"
                                        >取消</button>
                                        <button
                                          type="button"
                                          class="rounded-lg bg-primary px-2.5 py-1 text-[11px] font-bold text-white hover:opacity-90"
                                          @click="applyImageParams"
                                        >完成</button>
                                      </div>
                                    </div>
                                    </template>

                                    <!-- ───── 改图页 ───── -->
                                    <template v-else>
                                      <!-- 改图不可用的原因已经在 tab 下方常驻显示。原先这里是
                                           `v-if="!editPanelEnabled"` 的一整块说明，但它是**死代码**：
                                           imagePanelTab 只有在 editPanelEnabled 为真时才能变成 'edit'，
                                           能力丢失时 watch 还会把它拉回生成页，所以永远渲染不到。 -->
                                        <div class="min-h-0 flex-auto space-y-3 overflow-y-auto overscroll-contain px-3.5 py-3">
                                          <!-- 失败原因就地留下：toast 几秒就没了，而用户往往
                                               要盯着面板想「刚才到底发生了什么」。 -->
                                          <div
                                            v-if="imageEditError"
                                            role="status"
                                            aria-live="polite"
                                            class="flex items-start gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-2 py-1.5 text-[11px] leading-relaxed text-rose-700"
                                          >
                                            <span class="flex-1">{{ imageEditError }}</span>
                                            <button
                                              type="button"
                                              class="-mr-0.5 shrink-0 rounded px-0.5 text-[11px] text-rose-400 hover:bg-rose-100 hover:text-rose-600"
                                              title="关闭提示"
                                              @click="imageEditError = ''"
                                            >×</button>
                                          </div>
                                          <div>
                                            <div class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">图片</div>
                                            <input
                                              ref="imageEditFileInput"
                                              type="file"
                                              accept="image/png,image/jpeg,image/jpg,image/webp"
                                              class="hidden"
                                              @change="onEditImageChange"
                                            />
                                            <input
                                              ref="imageEditRefFileInput"
                                              type="file"
                                              accept="image/png,image/jpeg,image/jpg,image/webp"
                                              class="hidden"
                                              @change="onEditRefImageChange"
                                            />

                                            <!-- 两个槽位并排：左主图（必选）、右参考图（可选）。
                                                 并排而不是纵向列表，是为了让「只能放两张」这件事不用读字就看得出来。 -->
                                            <div class="flex items-stretch gap-2">
                                              <!-- 槽位用 <button> 而不是 <div @click>：div 不可聚焦、Tab 进不来、
                                                   Enter/Space 也触发不了，键盘用户根本选不上图，「完成」因此永远禁用。
                                                   顺带支持把图直接拖到虚线上（拖到输入框会变成普通附件）。 -->
                                              <button
                                                type="button"
                                                v-if="!imageEditFile"
                                                class="flex h-[74px] flex-1 cursor-pointer flex-col items-center justify-center gap-0.5 rounded-lg border border-dashed border-gray-300 px-1 text-center transition-colors hover:border-primary/50 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 dark:border-gray-600 dark:hover:bg-gray-700/40"
                                                @click="pickEditImage"
                                                @dragover.prevent
                                                @drop.prevent="onEditImageDrop"
                                              >
                                                <svg class="h-4 w-4 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" d="M12 5v14M5 12h14"/></svg>
                                                <span class="text-[11px] font-medium text-gray-500">主图</span>
                                                <span class="text-[10px] text-gray-500 dark:text-gray-400">必选</span>
                                              </button>
                                              <div
                                                v-else
                                                class="group relative h-[74px] flex-1 overflow-hidden rounded-lg border border-primary/40 bg-primary/5"
                                              >
                                                <img :src="imageEditPreviewUrl" :title="imageEditFileName" class="h-full w-full object-cover" alt="" />
                                                <span class="absolute left-1 top-1 rounded bg-primary px-1 py-0.5 text-[9px] font-bold leading-tight text-white">主图</span>
                                                <div class="absolute right-1 top-1 flex gap-0.5">
                                                  <button type="button" class="inline-flex min-h-[24px] min-w-[24px] items-center justify-center rounded bg-white/90 px-1.5 text-[10px] text-gray-600 shadow-sm hover:bg-white" title="换一张" @click="pickEditImage">换</button>
                                                  <button type="button" class="inline-flex min-h-[24px] min-w-[24px] items-center justify-center rounded bg-white/90 px-1.5 text-[11px] leading-tight text-gray-500 shadow-sm hover:bg-white" title="移除" @click="clearEditImage">×</button>
                                                </div>
                                              </div>

                                              <button
                                                type="button"
                                                v-if="!imageEditRefFile"
                                                class="flex h-[74px] flex-1 cursor-pointer flex-col items-center justify-center gap-0.5 rounded-lg border border-dashed border-gray-300 px-1 text-center transition-colors hover:border-primary/50 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 dark:border-gray-600 dark:hover:bg-gray-700/40"
                                                @click="pickEditRefImage"
                                                @dragover.prevent
                                                @drop.prevent="onEditRefImageDrop"
                                              >
                                                <svg class="h-4 w-4 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" d="M12 5v14M5 12h14"/></svg>
                                                <span class="text-[11px] font-medium text-gray-500">参考图</span>
                                                <span class="text-[10px] text-gray-500 dark:text-gray-400">可选</span>
                                              </button>
                                              <div
                                                v-else
                                                class="relative h-[74px] flex-1 overflow-hidden rounded-lg border border-gray-400/60 bg-gray-50 dark:bg-gray-700/40"
                                              >
                                                <img :src="imageEditRefPreviewUrl" :title="imageEditRefName" class="h-full w-full object-cover" alt="" />
                                                <span class="absolute left-1 top-1 rounded bg-gray-600 px-1 py-0.5 text-[9px] font-bold leading-tight text-white">参考图</span>
                                                <div class="absolute right-1 top-1 flex gap-0.5">
                                                  <button type="button" class="inline-flex min-h-[24px] min-w-[24px] items-center justify-center rounded bg-white/90 px-1.5 text-[10px] text-gray-600 shadow-sm hover:bg-white" title="换一张" @click="pickEditRefImage">换</button>
                                                  <button type="button" class="inline-flex min-h-[24px] min-w-[24px] items-center justify-center rounded bg-white/90 px-1.5 text-[11px] leading-tight text-gray-500 shadow-sm hover:bg-white" title="移除" @click="clearEditRefImage">×</button>
                                                </div>
                                              </div>
                                            </div>

                                            <p class="mt-1.5 text-[10px] leading-relaxed text-gray-400 dark:text-gray-500">
                                              主图是要改的那张；参考图只提供风格、配色或构图，不会被直接改。
                                            </p>
                                          </div>

                                          <div>
                                            <div class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">要改哪里</div>
                                            <textarea
                                              v-model="imageEditInstruction"
                                              rows="2"
                                              placeholder="比如：把背景换成浅蓝色，杯子别动"
                                              class="w-full resize-none rounded-lg border border-gray-200 px-2.5 py-1.5 text-[12px] leading-relaxed text-gray-800 outline-none transition-colors focus:border-primary dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
                                              @paste="onEditInstructionPaste"
                                            ></textarea>
                                          </div>

                                          <div>
                                            <div class="mb-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">风格</div>
                                            <div class="flex flex-wrap gap-1.5">
                                              <button
                                                v-for="style in IMAGE_STYLE_OPTIONS"
                                                :key="'edit-' + style"
                                                type="button"
                                                class="rounded-full border px-2 py-0.5 text-[11px] transition-colors"
                                                :class="(imageParamStyle || '不指定') === style
                                                  ? 'border-primary bg-primary text-white'
                                                  : 'border-gray-200 text-gray-600 hover:border-primary/40 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300'"
                                                @click="imageParamStyle = style === '不指定' ? '' : style"
                                              >{{ style }}</button>
                                            </div>
                                          </div>
                                        </div>

                                        <div class="shrink-0 border-t border-gray-100 bg-gray-50/60 px-3.5 py-2.5 dark:border-gray-700 dark:bg-gray-800/60">
                                          <div class="flex items-center gap-2">
                                            <button
                                              type="button"
                                              class="ml-auto rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-medium text-gray-600 hover:bg-gray-50"
                                              @click="closeImageParamPopSafely"
                                            >取消</button>
                                            <button
                                              type="button"
                                              class="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-[11px] font-bold text-white hover:opacity-90 disabled:cursor-not-allowed"
                                              :class="!imageEditFile ? 'opacity-40' : (editSubmitting ? 'opacity-80' : '')"
                                              :disabled="!imageEditFile || editSubmitting"
                                              @click="applyEditParams"
                                            >
                                              <span
                                                v-if="editSubmitting"
                                                class="h-2.5 w-2.5 animate-spin rounded-full border-2 border-white/40 border-t-white"
                                              ></span>
                                              {{ editSubmitting ? '处理中…' : '完成' }}
                                            </button>
                                          </div>
                                        </div>
                                    </template>
                                  </template>
                                </div>
                                </Teleport>

                                <!-- Desktop flyout: 数据 & 知识库（两个 tab 直接挂载） -->
                                <div
                                  v-if="showDataKnowledgeCascade && !isMobileViewport"
                                  class="absolute z-[60] left-full top-0 bottom-0 ml-1.5 flex flex-col"
                                  @click.stop
                                >
                                  <DataKnowledgeCascadeMenu
                                    ref="dataKnowledgeCascadeRef"
                                    fill-height
                                    :active-dataset-ids="attachedMetadataDatasetIds"
                                    :active-knowledge-base-ids="attachedKnowledgeBaseIds"
                                    :allowed-dataset-ids="allowedDatasetIds"
                                    :allowed-knowledge-base-ids="allowedKnowledgeBaseIds"
                                    :dataset-disabled="!canMountDatasets"
                                    :dataset-locked="boundDatasetCount > 0"
                                    :knowledge-disabled="!canMountKnowledgeBases"
                                    @toggle-dataset="toggleDatasetMountFromCascade"
                                    @toggle-knowledge-base="toggleKnowledgeBaseMountFromCascade"
                                  />
                                </div>
                            </div>
                        </div>
                    </transition>

                    <!-- Mobile drawer: Skills -->
                    <Teleport to="body">
                      <transition
                        enter-active-class="transition ease-out duration-200"
                        enter-from-class="opacity-0"
                        enter-to-class="opacity-100"
                        leave-active-class="transition ease-in duration-150"
                        leave-from-class="opacity-100"
                        leave-to-class="opacity-0"
                      >
                        <div
                          v-if="showSkillCascade && isMobileViewport"
                          class="fixed inset-0 z-[1200]"
                          @click="closeSkillCascade"
                        >
                          <div class="absolute inset-0 bg-black/40" />
                          <div
                            class="absolute inset-x-0 bottom-0 flex flex-col max-h-[min(80vh,36rem)] overflow-hidden rounded-t-2xl border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-2xl pb-[max(0.5rem,env(safe-area-inset-bottom))]"
                            @click.stop
                          >
                            <div class="shrink-0 flex justify-center pt-2 pb-1" aria-hidden="true">
                              <div class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600" />
                            </div>
                            <div class="flex items-center justify-between px-3 pb-1 shrink-0">
                              <span class="text-sm font-semibold text-gray-900 dark:text-gray-100">技能中心</span>
                              <button
                                type="button"
                                class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 p-1"
                                @click="closeSkillCascade"
                              >
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                              </button>
                            </div>
                            <div class="min-h-0 flex-1 overflow-hidden">
                              <SkillCascadeMenu
                                ref="skillCascadeRef"
                                full-width
                                :agent-id="agentId"
                                :attached-skill-ids="attachedSkillIds"
                                @select="mountSkillFromCascade"
                              />
                            </div>
                          </div>
                        </div>
                      </transition>
                    </Teleport>

                    <!-- Mobile drawer: MCP -->
                    <Teleport to="body">
                      <transition
                        enter-active-class="transition ease-out duration-200"
                        enter-from-class="opacity-0"
                        enter-to-class="opacity-100"
                        leave-active-class="transition ease-in duration-150"
                        leave-from-class="opacity-100"
                        leave-to-class="opacity-0"
                      >
                        <div
                          v-if="showMcpCascade && isMobileViewport"
                          class="fixed inset-0 z-[1200]"
                          @click="closeMcpCascade"
                        >
                          <div class="absolute inset-0 bg-black/40" />
                          <div
                            class="absolute inset-x-0 bottom-0 flex flex-col max-h-[min(80vh,36rem)] overflow-hidden rounded-t-2xl border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-2xl pb-[max(0.5rem,env(safe-area-inset-bottom))]"
                            @click.stop
                          >
                            <div class="shrink-0 flex justify-center pt-2 pb-1" aria-hidden="true">
                              <div class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600" />
                            </div>
                            <div class="flex items-center justify-between px-3 pb-1 shrink-0">
                              <span class="text-sm font-semibold text-gray-900 dark:text-gray-100">MCP 工具</span>
                              <button
                                type="button"
                                class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 p-1"
                                @click="closeMcpCascade"
                              >
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                              </button>
                            </div>
                            <div class="min-h-0 flex-1 overflow-hidden">
                              <McpCascadeMenu
                                ref="mcpCascadeRef"
                                full-width
                                :attached-tool-names="attachedMcpToolNames"
                                @select="mountMcpFromCascade"
                              />
                            </div>
                          </div>
                        </div>
                      </transition>
                    </Teleport>

                    <!-- Mobile drawer: Experts -->
                    <Teleport to="body">
                      <transition
                        enter-active-class="transition ease-out duration-200"
                        enter-from-class="opacity-0"
                        enter-to-class="opacity-100"
                        leave-active-class="transition ease-in duration-150"
                        leave-from-class="opacity-100"
                        leave-to-class="opacity-0"
                      >
                        <div
                          v-if="showExpertCascade && isMobileViewport"
                          class="fixed inset-0 z-[1200]"
                          @click="closeExpertCascade"
                        >
                          <div class="absolute inset-0 bg-black/40" />
                          <div
                            class="absolute inset-x-0 bottom-0 overflow-hidden rounded-t-2xl border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-2xl pb-[max(0.5rem,env(safe-area-inset-bottom))]"
                            @click.stop
                          >
                            <div class="shrink-0 flex justify-center pt-2 pb-1" aria-hidden="true">
                              <div class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600" />
                            </div>
                            <div class="flex items-center justify-end px-3 pb-1">
                              <button
                                type="button"
                                class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 p-1"
                                @click="closeExpertCascade"
                              >
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                              </button>
                            </div>
                            <ExpertCascadeMenu
                              full-width
                              :routing-mode="routingMode"
                              :expert-agent-id="expertAgentId"
                              :allowed-agents="allowedAgents"
                              :is-loading-agents="isLoadingAgents"
                              @select-auto="selectAutoRouting"
                              @select-expert="selectExpertAgent"
                              @refresh="emit('refresh-agents')"
                              @close="closeExpertCascade"
                            />
                          </div>
                        </div>
                      </transition>
                    </Teleport>

                    <!-- Mobile drawer: 数据 & 知识库 -->
                    <Teleport to="body">
                      <transition
                        enter-active-class="transition ease-out duration-200"
                        enter-from-class="opacity-0"
                        enter-to-class="opacity-100"
                        leave-active-class="transition ease-in duration-150"
                        leave-from-class="opacity-100"
                        leave-to-class="opacity-0"
                      >
                        <div
                          v-if="showDataKnowledgeCascade && isMobileViewport"
                          class="fixed inset-0 z-[1200]"
                          @click="closeDataKnowledgeCascade"
                        >
                          <div class="absolute inset-0 bg-black/40" />
                          <div
                            class="absolute inset-x-0 bottom-0 flex flex-col max-h-[min(80vh,36rem)] overflow-hidden rounded-t-2xl border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-2xl pb-[max(0.5rem,env(safe-area-inset-bottom))]"
                            @click.stop
                          >
                            <div class="shrink-0 flex justify-center pt-2 pb-1" aria-hidden="true">
                              <div class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600" />
                            </div>
                            <div class="flex items-center justify-between px-3 pb-1 shrink-0">
                              <span class="text-sm font-semibold text-gray-900 dark:text-gray-100">数据&amp;知识库</span>
                              <button
                                type="button"
                                class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 p-1"
                                @click="closeDataKnowledgeCascade"
                              >
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                              </button>
                            </div>
                            <div class="min-h-0 flex-1 overflow-hidden">
                              <DataKnowledgeCascadeMenu
                                ref="dataKnowledgeCascadeRef"
                                full-width
                                :active-dataset-ids="attachedMetadataDatasetIds"
                                :active-knowledge-base-ids="attachedKnowledgeBaseIds"
                                :allowed-dataset-ids="allowedDatasetIds"
                                :allowed-knowledge-base-ids="allowedKnowledgeBaseIds"
                                :dataset-disabled="!canMountDatasets"
                                :dataset-locked="boundDatasetCount > 0"
                                :knowledge-disabled="!canMountKnowledgeBases"
                                @toggle-dataset="toggleDatasetMountFromCascade"
                                @toggle-knowledge-base="toggleKnowledgeBaseMountFromCascade"
                              />
                            </div>
                          </div>
                        </div>
                      </transition>
                    </Teleport>
                </div>

                <!-- 智能委派胶囊 (桌面端；移动端走加号「专家中心」；URL 锁定时隐藏) -->
                <div v-if="!isMobileViewport && !lockExpertAgent" ref="expertSelectorRef" data-tour="composer-routing" class="relative flex-shrink-0 z-30">
                    <button
                      type="button"
                      :disabled="isInteractionLocked"
                      :title="isExpertMode ? `当前专家：${expertCapsuleLabel}` : '智能委派：由主助手处理或委派其他专家'"
                      class="flex h-8 sm:h-7 items-center gap-0.5 sm:gap-0.5 rounded-full px-1.5 sm:px-2 text-xs font-semibold leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-40 max-w-[5.25rem] sm:max-w-[11rem]"
                      :class="isExpertMode
                        ? 'bg-primary/10 text-primary hover:bg-primary/15 dark:bg-primary/20 dark:hover:bg-primary/25'
                        : 'text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700/70'"
                      :aria-expanded="showExpertSelector"
                      aria-haspopup="listbox"
                      @click.stop="toggleExpertSelector"
                    >
                        <!-- 团主专家用「团队」图标；普通专家用单人图标；智能委派仍是闪电 -->
                        <svg
                          v-if="currentExpertIsTeamLead"
                          class="h-3.5 w-3.5 flex-shrink-0 opacity-90"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          aria-hidden="true"
                        >
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                        </svg>
                        <svg
                          v-else-if="isExpertMode"
                          class="h-3.5 w-3.5 flex-shrink-0 opacity-90"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          aria-hidden="true"
                        >
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                        </svg>
                        <BoltIcon
                          v-else
                          class="h-3.5 w-3.5 flex-shrink-0 opacity-90"
                          aria-hidden="true"
                        />
                        <span class="truncate">{{ expertCapsuleLabel }}</span>
                        <svg
                          class="hidden sm:block h-3 w-3 flex-shrink-0 opacity-60 transition-transform"
                          :class="{ 'rotate-180': showExpertSelector }"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 9l6 6 6-6" />
                        </svg>
                    </button>

                    <Teleport to="body">
                      <transition
                        enter-active-class="transition ease-out duration-150"
                        enter-from-class="opacity-0"
                        enter-to-class="opacity-100"
                        leave-active-class="transition ease-in duration-100"
                        leave-from-class="opacity-100"
                        leave-to-class="opacity-0"
                      >
                        <div
                          v-if="showExpertSelector"
                          class="fixed inset-0 z-[1200]"
                          @click="showExpertSelector = false"
                        >
                          <div
                            class="absolute inset-0"
                            :class="isMobileViewport ? 'bg-black/40' : 'bg-transparent'"
                          />
                          <div
                            v-if="isMobileViewport"
                            class="absolute inset-x-0 bottom-0 overflow-hidden rounded-t-2xl border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-2xl pb-[max(0.5rem,env(safe-area-inset-bottom))]"
                            @click.stop
                          >
                            <div class="shrink-0 flex justify-center pt-2 pb-1" aria-hidden="true">
                              <div class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600" />
                            </div>
                            <div class="flex items-center justify-end px-3 pb-1">
                              <button
                                type="button"
                                class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 transition-colors p-1"
                                @click="showExpertSelector = false"
                              >
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                              </button>
                            </div>
                            <ExpertCascadeMenu
                              full-width
                              :routing-mode="routingMode"
                              :expert-agent-id="expertAgentId"
                              :allowed-agents="allowedAgents"
                              :is-loading-agents="isLoadingAgents"
                              @select-auto="selectAutoRouting"
                              @select-expert="selectExpertAgent"
                              @refresh="emit('refresh-agents')"
                              @close="showExpertSelector = false"
                            />
                          </div>
                          <div
                            v-else
                            class="absolute"
                            :style="{
                              bottom: `${expertMenuPosition.bottom}px`,
                              left: `${expertMenuPosition.left}px`,
                            }"
                            @click.stop
                          >
                            <ExpertCascadeMenu
                              :routing-mode="routingMode"
                              :expert-agent-id="expertAgentId"
                              :allowed-agents="allowedAgents"
                              :is-loading-agents="isLoadingAgents"
                              @select-auto="selectAutoRouting"
                              @select-expert="selectExpertAgent"
                              @refresh="emit('refresh-agents')"
                              @close="showExpertSelector = false"
                            />
                          </div>
                        </div>
                      </transition>
                    </Teleport>
                </div>

                <div ref="approvalTriggerWrapperRef" data-tour="composer-approval" class="relative flex-shrink-0">
                    <button
                      ref="approvalTriggerRef"
                      type="button"
                      :disabled="isInteractionLocked"
                      :title="`工具批准：${activeApprovalLabel}`"
                      class="flex h-7 max-w-[7.25rem] sm:max-w-none items-center gap-0.5 sm:gap-1 rounded-full px-1.5 sm:px-2.5 text-[11px] sm:text-xs font-semibold leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                      :class="[
                        approvalTriggerToneClass,
                        showApprovalMenu && !isInteractionLocked
                          ? (activeApprovalMode === 'ask'
                            ? 'bg-gray-100 dark:bg-gray-700/70'
                            : 'ring-1 ring-current/25 shadow-sm')
                          : '',
                      ]"
                      aria-haspopup="listbox"
                      :aria-expanded="showApprovalMenu"
                      @click.stop="toggleApprovalMenu"
                    >
                        <svg
                          v-if="activeApprovalMode === 'ask'"
                          class="h-3.5 w-3.5 flex-shrink-0 opacity-90"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          aria-hidden="true"
                        >
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
                        </svg>
                        <svg
                          v-else-if="activeApprovalMode === 'allow'"
                          class="h-3.5 w-3.5 flex-shrink-0 opacity-90"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          aria-hidden="true"
                        >
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016a11.959 11.959 0 0 0-4.5-1.253Z" />
                        </svg>
                        <svg
                          v-else
                          class="h-3.5 w-3.5 flex-shrink-0 opacity-90"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          aria-hidden="true"
                        >
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M18.364 18.364A9 9 0 0 0 5.636 5.636m12.728 12.728A9 9 0 0 1 5.636 5.636m12.728 12.728L5.636 5.636" />
                        </svg>
                        <span
                          class="truncate"
                          :class="activeApprovalMode === 'ask' ? 'text-gray-700 dark:text-gray-200' : ''"
                        >{{ approvalCapsuleLabel }}</span>
                        <svg class="hidden sm:block h-3.5 w-3.5 flex-shrink-0 opacity-70 transition-transform" :class="{ 'rotate-180': showApprovalMenu }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 9l6 6 6-6" />
                        </svg>
                    </button>

                    <Teleport to="body">
                        <transition
                          enter-active-class="transition ease-out duration-150"
                          enter-from-class="opacity-0 translate-y-1"
                          enter-to-class="opacity-100 translate-y-0"
                          leave-active-class="transition ease-in duration-100"
                          leave-from-class="opacity-100 translate-y-0"
                          leave-to-class="opacity-0 translate-y-1"
                        >
                            <div
                              v-if="showApprovalMenu"
                              class="fixed inset-0 z-[1200]"
                              @click="showApprovalMenu = false"
                            >
                                <div
                                  class="absolute inset-0"
                                  :class="isMobileViewport ? 'bg-black/50' : 'bg-black/15'"
                                />
                                <div
                                  ref="approvalMenuPanelRef"
                                  class="overflow-hidden shadow-2xl"
                                  :class="isMobileViewport
                                    ? 'absolute inset-x-0 bottom-0 rounded-t-2xl border-t border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800'
                                    : 'absolute rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800'"
                                  :style="isMobileViewport ? {} : {
                                    bottom: `${approvalMenuPosition.bottom}px`,
                                    left: `${approvalMenuPosition.left}px`,
                                    width: `${approvalMenuPosition.width}px`,
                                  }"
                                  role="listbox"
                                  aria-label="工具批准方式"
                                  @click.stop
                                >
                                    <div
                                      class="flex items-center justify-between border-b border-gray-100 dark:border-gray-700"
                                      :class="isMobileViewport ? 'px-4 py-3.5' : 'px-3 py-2.5'"
                                    >
                                        <p
                                          class="font-semibold text-gray-900 dark:text-gray-100"
                                          :class="isMobileViewport ? 'text-sm' : 'text-xs'"
                                        >应如何批准工具操作？</p>
                                        <button
                                          type="button"
                                          class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
                                          aria-label="关闭工具批准方式"
                                          title="关闭"
                                          @click.stop="showApprovalMenu = false"
                                        >
                                            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.25" d="M6 6l12 12M18 6L6 18" />
                                            </svg>
                                        </button>
                                    </div>
                                    <div
                                      class="overflow-y-auto py-1 custom-scrollbar"
                                      :class="isMobileViewport ? 'max-h-[min(70vh,420px)] pb-[max(0.75rem,env(safe-area-inset-bottom))]' : 'max-h-[min(50vh,280px)]'"
                                    >
                                        <button
                                          v-for="option in APPROVAL_MODE_OPTIONS"
                                          :key="option.value"
                                          type="button"
                                          role="option"
                                          :aria-selected="activeApprovalMode === option.value"
                                          class="flex w-full items-start gap-2.5 text-left transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/50"
                                          :class="[
                                            isMobileViewport ? 'px-4 py-3.5' : 'px-3 py-2.5',
                                            activeApprovalMode === option.value ? 'bg-primary/10 dark:bg-primary/20' : '',
                                          ]"
                                          @click="selectApprovalMode(option.value)"
                                        >
                                            <div class="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-300">
                                                <svg v-if="option.value === 'ask'" class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
                                                </svg>
                                                <svg v-else-if="option.value === 'allow'" class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016a11.959 11.959 0 0 0-4.5-1.253Z" />
                                                </svg>
                                                <svg v-else class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M18.364 18.364A9 9 0 0 0 5.636 5.636m12.728 12.728A9 9 0 0 1 5.636 5.636m12.728 12.728L5.636 5.636" />
                                                </svg>
                                            </div>
                                            <div class="min-w-0 flex-1">
                                                <div class="flex items-center justify-between gap-2">
                                                    <span
                                                      class="font-semibold text-gray-900 dark:text-gray-100"
                                                      :class="isMobileViewport ? 'text-base' : 'text-sm'"
                                                    >{{ option.label }}</span>
                                                    <svg
                                                      v-if="activeApprovalMode === option.value"
                                                      class="h-4 w-4 flex-shrink-0 text-primary"
                                                      fill="none"
                                                      stroke="currentColor"
                                                      viewBox="0 0 24 24"
                                                    >
                                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
                                                    </svg>
                                                </div>
                                                <p
                                                  class="mt-1 leading-snug text-gray-600 dark:text-gray-300"
                                                  :class="isMobileViewport ? 'text-xs' : 'text-[11px]'"
                                                >{{ option.description }}</p>
                                            </div>
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </transition>
                    </Teleport>
                </div>

                <div class="min-w-1 flex-1"></div>
                <!-- 候选为空时的兜底：在原切换器位置显示只读提示，避免"白名单生效"被误判成功能坏了 -->
                <span
                  v-if="!allowModelSwitch && modelSwitchHint"
                  class="flex-shrink min-w-0 truncate px-2 text-[11px] font-medium text-amber-600 dark:text-amber-400"
                  :title="modelSwitchHint"
                >{{ modelSwitchHint }}</span>
                <!-- Custom Model Dropdown Selector：版本级开关或候选并集不满足时整块隐藏 -->
                <div
                  v-if="allowModelSwitch"
                  ref="modelDropdownRef"
                  class="relative flex-shrink min-w-0"
                  :class="{ 'z-50': showModelDropdown }"
                >
                    <button
                      ref="modelDropdownTriggerRef"
                      :disabled="isInteractionLocked"
                      @click="toggleModelDropdown"
                      class="relative flex h-7 items-center gap-1 rounded-full px-2 sm:px-2.5 text-[11px] sm:text-xs font-semibold leading-none text-gray-600 transition-colors hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700/70 disabled:opacity-50 disabled:cursor-not-allowed select-none max-w-[min(48vw,13rem)] sm:max-w-[320px]"
                      :title="modelTriggerTooltip"
                    >
                        <span class="pointer-events-none truncate flex-1 min-w-0 text-left">{{ modelLabel }}</span>
                        <span v-if="thinkingSummaryLabel" class="pointer-events-none flex-shrink-0 rounded-full bg-primary/10 px-1 py-0.5 text-[8px] sm:px-1.5 sm:text-[9px] font-semibold text-primary dark:bg-primary/20 dark:text-primary-hover">{{ thinkingSummaryLabel }}</span>
                        <span
                          v-if="temperatureSummaryLabel"
                          class="pointer-events-none flex-shrink-0 rounded-full px-1 py-0.5 text-[8px] sm:px-1.5 sm:text-[9px] font-semibold transition-colors"
                          :class="effectiveTemperature > 1
                            ? 'bg-amber-50 text-amber-600 dark:bg-amber-950/60 dark:text-amber-300'
                            : 'bg-sky-50 text-sky-600 dark:bg-sky-950/60 dark:text-sky-300'"
                        >
                          {{ temperatureSummaryLabel }}
                        </span>
                        <svg class="pointer-events-none h-3 w-3 sm:h-3.5 sm:w-3.5 flex-shrink-0 text-gray-400 transform transition-transform duration-200" :class="{ 'rotate-180': showModelDropdown }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
                        </svg>
                    </button>

                    <!-- Custom Dropdown Menu (Pop up upwards) -->
                    <transition name="slide-up">
                        <div
                          v-show="showModelDropdown"
                          class="z-50 overflow-hidden bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl"
                          :class="isMobileViewport
                            ? 'fixed max-h-[min(70vh,420px)]'
                            : (showThinkingPanel && selectedModelConfig
                              ? 'absolute bottom-full mb-2 right-0 w-[min(580px,calc(100vw-24px))] max-h-[min(448px,calc(100vh-96px))] origin-bottom-right'
                              : 'absolute bottom-full mb-2 right-0 w-[min(330px,calc(100vw-24px))] max-h-[min(448px,calc(100vh-96px))] origin-bottom-right')"
                          :style="isMobileViewport ? {
                            bottom: `${modelDropdownPosition.bottom}px`,
                            left: `${modelDropdownPosition.left}px`,
                            width: `${modelDropdownPosition.width}px`,
                          } : undefined"
                        >
                            <div :class="isMobileViewport
                              ? 'flex max-h-[min(70vh,420px)] flex-col'
                              : 'flex h-full min-h-0 max-h-[min(448px,calc(100vh-96px))] flex-row'"
                            >
                              <!-- 一级：模型列表（移动端进入思考设置后隐藏） -->
                              <div
                                v-show="!isMobileViewport || !showThinkingPanel"
                                class="min-w-0 min-h-0 flex-1 flex flex-col"
                              >
                                <div v-if="showModelSearch" class="shrink-0 border-b border-gray-100 p-1.5 dark:border-gray-700">
                                  <input
                                    ref="modelSearchInputRef"
                                    v-model="modelSearchQuery"
                                    type="search"
                                    placeholder="搜索模型…"
                                    class="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 outline-none focus:border-primary/40 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200"
                                    @click.stop
                                    @keydown.down.stop.prevent="navigateModelList(1)"
                                    @keydown.up.stop.prevent="navigateModelList(-1)"
                                    @keydown.enter.stop.prevent="selectActiveModel"
                                    @keydown.esc.stop.prevent="closeModelMenu"
                                  />
                                  <div class="mt-1 flex items-center justify-between px-1 text-[10px] text-gray-400 dark:text-gray-500 select-none">
                                    <span>↑↓ 切换 · ↵ 选择</span>
                                    <span>Esc 关闭</span>
                                  </div>
                                </div>
                                <div ref="modelListScrollRef" class="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5 custom-scrollbar touch-pan-y space-y-1">
                                <button
                                  @click="resetModelSelection"
                                  @mouseenter="activeModelIndex = 0"
                                  type="button"
                                  class="w-full text-left px-2.5 py-2 rounded-lg text-xs transition-all flex items-center justify-between"
                                  data-model-index="0"
                                  :data-model-current="!selectedModel ? 'true' : undefined"
                                  :data-model-active="activeModelIndex === 0 ? 'true' : undefined"
                                  :class="[
                                    !selectedModel
                                      ? (activeModelIndex === 0
                                        ? 'bg-primary/15 ring-2 ring-primary/40 dark:bg-primary/25'
                                        : 'bg-primary/10 ring-1 ring-primary/20 dark:bg-primary/20')
                                      : (activeModelIndex === 0
                                        ? 'bg-gray-100 dark:bg-gray-800 ring-1 ring-gray-300 dark:ring-gray-600 text-gray-800 dark:text-gray-200'
                                        : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50')
                                  ]"
                                >
                                    <div class="min-w-0 flex-1 pr-2">
                                      <div class="flex items-center gap-1.5 min-w-0">
                                        <span class="truncate text-xs font-semibold" :class="!selectedModel ? 'text-primary' : 'text-gray-800 dark:text-gray-100'">使用默认模型</span>
                                        <span v-if="!selectedModel" class="text-primary text-[11px] font-bold shrink-0">✓</span>
                                      </div>
                                      <div class="mt-0.5 text-[10px] text-gray-400 dark:text-gray-500 truncate">
                                        按平台全局策略智能调度
                                      </div>
                                    </div>
                                    <div class="ml-1 flex flex-shrink-0 items-center gap-1">
                                      <span
                                        v-if="activeModelIndex === 0"
                                        class="text-[10px] text-primary font-mono px-1 py-0.5 rounded bg-primary/10 dark:bg-primary/20 font-bold shrink-0"
                                        title="按 Enter 选择"
                                      >↵</span>
                                    </div>
                                </button>

                                <button
                                  v-for="(model, index) in filteredAvailableModels"
                                  :key="model.id || model.model_id"
                                  type="button"
                                  class="w-full text-left px-2.5 py-2 rounded-lg text-xs transition-all flex items-center justify-between"
                                  :data-model-index="index + 1"
                                  :data-model-current="selectedModel === model.model_id ? 'true' : undefined"
                                  :data-model-active="activeModelIndex === index + 1 ? 'true' : undefined"
                                  :class="[
                                    selectedModel === model.model_id
                                      ? (activeModelIndex === index + 1
                                        ? 'bg-primary/15 ring-2 ring-primary/40 dark:bg-primary/25'
                                        : 'bg-primary/10 ring-1 ring-primary/20 dark:bg-primary/20')
                                      : (activeModelIndex === index + 1
                                        ? 'bg-gray-100 dark:bg-gray-800 ring-1 ring-gray-300 dark:ring-gray-600 text-gray-800 dark:text-gray-200'
                                        : 'hover:bg-gray-50 dark:hover:bg-gray-700/50')
                                  ]"
                                  @click="selectModel(model)"
                                  @mouseenter="activeModelIndex = index + 1"
                                >
                                    <div class="min-w-0 flex-1 pr-2">
                                      <div class="flex items-center gap-1.5 min-w-0">
                                        <span
                                          class="truncate text-xs font-semibold"
                                          :class="selectedModel === model.model_id ? 'text-primary' : 'text-gray-800 dark:text-gray-100'"
                                        >
                                          {{ model.name || model.model_id }}
                                        </span>
                                        <span v-if="selectedModel === model.model_id" class="text-primary text-[11px] font-bold shrink-0">✓</span>
                                      </div>
                                      <div class="mt-0.5 flex items-center gap-1.5 text-[10px] text-gray-400 dark:text-gray-500">
                                        <span v-if="model.type === 'multimodal'" class="inline-flex items-center gap-0.5 text-gray-400 dark:text-gray-500">
                                          <PhotoIcon class="h-3 w-3 shrink-0 text-gray-400 dark:text-gray-500" aria-hidden="true" />
                                          <span>多模态</span>
                                        </span>
                                        <span v-else class="text-gray-400 dark:text-gray-500">文本</span>

                                        <span v-if="model.thinking_enable" class="text-gray-300 dark:text-gray-600">·</span>
                                        <span v-if="model.thinking_enable" class="text-gray-400 dark:text-gray-500">深度思考</span>
                                      </div>
                                    </div>
                                    <div class="ml-1 flex flex-shrink-0 items-center gap-1">
                                      <span
                                        v-if="activeModelIndex === index + 1"
                                        class="text-[10px] text-primary font-mono px-1 py-0.5 rounded bg-primary/10 dark:bg-primary/20 font-bold shrink-0"
                                        title="按 Enter 选择"
                                      >↵</span>
                                      <button
                                        v-if="model.thinking_enable"
                                        type="button"
                                        class="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-medium transition-colors"
                                        :class="selectedModel === model.model_id
                                          ? 'bg-primary/15 text-primary hover:bg-primary/25 dark:bg-primary/25 dark:text-primary-hover'
                                          : 'bg-gray-100 text-gray-500 hover:bg-gray-200 dark:bg-gray-700/60 dark:text-gray-300 dark:hover:bg-gray-700'"
                                        :title="isMobileViewport ? '思考与参数设置' : '调整本次会话思考与温度'"
                                        @click="openThinkingSettings(model, $event)"
                                      >
                                        <span>{{ selectedModel === model.model_id ? (thinkingSummaryLabel || '思考') : '思考' }}</span>
                                        <svg class="h-3 w-3 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 5 7 7-7 7" /></svg>
                                      </button>
                                      <button
                                        v-else
                                        type="button"
                                        class="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-medium transition-colors"
                                        :class="selectedModel === model.model_id && !isFollowingDefaultTemperature
                                          ? 'bg-primary/15 text-primary hover:bg-primary/25 dark:bg-primary/25 dark:text-primary-hover'
                                          : 'bg-gray-100 text-gray-500 hover:bg-gray-200 dark:bg-gray-700/60 dark:text-gray-300 dark:hover:bg-gray-700'"
                                        :title="isMobileViewport ? '模型参数设置' : '调整模型参数与温度'"
                                        @click="openThinkingSettings(model, $event)"
                                      >
                                        <span>{{ selectedModel === model.model_id && !isFollowingDefaultTemperature ? `T:${effectiveTemperature.toFixed(1)}` : '参数' }}</span>
                                        <svg class="h-3 w-3 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 5 7 7-7 7" /></svg>
                                      </button>
                                    </div>
                                </button>
                                <div v-if="filteredAvailableModels.length === 0" class="px-2.5 py-3 text-center text-[10px] text-gray-400">
                                  无匹配模型
                                </div>
                                </div>
                              </div>

                              <!-- 二级（移动）/ 侧栏（桌面）：模型参数与思考设置 -->
                              <div
                                v-if="showThinkingPanel && selectedModelConfig"
                                class="w-full flex-shrink-0 border-gray-100 bg-gray-50/70 p-3 dark:border-gray-700 dark:bg-gray-900/60 sm:w-[250px] sm:border-l sm:border-t-0 flex flex-col overflow-y-auto custom-scrollbar"
                                :class="isMobileViewport
                                  ? 'min-h-0 flex-1 overflow-y-auto overscroll-contain touch-pan-y border-0'
                                  : 'border-t sm:border-t-0'"
                              >
                                <div v-if="isMobileViewport" class="mb-3 flex items-center gap-2">
                                  <button
                                    type="button"
                                    class="inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-800 dark:hover:bg-gray-800 dark:hover:text-gray-200"
                                    aria-label="返回模型列表"
                                    title="返回模型列表"
                                    @click="backFromThinkingPanel"
                                  >
                                    <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" /></svg>
                                  </button>
                                  <div class="min-w-0 flex-1">
                                    <div class="text-[11px] font-bold text-gray-800 dark:text-gray-100">
                                      {{ selectedModelConfig.thinking_enable ? '思考与参数' : '模型参数' }}
                                    </div>
                                    <div class="mt-0.5 truncate text-[10px] text-gray-500 dark:text-gray-400">{{ thinkingPanelSubtitle }}</div>
                                  </div>
                                  <button
                                    type="button"
                                    class="flex-shrink-0 rounded-lg px-2.5 py-1.5 text-[11px] font-semibold text-primary hover:bg-primary/5"
                                    @click="closeModelMenu"
                                  >
                                    完成
                                  </button>
                                </div>
                                <div v-else class="mb-3 flex items-center justify-between gap-2">
                                  <div class="min-w-0">
                                    <div class="text-[11px] font-bold text-gray-800 dark:text-gray-100">
                                      {{ selectedModelConfig.thinking_enable ? '思考与参数' : '模型参数' }}
                                    </div>
                                    <div class="mt-0.5 truncate text-[10px] text-gray-500 dark:text-gray-400">{{ thinkingPanelSubtitle }}</div>
                                  </div>
                                  <template v-if="selectedModelConfig.thinking_enable">
                                    <button
                                      v-if="canToggleThinking"
                                      type="button"
                                      class="relative inline-flex h-6 w-11 flex-shrink-0 overflow-hidden rounded-full p-0 transition-colors"
                                      :class="thinkingEnabledForSession ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600'"
                                      @click="toggleThinkingForSession"
                                      :aria-pressed="thinkingEnabledForSession"
                                      aria-label="切换本次会话思考模式"
                                      :title="thinkingEnabledForSession ? '关闭本次会话思考' : '开启本次会话思考'"
                                    >
                                      <span class="absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform" :class="thinkingEnabledForSession ? 'translate-x-5' : 'translate-x-0.5'"></span>
                                    </button>
                                    <span v-else class="rounded-full bg-primary/10 px-2 py-1 text-[10px] font-semibold text-primary dark:bg-primary/20 dark:text-primary-hover">
                                      已开启
                                    </span>
                                  </template>
                                </div>

                                <!-- 思考模块（仅在模型支持思考时显示） -->
                                <template v-if="selectedModelConfig.thinking_enable">
                                  <div v-if="isMobileViewport" class="mb-3 flex items-center justify-between">
                                    <div class="text-[10px] font-semibold text-gray-500 dark:text-gray-400">本次会话思考</div>
                                    <button
                                      v-if="canToggleThinking"
                                      type="button"
                                      class="relative inline-flex h-6 w-11 flex-shrink-0 overflow-hidden rounded-full p-0 transition-colors"
                                      :class="thinkingEnabledForSession ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600'"
                                      @click="toggleThinkingForSession"
                                      :aria-pressed="thinkingEnabledForSession"
                                      aria-label="切换本次会话思考模式"
                                      :title="thinkingEnabledForSession ? '关闭本次会话思考' : '开启本次会话思考'"
                                    >
                                      <span class="absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform" :class="thinkingEnabledForSession ? 'translate-x-5' : 'translate-x-0.5'"></span>
                                    </button>
                                    <span v-else class="rounded-full bg-primary/10 px-2 py-1 text-[10px] font-semibold text-primary dark:bg-primary/20 dark:text-primary-hover">
                                      已开启
                                    </span>
                                  </div>

                                  <div v-if="thinkingEnabledForSession" class="relative mb-2">
                                    <div class="mb-2 rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[10px] text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
                                      开启思考可能增加响应耗时，适合复杂推理任务。
                                    </div>
                                    <div class="mb-1 text-[10px] font-semibold text-gray-500 dark:text-gray-400">思考强度</div>
                                    <div class="max-h-[160px] overflow-y-auto rounded-lg border border-gray-200 bg-white p-1 custom-scrollbar dark:border-gray-700 dark:bg-gray-800">
                                      <button
                                        type="button"
                                        class="w-full rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/60"
                                        :class="isFollowingModelEffort ? 'bg-primary/5 font-semibold text-primary' : 'text-gray-700 dark:text-gray-300'"
                                        title="不覆盖模型注册配置"
                                        @click="selectReasoningEffort(null)"
                                      >
                                        <span class="flex items-center justify-between gap-2">
                                          <span>跟随模型默认</span>
                                          <span v-if="isFollowingModelEffort">✓</span>
                                        </span>
                                      </button>
                                      <button
                                        v-for="option in supportedReasoningEfforts"
                                        :key="option.value"
                                        type="button"
                                        class="w-full rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/60"
                                        :class="!isFollowingModelEffort && selectedReasoningEffort === option.value ? 'bg-primary/5 font-semibold text-primary' : 'text-gray-700 dark:text-gray-300'"
                                        :title="option.description"
                                        @click="selectReasoningEffort(option.value)"
                                      >
                                        <span class="flex items-center justify-between gap-2">
                                          <span>{{ option.label }}</span>
                                          <span v-if="!isFollowingModelEffort && selectedReasoningEffort === option.value">✓</span>
                                        </span>
                                      </button>
                                      <div v-if="supportedReasoningEfforts.length === 0" class="px-2 py-2 text-[10px] text-gray-400">模型未配置可选思考强度</div>
                                    </div>
                                  </div>
                                  <button
                                    v-else-if="canToggleThinking"
                                    type="button"
                                    class="mb-2 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-left text-[10px] text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400"
                                    @click="toggleThinkingForSession"
                                  >
                                    关闭思考后，本次会话将以非思考模式发送。
                                  </button>
                                </template>

                                <!-- 采样温度调节卡片 (Temperature) -->
                                <div :class="selectedModelConfig.thinking_enable ? 'mt-1 border-t border-gray-200/80 pt-2.5 dark:border-gray-700/80' : ''">
                                  <div class="mb-1.5 flex items-center justify-between">
                                    <span class="text-[10px] font-semibold text-gray-500 dark:text-gray-400">采样温度 (Temperature)</span>
                                    <div class="flex items-center gap-1.5">
                                      <span
                                        class="font-mono text-xs font-semibold"
                                        :class="effectiveTemperature > 1 ? 'text-amber-600 dark:text-amber-400' : 'text-primary'"
                                      >
                                        {{ effectiveTemperature.toFixed(2) }}
                                      </span>
                                      <button
                                        v-if="!isFollowingDefaultTemperature"
                                        type="button"
                                        class="text-[10px] text-gray-400 hover:text-primary transition-colors underline decoration-dotted"
                                        title="恢复跟随模型默认值"
                                        @click="resetTemperatureToDefault"
                                      >
                                        恢复默认
                                      </button>
                                      <span v-else class="text-[9px] text-gray-400">跟随默认</span>
                                    </div>
                                  </div>

                                  <!-- 滑块调节 -->
                                  <div class="px-0.5 py-1">
                                    <input
                                      type="range"
                                      min="0"
                                      max="2"
                                      step="0.05"
                                      :value="effectiveTemperature"
                                      @input="setCustomTemperature(Number(($event.target as HTMLInputElement).value))"
                                      class="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-gray-200 accent-primary dark:bg-gray-700"
                                      :title="`当前温度: ${effectiveTemperature.toFixed(2)}`"
                                    />
                                    <div class="mt-1 flex justify-between text-[9px] text-gray-400 select-none">
                                      <span>0.0 严谨</span>
                                      <span>1.0 均衡</span>
                                      <span>2.0 发散</span>
                                    </div>
                                  </div>

                                  <!-- 快捷预设场景胶囊 -->
                                  <div class="mt-2 grid grid-cols-3 gap-1">
                                    <button
                                      v-for="preset in PRESET_TEMPERATURES"
                                      :key="preset.value"
                                      type="button"
                                      class="rounded-md border py-1 text-center text-[10px] transition-colors"
                                      :class="!isFollowingDefaultTemperature && Math.abs(effectiveTemperature - preset.value) < 0.01
                                        ? 'border-primary bg-primary/10 font-semibold text-primary'
                                        : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:border-gray-600'"
                                      :title="preset.desc"
                                      @click="setCustomTemperature(preset.value)"
                                    >
                                      {{ preset.label }} {{ preset.value }}
                                    </button>
                                  </div>

                                  <!-- 动态指引与说明 -->
                                  <div class="mt-2 rounded-lg border border-gray-200/70 bg-white p-2 text-[10px] leading-relaxed text-gray-500 shadow-xs dark:border-gray-700/60 dark:bg-gray-800/80 dark:text-gray-400">
                                    {{ getTemperatureGuidance(effectiveTemperature) }}
                                  </div>

                                  <!-- 温度大于 1 时的警示说明（部分模型仅支持 0.0~1.0） -->
                                  <div
                                    v-if="effectiveTemperature > 1"
                                    class="mt-1.5 flex items-start gap-1.5 rounded-lg border border-amber-200 bg-amber-50/90 p-2 text-[10px] leading-snug text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/40 dark:text-amber-300"
                                  >
                                    <svg class="h-3.5 w-3.5 flex-shrink-0 text-amber-500 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                                    </svg>
                                    <span>当前温度大于 1.0。部分模型仅支持 0.0～1.0 范围，超出范围可能被服务商忽略或引发调用异常，请确认模型官方文档支持。</span>
                                  </div>
                                </div>
                              </div>
                            </div>
                        </div>
                    </transition>
                </div>

                <button type="button" @click="isProcessing ? emit('stop') : isSubmitting ? null : sendWithImageParams()" :disabled="!isProcessing && (isSubmitting || !canSend)" class="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full text-white hover:opacity-90 disabled:opacity-50 transition-all shadow-sm z-10 relative" :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }" :title="isProcessing ? '停止生成' : isSubmitting ? '准备发送…' : '发送'">
                    <svg v-if="isProcessing" class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><rect x="5" y="5" width="10" height="10" /></svg>
                    <svg v-else class="w-4 h-4 -rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M5 12h14M13 6l6 6-6 6" /></svg>
                </button>
            </div>
        </div>
      </div>

      <!-- 桌面端「更多」指令库：Teleport + 可视区内滚动，避免嵌在 h-8 栏里被裁切 -->
      <Teleport to="body">
        <div
          v-if="isDrawerExpanded && windowWidth >= 640"
          ref="desktopCommandDrawerRef"
          class="fixed z-[1200] px-1 animate-fade-in-up"
          :style="{
            left: `${desktopCommandDrawerPosition.left}px`,
            bottom: `${desktopCommandDrawerPosition.bottom}px`,
            width: `${desktopCommandDrawerPosition.width}px`,
          }"
          @mousedown.stop
          @wheel.stop
        >
          <div
            ref="desktopCommandDrawerPanelRef"
            class="bg-white/95 dark:bg-gray-800/95 backdrop-blur-xl border border-gray-200 dark:border-gray-700 p-4 shadow-2xl overflow-y-auto overscroll-contain custom-scrollbar ring-1 ring-black/5 rounded-2xl"
            :style="{ maxHeight: `${desktopCommandDrawerPosition.maxHeight}px` }"
          >
            <div class="flex items-center justify-between mb-6">
              <div class="flex items-center space-x-2">
                <span class="w-1.5 h-4 bg-primary rounded-full"></span>
                <span class="text-[11px] font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest">指令库 · Commands</span>
              </div>
              <button @click="closeCommandDrawer" class="w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 dark:bg-gray-700 text-gray-500 hover:bg-gray-200 transition-all">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" /></svg>
              </button>
            </div>
            <div class="space-y-6">
              <div v-if="filteredUserCommands.length > 0">
                <div class="text-[10px] font-black text-blue-500 mb-3 px-1 flex items-center uppercase tracking-tighter">Mine · 我的常用</div>
                <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  <div v-for="cmd in filteredUserCommands" :key="'grid-user-'+cmd.id" class="relative group/grid-item">
                    <button draggable="true" @dragstart="handleDragStart($event, cmd, 'user')" @dragover.prevent @drop="handleDrop($event, cmd, 'user')" @click="handleShortcutClick(cmd); closeCommandDrawer();" class="w-full text-left p-3.5 rounded-2xl bg-gray-50 dark:bg-gray-900/50 border border-gray-100 dark:border-gray-800 hover:border-primary/30 hover:bg-white dark:hover:bg-gray-900 hover:shadow-md transition-all">
                      <div class="text-xs font-bold text-gray-800 dark:text-gray-200 mb-1 truncate">{{ cmd.label }}</div>
                      <div class="text-[9px] text-gray-400 truncate opacity-60 font-mono">{{ cmd.command }}</div>
                    </button>
                    <button v-if="canDeleteCommand(cmd)" @click.stop="$emit('delete-command', cmd, $event)" class="absolute -top-1.5 -right-1.5 w-6 h-6 bg-red-500 text-white rounded-full flex items-center justify-center shadow-lg z-10 opacity-0 group-hover/grid-item:opacity-100 hover:scale-110 active:scale-95"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M6 18L18 6M6 6l12 12" /></svg></button>
                  </div>
                </div>
              </div>
              <div>
                <div class="text-[10px] font-black text-gray-400 mb-3 px-1 flex items-center uppercase tracking-tighter">System · 系统功能</div>
                <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  <button :disabled="cmd.disabled" v-for="cmd in filteredSystemCommands" :key="'grid-sys-'+cmd.id" @click="handleShortcutClick(cmd); closeCommandDrawer();" class="w-full text-left p-3.5 rounded-2xl bg-gray-50/50 dark:bg-gray-900/30 border border-transparent hover:bg-gray-100 dark:hover:bg-gray-800 transition-all disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-gray-50/50">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-gray-600 dark:text-gray-400 mb-1 truncate"><component v-if="getSystemCommandIcon(cmd)" :is="getSystemCommandIcon(cmd)" class="h-4 w-4 shrink-0" aria-hidden="true" />{{ cmd.label }}</div>
                    <div class="text-[9px] text-gray-400/60 truncate font-mono">{{ cmd.command }}</div>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </Teleport>

      <!-- 新会话类型菜单：Teleport 避免被快捷指令横向滚动 overflow 裁切 -->
      <Teleport to="body">
        <div
          v-if="showNewConversationMenu"
          ref="newConversationMenuPanelRef"
          class="fixed z-[1200] w-40 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-xl p-1"
          :style="{
            top: `${newConversationMenuPosition.top}px`,
            left: `${newConversationMenuPosition.left}px`,
          }"
          @mousedown.stop
          @click.stop
        >
          <button type="button" class="w-full inline-flex items-center gap-1.5 text-left px-3 py-2 rounded-lg text-xs text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700" @click.stop="selectNewConversationType('/new')"><ChatBubbleLeftRightIcon class="h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />新建普通会话</button>
          <button type="button" class="w-full inline-flex items-center gap-1.5 text-left px-3 py-2 rounded-lg text-xs text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700" @click.stop="selectNewConversationType('/project')"><FolderIcon class="h-4 w-4 shrink-0 text-amber-500" aria-hidden="true" />新建项目会话</button>
        </div>
      </Teleport>

      <MentionList
        ref="mentionListRef"
        :visible="showMentionList"
        :keyword="mentionKeyword"
        :agents="allowedAgents"
        :position="mentionPosition"
        :routing-mode="routingMode"
        :expert-agent-id="expertAgentId"
        :initial-tab="mentionInitialTab"
        :mounted-file-paths="mountedFilePaths"
        :expert-locked="lockExpertAgent"
        @select="handleMentionSelect"
        @select-auto="handleMentionSelectAuto"
        @select-file="handleMentionFileSelect"
        @open-workspace="handleOpenWorkspaceFromMention"
        @tab-change="handleMentionTabChange"
        @close="handleMentionClose"
      />

      <!-- Mobile command drawer (opened from header shortcut button) -->
      <Teleport to="body">
        <div
          v-if="isDrawerExpanded && windowWidth < 640"
          class="fixed inset-0 z-[9995] bg-black/40 backdrop-blur-sm flex flex-col justify-end p-0"
          @click.self="closeCommandDrawer"
        >
          <div class="bg-white/95 dark:bg-gray-800/95 backdrop-blur-xl border border-gray-200 dark:border-gray-700 p-4 shadow-2xl overflow-y-auto custom-scrollbar ring-1 ring-black/5 rounded-t-3xl max-h-[85vh] pb-12 animate-slide-up" @click.stop>
            <div class="flex items-center justify-between mb-6">
              <div class="flex items-center space-x-2">
                <span class="w-1.5 h-4 bg-primary rounded-full"></span>
                <span class="text-[11px] font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest">指令库 · Commands</span>
              </div>
              <button @click="closeCommandDrawer" class="w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 dark:bg-gray-700 text-gray-500 hover:bg-gray-200 transition-all">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" /></svg>
              </button>
            </div>
            <div class="space-y-6">
              <div v-if="filteredUserCommands.length > 0">
                <div class="text-[10px] font-black text-blue-500 mb-3 px-1 flex items-center uppercase tracking-tighter">Mine · 我的常用</div>
                <div class="grid grid-cols-2 gap-3">
                  <div v-for="cmd in filteredUserCommands" :key="'mobile-user-'+cmd.id" class="relative">
                    <button @click="handleShortcutClick(cmd); closeCommandDrawer();" class="w-full text-left p-3.5 rounded-2xl bg-gray-50 dark:bg-gray-900/50 border border-gray-100 dark:border-gray-800 hover:border-primary/30 hover:bg-white dark:hover:bg-gray-900 hover:shadow-md transition-all">
                      <div class="text-xs font-bold text-gray-800 dark:text-gray-200 mb-1 truncate">{{ cmd.label }}</div>
                      <div class="text-[9px] text-gray-400 truncate opacity-60 font-mono">{{ cmd.command }}</div>
                    </button>
                    <button v-if="canDeleteCommand(cmd)" @click.stop="$emit('delete-command', cmd, $event)" class="absolute -top-1.5 -right-1.5 w-6 h-6 bg-red-500 text-white rounded-full flex items-center justify-center shadow-lg z-10"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M6 18L18 6M6 6l12 12" /></svg></button>
                  </div>
                </div>
              </div>
              <div>
                <div class="text-[10px] font-black text-gray-400 mb-3 px-1 flex items-center uppercase tracking-tighter">System · 系统功能</div>
                <div class="grid grid-cols-2 gap-3">
                  <button :disabled="cmd.disabled" v-for="cmd in filteredSystemCommands" :key="'mobile-sys-'+cmd.id" @click="cmd.disabled ? null : (handleShortcutClick(cmd), closeCommandDrawer());" class="w-full text-left p-3.5 rounded-2xl bg-gray-50/50 dark:bg-gray-900/30 border border-transparent hover:bg-gray-100 dark:hover:bg-gray-800 transition-all disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-gray-50/50">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-gray-600 dark:text-gray-400 mb-1 truncate"><component v-if="getSystemCommandIcon(cmd)" :is="getSystemCommandIcon(cmd)" class="h-4 w-4 shrink-0" aria-hidden="true" />{{ cmd.label }}</div>
                    <div class="text-[9px] text-gray-400/60 truncate font-mono">{{ cmd.command }}</div>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </Teleport>
    </div>
</template>

<style scoped>
/* ── 原有动画保留 ── */
@keyframes slide-up { from { transform: translateY(100%); } to { transform: translateY(0); } }
.animate-slide-up { animation: slide-up 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards; }

/* ── AI 生成中：三点跳动 ── */
@keyframes ai-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
  40%           { transform: translateY(-5px); opacity: 1; }
}
.ai-dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background-color: #9ca3af;
  animation: ai-bounce 1.2s ease-in-out infinite;
}

.no-scrollbar::-webkit-scrollbar { display: none; }
.no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }

/* ── LTM 气泡淡入淡出滑动效果 ── */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.25s ease;
}
.fade-slide-enter-from,
.fade-slide-leave-to {
  transform: translateY(-8px);
  opacity: 0;
}
</style>
