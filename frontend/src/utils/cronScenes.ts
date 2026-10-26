/**
 * 面向普通用户的 Cron 场景模型：用「场景卡片 + 少量选项」代替直接编辑五段字段。
 *
 * 只生成标准 5 段 crontab，且只覆盖 APScheduler 能可靠执行的写法：
 * 不使用 L / W / # 等扩展语法，也不支持标准 Cron 无法表达的起止日期。
 */

export type CronSceneId =
  | 'daily'
  | 'workday'
  | 'weekend'
  | 'weekly'
  | 'monthly'
  | 'multiMonthday'
  | 'multiHourly'
  | 'hourly'
  | 'everyMinute'
  | 'everyHour'
  | 'everyDay'
  | 'hourRange'
  | 'quarter'
  | 'yearly'
  | 'monthOnly'
  | 'custom';

export type CronSceneTabId = 'common' | 'cycle' | 'calendar' | 'custom';

export interface CronSceneTab {
  id: CronSceneTabId;
  label: string;
}

export interface CronSceneDefinition {
  id: CronSceneId;
  tab: CronSceneTabId;
  icon: string;
  title: string;
  desc: string;
}

export interface CronSceneState {
  hour: number;
  minute: number;
  weekdays: number[];
  monthday: number;
  month: number;
  n: number;
  dayInterval: number;
  startHour: number;
  endHour: number;
  days: number[];
  hours: number[];
  customText: string;
}

export const CRON_SCENE_TABS: CronSceneTab[] = [
  { id: 'common', label: '常用' },
  { id: 'cycle', label: '周期执行' },
  { id: 'calendar', label: '日历规则' },
  { id: 'custom', label: '自定义' },
];

export const CRON_SCENES: CronSceneDefinition[] = [
  { id: 'daily', tab: 'common', icon: '☀', title: '每天执行', desc: '每天固定时间运行' },
  { id: 'workday', tab: 'common', icon: '▦', title: '工作日执行', desc: '周一至周五运行' },
  { id: 'weekend', tab: 'common', icon: '☕', title: '周末执行', desc: '周六和周日运行' },
  { id: 'weekly', tab: 'common', icon: '▣', title: '每周指定几天', desc: '例如周一、周三、周五' },
  { id: 'monthly', tab: 'common', icon: '▤', title: '每月指定日期', desc: '例如每月 1 号' },
  { id: 'multiMonthday', tab: 'common', icon: '◫', title: '每月多个日期', desc: '例如 1 号和 15 号' },
  { id: 'multiHourly', tab: 'common', icon: '◴', title: '每天多个时间', desc: '例如早 9 点、晚 6 点' },
  { id: 'hourly', tab: 'common', icon: '◷', title: '每小时整点', desc: '每小时固定分钟' },
  { id: 'quarter', tab: 'common', icon: '◈', title: '每季度执行', desc: '1、4、7、10 月' },
  { id: 'everyMinute', tab: 'cycle', icon: '↻', title: '每隔几分钟', desc: '例如每 5 分钟' },
  { id: 'everyHour', tab: 'cycle', icon: '◴', title: '每隔几小时', desc: '例如每 2 小时' },
  { id: 'everyDay', tab: 'cycle', icon: '↻', title: '每隔几天', desc: '例如每 3 天' },
  { id: 'hourRange', tab: 'cycle', icon: '↔', title: '每天指定时段', desc: '例如 9 点到 18 点' },
  { id: 'yearly', tab: 'calendar', icon: '▣', title: '每年指定日期', desc: '例如每年 1 月 1 日' },
  { id: 'monthOnly', tab: 'calendar', icon: '◫', title: '指定月份', desc: '只在某个月运行' },
  { id: 'custom', tab: 'custom', icon: '⚙', title: '自定义 Cron', desc: '直接编辑五段表达式' },
];

export function createCronSceneState(): CronSceneState {
  return {
    hour: 9,
    minute: 0,
    weekdays: [1],
    monthday: 1,
    month: 1,
    n: 5,
    dayInterval: 3,
    startHour: 9,
    endHour: 18,
    days: [1, 15],
    hours: [9, 18],
    customText: '0 9 * * 1-5',
  };
}

function clamp(value: number, min: number, max: number): number {
  const num = Math.floor(Number(value));
  if (!Number.isFinite(num)) return min;
  return Math.min(max, Math.max(min, num));
}

function uniqueSorted(values: number[], min: number, max: number): number[] {
  const unique = new Set<number>();
  for (const value of values) {
    const num = Math.floor(Number(value));
    if (Number.isFinite(num)) unique.add(clamp(num, min, max));
  }
  return [...unique].sort((left, right) => left - right);
}

/** 根据场景和选项生成标准 5 段表达式。 */
export function buildSceneExpression(scene: CronSceneId, state: CronSceneState): string {
  const minute = clamp(state.minute, 0, 59);
  const hour = clamp(state.hour, 0, 23);

  switch (scene) {
    case 'daily':
      return `${minute} ${hour} * * *`;
    case 'workday':
      return `${minute} ${hour} * * 1-5`;
    case 'weekend':
      return `${minute} ${hour} * * 0,6`;
    case 'weekly': {
      const weekdays = uniqueSorted(state.weekdays, 0, 6);
      if (!weekdays.length) return '';
      return `${minute} ${hour} * * ${weekdays.join(',')}`;
    }
    case 'monthly':
      return `${minute} ${hour} ${clamp(state.monthday, 1, 31)} * *`;
    case 'multiMonthday': {
      const days = uniqueSorted(state.days, 1, 31);
      if (!days.length) return '';
      return `${minute} ${hour} ${days.join(',')} * *`;
    }
    case 'multiHourly': {
      const hours = uniqueSorted(state.hours, 0, 23);
      if (!hours.length) return '';
      return `${minute} ${hours.join(',')} * * *`;
    }
    case 'hourly':
      return `${minute} * * * *`;
    case 'everyMinute':
      return `*/${clamp(state.n, 1, 59)} * * * *`;
    case 'everyHour':
      return `${minute} */${clamp(state.n, 1, 23)} * * *`;
    case 'everyDay':
      return `${minute} ${hour} */${clamp(state.dayInterval, 1, 30)} * *`;
    case 'hourRange': {
      const start = clamp(state.startHour, 0, 23);
      const end = clamp(state.endHour, 0, 23);
      if (start === end) return `${minute} ${start} * * *`;
      if (start < end) return `${minute} ${start}-${end} * * *`;
      return `${minute} ${start}-23,0-${end} * * *`;
    }
    case 'quarter':
      return `${minute} ${hour} ${clamp(state.monthday, 1, 31)} 1,4,7,10 *`;
    case 'yearly':
      return `${minute} ${hour} ${clamp(state.monthday, 1, 31)} ${clamp(state.month, 1, 12)} *`;
    case 'monthOnly':
      return `${minute} ${hour} * ${clamp(state.month, 1, 12)} *`;
    case 'custom':
      return String(state.customText || '').trim();
    default:
      return '';
  }
}

function isInt(text: string, min: number, max: number): boolean {
  if (!/^\d+$/.test(text)) return false;
  const value = Number(text);
  return value >= min && value <= max;
}

function parseNumberList(text: string, min: number, max: number): number[] | null {
  if (!/^\d+(,\d+)*$/.test(text)) return null;
  const values = text.split(',').map(Number);
  if (values.some((value) => value < min || value > max)) return null;
  return values;
}

function isWeekendList(text: string): boolean {
  return text === '0,6' || text === '6,0';
}

function parseHourRange(text: string): { start: number; end: number } | null {
  const normal = /^(\d{1,2})-(\d{1,2})$/.exec(text);
  if (normal) {
    const start = Number(normal[1]);
    const end = Number(normal[2]);
    if (start >= 0 && start <= 23 && end >= 0 && end <= 23) return { start, end };
  }
  const wrap = /^(\d{1,2})-23,0-(\d{1,2})$/.exec(text);
  if (wrap) {
    const start = Number(wrap[1]);
    const end = Number(wrap[2]);
    if (start >= 0 && start <= 23 && end >= 0 && end <= 23) return { start, end };
  }
  return null;
}

/** 从已有表达式反推场景；无法识别时落到「自定义」。 */
export function inferScene(expr: string): { scene: CronSceneId; state: CronSceneState } {
  const state = createCronSceneState();
  const custom = (): { scene: CronSceneId; state: CronSceneState } => {
    state.customText = String(expr || '').trim();
    return { scene: 'custom', state };
  };

  const parts = String(expr || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length !== 5) return custom();
  const [minute = '', hour = '', day = '', month = '', weekday = ''] = parts;

  if (/^\*\/\d+$/.test(minute) && hour === '*' && day === '*' && month === '*' && weekday === '*') {
    state.n = clamp(Number(minute.slice(2)), 1, 59);
    return { scene: 'everyMinute', state };
  }

  if (isInt(minute, 0, 59) && hour === '*' && day === '*' && month === '*' && weekday === '*') {
    state.minute = Number(minute);
    return { scene: 'hourly', state };
  }

  if (isInt(minute, 0, 59) && /^\*\/\d+$/.test(hour) && day === '*' && month === '*' && weekday === '*') {
    state.minute = Number(minute);
    state.n = clamp(Number(hour.slice(2)), 1, 23);
    return { scene: 'everyHour', state };
  }

  if (isInt(minute, 0, 59) && isInt(hour, 0, 23) && /^\*\/\d+$/.test(day) && month === '*' && weekday === '*') {
    state.minute = Number(minute);
    state.hour = Number(hour);
    state.dayInterval = clamp(Number(day.slice(2)), 1, 30);
    return { scene: 'everyDay', state };
  }

  if (isInt(minute, 0, 59) && day === '*' && month === '*' && weekday === '*') {
    const hours = parseNumberList(hour, 0, 23);
    if (hours && hours.length > 1) {
      state.minute = Number(minute);
      state.hours = hours;
      return { scene: 'multiHourly', state };
    }
  }

  if (isInt(minute, 0, 59) && day === '*' && month === '*' && weekday === '*') {
    const range = parseHourRange(hour);
    if (range) {
      state.minute = Number(minute);
      state.startHour = range.start;
      state.endHour = range.end;
      return { scene: 'hourRange', state };
    }
  }

  if (!isInt(minute, 0, 59) || !isInt(hour, 0, 23)) return custom();
  state.minute = Number(minute);
  state.hour = Number(hour);

  if (day === '*' && month === '*' && weekday === '1-5') return { scene: 'workday', state };
  if (day === '*' && month === '*' && isWeekendList(weekday)) return { scene: 'weekend', state };
  if (day === '*' && month === '*' && weekday !== '*') {
    const weekdays = parseNumberList(weekday, 0, 6);
    if (weekdays && weekdays.length) {
      state.weekdays = weekdays;
      return { scene: 'weekly', state };
    }
  }
  if (isInt(day, 1, 31) && month === '*' && weekday === '*') {
    state.monthday = Number(day);
    return { scene: 'monthly', state };
  }
  if (isInt(day, 1, 31) && month === '1,4,7,10' && weekday === '*') {
    state.monthday = Number(day);
    return { scene: 'quarter', state };
  }
  if (isInt(day, 1, 31) && isInt(month, 1, 12) && weekday === '*') {
    state.monthday = Number(day);
    state.month = Number(month);
    return { scene: 'yearly', state };
  }
  if (day === '*' && isInt(month, 1, 12) && weekday === '*') {
    state.month = Number(month);
    return { scene: 'monthOnly', state };
  }
  if (day === '*' && month === '*' && weekday === '*') return { scene: 'daily', state };

  const days = parseNumberList(day, 1, 31);
  if (days && days.length > 1 && month === '*' && weekday === '*') {
    state.days = days;
    return { scene: 'multiMonthday', state };
  }

  return custom();
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** 生成面向普通用户的中文摘要。 */
export function describeScene(
  scene: CronSceneId,
  state: CronSceneState,
): { summary: string; detail: string } {
  const time = `${pad(clamp(state.hour, 0, 23))}:${pad(clamp(state.minute, 0, 59))}`;
  const minute = clamp(state.minute, 0, 59);

  switch (scene) {
    case 'daily':
      return { summary: `每天 ${time} 执行`, detail: '每天运行一次。' };
    case 'workday':
      return { summary: `每个工作日 ${time} 执行`, detail: '周一至周五运行，周末跳过。' };
    case 'weekend':
      return { summary: `每个周末 ${time} 执行`, detail: '仅周六和周日运行。' };
    case 'weekly': {
      const labels = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
      const weekdays = uniqueSorted(state.weekdays, 0, 6);
      if (!weekdays.length) return { summary: '每周（未选择）', detail: '' };
      if (weekdays.length === 1) {
        return { summary: `每${labels[weekdays[0] ?? 1]} ${time} 执行`, detail: '每周运行一次。' };
      }
      const names = weekdays.map((day) => labels[day]?.replace('周', '') ?? String(day));
      return { summary: `每周${names.join('、')} ${time} 执行`, detail: '每周这些天各运行一次。' };
    }
    case 'monthly':
      return {
        summary: `每月 ${clamp(state.monthday, 1, 31)} 号 ${time} 执行`,
        detail: '每月指定日期运行；该月没有这一天时会跳过。',
      };
    case 'multiMonthday': {
      const days = uniqueSorted(state.days, 1, 31);
      return {
        summary: `每月 ${days.join('、')} 号 ${time} 执行`,
        detail: '每月这些日期各运行一次；没有该日期的月份会跳过。',
      };
    }
    case 'multiHourly': {
      const hours = uniqueSorted(state.hours, 0, 23);
      const times = hours.map((item) => `${pad(item)}:${pad(minute)}`).join('、');
      return { summary: `每天 ${times} 执行`, detail: '同一分钟、多个整点各运行一次。' };
    }
    case 'hourly':
      return {
        summary: `每小时第 ${minute} 分钟运行`,
        detail: `每个整点的第 ${minute} 分钟触发。`,
      };
    case 'everyMinute':
      return {
        summary: `每隔 ${clamp(state.n, 1, 59)} 分钟运行一次`,
        detail: '按分钟周期触发，从整点开始计算。',
      };
    case 'everyHour':
      return {
        summary: `每隔 ${clamp(state.n, 1, 23)} 小时运行一次`,
        detail: `在每小时的第 ${minute} 分钟触发。`,
      };
    case 'everyDay':
      return {
        summary: `每隔 ${clamp(state.dayInterval, 1, 30)} 天，${time} 执行`,
        detail: '按每月日期步长计算，不是严格连续 N 天；该月没有对应日期时跳过。',
      };
    case 'hourRange': {
      const start = clamp(state.startHour, 0, 23);
      const end = clamp(state.endHour, 0, 23);
      return {
        summary: `每天 ${pad(start)}:00 至 ${pad(end)}:59，每小时第 ${minute} 分钟运行`,
        detail: start > end ? '跨午夜时段已拆成两个小时范围。' : '时段内每小时运行一次。',
      };
    }
    case 'quarter':
      return {
        summary: `每季度首月 ${clamp(state.monthday, 1, 31)} 日 ${time} 执行`,
        detail: '按 1、4、7、10 月生成，每季度运行一次。',
      };
    case 'yearly':
      return {
        summary: `每年 ${clamp(state.month, 1, 12)} 月 ${clamp(state.monthday, 1, 31)} 日 ${time} 执行`,
        detail: '每年运行一次；该年没有这一天时会跳过。',
      };
    case 'monthOnly':
      return {
        summary: `每年 ${clamp(state.month, 1, 12)} 月，每天 ${time} 执行`,
        detail: '只在选定月份运行。',
      };
    case 'custom':
      return { summary: '自定义 Cron 表达式', detail: '五段格式：分 时 日 月 周。' };
    default:
      return { summary: '', detail: '' };
  }
}
