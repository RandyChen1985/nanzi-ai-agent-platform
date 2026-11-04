export interface ChatHistoryItem {
  created_at?: string | null;
  [key: string]: unknown;
}

export interface ChatHistoryDateGroup<T extends ChatHistoryItem> {
  id: string;
  title: string;
  items: T[];
  order: number;
}

export const groupChatHistoryByDate = <T extends ChatHistoryItem>(
  history: T[],
  now = new Date(),
): ChatHistoryDateGroup<T>[] => {
  if (!history.length) return [];

  const groupsMap = {
    today: { title: "今天", items: [] as T[], order: 1 },
    yesterday: { title: "昨天", items: [] as T[], order: 2 },
    threeDays: { title: "3天前", items: [] as T[], order: 3 },
    sevenDays: { title: "7天前", items: [] as T[], order: 4 },
    older: { title: "更早", items: [] as T[], order: 5 },
  };

  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const oneDayMs = 24 * 60 * 60 * 1000;

  history.forEach((item) => {
    if (!item.created_at) {
      groupsMap.older.items.push(item);
      return;
    }
    const itemTime = new Date(item.created_at).getTime();
    const diffMs = startOfToday - itemTime;

    if (itemTime >= startOfToday) {
      groupsMap.today.items.push(item);
    } else if (diffMs < oneDayMs) {
      groupsMap.yesterday.items.push(item);
    } else if (diffMs < 3 * oneDayMs) {
      groupsMap.threeDays.items.push(item);
    } else if (diffMs < 7 * oneDayMs) {
      groupsMap.sevenDays.items.push(item);
    } else {
      groupsMap.older.items.push(item);
    }
  });

  return Object.entries(groupsMap)
    .map(([id, group]) => ({ id, ...group }))
    .filter((group) => group.items.length > 0)
    .sort((left, right) => left.order - right.order);
};

/**
 * 未落库的首轮会话：历史列表在轮次收尾前拿不到它，由前端临时补一条。
 *
 * 历史侧栏读的是 `/api/v1/chat/history`（DB 的 ai_agent_execution_history，一行一个轮次，
 * 只在 FinalizeStep 落库），而用户消息在 ContextStep 就写进了 Redis 会话记忆——于是新会话
 * 首轮在 AI 回复结束前，界面上有内容、侧栏里没有条目。这里把「该不该补」的判定收敛成纯函数。
 */
export interface PendingHistorySeed {
  conversationId?: string | null;
  firstUserQuery?: string | null;
  history?: ChatHistoryItem[] | null;
  /** 历史接口是否已成功返回过一次：没返回过就补条目会先闪一条假的。 */
  historyLoaded?: boolean;
  keyword?: string | null;
  /** 搜索 / 筛选状态下不得凭空出现，否则会被当成命中了条件。 */
  isDefaultFilters?: boolean;
  now?: Date;
}

export const PENDING_HISTORY_TITLE_MAX = 60;

const normalizePendingTitle = (raw?: string | null): string => {
  const compact = String(raw ?? "").replace(/\s+/g, " ").trim();
  if (!compact) return "";
  if (compact.length <= PENDING_HISTORY_TITLE_MAX) return compact;
  return `${compact.slice(0, PENDING_HISTORY_TITLE_MAX)}…`;
};

/**
 * 生成「当前会话临时占位」条目；不该插入时返回 `null`。
 *
 * 五个闸门缺一不可：有会话 ID、历史已加载、默认筛选、无关键词、该会话还没出现在历史里。
 */
export const resolvePendingHistoryItem = (
  seed: PendingHistorySeed,
): Record<string, unknown> | null => {
  const conversationId = String(seed.conversationId ?? "").trim();
  if (!conversationId) return null;
  if (!seed.historyLoaded) return null;
  if (!seed.isDefaultFilters) return null;
  if (String(seed.keyword ?? "").trim()) return null;
  if (
    (seed.history || []).some(
      (item) => String(item?.conversation_id ?? "") === conversationId,
    )
  ) {
    return null;
  }

  const query = normalizePendingTitle(seed.firstUserQuery);
  if (!query) return null;

  return {
    conversation_id: conversationId,
    trace_id: null,
    query,
    // 没有摘要就不能编一个：落库后由接口数据接管。
    summary: "",
    created_at: (seed.now || new Date()).toISOString(),
    turn_count: 1,
    status: "",
    // 侧栏据此隐藏置顶 / 导出 / 分享 / 回溯日志 / 删除，并改用中性状态点。
    is_pending: true,
  };
};

/** 把临时条目并到列表最前；没有临时条目时原样返回（不打乱原有顺序）。 */
export const mergePendingHistoryItem = <T extends ChatHistoryItem>(
  history: T[],
  pending: Record<string, unknown> | null | undefined,
): T[] => {
  if (!pending) return history;
  const id = String(pending.conversation_id ?? "");
  if (!id) return history;
  if (history.some((item) => String(item?.conversation_id ?? "") === id)) return history;
  return [pending as unknown as T, ...history];
};

/** 置顶会话的快照：不依赖当前分页也能渲染置顶区（见后端 PinnedConversation）。 */
export interface PinnedConversationSnapshot {
  conversation_id: string;
  title?: string;
  created_at?: string | null;
  agent_name?: string;
  turn_count?: number;
}

export interface SplitHistoryByPinResult<T extends ChatHistoryItem> {
  pinnedGroup: ChatHistoryDateGroup<T> | null;
  dateGroups: ChatHistoryDateGroup<T>[];
}

/**
 * 把历史列表拆成「置顶组 + 日期组」。
 *
 * - 置顶组按 `pins` 顺序（首位为最近置顶），列表里存在的会话用**最新数据**并标记
 *   `__pinned`；不在当前分页时，只有 `includeMissingPins` 为真才用快照兜底——
 *   搜索/筛选状态下凭空显示未命中的会话会让人误以为它命中了关键词。
 * - 日期组排除已置顶的会话，避免同一会话出现两次。
 */
export const splitHistoryByPin = <T extends ChatHistoryItem>(
  history: T[],
  pins: PinnedConversationSnapshot[],
  options: { includeMissingPins?: boolean; now?: Date } = {},
): SplitHistoryByPinResult<T> => {
  const byId = new Map<string, T>();
  history.forEach((item) => {
    const id = String(item.conversation_id ?? "");
    if (id) byId.set(id, item);
  });

  const pinnedIds = new Set((pins || []).map((pin) => pin.conversation_id));
  const pinnedItems: T[] = [];

  (pins || []).forEach((pin) => {
    const live = byId.get(pin.conversation_id);
    if (live) {
      pinnedItems.push({ ...live, __pinned: true } as T);
      return;
    }
    if (!options.includeMissingPins) return;
    pinnedItems.push(
      {
        conversation_id: pin.conversation_id,
        query: pin.title || "未命名对话",
        created_at: pin.created_at || null,
        agent_name: pin.agent_name || "",
        turn_count: pin.turn_count ?? 0,
        __pinned: true,
        is_snapshot: true,
      } as unknown as T,
    );
  });

  const rest = history.filter(
    (item) => !pinnedIds.has(String(item.conversation_id ?? "")),
  );

  return {
    pinnedGroup: pinnedItems.length
      ? { id: "pinned", title: "置顶", items: pinnedItems, order: 0 }
      : null,
    dateGroups: groupChatHistoryByDate(rest, options.now),
  };
};
