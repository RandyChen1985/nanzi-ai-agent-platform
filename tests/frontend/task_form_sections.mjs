// 折叠摘要与错误归组的真实行为：源码字符串断言看不出来，必须调用实现。
import assert from 'node:assert/strict'

import {
  buildTaskSectionSummaries,
  resolveTaskSectionIssues,
  TASK_SECTION_IDS,
} from '../../frontend/src/utils/taskFormSections.ts'

// --- 1. 三段 id 固定，供 aria-controls 与滚动定位共用 ---
assert.deepEqual(TASK_SECTION_IDS, {
  what: 'task-section-what',
  when: 'task-section-when',
  result: 'task-section-result',
})

// --- 2. 全部填好：无错，firstInvalid 为 null ---
const ok = resolveTaskSectionIssues({
  name: 'PUE 日报',
  prompt: '查询昨日 PUE 并生成日报',
  cronExpr: '0 9 * * *',
  cronValid: true,
})
assert.deepEqual(ok.issues, { what: false, when: false, result: false })
assert.equal(ok.firstInvalid, null)

// --- 3. 只有名称空 → 归到 what ---
const noName = resolveTaskSectionIssues({
  name: '   ',
  prompt: '查询昨日 PUE',
  cronExpr: '0 9 * * *',
  cronValid: true,
})
assert.equal(noName.issues.what, true)
assert.equal(noName.firstInvalid, 'what')

// --- 4. 只有提示词空 → 同样归到 what ---
const noPrompt = resolveTaskSectionIssues({
  name: 'PUE 日报',
  prompt: '',
  cronExpr: '0 9 * * *',
  cronValid: true,
})
assert.equal(noPrompt.issues.what, true)
assert.equal(noPrompt.firstInvalid, 'what')

// --- 5. cron 空或非法 → 归到 when ---
const noCron = resolveTaskSectionIssues({
  name: 'PUE 日报',
  prompt: '查询昨日 PUE',
  cronExpr: '',
  cronValid: true,
})
assert.equal(noCron.issues.when, true)
assert.equal(noCron.firstInvalid, 'when')

const badCron = resolveTaskSectionIssues({
  name: 'PUE 日报',
  prompt: '查询昨日 PUE',
  cronExpr: 'bad cron',
  cronValid: false,
})
assert.equal(badCron.issues.when, true)
assert.equal(badCron.firstInvalid, 'when')

// --- 6. 两组都错时按 what → when 顺序取第一个 ---
const both = resolveTaskSectionIssues({
  name: '',
  prompt: '',
  cronExpr: '',
  cronValid: false,
})
assert.equal(both.firstInvalid, 'what')

// --- 7. 摘要：正常情况三段都给出人话 ---
const normal = buildTaskSectionSummaries({
  name: 'PUE 日报',
  agent: '数据分析专家',
  firstSentence: '查询昨日 PUE 并生成日报',
  when: '每天 09:00',
  whenValid: true,
  nextRun: '11-19 09:00',
  channels: ['企业微信', '邮件'],
})
assert.equal(normal.what, '《PUE 日报》· 数据分析专家 · 查询昨日 PUE 并生成日报')
assert.equal(normal.when, '每天 09:00 · 下次 11-19 09:00')
assert.equal(normal.result, '企业微信 + 邮件')

// --- 8. 摘要：缺项显示「未填写」，无下次运行只给周期，无渠道说清不发 ---
const sparse = buildTaskSectionSummaries({
  name: '',
  agent: '',
  firstSentence: '',
  when: '每天 09:00',
  whenValid: true,
  nextRun: '',
  channels: [],
})
assert.equal(sparse.what, '《未填写》· 未填写 · 未填写')
assert.equal(sparse.when, '每天 09:00')
assert.equal(sparse.result, '不发送通知')

// --- 9. 摘要：cron 非法时明确说表达式有问题，不冒充周期 ---
const badWhen = buildTaskSectionSummaries({
  name: 'PUE 日报',
  agent: '数据分析专家',
  firstSentence: '查询昨日 PUE',
  when: '表达式格式不正确',
  whenValid: false,
  nextRun: '11-19 09:00',
  channels: ['站内'],
})
assert.equal(badWhen.when, '表达式格式不正确')

// --- 10. 摘要：超长提示词首句截断到 40 字并带省略号 ---
const longText = '甲'.repeat(60)
const clipped = buildTaskSectionSummaries({
  name: '任务',
  agent: '专家',
  firstSentence: longText,
  when: '每天 09:00',
  whenValid: true,
  nextRun: '',
  channels: [],
})
assert.equal(clipped.what, `《任务》· 专家 · ${'甲'.repeat(40)}…`)

console.log('task_form_sections 行为测试通过（摘要拼装与错误归组全部符合规格）')
