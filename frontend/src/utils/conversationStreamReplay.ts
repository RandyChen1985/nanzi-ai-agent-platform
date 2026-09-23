/**
 * 会话流事件续显的进度合并：把服务端增量返回的事件过滤成"可安全写入本轮草稿"的一批。
 *
 * 幂等性来自 lastSeq：它随流式快照一起持久化，恢复时作为 after_seq 起点，
 * 因此切回页面或刷新后不会把已渲染过的正文再叠加一次。
 */

export interface StreamReplayEvent {
  _seq?: number;
  type?: string;
  trace_id?: string;
  content?: string;
  [key: string]: unknown;
}

export interface StreamReplayCursor {
  lastSeq?: number;
  traceId?: string;
}

export interface StreamReplayPlan {
  events: StreamReplayEvent[];
  lastSeq: number;
  gap: boolean;
}

const readSeq = (event: unknown): number | null => {
  if (!event || typeof event !== "object") return null;
  const raw = (event as StreamReplayEvent)._seq;
  const seq = Number(raw);
  return Number.isFinite(seq) && seq > 0 ? seq : null;
};

/**
 * 判断本次拉取是否需要「先清空草稿的累加字段，再按日志整体重建」。
 *
 * 活流期间游标不推进（SSE 帧不带 _seq），所以切走时快照里的 lastSeq 往往是 0。
 * 此时草稿只是「切走瞬间」的前缀，而日志还含切走期间后台新产出的事件：
 * 直接重放会把前缀叠加一遍（逐条 delta 达不到 32 字符去重阈值），只对齐游标又会
 * 让切走期间的内容永久缺失。所以首次必须清空后整体重建。
 *
 * 草稿为空（快照丢失 / 首次进入）时本来就没有可叠加的内容，无需清空；
 * 游标已知（>0）说明是增量轮询，同样不重建。
 */
export function shouldResetDraftBeforeReplay(
  cursor: StreamReplayCursor | null | undefined,
  draft: { hasContent: boolean },
  primed: boolean,
): boolean {
  if (primed) return false;
  if (Number(cursor?.lastSeq ?? 0) > 0) return false;
  return Boolean(draft?.hasContent);
}

/**
 * 重放前清空「累加型展示字段」，让日志能整体重建草稿。
 *
 * 只清累加字段：content / reasoningContent / processTimeline / logs /
 * processNarration* / citations —— 它们由逐条事件叠加而成，不清会在重放时翻倍。
 *
 * 刻意不清交互态（pendingPermission、pendingExternalExecution、userQuestion、
 * businessConfirmation）与身份/UI 偏好：前者是 upsert 终态，清空后重放可能把用户
 * 已经处理过的请求回退成待处理；后者是用户自己在界面上的展开选择。
 */
export function resetDraftForReplay(draft: Record<string, any> | null | undefined): void {
  if (!draft) return;
  draft.content = "";
  draft.reasoningContent = undefined;
  draft.processTimeline = [];
  draft.logs = [];
  draft.processNarration = "";
  draft.processNarrationPending = "";
  draft.citations = [];
  draft.isThinking = false;
}

export function planStreamReplay(
  events: unknown,
  cursor: StreamReplayCursor | null | undefined,
): StreamReplayPlan {
  const afterSeq = Number(cursor?.lastSeq ?? 0) || 0;
  const expectedTraceId = String(cursor?.traceId || "").trim();
  const list = Array.isArray(events) ? events : [];

  const accepted: StreamReplayEvent[] = [];
  let lastSeq = afterSeq;
  let gap = false;

  for (const raw of list) {
    const seq = readSeq(raw);
    if (seq === null || seq <= afterSeq) continue;
    const event = raw as StreamReplayEvent;
    const eventTraceId = String(event.trace_id || "").trim();
    // 上一轮残留事件不得写入本轮草稿；不带 trace 的过程事件（如 keepalive 类）照常放行。
    if (expectedTraceId && eventTraceId && eventTraceId !== expectedTraceId) {
      lastSeq = Math.max(lastSeq, seq);
      continue;
    }
    if (!accepted.length && seq > afterSeq + 1) gap = true;
    accepted.push(event);
    lastSeq = Math.max(lastSeq, seq);
  }

  return { events: accepted, lastSeq, gap };
}
