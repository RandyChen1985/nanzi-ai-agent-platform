/**
 * 生成态跑道上「小人走路」的速度换算。
 *
 * 单程时长必须随跑道宽度变化，否则速度会随宽度线性放大：原先写死 9s 时，
 * 移动端 375px 约 42px/s（用户实测「还行」），电脑端 1100px 就变成 122px/s
 * （用户实测「跑太快」）。这里固定「每秒走多少像素」，让宽度只决定单程要
 * 走多久。
 */

/** 小人每秒走过的像素数。与跑道宽度无关。 */
export const WALKER_PIXELS_PER_SECOND = 45;

/** 极窄跑道下的兜底：再短就会快得像闪烁。 */
const MIN_DURATION_SECONDS = 3;

/** 超宽屏下的兜底：再长就会慢到像静止。 */
const MAX_DURATION_SECONDS = 30;

/**
 * 按跑道宽度算出单程时长（秒）。
 *
 * @param laneWidthPx 跑道宽度；非有限值或非正数一律按 0 处理，落到最小时长。
 */
export function walkerDurationSeconds(laneWidthPx: number): number {
  const width =
    Number.isFinite(laneWidthPx) && laneWidthPx > 0 ? laneWidthPx : 0;
  const raw = width / WALKER_PIXELS_PER_SECOND;
  return Math.min(MAX_DURATION_SECONDS, Math.max(MIN_DURATION_SECONDS, raw));
}
