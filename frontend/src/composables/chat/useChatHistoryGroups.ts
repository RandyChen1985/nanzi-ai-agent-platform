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
