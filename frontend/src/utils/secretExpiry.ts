/**
 * 凭据有效期（`expires_at`）与 `<input type="datetime-local">` 的本地/UTC 双向转换工具。
 *
 * 后端按 UTC ISO 序列化（例如 `2026-12-31T15:59:00Z`），而原生日期时间控件按浏览器本地
 * 时区输入和展示；抽成单处转换避免个人/管理端各自拼接时差出错。
 */

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/** 把后端返回的 UTC ISO 字符串转成 `<input type="datetime-local">` 使用的 `YYYY-MM-DDTHH:mm`（本地时间）。 */
export function isoToLocalDateTimeInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

/** 把 `<input type="datetime-local">` 输入的本地时间转换成后端接收的 UTC ISO 字符串；空值返回 null。 */
export function localDateTimeInputToIso(localValue: string | null | undefined): string | null {
  const trimmed = String(localValue || '').trim()
  if (!trimmed) return null
  const date = new Date(trimmed)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString()
}

/** 将控件里的本地时间文本格式化为友好摘要文案（空串返回「长期有效」）。 */
export function formatLocalDateTimeSummary(localValue: string | null | undefined): string {
  const trimmed = String(localValue || '').trim()
  if (!trimmed) return '长期有效'
  return trimmed.replace('T', ' ')
}
