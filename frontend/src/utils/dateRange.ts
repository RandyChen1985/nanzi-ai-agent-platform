/**
 * 纯日期（本地时区）工具。
 *
 * Token 统计的区间参数是「日历日」而不是时间点，所有计算都按本地 00:00 对齐。
 * 直接用 `new Date("2026-11-05")` 会被当成 UTC 零点解析，在东八区会退回前一天，
 * 所以这里统一走「解析 → 本地 Date → 再格式化」的路径。
 */

export const DAY_MS = 86400000;

/** Date → YYYY-MM-DD（本地时区） */
export function formatDayKey(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** YYYY-MM-DD → 本地 00:00 的 Date；格式不对时返回 Invalid Date */
export function parseDayKey(key: string): Date {
  const [y, m, d] = (key || "").split("-").map((part) => Number(part));
  if (!y || !m || !d) return new Date(NaN);
  return new Date(y, m - 1, d);
}

/** 去掉时分秒 */
export function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function addDays(date: Date, days: number): Date {
  const next = startOfDay(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

export function endOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth() + 1, 0);
}

/** 含首尾的天数：2026-11-01 ~ 2026-11-05 = 5；非法输入返回 0 */
export function inclusiveDaySpan(startKey: string, endKey: string): number {
  const start = parseDayKey(startKey);
  const end = parseDayKey(endKey);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 0;
  return Math.round((startOfDay(end).getTime() - startOfDay(start).getTime()) / DAY_MS) + 1;
}

/** 本周一（周一为一周第一天） */
export function startOfWeek(date: Date): Date {
  const base = startOfDay(date);
  const offset = (base.getDay() + 6) % 7;
  return addDays(base, -offset);
}

export interface DateRangePresetDef {
  key: string;
  label: string;
  start: string;
  end: string;
}

/**
 * 快捷区间的唯一定义。
 *
 * Token 统计（今日/近 N 天）与概览（今日/本周/本月）用的是同一批口径，
 * 各写一套迟早会出现「同一页面上『本周』指的是滚动 7 天」这种错位。
 */
export function buildRangePresets(now: Date): DateRangePresetDef[] {
  const t = startOfDay(now);
  const thisWeekStart = startOfWeek(t);
  const lastWeekStart = addDays(thisWeekStart, -7);
  const lastMonthAnchor = addDays(startOfMonth(t), -1);
  const day = (date: Date) => formatDayKey(date);

  return [
    { key: "today", label: "今日", start: day(t), end: day(t) },
    { key: "yesterday", label: "昨日", start: day(addDays(t, -1)), end: day(addDays(t, -1)) },
    { key: "thisWeek", label: "本周", start: day(thisWeekStart), end: day(t) },
    { key: "lastWeek", label: "上周", start: day(lastWeekStart), end: day(addDays(thisWeekStart, -1)) },
    { key: "last7", label: "最近 7 天", start: day(addDays(t, -6)), end: day(t) },
    { key: "last14", label: "最近 14 天", start: day(addDays(t, -13)), end: day(t) },
    { key: "last30", label: "最近 30 天", start: day(addDays(t, -29)), end: day(t) },
    { key: "last90", label: "最近 90 天", start: day(addDays(t, -89)), end: day(t) },
    { key: "thisMonth", label: "本月", start: day(startOfMonth(t)), end: day(t) },
    {
      key: "lastMonth",
      label: "上月",
      start: day(startOfMonth(lastMonthAnchor)),
      end: day(endOfMonth(lastMonthAnchor)),
    },
  ];
}

/** YYYY-MM-DD → MM/DD（触发器上的副文案） */
export function formatMonthDay(key: string): string {
  const [, m, d] = (key || "").split("-");
  if (!m || !d) return "";
  return `${m}/${d}`;
}

/** 区间是否合法：两端齐全、起止不倒置、天数不超过上限 */
export function isRangeValid(startKey: string, endKey: string, maxDays: number): boolean {
  if (!startKey || !endKey) return false;
  if (endKey < startKey) return false;
  const span = inclusiveDaySpan(startKey, endKey);
  return span > 0 && span <= maxDays;
}
