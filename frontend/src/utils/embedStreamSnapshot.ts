/**
 * EmbedChat 流式快照：切页面/刷新后把「已经输出过的思考与正文」还回界面。
 *
 * 背景：EmbedChat 是路由级页面，切到平台内其他页面再切回会卸载重建，而本轮内容
 * 只存在于流式内存里、尚未落库，重建后 `messages` 为空 → 界面退回空会话欢迎页。
 * 这里把流式草稿按会话写入 sessionStorage，挂载后先还原，等落库终态到达时再由
 * 运行恢复器覆盖（草稿带 trace_id，可被 `planHistorySync` 精确命中）。
 *
 * 只做数据构建与校验，不碰存储，便于直接单测。
 */

export const SNAPSHOT_TTL_MS = 12 * 60 * 60 * 1000;
export const SNAPSHOT_MAX_BYTES = 400_000;
const MAX_CONTENT_CHARS = 20_000;
const MAX_REASONING_CHARS = 20_000;
const MAX_TIMELINE_ITEMS = 120;
const MAX_TIMELINE_DEPTH = 2;
const MAX_TEXT_ITEM_CHARS = 8_000;
const MAX_LOG_DETAILS_CHARS = 2_000;
const MAX_INTERACTION_BYTES = 24_000;

/** 待确认交互态：这些是「upsert 终态」，重放不会回退，适合随快照一起还原。 */
export type EmbedStreamSnapshotInteraction = Record<string, unknown>;

export interface EmbedStreamSnapshotDraft {
  content?: string;
  reasoningContent?: string;
  processTimeline?: unknown[];
  agentName?: string;
  agentDisplayName?: string;
  agentType?: string;
  agentAvatarUrl?: string;
  /**
   * 待确认卡片。事件日志被 LTRIM/TTL 截断时，客户端唯一的恢复来源就是这里；
   * 缺了它，刷新后权限卡片会永久消失、后台任务一直挂到确认超时。
   */
  pendingPermission?: EmbedStreamSnapshotInteraction | null;
  pendingExternalExecution?: EmbedStreamSnapshotInteraction | null;
  userQuestion?: EmbedStreamSnapshotInteraction | null;
  businessConfirmation?: EmbedStreamSnapshotInteraction | null;
}

export interface EmbedStreamSnapshotInput {
  conversationId?: string;
  traceId?: string | null;
  lastSeq?: number;
  user?: { content?: string; timestamp?: string } | null;
  draft?: EmbedStreamSnapshotDraft | null;
  now?: number;
}

export interface EmbedStreamSnapshot {
  version: 1;
  conversationId: string;
  traceId?: string;
  lastSeq?: number;
  user?: { content: string; timestamp?: string };
  draft: EmbedStreamSnapshotDraft;
  savedAt: number;
}

const clip = (value: unknown, max: number): string => {
  const text = String(value ?? "");
  return text.length > max ? text.slice(0, max) : text;
};

const serializedBytes = (value: unknown): number => {
  try {
    return JSON.stringify(value)?.length ?? 0;
  } catch {
    return Number.POSITIVE_INFINITY;
  }
};

/**
 * 时间线是快照里最大的一块（含工具 details、思考正文、文件元数据），逐层裁剪：
 * 限制条数与递归深度，并截断长文本字段。
 */
const trimTimelineItems = (items: unknown[], depth = 0): unknown[] => {
  const result: Record<string, unknown>[] = [];
  for (const raw of items.slice(0, MAX_TIMELINE_ITEMS)) {
    if (!raw || typeof raw !== "object") continue;
    const next: Record<string, any> = { ...(raw as Record<string, any>) };
    if (typeof next.content === "string") next.content = clip(next.content, MAX_TEXT_ITEM_CHARS);
    if (typeof next.details === "string") next.details = clip(next.details, MAX_LOG_DETAILS_CHARS);
    if (typeof next.error_reason === "string") {
      next.error_reason = clip(next.error_reason, MAX_LOG_DETAILS_CHARS);
    }
    if (next.file_metadata && typeof next.file_metadata === "object") {
      // changes 可能包含整段文件内容，快照不需要它。
      const meta = { ...(next.file_metadata as Record<string, unknown>) };
      delete meta.changes;
      next.file_metadata = meta;
    }
    if (Array.isArray(next.children)) {
      next.children =
        depth + 1 >= MAX_TIMELINE_DEPTH ? [] : trimTimelineItems(next.children, depth + 1);
    }
    result.push(next);
  }
  return result;
};

/**
 * 交互态裁剪：保留恢复卡片所需的全部终态字段；体积超限时只丢工具参数
 * （权限确认只需 request_id + confirmed，外部执行恢复可退化为默认工具名），
 * 仍超限则放弃这一项——绝不把一个撑爆存储的快照写进去。
 */
const trimInteraction = (value: unknown): EmbedStreamSnapshotInteraction | null => {
  if (!value || typeof value !== "object") return null;
  const next: Record<string, unknown> = { ...(value as Record<string, unknown>) };
  if (serializedBytes(next) > MAX_INTERACTION_BYTES) {
    // 外部执行草稿可能很长：先截断它、再丢工具参数（卡片本身要保住）。
    if (typeof next.outputDraft === "string") {
      next.outputDraft = clip(next.outputDraft, 4_000);
    }
    if ("tool_call" in next) delete next.tool_call;
  }
  return serializedBytes(next) <= MAX_INTERACTION_BYTES ? next : null;
};

const hasInteractionState = (draft: EmbedStreamSnapshotDraft | null | undefined): boolean =>
  Boolean(
    draft?.pendingPermission ||
      draft?.pendingExternalExecution ||
      draft?.userQuestion ||
      draft?.businessConfirmation,
  );

const hasVisibleContent = (snapshot: EmbedStreamSnapshot | null | undefined): boolean => {
  const draft = snapshot?.draft;
  if (draft) {
    if (String(draft.content || "").trim()) return true;
    if (String(draft.reasoningContent || "").trim()) return true;
    if (Array.isArray(draft.processTimeline) && draft.processTimeline.length > 0) return true;
    if (hasInteractionState(draft)) return true;
  }
  // 用户提问也算可见内容：首片正文还没到就刷新时，至少要让提问留在界面上；
  // 后续过程由恢复器从事件日志重建。
  return Boolean(String(snapshot?.user?.content || "").trim());
};

export function buildEmbedStreamSnapshot(
  input: EmbedStreamSnapshotInput,
): EmbedStreamSnapshot | null {
  const conversationId = String(input?.conversationId || "").trim();
  if (!conversationId) return null;

  const content = clip(input?.draft?.content, MAX_CONTENT_CHARS);
  const reasoningContent = clip(input?.draft?.reasoningContent, MAX_REASONING_CHARS);
  const processTimeline = trimTimelineItems(
    Array.isArray(input?.draft?.processTimeline) ? (input!.draft!.processTimeline as unknown[]) : [],
  );
  const pendingPermission = trimInteraction(input?.draft?.pendingPermission);
  const pendingExternalExecution = trimInteraction(input?.draft?.pendingExternalExecution);
  const userQuestion = trimInteraction(input?.draft?.userQuestion);
  const businessConfirmation = trimInteraction(input?.draft?.businessConfirmation);
  const userContent = clip(input?.user?.content, MAX_CONTENT_CHARS);

  const hasDraftState =
    Boolean(content || reasoningContent) ||
    processTimeline.length > 0 ||
    Boolean(pendingPermission || pendingExternalExecution || userQuestion || businessConfirmation);
  // 只有提问也算有效快照：刷新后先把提问还原出来，过程交给恢复器重建。
  if (!hasDraftState && !userContent) return null;

  const traceId = String(input?.traceId || "").trim();
  const userTimestamp = String(input?.user?.timestamp || "").trim();

  const base: EmbedStreamSnapshot = {
    version: 1,
    conversationId,
    ...(traceId ? { traceId } : {}),
    ...(Number.isFinite(Number(input?.lastSeq)) && Number(input?.lastSeq) > 0
      ? { lastSeq: Number(input?.lastSeq) }
      : {}),
    ...(userContent
      ? { user: { content: userContent, ...(userTimestamp ? { timestamp: userTimestamp } : {}) } }
      : {}),
    draft: {
      content,
      ...(reasoningContent ? { reasoningContent } : {}),
      ...(processTimeline.length > 0 ? { processTimeline } : {}),
      ...(pendingPermission ? { pendingPermission } : {}),
      ...(pendingExternalExecution ? { pendingExternalExecution } : {}),
      ...(userQuestion ? { userQuestion } : {}),
      ...(businessConfirmation ? { businessConfirmation } : {}),
      ...(input?.draft?.agentName ? { agentName: String(input.draft.agentName) } : {}),
      ...(input?.draft?.agentDisplayName
        ? { agentDisplayName: String(input.draft.agentDisplayName) }
        : {}),
      ...(input?.draft?.agentType ? { agentType: String(input.draft.agentType) } : {}),
      ...(input?.draft?.agentAvatarUrl
        ? { agentAvatarUrl: String(input.draft.agentAvatarUrl) }
        : {}),
    },
    savedAt: Number.isFinite(input?.now) ? Number(input!.now) : Date.now(),
  };

  if (serializedBytes(base) <= SNAPSHOT_MAX_BYTES) return base;

  // 体积降级：先丢时间线（最大的那块），再丢思考正文，保证绝不撑爆 sessionStorage。
  // 交互态是「刷新后唯一可点的东西」，降级顺序里必须最后才考虑它。
  const withoutTimeline: EmbedStreamSnapshot = {
    ...base,
    draft: { ...base.draft, processTimeline: [] },
  };
  if (serializedBytes(withoutTimeline) <= SNAPSHOT_MAX_BYTES) return withoutTimeline;

  const minimal: EmbedStreamSnapshot = {
    ...base,
    draft: { ...base.draft, processTimeline: [], reasoningContent: "" },
  };
  return serializedBytes(minimal) <= SNAPSHOT_MAX_BYTES ? minimal : null;
}

export function isEmbedStreamSnapshotUsable(
  snapshot: EmbedStreamSnapshot | null | undefined,
  conversationId: string,
  now: number = Date.now(),
): boolean {
  if (!snapshot || typeof snapshot !== "object") return false;
  if (Number((snapshot as { version?: unknown }).version) !== 1) return false;
  const expectedConversationId = String(conversationId || "").trim();
  if (!expectedConversationId) return false;
  if (String(snapshot.conversationId || "") !== expectedConversationId) return false;
  const savedAt = Number(snapshot.savedAt);
  if (!Number.isFinite(savedAt)) return false;
  if (now - savedAt > SNAPSHOT_TTL_MS) return false;
  return hasVisibleContent(snapshot);
}
