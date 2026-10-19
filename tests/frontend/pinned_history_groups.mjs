// 置顶分组的纯函数行为：顺序、去重、快照回退、默认筛选判定。
// 运行：node --experimental-strip-types --no-warnings tests/frontend/pinned_history_groups.mjs
import assert from 'node:assert/strict'

import {
  groupChatHistoryByDate,
  splitHistoryByPin,
} from '../../frontend/src/composables/chat/useChatHistoryGroups.ts'
import {
  DEFAULT_HISTORY_FILTERS,
  isDefaultHistoryFilters,
} from '../../frontend/src/composables/chat/useHistoryFilters.ts'

const now = new Date('2026-10-19T12:00:00+08:00')
const today = '2026-10-19T10:00:00+08:00'
const older = '2026-10-01T10:00:00+08:00'

const history = [
  { conversation_id: 'c1', query: '会话一', created_at: today, turn_count: 3 },
  { conversation_id: 'c2', query: '会话二', created_at: today, turn_count: 7 },
  { conversation_id: 'c3', query: '会话三', created_at: older, turn_count: 1 },
]

// 1) 置顶顺序完全跟随 pins（最近置顶在前），且不重复出现在日期组
{
  const pins = [
    { conversation_id: 'c2', title: '旧标题二' },
    { conversation_id: 'c1', title: '旧标题一' },
  ]
  const { pinnedGroup, dateGroups } = splitHistoryByPin(history, pins, { now })

  assert.deepEqual(
    pinnedGroup.items.map((item) => item.conversation_id),
    ['c2', 'c1'],
    '置顶组必须按 pins 顺序排列',
  )
  assert.equal(pinnedGroup.id, 'pinned')
  assert.equal(pinnedGroup.title, '置顶')
  assert.equal(pinnedGroup.order, 0)
  assert.equal(
    pinnedGroup.items[0].query,
    '会话二',
    '列表里有该会话时应使用最新数据覆盖快照标题',
  )
  assert.equal(pinnedGroup.items[0].__pinned, true)

  const dateIds = dateGroups.flatMap((group) => group.items.map((item) => item.conversation_id))
  assert.deepEqual(dateIds, ['c3'], '置顶项不得在日期组重复出现')
  assert.deepEqual(
    dateGroups.map((group) => group.title),
    ['更早'],
    '剩余项仍按日期分组',
  )
}

// 2) 不在当前分页的置顶项：默认视图用快照兜底，搜索/筛选时不显示
{
  const pins = [{ conversation_id: 'c9', title: '很久以前的会话', created_at: older, turn_count: 12 }]

  const withSnapshot = splitHistoryByPin(history, pins, { now, includeMissingPins: true })
  assert.deepEqual(
    withSnapshot.pinnedGroup.items.map((item) => item.conversation_id),
    ['c9'],
    '快照项应出现在置顶组',
  )
  assert.equal(withSnapshot.pinnedGroup.items[0].query, '很久以前的会话')
  assert.equal(withSnapshot.pinnedGroup.items[0].is_snapshot, true)
  assert.equal(withSnapshot.pinnedGroup.items[0].turn_count, 12)

  const withoutSnapshot = splitHistoryByPin(history, pins, { now, includeMissingPins: false })
  assert.equal(withoutSnapshot.pinnedGroup, null, '搜索/筛选时不得凭空显示未命中的快照项')
}

// 3) 没有置顶时整组不出现，日期分组保持原样
{
  const { pinnedGroup, dateGroups } = splitHistoryByPin(history, [], { now })
  assert.equal(pinnedGroup, null)
  assert.deepEqual(
    dateGroups.map((group) => [group.id, group.items.length]),
    groupChatHistoryByDate(history, now).map((group) => [group.id, group.items.length]),
    '空置顶时日期分组应与原函数完全一致',
  )
}

// 4) 默认筛选判定
{
  assert.equal(isDefaultHistoryFilters({ ...DEFAULT_HISTORY_FILTERS }), true)
  assert.equal(isDefaultHistoryFilters({ ...DEFAULT_HISTORY_FILTERS, scope: 'task' }), false)
  assert.equal(isDefaultHistoryFilters({ ...DEFAULT_HISTORY_FILTERS, agentId: 'a1' }), false)
  assert.equal(isDefaultHistoryFilters({ ...DEFAULT_HISTORY_FILTERS, status: 'failed' }), false)
  assert.equal(isDefaultHistoryFilters({ ...DEFAULT_HISTORY_FILTERS, timeRange: '7d' }), false)
  assert.equal(isDefaultHistoryFilters({ ...DEFAULT_HISTORY_FILTERS, startDate: '2026-10-01' }), false)
  assert.equal(isDefaultHistoryFilters({ ...DEFAULT_HISTORY_FILTERS, endDate: '2026-10-19' }), false)
}

console.log('pinned_history_groups: all assertions passed')
