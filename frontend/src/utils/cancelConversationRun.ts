import axios from './axios'

export interface CancelConversationRunOptions {
  traceId?: string
  headers?: Record<string, string>
}

export interface CancelConversationRunResult {
  /** 是否拿到后端响应；请求异常/无响应时为 false */
  responded: boolean
  /** 后端判定本次确实做了事：取消运行 / 停画布 / 释放到锁 */
  success: boolean
  runCancelled: boolean
  canvasStopped: number
  laneReleased: boolean
  sessionLocksReleased: number
}

const failedCancelResult: CancelConversationRunResult = {
  responded: false,
  success: false,
  runCancelled: false,
  canvasStopped: 0,
  laneReleased: false,
  sessionLocksReleased: 0,
}

/**
 * Cancel the current conversation generation on the backend, then the caller
 * should abort the SSE fetch.
 *
 * 返回结构化结果，供界面区分「真的取消了运行」与「只是强行释放了残留锁」：
 * 后者（runCancelled=false、success=true）正是僵死会话的场景，文案不能写成
 * 「已终止运行」，否则用户会以为任务被取消了，其实它早已不在运行。
 *
 * 不抛异常，避免终止动作本身再打断界面。
 */
export async function cancelConversationRunDetailed(
  conversationId: string,
  options?: CancelConversationRunOptions
): Promise<CancelConversationRunResult> {
  const cid = (conversationId || '').trim()
  if (!cid) return failedCancelResult

  try {
    const res = await axios.post(
      '/api/v1/chat/cancel',
      {
        conversation_id: cid,
        trace_id: options?.traceId || undefined,
      },
      options?.headers ? { headers: options.headers } : undefined
    )
    const body = (res.data?.data || {}) as Record<string, unknown>
    return {
      responded: true,
      success: body.success === true,
      runCancelled: body.run_cancelled === true,
      canvasStopped: Number(body.canvas_stopped || 0) || 0,
      laneReleased: body.lane_released === true,
      sessionLocksReleased: Number(body.session_locks_released || 0) || 0,
    }
  } catch (e) {
    console.warn('[Chat] cancel conversation run failed', cid, e)
    return failedCancelResult
  }
}

/**
 * 兼容入口：调用方只关心「服务端是否确认释放」时使用（输入框停止、
 * 排队条目终止等既有场景保持 boolean 语义不变）。
 */
export async function cancelConversationRun(
  conversationId: string,
  options?: CancelConversationRunOptions
): Promise<boolean> {
  const cid = (conversationId || '').trim()
  if (!cid) return false

  const result = await cancelConversationRunDetailed(cid, options)
  // 判定口径：拿到后端响应即算确认。success=false 也可能是「会话刚好自然结束，
  // 没有 handle、也没有锁可删」这种幂等情形——锁确实不被占用，用户目标已达成，
  // 此时弹「服务端未确认释放结果」属于假警告。只有请求异常（拿不到响应）才需提醒。
  if (result.responded) return true
  return false
}
