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
