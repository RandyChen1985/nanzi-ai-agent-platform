<script setup lang="ts">
import { ref, reactive, nextTick, computed, watch, onMounted, onUnmounted, type ComponentPublicInstance } from "vue";
import MentionList from "@/components/agent/MentionList.vue";
import AttachmentImageThumb from "@/components/embed/AttachmentImageThumb.vue";
import SkillCascadeMenu from "@/components/embed/SkillCascadeMenu.vue";
import type { SkillItem } from "@/components/embed/SkillCascadeMenu.vue";
import McpCascadeMenu from "@/components/embed/McpCascadeMenu.vue";
import type { McpToolItem } from "@/components/embed/McpCascadeMenu.vue";
import ExpertCascadeMenu from "@/components/embed/ExpertCascadeMenu.vue";
import type { ReasoningEffort } from "@/api/model";
import type { ContextCompactionRecord } from "@/api/agent";
import ContextCompactionTimeline from "@/components/chat/ContextCompactionTimeline.vue";
import { formatContextTokens, type ContextUsage } from "@/composables/useContextUsage";
import { isImageAttachment } from "@/utils/attachmentImages";
import { DATASET_PORTAL_SYSTEM_COMMAND_ID } from "@/constants/datasetPortalCommand";
import {
  ArchiveBoxIcon,
  ArrowPathIcon,
  BoltIcon,
  BookOpenIcon,
  ChartBarIcon,
  ChatBubbleLeftRightIcon,
  ChevronDownIcon,
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

const props = defineProps<{
  modelValue: string;
  isProcessing: boolean;
  /** 发送前置检查/历史同步阶段；此时不能显示“停止生成”。 */
  isSubmitting?: boolean;
  showShortcuts: boolean;
  slashCommands: any[];
  allowedAgents: any[];
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
  activeLtmPreference?: any;
  /** 当前会话有效智能体 ID，用于过滤平台技能列表 */
  agentId?: string | null;
  /** 会话已挂载的 MCP 工具名 */
  attachedMcpToolNames?: string[];
  /** 路由模式：auto | expert */
  routingMode?: string;
  /** 专家模式下选中的智能体 ID */
  expertAgentId?: string;
  /** 专家列表加载中 */
  isLoadingAgents?: boolean;
  /** URL agent_id 深链锁定：隐藏专家切换/@，禁止切自动路由 */
  lockExpertAgent?: boolean;
  /** Docker 沙箱工作区运行状态 */
  dockerWorkspaceStatus?: "idle" | "starting" | "stopping" | "running" | "error";
  /** 当前用户分配的 Docker 容器 ID */
  dockerWorkspaceContainerId?: string | null;
  /** Docker 沙箱容器启动时间 (ISO 8601) */
  dockerWorkspaceStartedAt?: string | null;
  /** Docker 沙箱容器运行时长秒数 */
  dockerWorkspaceUptimeSeconds?: number | null;
  /** Docker 沙箱错误信息 */
  dockerWorkspaceError?: string;
  /** 当前会话是否开启反幻觉校验 */
  enableGrounding?: boolean;
  /** 反幻觉阻断模式：严格缓冲 | 实时撤回 */
  groundingBlockMode?: "strict_buffer" | "stream_with_retraction";
}>();

const textareaPaddingRightClass = computed(() => {
  const hasContext = Boolean(props.contextUsage && props.contextUsage.physical_window);
  const hasGrounding = Boolean(props.enableGrounding);
  if (hasContext && hasGrounding) return "pr-48";
  if (hasContext || hasGrounding) return "pr-28";
  return "";
});

const isInteractionLocked = computed(() => props.isProcessing || props.isSubmitting === true);

const contextUsagePercent = computed(() => {
  const current = Number(props.contextUsage?.estimated_current_tokens || 0);
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
  const current = Number(props.contextUsage?.estimated_current_tokens || 0);
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
  const current = Number(props.contextUsage?.estimated_current_tokens || 0);
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
    .filter((record) => record.event_type === "context_summarized" || record.event_type === "context_compression")
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
  (e: 'manual-context-compaction', retainRatio: 0.25 | 0.5 | 0.75, mode: "fast" | "smart"): void;
  (e: 'start-docker-workspace'): void;
  (e: 'refresh-docker-workspace', manualFeedback?: boolean): void;
  (e: 'stop-docker-workspace'): void;
  (e: 'restart-docker-workspace'): void;
  (e: 'open-docker-terminal'): void;
  (e: 'disable-grounding'): void;
  (e: 'open-grounding-settings'): void;
}>();

const isDockerSandboxPolicy = computed(() => {
  const policy = String(props.contextUsage?.sandbox_policy || "").trim().toLowerCase();
  return policy === "docker";
});

const showDockerActionsMenu = ref(false);
const dockerActionsDropdownRef = ref<HTMLElement | null>(null);

const dockerUptimeNow = ref(Date.now());
let dockerUptimeTimer: ReturnType<typeof setInterval> | null = null;

const dockerUptimeSeconds = computed(() => {
  if (props.dockerWorkspaceStatus !== 'running') return 0;
  if (props.dockerWorkspaceStartedAt) {
    try {
      const started = new Date(props.dockerWorkspaceStartedAt).getTime();
      if (!Number.isNaN(started) && started > 0) {
        return Math.max(0, Math.floor((dockerUptimeNow.value - started) / 1000));
      }
    } catch {}
  }
  if (typeof props.dockerWorkspaceUptimeSeconds === 'number') {
    return Math.max(0, props.dockerWorkspaceUptimeSeconds);
  }
  return 0;
});

const dockerUptimeFormatted = computed(() => {
  if (props.dockerWorkspaceStatus !== 'running') return '';
  const seconds = dockerUptimeSeconds.value;
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
  if (atMatch && !props.lockExpertAgent) {
    const atIndex = val.slice(0, cursor).lastIndexOf(atMatch[0][0] || '@');
    const isStartOfWord = atIndex === 0 || val[atIndex - 1] === ' ' || val[atIndex - 1] === '\n';
    const query = atMatch[1] || '';
    if (isStartOfWord && props.allowedAgents.length > 0) {
        mentionKeyword.value = query;
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
    if (!canSend.value) return;
    e.preventDefault();
    emit('send');
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

const handleShortcutClick = (cmd: any) => {
    if (isInteractionLocked.value || !cmd) return;
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
    const cmd = filteredSystemCommands.value.find((c) => c.id === DATASET_PORTAL_SYSTEM_COMMAND_ID);
    handleShortcutClick(cmd);
};

import axios from "@/utils/axios";

// 附件上传状态
const uploadedFiles = ref<any[]>([]);

const isKnowledgePortalDisabled = computed(() => {
  return !!props.slashCommands?.find(c => c.id === 'sys_knowledge_portal')?.disabled;
});

const canSend = computed(
  () => !!props.modelValue.trim() || uploadedFiles.value.filter(f => f.type !== 'knowledge_settings').length > 0,
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
};

const backFromThinkingPanel = () => {
  showThinkingPanel.value = false;
};

/** 点模型行：移动端进思考二级；桌面端选中即关菜单 */
const selectModel = (model: ModelOption) => {
  emit("update:selectedModel", model.model_id);
  emit("update:thinking-enable-override", null);
  emit("update:reasoning-effort-override", null);
  if (isMobileViewport.value && model.thinking_enable) {
    showThinkingPanel.value = true;
    return;
  }
  closeModelMenu();
};

/** 点思考标签 / 箭头：打开思考设置（桌面侧栏 / 移动二级） */
const openThinkingSettings = (model: ModelOption, event?: Event) => {
  event?.stopPropagation();
  event?.preventDefault();
  emit("update:selectedModel", model.model_id);
  if (props.selectedModel !== model.model_id) {
    emit("update:thinking-enable-override", null);
    emit("update:reasoning-effort-override", null);
  }
  showModelDropdown.value = true;
  showThinkingPanel.value = true;
  if (isMobileViewport.value) nextTick(updateModelDropdownPosition);
};

const resetModelSelection = () => {
  emit("update:selectedModel", "");
  emit("update:thinking-enable-override", null);
  emit("update:reasoning-effort-override", null);
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
  // 选完强度即关闭，主路径更快
  closeModelMenu();
};

/** 未覆盖时高亮「跟随模型默认」，而不是落到模型注册档位 */
const isFollowingModelEffort = computed(
  () => props.reasoningEffortOverride === null || props.reasoningEffortOverride === undefined,
);

const isSelectedModelMultimodal = computed(() => {
  if (!props.selectedModel || !props.availableModels) return false;
  const m = props.availableModels.find((item) => item.model_id === props.selectedModel);
  return m?.type === 'multimodal';
});

const showModelDropdown = ref(false);
const modelDropdownRef = ref<HTMLElement | null>(null);
const modelDropdownTriggerRef = ref<HTMLButtonElement | null>(null);
const modelListScrollRef = ref<HTMLElement | null>(null);
const modelDropdownPosition = reactive({
  bottom: 12,
  left: 12,
  width: 0,
});

const scrollSelectedModelIntoView = () => {
  const list = modelListScrollRef.value;
  if (!list) return;
  const selected = list.querySelector('[data-model-current="true"]') as HTMLElement | null;
  if (!selected) return;
  const listRect = list.getBoundingClientRect();
  const itemRect = selected.getBoundingClientRect();
  list.scrollTop += itemRect.top - listRect.top - (list.clientHeight - itemRect.height) / 2;
};

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
    updateModelDropdownPosition();
  } else {
    showThinkingPanel.value = false;
    modelSearchQuery.value = "";
  }
  showModelDropdown.value = willOpen;
  if (willOpen) {
    nextTick(() => {
      if (isMobileViewport.value) updateModelDropdownPosition();
      scrollSelectedModelIntoView();
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
  if (isInteractionLocked.value) return;
  closeContextCompactionDetails();
  showContextUsageDetails.value = !showContextUsageDetails.value;
  if (showContextUsageDetails.value) {
    if (isDockerSandboxPolicy.value) {
      emit('refresh-docker-workspace', false);
    }
    await nextTick();
    updateContextUsageDetailsPlacement();
  }
};

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
  }
  if (showContextUsageDetails.value && contextUsageContainerRef.value && !contextUsageContainerRef.value.contains(event.target as Node)) {
    closeContextUsageDetails();
  }
  if (showContextCompactionDetails.value && contextUsageContainerRef.value && !contextUsageContainerRef.value.contains(event.target as Node)) {
    closeContextCompactionDetails();
  }
  if (showDockerActionsMenu.value && dockerActionsDropdownRef.value && !dockerActionsDropdownRef.value.contains(event.target as Node)) {
    showDockerActionsMenu.value = false;
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
      return;
    }
    if (showSkillCascade.value) {
      showSkillCascade.value = false;
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
  dockerUptimeTimer = setInterval(() => {
    if (showContextUsageDetails.value && props.dockerWorkspaceStatus === 'running') {
      dockerUptimeNow.value = Date.now();
    }
  }, 1000);
});

onUnmounted(() => {
  if (dockerUptimeTimer) {
    clearInterval(dockerUptimeTimer);
    dockerUptimeTimer = null;
  }
  closeContextUsageDetails();
  closeContextCompactionDetails();
  document.removeEventListener('click', handleGlobalClick);
  document.removeEventListener('keydown', handleGlobalKeydown);
  window.removeEventListener('resize', handleApprovalMenuLayout);
  window.removeEventListener('scroll', handleApprovalMenuLayout, true);
});
const showPlusMenu = ref(false);
const showSkillCascade = ref(false);
const showMcpCascade = ref(false);
const showExpertCascade = ref(false);
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
const fileInputRef = ref<HTMLInputElement | null>(null);

const attachedSkillIds = computed(() =>
  uploadedFiles.value
    .filter((f) => f.type === "skill")
    .map((f) => String(f.url)),
);

const isExpertMode = computed(
  () => props.routingMode === "expert" && !!props.expertAgentId,
);

const currentExpertAgent = computed(() => {
  if (!isExpertMode.value) return null;
  return props.allowedAgents?.find((a: any) => a.id === props.expertAgentId) || null;
});

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
  if (props.lockExpertAgent) {
    return isMobileViewport.value ? "今天帮你做些什么？" : "今天帮你做些什么？ / 调用技能与指令";
  }
  if (isMobileViewport.value) return "今天帮你做些什么？";
  return "今天帮你做些什么？ @ 选择智能体专家， / 调用技能与指令";
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
      showExpertCascade.value = false;
    }
  } else if (!isMobileViewport.value) {
    showSkillCascade.value = false;
    showExpertCascade.value = false;
  }
};

const toggleExpertSelector = () => {
  if (isInteractionLocked.value || props.lockExpertAgent) return;
  showExpertSelector.value = !showExpertSelector.value;
  if (showExpertSelector.value) {
    showPlusMenu.value = false;
    showSkillCascade.value = false;
    showExpertCascade.value = false;
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
  showExpertCascade.value = false;
  showMcpCascade.value = false;
  showSkillCascade.value = true;
  if (isMobileViewport.value) {
    showPlusMenu.value = false;
  }
  nextTick(() => {
    skillCascadeRef.value?.resetSearch?.();
  });
};

const openMcpCascade = () => {
  showExpertCascade.value = false;
  showSkillCascade.value = false;
  showMcpCascade.value = true;
  if (isMobileViewport.value) {
    showPlusMenu.value = false;
  }
  nextTick(() => {
    mcpCascadeRef.value?.resetSearch?.();
  });
};

const openExpertCascade = () => {
  if (props.lockExpertAgent) return;
  showSkillCascade.value = false;
  showMcpCascade.value = false;
  showExpertCascade.value = true;
  if (isMobileViewport.value) {
    showPlusMenu.value = false;
  }
  emit("refresh-agents");
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

/** 桌面端：悬停到加号菜单的非级联项时，收起技能/MCP/专家飞出层 */
const closePlusCascadesOnHover = () => {
  if (isMobileViewport.value) return;
  showSkillCascade.value = false;
  showMcpCascade.value = false;
  showExpertCascade.value = false;
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
  fileInputRef.value?.click();
};

watch(showPlusMenu, (open) => {
  if (!open && !isMobileViewport.value) {
    showSkillCascade.value = false;
    showMcpCascade.value = false;
    showExpertCascade.value = false;
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
};

// 核心文件上传逻辑
const uploadSingleFile = async (file: File) => {
  if (isInteractionLocked.value) return;
  if (file.size > 20 * 1024 * 1024) {
    alert("文件大小不能超过 20MB");
    return;
  }
  const name = file.name;
  const ext = '.' + name.split('.').pop()?.toLowerCase();
  const forbiddenExts = ['.exe', '.bat', '.sh', '.cmd', '.msi', '.php', '.js', '.html'];
  if (forbiddenExts.includes(ext)) {
    alert("暂不支持上传该类型的危险脚本文件");
    return;
  }

  isUploading.value = true;
  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await axios.post("/api/v1/chat/upload", formData);
    if (res.data && res.data.data) {
      uploadedFiles.value.push(res.data.data);
    } else {
      throw new Error("上传失败");
    }
  } catch (error: any) {
    console.error("Upload error:", error);
    alert(error.response?.data?.message || error.message || "上传文件时出错，请重试");
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
});
</script>

<template>
    <div class="flex-shrink-0 bg-white dark:bg-gray-900 flex flex-col relative z-20">
      <slot name="banner"></slot>

      <div
        :class="isMobileViewport
          ? 'px-3 pt-1 pb-[calc(env(safe-area-inset-bottom,0px)+0.625rem)]'
          : 'p-3 pb-2'"
      >
        <!-- Shortcut Bar (desktop only) -->
        <div
          v-if="showShortcutBar"
          ref="shortcutBarRef"
          data-shortcut-bar
          class="flex items-center space-x-2 mb-2 px-1 relative h-8"
          :class="{ 'opacity-50 pointer-events-none select-none': isProcessing }"
        >
            <!-- 1. Left Toggle Button (Visible on all devices now) -->
            <div @click="emit('toggle-shortcuts')" class="flex items-center space-x-1 cursor-pointer select-none group flex-shrink-0 bg-white dark:bg-gray-900 pr-2 z-10">
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
                                        <div
                                          v-if="cmd.id === 'sys_clear'"
                                          :ref="setNewConversationMenuRef"
                                          class="relative flex items-center shrink-0"
                                        >
                                          <button :disabled="cmd.disabled" @click="handleShortcutClick(cmd)" class="inline-flex items-center gap-1 px-2.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-l-full whitespace-nowrap hover:bg-gray-200 transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"><component :is="getSystemCommandIcon(cmd)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ cmd.label }}</button>
                                          <button type="button" @mousedown.stop @click.stop="toggleNewConversationMenu" class="px-1.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-r-full border-l border-white/70 dark:border-gray-700 hover:bg-gray-200" title="选择会话类型">⌄</button>
                                        </div>
                                        <button v-else :disabled="cmd.disabled" @click="handleShortcutClick(cmd)" class="inline-flex items-center gap-1 px-2.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-full whitespace-nowrap hover:bg-gray-200 transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-gray-100"><component v-if="getSystemCommandIcon(cmd)" :is="getSystemCommandIcon(cmd)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ cmd.label }}</button>
                                    </template>
                                    <div v-if="showShortcutDivider" class="w-px h-3 bg-gray-200 dark:bg-gray-700 flex-shrink-0"></div>
                                    <template v-for="cmd in visibleRowUserCommands" :key="'row-user-'+cmd.id">
                                        <button @click="handleShortcutClick(cmd)" class="inline-flex items-center gap-1 px-2.5 py-1 text-[10px] font-bold bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300 border border-blue-100/50 dark:border-blue-800 rounded-full whitespace-nowrap hover:bg-blue-100 transition-colors flex-shrink-0"><component v-if="getSystemCommandIcon(cmd)" :is="getSystemCommandIcon(cmd)" class="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{{ cmd.label }}</button>
                                    </template>
                                </div>
                            </div>
                            <button
                                v-if="hasShortcutChips"
                                data-shortcut-more
                                type="button"
                                @click.stop="toggleCommandDrawer"
                                class="flex-shrink-0 inline-flex items-center text-[10px] font-black text-primary hover:opacity-80 transition-all whitespace-nowrap"
                                :class="{ 'opacity-100': isDrawerExpanded }"
                            >
                                {{ isDrawerExpanded ? '收起' : '更多' }}
                                <svg class="w-3 h-3 ml-0.5 transition-transform" :class="{ 'rotate-180': isDrawerExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M19 9l-7 7-7-7" /></svg>
                            </button>
                </div>
            </div>

            <!-- Add Button -->
            <button @click="emit('open-command-manager')" class="flex-shrink-0 p-1.5 text-gray-400 hover:text-primary transition-all rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 group" title="新建快捷指令">
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
          @dragover.prevent
          @drop="handleDropFile"
          class="relative flex flex-col rounded-2xl border bg-white px-3 py-2.5 transition-all duration-300 focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/25 dark:bg-gray-800 dark:focus-within:ring-primary/30"
          :class="isProcessing
            ? 'border-primary/60 bg-blue-50/30 dark:bg-blue-950/20 dark:border-primary/50 input-glow-processing'
            : 'border-gray-200 dark:border-gray-700'"
        >
            <!-- 三点跳动 Loading 指示器 -->
            <div v-if="isInteractionLocked" class="absolute top-3 left-3 flex items-center space-x-1.5 pointer-events-none z-20">
                <span class="ai-dot" style="animation-delay: 0ms"></span>
                <span class="ai-dot" style="animation-delay: 150ms"></span>
                <span class="ai-dot" style="animation-delay: 300ms"></span>
                <span class="ml-1.5 text-[11px] font-medium text-primary/70 select-none">{{ isProcessing ? (enableGrounding ? 'AI 正在生成并严格核验证据…' : 'AI 正在生成回复…') : isSubmitting ? '准备发送…' : '' }}</span>
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
              </div>
              <div class="overflow-y-auto p-1 custom-scrollbar">
                <div
                  v-for="(cmd, index) in filteredCommands"
                  :key="cmd.id"
                  @click="cmd.disabled ? null : selectCommand(cmd)"
                  class="flex cursor-pointer items-center space-x-3 rounded-lg px-3 py-2 transition-all"
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
                </div>
              </div>
            </div>

            <textarea ref="inputRef" :value="modelValue" :disabled="isInteractionLocked" @input="handleInput" @focus="handleFocus" @keydown="handleKeydown" @compositionstart="handleCompositionStart" @compositionend="handleCompositionEnd" @paste="handlePaste" rows="1" class="w-full bg-transparent border-none outline-none focus:ring-0 text-base sm:text-sm placeholder:text-sm px-0 py-1 resize-none max-h-32 text-gray-900 dark:text-gray-100 placeholder-gray-400 peer z-10 relative disabled:cursor-not-allowed" :class="[
              isInteractionLocked ? 'min-h-[46px] opacity-0 pointer-events-none' : 'min-h-[46px] opacity-100',
              textareaPaddingRightClass,
            ]" :placeholder="inputPlaceholder"></textarea>

            <!-- 输入框内部右上角状态浮标组 (反幻觉浮标 + 上下文用量胶囊，完全对齐且低饱和淡雅配色) -->
            <div class="absolute right-2 top-2 z-30 flex items-center gap-1.5 pointer-events-auto">
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
                <button
                  type="button"
                  data-testid="context-usage-indicator"
                  class="inline-flex h-[22px] box-border items-center gap-1 rounded-full border px-2 text-[10px] font-medium leading-none transition-colors focus:outline-none focus:ring-2 focus:ring-primary/20"
                  :class="contextUsageTone.badge"
                  :aria-expanded="showContextUsageDetails"
                  aria-haspopup="dialog"
                  aria-controls="context-usage-details"
                  :aria-label="`上下文使用 ${formatContextTokens(contextUsage.estimated_current_tokens)} / ${formatContextTokens(contextUsage.physical_window)}`"
                  :title="`上下文使用 ${formatContextTokens(contextUsage.estimated_current_tokens)} / ${formatContextTokens(contextUsage.physical_window)}`"
                  @click.stop="toggleContextUsageDetails"
                >
                    <span aria-hidden="true" class="h-1.5 w-1.5 rounded-full" :class="contextUsageTone.dot" />
                    <span>{{ formatContextTokens(contextUsage.estimated_current_tokens) }}</span>
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
                                {{ formatContextTokens(contextUsage.estimated_current_tokens) }} /
                                {{ formatContextTokens(contextUsage.physical_window) }}
                            </span>
                            <span class="opacity-75">· {{ contextUsagePercentLabel }}</span>
                            </span>
                        </span>
                    </div>
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

                        <!-- Docker 容器运行状态与控制明细 -->
                        <div
                          v-if="isDockerSandboxPolicy"
                          class="flex flex-col gap-1.5 rounded-lg bg-gray-50/90 dark:bg-gray-800/70 p-2 text-[10px] font-mono border border-gray-100/90 dark:border-gray-700/70"
                        >
                          <div class="flex items-center justify-between gap-2">
                            <div class="flex items-center gap-1.5 min-w-0">
                              <span
                                class="inline-block h-2 w-2 shrink-0 rounded-full"
                                :class="{
                                  'bg-emerald-500 shadow-sm shadow-emerald-500/50': (dockerWorkspaceStatus || 'idle') === 'running',
                                  'bg-amber-400 animate-pulse': (dockerWorkspaceStatus || 'idle') === 'starting' || (dockerWorkspaceStatus || 'idle') === 'stopping',
                                  'bg-rose-500 shadow-sm shadow-rose-500/50': (dockerWorkspaceStatus || 'idle') === 'error',
                                  'bg-gray-300 dark:bg-gray-600': (dockerWorkspaceStatus || 'idle') === 'idle'
                                }"
                              ></span>
                              <span class="font-medium text-gray-700 dark:text-gray-200">
                                {{
                                  (dockerWorkspaceStatus || 'idle') === 'running' ? '容器已运行' :
                                  (dockerWorkspaceStatus || 'idle') === 'starting' ? '容器启动中...' :
                                  (dockerWorkspaceStatus || 'idle') === 'stopping' ? '容器关机中...' :
                                  (dockerWorkspaceStatus || 'idle') === 'error' ? '容器启动失败' : '容器未启动'
                                }}
                              </span>
                              <span
                                v-if="dockerWorkspaceContainerId"
                                class="truncate text-[9px] text-gray-400 dark:text-gray-500 max-w-[110px]"
                                :title="`当前容器 ID: ${dockerWorkspaceContainerId}`"
                              >
                                {{ dockerWorkspaceContainerId.slice(0, 12) }}
                              </span>
                            </div>

                            <div class="shrink-0 flex items-center gap-1">
                              <button
                                v-if="(dockerWorkspaceStatus || 'idle') === 'idle' || (dockerWorkspaceStatus || 'idle') === 'error'"
                                type="button"
                                class="rounded bg-indigo-600 px-2 py-0.5 text-[9px] font-medium text-white shadow-sm hover:bg-indigo-500 active:scale-95 transition-all disabled:opacity-50"
                                :disabled="isInteractionLocked"
                                :title="(dockerWorkspaceStatus || 'idle') === 'error' ? (dockerWorkspaceError || '重试启动 Docker 沙箱') : '启动当前用户的 Docker 沙箱容器'"
                                @click.stop="emit('start-docker-workspace')"
                              >
                                {{ (dockerWorkspaceStatus || 'idle') === 'error' ? '重试启动' : '启动容器' }}
                              </button>
                              <button
                                type="button"
                                class="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-gray-400 dark:hover:text-indigo-300 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
                                title="手动检测刷新 Docker 沙箱状态"
                                :disabled="(dockerWorkspaceStatus || 'idle') === 'starting' || (dockerWorkspaceStatus || 'idle') === 'stopping'"
                                @click.stop="emit('refresh-docker-workspace', true)"
                              >
                                <ArrowPathIcon class="h-3 w-3" :class="{ 'animate-spin': (dockerWorkspaceStatus || 'idle') === 'starting' || (dockerWorkspaceStatus || 'idle') === 'stopping' }" aria-hidden="true" />
                                <span>刷新</span>
                              </button>

                              <!-- Docker 容器运行时的操作下拉菜单 (进入 | 重启 | 关机) -->
                              <div
                                v-if="(dockerWorkspaceStatus || 'idle') === 'running'"
                                ref="dockerActionsDropdownRef"
                                class="relative inline-block text-left"
                              >
                                <button
                                  type="button"
                                  class="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[9px] text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-gray-400 dark:hover:text-indigo-300 dark:hover:bg-gray-700 transition-colors" :class="{ 'text-indigo-600 bg-indigo-50 dark:text-indigo-300 dark:bg-gray-700': showDockerActionsMenu }"
                                  title="Docker 容器管理操作"
                                  @click.stop="showDockerActionsMenu = !showDockerActionsMenu"
                                >
                                  <span>操作</span>
                                  <ChevronDownIcon class="h-2.5 w-2.5 transition-transform duration-200" :class="{ 'rotate-180': showDockerActionsMenu }" />
                                </button>

                                <div
                                  v-if="showDockerActionsMenu"
                                  class="absolute right-0 bottom-full mb-1.5 z-50 w-28 rounded-lg bg-white dark:bg-gray-800 py-1 shadow-lg ring-1 ring-black/5 dark:ring-white/10 border border-gray-100 dark:border-gray-700 text-[10px] font-sans"
                                  @click.stop
                                >
                                  <button
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 dark:hover:text-indigo-300 transition-colors"
                                    @click="showDockerActionsMenu = false; emit('open-docker-terminal')"
                                  >
                                    <CommandLineIcon class="h-3.5 w-3.5 text-emerald-500" />
                                    <span>进入终端</span>
                                  </button>
                                  <button
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 hover:text-indigo-600 dark:hover:text-indigo-300 transition-colors"
                                    @click="showDockerActionsMenu = false; emit('restart-docker-workspace')"
                                  >
                                    <ArrowPathIcon class="h-3.5 w-3.5 text-indigo-500" />
                                    <span>重启容器</span>
                                  </button>
                                  <div class="my-0.5 border-t border-gray-100 dark:border-gray-700/60"></div>
                                  <button
                                    type="button"
                                    class="flex w-full items-center gap-1.5 px-2.5 py-1 text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors"
                                    @click="showDockerActionsMenu = false; emit('stop-docker-workspace')"
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
                            v-if="(dockerWorkspaceStatus || 'idle') === 'running' && dockerUptimeFormatted"
                            class="flex items-center justify-between text-[9px] text-gray-400 dark:text-gray-500 pt-1 border-t border-gray-200/50 dark:border-gray-700/50"
                          >
                            <span class="flex items-center gap-1">
                              <ClockIcon class="h-3 w-3 text-emerald-500/80 shrink-0" aria-hidden="true" />
                              <span>运行时长：<strong class="text-gray-600 dark:text-gray-300 font-medium">{{ dockerUptimeFormatted }}</strong></span>
                            </span>
                            <span class="text-[8.5px] text-gray-400/80 dark:text-gray-500/80" title="容器连续空闲 30 分钟后将自动销毁释放资源">
                              空闲 30m 自动回收
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

            <div class="relative z-20 mt-1 flex min-h-7 flex-nowrap items-center gap-0.5 sm:gap-1.5">
                <!-- Plus Button & Menu (Premium Glassmorphism Style) -->
                <div ref="plusMenuContainerRef" class="relative flex-shrink-0 z-30">
                    <button @click="togglePlusMenu" :disabled="isInteractionLocked" class="w-8 h-8 sm:w-7 sm:h-7 flex items-center justify-center rounded-full text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-700 transition-all duration-200 focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-gray-400" :class="{ 'text-primary bg-gray-100 dark:bg-gray-700 rotate-45': showPlusMenu && !isInteractionLocked }" title="添加附件或上下文">
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
                                <div class="w-52 bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-gray-200 dark:border-gray-700 py-1.5 animate-fade-in-up">
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

                                    <!-- Mobile: 新会话 / 历史（桌面端快捷指令栏已有，此处方便移动端触发） -->
                                    <template v-if="isMobileViewport">
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
                                          type="button"
                                          @mouseenter="closePlusCascadesOnHover"
                                          @click="showPlusMenu = false; showSkillCascade = false; showMcpCascade = false; showExpertCascade = false; emit('system-command', '/history');"
                                          class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                        >
                                            <ClockIcon class="h-5 w-5 shrink-0 text-slate-500" aria-hidden="true" />
                                            <span class="font-medium text-left">历史</span>
                                        </button>
                                    </template>
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
                </div>

                <!-- 智能委派胶囊 (桌面端；移动端走加号「专家中心」；URL 锁定时隐藏) -->
                <div v-if="!isMobileViewport && !lockExpertAgent" ref="expertSelectorRef" class="relative flex-shrink-0 z-30">
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
                        <svg
                          v-if="isExpertMode"
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

                <div ref="approvalTriggerWrapperRef" class="relative flex-shrink-0">
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
                <!-- Custom Model Dropdown Selector -->
                <div ref="modelDropdownRef" class="relative flex-shrink min-w-0">
                    <button
                      ref="modelDropdownTriggerRef"
                      :disabled="isInteractionLocked"
                      @click="toggleModelDropdown"
                      class="relative flex h-7 items-center gap-0.5 sm:gap-1 rounded-full px-1.5 sm:px-2.5 text-[11px] sm:text-xs font-semibold leading-none text-gray-600 transition-colors hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700/70 disabled:opacity-50 disabled:cursor-not-allowed select-none max-w-[min(46vw,10.5rem)] sm:max-w-[280px]"
                      :title="selectedModel ? `覆盖模型: ${modelLabel}${thinkingSummaryLabel ? ` · ${thinkingSummaryLabel}` : ''}` : '使用智能体默认模型'"
                    >
                        <PhotoIcon v-if="isSelectedModelMultimodal" class="pointer-events-none hidden h-3.5 w-3.5 shrink-0 text-purple-500 sm:inline" aria-hidden="true" />
                        <span class="pointer-events-none truncate flex-1 min-w-0 text-left">{{ modelLabel }}</span>
                        <span v-if="thinkingSummaryLabel" class="pointer-events-none flex-shrink-0 rounded-full bg-violet-50 px-1 py-0.5 text-[8px] sm:px-1.5 sm:text-[9px] font-semibold text-violet-600 dark:bg-violet-950/50 dark:text-violet-300">{{ thinkingSummaryLabel }}</span>
                        <svg class="pointer-events-none h-3 w-3 sm:h-3.5 sm:w-3.5 flex-shrink-0 text-gray-400 transform transition-transform duration-200" :class="{ 'rotate-180': showModelDropdown }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
                        </svg>
                    </button>

                    <!-- Custom Dropdown Menu (Pop up upwards) -->
                    <transition name="slide-up">
                        <div
                          v-show="showModelDropdown"
                          class="z-30 overflow-hidden bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl"
                          :class="isMobileViewport
                            ? 'fixed max-h-[min(70vh,420px)]'
                            : (showThinkingPanel && selectedModelConfig?.thinking_enable
                              ? 'absolute bottom-full mb-2 right-0 w-[min(560px,calc(100vw-24px))] max-h-[min(448px,calc(100vh-96px))] origin-bottom-right'
                              : 'absolute bottom-full mb-2 right-0 w-[min(300px,calc(100vw-24px))] max-h-[min(448px,calc(100vh-96px))] origin-bottom-right')"
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
                                    v-model="modelSearchQuery"
                                    type="search"
                                    placeholder="搜索模型…"
                                    class="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 outline-none focus:border-primary/40 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200"
                                    @click.stop
                                  />
                                </div>
                                <div ref="modelListScrollRef" class="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5 custom-scrollbar touch-pan-y">
                                <button
                                  @click="resetModelSelection"
                                  class="w-full text-left px-2.5 py-2 rounded-lg text-xs transition-all flex items-center justify-between"
                                  :data-model-current="!selectedModel ? 'true' : undefined"
                                  :class="
                                    !selectedModel
                                      ? 'bg-primary/5 text-primary font-bold'
                                      : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                                  "
                                >
                                    <span class="truncate">使用默认模型</span>
                                    <span v-if="!selectedModel" class="text-[10px]">✓</span>
                                </button>

                                <button
                                  v-for="model in filteredAvailableModels"
                                  :key="model.id || model.model_id"
                                  type="button"
                                  class="w-full text-left px-2.5 py-2 rounded-lg text-xs transition-all flex items-center justify-between mt-0.5"
                                  :data-model-current="selectedModel === model.model_id ? 'true' : undefined"
                                  :class="
                                    selectedModel === model.model_id
                                      ? 'bg-primary/5 text-primary font-bold'
                                      : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                                  "
                                  @click="selectModel(model)"
                                >
                                    <div class="flex items-center space-x-1.5 min-w-0 flex-1">
                                        <PhotoIcon v-if="model.type === 'multimodal'" class="h-3.5 w-3.5 shrink-0 text-purple-500" title="多模态" aria-hidden="true" />
                                        <span class="truncate">{{ model.name || model.model_id }}</span>
                                    </div>
                                    <div class="ml-1 flex flex-shrink-0 items-center gap-1">
                                      <span v-if="selectedModel === model.model_id" class="text-[10px]">✓</span>
                                      <button
                                        v-if="model.thinking_enable"
                                        type="button"
                                        class="inline-flex items-center gap-0.5 rounded-full bg-violet-50 px-1.5 py-0.5 text-[9px] font-medium text-violet-600 hover:bg-violet-100 dark:bg-violet-950/50 dark:text-violet-300 dark:hover:bg-violet-900/50"
                                        :title="isMobileViewport ? '思考设置' : '调整本次会话思考'"
                                        @click="openThinkingSettings(model, $event)"
                                      >
                                        <span>{{ selectedModel === model.model_id ? (thinkingSummaryLabel || '思考') : '思考' }}</span>
                                        <svg class="h-3 w-3 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m9 5 7 7-7 7" /></svg>
                                      </button>
                                    </div>
                                </button>
                                <div v-if="filteredAvailableModels.length === 0" class="px-2.5 py-3 text-center text-[10px] text-gray-400">
                                  无匹配模型
                                </div>
                                </div>
                              </div>

                              <!-- 二级（移动）/ 侧栏（桌面）：思考设置 -->
                              <div
                                v-if="showThinkingPanel && selectedModelConfig?.thinking_enable"
                                class="w-full flex-shrink-0 border-gray-100 bg-gray-50/70 p-3 dark:border-gray-700 dark:bg-gray-900/60 sm:w-[240px] sm:border-l sm:border-t-0"
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
                                    <div class="text-[11px] font-bold text-gray-800 dark:text-gray-100">思考模式</div>
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
                                    <div class="text-[11px] font-bold text-gray-800 dark:text-gray-100">思考模式</div>
                                    <div class="mt-0.5 truncate text-[10px] text-gray-500 dark:text-gray-400">{{ thinkingPanelSubtitle }}</div>
                                  </div>
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
                                  <span v-else class="rounded-full bg-violet-100 px-2 py-1 text-[10px] font-semibold text-violet-700 dark:bg-violet-950/60 dark:text-violet-300">
                                    已开启
                                  </span>
                                </div>

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
                                  <span v-else class="rounded-full bg-violet-100 px-2 py-1 text-[10px] font-semibold text-violet-700 dark:bg-violet-950/60 dark:text-violet-300">
                                    已开启
                                  </span>
                                </div>

                                <div v-if="thinkingEnabledForSession" class="relative">
                                  <div class="mb-2 rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[10px] text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
                                    开启思考可能增加响应耗时，适合复杂推理任务。
                                  </div>
                                  <div class="mb-1 text-[10px] font-semibold text-gray-500 dark:text-gray-400">思考强度</div>
                                  <div class="max-h-[240px] overflow-y-auto rounded-lg border border-gray-200 bg-white p-1 custom-scrollbar dark:border-gray-700 dark:bg-gray-800">
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
                                  class="mt-2 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-left text-[10px] text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400"
                                  @click="toggleThinkingForSession"
                                >
                                  关闭思考后，本次会话将以非思考模式发送。
                                </button>
                              </div>
                            </div>
                        </div>
                    </transition>
                </div>

                <button type="button" @click="isProcessing ? emit('stop') : isSubmitting ? null : emit('send')" :disabled="!isProcessing && (isSubmitting || !canSend)" class="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full text-white hover:opacity-90 disabled:opacity-50 transition-all shadow-sm z-10 relative" :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }" :title="isProcessing ? '停止生成' : isSubmitting ? '准备发送…' : '发送'">
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
        @select="handleMentionSelect"
        @select-auto="handleMentionSelectAuto"
        @close="showMentionList = false"
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
  background-color: var(--primary-color, #1677ff);
  animation: ai-bounce 1.2s ease-in-out infinite;
}

/* ── AI 生成中：边框呼吸光晕 ── */
@keyframes glow-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(22, 119, 255, 0); }
  50%       { box-shadow: 0 0 0 4px rgba(22, 119, 255, 0.15), 0 0 16px 2px rgba(22, 119, 255, 0.10); }
}
.input-glow-processing {
  animation: glow-pulse 2s ease-in-out infinite;
}

/* ── 滚动条 ── */
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.5); border-radius: 2px; }

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
