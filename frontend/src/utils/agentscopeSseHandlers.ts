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
}

export interface ToolResultDataBlock {
  block_id?: string;
  media_type?: string;
  data?: unknown;
  url?: string | null;
}

export interface AgentStreamLog {
  id: string | number;
  title: string;
  details: string;
  status: "pending" | "success" | "error" | "warning";
  isExpanded?: boolean;
  category?: string;
  execution_time_ms?: number | null;
  elapsed_time_ms?: number | null;
  started_at?: number | null;
  isRouter?: boolean;
}

export interface AgentStreamMessage {
  trace_id?: string;
  content: string;
  citations?: unknown[];
  logs?: AgentStreamLog[];
  isThinking?: boolean;
  isThoughtExpanded?: boolean;
  agentName?: string;
  agentDisplayName?: string;
  turnType?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  pendingPermission?: PendingToolPermission;
  pendingExternalExecution?: PendingExternalExecution;
  toolResultData?: Record<string, ToolResultDataBlock[]>;
}

export type AddStreamLogFn<T extends AgentStreamMessage = AgentStreamMessage> = (
  msg: T,
  data: Record<string, unknown>,
) => void;

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

export function handleModelCallEvent<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
) {
  const phase = String(data.phase || "");
  const replyId = String(data.reply_id || `model_${Date.now()}`);
  if (phase === "start") {
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

export function handleAgentReplyEvent<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
) {
  const phase = String(data.phase || "");
  const replyId = String(data.reply_id || `reply_${Date.now()}`);
  if (phase === "start") {
    addLog(msg, {
      id: `agent_reply_${replyId}`,
      title: "Agent 回复开始",
      details: data.agent_name ? `Agent: ${data.agent_name}` : "",
      status: "pending",
      category: "agent",
    });
    return;
  }
  const pending = findLastPendingStreamLog(msg, "agent");
  const duration = Number(data.duration_ms || 0);
  addLog(msg, {
    id: pending?.id ?? `agent_reply_${replyId}`,
    title: "Agent 回复结束",
    details: pending?.details || "",
    status: "success",
    category: "agent",
    execution_time_ms: duration > 0 ? duration : undefined,
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

/** 主聊天流与 resume 流共用的 AgentScope 扩展事件分发 */
export function dispatchAgentscopeStreamEvent<T extends AgentStreamMessage>(
  msg: T,
  data: Record<string, unknown>,
  addLog: AddStreamLogFn<T>,
): boolean {
  switch (data.type) {
    case "permission_required":
      handlePermissionRequired(msg, data, addLog);
      return true;
    case "external_execution_required":
      handleExternalExecutionRequired(msg, data, addLog);
      return true;
    case "external_execution_result":
      if (msg.pendingExternalExecution) {
        msg.pendingExternalExecution.status = data.status === "error" ? "error" : "completed";
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
    case "agent_reply":
      handleAgentReplyEvent(msg, data, addLog);
      return true;
    case "context_compression":
      handleContextCompression(msg, data, addLog);
      return true;
    case "context_update":
      handleContextUpdate(msg, data, addLog);
      return true;
    case "thinking":
      if (data.phase === "start") msg.isThinking = true;
      if (data.phase === "end") msg.isThinking = false;
      if (data.status === "continuing") msg.isThinking = true;
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
    const lines = parser.feed(decoder.decode(value, { stream: true }));
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const payload = trimmed.slice(5).trim();
      if (payload === "[DONE]") return;
      try {
        options.onEvent(JSON.parse(payload));
      } catch {
        // ignore malformed chunks
      }
    }
  }
  for (const line of parser.flush()) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) continue;
    const payload = trimmed.slice(5).trim();
    if (payload === "[DONE]") continue;
    try {
      options.onEvent(JSON.parse(payload));
    } catch {
      // ignore
    }
  }
}
