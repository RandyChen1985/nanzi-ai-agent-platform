/**
 * 智能体发布历史的纯函数：按自然日分组、日期标签、版本号汇总。
 *
 * 抽屉里那条连续时间线在跨天时很难扫读（一天往往连着申请、审核、发基线好几条），
 * 所以先把条目按 `at` 落到自然日，再按日期呈现。放在这里而不是组件内联，是为了能
 * 用 node 直接跑行为测试——日期标签、跨年、`at` 缺失这些边界靠 UI 手测太容易漏。
 */

export interface PublishHistoryGroupable {
  at?: string | null
  version_number?: number | null
}

export interface PublishHistoryDayGroup<T> {
  /** 本地自然日键：`2026-11-24`；缺失时间归到 `unknown` */
  key: string
  /** 该日零点的本地时间；`unknown` 组为 null */
  date: Date | null
  /** 今天 / 昨天 / 11月24日 / 2025年12月30日 */
  label: string
  /** 周一 … 周日；`unknown` 组为空串 */
  weekday: string
  items: T[]
}

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'] as const

const pad2 = (value: number) => String(value).padStart(2, '0')

/** 本地自然日键，刻意不用 toISOString：UTC 会把「凌晨/深夜」的条目挪到相邻一天。 */
export const publishDayKey = (date: Date): string =>
  `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`

const isSameLocalDay = (a: Date, b: Date): boolean =>
  a.getFullYear() === b.getFullYear() &&
  a.getMonth() === b.getMonth() &&
  a.getDate() === b.getDate()

/** 解析时间；无法解析（含空值）返回 null，由调用方归到「时间未知」组。 */
export const parsePublishTime = (value?: string | null): Date | null => {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/**
 * 日期标签：今天 / 昨天 / 同年 `11月24日` / 跨年 `2025年12月30日`。
 * 跨年必须带年份，否则「12月30日」会被读成今年。
 */
export const formatPublishDayLabel = (date: Date, now: Date = new Date()): string => {
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  if (isSameLocalDay(date, now)) return '今天'
  if (isSameLocalDay(date, yesterday)) return '昨天'
  const day = `${date.getMonth() + 1}月${date.getDate()}日`
  return date.getFullYear() === now.getFullYear() ? day : `${date.getFullYear()}年${day}`
}

/**
 * 按自然日倒序分组。
 *
 * 排序只用 `at`（申请类已审完时 `at` 自动取审核时间，与列表口径一致）；
 * 缺失或非法时间的条目统一落到末尾的「时间未知」组，不静默丢弃。
 */
export const groupEntriesByDay = <T extends PublishHistoryGroupable>(
  entries: readonly T[],
  now: Date = new Date(),
): PublishHistoryDayGroup<T>[] => {
  const groups = new Map<string, PublishHistoryDayGroup<T>>()
  const unknown: PublishHistoryDayGroup<T> = {
    key: 'unknown',
    date: null,
    label: '时间未知',
    weekday: '',
    items: [],
  }
  const timed: { ts: number; date: Date; item: T }[] = []

  for (const entry of entries) {
    const date = parsePublishTime(entry.at)
    if (!date) {
      unknown.items.push(entry)
      continue
    }
    timed.push({ ts: date.getTime(), date, item: entry })
  }

  timed.sort((a, b) => b.ts - a.ts)

  for (const { date, item } of timed) {
    const key = publishDayKey(date)
    let group = groups.get(key)
    if (!group) {
      group = {
        key,
        date: new Date(date.getFullYear(), date.getMonth(), date.getDate()),
        label: formatPublishDayLabel(date, now),
        weekday: WEEKDAYS[date.getDay()],
        items: [],
      }
      groups.set(key, group)
    }
    group.items.push(item)
  }

  const result = [...groups.values()]
  if (unknown.items.length) result.push(unknown)
  return result
}

/**
 * 组头版本号：只统计带 `version_number` 的条目（启用状态变更、变更权属人、分享类
 * 都是账号级/会话级动作，没有版本），升序去重后最多留 `limit` 个。
 */
export const collectVersionNumbers = <T extends PublishHistoryGroupable>(
  entries: readonly T[],
  limit = 3,
): { shown: number[]; more: number } => {
  const unique = [
    ...new Set(
      entries
        .map((entry) => Number(entry.version_number))
        .filter((value) => Number.isFinite(value) && value > 0),
    ),
  ].sort((a, b) => a - b)
  const safeLimit = Math.max(1, limit)
  return {
    shown: unique.slice(0, safeLimit),
    more: Math.max(0, unique.length - safeLimit),
  }
}
