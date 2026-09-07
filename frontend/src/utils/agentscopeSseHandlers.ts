import type { BusinessConfirmationState } from "./businessConfirmation";
import {
  markOtherBusinessConfirmationsStale,
  parseBusinessConfirmationEvent,
  shouldSuppressBusinessConfirmation,
} from "./businessConfirmation";
import {
  markOtherUserQuestionsStale,
  parseUserQuestionEvent,
  type UserQuestionState,
} from "./userQuestion";
import {
  appendTimelineNarrationDelta,
  appendProcessNarrationText,
  appendTimelineReasoningDelta,
  commitTimelineNarration,
  discardPendingTimelineNarration,
  finishTimelineReasoning,
  normalizeProcessNarrationText,
  promoteTimelineNarration,
  timelineHasPending,
  upsertTimelineLog,
  upsertTimelineTodo,
  type ProcessTimelineItem,
} from "./processTimeline";
import {
  normalizeSubagentTraceMeta,
  type SubagentTraceMeta,
} from "./subagentTrace";

/**
 * AgentScope 运行时 SSE 事件处理（permission / external / observability）
 * EmbedChat 与 AgentDebug 共用。
 */

export interface PendingToolPermission {
  permission_request_id: string;
  reply_id?: string;
  id?: string;
  title: string;
  details: string;
  tool_call?: {
    id?: string;
    name?: string;
    args?: Record<string, unknown>;
  };
  status: "pending" | "approved" | "rejected" | "expired" | "error";
  isSubmitting?: boolean;
  expanded?: boolean;
}

export interface PendingExternalExecution {
  external_execution_request_id: string;
  reply_id?: string;
  id?: string;
  title: string;
  details: string;
  tool_call?: {
    id?: string;
    name?: string;
    args?: Record<string, unknown>;
  };
  status: "pending" | "completed" | "error";
  isSubmitting?: boolean;
  outputDraft?: string;
  expanded?: boolean;
}

export interface ToolResultDataBlock {
  block_id?: string;
  media_type?: string;
  data?: unknown;
  url?: string | null;
}

export interface GroundingBlockedAction {
  id: string;
  label: string;
  style: "primary" | "secondary";
  kind: "grounding_retry" | "grounding_method" | "send_message" | "switch_agent" | "upload_file";
  payload?: Record<string, unknown>;
}

export interface GroundingBlockedPayload {
  title: string;
  message: string;
  details?: string;
  required_evidence_types: string[];
  retry_query: string;
  actions: GroundingBlockedAction[];
  fallback_content?: string;
}

export interface AgentStreamLog {
  id: string | number;
  title: string;
  details: string;
  status: "pending" | "success" | "error" | "warning";
  error_reason?: string;
  isExpanded?: boolean;
  category?: string;
  tool_name?: string;
  file_metadata?: import("./processTimeline").FileToolMetadata;
  resolution_status?: "disabled" | "missing" | "filtered";
  execution_time_ms?: number | null;
  elapsed_time_ms?: number | null;
  started_at?: number | null;
  isRouter?: boolean;
  subagent?: SubagentTraceMeta;
}

export interface AgentStreamMessage {
  trace_id?: string;
  agentMaxToolcallTimeoutSeconds?: number;
  content: string;
  reasoningContent?: string;
  citations?: unknown[];
  logs?: AgentStreamLog[];
  isThinking?: boolean;
  isThoughtExpanded?: boolean;
  isReasoningExpanded?: boolean;
  processNarration?: string;
  processNarrationPending?: string;
  isProcessNarrationExpanded?: boolean;
  processTimeline?: ProcessTimelineItem[];
  agentName?: string;
  agentDisplayName?: string;
  fallbackNotice?: string;
  turnType?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  pendingPermission?: PendingToolPermission;
  pendingExternalExecution?: PendingExternalExecution;
  toolResultData?: Record<string, ToolResultDataBlock[]>;
  groundingBlocked?: GroundingBlockedPayload;
  businessConfirmation?: BusinessConfirmationState;
  userQuestion?: UserQuestionState;
}

export type AddStreamLogFn<T extends AgentStreamMessage = AgentStreamMessage> = (
  msg: T,
  data: Record<string, unknown>,
) => void;

export function applyStreamTraceId<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, any>,
): boolean {
  // Preserve the original page behavior: a nested sub-agent trace overrides
  // the outer orchestration trace when both are present.
  const traceId = data.data?.trace_id || data.trace_id;
  if (!traceId) return false;
  msg.trace_id = traceId as T["trace_id"];
  return true;
}

export function mergeStreamCitations<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, any>,
): boolean {
  // Preserve the original page semantics: a citation event is always consumed
  // here (even when its payload is malformed), so it never falls through to the
  // downstream answer/content branch.
  if (data.type !== "citation") return false;
  if (!Array.isArray(data.data)) return true;

  const citations = [...(msg.citations || [])] as Array<Record<string, any>>;
  data.data.forEach((candidate: Record<string, any>) => {
    const exists = citations.some((current) =>
      current.chunk_id === candidate.chunk_id ||
      (current.content === candidate.content && current.doc_name === candidate.doc_name)
    );
    if (!exists) citations.push(candidate);
  });
  msg.citations = citations;
  return true;
}

export const formatPermissionStatus = (status: PendingToolPermission["status"]) => {
  const labels: Record<PendingToolPermission["status"], string> = {
    pending: "待确认",
    approved: "已允许",
    rejected: "已拒绝",
    expired: "已过期",
    error: "错误",
  };
  return labels[status] || status;
};

export const formatExternalExecutionStatus = (status: PendingExternalExecution["status"]) => {
  const labels: Record<PendingExternalExecution["status"], string> = {
    pending: "待执行",
    completed: "已完成",
    error: "错误",
  };
  return labels[status] || status;
};

export function handlePermissionRequired<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
) {
  const requestId = String(data.permission_request_id || "");
  msg.pendingPermission = {
    permission_request_id: requestId,
    reply_id: data.reply_id as string | undefined,
    id: data.id as string | undefined,
    title: String(data.title || "工具调用确认"),
    details: String(data.details || ""),
    tool_call: data.tool_call as PendingToolPermission["tool_call"],
    status: "pending",
    expanded: true,
  };
  msg.isThinking = false;
  addLog(msg, {
    id: `permission_${requestId}`,
    title: String(data.title || "工具调用需要确认"),
    details: String(data.details || ""),
    status: "pending",
    category: "permission",
  });
}

export function handleExternalExecutionRequired<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
) {
  const requestId = String(
    data.external_execution_request_id || data.permission_request_id || "",
  );
  msg.pendingExternalExecution = {
    external_execution_request_id: requestId,
    reply_id: data.reply_id as string | undefined,
    id: data.id as string | undefined,
    title: String(data.title || "外部工具执行"),
    details: String(data.details || ""),
    tool_call: data.tool_call as PendingExternalExecution["tool_call"],
    status: "pending",
    outputDraft: "",
    expanded: true,
  };
  msg.isThinking = false;
  addLog(msg, {
    id: `external_${requestId}`,
    title: String(data.title || "需要客户端执行工具"),
    details: String(data.details || ""),
    status: "pending",
    category: "external",
  });
}

export function handleToolResultData(msg: AgentStreamMessage, data: Record<string, unknown>) {
  const toolCallId = String(data.tool_call_id || "");
  if (!toolCallId) return;
  if (!msg.toolResultData) msg.toolResultData = {};
  const block: ToolResultDataBlock = {
    block_id: data.block_id as string | undefined,
    media_type: data.media_type as string | undefined,
    data: data.data,
    url: (data.url as string | null | undefined) ?? null,
  };
  const existing = msg.toolResultData[toolCallId] || [];
  msg.toolResultData[toolCallId] = [...existing, block];

  const logs = msg.logs || [];
  const toolLog = logs.find((log) => log.id === toolCallId);
  const preview = JSON.stringify(block.data ?? block.url ?? block.media_type ?? "", null, 2);
  const suffix = `\n\n[结构化数据 ${block.media_type || "block"}]\n${preview.slice(0, 1200)}`;
  if (toolLog) {
    toolLog.details = `${toolLog.details || ""}${suffix}`.trim();
    upsertTimelineLog(msg, {
      id: toolCallId,
      title: toolLog.title,
      details: toolLog.details,
      status: toolLog.status,
      category: toolLog.category,
      execution_time_ms: toolLog.execution_time_ms,
      started_at: toolLog.started_at,
    });
  }
}

/** 取最近一条仍为 pending 的同类步骤 log（ReAct 多轮 model call 按栈闭合） */
export function findLastPendingStreamLog(
  msg: AgentStreamMessage,
  category: string,
): AgentStreamLog | undefined {
  const logs = msg.logs || [];
  for (let i = logs.length - 1; i >= 0; i -= 1) {
    const log = logs[i];
    if (log?.status === "pending" && log.category === category) {
      return log;
    }
  }
  return undefined;
}

export function findPendingAgentReplyLog(
  msg: AgentStreamMessage,
  replyId: string,
): AgentStreamLog | undefined {
  const targetId = `agent_reply_${replyId}`;
  return (msg.logs || []).find(
    (log) =>
      log.status === "pending" &&
      log.category === "agent" &&
      String(log.id) === targetId,
  );
}

/** 根据 started_at 或显式耗时推算步骤毫秒耗时 */
export function resolveStreamLogDurationMs(
  log: Partial<AgentStreamLog>,
  explicitMs?: number | null,
  now = Date.now(),
): number | undefined {
  if (explicitMs !== undefined && explicitMs !== null && explicitMs > 0) {
    return explicitMs;
  }
  if (
    log.execution_time_ms !== undefined &&
    log.execution_time_ms !== null &&
    log.execution_time_ms > 0
  ) {
    return log.execution_time_ms;
  }
  if (log.started_at) {
    return Math.max(1, now - log.started_at);
  }
  return undefined;
}

/** 新的同类步骤开始前，收尾仍挂起的旧步骤并冻结耗时 */
export function finalizePendingStreamLogs(
  msg: AgentStreamMessage,
  category: string,
  now = Date.now(),
) {
  for (const log of msg.logs || []) {
    if (log.status !== "pending" || log.category !== category) continue;
    log.status = "success";
    const durationMs = resolveStreamLogDurationMs(log, undefined, now);
    if (durationMs !== undefined) {
      log.execution_time_ms = durationMs;
    }
  }
}

const NON_LIVE_TIMER_CATEGORIES = new Set(["permission", "external"]);

/** 仅最后一条挂起步骤展示实时计时，避免历史 pending 泄漏导致秒表一直跑 */
export function isLiveThoughtStepTimer(
  log: AgentStreamLog,
  allLogs: AgentStreamLog[] | undefined,
): boolean {
  if (log.status !== "pending" || !log.started_at) return false;
  if (log.category && NON_LIVE_TIMER_CATEGORIES.has(log.category)) return false;
  const logs = allLogs || [];
  for (let i = logs.length - 1; i >= 0; i -= 1) {
    const item = logs[i];
    if (!item) continue;
    if (item.status !== "pending") continue;
    if (item.category && NON_LIVE_TIMER_CATEGORIES.has(item.category)) continue;
    return item === log;
  }
  return false;
}

/** 流结束时收尾所有挂起步骤（权限/外部执行除外） */
export function finalizeAllPendingStreamLogs(
  msg: AgentStreamMessage,
  now = Date.now(),
) {
  for (const log of msg.logs || []) {
    if (log.status !== "pending") continue;
    if (log.category && NON_LIVE_TIMER_CATEGORIES.has(log.category)) continue;
    log.status = "success";
    const durationMs = resolveStreamLogDurationMs(log, undefined, now);
    if (durationMs !== undefined) {
      log.execution_time_ms = durationMs;
    }
  }
}

const STALE_PENDING_CATEGORIES = new Set(["model", "agent", "tool", "sql", "knowledge", "default"]);

export const DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT_SECONDS = 180;

export function resolveAgentMaxToolcallTimeoutMs(
  msg: AgentStreamMessage,
  fallbackMs = DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT_SECONDS * 1000,
): number {
  const seconds = Number(msg.agentMaxToolcallTimeoutSeconds);
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : fallbackMs;
}

/** 长时间无响应的挂起步骤标为失败，避免一直显示「进行中」 */
export function markStalePendingStreamLogs(
  msg: AgentStreamMessage,
  now = Date.now(),
  staleMs?: number,
): boolean {
  const effectiveStaleMs =
    staleMs !== undefined && Number.isFinite(staleMs) && staleMs > 0
      ? staleMs
      : resolveAgentMaxToolcallTimeoutMs(msg);
  const staleSeconds = Math.max(1, Math.round(effectiveStaleMs / 1000));
  let changed = false;
  for (const log of msg.logs || []) {
    if (log.status !== "pending") continue;
    if (log.category && NON_LIVE_TIMER_CATEGORIES.has(log.category)) continue;
    if (log.category && !STALE_PENDING_CATEGORIES.has(log.category)) continue;
    if (!log.started_at || now - log.started_at < effectiveStaleMs) continue;
    log.status = "error";
    const durationMs = resolveStreamLogDurationMs(log, undefined, now);
    if (durationMs !== undefined) {
      log.execution_time_ms = durationMs;
    }
    const suffix = `（超过 ${staleSeconds} 秒无响应，可能模型或工具调用超时）`;
    log.details = log.details ? `${log.details}\n${suffix}` : suffix;
    changed = true;
  }
  return changed;
}

export function handleModelCallEvent<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
) {
  const phase = String(data.phase || "");
  const replyId = String(data.reply_id || `model_${Date.now()}`);
  if (phase === "start") {
    finalizePendingStreamLogs(msg, "model");
    const seq = (msg.logs || []).filter((log) => log.category === "model").length;
    addLog(msg, {
      id: `model_call_${replyId}_${seq}`,
      title: `模型调用: ${data.model_name || "unknown"}`,
      details: "等待模型响应...",
      status: "pending",
      category: "model",
    });
    return;
  }
  if (phase === "end") {
    const inputTokens = Number(data.input_tokens || 0);
    const outputTokens = Number(data.output_tokens || 0);
    if (inputTokens > 0) msg.prompt_tokens = (msg.prompt_tokens || 0) + inputTokens;
    if (outputTokens > 0) msg.completion_tokens = (msg.completion_tokens || 0) + outputTokens;
    const duration = Number(data.duration_ms || 0);
    const pending = findLastPendingStreamLog(msg, "model");
    const modelName = String(data.model_name || "").trim();
    addLog(msg, {
      id: pending?.id ?? `model_call_${replyId}_orphan`,
      title: pending?.title || (modelName ? `模型调用: ${modelName}` : "模型调用完成"),
      details: `输入 ${inputTokens} / 输出 ${outputTokens} tokens，耗时 ${duration.toFixed(0)} ms`,
      status: "success",
      category: "model",
      execution_time_ms: duration,
    });
  }
}

export function handleModelFallbackEvent<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
): void {
  const primaryModel = String(data.primary_model || "unknown");
  const fallbackModel = String(data.fallback_model || "unknown");
  const notice = String(
    data.content ||
      `> ⚠️ 主模型 \`${primaryModel}\` 调用失败，本次回答由 fallback 模型 \`${fallbackModel}\` 生成。\n\n`,
  );
  if (!notice || msg.fallbackNotice === notice.trim()) return;

  const normalizedNotice = notice.endsWith("\n\n") ? notice : `${notice}\n\n`;
  msg.fallbackNotice = notice.trim();
  if (!String(msg.content || "").startsWith(msg.fallbackNotice)) {
    msg.content = msg.content ? `${normalizedNotice}${msg.content}` : normalizedNotice;
  }
  addLog(msg, {
    id: `model_fallback_${fallbackModel}`,
    title: "⚠️ 已切换 fallback 模型",
    details: `主模型 ${primaryModel} 调用失败，当前回答由 fallback 模型 ${fallbackModel} 生成。`,
    status: "warning",
    category: "model",
  });
}

export function handleAgentReplyEvent<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
) {
  const phase = String(data.phase || "");
  const replyId = String(data.reply_id || `reply_${Date.now()}`);
  if (phase === "start") {
    finalizePendingStreamLogs(msg, "agent");
    addLog(msg, {
      id: `agent_reply_${replyId}`,
      title: "Agent 回复开始",
      details: data.agent_name ? `Agent: ${data.agent_name}` : "",
      status: "pending",
      category: "agent",
    });
    return;
  }
  const pending =
    findPendingAgentReplyLog(msg, replyId) ?? findLastPendingStreamLog(msg, "agent");
  const execution_time_ms = resolveStreamLogDurationMs(
    pending || {},
    Number(data.duration_ms || 0) || undefined,
  );
  addLog(msg, {
    id: pending?.id ?? `agent_reply_${replyId}`,
    title: "Agent 回复结束",
    details: pending?.details || "",
    status: "success",
    category: "agent",
    execution_time_ms,
  });
}

export function handleContextCompression<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
) {
  addLog(msg, {
    id: `context_compression_${Date.now()}`,
    title: String(data.title || "上下文已压缩"),
    details: String(data.details || ""),
    status: (data.status as AgentStreamLog["status"]) || "success",
    category: "context",
  });
}

export function handleContextUpdate<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
) {
  addLog(msg, {
    id: `context_update_${Date.now()}`,
    title: String(data.title || "Agent 状态已更新"),
    details: String(data.details || ""),
    status: (data.status as AgentStreamLog["status"]) || "success",
    category: "context",
  });
}

/**
 * F 项：链路 A（平台注入）的上下文摘录卡片。
 * 与 AgentScope 自带的 `context_compression`（`event_stream` 的 summary 观测噪声）彻底区分：
 * - 不同事件类型 `context_summarized` / `context_compression`；
 * - 不同 id 前缀 `context_summarized_` / `context_compression_`；
 * - 不同 category `context_summarized` / `context_compression`（前端 iconFor 为摘录卡渲染专属图标）；
 * - details 展示真正喂给 LLM 的摘录正文预览（`data.preview`），并标注来源与丢弃/保留条数。
 * 该卡片描述的是真实承载跨轮连续性的摘录，而非空卡观测。
 */
export function handleContextSummarized<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
) {
  const origin = String(data.origin || "");
  const originLabel =
    origin === "llm" ? "（模型语义摘要）" : origin === "deterministic" ? "（规则拼装摘要）" : "";
  const dropped = data.dropped != null ? Number(data.dropped) : 0;
  const kept = data.kept != null ? Number(data.kept) : 0;
  const preview = String(data.preview || data.details || "");
  const tokenUsed = data.token_used != null ? Number(data.token_used) : 0;
  const tokenBudget = data.history_budget != null
    ? Number(data.history_budget)
    : data.token_budget != null
      ? Number(data.token_budget)
      : 0;
  const physicalWindow = data.physical_window != null ? Number(data.physical_window) : 0;
  const completionReserve = data.completion_reserve_tokens != null
    ? Number(data.completion_reserve_tokens)
    : 0;
  const requestInputBudget = data.request_input_budget != null
    ? Number(data.request_input_budget)
    : 0;
  const detailLines: string[] = [];
  if (preview) detailLines.push(preview);
  if (dropped > 0) {
    detailLines.push(`本次压缩丢弃 ${dropped} 条，保留 ${kept} 条${originLabel}。`);
  }
  if (tokenBudget > 0) {
    const pct = Math.min(100, Math.round((tokenUsed / tokenBudget) * 100));
    const windowLabel = physicalWindow > 0
      ? `，模型物理窗口 ${physicalWindow.toLocaleString()} token`
      : "";
    detailLines.push(`压缩前历史预算使用率约 ${pct}%（${tokenUsed.toLocaleString()} / ${tokenBudget.toLocaleString()} 历史预算 token${windowLabel}）。`);
  }
  if (completionReserve > 0) {
    const inputLabel = requestInputBudget > 0
      ? `，请求输入上限 ${requestInputBudget.toLocaleString()} token`
      : "";
    detailLines.push(`已为单次输出预留 ${completionReserve.toLocaleString()} token${inputLabel}。`);
  }
  addLog(msg, {
    id: `context_summarized_${Date.now()}`,
    title: String(data.title || "对话上下文已压缩（平台摘录）"),
    details: detailLines.join("\n"),
    status: (data.status as AgentStreamLog["status"]) || "success",
    category: "context_summarized",
  });
}

export function handleBusinessConfirmation<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
  allMessages?: Array<{ role?: string; content?: string; businessConfirmation?: BusinessConfirmationState }>,
) {
  const parsed = parseBusinessConfirmationEvent(data);
  if (!parsed) return;
  if (shouldSuppressBusinessConfirmation(allMessages)) {
    addLog(msg, {
      id: `business_confirmation_suppressed_${parsed.confirmation_id}`,
      title: "已拦截业务确认卡",
      details: "用户刚取消确认，本轮禁止再次弹出确认卡。",
      status: "warning",
      category: "business_confirmation",
    });
    return;
  }
  if (allMessages) {
    markOtherBusinessConfirmationsStale(allMessages, parsed.confirmation_id);
  }
  msg.businessConfirmation = parsed;
  addLog(msg, {
    id: `business_confirmation_${parsed.confirmation_id}`,
    title: "业务数据确认",
    details: parsed.title,
    status: "pending",
    category: "business_confirmation",
  });
}

export function handleUserQuestion<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
  allMessages?: Array<{ role?: string; content?: string; userQuestion?: UserQuestionState }>,
) {
  const parsed = parseUserQuestionEvent(data);
  if (!parsed) return;
  if (allMessages) markOtherUserQuestionsStale(allMessages, parsed.question_id);
  msg.userQuestion = parsed;
  addLog(msg, {
    id: `user_question_${parsed.question_id}`,
    title: "需要用户回答",
    details: parsed.question,
    status: "pending",
    category: "user_question",
  });
}

export function collapseSecondaryFoldsOnBody<T extends AgentStreamMessage>(msg: T): void {
  msg.isProcessNarrationExpanded = false;
  msg.isReasoningExpanded = false;
  if (!timelineHasPending(msg.processTimeline)) {
    msg.isThoughtExpanded = false;
  }
}

export function assistantBodyHasStarted<T extends AgentStreamMessage>(msg: T): boolean {
  return Boolean(msg.content);
}

const DUPLICATE_BODY_GUARD_CHARS = 32;

function compactAssistantBody(text: string): string {
  return (text || "").replace(/\s+/g, "");
}

function visibleSuffixAfterWhitespacePrefix(prefix: string, text: string): string | null {
  let i = 0;
  let j = 0;
  const isWs = (ch: string) => /\s/.test(ch);
  while (i < prefix.length && j < text.length) {
    while (i < prefix.length && isWs(prefix.charAt(i))) i += 1;
    while (j < text.length && isWs(text.charAt(j))) j += 1;
    if (i >= prefix.length || j >= text.length) break;
    if (prefix.charAt(i) !== text.charAt(j)) return null;
    i += 1;
    j += 1;
  }
  while (i < prefix.length && isWs(prefix.charAt(i))) i += 1;
  if (i < prefix.length) return null;
  return text.slice(j);
}

function narrationSourceId(data: Record<string, unknown>): string | undefined {
  const name = String(data.agent_name || "").trim();
  return name || undefined;
}

export function isDuplicateAssistantBodyDelta(existing: string, piece: string): boolean {
  if (!existing || !piece) return false;
  const existingCompact = compactAssistantBody(existing);
  const pieceCompact = compactAssistantBody(piece);
  if (!existingCompact || !pieceCompact) return false;
  if (pieceCompact.length >= DUPLICATE_BODY_GUARD_CHARS && existingCompact.includes(pieceCompact)) {
    return true;
  }
  if (existingCompact.length >= DUPLICATE_BODY_GUARD_CHARS && pieceCompact.includes(existingCompact)) {
    return visibleSuffixAfterWhitespacePrefix(existing, piece) == null;
  }
  return false;
}

export function appendAssistantBodyDelta<T extends AgentStreamMessage>(msg: T, piece: string): void {
  if (!piece) return;
  const existing = msg.content || "";
  if (existing) {
    const extra = visibleSuffixAfterWhitespacePrefix(existing, piece);
    if (extra !== null) {
      if (!extra.trim()) return;
      piece = extra;
    } else if (isDuplicateAssistantBodyDelta(existing, piece)) {
      return;
    }
  }
  if (!existing) {
    discardPendingTimelineNarration(msg);
    msg.processNarrationPending = "";
    collapseSecondaryFoldsOnBody(msg);
  }
  msg.content = `${existing}${piece}`;
}

export function syncProcessTimelineLog<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  category?: string,
): void {
  const id = data.id as string | number | undefined;
  if (id === undefined || id === null) return;
  upsertTimelineLog(msg, {
    id,
    parent_id: data.parent_id as string | number | undefined,
    title: data.title === undefined ? undefined : String(data.title),
    details: data.details === undefined ? undefined : String(data.details),
    status: data.status as "pending" | "success" | "error" | "warning" | undefined,
    error_reason: data.error_reason === undefined ? undefined : String(data.error_reason),
    category: category || (data.category ? String(data.category) : undefined),
    tool_name: data.tool_name === undefined ? undefined : String(data.tool_name),
    file_metadata: data.file_metadata as import("./processTimeline").FileToolMetadata | undefined,
    resolution_status: data.resolution_status as "disabled" | "missing" | "filtered" | undefined,
    execution_time_ms:
      data.execution_time_ms === undefined ? undefined : Number(data.execution_time_ms),
    started_at: data.started_at === undefined ? undefined : Number(data.started_at),
    subagent: normalizeSubagentTraceMeta(data.subagent),
  });
}

export function syncProcessTimelineTodo<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
): void {
  upsertTimelineTodo(msg, {
    todos: data.todos,
    title: data.title,
  });
}

export function applyProcessNarrationEvent<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
): boolean {
  const eventType = String(data.type || "");
  const piece = String(data.content || "");
  const sourceId = narrationSourceId(data);
  if (eventType === "process_narration") {
    if (piece) {
      msg.processNarrationPending = appendProcessNarrationText(msg.processNarrationPending || "", piece);
      appendTimelineNarrationDelta(msg, piece, sourceId);
      if (!msg.content) msg.isProcessNarrationExpanded = true;
    }
    return true;
  }
  if (eventType === "process_narration_commit") {
    if (piece) {
      const normalizedPiece = normalizeProcessNarrationText(piece, true);
      const previous = normalizeProcessNarrationText(msg.processNarration || "", true);
      const gap = previous && !previous.endsWith("\n") ? "\n\n" : "";
      msg.processNarration = `${previous}${gap}${normalizedPiece}`;
      commitTimelineNarration(msg, normalizedPiece, sourceId);
    }
    msg.processNarrationPending = "";
    if (!msg.content) msg.isProcessNarrationExpanded = true;
    return true;
  }
  if (eventType === "process_narration_promote") {
    if (piece) appendAssistantBodyDelta(msg, piece);
    promoteTimelineNarration(msg, piece, sourceId);
    msg.processNarrationPending = "";
    collapseSecondaryFoldsOnBody(msg);
    msg.isThinking = false;
    return true;
  }
  if (eventType === "retraction") {
    const replacement = String(data.content ?? "");
    msg.content =
      msg.fallbackNotice && !replacement.startsWith(msg.fallbackNotice)
        ? replacement
          ? `${msg.fallbackNotice}\n\n${replacement}`
          : msg.fallbackNotice
        : replacement;
    if (data.final === false) {
      msg.isThinking = true;
      msg.isProcessNarrationExpanded = true;
      msg.isThoughtExpanded = true;
    } else {
      msg.isThinking = false;
    }
    return true;
  }
  return false;
}

/** 主聊天流与 resume 流共用的 AgentScope 扩展事件分发 */
export function dispatchAgentscopeStreamEvent<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
  allMessages?: Array<{
    role?: string;
    content?: string;
    businessConfirmation?: BusinessConfirmationState;
    userQuestion?: UserQuestionState;
  }>,
  onBashEnv?: (env: "host" | "docker" | "e2b" | "ssh") => void,
): boolean {
  switch (data.type) {
    case "run_config": {
      const seconds = Number(data.agent_max_toolcall_timeout);
      if (Number.isFinite(seconds) && seconds > 0) {
        msg.agentMaxToolcallTimeoutSeconds = seconds;
      }
      return true;
    }
    case "permission_required":
      handlePermissionRequired(msg, data, addLog);
      return true;
    case "external_execution_required":
      handleExternalExecutionRequired(msg, data, addLog);
      return true;
    case "business_confirmation":
      handleBusinessConfirmation(msg, data, addLog, allMessages);
      return true;
    case "user_question":
      handleUserQuestion(msg, data, addLog, allMessages);
      return true;
    case "external_execution_result":
      if (msg.pendingExternalExecution) {
        msg.pendingExternalExecution.status = data.status === "error" ? "error" : "completed";
        msg.pendingExternalExecution.expanded = false;
      }
      addLog(msg, {
        id: `external_result_${data.external_execution_request_id || Date.now()}`,
        title: data.status === "error" ? "外部执行失败" : "外部执行结果已提交",
        details: String(data.external_execution_request_id || ""),
        status: data.status === "error" ? "error" : "success",
        category: "external",
      });
      return true;
    case "permission_result":
      if (msg.pendingPermission) {
        msg.pendingPermission.status = data.status === "rejected" ? "rejected" : "approved";
        msg.pendingPermission.expanded = false;
      }
      addLog(msg, {
        id: `permission_${data.permission_request_id}`,
        title: data.status === "rejected" ? "已拒绝工具调用" : "已允许工具调用",
        details: `确认请求: ${data.permission_request_id}`,
        status: "success",
        category: "permission",
      });
      return true;
    case "tool_result_data":
      handleToolResultData(msg, data);
      return true;
    case "model_call":
      handleModelCallEvent(msg, data, addLog);
      return true;
    case "model_fallback":
      handleModelFallbackEvent(msg, data, addLog);
      return true;
    case "agent_reply":
      handleAgentReplyEvent(msg, data, addLog);
      return true;
    case "context_compression":
      handleContextCompression(msg, data, addLog);
      return true;
    case "context_summarized":
      handleContextSummarized(msg, data, addLog);
      return true;
    case "context_update":
      handleContextUpdate(msg, data, addLog);
      return true;
    case "grounding_blocked":
      msg.groundingBlocked = {
        title: String(data.title || "暂时无法验证事实"),
        message: String(data.message || "本次回答缺少可验证的事实来源。"),
        details: data.details ? String(data.details) : undefined,
        required_evidence_types: Array.isArray(data.required_evidence_types)
          ? data.required_evidence_types.map(String)
          : [],
        retry_query: String(data.retry_query || ""),
        actions: Array.isArray(data.actions)
          ? (data.actions as GroundingBlockedAction[])
          : [],
        fallback_content: data.fallback_content
          ? String(data.fallback_content)
          : undefined,
      };
      msg.isThinking = false;
      return true;
    case "thinking":
      if (data.phase === "start") msg.isThinking = true;
      if (data.phase === "end") {
        msg.isThinking = false;
        finishTimelineReasoning(msg);
      }
      if (data.status === "continuing" && !assistantBodyHasStarted(msg)) msg.isThinking = true;
      return true;
    case "todo_update":
      syncProcessTimelineTodo(msg, data);
      return true;
    case "reasoning_content": {
      const reasoningDelta = String(data.content || "");
      if (reasoningDelta) {
        msg.reasoningContent = `${msg.reasoningContent || ""}${reasoningDelta}`;
        appendTimelineReasoningDelta(msg, reasoningDelta);
        if (!assistantBodyHasStarted(msg)) msg.isReasoningExpanded = true;
      }
      return true;
    }
    case "answer_delta": {
      const piece = String(data.content || "");
      if (piece) {
        appendAssistantBodyDelta(msg, piece);
        msg.isThinking = false;
      }
      return true;
    }
    case "process_narration":
    case "process_narration_commit":
    case "process_narration_promote":
    case "retraction":
      return applyProcessNarrationEvent(msg, data);
    case "bash_env":
      if (onBashEnv) {
        const envVal = data.env;
        if (envVal === "docker" || envVal === "host" || envVal === "e2b" || envVal === "ssh") {
          onBashEnv(envVal);
        }
      }
      return true;
    default:
      return false;
  }
}

export async function resumeExternalExecutionStream(options: {
  requestId: string;
  toolCall?: PendingExternalExecution["tool_call"];
  output: string;
  headers?: Record<string, string>;
  credentials?: RequestCredentials;
  onEvent: (data: Record<string, unknown>) => void;
}): Promise<void> {
  const toolCallId = options.toolCall?.id || `call_${Date.now()}`;
  const toolName = options.toolCall?.name || "external_tool";
  const response = await fetch(
    `/api/v1/chat/external-executions/${options.requestId}/resume`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      credentials: options.credentials,
      body: JSON.stringify({
        results: [
          {
            id: toolCallId,
            name: toolName,
            output: options.output,
            state: "success",
          },
        ],
      }),
    },
  );
  if (!response.ok) {
    throw new Error(`外部执行恢复失败: HTTP ${response.status}`);
  }
  if (!response.body) {
    throw new Error("外部执行恢复流为空");
  }
  const { createSseLineParser } = await import("@/utils/chartRenderer");
  const parser = createSseLineParser();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // createSseLineParser.feed/flush 已剥离 `data: ` 前缀并 trim，
    // 返回的是纯 JSON payload 数组，无需（也不应）再做 data: 前缀二次过滤。
    const lines = parser.feed(decoder.decode(value, { stream: true }));
    for (const payload of lines) {
      if (payload === "[DONE]") return;
      try {
        options.onEvent(JSON.parse(payload));
      } catch {
        // ignore malformed chunks
      }
    }
  }
  for (const payload of parser.flush()) {
    if (payload === "[DONE]") continue;
    try {
      options.onEvent(JSON.parse(payload));
    } catch {
      // ignore
    }
  }
}
