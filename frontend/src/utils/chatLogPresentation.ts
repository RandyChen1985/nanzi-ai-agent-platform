import type { ProcessTimelineItem } from '@/utils/processTimeline'

export type ChatLogStatusTone = 'success' | 'error' | 'cancelled' | 'pending' | 'running' | 'default'

export interface ChatLogStatusMeta {
  label: string
  tone: ChatLogStatusTone
  dotClass: string
  textClass: string
  pillClass: string
}

const STATUS_META_MAP: Record<string, ChatLogStatusMeta> = {
  success: {
    label: '成功',
    tone: 'success',
    dotClass: 'bg-emerald-500',
    textClass: 'text-emerald-700',
    pillClass: 'bg-emerald-50 text-emerald-700 border-emerald-200/80',
  },
  error: {
    label: '异常',
    tone: 'error',
    dotClass: 'bg-red-500',
    textClass: 'text-red-600',
    pillClass: 'bg-red-50 text-red-600 border-red-200/80',
  },
  cancelled: {
    label: '已取消',
    tone: 'cancelled',
    dotClass: 'bg-slate-400',
    textClass: 'text-slate-600',
    pillClass: 'bg-slate-100 text-slate-600 border-slate-200',
  },
  interrupted: {
    label: '已中断',
    tone: 'cancelled',
    dotClass: 'bg-slate-400',
    textClass: 'text-slate-600',
    pillClass: 'bg-slate-100 text-slate-600 border-slate-200',
  },
  awaiting_user: {
    label: '待回答',
    tone: 'pending',
    dotClass: 'bg-amber-500',
    textClass: 'text-amber-700',
    pillClass: 'bg-amber-50 text-amber-700 border-amber-200/80',
  },
  awaiting_permission: {
    label: '待审批',
    tone: 'pending',
    dotClass: 'bg-amber-500',
    textClass: 'text-amber-700',
    pillClass: 'bg-amber-50 text-amber-700 border-amber-200/80',
  },
  awaiting_external_execution: {
    label: '待外部执行',
    tone: 'pending',
    dotClass: 'bg-amber-500',
    textClass: 'text-amber-700',
    pillClass: 'bg-amber-50 text-amber-700 border-amber-200/80',
  },
  running: {
    label: '执行中',
    tone: 'running',
    dotClass: 'bg-primary',
    textClass: 'text-primary',
    pillClass: 'bg-primary/10 text-primary border-primary/20',
  },
  pending: {
    label: '排队中',
    tone: 'running',
    dotClass: 'bg-primary',
    textClass: 'text-primary',
    pillClass: 'bg-primary/10 text-primary border-primary/20',
  },
}

const DEFAULT_STATUS_META: ChatLogStatusMeta = {
  label: '未知',
  tone: 'default',
  dotClass: 'bg-gray-400',
  textClass: 'text-gray-600',
  pillClass: 'bg-gray-100 text-gray-600 border-gray-200',
}

/** 统一中文化状态展示，避免 cancelled 等原样以全大写英文混排在中文状态旁。 */
export function getChatLogStatusMeta(status?: string | null): ChatLogStatusMeta {
  const normalized = (status || '').trim().toLowerCase()
  if (!normalized) return DEFAULT_STATUS_META
  const matched = STATUS_META_MAP[normalized]
  if (matched) return matched
  return {
    ...DEFAULT_STATUS_META,
    label: status!.trim(),
  }
}

export interface ParsedChatLogQuery {
  isAutomation: boolean
  taskId: string | null
  /** 列表与详情标题使用的可读摘要（自动化任务会剥离全局执行规则前缀）。 */
  displayText: string
  /** 若为自动化任务，分离出系统规则头与通知补充，便于详情区按需折叠展示。 */
  systemPrefix: string
  rawText: string
}

const TASK_CENTER_RULE_PREFIX = '【🌐 TaskCenter 自动化全局执行规则】'
const TASK_INSTRUCTION_RE = /【📋\s*任务执行指令\s*-\s*ID:\s*(\d+)】(?:@[^\n]*)?\s*\n?([\s\S]*)/
const NOTIFICATION_SUPPLEMENT_MARKERS = [
  '\n\n【📢',
  '\n\n【📨',
  '\n\n【🔔',
  '\n\n【通知',
]

function stripNotificationSupplement(text: string): string {
  let cutIndex = text.length
  for (const marker of NOTIFICATION_SUPPLEMENT_MARKERS) {
    const idx = text.indexOf(marker)
    if (idx !== -1 && idx < cutIndex) {
      cutIndex = idx
    }
  }
  return text.slice(0, cutIndex).trim()
}

/**
 * 解析聊天日志中的用户提问 / 自动化任务指令。
 *
 * TaskCenter 定时任务落库的 query 开头固定带 5 条「全局执行规则」样板文本，
 * 列表里若直接截断前 72 字，所有定时任务都会长得一模一样。
 * 这里提取出真正的任务指令正文与任务 ID，详情里保留完整原文可展开查看。
 */
export function parseChatLogQuery(rawQuery?: string | null): ParsedChatLogQuery {
  const rawText = (rawQuery || '').trim()
  if (!rawText) {
    return {
      isAutomation: false,
      taskId: null,
      displayText: '(空提问)',
      systemPrefix: '',
      rawText: '',
    }
  }

  const hasGlobalRulePrefix = rawText.startsWith(TASK_CENTER_RULE_PREFIX)
  const instructionMatch = rawText.match(TASK_INSTRUCTION_RE)

  if (!hasGlobalRulePrefix && !instructionMatch) {
    return {
      isAutomation: false,
      taskId: null,
      displayText: rawText,
      systemPrefix: '',
      rawText,
    }
  }

  if (instructionMatch) {
    const taskId = instructionMatch[1] || null
    const body = stripNotificationSupplement(instructionMatch[2] || '')
    const matchIndex = instructionMatch.index ?? 0
    const systemPrefix = rawText.slice(0, matchIndex).trim()
    return {
      isAutomation: true,
      taskId,
      displayText: body || (taskId ? `定时任务 #${taskId}` : '定时自动化任务'),
      systemPrefix,
      rawText,
    }
  }

  // 仅有全局规则前缀（如极端截断或历史数据）时仍标为定时任务，去掉开头样板头
  const cleaned = rawText
    .replace(TASK_CENTER_RULE_PREFIX, '')
    .replace(/\s+/g, ' ')
    .trim()
  return {
    isAutomation: true,
    taskId: null,
    displayText: cleaned || '定时自动化任务',
    systemPrefix: TASK_CENTER_RULE_PREFIX,
    rawText,
  }
}

export function compactSingleLine(text: string, max = 72): string {
  const normalized = (text || '').replace(/\s+/g, ' ').trim()
  if (!normalized) return '(空提问)'
  return normalized.length > max ? `${normalized.slice(0, max)}…` : normalized
}

export interface TraceEventMeta {
  label: string
  badgeClass: string
}

export function getTraceEventMeta(eventType?: string | null): TraceEventMeta {
  switch (eventType) {
    case 'thought':
      return {
        label: '思考',
        badgeClass: 'bg-primary/10 text-primary border-primary/20',
      }
    case 'tool_call':
      return {
        label: '工具调用',
        badgeClass: 'bg-amber-50 text-amber-700 border-amber-200/80',
      }
    case 'tool_result':
      return {
        label: '工具返回',
        badgeClass: 'bg-emerald-50 text-emerald-700 border-emerald-200/80',
      }
    case 'final_answer':
      return {
        label: '最终回复',
        badgeClass: 'bg-emerald-50 text-emerald-700 border-emerald-200/80',
      }
    case 'error':
      return {
        label: '执行异常',
        badgeClass: 'bg-red-50 text-red-600 border-red-200/80',
      }
    default:
      return {
        label: eventType || '步骤',
        badgeClass: 'bg-gray-100 text-gray-600 border-gray-200',
      }
  }
}

export function formatDurationMs(ms?: number | null): string {
  if (ms == null || Number.isNaN(Number(ms))) return '-'
  const rounded = Math.round(Number(ms))
  if (rounded >= 10000) {
    // 先按整数换算到 0.1s 再格式化：直接写 (12450/1000).toFixed(1) 会因 12.45 的
    // 二进制表示略小于 12.45 而得到 "12.4"，与「12.45s 应显示 12.5s」的直觉不符。
    return `${(Math.round(rounded / 100) / 10).toFixed(1)}s`
  }
  return `${rounded}ms`
}

/** 从步骤字段提取一行可读摘要，用于折叠态轨迹卡片头部快速扫视。 */
export function summarizeTraceStep(step: {
  tool_name?: string | null
  error_message?: string | null
  tool_input?: unknown
  tool_output?: unknown
  event_type?: string | null
}): string {
  if (step.error_message) {
    return compactSingleLine(step.error_message, 84)
  }

  const pickText = (payload: unknown): string => {
    if (payload == null) return ''
    if (typeof payload === 'string') {
      const trimmed = payload.trim()
      if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
        try {
          return pickText(JSON.parse(trimmed))
        } catch {
          return trimmed
        }
      }
      return trimmed
    }
    if (typeof payload === 'object') {
      const obj = payload as Record<string, unknown>
      for (const key of ['thought', 'content', 'summary', 'answer', 'query', 'path', 'command', 'sql', 'url', 'message']) {
        const val = obj[key]
        if (typeof val === 'string' && val.trim()) {
          return val.trim()
        }
      }
      const keys = Object.keys(obj)
      if (keys.length) {
        return `{ ${keys.slice(0, 4).join(', ')}${keys.length > 4 ? ', …' : ''} }`
      }
    }
    return String(payload)
  }

  const text = pickText(step.tool_input) || pickText(step.tool_output)
  if (text) return compactSingleLine(text, 84)
  return step.tool_name ? `调用 ${step.tool_name}` : (step.event_type || '执行步骤')
}

/** 统计单轮 process_timeline 中实际发生的工具/步骤数（零额外请求）。 */
export function countTurnToolSteps(timeline?: ProcessTimelineItem[] | null): number {
  if (!Array.isArray(timeline) || timeline.length === 0) return 0
  let count = 0
  for (const item of timeline) {
    if (item.kind === 'log') {
      count += 1
    } else if (item.kind === 'text' && Array.isArray(item.children)) {
      count += item.children.length
    }
  }
  return count
}
