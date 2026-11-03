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

export function workbenchStatusLabel(status?: string | null): string {
  if (!status) return ""
  const map: Record<string, string> = {
    failed: "失败",
    unread: "未读",
    active: "进行中",
    running: "运行中",
    pending: "待处理",
    success: "成功",
    completed: "已完成",
    scheduled: "活跃",
    stopped: "已暂停",
  }
  return map[status] || ""
}

/** 执行耗时：对齐任务中心「x分x秒」展示 */
export function formatWorkbenchDurationMs(ms?: number | null): string {
  if (ms == null || Number.isNaN(Number(ms))) return ""
  const totalMs = Math.max(0, Math.round(Number(ms)))
  if (totalMs < 1000) return totalMs === 0 ? "0秒" : `${(totalMs / 1000).toFixed(1)}秒`
  const totalSec = Math.floor(totalMs / 1000)
  const minutes = Math.floor(totalSec / 60)
  const seconds = totalSec % 60
  if (minutes <= 0) return `${seconds}秒`
  return `${minutes}分${seconds}秒`
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
    action === "open_task_log" ||
    action === "open_task_run"
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

export function workbenchActionLabel(item: {
  action?: string | null
  type?: string | null
}): string {
  const action = String(item.action || "")
  if (action === "open_task_log") return "查看日志"
  if (action === "open_task_run") return "查看记录"
  if (action === "open_task") return "查看任务"
  if (action === "open_digest") return "打开简报"
  if (action === "open_report") return "打开报表"
  if (action === "open_conversation") return "继续对话"
  const kind = resolveWorkbenchKind(item)
  if (kind === "task") return "查看任务"
  if (kind === "conversation") return "继续对话"
  return "查看详情"
}
