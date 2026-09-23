/**
 * 运行恢复同步决策。
 *
 * EmbedChat 是路由级页面：切到平台内其他页面再切回会卸载重建，SSE 与页面实例的绑定
 * 随之丢失，而服务端 producer 仍会把任务跑完并落库。恢复时必须在「不产生重复气泡、
 * 不覆盖更长正文」的前提下，把最新一条助手结果合并回当前消息列表。
 *
 * 这里只做决策、不碰响应式对象，便于直接单测。
 */

export interface HistorySyncMessage {
  role: string;
  trace_id?: string;
  content?: string;
  isThinking?: boolean;
}

export interface LatestAssistantRecord {
  trace_id?: string | null;
  summary?: string | null;
}

export type HistorySyncAction =
  | { kind: "update"; index: number }
  | { kind: "fill"; index: number }
  | { kind: "append" }
  | { kind: "reload" }
  | { kind: "ignore" };

export function planHistorySync(
  messages: HistorySyncMessage[],
  latest: LatestAssistantRecord,
): HistorySyncAction {
  const traceId = String(latest?.trace_id || "").trim();
  const summary = String(latest?.summary || "");
  // 服务端尚未落库终态内容（任务仍在跑）时不触碰界面。
  if (!traceId || !summary) return { kind: "ignore" };

  // 页面重建后消息列表为空、服务端已有终态记录：整段历史重拉，而不是只补一条
  // 助手消息（那会丢掉对应的用户提问）。直接忽略则会让回答永远不出现。
  if (messages.length === 0) return { kind: "reload" };

  const matchedIndex = messages.findIndex(
    (message) => message?.role === "agent" && message?.trace_id === traceId,
  );
  if (matchedIndex !== -1) {
    const current = messages[matchedIndex];
    const currentContent = String(current?.content || "");
    const needsSync = current?.isThinking === true || currentContent.length < summary.length;
    return needsSync ? { kind: "update", index: matchedIndex } : { kind: "ignore" };
  }

  // 该 trace 已有助手消息之外的情况：看最后一条能否承载这次同步。
  const lastIndex = messages.length - 1;
  if (lastIndex < 0) return { kind: "ignore" };
  const last = messages[lastIndex];

  // 流式占位消息还没拿到 trace_id：就地补全，避免出现两个助手气泡。
  if (last?.role === "agent" && !last?.trace_id && (last.isThinking === true || !last.content)) {
    return { kind: "fill", index: lastIndex };
  }
  // 用户刚提问、助手结果才落库：追加一条。trace 对不上说明是更早轮次的陈留记录，忽略。
  if (last?.role === "user" && (!last.trace_id || last.trace_id === traceId)) {
    return { kind: "append" };
  }

  return { kind: "ignore" };
}
