/**
 * run-status 收敛助推策略。
 *
 * `remoteRunActive` 由 run-status 轮询驱动（间隔 1.5s）。正常情况下 SSE 的 `run_status`
 * 终态事件会立即把它翻回 false，但切后台、代理缓冲或流异常中断会让这一帧丢失，此后
 * 前端只能等下一次轮询——这段时间里快捷按钮（quick button）点击会被发送守卫拦下。
 *
 * 流结束时刻意不直接清零该标记：流被中断时后台 producer 可能仍在运行，直接放开会与
 * 服务端会话锁冲突。因此改为「核验 + 有限次退避重试」，是否空闲一律以后端为准。
 */

/** 流结束后最多补验次数。 */
export const RUN_STATUS_NUDGE_MAX_ATTEMPTS = 3;

/** 相邻两次补验的间隔（毫秒）。 */
export const RUN_STATUS_NUDGE_INTERVAL_MS = 500;

/**
 * 是否还需要继续补验。
 *
 * @param remoteRunActive 后端 run-status 当前报告是否仍在运行
 * @param attempt 已完成的补验次数
 */
export function shouldContinueRunStatusNudge(
  remoteRunActive: boolean,
  attempt: number,
): boolean {
  if (!remoteRunActive) return false;
  return attempt < RUN_STATUS_NUDGE_MAX_ATTEMPTS;
}
