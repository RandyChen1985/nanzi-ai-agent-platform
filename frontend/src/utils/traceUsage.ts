/**
 * 轨迹里的「图像用量」口径。
 *
 * 后端 `image_generation_tool` 把一次生图/改图调用的计量写进
 * `ai_agent_execution_traces.meta_info.image_usage`（张数、尺寸、上游 token、耗时）。
 * 两个轨迹视图都要展示同一份东西——AgentDebug 用的 `TraceLogViewer`、聊天日志页
 * `ChatLogs`——所以口径只能有一处，避免改一边忘一边。
 *
 * 三条刻意的口径，改之前先确认需求：
 * - **没有用量就什么都不返回**：绝大多数工具没有这一项，不能平白多一个空块；
 * - **只出一部分时显示 `1/2`**：只说「1 张」会把「另一张没出来」盖掉；
 * - **上游没给 token 就明说**：显示 0 会被读成「这次没花钱」。
 */

export type ImageUsageMeta = {
  requested_count?: number;
  succeeded_count?: number;
  size?: string;
  model?: string;
  upstream_tokens?: { total_tokens?: number } | null;
  elapsed_ms?: number;
};

/** 从一条轨迹步骤里取图像用量；老轨迹没有这一项，一律当作「没有」。 */
export function imageUsageFromStep(step: unknown): ImageUsageMeta | null {
  const meta = (step as { meta_info?: Record<string, unknown> | null } | null
    | undefined)?.meta_info;
  const usage = meta?.image_usage;
  return usage && typeof usage === "object" ? (usage as ImageUsageMeta) : null;
}

export function formatUsageDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

/** 用量摘要 chips：出图张数（含请求张数）、尺寸、token、耗时。 */
export function imageUsageChips(step: unknown): string[] {
  const usage = imageUsageFromStep(step);
  if (!usage) return [];

  const done = Number(usage.succeeded_count ?? 0);
  const asked = Number(usage.requested_count ?? done);
  const chips = [asked > done ? `出图 ${done}/${asked} 张` : `出图 ${done} 张`];

  if (usage.size) chips.push(usage.size);

  const tokens = usage.upstream_tokens?.total_tokens;
  chips.push(tokens ? `${tokens} tokens` : "上游未返回 token 用量");

  const duration = formatUsageDuration(Number(usage.elapsed_ms ?? 0));
  if (duration) chips.push(duration);

  return chips;
}
