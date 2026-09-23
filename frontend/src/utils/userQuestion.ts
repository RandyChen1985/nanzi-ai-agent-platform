/** Helpers for the AI-initiated question interaction. */

export interface UserQuestionOption {
  id: string;
  label: string;
  description?: string;
}

export interface UserQuestionState {
  question_id: string;
  tool_call_id?: string;
  question: string;
  options: UserQuestionOption[];
  is_multi_select: boolean;
  allow_custom_input: boolean;
  context?: string;
  status: "pending" | "submitted" | "cancelled" | "stale";
  selected_option_ids?: string[];
  custom_input?: string;
}

export const USER_QUESTION_MESSAGE_PREFIX = "【用户回答】";

function normalizeOptions(raw: unknown): UserQuestionOption[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item) => ({
      id: String(item.id || "").trim(),
      label: String(item.label || item.id || "选项").trim(),
      description: item.description ? String(item.description) : undefined,
    }))
    .filter((item) => item.id && item.label);
}

export function parseUserQuestionEvent(
  data: Record<string, unknown>,
): UserQuestionState | null {
  if (String(data.type || "") !== "user_question") return null;
  const questionId = String(data.question_id || "").trim();
  const question = String(data.question || "").trim();
  const options = normalizeOptions(data.options);
  if (!questionId || !question || options.length < 2) return null;
  const ids = new Set<string>();
  if (options.some((option) => ids.has(option.id) || (ids.add(option.id), false))) return null;
  return {
    question_id: questionId,
    tool_call_id: data.tool_call_id ? String(data.tool_call_id) : undefined,
    question,
    options,
    is_multi_select: Boolean(data.is_multi_select),
    allow_custom_input: Boolean(data.allow_custom_input),
    context: data.context ? String(data.context) : undefined,
    status: "pending",
  };
}

export function buildUserQuestionUserMessage(
  questionId: string,
  selectedOptionIds: string[],
  customInput = "",
  cancelled = false,
  question = "",
  options: UserQuestionOption[] = [],
): string {
  const lines = [USER_QUESTION_MESSAGE_PREFIX];
  if (question && question.trim()) {
    lines.push(`问题: ${question.trim()}`);
  }
  if (!cancelled && selectedOptionIds.length > 0) {
    const selectedLabels = selectedOptionIds.map((id) => {
      const opt = options.find((o) => o.id === id);
      return opt ? `${opt.label} (${id})` : id;
    });
    lines.push(`所选选项: ${selectedLabels.join("、")}`);
  }
  if (customInput && customInput.trim()) {
    lines.push(`补充说明: ${customInput.trim()}`);
  }
  lines.push(
    "interaction_type: question",
    `question_id: ${questionId.trim() || "unknown"}`,
    `selected_option_ids: ${JSON.stringify(selectedOptionIds)}`,
    `custom_input: ${customInput.trim()}`,
  );
  if (cancelled) {
    lines.push("cancelled: true", "用户取消了本次提问，请停止当前任务，不要再次询问同一个问题。");
  } else {
    lines.push("请根据以上用户回答继续处理原问题。");
  }
  return lines.join("\n");
}

export function markOtherUserQuestionsStale<
  T extends { userQuestion?: UserQuestionState },
>(messages: T[], activeQuestionId: string): void {
  for (const message of messages) {
    const question = message.userQuestion;
    if (!question || question.status !== "pending") continue;
    if (question.question_id === activeQuestionId) continue;
    question.status = "stale";
  }
}

/**
 * 从持久化的 process_timeline 日志项重建提问卡。
 *
 * 实时路径把卡片挂在消息对象上（`userQuestion`）且不落库，历史会话只能依赖
 * process_timeline 里的 `user_question` 快照；缺失或结构不完整时返回 null，
 * 由调用方退化为普通日志行展示。
 */
export function userQuestionStateFromTimelineLog(
  log: { category?: unknown; user_question?: unknown } | null | undefined,
): UserQuestionState | null {
  if (!log || String(log.category || "") !== "user_question") return null;
  const raw = log.user_question;
  if (!raw || typeof raw !== "object") return null;
  // 复用实时事件的解析校验（question_id/问题文本/至少两个选项/选项去重），
  // 避免历史回放与实时路径各有一套口径而漂移。
  return parseUserQuestionEvent({
    ...(raw as Record<string, unknown>),
    type: "user_question",
  });
}

/**
 * 遍历 process_timeline 里所有日志项（含子项），收集提问卡。
 *
 * 历史快照的提问卡可能位于子步骤（children）中，因此需要递归；返回值按出现顺序排列。
 */
export function userQuestionStatesFromTimeline(items: unknown[] | null | undefined): UserQuestionState[] {
  const states: UserQuestionState[] = [];
  const visit = (list: unknown[] | null | undefined): void => {
    if (!Array.isArray(list)) return;
    for (const item of list) {
      if (!item || typeof item !== "object") continue;
      const record = item as Record<string, unknown>;
      if (record.kind === "log") {
        const state = userQuestionStateFromTimelineLog(record);
        if (state) states.push(state);
      }
      if (Array.isArray(record.children)) visit(record.children);
    }
  };
  visit(items);
  return states;
}

/** 历史回放中的回答回执；取值均来自服务端已落库的用户消息原文。 */
export type UserQuestionReceipt = {
  question_id: string;
  selected_option_ids: string[];
  custom_input: string;
  cancelled: boolean;
};

/**
 * 解析历史用户消息里的 `【用户回答】` 回执。
 *
 * 回答本身是新一轮的用户消息，只有它能说明上一张卡片已被作答；据此把历史卡片
 * 从 pending 回填为 submitted/cancelled，避免回放后卡片仍可点击（Redis 中的
 * 待答记录其实早已过期）。
 */
export function parseUserQuestionReceipt(text: string | null | undefined): UserQuestionReceipt | null {
  const raw = String(text || "");
  if (!raw.includes(USER_QUESTION_MESSAGE_PREFIX)) return null;
  const values: Record<string, string> = {};
  for (const line of raw.split("\n")) {
    const separator = line.indexOf(":");
    if (separator <= 0) continue;
    values[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
  }
  if (values["interaction_type"] !== "question") return null;
  const questionId = (values["question_id"] || "").trim();
  if (!questionId || questionId === "unknown") return null;
  let selected: unknown;
  try {
    selected = JSON.parse(values["selected_option_ids"] || "[]");
  } catch {
    return null;
  }
  if (!Array.isArray(selected) || !selected.every((item) => typeof item === "string")) return null;
  return {
    question_id: questionId,
    selected_option_ids: selected.map((item) => item.trim()).filter(Boolean),
    custom_input: (values["custom_input"] || "").trim(),
    cancelled: (values["cancelled"] || "").toLowerCase() === "true",
  };
}

/**
 * 从时间线里退取提问文本（不依赖卡片快照）。
 *
 * 修复前落库的提问轮没有 `user_question` 快照，日志项里只剩 `details` 的问题文本；
 * 日志类只读回放据此至少说清「AI 问了什么」，避免退化成「(无响应内容)」。
 */
export function userQuestionTextFromTimeline(items: unknown[] | null | undefined): string {
  let found = "";
  const visit = (list: unknown[] | null | undefined): void => {
    if (found || !Array.isArray(list)) return;
    for (const item of list) {
      if (!item || typeof item !== "object") continue;
      const record = item as Record<string, unknown>;
      if (record.kind === "log" && String(record.category || "") === "user_question") {
        const text = String(record.details || "").trim();
        if (text) {
          found = text;
          return;
        }
      }
      if (Array.isArray(record.children)) visit(record.children);
    }
  };
  visit(items);
  return found;
}

/**
 * 把时间线里对应的提问日志项置为终态。
 *
 * 提问日志项在创建时固定为 `status: "pending"`，后端也不会再补写结果项，因此
 * 已作答的步骤在刷新后仍会显示「进行中」呼吸灯（`item.status === 'pending'`），
 * 并让 `timelineHasPending` 误判该条消息仍在执行。作答/取消后必须一并收尾。
 *
 * 匹配同时接受 id 与卡片快照：修复前落库的旧数据没有 `user_question` 字段，
 * 只能靠 `user_question_<question_id>` 这个 id 认出。
 */
export function markUserQuestionTimelineResolved(
  items: unknown[] | null | undefined,
  questionId: string,
): void {
  if (!questionId) return;
  const targetId = `user_question_${questionId}`;
  const visit = (list: unknown[] | null | undefined): void => {
    if (!Array.isArray(list)) return;
    for (const item of list) {
      if (!item || typeof item !== "object") continue;
      const record = item as Record<string, unknown>;
      if (record.kind === "log" && isUserQuestionTimelineLog(record, questionId, targetId)) {
        // 与后端「等待类步骤有结果即 success」的惯例一致（已拒绝的审批也记 success）。
        record.status = "success";
      }
      if (Array.isArray(record.children)) visit(record.children);
    }
  };
  visit(items);
}

function isUserQuestionTimelineLog(
  record: Record<string, unknown>,
  questionId: string,
  targetId: string,
): boolean {
  if (String(record.id || "") === targetId) return true;
  if (String(record.category || "") !== "user_question") return false;
  const snapshot = record.user_question;
  if (!snapshot || typeof snapshot !== "object") return false;
  return String((snapshot as Record<string, unknown>).question_id || "") === questionId;
}

/**
 * 用回答回执回填同一会话内已持久化的提问卡。
 *
 * 回执出现在后续轮次，因此按 question_id 全局匹配；同一问题的重复回执以最后一次为准。
 * 同时收尾对应的时间线日志项，避免卡片已「已提交」而步骤仍亮呼吸灯。
 */
export function applyUserQuestionReceipts<
  T extends { userQuestion?: UserQuestionState; processTimeline?: unknown },
>(messages: T[], receipts: UserQuestionReceipt[]): void {
  if (receipts.length === 0) return;
  const byQuestionId = new Map<string, UserQuestionReceipt>();
  for (const receipt of receipts) byQuestionId.set(receipt.question_id, receipt);
  for (const message of messages) {
    const question = message.userQuestion;
    if (!question) continue;
    const receipt = byQuestionId.get(question.question_id);
    if (!receipt) continue;
    question.status = receipt.cancelled ? "cancelled" : "submitted";
    question.selected_option_ids = receipt.cancelled ? [] : receipt.selected_option_ids;
    question.custom_input = receipt.cancelled ? "" : receipt.custom_input;
    markUserQuestionTimelineResolved(message.processTimeline as unknown[], question.question_id);
  }
}
