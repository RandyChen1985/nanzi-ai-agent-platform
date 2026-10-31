/**
 * 主流存活性看门狗：判断「本地这条 SSE 是否还值得继续等」。
 *
 * 背景：`reader.read()` 没有空闲超时。页面被冻结/主线程停摆/连接假活时，读循环会
 * 永远挂在那一行，而恢复器被 `streamInFlight` 闸门挡住——界面停在「生成中」，
 * 只能刷新。这里把判定收敛成纯函数，便于按边界测试，避免在 1.2 万行的视图里
 * 再写一段无法验证的条件。
 *
 * 两个判据：
 * - **空闲超时**：后端在静默期每秒发 keepalive，15s 一帧都没有说明连接已不可信。
 * - **后端已收尾**：流期间低频查 run-status，明确 `active=false` 就说明本轮结束、
 *   只是本地流没读到 `[DONE]`。查询失败不会走到这里（控制器保留旧锁态）。
 *
 * 等待用户确认/外部执行时不中止：那时后端确实在等，静默是正常的。
 */

/** 后端 keepalive 间隔 1s；留 15 倍余量，避免一次 GC 或代理抖动就误杀。 */
export const STREAM_IDLE_TIMEOUT_MS = 15_000;
/** 看门狗自身检查间隔。 */
export const STREAM_LIVENESS_TICK_MS = 2_000;
/** 流期间 run-status 探测间隔。 */
export const STREAM_RUN_STATUS_PROBE_INTERVAL_MS = 10_000;

export type StreamAbortReason = "idle" | "run-finished";

export interface StreamAbortDecisionInput {
  streamInFlight: boolean;
  pendingConfirmation: boolean;
  /** 距最近一帧（含 keepalive）的毫秒数。 */
  idleMs: number;
  remoteRunActive: boolean;
  idleTimeoutMs?: number;
}

export function decideStreamAbort(input: StreamAbortDecisionInput): StreamAbortReason | null {
  if (!input.streamInFlight || input.pendingConfirmation) return null;
  const idleTimeoutMs = input.idleTimeoutMs ?? STREAM_IDLE_TIMEOUT_MS;
  if (Number.isFinite(input.idleMs) && input.idleMs >= idleTimeoutMs) return "idle";
  // 只有明确 false 才算结论：查询失败时控制器会保留旧值（true），不能据此中止。
  if (input.remoteRunActive === false) return "run-finished";
  return null;
}
