/**
 * 分享页的时间展示口径。
 *
 * 为什么不复用 `utils/date.ts` 的 `formatDate`：那个是相对时间（「3 天前」），
 * 用于列表里「多久以前更新过」。分享页的场景相反——收链接的人不知道分享是什么
 * 时候发生的，需要的是**绝对时刻**（这段对话发生在哪天几点），相对时间反而没信息量。
 *
 * 时区：快照里存的是后端 `isoformat()` 的本地时间字符串，`new Date()` 会按浏览器
 * 本地时区解析，对国内用户即北京时间，不额外做时区换算。
 */

const PAD = (value: number): string => String(value).padStart(2, '0')

/** 解析 ISO 字符串；无效或空值返回 null（调用方据此跳过渲染，不显示 "Invalid Date"）。 */
export function parseShareTime(raw: string | null | undefined): Date | null {
  if (!raw) return null
  const date = new Date(raw)
  return Number.isNaN(date.getTime()) ? null : date
}

/** `2026-10-28 14:23`；无效时间返回空串。 */
export function formatShareDateTime(raw: string | null | undefined): string {
  const date = parseShareTime(raw)
  if (!date) return ''
  return `${date.getFullYear()}-${PAD(date.getMonth() + 1)}-${PAD(date.getDate())} ${PAD(date.getHours())}:${PAD(date.getMinutes())}`
}

/** `14:23`；无效时间返回空串。 */
export function formatShareClock(raw: string | null | undefined): string {
  const date = parseShareTime(raw)
  if (!date) return ''
  return `${PAD(date.getHours())}:${PAD(date.getMinutes())}`
}

/** `2026-10-28`；无效时间返回空串。 */
export function formatShareDay(raw: string | null | undefined): string {
  const date = parseShareTime(raw)
  if (!date) return ''
  return `${date.getFullYear()}-${PAD(date.getMonth() + 1)}-${PAD(date.getDate())}`
}

/** 两个时刻是否落在同一个自然日；任一无效时返回 false（宁可多打一次日期）。 */
export function isSameShareDay(a: string | null | undefined, b: string | null | undefined): boolean {
  const left = formatShareDay(a)
  const right = formatShareDay(b)
  return Boolean(left) && left === right
}

/**
 * 单条消息的时间标签。
 *
 * 同一天的对话只显示 `HH:mm`，跨天时在最前面补 `MM-DD`——一屏里堆满完整日期很难读，
 * 但完全不显示日期又会让人分不清这是今天还是上周的对话。
 */
export function formatShareMessageStamp(
  raw: string | null | undefined,
  previousRaw?: string | null,
): string {
  const clock = formatShareClock(raw)
  if (!clock) return ''
  if (!previousRaw) return `${formatShareDay(raw)} ${clock}`
  if (isSameShareDay(raw, previousRaw)) return clock
  const day = formatShareDay(raw)
  return day ? `${day.slice(5)} ${clock}` : clock
}
