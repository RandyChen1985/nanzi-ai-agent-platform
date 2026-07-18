/** 工作台卡片展示：相对时间、类型、动作文案 */

export function formatWorkbenchRelativeTime(
  value?: string | null,
  now: Date = new Date()
): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""

  const diffMs = date.getTime() - now.getTime()
  const absSec = Math.round(Math.abs(diffMs) / 1000)
  const future = diffMs > 0

  const pick = (n: number, unit: string) =>
    future ? `${n}${unit}后` : `${n}${unit}前`

  if (absSec < 60) return future ? "即将开始" : "刚刚"
  const absMin = Math.round(absSec / 60)
  if (absMin < 60) return pick(absMin, " 分钟")
  const absHour = Math.round(absMin / 60)
  if (absHour < 24) return pick(absHour, " 小时")
  const absDay = Math.round(absHour / 24)
  if (absDay < 30) return pick(absDay, " 天")

  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function workbenchSeverityLabel(severity?: string | null): string {
  if (severity === "critical") return "紧急"
  if (severity === "warning") return "关注"
  return ""
}

export function workbenchSeverityClass(severity?: string | null): string {
  if (severity === "critical") return "bg-red-50 text-red-700 border-red-200"
  if (severity === "warning") return "bg-amber-50 text-amber-700 border-amber-200"
  return "bg-gray-50 text-gray-600 border-gray-200"
}

export function workbenchStatusLabel(status?: string | null): string {
  if (!status) return ""
  const map: Record<string, string> = {
    failed: "失败",
    unread: "未读",
    active: "进行中",
    success: "成功",
    completed: "已完成",
  }
  return map[status] || ""
}

export type WorkbenchKind = "task" | "digest" | "report" | "conversation" | "notification" | "other"

export function resolveWorkbenchKind(item: {
  type?: string | null
  action?: string | null
}): WorkbenchKind {
  const type = String(item.type || "").toLowerCase()
  const action = String(item.action || "").toLowerCase()
  if (
    type.includes("task") ||
    action === "open_task" ||
    action === "open_task_log"
  ) {
    return "task"
  }
  if (type.includes("digest") || action === "open_digest") return "digest"
  if (
    type.includes("report") ||
    action === "open_report" ||
    type.includes("analysis")
  ) {
    return "report"
  }
  if (type.includes("conversation") || action === "open_conversation") {
    return "conversation"
  }
  if (type.includes("notification")) return "notification"
  return "other"
}

export function workbenchKindLabel(kind: WorkbenchKind): string {
  const map: Record<WorkbenchKind, string> = {
    task: "任务",
    digest: "简报",
    report: "报表",
    conversation: "会话",
    notification: "通知",
    other: "事项",
  }
  return map[kind]
}

export function workbenchKindClass(kind: WorkbenchKind): string {
  const map: Record<WorkbenchKind, string> = {
    task: "bg-amber-50 text-amber-700 border-amber-200",
    digest: "bg-violet-50 text-violet-700 border-violet-200",
    report: "bg-indigo-50 text-indigo-700 border-indigo-200",
    conversation: "bg-blue-50 text-blue-700 border-blue-200",
    notification: "bg-slate-50 text-slate-600 border-slate-200",
    other: "bg-gray-50 text-gray-600 border-gray-200",
  }
  return map[kind]
}

export function workbenchKindIcon(kind: WorkbenchKind): string {
  const map: Record<WorkbenchKind, string> = {
    task: "⏱",
    digest: "📰",
    report: "📊",
    conversation: "💬",
    notification: "🔔",
    other: "•",
  }
  return map[kind]
}

export function workbenchActionLabel(item: {
  action?: string | null
  type?: string | null
}): string {
  const action = String(item.action || "")
  if (action === "open_task_log") return "查看日志"
  if (action === "open_task") return "查看任务"
  if (action === "open_digest") return "打开简报"
  if (action === "open_report") return "打开报表"
  if (action === "open_conversation") return "继续对话"
  const kind = resolveWorkbenchKind(item)
  if (kind === "task") return "查看任务"
  if (kind === "conversation") return "继续对话"
  return "查看详情"
}

const AGENT_EMOJIS = ["🤖", "📘", "📈", "🧠", "🗂️", "🛠️", "💡", "🛰️"]

export function workbenchAgentEmoji(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash + name.charCodeAt(i) * (i + 1)) % AGENT_EMOJIS.length
  }
  return AGENT_EMOJIS[hash] || "🤖"
}
