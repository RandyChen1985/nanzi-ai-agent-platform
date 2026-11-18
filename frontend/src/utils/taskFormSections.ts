/**
 * 定时任务表单三段的纯展示逻辑。
 *
 * 摘要与红点必须同源：两处都从同一份输入计算，否则会出现「红点亮着、摘要却说
 * 一切正常」。这里不碰 DOM、不读路由，方便 Node 直接跑行为断言。
 */

export type TaskSectionKey = 'what' | 'when' | 'result'

export const TASK_SECTION_IDS: Record<TaskSectionKey, string> = {
  what: 'task-section-what',
  when: 'task-section-when',
  result: 'task-section-result',
}

const SECTION_ORDER: TaskSectionKey[] = ['what', 'when', 'result']
const PLACEHOLDER = '未填写'

export interface TaskSectionIssueInput {
  name: string
  prompt: string
  cronExpr: string
  cronValid: boolean
}

export interface TaskSectionIssueResult {
  issues: Record<TaskSectionKey, boolean>
  firstInvalid: TaskSectionKey | null
}

/**
 * 组内是否有必须补全的项。`result` 目前没有必填（渠道默认「站内」，
 * 保存前还会剔除不可用渠道），恒为 false，保留键位是为了顺序与语义完整。
 */
export function resolveTaskSectionIssues(
  input: TaskSectionIssueInput,
): TaskSectionIssueResult {
  const issues: Record<TaskSectionKey, boolean> = {
    what: !String(input.name || '').trim() || !String(input.prompt || '').trim(),
    when: !String(input.cronExpr || '').trim() || !input.cronValid,
    result: false,
  }
  const firstInvalid = SECTION_ORDER.find((key) => issues[key]) ?? null
  return { issues, firstInvalid }
}

export interface TaskSectionSummaryInput {
  name: string
  agent: string
  firstSentence: string
  when: string
  whenValid: boolean
  nextRun: string
  channels: string[]
}

export type TaskSectionSummaries = Record<TaskSectionKey, string>

function clip(text: string, max: number): string {
  const value = String(text || '').trim()
  if (value.length <= max) return value
  return `${value.slice(0, max)}…`
}

/** 收起态的一行摘要；所有文本都截断，保证标题行不换行。 */
export function buildTaskSectionSummaries(
  input: TaskSectionSummaryInput,
): TaskSectionSummaries {
  const name = clip(input.name, 20) || PLACEHOLDER
  const agent = clip(input.agent, 12) || PLACEHOLDER
  const firstSentence = clip(input.firstSentence, 40) || PLACEHOLDER
  const when = input.whenValid
    ? String(input.when || '').trim() || PLACEHOLDER
    : '表达式格式不正确'
  // 表达式非法时「下次运行」本身不可信，不拼进摘要冒充周期
  const nextRun = input.whenValid ? String(input.nextRun || '').trim() : ''
  const channels = (input.channels || []).filter(Boolean)
  return {
    what: `《${name}》· ${agent} · ${firstSentence}`,
    when: nextRun ? `${when} · 下次 ${nextRun}` : when,
    result: channels.length ? channels.join(' + ') : '不发送通知',
  }
}
