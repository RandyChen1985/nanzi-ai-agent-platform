<script setup lang="ts">
// ... imports ...
import { ref, nextTick, watch, onUnmounted, reactive, onMounted, computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import TraceLogViewer from "@/components/TraceLogViewer.vue";
import DebugConfigPanel from "@/components/DebugConfigPanel.vue";
import AgentLogicFlowModal from "@/components/debug/AgentLogicFlowModal.vue";
import ChatHistorySidebar from "@/components/ChatHistorySidebar.vue";
import MessageRenderer from "@/components/MessageRenderer.vue";
import ToolPermissionCard from "@/components/chat/ToolPermissionCard.vue";
import GroundingBlockedCard from "@/components/GroundingBlockedCard.vue";
import BusinessConfirmationCard from "@/components/BusinessConfirmationCard.vue";
import UserQuestionCard from "@/components/UserQuestionCard.vue";
import DatasetCapabilityMenu from "@/components/chatbi/DatasetCapabilityMenu.vue";
import DatasetPortalDrawer from "@/components/chatbi/DatasetPortalDrawer.vue";
import ChatBIInsightPanel from "@/components/chatbi/ChatBIInsightPanel.vue";
import ChatBIContinueAnalysis from "@/components/chatbi/ChatBIContinueAnalysis.vue";
import MessageContinueAnalysis from "@/components/chat/MessageContinueAnalysis.vue";
import ErrorDetailCard from "@/components/chat/ErrorDetailCard.vue";
import ChatBIMonitorDialog from "@/components/chatbi/ChatBIMonitorDialog.vue";
import ChatBIMetadataGuide from "@/components/chatbi/ChatBIMetadataGuide.vue";
import AgentHandoffNotice from "@/components/chat/AgentHandoffNotice.vue";
import type { ChatBIInsightMeta } from "@/types/chatbiInsight";
import { applyChatBIInsightEvent } from "@/utils/chatbiInsight";
import type { ChatBIMetadataGuide as ChatBIMetadataGuidePayload } from "@/types/chatbiMetadataGuide";
import { applyChatBIMetadataGuideEvent } from "@/utils/chatbiMetadataGuide";
import type { AgentHandoffNoticeData } from "@/types/agentHandoff";
import { applyAgentHandoffEvent } from "@/utils/agentHandoff";
import KnowledgePortalDrawer from "@/components/knowledge/KnowledgePortalDrawer.vue";
import { useDatasetPortal } from "@/composables/useDatasetPortal";
import { useDatasetMount } from "@/composables/useDatasetMount";
import { useKnowledgePortal } from "@/composables/useKnowledgePortal";
import {
  DATASET_PORTAL_SLASH_COMMAND,
  DATASET_PORTAL_SYSTEM_COMMAND_ID,
  isDatasetPortalSlashCommand,
  KNOWLEDGE_PORTAL_SLASH_COMMAND,
  KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID,
  isKnowledgePortalSlashCommand,
} from "@/constants/datasetPortalCommand";
import {
  WORKSPACE_SLASH_COMMAND,
  WORKSPACE_SYSTEM_COMMAND_ID,
  isWorkspaceSlashCommand,
} from "@/constants/workspaceCommand";
import CitationPopover from "@/components/CitationPopover.vue";
import RagPreviewDrawer from "@/components/RagPreviewDrawer.vue";
import axios from "@/utils/axios";
import { finalizeConversation } from "@/utils/conversationFinalize";
import { cancelConversationRun } from "@/utils/cancelConversationRun";
import { createConversationId } from "@/utils/conversationId";
import { createSseLineParser } from "@/utils/chartRenderer";
import { normalizeAgentSwitchCommand } from "@/utils/agentSwitchCommands";
import {
  applyStreamTraceId,
  appendAssistantBodyDelta,
  dispatchAgentscopeStreamEvent,
  formatExternalExecutionStatus,
  markStalePendingStreamLogs,
  mergeStreamCitations,
  resumeExternalExecutionStream,
  syncProcessTimelineLog,
  type PendingExternalExecution,
  type PendingToolPermission,
  type GroundingBlockedAction,
  type GroundingBlockedPayload,
} from "@/utils/agentscopeSseHandlers";
import { hydrateHistoryProcessTimeline, timelineHasPending } from "@/utils/processTimeline";
import { normalizeSubagentTraceMeta, type SubagentTraceMeta } from "@/utils/subagentTrace";
import {
  buildBusinessConfirmationUserMessage,
  type BusinessConfirmationField,
  type BusinessConfirmationState,
} from "@/utils/businessConfirmation";
import {
  buildUserQuestionUserMessage,
  type UserQuestionState,
} from "@/utils/userQuestion";
import { useToast } from "../composables/useToast";
import { useTokenQuota } from "@/composables/useTokenQuota";
import { useContextUsage } from "@/composables/useContextUsage";
import { useContextCompactions } from "@/composables/useContextCompactions";
import { buildQuotaStatusMarkdown } from "@/utils/quotaDisplay";
import {
  buildSkillFlowBadges,
  summarizeSkillFlowBadges,
  type SkillFlowBadge,
} from "@/utils/skillFlowBadges";

import ChatInput from "@/components/embed/ChatInput.vue";
import SkillCreatedBanner from "@/components/chat/SkillCreatedBanner.vue";
import { parseSkillCreatedMarker, type SkillCreatedInfo } from "@/utils/skillCreated";
import WorkspaceBrowserDrawer from "@/components/embed/WorkspaceBrowserDrawer.vue";
import MemoryBrowserDrawer from "@/components/embed/MemoryBrowserDrawer.vue";
import ChatCanvas from "@/components/embed/ChatCanvas.vue";
import ChatExecutionTimeline from "@/components/chat/ChatExecutionTimeline.vue";
import ChatTodoCard from "@/components/chat/ChatTodoCard.vue";
import ChatModelCallStatsModal from "@/components/chat/ChatModelCallStatsModal.vue";
import DataPortalReportCreateModal from "@/components/data-portal/DataPortalReportCreateModal.vue";
import SavedReportRunModal from "@/components/chat/SavedReportRunModal.vue";
import AttachmentImageThumb from "@/components/embed/AttachmentImageThumb.vue";
import { isImageAttachment } from "@/utils/attachmentImages";
import { isDirectRenderableUrl, resolvePublicUploadsPreviewUrl } from "@/utils/workspaceFilePreview";
import { copyToClipboard } from "@/utils/clipboard";
import {
  applyStreamErrorMessage,
  type StreamErrorDetail,
} from "@/utils/streamErrorPresentation";
import { resolveGeneratedFileHref } from "@/utils/generatedFileUrl";
import {
  sanitizeStreamContent,
  stripInternalContextBlocks,
} from "@/utils/streamContentSanitize";
import {
  canSaveGoldenReportFromMessage,
  resolveSavableSqlFromMessage,
} from "@/utils/toolLogDisplay";
import {
  deriveSavedReportDescription,
  deriveSavedReportTagsInput,
  deriveSavedReportTitle,
  parseRequirementAnalysisFromMessage,
  resolveSavedReportSourceContext,
} from "@/utils/savedReportDefaults";
import {
  buildSavedReportRunParams,
  composeSavedReportExecuteMarkdown,
  detectSavedReportDateTemplate,
  extractColumnMetaFromAgentMessage,
  extractSavedReportExecuteErrorMessage,
  mergeSavedReportAnalysisIntoResult,
  todayDateString,
  todayMonthString,
} from "@/composables/chat/useSavedReportWorkflow";
import { useWorkspaceCanvas } from "@/composables/chat/useWorkspaceCanvas";
import { createChatSendGate } from "@/composables/chat/useChatSendGate";
import { useConversationRunStatus } from "@/composables/chat/useConversationRunStatus";
import { createClientRequestId } from "@/utils/clientRequestId";
import {
  USER_MESSAGE_CONTEXT_DIVIDER,
  splitUserMessageContent,
  useChatAttachments,
} from "@/composables/chat/useChatAttachments";
import { groupChatHistoryByDate } from "@/composables/chat/useChatHistoryGroups";

const route = useRoute();
const router = useRouter();
const openFullDataPortal = () => router.push({ path: "/dashboard/personal", query: { tab: "data" } });
const { showToast } = useToast();
const { quotaStatus, refreshQuota } = useTokenQuota();

// --- New Features State ---
const showSessionPreview = ref(false);
const conversationTurns = ref<any[]>([]);
const loadingTrace = ref(false);
const previewConversationId = ref<string>("");

const aggregatedHistoryList = computed(() => {
  if (!historyList.value.length) return [];
  const groups: Record<string, any[]> = {};
  const orderedKeys: string[] = [];

  // 1. Group by conversation_id
  historyList.value.forEach(item => {
    const cid = item.conversation_id || `trace_${item.trace_id}`;
    if (!groups[cid]) {
      groups[cid] = [];
      orderedKeys.push(cid);
    }
    groups[cid].push(item);
  });

    // 2. Map to representative item with turn_count
  return orderedKeys.map(key => {
    const items = groups[key];
    if (!items || items.length === 0) return null;
    // Use the latest item (or first, depending on sort) as representative
    // Assuming historyList is sorted by created_at desc?
    // Usually fetching history returns latest first.
    const representative = items[0];

    // If we have multiple items locally, that's the count (frontend aggregation).
    // If we have 1 item, it might be a pre-grouped item from backend with a count.
    const count = items.length > 1 ? items.length : (representative.turn_count || items.length);

    return {
      ...representative,
      turn_count: count
    };
  }).filter(Boolean);
});

const groupedHistoryList = computed(() => groupChatHistoryByDate(aggregatedHistoryList.value));

const formatDate = (dateStr: string) => {
  if (!dateStr) return "-";
  try {
    return new Date(dateStr).toLocaleString();
  } catch (e) { return dateStr; }
};

const openSessionPreview = async (traceIdOrItem: string | any) => {
  const traceId = typeof traceIdOrItem === 'string' ? traceIdOrItem : traceIdOrItem?.trace_id;
  const convId = typeof traceIdOrItem === 'string' ? null : traceIdOrItem?.conversation_id;

  if (!traceId && !convId) return;

  showSessionPreview.value = true;
  loadingTrace.value = true;
  conversationTurns.value = [];
  previewConversationId.value = "";

  try {
     if (convId) {
         previewConversationId.value = convId;
         await fetchSessionTurns(convId);
     } else if (traceId) {
        const res = await axios.get(`/api/v1/chat/logs/${traceId}`);
        if (res.data?.data) {
            const cid = res.data.data.history?.conversation_id;
            if (cid) {
                previewConversationId.value = cid;
                await fetchSessionTurns(cid);
            } else if (res.data.data.history) {
                // Single trace fallback
                conversationTurns.value = [{
                    ...res.data.data.history,
                    steps: res.data.data.steps || [],
                    isExpanded: true,
                    trace_id: traceId
                }];
            }
        }
     }
  } catch (e) {
    console.error("Failed to load session preview", e);
  } finally {
    loadingTrace.value = false;
  }
};

const fetchSessionTurns = async (cid: string) => {
    try {
        const historyRes = await axios.get(`/api/v1/chat/history`, {
            params: { conversation_id: cid, page_size: 100 }
        });
        if (historyRes.data?.data?.items) {
            // Sort by created_at (oldest first for chat view)
            const sorted = historyRes.data.data.items.reverse();

            // 全部默认折叠
            conversationTurns.value = sorted.map((item: any) => ({
                ...item,
                steps: [], // Lazy load steps
                loading: false,
                isExpanded: false
            }));
        }
    } catch (e) { console.error(e); }
};

const toggleTurnSteps = async (turn: any) => {
    turn.isExpanded = !turn.isExpanded;
    if (turn.isExpanded && (!turn.steps || turn.steps.length === 0)) {
        turn.loading = true;
        try {
            const res = await axios.get(`/api/v1/chat/logs/${turn.trace_id}`);
            if (res.data?.data?.steps) turn.steps = res.data.data.steps;
        } catch (e) {
            console.error("Failed to fetch steps for turn", e);
        } finally {
            turn.loading = false;
        }
    }
};

const continueChatFromTrace = () => {
    // 1. Prefer explicit conversation ID
    let targetId = previewConversationId.value;

    // 2. Fallback to extracting from turns
    if (!targetId && conversationTurns.value.length > 0) {
        const targetTurn = conversationTurns.value[conversationTurns.value.length - 1];
        if (targetTurn?.conversation_id) {
            targetId = targetTurn.conversation_id;
        }
    }

    if (targetId) {
        const previousId = conversationId.value;
        if (previousId && previousId !== targetId) {
            finalizeConversationInBackground(previousId);
        }
        conversationId.value = targetId;
        resetDebugThinkingOverrides();
        localStorage.setItem("agent_debug_conv_id", targetId);
        messages.value = [];
        loadSessionHistory(targetId);
        showSessionPreview.value = false;
    } else {
        alert("无法继续：该会话可能未正确保存或仅为单次追踪记录。");
    }
};


import { modelApi, type AIModel, type ReasoningEffort } from "../api/model";

import ConfirmModal from "@/components/ConfirmModal.vue";

interface SlashCommand {
  id?: number;
  label: string;
  command: string;
  sort_order: number;
}

const agentParams = reactive<{
  agent_id?: string | null; // null = Auto Router
  version_id?: string | null;
}>({
  agent_id: null,
  version_id: null,
});

// History State
const historyList = ref<any[]>([]);
const loadingHistory = ref(false);
const historyKeyword = ref("");
let searchTimer: any = null;

const fetchHistory = async () => {
  loadingHistory.value = true;
  try {
    const params: any = { page: 1, page_size: 50, group_by_conversation: true };
    if (agentParams.agent_id) {
      params.agent_id = agentParams.agent_id;
    }
    if (historyKeyword.value) {
      params.keyword = historyKeyword.value;
    }
    const res = await axios.get("/api/v1/chat/history", { params });
    if (res.data?.data) historyList.value = res.data.data.items || [];
  } catch (e) {
    console.error("Failed to fetch history", e);
  } finally {
    loadingHistory.value = false;
  }
};

watch(historyKeyword, () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    fetchHistory();
  }, 500);
});

watch(
  () => agentParams.agent_id,
  () => {
    fetchHistory();
  }
);

// Agents State for Dropdown
const agents = ref<any[]>([]);
const debugMode = ref<"auto" | "specific">("auto");
const isGeneralAgentMessage = (msg: Message): boolean => {
  if (msg.agentType) return msg.agentType === "GENERAL";
  const agent = agents.value.find(
    (item: any) => item.name === msg.agentName || item.id === msg.agentName,
  );
  return agent?.agent_type === "GENERAL";
};

const visibleStreamBody = (msg: Message): string => {
  return msg.role === "agent"
    ? stripInternalContextBlocks(msg.content || "")
    : (msg.content || "");
};

const showAgentDropdown = ref(false);
const agentDropdownRef = ref<HTMLElement | null>(null);

const selectedAgent = computed(() => {
  return agents.value.find((a: any) => a.id === agentParams.agent_id);
});

const handleDocumentClick = (e: MouseEvent) => {
  if (agentDropdownRef.value && !agentDropdownRef.value.contains(e.target as Node)) {
    showAgentDropdown.value = false;
  }
};
const SYSTEM_SLASH_COMMANDS = [
  { id: "sys_clear", command: "/new", label: "💬 新会话", sort_order: -40 },
  { id: "sys_history", command: "/history", label: "🕒 历史", sort_order: -39 },
  { id: DATASET_PORTAL_SYSTEM_COMMAND_ID, command: DATASET_PORTAL_SLASH_COMMAND, label: "📊 数据门户", sort_order: -35 },
  { id: KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID, command: KNOWLEDGE_PORTAL_SLASH_COMMAND, label: "📚 知识库中心", sort_order: -34.5 },
  { id: WORKSPACE_SYSTEM_COMMAND_ID, command: WORKSPACE_SLASH_COMMAND, label: "💻 工作空间", sort_order: -34 },
  { id: "sys_quota", command: "/quota", label: "📊 我的额度", sort_order: -18 },
  { id: "sys_compact", command: "/compact", label: "🧹 压缩上下文", sort_order: -17 },
  { id: "sys_settings", command: "/settings", label: "⚙️ 设置", sort_order: -15 },
];
const isKnowledgeEnabled = ref(true);
const slashCommands = ref<any[]>([...SYSTEM_SLASH_COMMANDS]);

const fetchAgents = async () => {
  try {
    const res = await axios.get("/api/portal/agents/");
    if (res.data) {
      agents.value = res.data.filter((a: any) => a.is_enabled !== false);
    }
  } catch (e) {
    console.error("Failed to fetch agents", e);
  }
};

const hideDebugLikeDislikeForHostedAgent = computed(() => {
  if (!agentParams.agent_id) return false;
  const ag = agents.value.find((a) => a.id === agentParams.agent_id);
  const t = ag?.engine_type;
  return t === "RAGFLOW" || t === "OPENCLAW";
});

const fetchSlashCommands = async () => {
  try {
    // 并行获取 RAGFlow 配置和快捷指令
    const [configRes, res] = await Promise.all([
      axios.get("/api/portal/ragflow/config").catch(e => {
        console.warn("Failed to fetch ragflow config", e);
        return null;
      }),
      axios.get("/api/portal/slash-commands/").catch(e => {
        console.warn("Failed to fetch user slash-commands", e);
        return { data: null };
      })
    ]);

    if (configRes && configRes.data?.data) {
      isKnowledgeEnabled.value = configRes.data.data.knowledge_base_enabled !== false;
    } else {
      isKnowledgeEnabled.value = true;
    }

    const sysCommands = SYSTEM_SLASH_COMMANDS.map(cmd => {
      if (cmd.id === KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID) {
        return {
          ...cmd,
          disabled: !isKnowledgeEnabled.value
        };
      }
      return cmd;
    });

    if (res.data) {
      // 获取用户命令
      const userCommands = Array.isArray(res.data) ? res.data : [];
      // 合并系统命令和用户命令，并按 sort_order 排序
      slashCommands.value = [
        ...sysCommands,
        ...userCommands
      ].sort((a, b) => (a.sort_order || 999) - (b.sort_order || 999));
    } else {
      slashCommands.value = [...sysCommands];
    }
  } catch (e) {
    console.error("Failed to fetch slash commands", e);
    const sysCommands = SYSTEM_SLASH_COMMANDS.map(cmd => {
      if (cmd.id === KNOWLEDGE_PORTAL_SYSTEM_COMMAND_ID) {
        return {
          ...cmd,
          disabled: !isKnowledgeEnabled.value
        };
      }
      return cmd;
    });
    slashCommands.value = [...sysCommands];
  }
};

const availableModels = ref<AIModel[]>([]);
const fetchModels = async () => {
  try {
    const res = await modelApi.list();
    availableModels.value = res.data.filter(
      (m) => (m.type === "llm" || m.type === "multimodal") && m.is_active
    );
  } catch (e) {
    console.error("Failed to fetch models", e);
  }
};

const messages = ref<Message[]>([]);
const displayMessages = computed(() => {
  const raw = messages.value;
  if (!raw || raw.length === 0) return [];

  const filtered: Message[] = [];
  if (raw[0]) filtered.push(raw[0]);

  for (let i = 1; i < raw.length; i++) {
    const prev = raw[i - 1];
    const curr = raw[i];
    // Filter out consecutive duplicates with same role and content
    // EXCEPT if the current message has citations or logs (which makes it unique/active)
    const hasMetadata = (curr?.citations && curr.citations.length > 0) || (curr?.logs && curr.logs.length > 0);
    if (prev && curr && curr.role === prev.role && curr.content === prev.content && !curr.isThinking && !hasMetadata) {
      continue;
    }
    if (curr) filtered.push(curr);
  }
  return filtered;
});

function getSkillFlowBadgesForMessage(msg: Message, allMessages: Message[]): SkillFlowBadge[] {
  if (msg.role !== 'agent') return [];
  const idx = allMessages.findIndex(m => m.id === msg.id);
  if (idx <= 0) return [];
  let files: ChatFile[] = [];
  for (let i = idx - 1; i >= 0; i--) {
    const prev = allMessages[i];
    if (!prev) continue;
    if (prev.role === 'user') {
      files = prev.files || [];
      break;
    }
  }
  return buildSkillFlowBadges(files, msg.logs || []);
}
const conversationId = ref("");
const metadataMountableDatasets = ref<Array<{ id: string; name?: string; description?: string; dataset_name?: string }>>([]);
const sessionMountedMetadataDatasetIds = ref<string[]>([]);

const debugAuthHeaders = (): Record<string, string> | undefined => {
  const key = localStorage.getItem("api_key");
  return key ? { "X-API-Key": key } : undefined;
};

const {
  remoteRunActive,
  refresh: refreshRemoteRunStatus,
  stopPolling: stopRemoteRunPolling,
  markOutputCompleted,
} = useConversationRunStatus(async (cid) => {
  const response = await axios.get(
    `/api/v1/chat/conversation/${encodeURIComponent(cid)}/run-status`,
    { headers: debugAuthHeaders() },
  );
  return response.data?.data || {};
});

const refreshCurrentRunStatus = () => (
  conversationId.value
    ? refreshRemoteRunStatus(conversationId.value)
    : Promise.resolve(false)
);

watch(conversationId, () => {
  void refreshCurrentRunStatus();
}, { immediate: true });

const finalizeConversationInBackground = (cid: string) => {
  void finalizeConversation(cid, debugAuthHeaders());
};

const generateNewConversation = (isManual = false) => {
  const previousId = conversationId.value;
  if (previousId) {
    finalizeConversationInBackground(previousId);
  }
  conversationId.value = createConversationId();
  resetDebugThinkingOverrides();
  debugConfig.enableGrounding = false;
  localStorage.setItem("agent_debug_conv_id", conversationId.value);
  if (isManual) {
    messages.value = [];
    loadGreeting();
  }
};

const loadSessionHistory = async (id: string) => {
  try {
    const res = await axios.get(`/api/v1/chat/conversation/${id}`, {
      headers: { 'X-API-Key': localStorage.getItem('api_key') }
    });
    if (res.data?.data && Array.isArray(res.data.data.messages)) {
      // Deduplicate: Filter out consecutive messages with same role and content
      const rawMessages = res.data.data.messages;
      const validMessages: any[] = [];

      if (rawMessages.length > 0) {
        validMessages.push(rawMessages[0]);
        for (let i = 1; i < rawMessages.length; i++) {
          const prev = rawMessages[i-1];
          const curr = rawMessages[i];
          // Simple deduplication check
          if (
            curr.role === "assistant" &&
            prev.role === "assistant" &&
            curr.trace_id &&
            curr.trace_id === prev.trace_id &&
            curr.content === prev.content &&
            curr.reasoning_content === prev.reasoning_content
          ) {
            continue; // Skip duplicate
          }
          validMessages.push(curr);
        }
      }

      const historyMsg: Message[] = validMessages.map(
        (m: any, idx: number) => ({
          id: Date.now() + idx,
          trace_id: m.trace_id,
          role: m.role === "assistant" ? "agent" : m.role,
          content: m.content as string,
          reasoningContent: m.reasoning_content || undefined,
          processTimeline: hydrateHistoryProcessTimeline(m.process_timeline, m.reasoning_content),
          logs: [],
          isThinking: false,
          isHistory: true, // Mark as history
          feedback: m.feedback,
          agentName: m.agent_name || undefined,
          agentDisplayName: m.agent_display_name || (String(m.agent_name || '').startsWith('sys_') ? '系统助手' : undefined),
          agentType: m.agent_type || undefined,
        })
      );
      if (historyMsg.length > 0) {
        // Add Separator with Timestamp
        const lastMsg = res.data.data.messages[res.data.data.messages.length - 1];
        let timeStr = "";
        if (lastMsg && lastMsg.timestamp) {
             try {
                const date = new Date(lastMsg.timestamp);
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                const hours = String(date.getHours()).padStart(2, '0');
                const minutes = String(date.getMinutes()).padStart(2, '0');
                timeStr = `${year}-${month}-${day} ${hours}:${minutes}`;
            } catch (e) { }
        }

        historyMsg.push({
          id: Date.now() + historyMsg.length,
          role: "system",
          content: "以上是历史会话，可以重置会话清除",
          timestamp: timeStr
        });

        messages.value = historyMsg;
        nextTick(scrollToBottom);
        return;
      }
    }
    // If empty or fail, load greeting
    loadGreeting();
  } catch (e) {
    console.warn("Failed to load session history", e);
    loadGreeting();
  }
};

const getAgentDisplayName = (msg: Message) => {
  if (msg.agentDisplayName) return msg.agentDisplayName;
  if (msg.agentName) {
    const found = agents.value.find((a: any) => a.name === msg.agentName);
    return found ? found.display_name : msg.agentName;
  }
  return '';
};

const currentUser = ref<any>(null);

const fetchCurrentUser = async () => {
  try {
    const res = await axios.get("/api/portal/auth/me");
    if (res.data?.data) {
      currentUser.value = res.data.data;
    }
  } catch (e) {
    console.error("Failed to fetch user info", e);
  }
};

onMounted(() => {
  fetchCurrentUser();
  // Check for traceId in URL
  const queryTraceId = route.query.traceId as string;
  if (queryTraceId) {
    openFullLogs(queryTraceId);
  }

  // Check for Agent Debug Context (agent_id, version_id)
  const qAgentId = route.query.agent_id as string;
  const qVersionId = route.query.version_id as string;
  const qSampleQuestion = route.query.sample_question as string;

  fetchAgents();
  fetchSlashCommands(); // Fetch dynamic commands
  fetchModels();
  fetchModels();
  fetchHistory(); // Auto-load history on mount

  if (qAgentId) {
    agentParams.agent_id = qAgentId;
    debugMode.value = "specific";
  } else {
    // Default to Auto Router mode (agent_id = null)
    agentParams.agent_id = null;
    debugMode.value = "auto";
  }

  // Initialize or Retrieve Conversation ID
  const savedId = localStorage.getItem("agent_debug_conv_id");
  if (savedId) {
    conversationId.value = savedId;
    loadSessionHistory(savedId);
  } else {
    // If no session, generate key
    generateNewConversation();
  }

  if (qVersionId) agentParams.version_id = qVersionId;
  if (qSampleQuestion) userInput.value = qSampleQuestion;

  if (qVersionId) {
    // Add a system update message to inform user they are in preview mode
    messages.value.push({
      id: Date.now(),
      role: "agent",
      content: `<div class="bg-amber-50 border-l-4 border-amber-400 p-4 rounded-r-lg mb-4">
        <div class="flex">
          <div class="flex-shrink-0">
            <svg class="h-5 w-5 text-amber-400" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd" />
            </svg>
          </div>
          <div class="ml-3">
            <h3 class="text-sm leading-5 font-medium text-amber-800">
              调试预览模式 (Debug Preview Mode)
            </h3>
            <div class="mt-2 text-sm leading-5 text-amber-700">
              <p>当前会话已绑定到特定版本，修改将实时生效。</p>
              <ul class="list-disc pl-5 space-y-1 mt-1">
                <li><strong>Agent ID:</strong> <code class="bg-amber-100 px-1 py-0.5 rounded text-xs font-mono">${
                  qAgentId || "default"
                }</code></li>
                <li><strong>Version ID:</strong> <code class="bg-amber-100 px-1 py-0.5 rounded text-xs font-mono">${qVersionId}</code></li>
              </ul>
            </div>
          </div>
        </div>
      </div>`,
    });
  }
});

// 固化报表暂存状态
const showSaveReportModal = ref(false);
const isEditingReport = ref(false);
const editingReportId = ref<string | null>(null);
const showReportRunModal = ref(false);
const pendingSavedReport = ref<SavedReportPayload | null>(null);
const isPreviewingSavedReport = ref(false);
const reportRunPreview = ref<any | null>(null);
const reportRunForm = ref({
  dateRange: 'month_start_to_today',
  startDate: '',
  endDate: '',
  monthRange: 'last_6_completed_months',
  startMonth: '',
  endMonth: '',
  customParams: {} as Record<string, any>,
});
const saveReportForm = ref({
  id: null as number | string | null,
  title: '',
  description: '',
  sql_content: '',
  dataset_id: null as number | null,
  dataset_name: '',
  data_source: 'default_clickhouse',
  original_query: '',
  mode: 'static_sql',
  sql_template: '',
  params_schema: [] as any[],
  default_params: {} as Record<string, any>,
  column_meta: null as Record<string, any> | null,
  analysis_mode: 'auto',
  tags_input: '',
});

const openSaveReportModal = (sql: string, agentMessage: any) => {
  isEditingReport.value = false;
  editingReportId.value = null;

  let originalQuery = '';
  if (agentMessage && messages.value) {
    const idx = messages.value.findIndex((m: any) => m.id === agentMessage.id);
    if (idx > 0) {
      for (let i = idx - 1; i >= 0; i--) {
        const previousMessage = messages.value[i];
        if (previousMessage?.role === 'user') {
          const content = previousMessage.content || '';
          if (content.includes('---')) {
            originalQuery = (content.split('---')[0] || '').trim();
          } else {
            originalQuery = content.trim();
          }
          break;
        }
      }
    }
  }

  let cleanSql = sql || '';
  if (cleanSql.includes('[Executed SQL]:')) {
    cleanSql = cleanSql.replace(/\[Executed\s+SQL\]:\s*/i, '').trim();
  }

  if (!originalQuery && cleanSql) {
    const fromMatch = cleanSql.match(/from\s+([a-zA-Z0-9_]+)/i);
    if (fromMatch && fromMatch[1]) {
      originalQuery = `${fromMatch[1]}数据查询`;
    }
  }

  const detectedTemplate = detectSavedReportDateTemplate(cleanSql);
  const requirementIntent = parseRequirementAnalysisFromMessage(agentMessage);
  const sourceContext = resolveSavedReportSourceContext(agentMessage);

  saveReportForm.value = {
    id: null,
    title: deriveSavedReportTitle(requirementIntent, originalQuery),
    description: deriveSavedReportDescription(requirementIntent, originalQuery),
    sql_content: cleanSql,
    dataset_id: sourceContext.dataset_id,
    dataset_name: sourceContext.dataset_name,
    data_source: sourceContext.data_source,
    original_query: originalQuery,
    mode: detectedTemplate ? 'param_sql' : 'static_sql',
    sql_template: detectedTemplate?.sql_template || '',
    params_schema: detectedTemplate?.params_schema || [],
    default_params: detectedTemplate?.default_params || {},
    column_meta: extractColumnMetaFromAgentMessage(agentMessage),
    analysis_mode: 'auto',
    tags_input: deriveSavedReportTagsInput(requirementIntent, originalQuery),
  };
  showSaveReportModal.value = true;
};

const handleSaveReportFromMessage = (msg: any) => {
  const sql = resolveSavableSqlFromMessage(msg);
  if (sql) openSaveReportModal(sql, msg);
};

const openEditReportModal = (report: any) => {
  isEditingReport.value = true;
  editingReportId.value = report.id;
  saveReportForm.value = {
    id: report.id,
    title: report.title || '',
    description: report.description || '',
    sql_content: report.sql_content || '',
    dataset_id: report.dataset_id ?? null,
    dataset_name: report.dataset_name || '',
    data_source: report.data_source || 'default_clickhouse',
    original_query: report.original_query || '',
    mode: report.mode || 'static_sql',
    sql_template: report.sql_template || '',
    params_schema: report.params_schema || [],
    default_params: report.default_params || {},
    column_meta: report.column_meta || null,
    analysis_mode: 'auto',
    tags_input: Array.isArray(report.tags) ? report.tags.join(', ') : '',
  };
  showSaveReportModal.value = true;
};

const closeSavedReportEditor = () => {
  showSaveReportModal.value = false;
  isEditingReport.value = false;
  editingReportId.value = null;
};

const handleSavedReportEditorCreated = () => {
  closeSavedReportEditor();
};

const savedReportNeedsRunOptions = (report: SavedReportPayload) => {
  return report.mode === 'param_sql' && Array.isArray(report.params_schema) && report.params_schema.length > 0;
};

const savedReportUsesMonthRange = (report?: SavedReportPayload | null) => {
  return Boolean(report?.params_schema?.some((item: any) => item?.type === 'month_range' || item?.name === 'month_range'));
};

const savedReportUsesDateRange = (report?: SavedReportPayload | null) => {
  return Boolean(report?.params_schema?.some((item: any) => item?.type === 'date_range' || item?.name === 'date_range'));
};

let suppressSavedReportRunPreviewWatch = false;

const prepareSavedReportRunForm = (report: SavedReportPayload) => {
  suppressSavedReportRunPreviewWatch = true;
  const defaults = report.default_params || {};
  const customParams: Record<string, any> = {};
  for (const item of report.params_schema || []) {
    const type = String(item?.type || '');
    const name = String(item?.name || '');
    if (!name || type === 'date_range' || type === 'month_range' || name === 'date_range' || name === 'month_range') continue;
    customParams[name] = defaults[name] ?? item.default ?? (type === 'select' ? item.options?.[0] ?? '' : '');
  }
  reportRunForm.value = {
    dateRange: String(defaults.date_range || 'month_start_to_today'),
    startDate: String(defaults.start_date || todayDateString()),
    endDate: String(defaults.end_date || todayDateString()),
    monthRange: String(defaults.month_range || 'last_6_completed_months'),
    startMonth: String(defaults.start_month || todayMonthString()),
    endMonth: String(defaults.end_month || todayMonthString()),
    customParams,
  };
  nextTick(() => {
    suppressSavedReportRunPreviewWatch = false;
  });
};

let savedReportPreviewSeq = 0;
let savedReportPreviewAbort: AbortController | null = null;

const previewSavedReportRun = async () => {
  const report = pendingSavedReport.value;
  if (!report) return;
  const seq = ++savedReportPreviewSeq;
  savedReportPreviewAbort?.abort();
  const controller = new AbortController();
  savedReportPreviewAbort = controller;
  isPreviewingSavedReport.value = true;
  reportRunPreview.value = null;
  try {
    const res = await axios.post(`/api/portal/saved-reports/${report.id}/preview`, {
      params: buildSavedReportRunParams(pendingSavedReport.value, reportRunForm.value),
      analysis_mode: 'auto',
    }, { signal: controller.signal });
    if (seq !== savedReportPreviewSeq) return;
    reportRunPreview.value = res.data?.data || null;
  } catch (error: any) {
    if (controller.signal.aborted || seq !== savedReportPreviewSeq) return;
    console.error("Failed to preview saved report:", error);
    reportRunPreview.value = {
      rendered_sql: report.sql_content,
      permission_status: 'unknown',
      permission_message: extractSavedReportExecuteErrorMessage(error),
      can_run: true,
    };
  } finally {
    if (seq === savedReportPreviewSeq) {
      isPreviewingSavedReport.value = false;
    }
  }
};

let savedReportPreviewTimer: ReturnType<typeof setTimeout> | null = null;

const scheduleSavedReportPreview = (immediate = false) => {
  if (!showReportRunModal.value || !pendingSavedReport.value) return;
  if (!immediate && suppressSavedReportRunPreviewWatch) return;
  if (savedReportPreviewTimer) clearTimeout(savedReportPreviewTimer);
  if (immediate) {
    void previewSavedReportRun();
    return;
  }
  savedReportPreviewTimer = setTimeout(() => previewSavedReportRun(), 250);
};

watch(
  () => [
    reportRunForm.value.dateRange,
    reportRunForm.value.startDate,
    reportRunForm.value.endDate,
    reportRunForm.value.monthRange,
    reportRunForm.value.startMonth,
    reportRunForm.value.endMonth,
    JSON.stringify(reportRunForm.value.customParams),
  ],
  () => scheduleSavedReportPreview(false),
  { flush: 'post' }
);

onUnmounted(() => {
  savedReportPreviewAbort?.abort();
  if (savedReportPreviewTimer) clearTimeout(savedReportPreviewTimer);
});

const handleExecuteSavedReport = async (report: SavedReportPayload) => {
  if (!savedReportNeedsRunOptions(report)) {
    pendingSavedReport.value = report;
    reportRunPreview.value = null;
    await executeSavedReportWithOptions(report);
    return;
  }
  pendingSavedReport.value = report;
  reportRunPreview.value = null;
  showReportRunModal.value = true;
  prepareSavedReportRunForm(report);
  scheduleSavedReportPreview(true);
};

const executeSavedReportWithOptions = async (reportArg?: SavedReportPayload | null) => {
  const report = reportArg || pendingSavedReport.value;
  if (!report) return;
  if (isProcessing.value) return;
  if (savedReportNeedsRunOptions(report) && (isPreviewingSavedReport.value || !reportRunPreview.value)) {
    showToast("请等待运行预览完成后再执行。", "error");
    return;
  }
  if (reportRunPreview.value?.can_run === false) {
    showToast("暂无该报表所需数据权限，无法运行。", "error");
    return;
  }

  showReportRunModal.value = false;

  if (showPortalDrawer.value && !portalKeepOpenOnQuestion.value) {
    closePortalDrawer();
  }

  // 保证有会话 ID，否则 Redis last_data_result 无法写入，后续「可视化分析」会丢上下文
  if (!conversationId.value) {
    generateNewConversation();
  }

  isProcessing.value = true;

  messages.value.push({
    id: Date.now(),
    role: "user",
    content: `📌 执行固化 SQL 报表: ${report.title}`,
    timestamp: new Date().toISOString(),
  });

  const agentMsg = ref<any>({
    id: Date.now() + 1,
    role: "agent",
    agentName: "chat-bi",
    agentDisplayName: "数据智能助手",
    isSavedReportResult: true,
    content: "",
    isThinking: true,
    thinkingText: "正在执行固化报表，请稍候...",
    logs: [],
    thoughtStartTime: Date.now(),
    thoughtDuration: "0.0",
    isThoughtExpanded: false,
    isCitationsExpanded: false,
    timestamp: new Date().toISOString(),
  });
  messages.value.push(agentMsg.value);
  await nextTick();
  scrollToBottom(true);

  let execResult: any = null;
  let resultMarkdown = "";

  try {
    const res = await axios.post(`/api/portal/saved-reports/${report.id}/execute`, {
      params: buildSavedReportRunParams(pendingSavedReport.value, reportRunForm.value),
      analysis_mode: 'auto',
      defer_analysis: true,
    }, {
      params: { conversation_id: conversationId.value },
      timeout: 60000,
    });

    agentMsg.value.isThinking = false;
    agentMsg.value.thinkingText = "";

    let detailsText = "";

    if (res.data && res.data.data !== undefined) {
      execResult = {
        ...res.data.data,
        analysis_status: res.data.data?.analysis_status || 'deferred',
      };
      resultMarkdown = composeSavedReportExecuteMarkdown(report.title, execResult);
      detailsText = `${report.sql_content}\n--- 结果 ---\n${typeof execResult === 'object' ? JSON.stringify(execResult, null, 2) : String(execResult)}`;
      agentMsg.value.permissionNotice = execResult?.permission_notice;
      if (execResult?.chatbi_insight?.actions?.length) {
        agentMsg.value.chatbiInsight = execResult.chatbi_insight;
      }
    } else {
      resultMarkdown = composeSavedReportExecuteMarkdown(report.title, null);
      detailsText = `${report.sql_content}\n--- 结果 ---\n无`;
    }

    agentMsg.value.content = resultMarkdown;

    agentMsg.value.logs = [
      {
        id: `log_${Date.now()}`,
        name: "execute_sql_query",
        title: "工具完成: execute_sql_query",
        category: "sql",
        status: "success",
        details: detailsText,
      }
    ];

    if (execResult) {
      try {
        agentMsg.value.isThinking = true;
        agentMsg.value.thinkingText = "正在生成业务解读…";
        const analyzeRes = await axios.post(`/api/portal/saved-reports/${report.id}/analyze`, {
          conversation_id: conversationId.value,
          run_id: execResult.run_id,
        }, {
          params: { conversation_id: conversationId.value },
          timeout: 120000,
        });
        const merged = mergeSavedReportAnalysisIntoResult(execResult, analyzeRes.data?.data || {});
        agentMsg.value.content = composeSavedReportExecuteMarkdown(report.title, merged);
      } catch (analyzeError) {
        console.warn("Failed to analyze saved report:", analyzeError);
        agentMsg.value.content = composeSavedReportExecuteMarkdown(report.title, {
          ...execResult,
          analysis_status: 'error',
        });
      } finally {
        agentMsg.value.isThinking = false;
        agentMsg.value.thinkingText = "";
      }
    }
  } catch (error: any) {
    console.error("Failed to execute saved report:", error);
    agentMsg.value.isThinking = false;
    agentMsg.value.thinkingText = "";

    const errorMsg = extractSavedReportExecuteErrorMessage(error);

    agentMsg.value.content = `### ❌ 报表执行失败\n\n在直连执行 SQL 报表时遇到错误：\n\n\`\`\`\n${errorMsg}\n\`\`\``;
    agentMsg.value.logs = [
      {
        id: `log_${Date.now()}`,
        name: "execute_sql_query",
        title: "工具完成: execute_sql_query",
        category: "sql",
        status: "error",
        details: `${report.sql_content}\n--- 错误 ---\n${errorMsg}`,
      }
    ];
  } finally {
    agentMsg.value.isThinking = false;
    agentMsg.value.thinkingText = "";
    isProcessing.value = false;
    await nextTick();
    scrollToBottom(true);
  }
};

const copyContent = async (content: string, event?: Event) => {
  if (!content) {
    showToast("内容为空", "warning");
    return;
  }
  const btn = event?.currentTarget as HTMLElement | undefined;
  const ok = await copyToClipboard(content);
  if (!ok) {
    console.error("Failed to copy");
    showToast("复制失败，请手动复制", "error");
    return;
  }

  showToast("已复制到剪贴板", "success");

  // 底部带文字的按钮：短暂切换为「已复制」
  if (btn && btn.querySelector("span")) {
    const originalHtml = btn.innerHTML;
    btn.innerHTML = `<svg class="w-3 h-3 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg><span>已复制</span>`;
    btn.classList.add("text-green-600", "bg-green-50");
    btn.classList.remove("text-gray-400", "hover:text-gray-600");

    setTimeout(() => {
      btn.innerHTML = originalHtml;
      btn.classList.remove("text-green-600", "bg-green-50");
      btn.classList.add("text-gray-400", "hover:text-gray-600");
    }, 2000);
  }
};

const exportData = async (traceId: string, format = 'xlsx') => {
  if (!traceId) return;
  try {
    const response = await axios.get(`/api/v1/chat/export/data/${traceId}`, {
      params: { format }
    });

    const payload = response.data as {
      download_url?: string;
    };
    if (!payload || !payload.download_url) {
      showToast("导出失败：未找到可导出数据", "error");
      return;
    }

    const href = resolveGeneratedFileHref(payload.download_url);
    const link = document.createElement('a');
    link.href = href;
    const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    link.setAttribute('download', `debug_export_${dateStr}_${traceId.slice(0, 8)}.${format}`);
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    link.remove();
    showToast("数据导出成功", "success");
  } catch (e) {
    console.error("Export failed", e);
    showToast("导出失败：未找到可导出数据", "error");
  }
};

// ... existing code ...

interface LogEntry {
  id: string | number;
  parent_id?: string | number;
  title: string;
  details: string;
  status: "pending" | "success" | "error" | "warning";
  error_reason?: string;
  isExpanded: boolean;
  isDebug?: boolean;
  isRouter?: boolean;
  category?: 'router' | 'sql' | 'knowledge' | 'tool' | 'tool_resolution' | 'intent' | 'permission' | 'external' | 'model' | 'agent' | 'context' | 'default';
  tool_name?: string;
  file_metadata?: import("@/utils/processTimeline").FileToolMetadata;
  resolution_status?: 'disabled' | 'missing' | 'filtered';
  execution_time_ms?: number | null;
  started_at?: number | null;
  model?: string;
  temperature?: number;
  subagent?: SubagentTraceMeta;
  rowFilterApplied?: boolean;
}

interface SavedReportPayload {
  id: string;
  title: string;
  sql_content: string;
  mode?: string;
  sql_template?: string;
  params_schema?: Array<{ type?: string; name?: string; label?: string; default?: any; required?: boolean; options?: any[] }>;
  default_params?: Record<string, any>;
  analysis_mode?: string;
  description?: string;
  tags?: string[];
}

interface PermissionNotice {
  row_filter_applied?: boolean;
  dataset_name?: string;
  rule_count?: number;
  message?: string;
}

// ... inside script ...

interface SkillMeta {
  id: string;
  name: string;
  description?: string;
}

interface ChatFile {
  type?: string;
  url: string;
  filename: string;
  size: number;
  ext: string;
  skillMeta?: SkillMeta;
  memoryMeta?: any[];
}

interface ChatSendSnapshot {
  content: string;
  files: ChatFile[];
  clientRequestId: string;
  groundingAction?: Record<string, unknown>;
}

interface ChatSendOverrides {
  content?: string;
  files?: ChatFile[];
  clientRequestId?: string;
  groundingAction?: Record<string, unknown>;
}

interface DatasetCapabilityQuestion {
  label: string;
  query: string;
  type?: string;
  click_count?: number;
  last_clicked_at?: string;
}

interface DatasetNavigationPayload {
  dataset_count?: number;
  dataset_menu_hash?: string;
  generated_at?: string;
  groups?: Array<{
    id?: string;
    title: string;
    summary: string;
    tags?: string[];
    questions?: DatasetCapabilityQuestion[];
    related_data?: Array<{
      dataset?: string;
      display_name?: string;
      tables?: string[];
      table_descriptions?: Array<{ name: string; description?: string }>;
      table_physical_names?: Record<string, string>;
    }>;
    followups?: DatasetCapabilityQuestion[];
    updated_at?: string;
    enabled?: boolean;
  }>;
  markdown?: string;
  is_fallback?: boolean;
  has_datasets?: boolean;
  from_cache?: boolean;
  llm_generation_failed?: boolean;
  llm_error_message?: string | null;
  _failed_at?: string;
}

interface Message {
  id: number;
  role: "user" | "agent" | "system";
  content: string;
  errorDetail?: StreamErrorDetail;
  reasoningContent?: string;
  isReasoningExpanded?: boolean;
  files?: ChatFile[];
  rawContent?: string; // Store original markdown for copying
  logs?: LogEntry[];
  isThinking?: boolean;
  processNarration?: string;
  processNarrationPending?: string;
  processTimeline?: import("@/utils/processTimeline").ProcessTimelineItem[];
  isProcessNarrationExpanded?: boolean;
  timestamp?: string;
  intent?: string;
  rawPrompt?: any; // Store raw prompt data
  trace_id?: string; // Associated Trace ID for full logs
  citations?: any[]; // Knowledge base references
  isCitationsExpanded?: boolean; // Collapsible toggle
  agentName?: string; // Which agent responded (ID or Name)
  agentDisplayName?: string; // Human readable display name
  agentType?: string;
  isSavedReportResult?: boolean;
  isThoughtExpanded?: boolean; // Toggle for the logs block
  thoughtStartTime?: number; // Timestamp when thinking started
  thoughtDuration?: string; // Duration in seconds (formatted)
  thinkingText?: string; // Dynamic thinking text
  isGreeting?: boolean; // Is this the initial greeting message?
  isHistory?: boolean; // Is this a history separator or message?
  feedback?: "up" | "down" | null;
  pendingPermission?: PendingToolPermission;
  pendingExternalExecution?: PendingExternalExecution;
  toolResultData?: Record<string, Array<{ block_id?: string; media_type?: string; data?: unknown; url?: string | null }>>;
  datasetNavigation?: DatasetNavigationPayload;
  permissionNotice?: PermissionNotice;
  chatbiInsight?: ChatBIInsightMeta;
  chatbiMetadataGuide?: ChatBIMetadataGuidePayload;
  agentHandoff?: AgentHandoffNoticeData;
  groundingBlocked?: GroundingBlockedPayload;
  businessConfirmation?: BusinessConfirmationState;
  userQuestion?: UserQuestionState;
  prompt_tokens?: number;
  completion_tokens?: number;
}

const isAgentTimelineMessage = (msg: Message): boolean => {
  const role = (msg as unknown as { role?: string }).role;
  return role === "agent" || role === "assistant";
};

const isChatContextMessage = (message: Message): boolean => (
  !message.isThinking &&
  (message.role === "user" || message.role === "agent") &&
  Boolean(message.content || message.files?.length)
);

// --- Debug Config State ---
const showHistorySidebar = ref(false);
const showConfigPanel = ref(true);
const showLogicFlowModal = ref(false);
const isConfigPanelFloating = ref(false);
const debugConfig = reactive({
  model: "", // Empty means default
  thinkingEnableOverride: null as boolean | null,
  reasoningEffortOverride: null as ReasoningEffort | null,
  approvalMode: "ask" as "ask" | "allow" | "deny",
  temperature: 0.0,
  dryRun: false, // SQL Review Mode
  returnRawPrompt: true, // Always verify context
  enableMultiAgent: true, // Multi-agent collaboration
  enableGrounding: false, // Session grounding audit (opt-in)
  showShortcuts: true, // Show slash commands
  systemPromptOverride: "", // Prompt Engineering
  injectedContext: [] as { key: string; value: string }[], // Manual Context Injection
});

const handleDebugModelSelection = (model: string) => {
  debugConfig.model = model;
  debugConfig.thinkingEnableOverride = null;
  debugConfig.reasoningEffortOverride = null;
};

const { contextUsage, refreshContextUsage } = useContextUsage();
const {
  contextCompactions,
  contextCompactionCount,
  contextCompactionsLoading,
  contextCompactionsError,
  contextCompactionActionLoading,
  manuallyCompactContext,
  refreshContextCompactions,
} = useContextCompactions();
const refreshDebugContextUsage = () => refreshContextUsage({
  conversationId: conversationId.value,
  modelId: debugConfig.model || undefined,
  headers: debugAuthHeaders(),
});
const refreshDebugContextCompactions = (force = false) => refreshContextCompactions({
  conversationId: conversationId.value,
  headers: debugAuthHeaders(),
}, force);
const manualCompactDebugContext = async (retainRatio: 0.25 | 0.5 | 0.75 = 0.5, mode: "fast" | "smart" = "fast") => {
  try {
    const result = await manuallyCompactContext({ conversationId: conversationId.value, headers: debugAuthHeaders(), retainRatio, mode });
    await refreshContextUsage({ conversationId: conversationId.value, modelId: debugConfig.model || undefined, headers: debugAuthHeaders() });
    showToast(result?.compacted ? `上下文已压缩，预计节省 ${Number(result.saved_percent || 0)}%` : "当前没有可压缩的历史内容", result?.compacted ? "success" : "info");
  } catch {
    showToast("上下文压缩失败，请稍后重试", "error");
  }
};

watch(
  [conversationId, () => debugConfig.model],
  () => void refreshDebugContextUsage(),
  { immediate: true },
);

watch(
  [conversationId, () => debugConfig.model],
  () => void refreshDebugContextCompactions(true),
  { immediate: true },
);

const resetDebugThinkingOverrides = () => {
  debugConfig.thinkingEnableOverride = null;
  debugConfig.reasoningEffortOverride = null;
};



const loadingConfig = ref(false);
const loadCurrentPrompt = async () => {
  if (!agentParams.agent_id) {
    alert("请先选择一个特定的智能体");
    return;
  }

  loadingConfig.value = true;
  try {
    const res = await axios.get(
      `/api/portal/agents/${agentParams.agent_id}/active-config`
    );
    if (res.data && res.data.system_prompt) {
      debugConfig.systemPromptOverride = res.data.system_prompt;
      // Optional: also load model and temperature
      if (res.data.model_name) debugConfig.model = res.data.model_name;
      if (res.data.temperature !== undefined)
        debugConfig.temperature = res.data.temperature;
      resetDebugThinkingOverrides();
    }
  } catch (e) {
    console.error("Failed to load agent config", e);
    alert("加载配置失败，请检查该智能体是否已发布版本");
  } finally {
    loadingConfig.value = false;
  }
};

const showRawPromptModal = ref(false);
const showFullLogViewer = ref(false);
const selectedRawPrompt = ref<any>(null);
const activeTraceId = ref("");

// --- Agent Context State ---
const agentContext = ref<Record<string, any>>({});
const ragRetrievalMeta = ref<Record<string, any> | null>(null);

const clearContext = (key?: string) => {
  if (key) {
    delete agentContext.value[key];
  } else {
    agentContext.value = {};
  }
};

const loadGreeting = async () => {
  try {
    // Show a temporary placeholder while loading
    messages.value = [
      {
        id: Date.now(),
        role: "agent",
        content: "", // Show empty content with thinking indicator instead of text
        isThinking: true,
      },
    ];

    const res = await axios.get("/api/v1/chat/greeting");
    if (res.data?.data && res.data.data.greeting) {
      messages.value = [
        {
          id: Date.now(),
          role: "agent",
          content: res.data.data.greeting,
          isGreeting: true,
        },
      ];
    }
  } catch (e) {
    messages.value = [
      {
        id: Date.now(),
        role: "agent",
        content: "您好！我是你的智能体助手，期待为您服务。",
        isGreeting: true,
      },
    ];
  }
};

const clearHistory = () => {
  generateNewConversation(true);
  agentContext.value = {};
  ragRetrievalMeta.value = null;
  activeTraceId.value = "";
  showFullLogViewer.value = false;
};

const openFullLogs = (traceId: string) => {
  activeTraceId.value = traceId;
  showFullLogViewer.value = true;
};

const openRawPrompt = (msg: Message) => {
  if (msg.rawPrompt) {
    selectedRawPrompt.value = msg.rawPrompt;
    showRawPromptModal.value = true;
  }
};

const showCommandManager = ref(false);
const editingCommand = ref<SlashCommand>({
  label: "",
  command: "",
  sort_order: 0,
});
const isEditingCmd = ref(false);
const showDeleteConfirm = ref(false);
const commandToDelete = ref<number | null>(null);

const openCommandManager = () => {
  showCommandManager.value = true;
  resetCommandForm();
};

const resetCommandForm = () => {
  editingCommand.value = { label: "", command: "", sort_order: 0 };
  isEditingCmd.value = false;
};

const editCommand = (cmd: any) => {
  isEditingCmd.value = true;
  editingCommand.value = { ...cmd };
};

const confirmDeleteCommand = (cmdId: number) => {
  commandToDelete.value = cmdId;
  showDeleteConfirm.value = true;
};

const performDeleteCommand = async () => {
  if (commandToDelete.value === null) return;
  try {
    await axios.delete(`/api/portal/slash-commands/${commandToDelete.value}`);
    await fetchSlashCommands();
    showDeleteConfirm.value = false;
    commandToDelete.value = null;
  } catch (e) {
    console.error("Failed to delete command", e);
    alert("删除失败");
  }
};

const saveCommand = async () => {
  if (!editingCommand.value.label || !editingCommand.value.command) {
    alert("请填写完整信息");
    return;
  }

  try {
    if (isEditingCmd.value && editingCommand.value.id) {
      // Update
      await axios.put(
        `/api/portal/slash-commands/${editingCommand.value.id}`,
        editingCommand.value
      );
    } else {
      // Create
      await axios.post("/api/portal/slash-commands/", editingCommand.value);
    }
    await fetchSlashCommands();
    resetCommandForm();
  } catch (e) {
    console.error("Failed to save command", e);
    alert("保存失败");
  }
};

const closeModals = () => {
  // Do not close config panel
  showCommandManager.value = false;
  showLogicFlowModal.value = false;
  showRawPromptModal.value = false;
  selectedRawPrompt.value = null;
};

const handleImageUpload = () => {
  alert("多模态图片上传功能开发中...");
};

// --- Export Chat ---
const exportChat = () => {
  if (messages.value.length === 0) {
    alert("暂无对话记录");
    return;
  }

  const thread = messages.value.map(m => {
    return `### ${m.role === 'user' ? 'User' : m.agentName || 'Agent'}\n\n${m.content}\n`;
  }).join("\n---\n\n");

  const blob = new Blob([thread], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `chat_export_${new Date().toISOString().slice(0,19).replace('T','_').replace(/:/g,'-')}.md`;
  a.click();
  URL.revokeObjectURL(url);
};

// --- Drag & Drop Sorting ---
const draggedItemIndex = ref<number | null>(null);

const onDragStart = (event: DragEvent, index: number) => {
  draggedItemIndex.value = index;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.dropEffect = "move";
  }
};

const onDrop = async (_event: DragEvent, targetIndex: number) => {
  if (draggedItemIndex.value === null || draggedItemIndex.value === targetIndex)
    return;

  // Reorder local array
  const item = slashCommands.value.splice(draggedItemIndex.value, 1)[0];
  slashCommands.value.splice(targetIndex, 0, item);

  draggedItemIndex.value = null;

  // Update sort_order and save
  await updateSortOrders();
};

const updateSortOrders = async () => {
  const updates = slashCommands.value.map((cmd, index) => ({
    id: cmd.id,
    label: cmd.label,
    command: cmd.command,
    sort_order: (index + 1) * 10,
  }));

  // Optimistic update
  slashCommands.value.forEach((cmd, index) => {
    cmd.sort_order = (index + 1) * 10;
  });

  try {
    // Parallel update requests
    await Promise.all(
      updates.map((u) => axios.put(`/api/portal/slash-commands/${u.id}`, u))
    );
  } catch (e) {
    console.error("Failed to update sort order", e);
    // Revert logic could be added here if strict consistency is needed
  }
};

// --- Edit & Resend ---
const editingMsgId = ref<number | null>(null);
const editContent = ref("");

const truncateServerHistory = async (keepCount: number): Promise<boolean> => {
  if (!conversationId.value) return true;
  try {
    const response = await axios.post(
      "/api/v1/chat/history/truncate",
      { conversation_id: conversationId.value, keep_count: keepCount },
      { headers: debugAuthHeaders() },
    );
    return response.data?.data?.success !== false;
  } catch (e) {
    console.warn("Failed to truncate server history before resend", e);
    showToast("同步会话历史失败，请稍后重试", "error");
    return false;
  }
};

const startEdit = (msg: Message) => {
  editingMsgId.value = msg.id;
  editContent.value = msg.content;
};

const cancelEdit = () => {
  editingMsgId.value = null;
  editContent.value = "";
};

const saveAndResend = async () => {
  await sendPreparedMessage(async () => {
    if (editingMsgId.value === null) return null;
    const msgIndex = messages.value.findIndex(m => m.id === editingMsgId.value);
    if (msgIndex === -1) return null;
    const newContent = editContent.value.trim();
    if (!newContent) return null;

    const originalMsg = messages.value[msgIndex];
    const files = (originalMsg?.files || []).map((file) => ({ ...file }));
    const clientRequestId = createClientRequestId();
    const remainingMessages = messages.value.slice(0, msgIndex);
    const keepCount = remainingMessages.filter(isChatContextMessage).length;
    if (!(await truncateServerHistory(keepCount))) return null;

    messages.value = remainingMessages;
    editingMsgId.value = null;
    editContent.value = "";
    return { content: newContent, files, clientRequestId };
  });
};

const regenerate = async (agentMsg: Message) => {
  await sendPreparedMessage(async () => {
    const idx = messages.value.findIndex(m => m.id === agentMsg.id);
    if (idx === -1) return null;
    const userMsg = messages.value[idx - 1];
    if (!userMsg || userMsg.role !== 'user') return null;
    const content = userMsg.content.trim();
    const files = (userMsg.files || []).map((file) => ({ ...file }));
    if (!content && files.length === 0) return null;
    const remainingMessages = messages.value.slice(0, idx - 1);
    const keepCount = remainingMessages.filter(isChatContextMessage).length;
    const clientRequestId = createClientRequestId();
    if (!(await truncateServerHistory(keepCount))) return null;
    messages.value = remainingMessages;
    return { content, files, clientRequestId };
  });
};

const openModelCallStats = async (msg: any) => {
  currentStats.value = [];
  showStatsModal.value = true;
  loadingStats.value = true;
  try {
    const res = await axios.get(`/api/v1/chat/conversation/${conversationId.value}/model_calls`, {
      params: { trace_id: msg.trace_id }
    });
    if (res.data && res.data.data) {
      currentStats.value = res.data.data.stats || [];
    }
  } catch (err) {
    console.error("加载大模型调用明细失败:", err);
  } finally {
    loadingStats.value = false;
  }
};

const chatInputRef = ref<any>(null);
const showWorkspaceDrawer = ref(false);

const readStoredBoolean = (key: string, defaultWhenUnset: boolean) => {
  const stored = localStorage.getItem(key);
  if (stored === "1") return true;
  if (stored === "0") return false;
  return defaultWhenUnset;
};

const workspaceKeepOpenOnSelect = ref(
  readStoredBoolean(
    "debug_workspace_keep_open",
    typeof window !== "undefined" &&
      !window.matchMedia("(max-width: 639px)").matches,
  ),
);
watch(workspaceKeepOpenOnSelect, (val) => {
  localStorage.setItem("debug_workspace_keep_open", val ? "1" : "0");
});

const workspacePinned = ref(
  typeof window !== "undefined" &&
    !window.matchMedia("(max-width: 639px)").matches &&
    readStoredBoolean("debug_workspace_pinned", false),
);
watch(workspacePinned, (val) => {
  // 窄屏下抽屉会强制取消钉住，属于临时状态，不覆盖桌面端的钉住偏好
  if (window.matchMedia("(max-width: 639px)").matches) return;
  localStorage.setItem("debug_workspace_pinned", val ? "1" : "0");
});

/** 桌面端打开工作空间时强制钉住；用户仍可手动取消钉住 */
const openWorkspaceDrawer = () => {
  if (!window.matchMedia("(max-width: 639px)").matches) {
    workspacePinned.value = true;
  }
  showWorkspaceDrawer.value = true;
};
const toggleWorkspaceDrawer = () => {
  if (showWorkspaceDrawer.value) {
    showWorkspaceDrawer.value = false;
    return;
  }
  openWorkspaceDrawer();
};

const showMemoryDrawer = ref(false);

const memoryKeepOpenOnSelect = ref(
  readStoredBoolean(
    "debug_memory_keep_open",
    typeof window !== "undefined" &&
      !window.matchMedia("(max-width: 639px)").matches,
  ),
);
watch(memoryKeepOpenOnSelect, (val) => {
  localStorage.setItem("debug_memory_keep_open", val ? "1" : "0");
});

const memoryPinned = ref(
  typeof window !== "undefined" &&
    !window.matchMedia("(max-width: 639px)").matches &&
    readStoredBoolean("debug_memory_pinned", false),
);
watch(memoryPinned, (val) => {
  localStorage.setItem("debug_memory_pinned", val ? "1" : "0");
});

const attachedMemoryConversationIds = computed(() => {
  const memFile = chatInputRef.value?.uploadedFiles?.find((f: any) => f.type === "memory");
  return memFile?.url ? String(memFile.url) : "";
});

const showStatsModal = ref(false);
const loadingStats = ref(false);
const currentStats = ref<any[]>([]);
const expandedStats = ref<Record<string, boolean>>({});

const toggleStatExpand = (callIndex: number) => {
  expandedStats.value[callIndex] = !expandedStats.value[callIndex];
};

watch(showStatsModal, (newVal) => {
  if (!newVal) {
    expandedStats.value = {};
  }
});

const openMemorySelector = () => {
  showMemoryDrawer.value = true;
};

const handleMemoryMount = (memory: {
  conversation_id: string;
  summary: string;
  last_active?: number;
}) => {
  if (!chatInputRef.value) return;
  const files = chatInputRef.value.uploadedFiles || [];
  const memFile = files.find((f: any) => f.type === "memory");
  const existingIds = memFile?.url
    ? String(memFile.url).split(",").map((id) => id.trim()).filter(Boolean)
    : [];
  if (existingIds.includes(memory.conversation_id)) return;
  const existingMeta = memFile?.memoryMeta || [];
  const newIds = [...existingIds, memory.conversation_id];
  const newMeta = [
    ...existingMeta,
    {
      conversation_id: memory.conversation_id,
      summary: memory.summary,
      last_active: memory.last_active,
    },
  ];
  chatInputRef.value.uploadedFiles = files.filter((f: any) => f.type !== "memory");
  chatInputRef.value.uploadedFiles.push({
    type: "memory",
    url: newIds.join(","),
    filename: `已选择 ${newIds.length} 条记忆记录`,
    size: 0,
    ext: "memory",
    memoryMeta: newMeta,
  });
};

const handleMemoryCleared = (payload: { conversationIds: string[]; all?: boolean }) => {
  if (!chatInputRef.value) return;
  const files = chatInputRef.value.uploadedFiles || [];
  const memFile = files.find((f: any) => f.type === "memory");
  if (!memFile?.url) return;
  if (payload.all) {
    chatInputRef.value.uploadedFiles = files.filter((f: any) => f.type !== "memory");
    return;
  }
  const remainingIds = String(memFile.url)
    .split(",")
    .map((id) => id.trim())
    .filter((id) => id && !payload.conversationIds.includes(id));
  const remainingMeta = (memFile.memoryMeta || []).filter(
    (m: { conversation_id: string }) => !payload.conversationIds.includes(m.conversation_id),
  );
  chatInputRef.value.uploadedFiles = files.filter((f: any) => f.type !== "memory");
  if (remainingIds.length > 0) {
    chatInputRef.value.uploadedFiles.push({
      ...memFile,
      url: remainingIds.join(","),
      filename: `已选择 ${remainingIds.length} 条记忆记录`,
      memoryMeta: remainingMeta,
    });
  }
};

const windowWidth = ref(window.innerWidth);
const isMobile = computed(() => windowWidth.value < 640);
const handleResize = () => {
  windowWidth.value = window.innerWidth;
};
onMounted(() => {
  window.addEventListener('resize', handleResize);
});
onUnmounted(() => {
  window.removeEventListener('resize', handleResize);
});


/** 知识库问答专家候选（capability 或名称命中） */
const listKnowledgeExpertAgents = () => {
  return agents.value.filter((a: any) => {
    const capabilities = Array.isArray(a?.capabilities) ? a.capabilities : [];
    if (capabilities.includes("knowledge_base")) return true;
    const name = String(a?.name || "").toLowerCase();
    const label = String(a?.display_name || "");
    return (
      name === "knowledge-base" ||
      name.includes("knowledge") ||
      label.includes("知识库")
    );
  });
};

/** 仅当恰好 1 个知识库智能体时返回，多个则不自动锁定 */
const resolveKnowledgeExpertAgent = () => {
  const matches = listKnowledgeExpertAgents();
  return matches.length === 1 ? matches[0] : undefined;
};

const buildKnowledgeBaseAttachmentHint = (datasetIdLine: string) => {
  const expert = resolveKnowledgeExpertAgent();
  const expertHint = expert
    ? `本次为知识库查询，须优先由知识库专家「${expert.display_name || expert.name}」处理（agent_name: ${expert.name}，agent_id: ${expert.id}）；自动路由时必须选择该专家，不得分发给 ChatBI、运维或其他专家。`
    : `本次为知识库查询，须优先选择知识库专家（agent_name: knowledge-base）；自动路由时不得分发给 ChatBI、运维或其他专家。`;

  return `${expertHint}\n\n【必须执行】${datasetIdLine}`;
};

const buildDatasetAttachmentHint = (datasetIdLine: string) => {
  const expert = findUniqueDataQueryAgent();
  const expertHint = expert
    ? `本次为数据查询与分析，须优先由数据查询专家「${expert.display_name || expert.name}」（agent_name: ${expert.name}，agent_id: ${expert.id}）处理；自动路由时必须选择该专家，不得分发给主助手或其他专家。`
    : `本次为数据查询与分析，须优先选择具有数据查询能力的专家智能体处理；自动路由时不得分发给其他无关专家。`;

  return `${expertHint}\n\n【必须执行】${datasetIdLine}`;
};

const { appendAttachmentContext } = useChatAttachments({
  buildKnowledgeBaseAttachmentHint,
  buildDatasetAttachmentHint,
});

const handleSelectLocalFs = (payload: { type: 'local_file' | 'local_dir'; path: string; name: string; size: number; ext: string }) => {
  if (!chatInputRef.value) return;
  const files = chatInputRef.value.uploadedFiles || [];
  const exists = files.some((f: any) => f.type === payload.type && f.url === payload.path);
  if (!exists) {
    chatInputRef.value.uploadedFiles.push({
      type: payload.type,
      url: payload.path,
      filename: payload.name,
      size: payload.size,
      ext: payload.ext
    });
  }
};

const resolveFileUrl = (url: string): string => {
  if (!url) return '';
  if (isDirectRenderableUrl(url)) {
    return url;
  }
  const publicUploadUrl = resolvePublicUploadsPreviewUrl(url);
  if (publicUploadUrl) return publicUploadUrl;
  if (!url.startsWith('/static/') &&
      !url.startsWith('/api/') &&
      !url.startsWith('/assets/')) {
    const convParam = conversationId.value ? `&conversation_id=${encodeURIComponent(conversationId.value)}` : "";
    return `/api/v1/chat/fs/preview?path=${encodeURIComponent(url)}${convParam}`;
  }
  return url;
};

const {
  canvasVisible,
  canvasPinned,
  canvasFromWorkspace,
  canvasData,
  handleWorkspaceFilePreview,
  handleOpenCanvas,
  closeCanvas,
  revokeActiveBlobUrl,
} = useWorkspaceCanvas({
  getConversationId: () => conversationId.value,
  resolveFileUrl,
  showToast,
  normalizeDirectPayloadTitle: true,
  isMobile: () => isMobile.value,
});
onUnmounted(() => revokeActiveBlobUrl());

const isImageFile = isImageAttachment;

const formatBytes = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const resolveReqContent = (msg: Message) => {
  let reqContent = msg.content || "";
  if (msg.role === "user" && msg.files && msg.files.length > 0) {
    if (!reqContent.includes(USER_MESSAGE_CONTEXT_DIVIDER)) {
      reqContent = appendAttachmentContext(msg.content, msg.files);
    }
  }
  return reqContent;
};

/** 发给 API 的消息：已有会话只发送当前轮 user，历史由服务端 Redis 负责提供。 */
const buildOutboundMessages = () => {
  const sendable = messages.value.filter(isChatContextMessage);
  if (conversationId.value) {
    const latestUser = [...sendable].reverse().find((m) => m.role === "user");
    if (latestUser) {
      const msgObj: any = {
        role: "user",
        content: resolveReqContent(latestUser),
      };
      if (latestUser.files?.length) {
        msgObj.files = latestUser.files;
      }
      return [msgObj];
    }
    return [];
  }

  const lastUserIdx = sendable.reduce(
    (last, m, i) => (m.role === "user" ? i : last),
    -1,
  );
  return sendable.map((m, idx) => {
    const role = m.role === "agent" ? "assistant" : m.role;
    if (m.role === "user" && idx !== lastUserIdx) {
      return {
        role,
        content: splitUserMessageContent(m.content || "").userPart,
      };
    }
    const msgObj: any = {
      role,
      content: m.role === "user" ? resolveReqContent(m) : (m.content || ""),
    };
    if (m.role === "user" && m.files?.length) {
      msgObj.files = m.files;
    }
    return msgObj;
  });
};

const collectKnowledgeDatasetIds = (): string[] => {
  const ids: string[] = [];
  const pushId = (raw: string) => {
    const value = String(raw || "").trim();
    if (value && !ids.includes(value)) ids.push(value);
  };
  const uploaded = chatInputRef.value?.uploadedFiles || [];
  uploaded.forEach((file: any) => {
    if (file.type === "knowledge_base") pushId(file.url);
  });
  const sendable = messages.value.filter((m) => !m.isThinking && (m.content || m.files?.length));
  sendable.forEach((m) => {
    if (m.role !== "user") return;
    m.files?.forEach((file: any) => {
      if (file.type === "knowledge_base") pushId(file.url);
    });
  });
  return ids;
};

const skillCreatedInfo = ref<SkillCreatedInfo | null>(null);

watch(
  () =>
    messages.value
      .map((m) =>
        [
          m.content || "",
          ...((m.logs || []).map((log: any) => String(log.details || ""))),
        ].join("\n"),
      )
      .join("\n---\n"),
  (blob) => {
    const info = parseSkillCreatedMarker(blob);
    if (info && info.scope === "personal") {
      skillCreatedInfo.value = info;
    }
  },
);

const mountCreatedSkill = () => {
  if (!skillCreatedInfo.value) return;
  handleSelectSkill({
    id: skillCreatedInfo.value.skill_id,
    name: skillCreatedInfo.value.name,
    description: "",
    scope: skillCreatedInfo.value.scope,
  });
  skillCreatedInfo.value = null;
};

const handleSelectSkill = (skill: any) => {
  if (!chatInputRef.value) return;
  const scope = skill.scope === "personal" ? "personal" : "global";
  chatInputRef.value.uploadedFiles.push({
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
};

const handleSwitchMode = (agent: any) => {
  debugMode.value = 'specific';
  agentParams.agent_id = agent.id;
};

const listDataQueryAgents = () => {
  return agents.value.filter((agent: any) => {
    const capabilities = Array.isArray(agent?.capabilities) ? agent.capabilities : [];
    if (capabilities.includes("data_query")) return true;
    const label = `${agent?.name || ""} ${agent?.display_name || ""} ${agent?.description || ""}`;
    return /数据查询|ChatBI|DataQuery/i.test(label);
  });
};

/** 仅当恰好 1 个查数智能体时返回，多个则不自动锁定 */
const findUniqueDataQueryAgent = () => {
  const matches = listDataQueryAgents();
  return matches.length === 1 ? matches[0] : undefined;
};

const hasDataQueryAgent = () => listDataQueryAgents().length > 0;

/** ChatBI「继续分析」等追问：本轮强制走查数智能体，避免自动路由到主助手 */
const forceDataQueryAgentOnce = ref(false);
const resolvePreferredDataQueryAgentId = () => {
  const unique = findUniqueDataQueryAgent();
  if (unique?.id) return String(unique.id);
  const first = listDataQueryAgents()[0];
  return first?.id ? String(first.id) : "";
};
const armDataQueryAgentForFollowup = () => {
  const agentId = resolvePreferredDataQueryAgentId();
  if (!agentId) {
    showToast("未找到可用的数据查询智能体，无法继续可视化分析", "warning");
    return false;
  }
  forceDataQueryAgentOnce.value = true;
  return true;
};
const handleChatBIContinueSelect = (query: string) => {
  if (!armDataQueryAgentForFollowup()) return;
  void handleQuickQuestion(query);
};

const openImagePreview = (url: string) => {
  window.open(url, "_blank");
};

const handleReorderCommands = async (reorderData: any[]) => {
  try {
    await axios.post("/api/portal/slash-commands/reorder", { items: reorderData });
    await fetchSlashCommands();
  } catch (e) {
    console.error("Failed to reorder commands", e);
  }
};

const isFullScreen = ref(false);
const FULLSCREEN_TIP_KEY = "agent_debug_fullscreen_tip_dismissed";
const showFullscreenTip = ref(
  typeof localStorage !== "undefined" &&
    localStorage.getItem(FULLSCREEN_TIP_KEY) !== "1"
);

const toggleFullScreen = () => {
  isFullScreen.value = !isFullScreen.value;
  if (isFullScreen.value) {
    dismissFullscreenTip();
  }
};

const dismissFullscreenTip = () => {
  showFullscreenTip.value = false;
  try {
    localStorage.setItem(FULLSCREEN_TIP_KEY, "1");
  } catch {
    /* ignore */
  }
};

const enterFullScreenFromTip = () => {
  isFullScreen.value = true;
  dismissFullscreenTip();
};

const userInput = ref("");
const isProcessing = ref(false);
const { locked: sendLocked, runExclusive: runSendExclusive } = createChatSendGate();
const focusChatInputWhenReady = () => {
  if (isMobile.value || isProcessing.value || remoteRunActive.value || sendLocked.value) return;
  nextTick(() => chatInputRef.value?.focus());
};

watch([isProcessing, remoteRunActive, sendLocked], focusChatInputWhenReady);

const activeTodoTimeline = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const msg = messages.value[i];
    if (!msg) continue;
    if (isAgentTimelineMessage(msg)) {
      const hasTodo = msg.processTimeline?.some((item) => item.kind === 'todo');
      if (hasTodo) {
        return msg.processTimeline;
      }
    }
  }
  return undefined;
});
const messagesContainer = ref<HTMLDivElement | null>(null);

let abortController: AbortController | null = null;
let thoughtTimer: any = null;

// Auto-scroll
const isUserAtBottom = ref(true);

const handleScroll = () => {
  if (!messagesContainer.value) return;
  const { scrollTop, scrollHeight, clientHeight } = messagesContainer.value;
  // Threshold 50px
  isUserAtBottom.value = scrollHeight - scrollTop - clientHeight < 50;
};

const scrollToBottom = (force = false) => {
  if (messagesContainer.value && (isUserAtBottom.value || force)) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
  }
};

watch(
  [
    () => messages.value.length,
    () => messages.value[messages.value.length - 1]?.content?.length,
    () => messages.value[messages.value.length - 1]?.logs?.length,
  ],
  () => {
    nextTick(scrollToBottom);
  }
);

const showQuotaStatusInChat = async () => {
  messages.value.push({
    id: Date.now(),
    role: "user",
    content: "/quota",
    timestamp: new Date().toISOString(),
  });
  await refreshQuota();
  messages.value.push({
    id: Date.now() + 1,
    role: "agent",
    agentName: "sys_quota",
    agentDisplayName: "系统助手",
    content: buildQuotaStatusMarkdown(quotaStatus.value),
    timestamp: new Date().toISOString(),
  });
  await nextTick();
  scrollToBottom(true);
};

const handleSystemCommand = async (cmd: string): Promise<boolean> => {
  const normalizedCmd = normalizeAgentSwitchCommand(cmd, agents.value);
  if (isDatasetPortalSlashCommand(normalizedCmd)) {
    userInput.value = "";
    if (showPortalDrawer.value) {
      closePortalDrawer();
    } else {
      await openPortalDrawer();
    }
    return true;
  }
  if (isWorkspaceSlashCommand(normalizedCmd)) {
    userInput.value = "";
    toggleWorkspaceDrawer();
    return true;
  }
  if (isKnowledgePortalSlashCommand(normalizedCmd)) {
    userInput.value = "";
    if (showKnowledgePortal.value) {
      closeKnowledgePortal();
    } else {
      await openKnowledgePortal();
    }
    return true;
  }
  if (normalizedCmd === "/switch_to_auto" || normalizedCmd === "/switch_agent_auto") {
    userInput.value = "";
    debugMode.value = "auto";
    showToast("已切换为智能委派模式", "success");
    return true;
  }
  if (normalizedCmd.startsWith("/switch_agent_expert?agent_id=")) {
    userInput.value = "";
    const agentId = normalizedCmd.split("?agent_id=")[1];
    if (agentId) {
      const agent = agents.value.find((a: any) => a.id === agentId);
      if (agent) {
        handleSwitchMode(agent);
        showToast("已切换到指定智能体", "success");
      }
    }
    return true;
  }
  switch (normalizedCmd) {
    case "/history":
      userInput.value = "";
      showHistorySidebar.value = !showHistorySidebar.value;
      return true;
    case "/settings":
      userInput.value = "";
      showConfigPanel.value = !showConfigPanel.value;
      return true;
    case "/quota":
    case "/tokens":
      userInput.value = "";
      await showQuotaStatusInChat();
      return true;
    case "/compact":
      userInput.value = "";
      await manualCompactDebugContext();
      return true;
    case "/new":
    case "/clear":
      userInput.value = "";
      generateNewConversation(true);
      return true;
  }
  return false;
};

const chatbiMonitorDialogOpen = ref(false);
const chatbiMonitorResultId = ref<string>();
const handleChatBIMonitorCreated = (payload: { created: boolean }) => {
  chatbiMonitorDialogOpen.value = false;
  showToast(payload.created === false ? "该结果的订阅已存在" : "查询订阅已创建，可在固化报表中管理", "success");
};

const handleChatBIResultAction = async (
  action: ChatBIInsightMeta["actions"][number],
  sourceMessage?: { content?: string; chatbiInsight?: ChatBIInsightMeta },
) => {
  if (action.id === "monitor") {
    chatbiMonitorResultId.value = action.result_id;
    chatbiMonitorDialogOpen.value = true;
    return;
  }
  if (action.id !== "brief") {
    if (!armDataQueryAgentForFollowup()) return;
    return handleQuickQuestion(action.query);
  }
  const assistantReport = sourceMessage?.content?.trim();
  if (!assistantReport) {
    showToast("未找到当前分析正文，请在本轮回复旁重试", "warning");
    return;
  }
  try {
    showToast("正在生成业务简报…", "info");
    const res = await axios.post("/api/portal/chatbi-briefs", {
      conversation_id: conversationId.value,
      result_id: action.result_id || sourceMessage?.chatbiInsight?.result_id,
      export_word: true,
      assistant_report: assistantReport,
      polish_with_llm: true,
    });
    const artifact = res.data?.data?.artifact;
    if (artifact?.download_url) {
      const link = document.createElement("a");
      link.href = artifact.download_url;
      link.download = artifact.filename || "ChatBI业务简报.docx";
      link.click();
    }
    showToast("业务简报已生成", "success");
  } catch (error: any) {
    showToast(error.response?.data?.detail || "业务简报生成失败", "error");
  }
};

const handleQuickQuestion = async (question: string, action: "send" | "fill" = "send", sourceContent?: string) => {
  if (!question) return;
  if (action === "send" && (isProcessing.value || remoteRunActive.value || sendLocked.value)) return;
  const selectedSource = sourceContent?.trim();
  const nextContent = selectedSource
    ? `${question}${USER_MESSAGE_CONTEXT_DIVIDER}【被点击的 AI 回复】\n${selectedSource}`
    : question;
  if (action === "send") {
    await sendMessage({ content: nextContent });
  } else {
    userInput.value = nextContent;
  }
};

const handleAnalyzeCodeOutput = async (question: string) => {
  canvasVisible.value = false;
  await sendMessage({ content: question });
};

const handleGroundingAction = async (
  payload: GroundingBlockedPayload | undefined,
  action: GroundingBlockedAction,
) => {
  if (!payload || isProcessing.value || remoteRunActive.value || sendLocked.value) return;
  if (action.kind === "grounding_retry") {
    const groundingAction = {
      ...(action.payload || {}),
      type: "retry",
    };
    await sendMessage({ content: payload.retry_query, groundingAction });
    return;
  }
  if (action.kind === "grounding_method") {
    const groundingAction = {
      ...(action.payload || {}),
      type: "method",
    };
    await sendMessage({ content: String(action.payload?.message || ""), groundingAction });
    return;
  }
  if (action.kind === "send_message") {
    await handleQuickQuestion(String(action.payload?.message || ""));
  }
};

const {
  showPortalDrawer,
  portalNavigationPayload,
  portalLoading,
  portalBackgroundRefreshing,
  portalKeepOpenOnQuestion,
  portalPinned,
  openPortalDrawer: rawOpenPortalDrawer,
  closePortalDrawer,
  refreshPortalNavigation,
  handlePortalQuickQuestion,
  recordDatasetMenuQuestionClick: recordPortalQuestionClick,
  clearDatasetMenuQuestionClick: clearPortalQuestionClick,
  fetchDatasetMenuNavigationPayload,
  disposePortalTimers,
} = useDatasetPortal({
  getAuthHeaders: () => debugAuthHeaders() || {},
  showToast,
  onQuickQuestion: handleQuickQuestion,
  hasDataQueryAgent,
  keepOpenStorageKey: "debug_portal_keep_open",
  pinStorageKey: "debug_portal_pinned",
});

const {
  activeMetadataDatasetIds,
  syncActiveMetadataDatasetsFromInput,
  toggleMetadataDatasetActive,
} = useDatasetMount();

const loadMetadataMountableDatasets = async () => {
  if (metadataMountableDatasets.value.length) return;
  try {
    const res = await axios.get("/api/portal/metadata/datasets/accessible", { headers: debugAuthHeaders() });
    const raw = res.data?.data || res.data?.datasets || res.data || [];
    metadataMountableDatasets.value = (Array.isArray(raw) ? raw : [])
      .filter((item: any) => item.status === undefined || item.status === 1 || item.status === "1" || item.status === "active")
      .map((item: any) => ({
        id: String(item.id || item.name),
        name: item.display_name || item.name || item.dataset_name,
        dataset_name: item.name || item.dataset_name,
        description: item.description || item.remark || item.notes,
      }));
  } catch (error) {
    console.warn("Failed to load metadata dataset mount options", error);
    showToast("加载可挂载数据集失败，请稍后重试", "error");
  }
};

const loadMetadataDatasetSessionScope = async () => {
  if (!conversationId.value) return;
  try {
    const res = await axios.get(
      `/api/v1/chat/conversation/${encodeURIComponent(conversationId.value)}/resource-scope`,
      { headers: debugAuthHeaders() },
    );
    sessionMountedMetadataDatasetIds.value = (res.data?.data?.datasets || [])
      .map((item: any) => String(item.id || "").trim())
      .filter(Boolean);
  } catch (error) {
    console.warn("Failed to load metadata dataset session scope", error);
  }
};

const mountMcpToolToSession = async (
  toolsInput:
    | Array<{ id: string; name: string; description?: string; server_name?: string; server_remark?: string; scope?: string }>
    | { id: string; name: string; description?: string; server_name?: string; server_remark?: string; scope?: string },
) => {
  if (!conversationId.value) {
    showToast("请先开始会话", "error");
    return;
  }
  const tools = (Array.isArray(toolsInput) ? toolsInput : [toolsInput])
    .map((tool) => ({
      id: String(tool?.id || "").trim(),
      name: String(tool?.name || "").trim(),
      description: tool?.description || "",
      server_name: tool?.server_name || "",
      server_remark: tool?.server_remark || "",
      scope: tool?.scope || "global",
    }))
    .filter((tool) => tool.id && tool.name);
  if (!tools.length) return;
  try {
    const res = await axios.get(
      `/api/v1/chat/conversation/${encodeURIComponent(conversationId.value)}/resource-scope`,
      { headers: debugAuthHeaders() },
    );
    const scope = res.data?.data || { project_name: "", datasets: [], knowledge_bases: [], skills: [], mcp_tools: [] };
    const existing = scope.mcp_tools || [];
    const existingNames = new Set(existing.map((item: any) => String(item.name || "").trim()).filter(Boolean));
    const existingIds = new Set(existing.map((item: any) => String(item.id || "").trim()).filter(Boolean));
    const toAdd = tools.filter((tool) => !existingNames.has(tool.name) && !existingIds.has(tool.id));
    if (!toAdd.length) {
      showToast(tools.length === 1 ? "该 MCP 工具已挂载到本会话" : "所选 MCP 工具均已挂载到本会话", "info");
      return;
    }
    await axios.put(
      `/api/v1/chat/conversation/${encodeURIComponent(conversationId.value)}/resource-scope`,
      {
        ...scope,
        mcp_tools: [...existing, ...toAdd],
      },
      { headers: debugAuthHeaders() },
    );
    showToast(
      toAdd.length === 1 ? `已挂载 MCP 工具：${toAdd[0]?.name || ''}` : `已挂载 ${toAdd.length} 个 MCP 工具`,
      "success",
    );
  } catch (error) {
    console.warn("Failed to mount MCP tool to session", error);
    showToast("挂载 MCP 工具失败", "error");
  }
};

const pinMetadataDatasetToSession = async (datasetId: string) => {
  if (!conversationId.value) {
    showToast("请先开始会话", "error");
    return;
  }
  const id = String(datasetId || "").trim();
  if (!id) return;
  try {
    const res = await axios.get(
      `/api/v1/chat/conversation/${encodeURIComponent(conversationId.value)}/resource-scope`,
      { headers: debugAuthHeaders() },
    );
    const scope = res.data?.data || { project_name: "", datasets: [], knowledge_bases: [], skills: [], mcp_tools: [] };
    if ((scope.datasets || []).some((item: any) => String(item.id || "").trim() === id)) {
      showToast("该数据集已设为本会话默认", "info");
      return;
    }
    const dataset = metadataMountableDatasets.value.find((item) => item.id === id);
    const selected = {
      id,
      name: dataset?.name || id,
      ...(dataset?.dataset_name ? { dataset_name: dataset.dataset_name } : {}),
    };
    const saved = await axios.put(
      `/api/v1/chat/conversation/${encodeURIComponent(conversationId.value)}/resource-scope`,
      { ...scope, datasets: [...(scope.datasets || []), selected] },
      { headers: debugAuthHeaders() },
    );
    sessionMountedMetadataDatasetIds.value = (saved.data?.data?.datasets || [...(scope.datasets || []), selected])
      .map((item: any) => String(item.id || "").trim())
      .filter(Boolean);
    const dsName = selected.name || dataset?.name || id;
    showToast(`已设为本会话默认：后续提问将默认锁定在【${dsName}】`, "success");
  } catch (error) {
    console.warn("Failed to pin metadata dataset to session", error);
    showToast("设置会话默认范围失败", "error");
  }
};

const unpinMetadataDatasetFromSession = async (datasetId: string) => {
  if (!conversationId.value) {
    showToast("请先开始会话", "error");
    return;
  }
  const id = String(datasetId || "").trim();
  if (!id) return;
  try {
    const res = await axios.get(
      `/api/v1/chat/conversation/${encodeURIComponent(conversationId.value)}/resource-scope`,
      { headers: debugAuthHeaders() },
    );
    const scope = res.data?.data || { project_name: "", datasets: [], knowledge_bases: [], skills: [], mcp_tools: [] };
    const nextDatasets = (scope.datasets || []).filter((item: any) => String(item.id || "").trim() !== id);
    const saved = await axios.put(
      `/api/v1/chat/conversation/${encodeURIComponent(conversationId.value)}/resource-scope`,
      { ...scope, datasets: nextDatasets },
      { headers: debugAuthHeaders() },
    );
    sessionMountedMetadataDatasetIds.value = (saved.data?.data?.datasets || nextDatasets)
      .map((item: any) => String(item.id || "").trim())
      .filter(Boolean);
    showToast("已取消本会话默认，问数将恢复默认权限范围", "info");
  } catch (error) {
    console.warn("Failed to unpin metadata dataset from session", error);
    showToast("取消会话默认失败", "error");
  }
};

const openPortalDrawer = async () => {
  // 挂载匹配异步补齐，不阻塞门户打开
  void loadMetadataMountableDatasets();
  void loadMetadataDatasetSessionScope();
  await rawOpenPortalDrawer();
};

const {
  showKnowledgePortal,
  knowledgePinned,
  knowledgeKeepOpenOnQuestion,
  hallucinationCheckEnabled,
  knowledgeSimilarityThreshold,
  knowledgeVectorWeight,
  knowledgeMetadataTopK,
  knowledgeGeneratedAt,
  datasets: knowledgeDatasets,
  loadingDatasets: loadingKnowledgeDatasets,
  datasetLoadError: knowledgeLoadError,
  activeDatasetIds,
  datasetRecommendations,
  pinnedDatasetIds,
  datasetDocuments,
  documentRecommendations,
  toggleDatasetPinned,
  fetchDatasetDocuments,
  fetchDocumentRecommendations,
  fetchDatasets,
  fetchRecommendations,
  syncActiveDatasetsFromInput,
  toggleDatasetActive,
  openKnowledgePortal: rawOpenKnowledgePortal,
  closeKnowledgePortal
} = useKnowledgePortal({
  showToast,
  onOpenAnotherPortal: () => {
    closePortalDrawer();
  }
});

const openKnowledgePortal = async () => {
  await rawOpenKnowledgePortal();
};

watch(showPortalDrawer, (val) => {
  if (val) {
    closeKnowledgePortal();
  }
});

// 监听上传文件的变更，保持知识库与数据集激活状态在抽屉卡片里是最新同步的
watch(
  () => chatInputRef.value?.uploadedFiles,
  () => {
    syncActiveDatasetsFromInput(chatInputRef.value);
    syncActiveMetadataDatasetsFromInput(chatInputRef.value);
  },
  { deep: true }
);

const pinnedDrawerDockOffsetRem = (exclude?: "portal" | "workspace" | "memory" | "knowledge") => {
  let rem = 0;
  if (exclude !== "portal" && showPortalDrawer.value && portalPinned.value) rem += 28;
  if (exclude !== "knowledge" && showKnowledgePortal.value && knowledgePinned.value) rem += 28;
  if (exclude !== "workspace" && showWorkspaceDrawer.value && workspacePinned.value) rem += 28;
  if (exclude !== "memory" && showMemoryDrawer.value && memoryPinned.value) rem += 28;
  return rem;
};

const pinnedDrawerRightRem = computed(() => {
  if (isMobile.value) return 0;
  return pinnedDrawerDockOffsetRem();
});

const saveReportModalOverlayStyle = computed(() => {
  const rem = pinnedDrawerRightRem.value;
  return { right: rem > 0 ? `${rem}rem` : "0" };
});
const saveReportModalOverlayClass = computed(() => {
  const isPinned = (showPortalDrawer.value && portalPinned.value) || (showKnowledgePortal.value && knowledgePinned.value);
  return isPinned ? 'right-[28rem]' : 'right-0';
});

const pinnedDrawerMarginStyle = computed(() => {
  const rem = pinnedDrawerRightRem.value;
  return { marginRight: rem > 0 ? `min(${rem}rem, 100vw)` : "" };
});

const hasPinnedDrawer = computed(() => {
  return pinnedDrawerRightRem.value > 0;
});

const workspacePinnedDockClass = computed(() => {
  const rem = pinnedDrawerDockOffsetRem("workspace");
  return rem > 0 ? `right-[${rem}rem]` : "right-0";
});

const memoryPinnedDockClass = computed(() => {
  const rem = pinnedDrawerDockOffsetRem("memory");
  return rem > 0 ? `right-[${rem}rem]` : "right-0";
});

const INLINE_DATASET_MENU_EMPTY =
  "当前暂无可展示的数据集导航，请联系管理员开通数据权限。";

/** 历史消息内嵌 DatasetCapabilityMenu 的刷新（抽屉门户走 refreshPortalNavigation） */
const refreshDatasetMenuNavigation = async (msg: Message) => {
  if (isProcessing.value) return;
  try {
    const payload = await fetchDatasetMenuNavigationPayload(true);
    msg.datasetNavigation = payload;
    msg.content = payload?.markdown || INLINE_DATASET_MENU_EMPTY;
    if (payload?.llm_generation_failed) {
      const detail = String(payload.llm_error_message || "").trim();
      const hint = detail ? `：${detail}` : "";
      showToast(`AI 模型暂不可用，仍为基础场景目录${hint}`, "error");
    } else {
      showToast("数据门户刷新成功", "success");
    }
    await nextTick();
    scrollToBottom(true);
  } catch (error) {
    console.warn("Failed to refresh dataset menu navigation", error);
    showToast("刷新数据门户失败，请稍后重试", "error");
    if (msg.datasetNavigation) {
      msg.datasetNavigation = { ...msg.datasetNavigation, _failed_at: new Date().toISOString() };
    }
  }
};

const handleEscKey = (e: KeyboardEvent) => {
  if (e.key === "Escape" && showWorkspaceDrawer.value) {
    showWorkspaceDrawer.value = false;
    return;
  }
  if (e.key === "Escape" && showPortalDrawer.value) {
    closePortalDrawer();
    return;
  }
  if (e.key === "Escape" && isFullScreen.value) {
    isFullScreen.value = false;
  }
};

const handleRunStatusVisibilityChange = () => {
  if (document.visibilityState === "visible") {
    void refreshCurrentRunStatus();
  } else {
    stopRemoteRunPolling();
  }
};

onMounted(() => {
  window.addEventListener("keydown", handleEscKey);
  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("visibilitychange", handleRunStatusVisibilityChange);
});

onUnmounted(() => {
  window.removeEventListener("keydown", handleEscKey);
  document.removeEventListener("click", handleDocumentClick);
  document.removeEventListener("visibilitychange", handleRunStatusVisibilityChange);
  disposePortalTimers();
});

const citationPopover = ref<{
  visible: boolean;
  citation: any;
  anchorRect: DOMRect | null;
  anchorEl: HTMLElement | null;
}>({
  visible: false,
  citation: null,
  anchorRect: null,
  anchorEl: null,
});

const closeCitationPopover = () => {
  citationPopover.value.visible = false;
  citationPopover.value.citation = null;
  citationPopover.value.anchorRect = null;
  citationPopover.value.anchorEl = null;
};

const openCitationPopover = (citation: any, event: MouseEvent | HTMLElement) => {
  const anchor = event instanceof HTMLElement ? event : (event.currentTarget as HTMLElement);
  if (!anchor) return;

  if (
    citationPopover.value.visible &&
    citationPopover.value.citation === citation
  ) {
    closeCitationPopover();
    return;
  }

  const rect = anchor.getBoundingClientRect();
  citationPopover.value = {
    visible: true,
    citation,
    anchorRect: new DOMRect(rect.x, rect.y, rect.width, rect.height),
    anchorEl: anchor,
  };
};

const resolveCitation = (msg: Message, citeId: string) => {
  if (!msg.citations || msg.citations.length === 0) return null;

  let target = msg.citations.find(
    (c) =>
      String(c.chunk_id) === String(citeId) ||
      String(c.id) === String(citeId) ||
      String(c.chunk_id)?.endsWith(String(citeId))
  );

  if (!target && /^\d+$/.test(citeId)) {
    const idx = parseInt(citeId);
    target = msg.citations[idx - 1] || msg.citations[idx];
  }

  return target || null;
};

const handleShowCitation = async (msg: Message, citeId: string, anchor?: HTMLElement) => {
  const target = resolveCitation(msg, citeId);
  if (!target) return;

  msg.isCitationsExpanded = true;
  await nextTick();
  const anchorEl = anchor || (document.querySelector(`[data-cite-id="${citeId}"]`) as HTMLElement);
  if (anchorEl) {
    anchorEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    openCitationPopover(target, anchorEl);
  }
};

const ragPreviewVisible = ref(false);
const ragPreviewDatasetId = ref("");
const ragPreviewDocId = ref("");
const ragPreviewDocName = ref("");
const ragPreviewPageNo = ref<string | number>(1);
const ragPreviewContent = ref("");

const ragPreviewFileUrl = computed(() => {
  if (!ragPreviewDatasetId.value || !ragPreviewDocId.value) return "";
  const datasetId = encodeURIComponent(ragPreviewDatasetId.value);
  const docId = encodeURIComponent(ragPreviewDocId.value);
  return `/api/portal/ragflow/datasets/${datasetId}/documents/${docId}/file`;
});

const isOfficeDocument = computed(() => {
  const name = ragPreviewDocName.value.toLowerCase();
  return name.endsWith(".doc") || name.endsWith(".docx") || 
         name.endsWith(".xls") || name.endsWith(".xlsx") || 
         name.endsWith(".ppt") || name.endsWith(".pptx");
});

const handleViewOriginal = (citation: any) => {
  closeCitationPopover();
  if (citation.source_type === "web") {
    if (citation.link) {
      window.open(citation.link, "_blank");
    }
  } else {
    ragPreviewDatasetId.value = citation.dataset_id || "";
    ragPreviewDocId.value = citation.doc_id || "";
    ragPreviewDocName.value = citation.doc_name || "文件预览";
    ragPreviewPageNo.value = citation.page_no || 1;
    ragPreviewContent.value = citation.content || "";
    ragPreviewVisible.value = Boolean(ragPreviewDatasetId.value && ragPreviewDocId.value);
  }
};

const copyCitationContent = async (content: string) => {
  const ok = await copyToClipboard(content);
  if (ok) {
    showToast("已复制引用内容", "success");
  } else {
    console.error("Failed to copy citation");
    showToast("复制失败", "error");
  }
};


const handleFeedback = async (msg: Message, type: "up" | "down") => {
  const oldFeedback = msg.feedback;
  if (msg.feedback === type) {
    msg.feedback = null;
  } else {
    msg.feedback = type;
  }

  // 立即给予 UI 反馈提示 (乐观更新)
  if (msg.feedback) {
    showToast(msg.feedback === 'up' ? "感谢您的点赞！" : "已记录您的反馈，我们将持续改进。", "success");
  } else {
    showToast("已取消反馈", "info");
  }

  if (!msg.trace_id) {
    console.warn("Cannot post feedback to server: missing trace_id");
    return;
  }

  try {
    await axios.post("/api/portal/chat/feedback", {
      trace_id: msg.trace_id,
      feedback: msg.feedback || "none",
      user_id: currentUser.value?.user_id || "anonymous"
    });
  } catch (error) {
    console.error("Failed to post feedback", error);
    msg.feedback = oldFeedback;
  }
};


const stopGeneration = () => {
  const lastMsg = messages.value.length > 0 ? messages.value[messages.value.length - 1] : null;
  if (conversationId.value) {
    void cancelConversationRun(conversationId.value, {
      traceId: lastMsg?.trace_id,
    }).finally(() => {
      void refreshCurrentRunStatus();
    });
  }
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
  isProcessing.value = false;

  // Stop thinking timer if active
  if (thoughtTimer) {
    clearInterval(thoughtTimer);
    thoughtTimer = null;
  }

  // Update last message status if needed
  if (lastMsg && lastMsg.role === 'agent' && lastMsg.isThinking) {
    lastMsg.isThinking = false;
    lastMsg.content += "\n[用户终止生成]";
  }
};

const tryLocalChartOptionPatch = (userText: string): boolean => {
  const q = userText.toLowerCase().trim();
  let newType: 'line' | 'bar' | 'pie' | null = null;
  if (/改(成|为)折线图/.test(q) || /换成折线/.test(q)) {
    newType = 'line';
  } else if (/改(成|为)柱状图/.test(q) || /换成柱状/.test(q)) {
    newType = 'bar';
  } else if (/改(成|为)饼图/.test(q) || /换成饼图/.test(q)) {
    newType = 'pie';
  }

  const isRedPatch = /标红/.test(q);

  if (!newType && !isRedPatch) {
    return false;
  }

  // Find the last agent message with a chart block
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const msg = messages.value[i];
    if (!msg) continue;
    if (msg.role === 'agent' && msg.content) {
      const chartRegex = /(<chart>([\s\S]*?)<\/chart>)|(```(?:chart|echarts)\s*([\s\S]*?)```)/gi;
      const match = chartRegex.exec(msg.content);
      if (match) {
        const fullMatch = match[0];
        const jsonContent = (match[2] || match[4] || "").trim();
        if (!jsonContent) continue;
        try {
          const option = JSON.parse(jsonContent);
          if (option.series) {
            if (newType) {
              if (Array.isArray(option.series)) {
                option.series = option.series.map((s: any) => ({ ...s, type: newType }));
              } else if (typeof option.series === 'object') {
                option.series.type = newType;
              }
              if (newType === 'pie') {
                delete option.xAxis;
                delete option.yAxis;
              }
            }
            if (isRedPatch) {
              if (Array.isArray(option.series)) {
                option.series = option.series.map((s: any) => ({
                  ...s,
                  itemStyle: { ...s.itemStyle, color: '#ef4444' }
                }));
              } else if (typeof option.series === 'object') {
                option.series.itemStyle = { ...option.series.itemStyle, color: '#ef4444' };
              }
            }

            const updatedJson = JSON.stringify(option, null, 2);
            let updatedMatch = "";
            if (match[1]) {
              updatedMatch = `<chart>\n${updatedJson}\n</chart>`;
            } else {
              updatedMatch = `\`\`\`chart\n${updatedJson}\n\`\`\``;
            }

            msg.content = msg.content.replace(fullMatch, updatedMatch);
            messages.value.push({
              id: Date.now(),
              role: "user",
              content: userText,
              timestamp: new Date().toISOString(),
            });
            messages.value.push({
              id: Date.now() + 1,
              role: "agent",
              content: `✨ 已为您本地秒级重绘图表，将图表形式调整为：${newType === 'line' ? '折线图' : newType === 'bar' ? '柱状图' : newType === 'pie' ? '饼图' : '标红调整'}。`,
              timestamp: new Date().toISOString(),
              logs: [],
              citations: [],
            });
            return true;
          }
        } catch (err) {
          console.error("Failed to parse or patch ECharts option locally:", err);
        }
      }
    }
  }
  return false;
};

const captureSendSnapshot = (overrides: ChatSendOverrides = {}): ChatSendSnapshot => ({
  content: String(overrides.content ?? userInput.value).trim(),
  files: overrides.files
    ? overrides.files.map((file) => ({ ...file }))
    : (chatInputRef.value?.uploadedFiles ? Array.from(chatInputRef.value.uploadedFiles) as ChatFile[] : [])
        .map((file) => ({ ...file })),
  clientRequestId: overrides.clientRequestId || createClientRequestId(),
  groundingAction: overrides.groundingAction ? { ...overrides.groundingAction } : undefined,
});

const sendPreparedMessage = async (
  prepare: () => Promise<ChatSendSnapshot | null>,
) => runSendExclusive(async () => {
  if (isProcessing.value || remoteRunActive.value) return;
  const snapshot = await prepare();
  if (!snapshot) return;
  return sendMessageInternal(snapshot);
});

const sendMessage = async (overrides: ChatSendOverrides = {}) => runSendExclusive(async () => {
  if (isProcessing.value || remoteRunActive.value) return;
  return sendMessageInternal(captureSendSnapshot(overrides));
});

const sendMessageInternal = async (snapshot: ChatSendSnapshot) => {
  const { content, files } = snapshot;
  const turnMetadataDatasetIds = [...activeMetadataDatasetIds.value];
  if (!content && files.length === 0) return;
  if (isProcessing.value || remoteRunActive.value) return;

  // 尽早消费「强制查数智能体」标记，避免中途 return 后泄漏到下一轮普通提问
  const forcedDataAgentIdForTurn = forceDataQueryAgentOnce.value ? resolvePreferredDataQueryAgentId() : "";
  forceDataQueryAgentOnce.value = false;

  if (files.length === 0 && tryLocalChartOptionPatch(content)) {
    userInput.value = "";
    if (chatInputRef.value) {
      chatInputRef.value.uploadedFiles = [];
    }
    nextTick(() => scrollToBottom(true));
    return;
  }

  if (await handleSystemCommand(content)) {
    userInput.value = "";
    if (chatInputRef.value) {
      chatInputRef.value.uploadedFiles = [];
    }
    return;
  }

  // 1. Add User Message
  messages.value.push({
    id: Date.now(),
    role: "user",
    content: content,
    files: files,
  });

  // Force scroll for user message
  nextTick(() => scrollToBottom(true));

  userInput.value = "";
  if (chatInputRef.value) {
    chatInputRef.value.uploadedFiles = [];
  }
  isProcessing.value = true;

  // 2. Add Agent Placeholder
  const agentMsgId = Date.now() + 1;
  const agentMsg = ref<Message>({
    id: agentMsgId,
    role: "agent",
    content: "",
    isThinking: true,
    logs: [],
    citations: [],
    isCitationsExpanded: false,
    isThoughtExpanded: true, // Default expanded to show progress
    thoughtStartTime: Date.now(),
    thoughtDuration: "0.0",
    thinkingText: "南孜正在处理您的请求...",
  });
  messages.value.push(agentMsg.value);

  // Thinking Messages
  const THINKING_MESSAGES = [
    "正在分析任务...",
    "正在组织语言...",
  ];

  // Start Timer
  if (thoughtTimer) clearInterval(thoughtTimer);
  let ticks = 0;
  thoughtTimer = setInterval(() => {
    ticks++;
    if (agentMsg.value.thoughtStartTime) {
      const duration = (Date.now() - agentMsg.value.thoughtStartTime) / 1000;
      agentMsg.value.thoughtDuration = duration.toFixed(1);
    }

    // Rotate message every 3 seconds (30 * 100ms)
    if (ticks % 30 === 0) {
      const msgIndex = (ticks / 30) % THINKING_MESSAGES.length;
      agentMsg.value.thinkingText = THINKING_MESSAGES[msgIndex];
    }
    if (ticks % 100 === 0) {
      if (markStalePendingStreamLogs(agentMsg.value)) {
        agentMsg.value.isThinking = false;
      }
    }
  }, 100);

  // 3. Call Real API with SSE
  // SSE 可能因切后台/网络变化提前结束；在状态接口确认释放前继续阻止新一轮发送。
  remoteRunActive.value = true;
  abortController = new AbortController();
  ragRetrievalMeta.value = null;

  try {
    // Prepare Debug Options
    const debugOptions: any = {
      return_raw_prompt: debugConfig.returnRawPrompt,
      dry_run: debugConfig.dryRun,
      grounding_enabled: debugConfig.enableGrounding,
      hallucination_check: hallucinationCheckEnabled.value || undefined,
      knowledge_ragflow_similarity_threshold: knowledgeSimilarityThreshold.value,
      knowledge_ragflow_vector_weight: knowledgeVectorWeight.value,
      knowledge_ragflow_metadata_top_k: knowledgeMetadataTopK.value,
    };
    if (debugConfig.model) debugOptions.model = debugConfig.model;
    if (debugConfig.thinkingEnableOverride !== null) {
      debugOptions.thinking_enable = debugConfig.thinkingEnableOverride;
    }
    if (debugConfig.reasoningEffortOverride !== null) {
      debugOptions.reasoning_effort = debugConfig.reasoningEffortOverride;
    }
    if (debugConfig.temperature > 0)
      debugOptions.temperature = debugConfig.temperature;

    // Add Prompt Override
    if (debugConfig.systemPromptOverride.trim()) {
      debugOptions.system_prompt_override = debugConfig.systemPromptOverride;
    }

    // Add Context Injection
    if (debugConfig.injectedContext.length > 0) {
      const contextMap: Record<string, string> = {};
      debugConfig.injectedContext.forEach((item) => {
        if (item.key.trim()) contextMap[item.key] = item.value;
      });
      if (Object.keys(contextMap).length > 0) {
        debugOptions.injected_context = contextMap;
      }
    }

    const knowledgeDatasetIds = collectKnowledgeDatasetIds();
    const requestBody: Record<string, unknown> = {
        messages: buildOutboundMessages(),
        stream: true,
        enable_multi_agent: debugConfig.enableMultiAgent,
        debug_options: debugOptions,
        permission_options: {
          approval_mode: debugConfig.approvalMode || "ask",
        },
        agent_id: forcedDataAgentIdForTurn || agentParams.agent_id,
        version_id: agentParams.version_id,
        conversation_id: conversationId.value,
    };
    if (knowledgeDatasetIds.length > 0) {
      requestBody.knowledge_dataset_ids = knowledgeDatasetIds;
    }
    if (turnMetadataDatasetIds.length > 0) {
      requestBody.metadata_dataset_ids = turnMetadataDatasetIds;
    }
    if (snapshot.groundingAction) requestBody.grounding_action = snapshot.groundingAction;
    requestBody.client_request_id = snapshot.clientRequestId;

    const response = await fetch("/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": localStorage.getItem("api_key") || "",
      },
      body: JSON.stringify(requestBody),
      signal: abortController.signal,
    });

    if (!response.ok) {
      let errorDetails = `Status: ${response.status} ${response.statusText}`;
      try {
        const errData = await response.json();
        if (errData.message) errorDetails = errData.message;
      } catch (e) {}
      throw new Error(errorDetails);
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();

    if (!reader) throw new Error("No response body");

    const sseLineParser = createSseLineParser();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      const dataLines = sseLineParser.feed(chunk);

      for (const dataStr of dataLines) {
          if (dataStr === "[DONE]") continue;

          try {
            const data = JSON.parse(dataStr);
            if (data?.type === "keepalive") {
              // 后端静默期心跳帧：仅用于连接保活，无业务负载。
              continue;
            }
            applyStreamTraceId(agentMsg.value, data);

            // Handle Structured Logs
            if (data.type === "duplicate_request") {
              agentMsg.value.isThinking = false;
              (agentMsg.value as any).status = "duplicate_request";
              agentMsg.value.content = String(data.content || "相同发送请求已提交，请等待原任务完成。\n");
              remoteRunActive.value = true;
              void refreshCurrentRunStatus();
            } else if (data.type === "run_status") {
              agentMsg.value.isThinking = false;
              markOutputCompleted();
              if (data.status === "success") {
                (agentMsg.value as any).status = "success";
              }
            } else if (data.type === "log") {
              addRealLog(agentMsg.value, data);
            }
            // Handle Router Logic (New)
            else if (data.type === "router_log") {
              const thoughtText = data.thought || "No reasoning provided.";
              const agentName = data.selected_agent || "Unknown";
              const conf = data.confidence !== undefined ? `(置信度: ${data.confidence})` : "";

              addRealLog(agentMsg.value, {
                id: data.id || "route:target_selection",
                parent_id: data.parent_id || "route:target_config",
                title: "智能路由决策",
                details: `**思考过程 (Chain of Thought):**\n${thoughtText}\n\n**最终选择:** ${agentName} ${conf}`,
                status: "success",
                isDebug: true,
                isRouter: true // Mark as router log
              });
            }
            // Handle Debug Events
            else if (data.type === "debug") {
              if (data.subtype === "raw_prompt") {
                agentMsg.value.rawPrompt = data.data;
                addRealLog(agentMsg.value, {
                  title: "Debug: Raw Prompt Captured",
                  details: 'Click "Raw Prompt" button to view.',
                  status: "success",
                  isDebug: true,
                });
              }
            }
            // Handle Citations (New)
            else if (mergeStreamCitations(agentMsg.value, data)) {
              // Citations are merged and de-duplicated by the shared stream normalizer.
            }
            // Handle Context Update Events (New)
            else if (data.type === "context") {
              addRealLog(agentMsg.value, {
                title: "✨ Context Updated",
                details: JSON.stringify(data.data, null, 2),
                status: "success",
              });
              // Update reactive context stack
              if (data.data) {
                agentContext.value = { ...agentContext.value, ...data.data };
              }
            }
            else if (dispatchAgentscopeStreamEvent(agentMsg.value, data, addRealLog, messages.value)) {
              if (
                (data.type === "permission_required" || (data.type === "retraction" && data.final !== false))
                && thoughtTimer
              ) {
                clearInterval(thoughtTimer);
                thoughtTimer = null;
              }
            }
            // Handle Retraction Event
            else if (data.type === "retraction") {
              agentMsg.value.content = stripInternalContextBlocks(String(data.content || ""));
              if (data.final !== false) {
                agentMsg.value.isThinking = false;
                if (thoughtTimer) {
                  clearInterval(thoughtTimer);
                  thoughtTimer = null;
                }
              }
            }
            // Handle Content Stream
            else if (data.type === "answer_delta" || data.content) {
              const piece = sanitizeStreamContent(String(data.content));
              if (piece) {
                if (
                  agentMsg.value.isThoughtExpanded
                  && !agentMsg.value.content
                  && !timelineHasPending(agentMsg.value.processTimeline)
                ) {
                  agentMsg.value.isThoughtExpanded = false;
                }
                appendAssistantBodyDelta(agentMsg.value, piece);
                if (agentMsg.value.isThinking) {
                  agentMsg.value.isThinking = false;
                  if (thoughtTimer) {
                    clearInterval(thoughtTimer);
                    thoughtTimer = null;
                  }
                }
              }
            }
            // Handle Meta Info (Agent Name)
            else if (applyChatBIInsightEvent(agentMsg.value, data) || applyChatBIMetadataGuideEvent(agentMsg.value, data) || applyAgentHandoffEvent(agentMsg.value, data)) {
              // Additive ChatBI evidence event.
            }
            else if (data.type === "meta") {
              if (data.agent_name) {
                agentMsg.value.agentName = data.agent_name;
                if (data.agent_type) agentMsg.value.agentType = data.agent_type;
                if (data.agent_display_name) {
                    agentMsg.value.agentDisplayName = data.agent_display_name;
                }
              }
              if (data.rag_retrieval) {
                ragRetrievalMeta.value = data.rag_retrieval;
              }
              if (data.permission_notice) {
                agentMsg.value.permissionNotice = data.permission_notice;
              }
            }

            // Handle Status
            if (data.status === "generating") {
              // Only stop thinking if we already have content, otherwise wait for first content chunk
              if (agentMsg.value.content) {
                 agentMsg.value.isThinking = false;
                 if (thoughtTimer) {
                   clearInterval(thoughtTimer);
                   thoughtTimer = null;
                 }
              }
            } else if (data.type === "error" || data.status === "error") {
              agentMsg.value.isThinking = false;
              if (thoughtTimer) {
                clearInterval(thoughtTimer);
                thoughtTimer = null;
              }
              applyStreamErrorMessage(agentMsg.value, data);
            }
            if (data.intent) {
              agentMsg.value.intent = data.intent;
            }
          } catch (e) {
            console.warn("Failed to parse SSE chunk", e);
          }
      }
    }

    // Render final content as markdown - REMOVED to fix Chart Rendering
    // agentMsg.value.content = renderMarkdown(agentMsg.value.content);
    agentMsg.value.isThinking = false;
    if (thoughtTimer) {
      clearInterval(thoughtTimer);
      thoughtTimer = null;
    }

    // Final cleanup of pending logs
    if (agentMsg.value.logs) {
      agentMsg.value.logs.forEach(log => {
        if (log.status === 'pending' && log.category !== 'permission') {
          log.status = 'success'; // Assume success if stream finished normally
        }
      });
    }

    // Refresh history sidebar
    fetchHistory();
  } catch (error: any) {
    if (error.name === "AbortError") return;

    agentMsg.value.content += `\n[异常中断: ${error.message}]`;
    agentMsg.value.isThinking = false;
    if (thoughtTimer) {
      clearInterval(thoughtTimer);
      thoughtTimer = null;
    }

    // Mark pending logs as error
    if (agentMsg.value.logs) {
      agentMsg.value.logs.forEach(log => {
        if (log.status === 'pending') {
          log.status = 'error';
        }
      });
    }
  } finally {
    isProcessing.value = false;
    void refreshCurrentRunStatus();
    void refreshDebugContextUsage();
    void refreshDebugContextCompactions(true);
  }
};

const addRealLog = (msg: Message, data: any) => {
  if (!msg.logs) msg.logs = [];
  const logId = data.id || Date.now() + Math.random();
  const existingLog = msg.logs.find((l) => l.id === logId);

  if (existingLog) {
    if (data.parent_id !== undefined) existingLog.parent_id = data.parent_id;
    existingLog.status = data.status || "success";
    existingLog.title = data.title || existingLog.title;
    existingLog.details = data.details || existingLog.details;
    if (data.error_reason !== undefined) existingLog.error_reason = data.error_reason;
    if (data.isDebug !== undefined) existingLog.isDebug = data.isDebug;
    if (data.isRouter !== undefined) existingLog.isRouter = data.isRouter;
    if (data.category !== undefined) existingLog.category = data.category as any;
    if (data.subagent !== undefined) existingLog.subagent = normalizeSubagentTraceMeta(data.subagent);
    if (data.tool_name !== undefined) existingLog.tool_name = data.tool_name;
    if (data.file_metadata !== undefined) existingLog.file_metadata = data.file_metadata;
    if (data.resolution_status !== undefined) existingLog.resolution_status = data.resolution_status;
    if (data.execution_time_ms !== undefined) existingLog.execution_time_ms = data.execution_time_ms;
    if (data.started_at !== undefined) existingLog.started_at = data.started_at;
    if (data.row_filter_applied === true) existingLog.rowFilterApplied = true;
    syncProcessTimelineLog(msg, { ...data, id: logId }, existingLog.category);
  } else {
    // Categorization Logic for new logs
    let inferredCategory = (data.category as any) || 'default';
    if (inferredCategory === 'default') {
      const titleLower = (data.title || "").toLowerCase();
      if (titleLower.includes("路由") || titleLower.includes("router")) inferredCategory = 'router';
      else if (titleLower.includes("sql") || titleLower.includes("数据")) inferredCategory = 'sql';
      else if (titleLower.includes("knowledge") || titleLower.includes("知识") || titleLower.includes("检索") || titleLower.includes("分析")) inferredCategory = 'knowledge';
      else if (titleLower.includes("tool") || titleLower.includes("工具")) inferredCategory = 'tool';
      else if (titleLower.includes("intent") || titleLower.includes("意图")) inferredCategory = 'intent';
      else if (titleLower.includes("model") || titleLower.includes("模型")) inferredCategory = 'model';
      else if (titleLower.includes("permission") || titleLower.includes("权限")) inferredCategory = 'permission';
    }

    const log: LogEntry = {
      id: logId,
      parent_id: data.parent_id,
      title: data.title || "Processing",
      details: data.details || "",
      status: data.status || "success",
      error_reason: data.error_reason,
      isExpanded: false,
      isDebug: data.isDebug,
      isRouter: data.isRouter,
      category: inferredCategory,
      model: data.model,
      temperature: data.temperature,
      subagent: normalizeSubagentTraceMeta(data.subagent),
      tool_name: data.tool_name,
      file_metadata: data.file_metadata,
      resolution_status: data.resolution_status,
      execution_time_ms: data.execution_time_ms ?? null,
      started_at: data.started_at ?? null,
      rowFilterApplied: data.row_filter_applied === true,
    };
    msg.logs.push(log);
    syncProcessTimelineLog(msg, { ...data, id: logId, category: inferredCategory }, inferredCategory);
  }
};

const submitPendingExternalExecution = async (msg: Message) => {
  const pending = msg.pendingExternalExecution;
  if (!pending || pending.status !== "pending" || pending.isSubmitting) return;
  pending.isSubmitting = true;
  isProcessing.value = true;
  msg.isThinking = true;
  msg.thoughtStartTime = Date.now();
  msg.thoughtDuration = "0.0";
  msg.thinkingText = "正在提交外部执行结果...";

  try {
    await resumeExternalExecutionStream({
      requestId: pending.external_execution_request_id,
      toolCall: pending.tool_call,
      output: pending.outputDraft || "(empty external result)",
      headers: {
        "X-API-Key": localStorage.getItem("api_key") || "",
      },
      onEvent: (data) => applyPermissionStreamEvent(msg, data),
    });
  } catch (error: any) {
    pending.status = "error";
    msg.content += `\n[外部执行恢复失败: ${error.message || "Unknown error"}]`;
  } finally {
    pending.isSubmitting = false;
    isProcessing.value = msg.pendingExternalExecution?.status === "pending" || msg.pendingPermission?.status === "pending";
    msg.isThinking = false;
    if (thoughtTimer) {
      clearInterval(thoughtTimer);
      thoughtTimer = null;
    }
    if (msg.logs) {
      msg.logs.forEach((log) => {
        if (log.status === "pending" && log.category !== "permission" && log.category !== "external") {
          log.status = "success";
        }
      });
    }
    scrollToBottom();
  }
};

const applyPermissionStreamEvent = (msg: Message, data: any) => {
  // 无条件写入 trace_id —— 不作为门控（恢复流 permission_result/log 等多不带
  // trace_id，若作为 gate 会整体丢帧，见 EmbedChat 同函数做法）。
  applyStreamTraceId(msg, data);
  if (data.agent_name && !msg.agentName) msg.agentName = data.agent_name;
  if (data.agent_display_name && !msg.agentDisplayName) msg.agentDisplayName = data.agent_display_name;

  if (data?.type === "run_status") {
    // 恢复流末尾的 run_status 终态：status 为 success / rejected / error 等真实结果，
    // 按终态映射，避免把“用户拒绝/执行失败”错误显示为已完成。
    if (msg.pendingPermission) {
      msg.pendingPermission.status =
        data.status === "rejected" || data.status === "denied"
          ? "rejected"
          : data.status === "error" || data.status === "failed"
            ? "error"
            : "approved";
    }
    if (msg.pendingExternalExecution) {
      msg.pendingExternalExecution.status =
        data.status === "error" || data.status === "failed" ? "error" : "completed";
    }
    msg.isThinking = false;
    markOutputCompleted();
    if (thoughtTimer) {
      clearInterval(thoughtTimer);
      thoughtTimer = null;
    }
    if (data.status === "success") {
      (msg as any).status = "success";
    }
    return;
  }

  {
    if (dispatchAgentscopeStreamEvent(msg, data, addRealLog, messages.value)) {
      if (data.type === "error") {
        if (msg.pendingPermission) msg.pendingPermission.status = "error";
        if (msg.pendingExternalExecution) msg.pendingExternalExecution.status = "error";
        msg.isThinking = false;
        applyStreamErrorMessage(msg, data);
      } else if (
        data.content &&
        data.type !== "reasoning_content" &&
        data.type !== "process_narration" &&
        data.type !== "process_narration_commit" &&
        data.type !== "process_narration_promote" &&
        data.type !== "answer_delta" &&
        data.type !== "retraction"
      ) {
        const piece = sanitizeStreamContent(String(data.content));
        if (piece) {
          if (
            msg.isThoughtExpanded
            && !msg.content
            && !timelineHasPending(msg.processTimeline)
          ) {
            msg.isThoughtExpanded = false;
          }
          appendAssistantBodyDelta(msg, piece);
          if (msg.isThinking) {
            msg.isThinking = false;
            if (thoughtTimer) {
              clearInterval(thoughtTimer);
              thoughtTimer = null;
            }
          }
        }
      }
      if (data.status === "generating" && msg.content) msg.isThinking = false;
      else if (data.status === "error") {
        msg.isThinking = false;
        applyStreamErrorMessage(msg, data);
      }
      if (data.intent) msg.intent = data.intent;
      return;
    }

    if (data.type === "log") {
      addRealLog(msg, data);
    } else if (data.type === "router_log") {
      const thoughtText = data.thought || "No reasoning provided.";
      const agentName = data.selected_agent || "Unknown";
      const conf = data.confidence !== undefined ? `(置信度: ${data.confidence})` : "";
      addRealLog(msg, {
        id: data.id || "route:target_selection",
        parent_id: data.parent_id || "route:target_config",
        title: "智能路由决策",
        details: `**思考过程 (Chain of Thought):**\n${thoughtText}\n\n**最终选择:** ${agentName} ${conf}`,
        status: "success",
        isDebug: true,
        isRouter: true,
      });
    } else if (data.type === "debug" && data.subtype === "raw_prompt") {
      msg.rawPrompt = data.data;
      addRealLog(msg, {
        title: "Debug: Raw Prompt Captured",
        details: 'Click "Raw Prompt" button to view.',
        status: "success",
        isDebug: true,
      });
    } else if (mergeStreamCitations(msg, data)) {
      // Citations are merged and de-duplicated by the shared stream normalizer.
    } else if (data.type === "context") {
      addRealLog(msg, {
        title: "✨ Context Updated",
        details: JSON.stringify(data.data, null, 2),
        status: "success",
      });
      if (data.data) {
        agentContext.value = { ...agentContext.value, ...data.data };
      }
    } else if (applyChatBIInsightEvent(msg, data) || applyChatBIMetadataGuideEvent(msg, data) || applyAgentHandoffEvent(msg, data)) {
      return;
    } else if (data.type === "thinking" && data.status === "continuing") {
      msg.isThinking = true;
    } else if (data.type === "meta") {
      if (data.agent_name) msg.agentName = data.agent_name;
      if (data.agent_type) msg.agentType = data.agent_type;
      if (data.agent_display_name) msg.agentDisplayName = data.agent_display_name;
      if (data.rag_retrieval) ragRetrievalMeta.value = data.rag_retrieval;
      if (data.permission_notice) msg.permissionNotice = data.permission_notice;
    } else if (data.type === "error") {
      if (msg.pendingPermission) msg.pendingPermission.status = "error";
      msg.isThinking = false;
      applyStreamErrorMessage(msg, data);
    } else if (data.type === "retraction") {
      msg.content = stripInternalContextBlocks(String(data.content || ""));
      if (data.final !== false) {
        msg.isThinking = false;
        if (thoughtTimer) {
          clearInterval(thoughtTimer);
          thoughtTimer = null;
        }
      }
    } else if (
      data.content &&
      data.type !== "reasoning_content" &&
      data.type !== "process_narration" &&
      data.type !== "process_narration_commit" &&
      data.type !== "process_narration_promote" &&
      data.type !== "answer_delta" &&
      data.type !== "retraction"
    ) {
      const piece = sanitizeStreamContent(String(data.content));
      if (piece) {
        if (
          msg.isThoughtExpanded
          && !msg.content
          && !timelineHasPending(msg.processTimeline)
        ) {
          msg.isThoughtExpanded = false;
        }
        appendAssistantBodyDelta(msg, piece);
        if (msg.isThinking) {
          msg.isThinking = false;
          if (thoughtTimer) {
            clearInterval(thoughtTimer);
            thoughtTimer = null;
          }
        }
      }
    }
    if (data.status === "generating" && msg.content) msg.isThinking = false;
    else if (data.status === "error") {
      msg.isThinking = false;
      applyStreamErrorMessage(msg, data);
    }
    if (data.intent) msg.intent = data.intent;
    return;
  }

};

const submitBusinessConfirmation = async (
  msg: Message,
  payload: { confirmed: boolean; fields: BusinessConfirmationField[] },
) => {
  const card = msg.businessConfirmation;
  if (!card || card.status !== "pending" || isProcessing.value) return;
  const content = buildBusinessConfirmationUserMessage(
    payload.confirmed,
    card.confirmation_id,
    payload.fields,
  );
  card.fields = payload.fields.map((field) => ({ ...field }));
  card.status = "submitted";
  card.decision = payload.confirmed ? "confirmed" : "cancelled";
  userInput.value = content;
  await sendMessage();
};

const submitUserQuestion = async (
  msg: Message,
  payload: { selectedOptionIds: string[]; customInput: string; cancelled: boolean },
) => {
  const card = msg.userQuestion;
  if (!card || card.status !== "pending" || isProcessing.value) return;
  const content = buildUserQuestionUserMessage(
    card.question_id,
    payload.selectedOptionIds,
    payload.customInput,
    payload.cancelled,
    card.question,
    card.options,
  );
  card.selected_option_ids = [...payload.selectedOptionIds];
  card.custom_input = payload.customInput;
  card.status = payload.cancelled ? "cancelled" : "submitted";
  userInput.value = content;
  await sendMessage();
};

const confirmPendingPermission = async (msg: Message, confirmed: boolean) => {
  const pending = msg.pendingPermission;
  if (!pending || pending.status !== "pending" || pending.isSubmitting) return;
  pending.isSubmitting = true;
  isProcessing.value = true;
  if (confirmed) {
    msg.isThinking = true;
    msg.thoughtStartTime = Date.now();
    msg.thoughtDuration = "0.0";
    msg.thinkingText = "正在继续执行...";
  }

  try {
    const response = await fetch(`/api/v1/chat/permissions/${pending.permission_request_id}/confirm`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": localStorage.getItem("api_key") || "",
      },
      body: JSON.stringify({ confirmed }),
    });
    if (!response.ok) throw new Error(response.statusText);
    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    if (!reader) throw new Error("No response body");
    const parser = createSseLineParser();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const dataLines = parser.feed(decoder.decode(value, { stream: true }));
      for (const dataStr of dataLines) {
        if (dataStr === "[DONE]") continue;
        applyPermissionStreamEvent(msg, JSON.parse(dataStr));
      }
      scrollToBottom();
    }
    for (const dataStr of parser.flush()) {
      if (dataStr !== "[DONE]") applyPermissionStreamEvent(msg, JSON.parse(dataStr));
    }
  } catch (error: any) {
    pending.status = "error";
    msg.isThinking = false;
    msg.content += `\n[工具确认失败: ${error.message || "Unknown error"}]`;
  } finally {
    pending.isSubmitting = false;
    isProcessing.value = msg.pendingPermission?.status === "pending";
    msg.isThinking = false;
    if (thoughtTimer) {
      clearInterval(thoughtTimer);
      thoughtTimer = null;
    }
    if (msg.logs) {
      msg.logs.forEach(log => {
        if (log.status === "pending" && log.category !== "permission") {
          log.status = "success";
        }
      });
    }
  }
};

onUnmounted(() => {
  if (abortController) abortController.abort();
});
</script>

<template>
  <div
    class="h-full flex bg-gray-100 overflow-hidden"
    :class="{ 'fixed inset-0 z-[99] w-screen h-screen': isFullScreen }"
  >
    <!-- Trace Log Viewer Component -->
    <TraceLogViewer
      :visible="showFullLogViewer"
      :trace-id="activeTraceId"
      @close="showFullLogViewer = false"
    />

    <!-- Session Preview Modal (New Feature) -->
    <div
      v-if="showSessionPreview"
      class="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 backdrop-blur-sm sm:p-4"
      @click.self.stop="showSessionPreview = false"
    >
        <div class="bg-white dark:bg-gray-800 w-full flex flex-col overflow-hidden animate-fade-in-up border border-gray-200 dark:border-gray-700 shadow-2xl transition-all duration-300 max-w-3xl h-[80vh] rounded-xl">
            <!-- Header -->
            <div class="px-4 py-3 sm:px-6 sm:py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50 flex-shrink-0">
                <div class="flex items-center gap-3">
                    <h3 class="text-sm sm:text-lg font-black text-gray-800 dark:text-gray-100 truncate">会话概览 (Session Preview)</h3>
                    <span v-if="conversationTurns.length" class="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs font-bold">{{ conversationTurns.length }} Rounds</span>
                </div>
                <div class="flex items-center gap-2 flex-shrink-0">
                    <button
                        v-if="conversationTurns.length > 0"
                        @click.stop="continueChatFromTrace"
                        class="flex items-center space-x-1.5 px-3 py-1.5 bg-primary/10 text-primary hover:bg-primary hover:text-white rounded-lg transition-all text-xs font-black border border-primary/20"
                    >
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
                        <span>继续调试</span>
                    </button>
                    <div class="w-px h-4 bg-gray-300 dark:bg-gray-600 mx-1"></div>
                    <button @click.stop="showSessionPreview = false" class="p-2 text-gray-500 hover:text-gray-800"><svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg></button>
                </div>
            </div>

            <!-- Content -->
            <div class="flex-1 overflow-y-auto p-4 sm:p-6 bg-gray-50 dark:bg-gray-900 custom-scrollbar">
                <div v-if="loadingTrace" class="flex flex-col items-center justify-center h-full text-gray-400 py-20">
                    <svg class="w-10 h-10 animate-spin mb-3 text-primary" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    <p class="text-xs font-bold uppercase tracking-widest">加载会话记录...</p>
                </div>
                <div v-else-if="conversationTurns.length > 0" class="space-y-6 pb-10">
                    <div v-for="(turn, tIdx) in conversationTurns" :key="turn.id"
                         class="bg-white dark:bg-gray-800 p-4 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-sm relative overflow-hidden"
                         :class="{'ring-2 ring-primary/20': turn.trace_id === activeTraceId}"
                    >
                        <div class="flex justify-between items-center mb-4">
                            <span class="text-[10px] font-black text-gray-400 uppercase tracking-widest flex items-center gap-1.5">
                                <span class="w-2 h-2 rounded-full bg-blue-500"></span>
                                回合 #{{ tIdx + 1 }}
                            </span>
                            <span class="text-[9px] text-gray-400 font-mono">{{ formatDate(turn.created_at) }}</span>
                        </div>
                        <div class="space-y-4">
                            <div>
                                <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1 opacity-70">用户</div>
                                <div class="text-gray-800 dark:text-gray-200 text-sm font-bold bg-gray-50 p-2 rounded-lg border border-gray-100">{{ turn.query }}</div>
                            </div>
                            <div>
                                <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1 opacity-70">智能体</div>
                                <div class="text-gray-600 dark:text-gray-300 text-xs sm:text-sm"><MessageRenderer :content="stripInternalContextBlocks(turn.summary || 'N/A')" @open-canvas="handleOpenCanvas" /></div>
                            </div>
                        </div>

                        <!-- Actions & Steps -->
                        <div class="mt-4 pt-3 border-t border-gray-50">
                            <div class="flex items-center justify-between">
                                <button @click="toggleTurnSteps(turn)" class="flex items-center p-2 rounded-lg hover:bg-gray-50 transition-all group">
                                    <span class="text-[10px] font-black text-gray-500 group-hover:text-primary transition-colors uppercase tracking-widest">执行步骤 (Steps)</span>
                                    <div class="flex items-center ml-2">
                                        <div v-if="turn.loading" class="w-3 h-3 border-2 border-primary/30 border-t-primary rounded-full animate-spin mr-2"></div>
                                        <svg class="w-4 h-4 text-gray-400 transform transition-transform duration-300" :class="{ 'rotate-180': turn.isExpanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" /></svg>
                                    </div>
                                </button>

                                <!-- Link to Full Trace Viewer -->
                                <button
                                    @click.stop="openFullLogs(turn.trace_id)"
                                    class="px-3 py-1.5 text-[10px] font-bold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded border border-indigo-100 transition-colors uppercase tracking-widest flex items-center gap-1"
                                >
                                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" /></svg>
                                    完整链路
                                </button>
                            </div>

                            <div v-show="turn.isExpanded" class="mt-3 pl-4 border-l-2 border-gray-100 space-y-3 animate-fade-in">
                                <div v-if="turn.steps && turn.steps.length > 0" class="space-y-3">
                                    <div v-for="(step, sIdx) in turn.steps" :key="sIdx" class="bg-white p-3 rounded-xl border border-gray-100 shadow-sm text-xs">
                                        <div class="flex justify-between items-center mb-2">
                                            <span class="text-[9px] font-black px-1.5 py-0.5 rounded uppercase bg-blue-100 text-blue-700">{{ step.event_type }}</span>
                                            <span class="text-[8px] text-gray-400 font-mono">{{ step.execution_time_ms?.toFixed(0) }}ms</span>
                                        </div>
                                        <div v-if="step.tool_output?.content" class="text-gray-600 leading-relaxed break-words">{{ step.tool_output.content.slice(0, 300) + (step.tool_output.content.length > 300 ? '...' : '') }}</div>
                                    </div>
                                </div>
                                <div v-else-if="!turn.loading" class="py-2 text-center text-[10px] text-gray-400 italic">暂无记录</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Left Sidebar: History -->
    <ChatHistorySidebar
      v-model:visible="showHistorySidebar"
      v-model="historyKeyword"
      :loading="loadingHistory"
      :history-list="groupedHistoryList"
      :active-trace-id="activeTraceId"
      @fetch-history="fetchHistory"
      @load-chat="openSessionPreview"
      @open-full-logs="openSessionPreview"
    />

    <!-- Center: Main Chat Area -->
    <div
      class="flex-1 flex flex-col bg-white shadow-sm overflow-hidden mr-px transition-[margin] duration-300 relative"
      :style="pinnedDrawerMarginStyle"
    >
      <!-- 模块说明提示（含一次性全屏引导，避免每次 toast） -->
      <div
        v-if="!isFullScreen"
        class="px-4 sm:px-6 py-2 bg-sky-50 border-b border-sky-100 flex items-start sm:items-center gap-2 flex-shrink-0"
      >
        <svg class="w-4 h-4 text-sky-600 shrink-0 mt-0.5 sm:mt-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <div class="flex-1 min-w-0 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <p class="text-xs sm:text-sm text-sky-800 leading-relaxed">
            当前是<strong class="font-semibold">智能体开发调试</strong>模块，可展示更详尽的运行日志与链路信息，便于开发排查与调试。
            <template v-if="showFullscreenTip">
              建议使用右上角<strong class="font-semibold">全屏</strong>，调试视野更完整。
            </template>
          </p>
          <div v-if="showFullscreenTip" class="flex items-center gap-2 shrink-0">
            <button
              type="button"
              @click="enterFullScreenFromTip"
              class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold text-white bg-sky-600 hover:bg-sky-700 rounded-md transition-colors"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4M20 4l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
              </svg>
              进入全屏
            </button>
            <button
              type="button"
              @click="dismissFullscreenTip"
              class="text-xs text-sky-600/80 hover:text-sky-900 px-1.5 py-1 transition-colors"
            >
              知道了
            </button>
          </div>
        </div>
      </div>

      <!-- Header -->
      <div
        class="h-14 px-6 border-b border-gray-200 flex items-center justify-between bg-white flex-shrink-0"
      >
        <div class="flex items-center">
          <button
            @click="showHistorySidebar = !showHistorySidebar"
            class="mr-3 p-1.5 rounded-md transition-all flex items-center space-x-1.5"
            :class="
              !showHistorySidebar
                ? 'bg-primary/10 text-primary px-3 border border-primary/20 hover:bg-primary/20'
                : 'text-gray-500 hover:bg-gray-100'
            "
            :title="showHistorySidebar ? '收起历史' : '展开历史'"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                v-if="showHistorySidebar"
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M11 19l-7-7 7-7m8 14l-7-7 7-7"
              />
              <path
                v-else
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M13 5l7 7-7 7M5 5l7 7-7 7"
              />
            </svg>
            <span v-if="!showHistorySidebar" class="text-xs font-bold"
              >历史记录</span
            >
          </button>
          <svg
            class="w-5 h-5 text-primary mr-2"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"
            />
          </svg>
          <span class="font-medium text-gray-800"
            >智能体调试</span
          >
        </div>
        <div class="flex items-center space-x-4">
          <!-- 1. 全屏 -->
          <button
            @click="toggleFullScreen"
            class="flex items-center gap-1.5 transition-all"
            :class="isFullScreen
              ? 'bg-primary/10 text-primary border border-primary/20 px-2.5 py-1.5 rounded-lg hover:bg-primary/20'
              : showFullscreenTip
                ? 'bg-sky-50 text-sky-700 border border-sky-200 px-2.5 py-1.5 rounded-lg hover:bg-sky-100 ring-2 ring-sky-200/60 animate-pulse'
                : 'text-gray-500 hover:text-blue-600 px-2 py-1.5 rounded-lg hover:bg-gray-50'"
            :title="isFullScreen ? '退出全屏（Esc）' : '全屏调试，获得更大视野'"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                v-if="!isFullScreen"
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4M20 4l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"
              />
              <path
                v-else
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M4 14h6v6m0-6l-6 6m16-6h-6v6m0-6l6 6M4 10h6V4m0 6L4 4m16 6h-6V4m0 6l6-6"
              />
            </svg>
            <span class="text-xs font-medium hidden sm:inline">{{ isFullScreen ? '退出全屏' : '全屏' }}</span>
          </button>

          <div class="h-4 w-px bg-gray-200"></div>

          <!-- 2. 清空 -->
          <button
            @click="clearHistory"
            class="text-gray-500 hover:text-red-600 transition-colors flex items-center space-x-1"
            title="清空会话"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
              />
            </svg>
          </button>

          <div class="h-4 w-px bg-gray-200"></div>

          <!-- 3. 运行逻辑 -->
          <button
            @click="showLogicFlowModal = true"
            class="text-gray-500 hover:text-blue-600 transition-colors flex items-center space-x-1"
            title="查看运行逻辑图"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 20l-5.447-2.724A2 2 0 013 15.492V4.508a2 2 0 011.553-1.944L9 2l6 2.724a2 2 0 011 1.732v10.984a2 2 0 01-1.553 1.944L9 20z"
              />
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 2v18M15 4v18"
              />
            </svg>
            <span class="text-xs font-medium">运行逻辑</span>
          </button>

          <div class="h-4 w-px bg-gray-200"></div>

          <!-- 4. 导出 Markdown -->
          <button
            @click="exportChat"
            class="p-1.5 rounded-md text-gray-500 hover:bg-gray-100 transition-colors flex items-center space-x-1"
            title="导出对话 (Markdown)"
          >
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
          </button>

          <div class="h-4 w-px bg-gray-200"></div>

          <!-- 5. 调试配置 (齿轮) 放最后 -->
          <button
            @click="showConfigPanel = !showConfigPanel"
            class="p-1.5 rounded-md transition-colors flex items-center space-x-1"
            :class="
              showConfigPanel
                ? 'bg-primary/10 text-primary'
                : 'text-gray-500 hover:bg-gray-100'
            "
            :title="showConfigPanel ? '收起配置' : '展开配置'"
          >
            <svg
              class="w-6 h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
              />
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
              />
            </svg>
          </button>
        </div>
      </div>

      <!-- Mode Selector Bar -->
      <div
        class="px-6 py-2 bg-gray-50 border-b border-gray-200 flex items-center space-x-4 flex-shrink-0"
      >
        <!-- Mode Toggle -->
        <div class="flex bg-gray-200 p-1 rounded-lg">
          <button
            @click="
              debugMode = 'auto';
              agentParams.agent_id = null;
            "
            class="px-3 py-1.5 text-xs font-medium rounded-md transition-all"
            :class="
              debugMode === 'auto'
                ? 'bg-white text-primary shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            "
          >
            🤖 智能委派 (Auto)
          </button>
          <button
            @click="
              debugMode = 'specific';
              if (agents.length) agentParams.agent_id = agents[0].id;
            "
            class="px-3 py-1.5 text-xs font-medium rounded-md transition-all"
            :class="
              debugMode === 'specific'
                ? 'bg-white text-primary shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            "
          >
            🎯 指定智能体 (Specific)
          </button>
        </div>

        <!-- Custom Dropdown (Visible only in Specific Mode) -->
        <div v-if="debugMode === 'specific'" ref="agentDropdownRef" class="relative z-30">
          <!-- Dropdown Trigger Button -->
          <button
            @click="showAgentDropdown = !showAgentDropdown"
            class="flex items-center justify-between w-64 px-3 py-1.5 text-xs bg-white border border-gray-300 rounded-lg shadow-sm hover:border-gray-400 focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary transition-all text-left"
          >
            <div class="flex items-center space-x-2 min-w-0 flex-1">
              <!-- Selected Agent Avatar -->
              <div class="flex-shrink-0 w-5 h-5 rounded bg-gray-50 flex items-center justify-center border border-gray-100 overflow-hidden text-xs">
                <img
                  v-if="selectedAgent?.avatar_url && (selectedAgent.avatar_url.startsWith('http') || selectedAgent.avatar_url.startsWith('/') || selectedAgent.avatar_url.startsWith('data:'))"
                  :src="selectedAgent.avatar_url"
                  class="w-full h-full object-cover"
                />
                <span v-else-if="selectedAgent?.avatar_url" class="text-xs">{{ selectedAgent.avatar_url }}</span>
                <span v-else class="text-xs">{{ selectedAgent?.is_system ? '🔒' : '👤' }}</span>
              </div>
              <span class="font-medium text-gray-700 truncate">
                {{ selectedAgent?.display_name || '选择智能体' }}
              </span>
            </div>
            <!-- Arrow -->
            <svg
              class="w-4 h-4 text-gray-400 ml-1 transform transition-transform duration-200"
              :class="{ 'rotate-180': showAgentDropdown }"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          <!-- Dropdown Card Menu -->
          <transition name="slide-up">
            <div
              v-show="showAgentDropdown"
              class="absolute mt-1 left-0 z-30 w-[360px] max-h-80 overflow-y-auto bg-white border border-gray-200 rounded-xl shadow-xl py-1 px-1 custom-scrollbar origin-top-left"
            >
              <div
                v-for="agent in agents"
                :key="agent.id"
                @click="
                  agentParams.agent_id = agent.id;
                  showAgentDropdown = false;
                "
                class="my-1 p-2 rounded-lg border transition-all cursor-pointer flex items-start space-x-2.5"
                :class="
                  agentParams.agent_id === agent.id
                    ? 'border-primary/40 bg-primary/5 ring-1 ring-primary/5'
                    : 'border-transparent hover:bg-gray-50'
                "
              >
                <!-- Avatar -->
                <div
                  class="flex-shrink-0 w-7 h-7 rounded bg-gray-50 flex items-center justify-center text-sm border border-gray-100 overflow-hidden"
                  :class="agentParams.agent_id === agent.id ? 'bg-primary/10 border-primary/20' : ''"
                >
                  <img
                    v-if="agent.avatar_url && (agent.avatar_url.startsWith('http') || agent.avatar_url.startsWith('/') || agent.avatar_url.startsWith('data:'))"
                    :src="agent.avatar_url"
                    class="w-full h-full object-cover"
                  />
                  <span v-else-if="agent.avatar_url" class="text-sm">{{ agent.avatar_url }}</span>
                  <span v-else class="text-sm">{{ agent.is_system ? '🔒' : '👤' }}</span>
                </div>
                <!-- Info -->
                <div class="flex-1 min-w-0">
                  <div class="flex items-center justify-between">
                    <span
                      class="text-xs font-bold text-gray-800 truncate"
                      :class="agentParams.agent_id === agent.id ? 'text-primary' : ''"
                    >
                      {{ agent.display_name }}
                    </span>
                    <span
                      v-if="agent.is_system"
                      class="text-[8px] text-gray-400 font-mono scale-90 origin-right border border-gray-200 px-1 rounded bg-gray-50"
                      >SYSTEM</span
                    >
                  </div>
                  <div class="text-[9px] text-gray-400 font-mono truncate mt-0.5">
                    {{ agent.name }}
                  </div>
                  <div
                    class="text-[10px] text-gray-500 line-clamp-2 mt-1 leading-relaxed break-words"
                    :title="agent.description"
                  >
                    {{ agent.description || '暂无备注说明信息' }}
                  </div>
                </div>
              </div>
            </div>
          </transition>
        </div>

        <div class="text-xs text-gray-400 border-l pl-3 ml-2">
          {{
            debugMode === "auto"
              ? "系统将根据您的问题自动选择最合适的 Agent"
              : "强制请求发送给当前选中的 Agent"
          }}
        </div>
      </div>

      <!-- Chat History -->
      <div
        ref="messagesContainer"
        @scroll="handleScroll"
        class="flex-1 overflow-y-auto p-6 space-y-6 bg-gray-50 custom-scrollbar"
      >
        <div
          v-for="msg in displayMessages"
          :key="msg.id"
          class="flex w-full"
          :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
        >
          <!-- User Message -->
          <div
            v-if="msg.role === 'user'"
            class="flex space-x-3 items-start flex-row-reverse space-x-reverse max-w-[85%] group"
          >
            <div
              class="w-10 h-10 rounded-full bg-gray-200 flex-shrink-0 flex items-center justify-center text-gray-500 shadow-sm border border-white"
            >
              <svg
                class="w-6 h-6"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="1.5"
                  d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
                />
              </svg>
            </div>

            <!-- Editing Mode -->
            <div v-if="editingMsgId === msg.id" class="w-full flex flex-col items-end space-y-2">
               <textarea
                  v-model="editContent"
                  class="w-full p-3 border border-primary/30 rounded-lg shadow-sm focus:ring-2 focus:ring-primary focus:border-primary text-sm min-h-[80px]"
                ></textarea>
                <div class="flex space-x-2">
                  <button
                    @click="cancelEdit"
                    class="px-3 py-1 text-xs text-gray-500 bg-gray-100 hover:bg-gray-200 rounded"
                  >取消</button>
                  <button
                    @click="saveAndResend"
                    class="px-3 py-1 text-xs text-white bg-primary hover:bg-primary-dark rounded"
                  >发送</button>
                </div>
            </div>

            <!-- Normal Mode -->
            <div v-else class="flex flex-col items-end">
              <div
                class="bg-primary text-white px-5 py-3.5 rounded-2xl rounded-tr-none shadow-sm text-sm leading-relaxed text-left relative"
              >
                <template v-for="parts in [splitUserMessageContent(msg.content)]" :key="'user-parts'">
                  <template v-if="parts.hasContext">
                    <MessageRenderer v-if="parts.userPart" :content="parts.userPart" @open-canvas="handleOpenCanvas" />
                    <div v-if="parts.userPart" class="my-2.5 border-t border-white/30" role="separator" />
                    <details class="group/sys mt-2 text-[10px] text-white/70 select-none">
                      <summary class="cursor-pointer hover:text-white flex items-center gap-1 font-semibold focus:outline-none list-none [&::-webkit-details-marker]:hidden">
                        <svg class="w-3 h-3 transform transition-transform duration-200 group-open/sys:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                        </svg>
                        <span>⚙️ 附加系统元数据说明 (点击展开)</span>
                      </summary>
                      <div class="mt-1.5 p-2 rounded bg-black/15 text-white/85 font-mono text-[10px] leading-relaxed whitespace-pre-wrap break-all select-text selection:bg-white/20">
                        {{ parts.contextPart }}
                      </div>
                    </details>
                  </template>
                  <MessageRenderer v-else :content="msg.content" @open-canvas="handleOpenCanvas" />
                </template>

                <!-- Attached Files In Bubble -->
                <div v-if="msg.files && msg.files.length > 0" class="mt-2 space-y-2 border-t border-white/20 pt-2">
                    <div v-for="(file, fIdx) in msg.files" :key="fIdx" class="flex items-center bg-white/10 rounded-lg p-1.5 max-w-xs select-none">
                        <!-- Image Thumb -->
                        <AttachmentImageThumb
                          v-if="isImageFile(file)"
                          :file="file"
                          clickable
                          class="mr-2 border-white/10"
                          @click="openImagePreview"
                        />
                        <!-- Skill Icon -->
                        <div v-else-if="file.type === 'skill'" class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2 font-mono">
                            ⚙️
                        </div>
                        <!-- Metadata Dataset Icon -->
                        <div v-else-if="file.type === 'metadata_dataset'" class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2">
                            📊
                        </div>
                        <!-- Knowledge Base Icon -->
                        <div v-else-if="file.type === 'knowledge_base'" class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2">
                            📚
                        </div>
                        <!-- Memory Icon -->
                        <div v-else-if="file.type === 'memory'" class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2">
                            🧠
                        </div>
                        <!-- File Icon -->
                        <div v-else class="w-8 h-8 rounded bg-white/20 flex items-center justify-center text-white text-sm flex-shrink-0 mr-2">
                            📄
                        </div>
                        <div class="flex-1 min-w-0 flex flex-col">
                            <span v-if="file.type === 'skill' || file.type === 'knowledge_base' || file.type === 'metadata_dataset' || file.type === 'memory'" class="text-xs font-bold text-white truncate">{{ file.filename }}</span>
                            <a v-else :href="file.url" target="_blank" class="text-xs font-bold text-white hover:underline truncate">{{ file.filename }}</a>
                            <span class="text-[9px] text-white/70 font-mono">
                                {{
                                    file.type === 'skill' ? '生态技能' :
                                    file.type === 'knowledge_base' ? '知识库' :
                                    file.type === 'metadata_dataset' ? '数据集' :
                                    file.type === 'memory' ? '记忆记录' :
                                    formatBytes(file.size)
                                }}
                            </span>
                        </div>
                    </div>
                </div>
              </div>
              <!-- User Actions -->
              <div
                class="flex items-center space-x-2 mt-1 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <!-- Edit Button -->
                <button
                  @click="startEdit(msg)"
                  class="text-xs text-gray-400 hover:text-gray-600 flex items-center space-x-1 transition-colors"
                  :disabled="isProcessing"
                >
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                     <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                  <span>编辑</span>
                </button>

                <button
                  @click="copyContent(msg.content, $event)"
                  class="text-xs text-gray-400 hover:text-gray-600 flex items-center space-x-1 transition-colors"
                >
                  <svg
                    class="w-3 h-3"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                    />
                  </svg>
                  <span>复制</span>
                </button>
              </div>
            </div>
          </div>

          <!-- System Message / Separator -->
          <div
            v-else-if="msg.role === 'system'"
            class="w-full flex flex-col items-center justify-center my-6"
          >
             <span v-if="msg.timestamp" class="text-xs text-gray-400 dark:text-gray-500 font-medium tracking-wide mb-2">{{ msg.timestamp }}</span>
            <div class="flex items-center space-x-3 opacity-60">
              <div class="h-px w-16 bg-gray-300 dark:bg-gray-600"></div>
              <span class="text-xs text-gray-400 dark:text-gray-500 font-medium tracking-wide">{{ msg.content }}</span>
              <div class="h-px w-16 bg-gray-300 dark:bg-gray-600"></div>
            </div>
          </div>

          <!-- Agent Message -->
          <div v-else class="flex space-x-3 items-start max-w-[90%] group">
            <div
              class="w-10 h-10 rounded-full bg-blue-600 flex-shrink-0 flex items-center justify-center text-white shadow-md border border-blue-100"
            >
              <svg
                class="w-6 h-6"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="1.5"
                  d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"
                />
              </svg>
            </div>
            <div class="flex-1 space-y-2 min-w-0">
              <!-- Actions (Unified Toolbar) -->
              <div v-if="!msg.isGreeting" class="flex flex-wrap items-center gap-2 mb-2">
                <!-- Agent Badge (Smart Routing State) -->
                <div
                  class="flex items-center space-x-1 px-2 py-1 text-xs font-medium rounded-md select-none transition-all duration-300 border"
                  :class="msg.agentName
                    ? 'text-blue-600 bg-blue-50 border-blue-100'
                    : 'text-gray-400 bg-gray-50 border-gray-100 italic animate-pulse'"
                >
                  <svg
                    class="w-2.5 h-2.5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"
                    />
                  </svg>
                  <span>{{ getAgentDisplayName(msg) ? `${getAgentDisplayName(msg)} · ${String(msg.agentName || '').startsWith('sys_') ? '系统指令' : '为您服务'}` : (msg.agentName || '智能调度中...') }}</span>
                </div>

                <!-- Full Logs -->
                <button
                  v-if="msg.trace_id && !msg.isThinking"
                  @click="openFullLogs(msg.trace_id)"
                  class="flex items-center space-x-1 px-2 py-1 text-xs font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-100 rounded-md transition-colors"
                >
                  <svg
                    class="w-3 h-3"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"
                    />
                  </svg>
                  <span>完整日志</span>
                </button>

                <!-- Prompt -->
                <button
                  v-if="msg.rawPrompt"
                  @click="openRawPrompt(msg)"
                  class="flex items-center space-x-1 px-2 py-1 text-xs font-medium text-purple-600 bg-purple-50 hover:bg-purple-100 border border-purple-100 rounded-md transition-colors"
                  title="View Raw Prompt"
                >
                  <svg
                    class="w-3 h-3"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"
                    />
                  </svg>
                  <span>Prompt</span>
                </button>

                <!-- Regenerate Button -->
                <button
                  v-if="messages.indexOf(msg) === messages.length - 1 && !isProcessing && !msg.isThinking"
                  @click="regenerate(msg)"
                  class="flex items-center space-x-1 px-2 py-1 text-xs font-medium text-gray-500 bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded-md transition-colors"
                  title="重新生成"
                >
                  <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  <span>重新生成</span>
                </button>

                <!-- Token Usage -->
                <button
                  v-if="msg.prompt_tokens !== undefined || msg.completion_tokens !== undefined"
                  @click="openModelCallStats(msg)"
                  class="flex items-center space-x-1.5 px-2 py-1 text-[10px] font-mono text-gray-500 bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded-md transition-all duration-200 cursor-pointer active:scale-95"
                  title="点击查看详细的大模型调用统计指标"
                >
                  <span class="flex items-center space-x-0.5">
                    <span class="scale-90 text-[9px] text-gray-400/80">in:</span>
                    <span class="font-medium text-gray-500 dark:text-gray-400">{{ msg.prompt_tokens || 0 }}</span>
                  </span>
                  <span class="text-gray-300 dark:text-gray-700">/</span>
                  <span class="flex items-center space-x-0.5">
                    <span class="scale-90 text-[9px] text-gray-400/80">out:</span>
                    <span class="font-medium text-gray-500 dark:text-gray-400">{{ msg.completion_tokens || 0 }}</span>
                  </span>
                </button>
              </div>

              <!-- Agent Message Bubble (Unified Card Style) -->
              <div
                v-if="(!msg.isGreeting && (msg.logs && msg.logs.length > 0)) || (msg.processTimeline && msg.processTimeline.length > 0) || msg.content || msg.reasoningContent || msg.processNarration || msg.processNarrationPending || msg.isThinking || (msg.citations && msg.citations.length > 0)"
                :class="msg.content || (msg.citations && msg.citations.length) || msg.chatbiInsight
                  ? 'bg-gradient-to-br from-slate-50/80 to-white dark:from-slate-900/20 dark:to-gray-800 rounded-2xl rounded-tl-none border border-gray-200 dark:border-gray-700 border-l-4 border-l-primary/60 dark:border-l-primary/40 shadow-sm p-4 overflow-hidden'
                  : 'overflow-visible bg-transparent'"
              >
              <ChatExecutionTimeline
                v-model="msg.isThoughtExpanded"
                :timeline="msg.processTimeline"
                :logs="msg.logs"
                :reasoning-content="msg.reasoningContent"
                :process-narration="msg.processNarration"
                :process-narration-pending="msg.processNarrationPending"
                :is-thinking="msg.isThinking"
                :has-answer="Boolean(visibleStreamBody(msg))"
                :thinking-text="msg.thinkingText"
                :duration="msg.thoughtDuration"
                :skill-summary="getSkillFlowBadgesForMessage(msg, messages).length > 0 ? summarizeSkillFlowBadges(getSkillFlowBadgesForMessage(msg, messages)) : ''"
                :skill-badges="getSkillFlowBadgesForMessage(msg, messages)"
                :suppress-permission-logs="Boolean(msg.pendingPermission)"
                bordered
              />

              <ToolPermissionCard
                v-if="msg.pendingPermission"
                :payload="msg.pendingPermission"
                @submit="(confirmed) => confirmPendingPermission(msg, confirmed)"
              />
              <!-- External Tool Execution -->
              <div
                v-if="msg.pendingExternalExecution"
                class="mt-3 rounded-lg border border-sky-200 bg-sky-50/80 p-3 text-xs transition-all"
              >
                <div class="flex items-start gap-2">
                  <div class="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md bg-sky-100 text-sky-700">
                    <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4M14 4h6m0 0v6m0-6L10 14" />
                    </svg>
                  </div>
                  <div class="min-w-0 flex-1">
                    <!-- Card Header with Toggle -->
                    <div
                      class="flex items-center justify-between gap-2 cursor-pointer select-none group"
                      :title="(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')) ? '点击收起' : '点击展开'"
                      @click="msg.pendingExternalExecution.expanded = !(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending'))"
                    >
                      <div class="flex min-w-0 flex-1 items-center gap-2">
                        <div class="font-bold text-sky-900 shrink-0">
                          {{ msg.pendingExternalExecution.title || '外部工具执行' }}
                        </div>
                        <!-- Collapsed summary preview -->
                        <span
                          v-if="!(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')) && (msg.pendingExternalExecution.tool_call?.name || msg.pendingExternalExecution.details)"
                          class="min-w-0 flex-1 truncate text-[11px] text-sky-800/70 font-normal"
                        >
                          {{ msg.pendingExternalExecution.tool_call?.name ? `${msg.pendingExternalExecution.tool_call.name}${msg.pendingExternalExecution.tool_call.args ? ': ' + JSON.stringify(msg.pendingExternalExecution.tool_call.args) : ''}` : msg.pendingExternalExecution.details }}
                        </span>
                      </div>
                      <div class="flex items-center gap-1.5 shrink-0">
                        <span class="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase bg-sky-100 text-sky-700">
                          {{ formatExternalExecutionStatus(msg.pendingExternalExecution.status) }}
                        </span>
                        <!-- Fold/Unfold Arrow -->
                        <button
                          type="button"
                          class="flex h-5 w-5 items-center justify-center rounded text-sky-600 hover:text-sky-900 transition-colors"
                          :aria-label="(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')) ? '收起执行' : '展开执行'"
                          @click.stop="msg.pendingExternalExecution.expanded = !(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending'))"
                        >
                          <svg
                            class="h-3.5 w-3.5 transition-transform duration-200"
                            :class="{ 'rotate-180': !(msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')) }"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 15-7-7-7 7" />
                          </svg>
                        </button>
                      </div>
                    </div>

                    <!-- Expanded Content -->
                    <div v-show="msg.pendingExternalExecution.expanded ?? (msg.pendingExternalExecution.status === 'pending')" class="mt-2">
                      <div class="text-sky-800/80 break-words">
                        {{ msg.pendingExternalExecution.details }}
                      </div>
                      <div
                        v-if="msg.pendingExternalExecution.tool_call?.name"
                        class="mt-2 rounded-md bg-white/80 border border-sky-100 p-2 font-mono text-[10px] text-gray-600 overflow-x-auto max-h-56 overflow-y-auto"
                      >
                        <div class="font-semibold text-sky-900 mb-0.5">{{ msg.pendingExternalExecution.tool_call.name }}</div>
                        <pre v-if="msg.pendingExternalExecution.tool_call.args" class="whitespace-pre-wrap break-all font-mono">{{ JSON.stringify(msg.pendingExternalExecution.tool_call.args, null, 2) }}</pre>
                      </div>
                      <div v-if="msg.pendingExternalExecution.status === 'pending'" class="mt-3 space-y-2">
                        <textarea
                          v-model="msg.pendingExternalExecution.outputDraft"
                          rows="4"
                          placeholder="在此粘贴客户端执行该工具后的输出结果..."
                          class="w-full rounded-md border border-sky-200 bg-white/90 px-3 py-2 text-xs text-gray-700"
                        />
                        <button
                          @click="submitPendingExternalExecution(msg)"
                          :disabled="msg.pendingExternalExecution.isSubmitting"
                          class="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-bold text-white shadow-sm hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          提交结果并继续
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <GroundingBlockedCard
                v-if="msg.groundingBlocked"
                class="mt-2"
                :payload="msg.groundingBlocked"
                :disabled="isProcessing"
                @action="(action) => handleGroundingAction(msg.groundingBlocked, action)"
              />

              <!-- Main Content -->
              <div
                v-if="visibleStreamBody(msg) && !msg.groundingBlocked"
                class="relative group/content mt-2 text-gray-800 leading-relaxed markdown-body"
              >
                <!-- Floating Copy Button -->
                <button
                  v-if="!msg.datasetNavigation?.groups?.length"
                  type="button"
                  @click.stop="copyContent(visibleStreamBody(msg), $event)"
                  class="absolute -top-1 -right-1 p-1.5 text-gray-400 bg-white/90 dark:bg-gray-700/90 hover:bg-gray-100 dark:hover:bg-gray-600 hover:text-primary rounded-md transition-all opacity-0 group-hover/content:opacity-100 focus:opacity-100 z-10 shadow-sm border border-gray-100 dark:border-gray-600"
                  title="复制内容"
                >
                  <svg
                    class="w-3.5 h-3.5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                    />
                  </svg>
                </button>
                <AgentHandoffNotice v-if="msg.agentHandoff" :handoff="msg.agentHandoff" />
                <div
                  v-if="msg.permissionNotice?.row_filter_applied && !msg.chatbiInsight"
                  class="mb-2 inline-flex max-w-full items-start gap-1.5 rounded-lg border border-emerald-100 bg-emerald-50/70 px-2.5 py-1.5 text-[11px] font-medium leading-relaxed text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-300"
                >
                  <svg class="mt-0.5 h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                  <span>{{ msg.permissionNotice.message || '已按你的数据权限自动过滤结果' }}</span>
                </div>
                <MessageRenderer
                  v-if="!msg.groundingBlocked && !msg.datasetNavigation?.groups?.length"
                  :content="visibleStreamBody(msg)"
                  :hide-quick-buttons="!!msg.businessConfirmation || !!msg.userQuestion"
                  @quick-question="handleQuickQuestion"
                  @show-citation="(payload) => handleShowCitation(msg, payload.id, payload.anchor)"
                  @open-canvas="handleOpenCanvas"
                />
                <ChatBIInsightPanel
                  v-if="msg.chatbiInsight || (msg.citations && msg.citations.length)"
                  :meta="msg.chatbiInsight"
                  :citations="msg.citations"
                  @open-citation="({ citation, event }) => openCitationPopover(citation, event)"
                />
                <ChatBIMetadataGuide v-if="msg.chatbiMetadataGuide" :guide="msg.chatbiMetadataGuide" @select="handleQuickQuestion" />
                <div v-if="msg.chatbiInsight?.actions?.length && !msg.isThinking" class="mt-2 flex justify-end">
                  <ChatBIContinueAnalysis
                    :actions="msg.chatbiInsight.actions"
                    :is-mobile="isMobile"
                    :result-id="msg.chatbiInsight.result_id"
                    @select="handleChatBIContinueSelect"
                    @action="(action) => handleChatBIResultAction(action, msg)"
                  />
                </div>
                <div v-if="msg.role === 'agent' && isGeneralAgentMessage(msg) && !msg.chatbiInsight?.actions?.length && !msg.isThinking && visibleStreamBody(msg)" class="mt-2 flex justify-end">
                  <MessageContinueAnalysis
                    :is-mobile="isMobile"
                    @select="(query) => handleQuickQuestion(query, 'send', visibleStreamBody(msg))"
                  />
                </div>
                <ErrorDetailCard
                  v-if="msg.errorDetail?.rawError"
                  :raw-error="msg.errorDetail.rawError"
                  :ai-status="msg.errorDetail.aiStatus"
                />
                <DatasetCapabilityMenu
                  v-if="msg.datasetNavigation?.groups?.length"
                  :payload="msg.datasetNavigation"
                  @quick-question="handleQuickQuestion"
                  @record-question-click="(payload) => recordPortalQuestionClick(msg.datasetNavigation, payload)"
	                  @clear-question-click="(payload) => clearPortalQuestionClick(msg.datasetNavigation, payload)"
	                  @refresh="refreshDatasetMenuNavigation(msg)"
	                  @execute-saved-report="handleExecuteSavedReport"
	                  @edit-saved-report="openEditReportModal"
	                />
                <!-- 复制 / 导出 / 点赞踩（托管 RAGFlow、OpenClaw 不展示点赞踩） -->
                <div
                  v-if="msg.role === 'agent' && !msg.isThinking && !(isProcessing && messages.indexOf(msg) === messages.length - 1) && (msg.content || msg.trace_id || canSaveGoldenReportFromMessage(msg) || !hideDebugLikeDislikeForHostedAgent)"
                  class="flex items-center space-x-2 mt-2 pt-2 border-t border-gray-50 opacity-20 hover:opacity-100 group-hover/content:opacity-100 transition-opacity"
                  :class="{'!opacity-100': msg.feedback && !hideDebugLikeDislikeForHostedAgent}"
                >
                  <!-- Copy Content -->
                  <button
                    v-if="msg.content"
                    type="button"
                    @click.stop="copyContent(visibleStreamBody(msg), $event)"
                    class="flex items-center space-x-1 p-1 rounded hover:bg-blue-50 text-gray-400 hover:text-primary transition-colors"
                    title="复制"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                    <span class="text-[10px] font-bold">复制</span>
                  </button>
                  <div v-if="msg.content && (canSaveGoldenReportFromMessage(msg) || msg.trace_id || !hideDebugLikeDislikeForHostedAgent)" class="w-px h-3 bg-gray-200 mx-1"></div>
                  <!-- Save Golden Report -->
                  <button
                    v-if="canSaveGoldenReportFromMessage(msg)"
                    type="button"
                    @click="handleSaveReportFromMessage(msg)"
                    class="flex items-center space-x-1 p-1 rounded hover:bg-amber-50 text-amber-700 hover:text-amber-800 transition-colors"
                    title="将本轮成功查数的 SQL 沉淀为固化报表"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
                    </svg>
                    <span class="text-[10px] font-bold">添加固化报表</span>
                  </button>
                  <div v-if="canSaveGoldenReportFromMessage(msg) && msg.trace_id" class="w-px h-3 bg-gray-200 mx-1"></div>
                  <!-- Export Data Button -->
                  <button
                    v-if="msg.trace_id"
                    @click="exportData(msg.trace_id, 'xlsx')"
                    class="flex items-center space-x-1 p-1 rounded hover:bg-blue-50 text-gray-400 hover:text-primary transition-colors"
                    title="导出数据 (Excel)"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    <span class="text-[10px] font-bold">导出</span>
                  </button>
                  <div v-if="msg.trace_id && !hideDebugLikeDislikeForHostedAgent" class="w-px h-3 bg-gray-200 mx-1"></div>
                  <button
                    v-if="!hideDebugLikeDislikeForHostedAgent"
                    @click="handleFeedback(msg, 'up')"

                    class="p-1 rounded hover:bg-green-50 text-gray-400 hover:text-green-500 transition-colors"
                    :class="{ 'text-green-500 bg-green-50': msg.feedback === 'up' }"
                    title="很有帮助"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10h4.708C19.712 10 20.5 10.743 20.5 11.658c0 .354-.05.7-.145 1.03l-1.921 6.641C18.232 20.141 17.514 21 16.5 21H8.5c-1.105 0-2-.895-2-2v-8c0-.55.224-1.05.586-1.414l5-5c.381-.381 1-.381 1.381 0L14 5v5z" />
                    </svg>
                  </button>
                  <button
                    v-if="!hideDebugLikeDislikeForHostedAgent"
                    @click="handleFeedback(msg, 'down')"
                    class="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-500 transition-colors"
                    :class="{ 'text-red-500 bg-red-50': msg.feedback === 'down' }"
                    title="回答不准确"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14H5.292C4.288 14 3.5 13.257 3.5 12.342c0-.354.05-.7.145-1.03l1.921-6.641C6.768 3.859 7.486 3 8.5 3H16.5c1.105 0 2 .895 2 2v8c0 .55-.224 1.05-.586 1.414l-5 5c-.381.381-1 .381-1.381 0L10 19v-5z" />
                    </svg>
                  </button>
                </div>
                <!-- Typewriter Cursor -->
                <span
                  v-if="msg.role === 'agent' && isProcessing && messages.indexOf(msg) === messages.length - 1 && !msg.isThinking"
                  class="typing-cursor"
                ></span>
              </div>

              <BusinessConfirmationCard
                v-if="msg.businessConfirmation"
                :payload="msg.businessConfirmation"
                :disabled="isProcessing"
                @submit="(payload) => submitBusinessConfirmation(msg, payload)"
              />

              <UserQuestionCard
                v-if="msg.userQuestion"
                :payload="msg.userQuestion"
                :disabled="isProcessing"
                @submit="(payload) => submitUserQuestion(msg, payload)"
              />

              <style scoped>
              .typing-cursor::after {
                content: '▋';
                animation: blink 1s step-start infinite;
                display: inline-block;
                margin-left: 2px;
                vertical-align: baseline;
                color: #2563eb; /* Primary blue */
              }
              @keyframes blink {
                50% { opacity: 0; }
              }
              </style>
              </div>

              <!-- Thinking Indicator (Removed in favor of dynamic timeline indicator) -->
            </div>
          </div>
        </div>
      </div>

      <!-- Input Area -->
      <div class="flex-shrink-0 px-4 py-2 bg-white border-t border-gray-200 relative z-20 debug-chat-input-wrapper">
        <div v-if="skillCreatedInfo" class="mb-2">
          <SkillCreatedBanner
            :info="skillCreatedInfo"
            @mount="mountCreatedSkill"
            @dismiss="skillCreatedInfo = null"
          />
        </div>
        <ChatInput
          ref="chatInputRef"
          v-model="userInput"
          :is-processing="isProcessing || remoteRunActive"
          :is-submitting="sendLocked"
          :show-shortcuts="debugConfig.showShortcuts"
          :slash-commands="slashCommands"
          :allowed-agents="agents"
          :current-user="currentUser"
          :window-width="windowWidth"
          :approval-mode="debugConfig.approvalMode"
          :selected-model="debugConfig.model"
          :available-models="availableModels"
          :context-usage="contextUsage"
          :context-compaction-enabled="Boolean(conversationId)"
          :context-compaction-count="contextCompactionCount"
          :context-compaction-records="contextCompactions"
          :context-compaction-loading="contextCompactionsLoading"
          :context-compaction-error="contextCompactionsError"
          :context-compaction-action-loading="contextCompactionActionLoading"
          :thinking-enable-override="debugConfig.thinkingEnableOverride"
          :reasoning-effort-override="debugConfig.reasoningEffortOverride"
          @update:approval-mode="debugConfig.approvalMode = $event"
          @update:selected-model="handleDebugModelSelection"
          @update:thinking-enable-override="debugConfig.thinkingEnableOverride = $event"
          @update:reasoning-effort-override="debugConfig.reasoningEffortOverride = $event"
          @send="sendMessage"
          @refresh-context-compactions="refreshDebugContextCompactions(true)"
          @manual-context-compaction="manualCompactDebugContext"
          @stop="stopGeneration"
          @toggle-shortcuts="debugConfig.showShortcuts = !debugConfig.showShortcuts"
          @open-command-manager="openCommandManager"
          @upload-image="handleImageUpload"
          @edit-command="editCommand"
          @delete-command="confirmDeleteCommand"
          @switch-mode="handleSwitchMode"
          @reorder-commands="handleReorderCommands"
          :agent-id="agentParams.agent_id"
          @select-knowledge-base="openKnowledgePortal"
          @select-local-fs="openWorkspaceDrawer"
          @select-memory="openMemorySelector"
          @select-mcp-tool="mountMcpToolToSession"
          @system-command="handleSystemCommand"
        >
          <template #banner>
            <div v-if="activeTodoTimeline" class="mx-3 mt-2">
              <ChatTodoCard :timeline="activeTodoTimeline" />
            </div>
          </template>
        </ChatInput>
      </div>

      <ChatCanvas
        :visible="canvasVisible"
        v-model:pinned="canvasPinned"
        :data="canvasData"
        :overlay="canvasFromWorkspace"
        :dock-side="canvasFromWorkspace ? 'left' : 'right'"
        :adjacent-dock-width="canvasFromWorkspace && showWorkspaceDrawer && workspacePinned ? 448 : 0"
        :conversation-id="conversationId"
        @close="closeCanvas"
        @analyze-output="handleAnalyzeCodeOutput"
      />
    </div>

    <!-- Right: Configuration Panel -->
    <DebugConfigPanel
      :visible="showConfigPanel && !hasPinnedDrawer"
      v-model:is-floating="isConfigPanelFloating"
      :config="debugConfig"
      :agent-params="agentParams"
      :loading-config="loadingConfig"
      :agent-context="agentContext"
      :rag-retrieval-meta="ragRetrievalMeta"
      @update:visible="(val) => { showConfigPanel = val; }"
      @load-config="loadCurrentPrompt"
      @clear-context="clearContext"
    />

    <!-- Modal: Raw Prompt -->
    <div
      v-if="showRawPromptModal && selectedRawPrompt"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
    >
      <div
        class="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden animate-fade-in-up"
      >
        <div
          class="h-14 px-6 border-b border-gray-100 flex items-center justify-between bg-gray-50"
        >
          <h3 class="font-bold text-gray-800">Raw Prompt Data</h3>
          <button
            @click="closeModals"
            class="text-gray-500 hover:text-gray-700"
          >
            <svg
              class="w-6 h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>
        <div class="flex-1 overflow-auto p-0 bg-gray-900">
          <div
            v-for="(msg, idx) in selectedRawPrompt"
            :key="idx"
            class="border-b border-gray-700 p-4"
          >
            <div class="flex items-center mb-2">
              <span
                class="text-xs font-bold uppercase tracking-wider px-2 py-0.5 rounded"
                :class="{
                  'bg-green-900 text-green-300': msg.role === 'system',
                  'bg-blue-900 text-blue-300': msg.role === 'user',
                  'bg-purple-900 text-purple-300': msg.role === 'assistant',
                  'bg-yellow-900 text-yellow-300': msg.role === 'tool',
                }"
              >
                {{ msg.role }}
              </span>
            </div>
            <pre
              class="text-sm font-mono text-gray-300 whitespace-pre-wrap break-all"
              >{{ msg.content }}</pre
            >
            <div
              v-if="msg.tool_calls"
              class="mt-2 pl-4 border-l-2 border-yellow-700"
            >
              <p class="text-xs text-yellow-500 font-bold mb-1">Tool Calls:</p>
              <pre class="text-xs text-yellow-200 font-mono">{{
                JSON.stringify(msg.tool_calls, null, 2)
              }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Modal: Command Manager -->
    <div
      v-if="showCommandManager"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
    >
      <div
        class="bg-white rounded-xl shadow-2xl w-full max-w-2xl flex flex-col overflow-hidden animate-fade-in-up"
      >
        <div
          class="px-6 py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50"
        >
          <h3 class="font-bold text-gray-800">快捷指令管理</h3>
          <button
            @click="closeModals"
            class="text-gray-500 hover:text-gray-700"
          >
            <svg
              class="w-6 h-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        <div class="p-6 bg-white flex-1 overflow-y-auto">
          <!-- Form -->
          <div class="mb-6 p-4 bg-gray-50 rounded-lg border border-gray-100">
            <h4 class="text-sm font-bold text-gray-700 mb-3">
              {{ isEditingCmd ? "编辑指令" : "新建指令" }}
            </h4>
            <div class="grid grid-cols-12 gap-4 mb-3">
              <div class="col-span-4">
                <input
                  v-model="editingCommand.label"
                  type="text"
                  placeholder="显示名称 (e.g. 🏢 列表)"
                  class="w-full text-xs border-gray-300 rounded focus:ring-primary focus:border-primary"
                />
              </div>
              <div class="col-span-6">
                <input
                  v-model="editingCommand.command"
                  type="text"
                  placeholder="执行内容"
                  class="w-full text-xs border-gray-300 rounded focus:ring-primary focus:border-primary"
                />
              </div>
              <div class="col-span-2">
                <input
                  v-model.number="editingCommand.sort_order"
                  type="number"
                  placeholder="排序"
                  class="w-full text-xs border-gray-300 rounded focus:ring-primary focus:border-primary"
                />
              </div>
            </div>
            <div class="flex justify-end space-x-2">
              <button
                v-if="isEditingCmd"
                @click="resetCommandForm"
                class="px-3 py-1.5 text-xs text-gray-600 bg-white border border-gray-300 rounded hover:bg-gray-50"
              >
                取消编辑
              </button>
              <button
                @click="saveCommand"
                class="px-3 py-1.5 text-xs text-white bg-primary rounded hover:bg-primary-dark transition-colors font-medium"
              >
                {{ isEditingCmd ? "保存修改" : "添加指令" }}
              </button>
            </div>
          </div>

          <!-- List -->
          <div class="space-y-2">
            <div
              v-for="(cmd, index) in slashCommands"
              :key="cmd.id"
              :draggable="currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name)"
              @dragstart="onDragStart($event, index)"
              @dragover.prevent
              @dragenter.prevent
              @drop="onDrop($event, index)"
              class="flex items-center justify-between p-3 border border-gray-100 rounded-lg hover:bg-gray-50 group transition-all duration-200"
              :class="{
                'opacity-50 scale-[0.98] bg-blue-50 border-blue-200': draggedItemIndex === index,
                'cursor-move': currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name),
                'cursor-default': !(currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name))
              }"
            >
              <div class="flex items-center space-x-3">
                <!-- Drag Handle (Only visible if draggable) -->
                <div v-if="currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name)" class="text-gray-300 group-hover:text-gray-400">
                  <svg
                    class="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M4 8h16M4 16h16"
                    />
                  </svg>
                </div>
                <div v-else class="w-4"></div> <!-- Spacer -->

                <span class="text-xs font-bold text-gray-500 w-6 text-center">{{
                  cmd.sort_order
                }}</span>
                <div class="flex items-center space-x-2">
                  <span
                    class="text-xs font-bold text-gray-800 bg-white border border-gray-200 px-2 py-0.5 rounded"
                    >{{ cmd.label }}</span
                  >
                  <!-- Badges -->
                  <span v-if="['system', 'admin'].includes(cmd.created_by)" class="px-1.5 py-0.5 rounded text-[10px] bg-purple-100 text-purple-700 border border-purple-200 font-medium">System</span>
                  <span v-else-if="currentUser && cmd.created_by === currentUser.user_name" class="px-1.5 py-0.5 rounded text-[10px] bg-blue-100 text-blue-700 border border-blue-200 font-medium">Mine</span>
                </div>

                <span class="text-xs text-gray-500 truncate max-w-xs">{{
                  cmd.command
                }}</span>
              </div>
              <div
                v-if="currentUser && (currentUser.role === 'admin' || cmd.created_by === currentUser.user_name)"
                class="flex items-center space-x-2 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <button
                  @click="editCommand(cmd)"
                  class="text-xs text-blue-600 hover:text-blue-800 underline"
                >
                  编辑
                </button>
                <button
                  @click="confirmDeleteCommand(cmd.id)"
                  class="text-xs text-red-600 hover:text-red-800 underline"
                >
                  删除
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <CitationPopover
    :visible="citationPopover.visible"
    :citation="citationPopover.citation"
    :anchor-rect="citationPopover.anchorRect"
    :anchor-el="citationPopover.anchorEl"
    @close="closeCitationPopover"
    @copy="copyCitationContent"
    @view-original="handleViewOriginal"
  />

  <RagPreviewDrawer
    v-model="ragPreviewVisible"
    :doc-name="ragPreviewDocName"
    :page-no="ragPreviewPageNo"
    :file-url="ragPreviewFileUrl"
    :content="ragPreviewContent"
    :is-office-document="isOfficeDocument"
  />

  <!-- Confirm Modal -->
  <ConfirmModal
    v-if="showDeleteConfirm"
    title="确认删除"
    message="确定要删除这个快捷指令吗？此操作无法撤销。"
    type="danger"
    confirm-text="删除"
    @confirm="performDeleteCommand"
    @cancel="showDeleteConfirm = false"
  />

  <AgentLogicFlowModal :visible="showLogicFlowModal" @close="showLogicFlowModal = false" />
  <KnowledgePortalDrawer
    v-model="showKnowledgePortal"
    v-model:pinned="knowledgePinned"
    v-model:keep-open-on-question="knowledgeKeepOpenOnQuestion"
    v-model:hallucination-check="hallucinationCheckEnabled"
    v-model:similarity-threshold="knowledgeSimilarityThreshold"
    v-model:vector-weight="knowledgeVectorWeight"
    v-model:metadata-top-k="knowledgeMetadataTopK"
    :generated-at="knowledgeGeneratedAt"
    :datasets="knowledgeDatasets"
    :active-dataset-ids="activeDatasetIds"
    :recommendations="datasetRecommendations"
    :pinned-dataset-ids="pinnedDatasetIds"
    :dataset-documents="datasetDocuments"
    :document-recommendations="documentRecommendations"
    :loading="loadingKnowledgeDatasets"
    :load-error="knowledgeLoadError"
    @toggle-active="(id) => toggleDatasetActive(id, chatInputRef)"
    @load-recommendations="fetchRecommendations"
    @quick-question="handleQuickQuestion"
    @refresh="fetchDatasets"
    @toggle-pin="toggleDatasetPinned"
    @load-documents="fetchDatasetDocuments"
    @load-document-recommendations="fetchDocumentRecommendations"
  />

  <WorkspaceBrowserDrawer
    v-model="showWorkspaceDrawer"
    v-model:keep-open-on-select="workspaceKeepOpenOnSelect"
    v-model:pinned="workspacePinned"
    :pinned-dock-class="workspacePinnedDockClass"
    @select="handleSelectLocalFs"
    @preview="handleWorkspaceFilePreview"
  />

  <MemoryBrowserDrawer
    v-model="showMemoryDrawer"
    v-model:keep-open-on-select="memoryKeepOpenOnSelect"
    v-model:pinned="memoryPinned"
    :pinned-dock-class="memoryPinnedDockClass"
    :attached-conversation-ids="attachedMemoryConversationIds"
    @mount="handleMemoryMount"
    @cleared="handleMemoryCleared"
  />

  <ChatModelCallStatsModal
    :visible="showStatsModal"
    :loading="loadingStats"
    :stats="currentStats"
    :expanded="expandedStats"
    @close="showStatsModal = false"
    @toggle="toggleStatExpand"
  />
  <DatasetPortalDrawer
    v-model="showPortalDrawer"
    v-model:keep-open-on-question="portalKeepOpenOnQuestion"
    v-model:pinned="portalPinned"
    :payload="portalNavigationPayload"
    :mountable-datasets="metadataMountableDatasets"
    :active-metadata-dataset-ids="activeMetadataDatasetIds"
    :session-mounted-dataset-ids="sessionMountedMetadataDatasetIds"
    :initial-loading="portalLoading && !portalNavigationPayload"
    :background-refreshing="portalBackgroundRefreshing"
    @quick-question="handlePortalQuickQuestion"
    @record-question-click="(payload) => recordPortalQuestionClick(portalNavigationPayload, payload)"
    @clear-question-click="(payload) => clearPortalQuestionClick(portalNavigationPayload, payload)"
    @toggle-metadata-dataset="(datasetId) => toggleMetadataDatasetActive(datasetId, chatInputRef, metadataMountableDatasets)"
    @pin-metadata-dataset="pinMetadataDatasetToSession"
    @unpin-metadata-dataset="unpinMetadataDatasetFromSession"
    @refresh="refreshPortalNavigation"
    @execute-saved-report="handleExecuteSavedReport"
    @edit-saved-report="openEditReportModal"
    @open-full-page="openFullDataPortal"
  />

  <SavedReportRunModal
    :visible="showReportRunModal"
    :pending-report="pendingSavedReport"
    :form="reportRunForm"
    :previewing="isPreviewingSavedReport"
    :preview="reportRunPreview"
    :uses-month-range="savedReportUsesMonthRange"
    :uses-date-range="savedReportUsesDateRange"
    :overlay-class="saveReportModalOverlayClass"
    :overlay-style="saveReportModalOverlayStyle"
    @close="showReportRunModal = false"
    @execute="executeSavedReportWithOptions"
  />

  <DataPortalReportCreateModal
    :visible="showSaveReportModal"
    :report="saveReportForm"
    :overlay-class="saveReportModalOverlayClass"
    :overlay-style="saveReportModalOverlayStyle"
    scrollbar-variant="debug"
    @close="closeSavedReportEditor"
    @created="handleSavedReportEditorCreated"
  />

    <ChatBIMonitorDialog
      :open="chatbiMonitorDialogOpen"
      :conversation-id="conversationId"
      :result-id="chatbiMonitorResultId"
      @close="chatbiMonitorDialogOpen = false"
      @created="handleChatBIMonitorCreated"
    />
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.3);
  border-radius: 3px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(156, 163, 175, 0.5);
}
.slide-fade-enter-active,
.slide-fade-leave-active {
  transition: all 0.3s ease;
}
.slide-fade-enter-from,
.slide-fade-leave-to {
  transform: translateX(20px);
  opacity: 0;
}
.slide-fade-left-enter-active,
.slide-fade-left-leave-active {
  transition: all 0.3s ease;
}
.slide-fade-left-enter-from,
.slide-fade-left-leave-to {
  transform: translateX(-20px);
  opacity: 0;
}
.animate-fade-in-up {
  animation: fadeInUp 0.3s ease-out;
}
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Markdown Styles */
:deep(.markdown-body) {
  font-size: 14px;
}
:deep(.markdown-body p) {
  margin-bottom: 1em;
}
:deep(.markdown-body p:last-child) {
  margin-bottom: 0;
}
:deep(.markdown-body h1, .markdown-body h2, .markdown-body h3) {
  font-weight: 600;
  margin-top: 1.5em;
  margin-bottom: 0.5em;
}
:deep(.markdown-body code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
    "Liberation Mono", "Courier New", monospace;
  background-color: rgba(175, 184, 193, 0.2);
  padding: 0.2em 0.4em;
  border-radius: 6px;
  font-size: 85%;
}
:deep(.markdown-body pre) {
  margin-top: 1em;
  margin-bottom: 1em;
  padding: 1.25em 1em 1em 1em;
  background-color: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: auto;
  position: relative;
  box-shadow: none;
}
:deep(.markdown-body pre):before {
  content: "";
  position: absolute;
  top: 10px;
  left: 12px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: #ff5f56;
  box-shadow: 16px 0 0 #ffbd2e, 32px 0 0 #27c93f;
  z-index: 1;
}
:deep(.markdown-body pre code) {
  background-color: transparent;
  padding: 0;
  font-size: 13px;
  font-family: "Fira Code", "JetBrains Mono", source-code-pro, Menlo, Monaco,
    Consolas, monospace;
  color: #24292e;
  line-height: 1.5;
}
/* Highlight.js Color Overrides for GitHub Light Style */
:deep(.hljs-keyword),
:deep(.hljs-selector-tag) {
  color: #d73a49;
}
:deep(.hljs-string) {
  color: #032f62;
}
:deep(.hljs-number) {
  color: #005cc5;
}
:deep(.hljs-type),
:deep(.hljs-built_in) {
  color: #6f42c1;
}
:deep(.hljs-attr),
:deep(.hljs-variable) {
  color: #e36209;
}
:deep(.hljs-comment) {
  color: #6a737d;
  font-style: italic;
}
:deep(.hljs-function) {
  color: #6f42c1;
}
:deep(.hljs-params) {
  color: #24292e;
}
:deep(.hljs-meta) {
  color: #005cc5;
}
:deep(.hljs-operator) {
  color: #d73a49;
}
:deep(.hljs-title) {
  color: #6f42c1;
}
:deep(.hljs-punctuation) {
  color: #24292e;
}
:deep(.markdown-body ul, .markdown-body ol) {
  padding-left: 2em;
  margin-bottom: 1em;
}
:deep(.markdown-body li) {
  margin-bottom: 0.25em;
}
:deep(.markdown-body table) {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 1em;
}
:deep(.markdown-body th, .markdown-body td) {
  border: 1px solid #d0d7de;
  padding: 6px 13px;
}
:deep(.markdown-body tr:nth-child(even)) {
  background-color: #f6f8fa;
}
:deep(.markdown-body .code-block-wrapper) {
  position: relative;
  margin-top: 1em;
  margin-bottom: 1em;
}
:deep(.markdown-body .code-block-wrapper pre) {
  margin: 0;
}
:deep(.markdown-body .code-copy-btn) {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 24px;
  height: 24px;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z'/%3E%3C/svg%3E");
  background-size: 16px;
  background-repeat: no-repeat;
  background-position: center;
  background-color: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0;
  transition: all 0.2s;
  z-index: 10;
}
:deep(.markdown-body .code-block-wrapper:hover .code-copy-btn) {
  opacity: 1;
}
:deep(.markdown-body .code-copy-btn:hover) {
  background-color: #f3f4f6;
  border-color: #d1d5db;
}
:deep(.markdown-body .code-copy-btn.copied) {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2310b981'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M5 13l4 4L19 7'/%3E%3C/svg%3E");
  border-color: #10b981;
}

/* 强行剥离 ChatInput 外部的多余边框和背景 */
.debug-chat-input-wrapper :deep(.flex-shrink-0.border-t) {
  border-top-width: 0px !important;
  background-color: transparent !important;
}

/* 强行压缩 ChatInput 内部的内边距，减少占位空间 */
.debug-chat-input-wrapper :deep(.p-3.pb-2) {
  padding: 0px !important;
  padding-bottom: 2px !important;
}

.custom-table-render :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 11px;
  line-height: 1.5;
  background-color: #ffffff;
}
.dark .custom-table-render :deep(table) {
  background-color: #1f2937;
}
.custom-table-render :deep(th),
.custom-table-render :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 6px 8px;
  text-align: left;
  word-break: break-all;
}
.dark .custom-table-render :deep(th),
.dark .custom-table-render :deep(td) {
  border-color: #374151;
}
.custom-table-render :deep(th) {
  background-color: #f3f4f6;
  font-weight: 700;
  color: #1f2937;
}
.dark .custom-table-render :deep(th) {
  background-color: #374151;
  color: #f9fafb;
}
.custom-table-render :deep(tr:nth-child(even)) {
  background-color: #f9fafb;
}
.dark .custom-table-render :deep(tr:nth-child(even)) {
  background-color: rgba(31, 41, 55, 0.4);
}
.custom-table-render :deep(caption) {
  font-size: 10px;
  color: #6b7280;
  padding: 6px 4px;
  font-weight: 700;
  text-align: left;
  background-color: rgba(243, 244, 246, 0.5);
  border-bottom: 2px solid #e5e7eb;
}
.dark .custom-table-render :deep(caption) {
  color: #9ca3af;
  background-color: rgba(55, 65, 81, 0.5);
  border-color: #4b5563;
}
</style>
