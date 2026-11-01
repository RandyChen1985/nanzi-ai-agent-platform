/**
 * 标准 5 段 crontab 的校验与下次执行时间预览。
 *
 * - 字段顺序：分 时 日 月 周
 * - 星期字段按标准 crontab：0/7 = 周日，1 = 周一 … 6 = 周六
 * - 调度侧由后端 app/services/cron_compat.py 把该星期语义翻译给 APScheduler，
 *   前端不要自行做数字偏移，否则预览与实际执行会再次错开一天
 * - 「日」和「周」不能同时限定：APScheduler 对二者按 AND 匹配，与标准 crontab 的 OR 不同
 */

import { getPlatformTimezone } from '@/utils/platformTimezone';

export type CronFieldKey = 'minute' | 'hour' | 'day' | 'month' | 'weekday';

export interface CronFieldDefinition {
  key: CronFieldKey;
  label: string;
  min: number;
  max: number;
  allowSeven?: boolean;
  aliases?: Record<string, number>;
}

export const WEEKDAY_LABELS: Record<number, string> = {
  0: '周日',
  1: '周一',
  2: '周二',
  3: '周三',
  4: '周四',
  5: '周五',
  6: '周六',
};

const MONTH_NAMES = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

const MONTH_ALIASES: Record<string, number> = Object.fromEntries(
  MONTH_NAMES.map((name, index) => [name, index + 1]),
);

const WEEKDAY_ALIASES: Record<string, number> = {
  sun: 0,
  mon: 1,
  tue: 2,
  wed: 3,
  thu: 4,
  fri: 5,
  sat: 6,
};

export const CRON_FIELD_DEFS: CronFieldDefinition[] = [
  { key: 'minute', label: '分钟', min: 0, max: 59 },
  { key: 'hour', label: '小时', min: 0, max: 23 },
  { key: 'day', label: '日', min: 1, max: 31 },
  { key: 'month', label: '月', min: 1, max: 12, aliases: MONTH_ALIASES },
  { key: 'weekday', label: '周', min: 0, max: 6, allowSeven: true, aliases: WEEKDAY_ALIASES },
];

export interface CronValidation {
  valid: boolean;
  message: string;
}

function isFieldEvery(text: string): boolean {
  const value = String(text || '').trim();
  return value === '*' || value === '?';
}

function normalizeFieldValue(value: number, def: CronFieldDefinition): number {
  if (def.key === 'weekday' && value === 7) return 0;
  return value;
}

function parseFieldToken(token: string, def: CronFieldDefinition): number {
  const text = token.trim().toLowerCase();
  if (!text) throw new Error(`${def.label}取值不能为空`);
  const alias = def.aliases?.[text];
  if (alias !== undefined) return alias;
  if (!/^\d+$/.test(text)) throw new Error(`${def.label}取值「${token}」不合法`);
  const value = Number(text);
  const upper = def.allowSeven ? 7 : def.max;
  if (value < def.min || value > upper) {
    throw new Error(`${def.label}取值需在 ${def.min}-${upper} 之间`);
  }
  return normalizeFieldValue(value, def);
}

/** 把单个字段展开为具体取值集合；非法时抛错。 */
export function expandCronField(text: string, def: CronFieldDefinition): Set<number> {
  const raw = String(text || '').trim();
  if (!raw) throw new Error(`${def.label}不能为空`);
  if (raw !== '?' && raw.includes('?')) throw new Error(`${def.label}中 ? 只能单独使用`);
  if (raw === '?' && def.key !== 'day' && def.key !== 'weekday') {
    throw new Error(`${def.label}不支持 ? 占位符`);
  }

  const values = new Set<number>();
  for (const chunk of raw.split(',')) {
    const part = chunk.trim();
    if (!part) throw new Error(`${def.label}存在空列表项`);

    let base = part;
    let step = 1;
    const slashIndex = part.indexOf('/');
    if (slashIndex >= 0) {
      base = part.slice(0, slashIndex).trim();
      const stepText = part.slice(slashIndex + 1).trim();
      if (!/^\d+$/.test(stepText)) throw new Error(`${def.label}步长「${stepText}」不合法`);
      step = Number(stepText);
      if (step <= 0) throw new Error(`${def.label}步长必须为正整数`);
    }

    let start: number;
    let end: number;
    if (base === '*' || base === '?') {
      start = def.min;
      end = def.max;
    } else if (base.includes('-')) {
      const [startText = '', endText = ''] = base.split('-');
      start = parseFieldToken(startText, def);
      end = parseFieldToken(endText, def);
    } else {
      start = parseFieldToken(base, def);
      end = slashIndex >= 0 ? def.max : start;
    }

    if (start > end) throw new Error(`${def.label}范围起始值不能大于结束值`);
    for (let value = start; value <= end; value += step) {
      values.add(normalizeFieldValue(value, def));
    }
  }

  if (!values.size) throw new Error(`${def.label}没有可用取值`);
  return values;
}

export function validateCronExpression(expr: string): CronValidation {
  const parts = String(expr || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return { valid: false, message: '请输入 Cron 表达式' };
  if (parts.length !== 5) {
    return { valid: false, message: '需要 5 段：分 时 日 月 周（例如 0 9 * * 1-5）' };
  }

  for (let index = 0; index < CRON_FIELD_DEFS.length; index += 1) {
    const def = CRON_FIELD_DEFS[index];
    const part = parts[index];
    if (!def || part === undefined) return { valid: false, message: '表达式段数不正确' };
    try {
      expandCronField(part, def);
    } catch (error) {
      const message = error instanceof Error ? error.message : '格式不正确';
      return { valid: false, message: `第 ${index + 1} 段（${def.label}）：${message}` };
    }
  }

  if (!isFieldEvery(parts[2] ?? '') && !isFieldEvery(parts[4] ?? '')) {
    return {
      valid: false,
      message: '暂不支持同时限定「日」和「周」：请只保留一个维度（两者同时指定时调度器按 AND 匹配，与标准 crontab 的 OR 不同）',
    };
  }

  return { valid: true, message: '' };
}

interface ParsedCronSets {
  minute: Set<number> | null;
  hour: Set<number> | null;
  day: Set<number> | null;
  month: Set<number> | null;
  weekday: Set<number> | null;
}

interface ZonedWallClock {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
}

function parseCronSets(expr: string): ParsedCronSets | null {
  const parts = String(expr || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length !== 5) return null;
  const sets = {} as ParsedCronSets;
  const target = sets as Record<CronFieldKey, Set<number> | null>;
  for (let index = 0; index < CRON_FIELD_DEFS.length; index += 1) {
    const def = CRON_FIELD_DEFS[index];
    const part = parts[index];
    if (!def || part === undefined) return null;
    if (isFieldEvery(part)) {
      target[def.key] = null;
      continue;
    }
    try {
      target[def.key] = expandCronField(part, def);
    } catch {
      return null;
    }
  }
  return sets;
}

const zonedPartsFormatterCache = new Map<string, Intl.DateTimeFormat>();

function getZonedParts(date: Date, timeZone: string): ZonedWallClock {
  let formatter = zonedPartsFormatterCache.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    });
    zonedPartsFormatterCache.set(timeZone, formatter);
  }
  const parts = formatter.formatToParts(date);
  const pick = (type: string) => Number(parts.find((part) => part.type === type)?.value || 0);
  return {
    year: pick('year'),
    month: pick('month'),
    day: pick('day'),
    hour: pick('hour') % 24,
    minute: pick('minute'),
  };
}

function zonedWallClockToDate(wall: ZonedWallClock, timeZone: string): Date {
  const targetAsUtc = Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute);
  let timestamp = targetAsUtc;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const actual = getZonedParts(new Date(timestamp), timeZone);
    const actualAsUtc = Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute);
    timestamp += targetAsUtc - actualAsUtc;
  }
  return new Date(timestamp);
}

function sortedValues(values: Set<number> | null, min: number, max: number): number[] {
  if (!values) {
    const all: number[] = [];
    for (let value = min; value <= max; value += 1) all.push(value);
    return all;
  }
  return [...values].sort((left, right) => left - right);
}

/** 计算接下来若干次执行时间（按平台业务时区）；表达式非法时返回空数组。 */
export function nextCronRunTimes(
  expr: string,
  count = 3,
  fromDate: Date = new Date(),
  timeZone: string = getPlatformTimezone(),
): Date[] {
  if (count <= 0) return [];
  if (!validateCronExpression(expr).valid) return [];
  const sets = parseCronSets(expr);
  if (!sets) return [];
  if (sets.day && sets.weekday) return [];

  const hours = sortedValues(sets.hour, 0, 23);
  const minutes = sortedValues(sets.minute, 0, 59);
  const months = sets.month;
  const days = sets.day;
  const weekdays = sets.weekday;

  // 从下一个整分钟开始，避免把已经开始的那一分钟算进来
  const startMs = Math.floor(fromDate.getTime() / 60000) * 60000 + 60000;
  const startWall = getZonedParts(new Date(startMs), timeZone);
  const startMinutesOfDay = startWall.hour * 60 + startWall.minute;

  const results: Date[] = [];
  const maxDays = 366 * 5;
  for (let offset = 0; offset <= maxDays && results.length < count; offset += 1) {
    const calendar = new Date(Date.UTC(startWall.year, startWall.month - 1, startWall.day) + offset * 86400000);
    const year = calendar.getUTCFullYear();
    const month = calendar.getUTCMonth() + 1;
    const day = calendar.getUTCDate();
    if (months && !months.has(month)) continue;

    const dayOfWeek = calendar.getUTCDay(); // 0 = 周日，与标准 crontab 一致
    const dayMatches = days ? days.has(day) : true;
    const weekdayMatches = weekdays ? weekdays.has(dayOfWeek) : true;
    if (!dayMatches || !weekdayMatches) continue;

    for (const hour of hours) {
      for (const minute of minutes) {
        if (offset === 0 && hour * 60 + minute < startMinutesOfDay) continue;
        results.push(zonedWallClockToDate({ year, month, day, hour, minute }, timeZone));
        if (results.length >= count) break;
      }
      if (results.length >= count) break;
    }
  }

  return results;
}

export function formatCronRunTime(date: Date, timeZone: string = getPlatformTimezone()): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date);
}
