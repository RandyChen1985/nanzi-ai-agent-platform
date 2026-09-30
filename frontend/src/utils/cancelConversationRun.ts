import axios from './axios'

export interface CancelConversationRunOptions {
  traceId?: string
  headers?: Record<string, string>
}

/**
 * Cancel the current conversation generation on the backend, then the caller
 * should abort the SSE fetch.
 *
 * 返回服务端是否确认取消，便于调用方给出「已终止」等可见反馈；
 * 不抛异常，避免终止动作本身再打断界面。
 */
export async function cancelConversationRun(
  conversationId: string,
  options?: CancelConversationRunOptions
): Promise<boolean> {
  const cid = (conversationId || '').trim()
  if (!cid) return false

  try {
    await axios.post(
      '/api/v1/chat/cancel',
      {
        conversation_id: cid,
        trace_id: options?.traceId || undefined,
      },
      options?.headers ? { headers: options.headers } : undefined
    )
    return true
  } catch (e) {
    console.warn('[Chat] cancel conversation run failed', cid, e)
    return false
  }
}
