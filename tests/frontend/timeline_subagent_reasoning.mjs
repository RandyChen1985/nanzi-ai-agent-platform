// 子代理思考日志不得被当成委派容器。
//
// 背景（`b87ce97b` 引入的子代理思考回流）：后端把子代理的 reasoning 作为
// `type: "log"`、`category: "reasoning"`、id 为 `subagent_reasoning:{run_id}:{seq}`
// 的日志投给父会话，前端应把它折叠挂进委派容器的 children。
//
// 但三处「按 id 前缀认委派容器」的判定写的都是 `startsWith("subagent_")`——
// 思考日志的 id 恰好也以 `subagent_` 开头，于是它被当成**容器**：合并进已有委派卡，
// 并把卡片的 status / details / 标题一起改写（委派还在跑，卡上已经因为思考日志的
// `status: "success"` 变绿；正文被最多 4000 字的思考覆盖）。
//
// 这里验证：思考日志不进容器分支，委派卡的三个字段不被它改写。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const { loadModule } = createRequire(import.meta.url)('./ts_module_loader.cjs')

const {
  isSubagentReasoningLog,
  mergeTimelineLogs,
} = loadModule('frontend/src/utils/processTimeline.ts')

// --- 1. 判定本身：三种形态都要认出来 ---
assert.equal(
  isSubagentReasoningLog({ id: 'subagent_reasoning:run-1:1' }),
  true,
  '按 id 前缀要认出思考日志',
)
assert.equal(
  isSubagentReasoningLog({ id: 'whatever', reasoning: true }),
  true,
  '按 reasoning 标记要认出思考日志',
)
assert.equal(
  isSubagentReasoningLog({ id: 'whatever', category: 'reasoning' }),
  true,
  '按 category 要认出思考日志',
)
assert.equal(isSubagentReasoningLog(null), false, '空值不能崩')
// 真正的委派容器不能被误伤
assert.equal(
  isSubagentReasoningLog({ id: 'subagent:run-1:lifecycle', category: 'agent' }),
  false,
  '委派容器本身不是思考日志',
)

// --- 2. 思考日志不得改写委派卡 ---
const delegationCard = {
  kind: 'log',
  id: 'subagent:run-1:lifecycle',
  title: '委派智能体 · 数据专家',
  details: '子代理的最终结论：错误集中在 3 号产线。',
  status: 'pending',
  category: 'agent',
  execution_time_ms: 1200,
  subagent: { run_id: 'run-1', display_name: '数据专家', agent_name: 'data_expert' },
}

const reasoningLog = {
  id: 'subagent_reasoning:run-1:1',
  title: '[数据专家] 子代理思考',
  details: '先确认表结构，再看昨天的错误分布……',
  status: 'success',
  category: 'reasoning',
  reasoning: true,
  subagent: { run_id: 'run-1', display_name: '数据专家', agent_name: 'data_expert' },
}

const merged = mergeTimelineLogs([delegationCard], [reasoningLog])

const card = merged.find((item) => item.id === 'subagent:run-1:lifecycle')
assert.ok(card, '委派卡必须还在')
assert.equal(card.status, 'pending', '思考日志的 success 不能把还在跑的委派标成完成')
assert.equal(
  card.details,
  '子代理的最终结论：错误集中在 3 号产线。',
  '思考全文不能覆盖委派结果',
)
assert.equal(card.title, '委派智能体 · 数据专家', '标题不能被改成「委派智能体 · [某某] 子代理思考」')

// --- 3. 思考日志自己仍然进时间线，但挂在委派卡下面（不是顶层并列）---
const reasoningItem = merged.find((item) => item.id === 'subagent_reasoning:run-1:1')
assert.equal(reasoningItem, undefined, '思考日志不该落在顶层')
assert.ok(
  (card.children || []).some((c) => c.id === 'subagent_reasoning:run-1:1'),
  '思考日志要折进委派卡的 children，用户展开才看得到',
)

// --- 4. 思考日志要挂进委派容器的 children，而不是和容器并列 ---
const { upsertTimelineLog } = loadModule('frontend/src/utils/processTimeline.ts')

const mkCard = () => ({
  kind: 'log',
  id: 'subagent:run-2:lifecycle',
  title: '委派智能体 · 数据专家',
  details: '',
  status: 'pending',
  category: 'agent',
  execution_time_ms: 0,
  subagent: { run_id: 'run-2', display_name: '数据专家', agent_name: 'data_expert' },
})

const target = { processTimeline: [mkCard()] }
upsertTimelineLog(target, {
  id: 'subagent_reasoning:run-2:1',
  title: '[数据专家] 子代理思考',
  details: '先看表结构。',
  status: 'success',
  category: 'reasoning',
  reasoning: true,
  subagent: { run_id: 'run-2', display_name: '数据专家', agent_name: 'data_expert' },
})

const holder = target.processTimeline.find((item) => item.id === 'subagent:run-2:lifecycle')
assert.ok(holder, '委派卡必须还在')
assert.ok(
  (holder.children || []).some((c) => c.id === 'subagent_reasoning:run-2:1'),
  '思考日志应折进委派容器，而不是落在顶层并列',
)
assert.equal(
  target.processTimeline.length,
  1,
  '顶层不该多出一个思考节点（它已经挂进 children）',
)
assert.equal(holder.status, 'pending', '挂载过程中也不能改写委派卡状态')
assert.equal(holder.details, '', '挂载过程中也不能改写委派卡正文')

// --- 5. `sub_agent_call` 的完成事件不能写进思考日志节点 ---
// 修之前：思考日志 id 以 subagent_ 开头，会被当成「已有容器」命中，
// 委派结果被写进思考节点，真正的委派卡反而拿不到最终状态。
const target2 = { processTimeline: [mkCard()] }
// 先让思考日志单独成一个节点（模拟它先于容器到达的时序）
upsertTimelineLog(target2, {
  id: 'subagent_reasoning:run-2:2',
  title: '[数据专家] 子代理思考',
  details: '第二轮思考。',
  status: 'success',
  category: 'reasoning',
  reasoning: true,
})
upsertTimelineLog(target2, {
  id: 'tool:sub_agent_call:1',
  title: '调用子代理：数据专家',
  details: '最终结论：3 号产线。',
  status: 'success',
  category: 'tool',
  tool_name: 'sub_agent_call',
  subagent: { run_id: 'run-2', display_name: '数据专家', agent_name: 'data_expert' },
})

const reasoningNode = target2.processTimeline.find(
  (item) => item.id === 'subagent_reasoning:run-2:2',
)
assert.equal(
  reasoningNode?.details,
  '第二轮思考。',
  '委派结果不能写进思考节点',
)

console.log('timeline_subagent_reasoning: all assertions passed')
