/**
 * 会话历史请求闸门。
 *
 * 旧实现只用一个 `isLoadingHistory` 布尔量拦截所有请求：切换会话时，如果上一条会话
 * 的历史请求还在飞，新会话的首屏加载会被直接丢弃，而「正在加载历史记录...」仍挂在
 * 上一条请求上。这里把「同一会话去重」和「切换会话抢占」分开：
 *
 * - 同一会话的首屏请求仍在飞：去重，避免重复覆盖；
 * - 切到另一个会话：允许新请求抢占，旧请求由调用方的请求序号 / AbortController 作废；
 * - 加载更多：只允许在没有在飞请求且还有下一页时发起（调用方保证是当前会话）。
 *
 * 保持零依赖，便于 Node 直接跑行为测试。
 */
export interface ConversationHistoryLoadState {
  targetConversationId: string;
  isLoadMore: boolean;
  hasMore: boolean;
  isLoading: boolean;
  loadingConversationId: string;
}

export function shouldStartConversationHistoryLoad(
  state: ConversationHistoryLoadState,
): boolean {
  if (!state.targetConversationId) return false;

  if (state.isLoadMore) {
    return state.hasMore && !state.isLoading;
  }

  return !(
    state.isLoading &&
    state.loadingConversationId === state.targetConversationId
  );
}
