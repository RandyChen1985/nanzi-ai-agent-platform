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
  /** 历史接口重建出来的消息（非本轮本地草稿）。 */
  isHistory?: boolean;
  timestamp?: string;
}

export interface LatestAssistantRecord {
  trace_id?: string | null;
  summary?: string | null;
  /**
   * 本轮用户提问。用于在「草稿还没有 trace_id / trace 被覆盖」时判断这条记录
   * 是否属于界面上的最后一轮；缺省时退化为只按 trace 匹配（兼容旧调用）。
   */
  query?: string | null;
  reasoning_content?: string | null;
  process_timeline?: unknown[] | null;
  status?: string | null;
  created_at?: string | null;
}

export interface HistorySyncOptions {
  /** 界面上是否还有一份未收敛的在途草稿（快照已还原、落库终态未到）。 */
  hasUnsettledDraft?: boolean;
}

export type HistorySyncAction =
  | { kind: "update"; index: number }
  | { kind: "fill"; index: number }
  | { kind: "append" }
  | { kind: "reload" }
  | { kind: "ignore" };

const text = (value: unknown): string => String(value ?? "");
const roleOf = (message: HistorySyncMessage | null | undefined): string =>
  text(message?.role);
const isSystemMessage = (message: HistorySyncMessage | null | undefined): boolean =>
  roleOf(message) === "system";
const isUserMessage = (message: HistorySyncMessage | null | undefined): boolean =>
  roleOf(message) === "user";
const isAgentMessage = (message: HistorySyncMessage | null | undefined): boolean => {
  const role = roleOf(message);
  return role === "agent" || role === "assistant";
};

/**
 * 最后一条「有语义」的消息。
 *
 * 首屏历史末尾总有一条 `system` 分隔符（"以上是历史会话…"），它既不是用户提问也不是
 * 助手回答。旧实现直接取 `messages[length - 1]`，在无草稿恢复时永远命中这条分隔符，
 * fill/append 全部失效、最终走到 ignore——界面于是永久停在上一轮回答上。
 */
const lastMeaningfulIndex = (messages: HistorySyncMessage[]): number => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (!isSystemMessage(messages[index])) return index;
  }
  return -1;
};

/** 最后一条用户提问的下标（用于判断记录是否属于界面上的最后一轮）。 */
const lastUserIndex = (messages: HistorySyncMessage[]): number => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (isUserMessage(messages[index])) return index;
  }
  return -1;
};

/**
 * 服务端是否已经落库了本轮内容。
 *
 * 不能只看 summary：有工具执行但最终没有文字回答的轮次同样会落库
 * （`_should_persist_turn_history` 接受 process_timeline / reasoning_content），
 * 旧判据把它们当成「尚未落库」永久忽略，界面永远收敛不到终态。
 */
const hasPersistedBody = (latest: LatestAssistantRecord | null | undefined): boolean => {
  if (!latest) return false;
  if (text(latest.summary).trim()) return true;
  if (text(latest.reasoning_content).trim()) return true;
  return Array.isArray(latest.process_timeline) && latest.process_timeline.length > 0;
};

const readTime = (value: unknown): number | null => {
  const raw = text(value).trim();
  if (!raw) return null;
  const timestamp = Date.parse(raw);
  return Number.isFinite(timestamp) ? timestamp : null;
};

/**
 * 本地「已加载到哪一轮」的水位线。
 *
 * - 首屏历史末尾的 system 分隔符，用的是本轮历史最新一条的 created_at；
 * - 加载更多后分隔符被移除，退化为最后一条历史消息。
 *
 * 用来区分「服务端出现了本地没加载到的新一轮」（重拉）与「更早轮次的陈留记录」
 * （忽略），避免把旧回答贴到本地正在跑的新提问下面。
 */
const localWatermark = (messages: HistorySyncMessage[]): HistorySyncMessage | null => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (isSystemMessage(messages[index])) return messages[index];
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.isHistory === true) return messages[index];
  }
  return null;
};

export function planHistorySync(
  messages: HistorySyncMessage[],
  latest: LatestAssistantRecord,
  options: HistorySyncOptions = {},
): HistorySyncAction {
  const list = Array.isArray(messages) ? messages : [];
  const traceId = text(latest?.trace_id).trim();
  const summary = text(latest?.summary);
  const query = text(latest?.query);
  // 服务端尚未落库终态内容（任务仍在跑）时不触碰界面。
  if (!traceId || !hasPersistedBody(latest)) return { kind: "ignore" };

  // 页面重建后消息列表为空、服务端已有终态记录：整段历史重拉，而不是只补一条
  // 助手消息（那会丢掉对应的用户提问）。直接忽略则会让回答永远不出现。
  if (list.length === 0) return { kind: "reload" };

  const unsettled = options?.hasUnsettledDraft === true;
  const lastMeaningfulAt = lastMeaningfulIndex(list);
  const lastMeaningful = lastMeaningfulAt >= 0 ? list[lastMeaningfulAt] : null;
  const lastUserAt = lastUserIndex(list);
  const lastUser = lastUserAt >= 0 ? list[lastUserAt] : null;
  // 同一会话里重复提问完全合法。若最新记录的 trace 属于最后一条用户消息之前的
  // 某一轮（上一次问过同样的问题），按 query 匹配就会把上一轮回答误判成本轮——
  // 这里用 trace 归属提前排除，不能依赖时间戳（历史记录是服务端本地时间、本地
  // 消息是 UTC ISO，跨时区比较会偏）。
  const traceBelongsToEarlierTurn =
    lastUserAt > 0 &&
    list
      .slice(0, lastUserAt)
      .some((message) => isUserMessage(message) && text(message?.trace_id).trim() === traceId);
  const queryMatches =
    Boolean(query) &&
    Boolean(lastUser) &&
    text(lastUser?.content) === query &&
    !traceBelongsToEarlierTurn;
  const hasAgentAfterLastUser =
    lastUserAt >= 0 && list.slice(lastUserAt + 1).some(isAgentMessage);

  const matchedIndex = list.findIndex(
    (message) =>
      isAgentMessage(message) && text(message?.trace_id).trim() === traceId,
  );
  if (matchedIndex !== -1) {
    const current = list[matchedIndex];
    const currentContent = text(current?.content);
    // `unsettled` 只对「本轮草稿」有效。若命中的是上一轮的终态消息（在最后一条用户
    // 提问之前），它不该因为本地还有一份新草稿就被反复 update——否则调用方会误以为
    // 本轮已收敛，清掉草稿、停止续显，恢复器再补建，形成每 3s 一次的循环。
    const matchedIsCurrentTurn = matchedIndex > lastUserAt;
    const needsSync =
      (unsettled && matchedIsCurrentTurn) ||
      current?.isThinking === true ||
      currentContent.length < summary.length;
    return needsSync ? { kind: "update", index: matchedIndex } : { kind: "ignore" };
  }

  // 用户刚提问、助手结果才落库：追加一条。
  // 用 query 判定而不是只看最后一条角色，末尾的 system 分隔符也不会再挡住这条路径。
  if (queryMatches && !hasAgentAfterLastUser) return { kind: "append" };

  // 流式占位消息还没拿到 trace_id（或 trace 被嵌套子代理覆盖）：就地补全/覆盖，
  // 避免出现两个助手气泡。query 对得上是同一轮才敢写 trace，防止锁死上一轮的 trace。
  if (lastMeaningful && isAgentMessage(lastMeaningful) && lastMeaningful.isHistory !== true) {
    const draftTrace = text(lastMeaningful.trace_id).trim();
    if (!draftTrace) {
      const hasBody = Boolean(text(lastMeaningful.content).trim());
      if (!hasBody && lastMeaningful.isThinking !== false) {
        // 空白占位：沿用既有 fill 语义（只补内容、不锁 trace）。但调用方给了 query
        // 且对不上时，这条记录属于更早轮次，不能把上一轮回答填进本轮占位。
        if (!query || queryMatches) return { kind: "fill", index: lastMeaningfulAt };
      }
      if (queryMatches) return { kind: "update", index: lastMeaningfulAt };
    } else if (draftTrace === traceId || queryMatches) {
      return { kind: "update", index: lastMeaningfulAt };
    }
  }

  if (!lastMeaningful) return { kind: "reload" };

  // trace 不在本地：要么是「服务端出现了本地没加载到的新一轮」，要么是更早轮次的
  // 陈留记录。用本地历史水位线区分：
  // - 记录时间不晚于水位线 → 旧记录，忽略（不能把旧回答贴到新提问下面）；
  // - 页面历史已加载（有分隔符/历史消息）且记录更新（或时间不可比）→ 整段重拉。
  //   只 append 会丢用户提问，ignore 则会让新回答永远不出现（刷新后卡在上一轮）。
  // - 没有任何历史水位线（本地只有本轮草稿/提问）→ 不重拉，避免冲掉在途现场。
  const watermark = localWatermark(list);
  // 只有「本地尾部是已加载的历史消息」才允许整段重拉。尾部若是本轮在途草稿/本地
  // 提问（非 isHistory），重拉会把尚未落库的现场直接冲掉——宁可等下一轮。
  if (watermark && lastMeaningful.isHistory === true) {
    const latestTime = readTime(latest?.created_at);
    const watermarkTime = readTime(watermark?.timestamp);
    if (latestTime !== null && watermarkTime !== null && latestTime <= watermarkTime) {
      return { kind: "ignore" };
    }
    return { kind: "reload" };
  }

  // 用户刚提问、助手结果才落库：追加一条。trace 对不上说明是更早轮次的陈留记录，忽略。
  if (isUserMessage(lastMeaningful)) {
    const userTrace = text(lastMeaningful.trace_id).trim();
    if (!userTrace || userTrace === traceId || queryMatches) {
      return { kind: "append" };
    }
  }

  return { kind: "ignore" };
}

export interface RecoverySyncMessage {
  role: string;
  trace_id?: string;
  content?: string;
  isThinking?: boolean;
  isHistory?: boolean;
  timestamp?: string;
}

export interface RecoverySyncInput {
  isProcessing: boolean;
  lastMessage?: RecoverySyncMessage | null;
  /** 界面上还有一份未收敛的在途草稿（快照已还原、落库终态未到）。 */
  hasUnsettledDraft: boolean;
}

/**
 * 历史终态同步是否值得再试一次。
 *
 * 只判断「界面上还有没有未收敛的痕迹」：本轮仍在处理、仍有在途草稿、最后一条是等待
 * 回答的提问，或助手消息还是占位/思考态。末尾的 system 分隔符不代表任何轮次状态，
 * 必须跳过（否则刷新后无草稿的场景会在这里静默放弃重试）。
 *
 * 刻意**不看草稿是否已有正文**：草稿恰恰是「切走瞬间」的前缀，而后端落库晚于它发出的
 * run_status 终态帧，晚到几秒到几十秒是常态。此前按 `isThinking || !content` 判定，
 * 草稿一旦有正文就判定无需重试并静默放弃，界面便永久停在这份草稿上——这正是
 * 「切回来什么都没动、刷新页面才看到已跑完的回答」的成因。
 */
export function shouldKeepRecoverySync(input: RecoverySyncInput): boolean {
  if (input?.isProcessing) return true;
  if (input?.hasUnsettledDraft) return true;
  const last = input?.lastMessage;
  if (!last) return false;
  if (isSystemMessage(last)) return false;
  if (isUserMessage(last)) return true;
  if (isAgentMessage(last) && (last.isThinking === true || !last.content)) return true;
  return false;
}
