// 时间线递归遍历的防环回归。
//
// 背景：委派场景的时间线是「旁白 → 子代理容器 → 步骤 → 子步骤」的嵌套结构，
// 而 `upsertTimelineLog` 会按 parent_id 重挂、按 subagent 元数据合并容器。一旦某个
// 节点被挂到自己下面（或两个节点互指），所有**深度优先递归**都会变成死循环：
//   1. `collectLogsByPredicate`（工具汇总，每次 props 变化都跑）
//   2. `findTimelineLog` / `findTimelineLogInLogs`（每条日志事件都要查父节点）
// 表现就是 Chrome 的「页面无响应」，且刷新后重新 hydrate 同一批数据继续卡。
//
// 这些函数没有导出，因此用「有环结构调用汇总函数」来间接验证：没有 WeakSet 保护时
// 这里会栈溢出 / 死循环；有保护时正常返回。
import assert from 'node:assert/strict'

import {
  summarizeToolCalls,
} from '../../frontend/src/utils/processTimeline.ts'

const log = (id, category = 'tool', children = []) => ({
  kind: 'log',
  id,
  title: `log-${id}`,
  status: 'success',
  category,
  execution_time_ms: 100,
  children,
})

// --- 1. 自环：节点把自己当 child ---
const selfRef = log('self')
selfRef.children = [selfRef]

let summary = summarizeToolCalls([selfRef])
assert.equal(summary.count, 1, '自环节点应只被统计一次')

// --- 2. 互指环：a ↔ b ---
const a = log('a')
const b = log('b')
a.children = [b]
b.children = [a]

summary = summarizeToolCalls([a])
assert.equal(summary.count, 2, '互指的环应各统计一次后停止')

// --- 3. 环里夹着正常节点：不能因为环丢掉别的条目 ---
const c = log('c')
b.children = [a, c]
summary = summarizeToolCalls([a])
assert.equal(summary.count, 3, '环之外的节点仍要被统计')

// --- 5. 正常结构不受影响：非环结构照常统计 ---
const normalA = log('x')
const normalB = log('y')
normalA.children = [normalB]
assert.equal(summarizeToolCalls([normalA, normalB]).count, 2, '正常父子结构不应被去重')

console.log('timeline_cycle_guard: all assertions passed')
