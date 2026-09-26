/** 会话历史筛选：类型定义、默认值与请求参数映射的唯一来源。 */

export type HistoryFilterScope = "all" | "task" | "chat";
export type HistoryFilterStatus = "" | "success" | "failed";
export type HistoryFilterTimeRange = "" | "today" | "7d" | "30d" | "custom";

export interface ChatHistoryFilters {
  scope: HistoryFilterScope;
  agentId: string;
  status: HistoryFilterStatus;
  timeRange: HistoryFilterTimeRange;
  startDate: string;
  endDate: string;
}

export interface AgentOption {
  id: string;
  display_name: string;
  avatar_url?: string;
}

export const DEFAULT_HISTORY_FILTERS: ChatHistoryFilters = {
  scope: "all",
  agentId: "",
  status: "",
  timeRange: "",
  startDate: "",
  endDate: "",
};

const DAY_MS = 24 * 60 * 60 * 1000;

function startOfToday(now: Date): Date {
  const d = new Date(now.getTime());
  d.setHours(0, 0, 0, 0);
  return d;
}

/** 把时间范围换算为后端 start_date / end_date。 */
export function buildHistoryTimeRange(
  filters: Pick<ChatHistoryFilters, "timeRange" | "startDate" | "endDate">,
  now: Date = new Date()
): { start_date?: string; end_date?: string } {
  switch (filters.timeRange) {
    case "today":
      return {
        start_date: startOfToday(now).toISOString(),
        end_date: now.toISOString(),
      };
    case "7d":
      return {
        start_date: new Date(now.getTime() - 7 * DAY_MS).toISOString(),
        end_date: now.toISOString(),
      };
    case "30d":
      return {
        start_date: new Date(now.getTime() - 30 * DAY_MS).toISOString(),
        end_date: now.toISOString(),
      };
    case "custom": {
      const start = filters.startDate ? new Date(`${filters.startDate}T00:00:00`) : null;
      const end = filters.endDate ? new Date(`${filters.endDate}T23:59:59`) : null;
      if (start && end && start.getTime() > end.getTime()) {
        // 起止颠倒时自动校正，避免出现恒空的结果集
        return { start_date: end.toISOString(), end_date: start.toISOString() };
      }
      return {
        ...(start ? { start_date: start.toISOString() } : {}),
        ...(end ? { end_date: end.toISOString() } : {}),
      };
    }
    default:
      return {};
  }
}

/** 把筛选状态映射为历史接口查询参数；默认值不下发。 */
export function buildHistoryFilterParams(
  filters: ChatHistoryFilters,
  now: Date = new Date()
): Record<string, string> {
  const params: Record<string, string> = {};
  if (filters.scope && filters.scope !== "all") params.scope = filters.scope;
  if (filters.agentId) params.agent_id = filters.agentId;
  if (filters.status) params.status = filters.status;
  Object.assign(params, buildHistoryTimeRange(filters, now));
  return params;
}
