<script setup lang="ts">
import { ref, reactive, nextTick, computed, watch, onMounted, onUnmounted } from "vue";
import MentionList from "@/components/agent/MentionList.vue";
import AttachmentImageThumb from "@/components/embed/AttachmentImageThumb.vue";
import SkillCascadeMenu from "@/components/embed/SkillCascadeMenu.vue";
import type { SkillItem } from "@/components/embed/SkillCascadeMenu.vue";
import ExpertCascadeMenu from "@/components/embed/ExpertCascadeMenu.vue";
import { isImageAttachment } from "@/utils/attachmentImages";
import { DATASET_PORTAL_SYSTEM_COMMAND_ID } from "@/constants/datasetPortalCommand";

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
};

const props = defineProps<{
  modelValue: string;
  isProcessing: boolean;
  showShortcuts: boolean;
  slashCommands: any[];
  allowedAgents: any[];
  currentUser: any;
  windowWidth: number;
  approvalMode?: ApprovalMode;
  selectedModel?: string;
  availableModels?: ModelOption[];
  activeLtmPreference?: any;
  /** 当前会话有效智能体 ID，用于过滤平台技能列表 */
  agentId?: string | null;
  /** 路由模式：auto | expert */
  routingMode?: string;
  /** 专家模式下选中的智能体 ID */
  expertAgentId?: string;
  /** 专家列表加载中 */
  isLoadingAgents?: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', val: string): void;
  (e: 'update:approvalMode', val: ApprovalMode): void;
  (e: 'update:selectedModel', val: string): void;
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
  (e: 'ignore-ltm'): void;
  (e: 'dismiss-ltm'): void;
}>();

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
const setNewConversationMenuRef = (el: Element | null) => {
  newConversationMenuRef.value = (el as HTMLElement | null) ?? null;
};
const activeCommandIndex = ref(0);
const mentionListRef = ref<any>(null);
const isDrawerExpanded = ref(false);

const openCommandDrawer = () => {
  isDrawerExpanded.value = true;
};

const closeCommandDrawer = () => {
  isDrawerExpanded.value = false;
};

const toggleNewConversationMenu = () => {
  showNewConversationMenu.value = !showNewConversationMenu.value;
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

watch(() => props.isProcessing, (processing) => {
  if (processing) {
    showPlusMenu.value = false;
    showApprovalMenu.value = false;
    isDrawerExpanded.value = false;
    showCommandMenu.value = false;
    showMentionList.value = false;
  }
});

const toggleApprovalMenu = () => {
  if (props.isProcessing) return;
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
  if (props.isProcessing) return;
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
  if (atMatch) {
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
  if (props.isProcessing) return;
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
  if (props.isProcessing || !cmd) return;
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
    if (props.isProcessing || !cmd) return;
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
    if (props.isProcessing) return;
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
  if (!props.selectedModel) return "模型";
  const model = props.availableModels?.find((item) => item.model_id === props.selectedModel);
  return model?.name || props.selectedModel;
});

const isSelectedModelMultimodal = computed(() => {
  if (!props.selectedModel || !props.availableModels) return false;
  const m = props.availableModels.find((item) => item.model_id === props.selectedModel);
  return m?.type === 'multimodal';
});

const showModelDropdown = ref(false);
const modelDropdownRef = ref<HTMLElement | null>(null);

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
const approvalTriggerWrapperRef = ref<HTMLElement | null>(null);
const approvalMenuPanelRef = ref<HTMLElement | null>(null);
const approvalTriggerRef = ref<HTMLButtonElement | null>(null);
const showApprovalMenu = ref(false);
const approvalMenuPosition = reactive({
  bottom: 0,
  left: 0,
  width: 320,
});

const isMobileViewport = computed(() => props.windowWidth < 640);

const shortcutRowRef = ref<HTMLElement | null>(null);
const shortcutMeasureRef = ref<HTMLElement | null>(null);
const visibleSystemCount = ref(Number.POSITIVE_INFINITY);
const visibleUserCount = ref(Number.POSITIVE_INFINITY);
let shortcutResizeObserver: ResizeObserver | null = null;

const visibleRowSystemCommands = computed(() =>
  filteredSystemCommands.value.filter((cmd) => cmd.id !== 'sys_project').slice(0, visibleSystemCount.value),
);
const visibleRowUserCommands = computed(() =>
  filteredUserCommands.value.slice(0, visibleUserCount.value),
);
const hasHiddenShortcuts = computed(
  () =>
    visibleSystemCount.value < filteredSystemCommands.value.length
    || visibleUserCount.value < filteredUserCommands.value.length,
);
const showShortcutDivider = computed(
  () =>
    visibleRowSystemCommands.value.length > 0
    && visibleRowUserCommands.value.length > 0,
);

const recalcVisibleShortcuts = async () => {
  await nextTick();
  const row = shortcutRowRef.value;
  const measure = shortcutMeasureRef.value;
  if (!row || !measure) {
    visibleSystemCount.value = filteredSystemCommands.value.length;
    visibleUserCount.value = filteredUserCommands.value.length;
    return;
  }

  const moreBtn = row.querySelector("[data-shortcut-more]") as HTMLElement | null;
  const gap = 8;
  const dividerWidth = 13;
  let available = row.clientWidth - ((moreBtn?.offsetWidth ?? 0) + gap);

  const sysButtons = Array.from(
    measure.querySelectorAll<HTMLElement>("[data-measure-sys]"),
  );
  const userButtons = Array.from(
    measure.querySelectorAll<HTMLElement>("[data-measure-user]"),
  );

  let sysCount = 0;
  for (const btn of sysButtons) {
    const need = btn.offsetWidth + (sysCount > 0 ? gap : 0);
    if (available < need) break;
    available -= need;
    sysCount += 1;
  }

  let userCount = 0;
  if (userButtons.length > 0 && sysCount > 0 && available >= dividerWidth) {
    available -= dividerWidth;
  }

  for (const btn of userButtons) {
    const need = btn.offsetWidth + (userCount > 0 ? gap : 0);
    if (available < need) break;
    available -= need;
    userCount += 1;
  }

  visibleSystemCount.value = sysCount;
  visibleUserCount.value = userCount;
};

const setupShortcutResizeObserver = () => {
  shortcutResizeObserver?.disconnect();
  if (!shortcutRowRef.value) return;
  shortcutResizeObserver = new ResizeObserver(() => {
    void recalcVisibleShortcuts();
  });
  shortcutResizeObserver.observe(shortcutRowRef.value);
};

watch(
  [showShortcutBar, filteredSystemCommands, filteredUserCommands, () => props.windowWidth],
  async () => {
    if (!showShortcutBar.value) return;
    await recalcVisibleShortcuts();
    setupShortcutResizeObserver();
  },
  { deep: true, immediate: true },
);

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
  if (showApprovalMenu.value) {
    const target = event.target as Node;
    const inTrigger = approvalTriggerWrapperRef.value?.contains(target);
    const inPanel = approvalMenuPanelRef.value?.contains(target);
    if (!inTrigger && !inPanel) {
      showApprovalMenu.value = false;
    }
  }
  if (showModelDropdown.value && modelDropdownRef.value && !modelDropdownRef.value.contains(event.target as Node)) {
    showModelDropdown.value = false;
  }
  // 新会话类型菜单：点击外部关闭（勿用 mouseleave，否则空隙会误关）
  if (showNewConversationMenu.value) {
    const target = event.target as Node;
    const root = newConversationMenuRef.value;
    // ref 写在 v-for 内时可能是数组
    const el = Array.isArray(root) ? root[0] : root;
    if (el && !el.contains(target)) {
      showNewConversationMenu.value = false;
    }
  }
};

const handleGlobalKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') {
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
    showPlusMenu.value = false;
    showApprovalMenu.value = false;
    showModelDropdown.value = false;
  }
};

const handleApprovalMenuLayout = () => {
  if (showApprovalMenu.value) updateApprovalMenuPosition();
  if (showExpertSelector.value && !isMobileViewport.value) updateExpertMenuPosition();
};

onMounted(() => {
  document.addEventListener('click', handleGlobalClick);
  document.addEventListener('keydown', handleGlobalKeydown);
  window.addEventListener('resize', handleApprovalMenuLayout);
  window.addEventListener('scroll', handleApprovalMenuLayout, true);
});

onUnmounted(() => {
  document.removeEventListener('click', handleGlobalClick);
  document.removeEventListener('keydown', handleGlobalKeydown);
  window.removeEventListener('resize', handleApprovalMenuLayout);
  window.removeEventListener('scroll', handleApprovalMenuLayout, true);
  shortcutResizeObserver?.disconnect();
});
const showPlusMenu = ref(false);
const showSkillCascade = ref(false);
const showExpertCascade = ref(false);
const skillCascadeRef = ref<InstanceType<typeof SkillCascadeMenu> | null>(null);
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
  return isMobileViewport.value ? "自动" : "全能助手";
});

const approvalCapsuleLabel = computed(() => activeApprovalLabel.value);

const inputPlaceholder = computed(() => {
  if (props.isProcessing) return "";
  if (isMobileViewport.value) return "今天帮你做些什么？";
  return "今天帮你做些什么？ @ 选择智能体专家， / 调用技能与指令";
});

const togglePlusMenu = () => {
  if (props.isProcessing) return;
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
  if (props.isProcessing) return;
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
  emit("switch-to-auto");
  showExpertSelector.value = false;
  showExpertCascade.value = false;
  showPlusMenu.value = false;
};

const selectExpertAgent = (agentId: string) => {
  emit("switch-to-expert", agentId);
  showExpertSelector.value = false;
  showExpertCascade.value = false;
  showPlusMenu.value = false;
};

const openSkillCascade = () => {
  showExpertCascade.value = false;
  showSkillCascade.value = true;
  if (isMobileViewport.value) {
    showPlusMenu.value = false;
  }
  nextTick(() => {
    skillCascadeRef.value?.resetSearch?.();
  });
};

const openExpertCascade = () => {
  showSkillCascade.value = false;
  showExpertCascade.value = true;
  if (isMobileViewport.value) {
    showPlusMenu.value = false;
  }
  emit("refresh-agents");
};

const closeSkillCascade = () => {
  showSkillCascade.value = false;
};

const closeExpertCascade = () => {
  showExpertCascade.value = false;
};

/** 桌面端：悬停到加号菜单的非级联项时，收起技能/专家飞出层 */
const closePlusCascadesOnHover = () => {
  if (isMobileViewport.value) return;
  showSkillCascade.value = false;
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
  showExpertCascade.value = false;
  showPlusMenu.value = false;
};

const triggerFileInput = () => {
  showPlusMenu.value = false;
  showSkillCascade.value = false;
  showExpertCascade.value = false;
  fileInputRef.value?.click();
};

watch(showPlusMenu, (open) => {
  if (!open && !isMobileViewport.value) {
    showSkillCascade.value = false;
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
  if (props.isProcessing) return;
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
  if (props.isProcessing) return;
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
  if (props.isProcessing) return;
  e.preventDefault();
  const files = e.dataTransfer?.files;
  if (!files) return;
  for (const file of Array.from(files)) {
    await uploadSingleFile(file);
  }
};

// 暴露属性给父组件
const focus = () => {
  inputRef.value?.focus();
};

defineExpose({
  uploadedFiles,
  focus,
  openCommandDrawer,
  closeCommandDrawer,
});
</script>

<template>
    <div class="flex-shrink-0 bg-white dark:bg-gray-900 flex flex-col relative z-20">
      <slot name="banner"></slot>

      <!-- Active LTM Preference Banner -->
      <transition name="fade-slide">
        <div v-if="activeLtmPreference" class="mx-3 mt-2 px-3 py-1.5 bg-indigo-50/80 dark:bg-indigo-950/40 border border-indigo-100/30 dark:border-indigo-900/30 rounded-xl flex items-center justify-between z-10 animate-fade-in-up">
          <div class="flex items-center space-x-2 text-[11px] text-indigo-600 dark:text-indigo-300 font-medium select-none">
            <span class="text-sm">🧠</span>
            <span class="truncate max-w-[200px] sm:max-w-[400px]">已自动应用您的常用偏好：{{ formatLtmText(activeLtmPreference) }}</span>
          </div>
          
          <div class="flex items-center space-x-2 flex-shrink-0 ml-2">
            <!-- 忽略本次按钮 -->
            <button 
              @click="emit('ignore-ltm')" 
              class="flex items-center space-x-1 px-1.5 py-0.5 border border-indigo-200 dark:border-indigo-900/50 hover:bg-indigo-100 dark:hover:bg-indigo-900/40 rounded-md text-[10px] text-indigo-500 hover:text-indigo-600 dark:text-indigo-400 transition-all font-medium"
              title="在本轮提问中临时停用并忽略此记忆偏好"
            >
              <span>🚫</span>
              <span>忽略本次</span>
            </button>
            
            <!-- 分割线 -->
            <span class="w-[1px] h-3 bg-indigo-100 dark:bg-indigo-900/50"></span>
            
            <!-- 仅关闭按钮 -->
            <button 
              @click="emit('dismiss-ltm')" 
              class="p-1 hover:bg-indigo-100 dark:hover:bg-indigo-900/40 rounded-full text-indigo-400 hover:text-indigo-600 dark:text-indigo-500 transition-colors" 
              title="仅关闭此提示气泡，不影响本轮偏好生效"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      </transition>

      <div
        :class="isMobileViewport
          ? 'px-3 pt-1 pb-[calc(env(safe-area-inset-bottom,0px)+0.625rem)]'
          : 'p-3 pb-2'"
      >
        <!-- Shortcut Bar (desktop only) -->
        <div v-if="showShortcutBar" class="flex items-center space-x-2 mb-2 px-1 relative h-8" :class="{ 'opacity-50 pointer-events-none select-none': isProcessing }">
            <!-- 1. Left Toggle Button (Visible on all devices now) -->
            <div @click="emit('toggle-shortcuts')" class="flex items-center space-x-1 cursor-pointer select-none group flex-shrink-0 bg-white dark:bg-gray-900 pr-2 z-10">
                <span class="text-[10px] font-black text-gray-400 group-hover:text-primary transition-colors tracking-tighter">⚡️ 快捷指令</span>
                <svg class="w-3 h-3 text-gray-300 group-hover:text-primary transition-transform duration-200 rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M19 9l-7 7-7-7" /></svg>
            </div>

            <!-- 2. Middle Content -->
            <div class="flex-1 min-w-0 relative">
                <div
                    ref="shortcutMeasureRef"
                    class="absolute left-0 top-0 -z-10 h-0 overflow-hidden opacity-0 pointer-events-none"
                    aria-hidden="true"
                >
                    <div class="flex items-center gap-2">
                        <button
                            v-for="cmd in filteredSystemCommands.filter((item) => item.id !== 'sys_project')"
                            :key="'measure-sys-' + cmd.id"
                            data-measure-sys
                            type="button"
                            class="px-2.5 py-1 text-[10px] font-bold bg-gray-100/80 rounded-full whitespace-nowrap flex-shrink-0"
                        >{{ cmd.label }}</button>
                        <button
                            v-for="cmd in filteredUserCommands"
                            :key="'measure-user-' + cmd.id"
                            data-measure-user
                            type="button"
                            class="px-2.5 py-1 text-[10px] font-bold bg-blue-50 border border-blue-100/50 rounded-full whitespace-nowrap flex-shrink-0"
                        >{{ cmd.label }}</button>
                    </div>
                </div>
                <transition enter-active-class="transition-all duration-300 ease-out" enter-from-class="opacity-0 -translate-y-2" enter-to-class="opacity-100 translate-y-0" leave-active-class="transition-all duration-200 ease-in" leave-to-class="opacity-0 -translate-y-2">
                    <div class="w-full">
                        <div v-if="!isDrawerExpanded" ref="shortcutRowRef" class="flex flex-1 min-w-0 items-center gap-2">
                            <div class="relative flex-1 min-w-0 overflow-visible">
                                <div class="flex items-center gap-2 min-w-0">
                                    <template v-for="cmd in visibleRowSystemCommands" :key="'row-sys-'+cmd.id">
                                        <div
                                          v-if="cmd.id === 'sys_clear'"
                                          :ref="setNewConversationMenuRef"
                                          class="relative flex items-center shrink-0"
                                        >
                                          <button :disabled="cmd.disabled" @click="handleShortcutClick(cmd)" class="px-2.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-l-full whitespace-nowrap hover:bg-gray-200 transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed">{{ cmd.label }}</button>
                                          <button type="button" @mousedown.stop @click.stop="toggleNewConversationMenu" class="px-1.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-r-full border-l border-white/70 dark:border-gray-700 hover:bg-gray-200" title="选择会话类型">⌄</button>
                                          <!-- pt-2 桥接触发区与面板，避免空隙导致指针落空 -->
                                          <div
                                            v-if="showNewConversationMenu"
                                            @mousedown.stop
                                            class="absolute left-0 top-full z-[100] pt-2"
                                          >
                                            <div class="w-40 rounded-xl border border-gray-200 bg-white dark:bg-gray-800 shadow-xl p-1">
                                              <button type="button" class="w-full text-left px-3 py-2 rounded-lg text-xs text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700" @click.stop="selectNewConversationType('/new')">💬 新建普通会话</button>
                                              <button type="button" class="w-full text-left px-3 py-2 rounded-lg text-xs text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700" @click.stop="selectNewConversationType('/project')">📁 新建项目会话</button>
                                            </div>
                                          </div>
                                        </div>
                                        <button v-else :disabled="cmd.disabled" @click="handleShortcutClick(cmd)" class="px-2.5 py-1 text-[10px] font-bold bg-gray-100/80 dark:bg-gray-800 text-gray-500 rounded-full whitespace-nowrap hover:bg-gray-200 transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-gray-100">{{ cmd.label }}</button>
                                    </template>
                                    <div v-if="showShortcutDivider" class="w-px h-3 bg-gray-200 dark:bg-gray-700 flex-shrink-0"></div>
                                    <template v-for="cmd in visibleRowUserCommands" :key="'row-user-'+cmd.id">
                                        <button @click="handleShortcutClick(cmd)" class="px-2.5 py-1 text-[10px] font-bold bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300 border border-blue-100/50 dark:border-blue-800 rounded-full whitespace-nowrap hover:bg-blue-100 transition-colors flex-shrink-0">{{ cmd.label }}</button>
                                    </template>
                                </div>
                                <div
                                    v-if="hasHiddenShortcuts"
                                    class="absolute right-0 top-0 bottom-0 w-8 pointer-events-none bg-gradient-to-l from-white via-white/90 to-transparent dark:from-gray-900 dark:via-gray-900/90"
                                    aria-hidden="true"
                                />
                            </div>
                            <button
                                data-shortcut-more
                                type="button"
                                @click="openCommandDrawer"
                                class="flex-shrink-0 inline-flex items-center text-[10px] font-black text-primary hover:opacity-80 transition-all whitespace-nowrap"
                            >
                                更多 <svg class="w-3 h-3 ml-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M19 9l-7 7-7-7" /></svg>
                            </button>
                        </div>

                        <!-- Desktop command drawer -->
                        <div v-else class="z-50 absolute bottom-full left-0 right-0 mb-3 animate-fade-in-up px-1" @click.self="closeCommandDrawer">
                            <div class="bg-white/95 dark:bg-gray-800/95 backdrop-blur-xl border border-gray-200 dark:border-gray-700 p-4 shadow-2xl overflow-y-auto custom-scrollbar ring-1 ring-black/5 rounded-2xl max-h-[24rem]" @click.stop>
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
                                                <div class="text-xs font-bold text-gray-600 dark:text-gray-400 mb-1 truncate">{{ cmd.label }}</div>
                                                <div class="text-[9px] text-gray-400/60 truncate font-mono">{{ cmd.command }}</div>
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </transition>
            </div>

            <!-- Add Button -->
            <button @click="emit('open-command-manager')" class="flex-shrink-0 p-1.5 text-gray-400 hover:text-primary transition-all rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 group" title="新建快捷指令">
                <svg class="w-4 h-4 transform group-hover:rotate-90 transition-transform duration-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4" /></svg>
            </button>
        </div>

        <!-- Attachments Preview Bar -->
        <div v-if="uploadedFiles.filter(f => f.type !== 'knowledge_settings').length > 0" class="flex flex-wrap gap-2 px-1 mb-2 max-h-36 overflow-y-auto no-scrollbar py-1">
            <template v-for="(file, idx) in uploadedFiles" :key="idx">
              <div v-if="file.type !== 'knowledge_settings'" class="relative flex items-center group bg-gray-100/80 dark:bg-gray-800/80 border border-gray-200/30 dark:border-gray-700/30 rounded-lg p-1.5 pr-8 max-w-[200px] transition-all hover:bg-white dark:hover:bg-gray-800 hover:shadow-sm">
                  <!-- Image Preview -->
                  <AttachmentImageThumb
                    v-if="isImage(file)"
                    :file="file"
                    clickable
                    class="mr-2"
                    @click="openImagePreview"
                  />
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
            <div v-if="isProcessing" class="absolute top-3 left-3 flex items-center space-x-1.5 pointer-events-none z-20">
                <span class="ai-dot" style="animation-delay: 0ms"></span>
                <span class="ai-dot" style="animation-delay: 150ms"></span>
                <span class="ai-dot" style="animation-delay: 300ms"></span>
                <span class="ml-1.5 text-[11px] font-medium text-primary/70 select-none">AI 正在生成回复…</span>
            </div>

            <div
              v-if="showCommandMenu && filteredCommands.length > 0 && !isProcessing"
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
                      <span class="truncate text-sm font-bold text-gray-900 dark:text-gray-100" :class="[index === activeCommandIndex && !cmd.disabled ? 'text-primary' : '', cmd.disabled ? 'text-gray-400 dark:text-gray-500' : '']">
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

            <textarea ref="inputRef" :value="modelValue" :disabled="isProcessing" @input="handleInput" @focus="handleFocus" @keydown="handleKeydown" @compositionstart="handleCompositionStart" @compositionend="handleCompositionEnd" @paste="handlePaste" rows="1" class="w-full bg-transparent border-none outline-none focus:ring-0 text-base sm:text-sm placeholder:text-sm px-0 py-1 resize-none max-h-32 text-gray-900 dark:text-gray-100 placeholder-gray-400 peer z-10 relative disabled:cursor-not-allowed" :class="isProcessing ? 'min-h-[46px] opacity-0 pointer-events-none' : 'min-h-[46px] opacity-100'" :placeholder="inputPlaceholder"></textarea>

            <div class="relative z-20 mt-1 flex min-h-9 flex-nowrap items-center gap-1 sm:gap-2">
                <!-- Plus Button & Menu (Premium Glassmorphism Style) -->
                <div ref="plusMenuContainerRef" class="relative flex-shrink-0 z-30">
                    <button @click="togglePlusMenu" :disabled="isProcessing" class="w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-700 transition-all duration-200 focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-gray-400" :class="{ 'text-primary bg-gray-100 dark:bg-gray-700 rotate-45': showPlusMenu && !isProcessing }" title="添加附件或上下文">
                        <svg class="w-5 h-5 transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
                                        <span class="text-lg">📊</span>
                                        <span class="font-medium text-left">打开数据门户</span>
                                    </button>

                                    <!-- Knowledge Base -->
                                    <button
                                      :disabled="isKnowledgePortalDisabled"
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="isKnowledgePortalDisabled ? null : (showPlusMenu = false, showSkillCascade = false, showExpertCascade = false, emit('select-knowledge-base'));"
                                      class="w-full flex items-center justify-between px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                                    >
                                        <div class="flex items-center space-x-3">
                                            <span class="text-lg">📚</span>
                                            <span class="font-medium text-left">打开知识库中心</span>
                                        </div>
                                    </button>

                                    <!-- Browse Workspace -->
                                    <button
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="showPlusMenu = false; showSkillCascade = false; showExpertCascade = false; emit('select-local-fs');"
                                      class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                    >
                                        <span class="text-lg">💻</span>
                                        <span class="font-medium text-left">浏览工作空间</span>
                                    </button>

                                    <!-- Upload File -->
                                    <button
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="triggerFileInput"
                                      class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                    >
                                        <span class="text-lg">📁</span>
                                        <span class="font-medium text-left">上传本地文件</span>
                                    </button>

                                    <!-- Memory Records (moved up) -->
                                    <button
                                      @mouseenter="closePlusCascadesOnHover"
                                      @click="showPlusMenu = false; showSkillCascade = false; showExpertCascade = false; emit('select-memory');"
                                      class="w-full flex items-center space-x-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary transition-all duration-150"
                                    >
                                        <span class="text-lg">🧠</span>
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
                                            <span class="text-lg">⚙️</span>
                                            <span class="font-medium text-left">技能中心</span>
                                        </div>
                                        <svg class="w-3.5 h-3.5 flex-shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                        </svg>
                                    </button>

                                    <!-- Expert cascade -->
                                    <button
                                      type="button"
                                      class="w-full flex items-center justify-between px-3 py-2 text-sm transition-all duration-150"
                                      :class="showExpertCascade
                                        ? 'bg-gray-100 dark:bg-gray-700/80 text-gray-900 dark:text-gray-100'
                                        : 'text-gray-700 dark:text-gray-200 hover:bg-primary/10 dark:hover:bg-primary/20 hover:text-primary'"
                                      @mouseenter="!isMobileViewport && openExpertCascade()"
                                      @click.stop="openExpertCascade"
                                    >
                                        <div class="flex items-center space-x-3">
                                            <span class="text-lg">🤖</span>
                                            <span class="font-medium text-left">专家中心</span>
                                        </div>
                                        <svg class="w-3.5 h-3.5 flex-shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                                        </svg>
                                    </button>
                                </div>

                                <!-- Desktop flyout: Skills -->
                                <div
                                  v-if="showSkillCascade && !isMobileViewport"
                                  class="absolute z-[60] left-full top-0 ml-1.5"
                                  @click.stop
                                >
                                  <SkillCascadeMenu
                                    ref="skillCascadeRef"
                                    :agent-id="agentId"
                                    :attached-skill-ids="attachedSkillIds"
                                    @select="mountSkillFromCascade"
                                  />
                                </div>

                                <!-- Desktop flyout: Experts -->
                                <div
                                  v-if="showExpertCascade && !isMobileViewport"
                                  class="absolute z-[60] left-full top-0 ml-1.5"
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
                            class="absolute inset-x-0 bottom-0 overflow-hidden rounded-t-2xl border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-2xl pb-[max(0.5rem,env(safe-area-inset-bottom))]"
                            @click.stop
                          >
                            <div class="shrink-0 flex justify-center pt-2 pb-1" aria-hidden="true">
                              <div class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600" />
                            </div>
                            <div class="flex items-center justify-between px-3 pb-1">
                              <span class="text-sm font-semibold text-gray-900 dark:text-gray-100">技能中心</span>
                              <button
                                type="button"
                                class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 p-1"
                                @click="closeSkillCascade"
                              >
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
                              </button>
                            </div>
                            <SkillCascadeMenu
                              ref="skillCascadeRef"
                              full-width
                              :agent-id="agentId"
                              :attached-skill-ids="attachedSkillIds"
                              @select="mountSkillFromCascade"
                            />
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
                            />
                          </div>
                        </div>
                      </transition>
                    </Teleport>
                </div>

                <!-- Expert routing capsule (桌面端；移动端走加号「专家中心」) -->
                <div v-if="!isMobileViewport" ref="expertSelectorRef" class="relative flex-shrink-0 z-30">
                    <button
                      type="button"
                      :disabled="isProcessing"
                      :title="isExpertMode ? `当前专家：${expertCapsuleLabel}` : '全能助手（自动路由）'"
                      class="flex h-8 items-center gap-0.5 sm:gap-1 rounded-full px-1.5 sm:px-2.5 text-xs font-semibold leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-40 max-w-[5.25rem] sm:max-w-[11rem]"
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
                        <svg
                          v-else
                          class="h-3.5 w-3.5 flex-shrink-0 opacity-90"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          aria-hidden="true"
                        >
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 10V3L4 14h7v7l9-11h-7z" />
                        </svg>
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
                      :disabled="isProcessing"
                      :title="`工具批准：${activeApprovalLabel}`"
                      class="flex h-8 max-w-[9.5rem] sm:max-w-none items-center gap-1 sm:gap-1.5 rounded-full px-2 sm:px-3 text-xs font-semibold leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                      :class="[
                        approvalTriggerToneClass,
                        showApprovalMenu && !isProcessing
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
                          class="h-3.5 w-3.5 sm:h-4 sm:w-4 flex-shrink-0 opacity-90"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          aria-hidden="true"
                        >
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
                        </svg>
                        <svg
                          v-else-if="activeApprovalMode === 'allow'"
                          class="h-3.5 w-3.5 sm:h-4 sm:w-4 flex-shrink-0 opacity-90"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          aria-hidden="true"
                        >
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016a11.959 11.959 0 0 0-4.5-1.253Z" />
                        </svg>
                        <svg
                          v-else
                          class="h-3.5 w-3.5 sm:h-4 sm:w-4 flex-shrink-0 opacity-90"
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
                        <svg class="h-3.5 w-3.5 flex-shrink-0 opacity-70 transition-transform" :class="{ 'rotate-180': showApprovalMenu }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
                                      class="border-b border-gray-100 dark:border-gray-700"
                                      :class="isMobileViewport ? 'px-4 py-3.5' : 'px-3 py-2.5'"
                                    >
                                        <p
                                          class="font-semibold text-gray-900 dark:text-gray-100"
                                          :class="isMobileViewport ? 'text-sm' : 'text-xs'"
                                        >应如何批准工具操作？</p>
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
                      :disabled="isProcessing"
                      @click="showModelDropdown = !showModelDropdown"
                      class="relative flex h-8 items-center gap-0.5 sm:gap-1.5 rounded-full px-2 sm:px-3 text-xs font-semibold leading-none text-gray-600 transition-colors hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700/70 border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed select-none max-w-[6.5rem] sm:max-w-[14rem]"
                      :title="selectedModel ? `覆盖模型: ${selectedModel}` : '使用智能体默认模型'"
                    >
                        <span v-if="isSelectedModelMultimodal" class="pointer-events-none select-none text-[11px] text-purple-500">🖼️</span>
                        <span class="pointer-events-none truncate flex-1 text-left">{{ modelLabel }}</span>
                        <svg class="pointer-events-none h-3.5 w-3.5 flex-shrink-0 text-gray-400 transform transition-transform duration-200" :class="{ 'rotate-180': showModelDropdown }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
                        </svg>
                    </button>

                    <!-- Custom Dropdown Menu (Pop up upwards) -->
                    <transition name="slide-up">
                        <div
                          v-show="showModelDropdown"
                          class="absolute bottom-full mb-2 right-0 z-30 w-52 max-h-60 overflow-y-auto bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl py-1.5 px-1 custom-scrollbar origin-bottom-right"
                        >
                            <!-- Default Option -->
                            <button
                              @click="
                                emit('update:selectedModel', '');
                                showModelDropdown = false;
                              "
                              class="w-full text-left px-2.5 py-1.5 rounded-lg text-xs transition-all flex items-center justify-between"
                              :class="
                                !selectedModel
                                  ? 'bg-primary/5 text-primary font-bold'
                                  : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                              "
                            >
                                <span class="truncate">使用默认模型</span>
                                <span v-if="!selectedModel" class="text-[10px]">✓</span>
                            </button>

                            <!-- Available Models Options -->
                            <button
                              v-for="model in (availableModels || [])"
                              :key="model.id || model.model_id"
                              @click="
                                emit('update:selectedModel', model.model_id);
                                showModelDropdown = false;
                              "
                              class="w-full text-left px-2.5 py-1.5 rounded-lg text-xs transition-all flex items-center justify-between mt-0.5"
                              :class="
                                selectedModel === model.model_id
                                  ? 'bg-primary/5 text-primary font-bold'
                                  : 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                              "
                            >
                                <div class="flex items-center space-x-1.5 min-w-0 flex-1">
                                    <span v-if="model.type === 'multimodal'" class="text-[10px] text-purple-500 flex-shrink-0" title="多模态">🖼️</span>
                                    <span class="truncate">{{ model.name || model.model_id }}</span>
                                </div>
                                <span v-if="selectedModel === model.model_id" class="text-[10px] ml-1 flex-shrink-0">✓</span>
                            </button>
                        </div>
                    </transition>
                </div>

                <button @click="isProcessing ? emit('stop') : emit('send')" :disabled="!isProcessing && !canSend" class="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full text-white hover:opacity-90 disabled:opacity-50 transition-all shadow-sm z-10 relative" :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }" :title="isProcessing ? '停止生成' : '发送'">
                    <svg v-if="isProcessing" class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><rect x="5" y="5" width="10" height="10" /></svg>
                    <svg v-else class="w-4 h-4 -rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M5 12h14M13 6l6 6-6 6" /></svg>
                </button>
            </div>
        </div>
      </div>

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
                    <div class="text-xs font-bold text-gray-600 dark:text-gray-400 mb-1 truncate">{{ cmd.label }}</div>
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
