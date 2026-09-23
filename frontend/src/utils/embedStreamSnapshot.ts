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

export interface EmbedStreamSnapshotDraft {
  content?: string;
  reasoningContent?: string;
  processTimeline?: unknown[];
  agentName?: string;
  agentDisplayName?: string;
  agentType?: string;
  agentAvatarUrl?: string;
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

const hasVisibleContent = (snapshot: EmbedStreamSnapshot | null | undefined): boolean => {
  const draft = snapshot?.draft;
  if (!draft) return false;
  if (String(draft.content || "").trim()) return true;
  if (String(draft.reasoningContent || "").trim()) return true;
  return Array.isArray(draft.processTimeline) && draft.processTimeline.length > 0;
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
  if (!content && !reasoningContent && processTimeline.length === 0) return null;

  const traceId = String(input?.traceId || "").trim();
  const userContent = clip(input?.user?.content, MAX_CONTENT_CHARS);
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
