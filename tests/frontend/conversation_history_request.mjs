// 用 Node 真跑历史请求闸门：快速切换会话时必须让新会话抢占，
// 而不是被上一条会话的在飞请求挡住（表现为一直「正在加载历史记录...」）。
import assert from 'node:assert/strict'

import { shouldStartConversationHistoryLoad } from '../../frontend/src/utils/conversationHistoryRequest.ts'

const base = {
  targetConversationId: 'conv-a',
  isLoadMore: false,
  hasMore: true,
  isLoading: false,
  loadingConversationId: '',
}

assert.equal(
  shouldStartConversationHistoryLoad(base),
  true,
  '空闲时应允许首屏加载',
)

assert.equal(
  shouldStartConversationHistoryLoad({
    ...base,
    isLoading: true,
    loadingConversationId: 'conv-a',
  }),
  false,
  '同一会话的首屏请求在飞时应去重',
)

assert.equal(
  shouldStartConversationHistoryLoad({
    ...base,
    targetConversationId: 'conv-b',
    isLoading: true,
    loadingConversationId: 'conv-a',
  }),
  true,
  '切到另一个会话时必须允许新请求抢占，不能丢掉目标会话的历史',
)

assert.equal(
  shouldStartConversationHistoryLoad({
    ...base,
    isLoadMore: true,
    isLoading: true,
    loadingConversationId: 'conv-a',
  }),
  false,
  '加载更多不能和首屏/上拉请求并发',
)

assert.equal(
  shouldStartConversationHistoryLoad({
    ...base,
    isLoadMore: true,
    isLoading: false,
    loadingConversationId: 'conv-a',
  }),
  true,
  '同一会话空闲且还有下一页时应允许加载更多',
)

assert.equal(
  shouldStartConversationHistoryLoad({
    ...base,
    isLoadMore: true,
    isLoading: false,
    hasMore: false,
    loadingConversationId: 'conv-a',
  }),
  false,
  '没有下一页时不应再请求',
)

assert.equal(
  shouldStartConversationHistoryLoad({
    ...base,
    targetConversationId: '',
  }),
  false,
  '没有目标会话时不应请求',
)

console.log('会话历史请求闸门测试全部通过')
