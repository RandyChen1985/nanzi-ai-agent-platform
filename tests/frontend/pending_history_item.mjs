// 「未落库的当前会话」临时历史条目的纯函数行为。
// 运行：node --experimental-strip-types --no-warnings tests/frontend/pending_history_item.mjs
//
// 背景：历史侧栏读的是 DB 的 ai_agent_execution_history，一行一个轮次，只在轮次
// 收尾时落库。于是新会话的首轮在 AI 回复结束前不会出现在历史里。这里把「当前会话
// 临时插一条」的判定与合并收敛成纯函数，逐条锁住「什么情况下不该插」——凭关键词
// 搜索时凭空冒出来、历史还没拉到就先插一条假的，都会让人以为列表错了。
import assert from 'node:assert/strict'

import {
  mergePendingHistoryItem,
  resolvePendingHistoryItem,
} from '../../frontend/src/composables/chat/useChatHistoryGroups.ts'

const now = new Date('2026-11-04T11:30:00+08:00')

const defaultSeed = {
  conversationId: 'conv_new',
  firstUserQuery: '帮我看看这个报错',
  history: [{ conversation_id: 'conv_old', query: '旧会话', created_at: '2026-11-03T10:00:00+08:00' }],
  historyLoaded: true,
  keyword: '',
  isDefaultFilters: true,
  now,
}

// 1) 首轮未落库：插一条带 is_pending 的条目，标题取首条用户提问
{
  const item = resolvePendingHistoryItem(defaultSeed)
  assert.ok(item, '新会话首轮进行中时应当生成临时条目')
  assert.equal(item.conversation_id, 'conv_new')
  assert.equal(item.query, '帮我看看这个报错')
  assert.equal(item.is_pending, true)
  assert.equal(item.trace_id, null, '未落库的轮次没有 trace_id')
  assert.equal(item.turn_count, 1)
  assert.equal(item.created_at, now.toISOString(), '时间取生成时刻，落进「今天」分组')
  assert.equal(item.summary, '', '没有摘要就不能编一个')
}

// 2) 历史里已经有这个会话（首轮已落库）：不再插，交给接口数据
{
  const item = resolvePendingHistoryItem({
    ...defaultSeed,
    history: [{ conversation_id: 'conv_new', query: '已落库', created_at: now.toISOString() }],
  })
  assert.equal(item, null, '已落库的会话不得再出现临时条目（会重复两条）')
}

// 3) 历史还没拉到：不插。否则首屏会先闪一条「假的」再被接口数据替换
{
  assert.equal(
    resolvePendingHistoryItem({ ...defaultSeed, historyLoaded: false }),
    null,
    '历史未加载完成前不应该插临时条目',
  )
}

// 4) 搜索 / 非默认筛选：不插。凭空出现会让人以为它命中了关键词或筛选条件
{
  assert.equal(
    resolvePendingHistoryItem({ ...defaultSeed, keyword: '报错' }),
    null,
    '关键词搜索时不应该出现临时条目',
  )
  assert.equal(
    resolvePendingHistoryItem({ ...defaultSeed, isDefaultFilters: false }),
    null,
    '筛选状态下不应该出现临时条目',
  )
}

// 5) 没有会话 ID，或本轮还没有用户提问（空会话 / 预览态）：不插
{
  assert.equal(resolvePendingHistoryItem({ ...defaultSeed, conversationId: '   ' }), null)
  assert.equal(resolvePendingHistoryItem({ ...defaultSeed, firstUserQuery: '   ' }), null)
  assert.equal(resolvePendingHistoryItem({ ...defaultSeed, firstUserQuery: null }), null)
}

// 6) 超长提问要截断，不能把整段正文塞进侧栏标题
{
  const long = '很长的提问'.repeat(40)
  const item = resolvePendingHistoryItem({ ...defaultSeed, firstUserQuery: long })
  assert.ok(item.query.length <= 61, '临时条目标题必须截断（60 字 + 省略号）')
  assert.ok(item.query.endsWith('…'), '截断后要有省略号')
}

// 7) 合并：插在最前；没有临时条目时原样返回；已存在同 cid 时不重复
{
  const history = [
    { conversation_id: 'conv_old', query: '旧会话' },
    { conversation_id: 'conv_pinned', query: '置顶会话' },
  ]
  const pending = resolvePendingHistoryItem(defaultSeed)

  const merged = mergePendingHistoryItem(history, pending)
  assert.equal(merged.length, 3)
  assert.equal(merged[0].conversation_id, 'conv_new', '临时条目要排在列表最前')
  assert.equal(merged[1].conversation_id, 'conv_old', '原有顺序不能被打乱')

  assert.deepEqual(
    mergePendingHistoryItem(history, null),
    history,
    '没有临时条目时必须原样返回（不能造新数组顺序）',
  )
  assert.deepEqual(
    mergePendingHistoryItem([{ conversation_id: 'conv_new' }], pending).length,
    1,
    '同会话已存在时不得重复插入',
  )
}

console.log('未落库会话临时历史条目测试全部通过')
