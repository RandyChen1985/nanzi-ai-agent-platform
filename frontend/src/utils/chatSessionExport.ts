import type { AgentExecutionHistory } from '@/api/agent'

export interface TraceStep {
  step_number?: number
  event_type?: string
  tool_name?: string
  tool_input?: unknown
  tool_output?: unknown
  execution_time_ms?: number
  status?: string
  error_message?: string
}

export interface ChatTraceDetail {
  trace_id?: string
  steps?: TraceStep[]
}

export interface SessionExportMeta {
  agentLabel: string
  username?: string
  conversationId?: string | null
  exportedAt?: Date
}

export const formatStepPayload = (value: unknown): string => {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const fenceBody = (text: string): string => {
  const raw = text || ''
  let fence = '```'
  while (raw.includes(fence)) {
    fence += '`'
  }
  return `${fence}\n${raw}\n${fence}`
}

const statusLabel = (status?: string) => {
  if (status === 'success') return '成功'
  if (status === 'error') return '异常'
  return status || '-'
}

const formatTraceStepsMarkdown = (steps: TraceStep[] | undefined): string => {
  if (!steps?.length) return '_（暂无执行链路）_\n'
  const lines: string[] = []
  steps.forEach((step, idx) => {
    const stepNo = step.step_number ?? idx + 1
    const ms = step.execution_time_ms != null ? ` · ${Number(step.execution_time_ms).toFixed(0)}ms` : ''
    lines.push(`#### Step ${stepNo} · ${step.event_type || 'step'}${ms}`)
    if (step.tool_name) {
      lines.push('')
      lines.push(`- **工具:** \`${step.tool_name}\``)
    }
    if (step.tool_input != null && formatStepPayload(step.tool_input)) {
      lines.push('')
      lines.push('**Params / Thought**')
      lines.push('')
      lines.push(fenceBody(formatStepPayload(step.tool_input)))
    }
    if (step.tool_output != null && formatStepPayload(step.tool_output)) {
      lines.push('')
      lines.push('**Result / Answer**')
      lines.push('')
      lines.push(fenceBody(formatStepPayload(step.tool_output)))
    }
    if (step.error_message) {
      lines.push('')
      lines.push(`> **错误:** ${step.error_message}`)
    }
    lines.push('')
  })
  return lines.join('\n')
}

const formatTurnMarkdown = (
  turn: AgentExecutionHistory,
  turnIndex: number,
  trace: ChatTraceDetail | undefined,
  agentLabel: string,
): string => {
  const created = turn.created_at ? new Date(turn.created_at).toISOString() : '-'
  const execMs =
    turn.execution_time_ms != null ? `${Math.round(turn.execution_time_ms)}ms` : '-'
  const lines: string[] = [
    `## 轮次 ${turnIndex} · ${created}`,
    '',
    `- **Trace ID:** \`${turn.trace_id}\``,
    `- **智能体:** ${agentLabel}`,
    `- **状态:** ${statusLabel(turn.status)} · ${execMs}`,
  ]
  if (turn.agent_version) lines.push(`- **版本:** ${turn.agent_version}`)
  if (turn.model_id) lines.push(`- **模型:** ${turn.model_id}`)
  lines.push('', '### 用户提问', '', turn.query?.trim() || '_(空)_', '', '### AI 回复', '', turn.summary?.trim() || '_(无响应内容)_', '', '### 执行链路', '', formatTraceStepsMarkdown(trace?.steps))
  return lines.join('\n')
}

export const buildChatSessionMarkdown = (
  turns: AgentExecutionHistory[],
  tracesByTraceId: Record<string, ChatTraceDetail | undefined>,
  meta: SessionExportMeta,
): string => {
  const exportedAt = (meta.exportedAt ?? new Date()).toISOString()
  const header: string[] = [
    '# 聊天会话导出',
    '',
    `- **导出时间:** ${exportedAt}`,
  ]
  if (meta.conversationId) {
    header.push(`- **会话 ID:** \`${meta.conversationId}\``)
  }
  header.push(`- **智能体:** ${meta.agentLabel}`)
  if (meta.username) header.push(`- **用户:** ${meta.username}`)
  header.push(`- **轮次:** ${turns.length}`)
  header.push('', '---', '')

  const body = turns.map((turn, i) => {
    const label = meta.agentLabel
    return formatTurnMarkdown(turn, i + 1, tracesByTraceId[turn.trace_id], label)
  })

  return [...header, ...body].join('\n')
}

export const downloadMarkdownFile = (filename: string, content: string) => {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export const buildSessionExportFilename = (
  turns: AgentExecutionHistory[],
  conversationId?: string | null,
): string => {
  const stamp = new Date().toISOString().slice(0, 19).replace('T', '_').replace(/:/g, '-')
  if (conversationId) {
    return `chat_session_${conversationId.slice(0, 8)}_${stamp}.md`
  }
  const trace = turns[0]?.trace_id?.slice(0, 8) || 'turn'
  return `chat_turn_${trace}_${stamp}.md`
}
