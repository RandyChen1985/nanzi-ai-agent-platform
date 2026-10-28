/**
 * 会话分享的纯函数：有效期换算、密码生成、状态推导、剩余时间文案。
 *
 * 抽成纯函数而不是写在组件里，是为了让 node 能直接跑行为测试——这几个换算前后端
 * 必须一致，错一天用户就会看到「弹窗写 7 天、列表显示 6 天后过期」。
 *
 * 保持零依赖（不引 Vue、不引 axios），便于 Node 直接执行。
 */

export type ExpiryPresetValue = '1h' | '24h' | '7d' | '30d' | 'permanent';

export interface ExpiryChoice {
  mode: 'preset' | 'custom' | 'permanent';
  preset?: ExpiryPresetValue;
  value?: number;
  unit?: 'hour' | 'day';
}

export interface ExpiryPreset {
  value: ExpiryPresetValue;
  label: string;
}

export type ShareStatus = 'active' | 'expired' | 'revoked';

export const EXPIRY_PRESETS: ExpiryPreset[] = [
  { value: '1h', label: '1 小时' },
  { value: '24h', label: '24 小时' },
  { value: '7d', label: '7 天' },
  { value: '30d', label: '30 天' },
  { value: 'permanent', label: '永久' },
];

const PRESET_MS: Record<Exclude<ExpiryPresetValue, 'permanent'>, number> = {
  '1h': 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
};

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

/** 与服务端 `SHARE_SECRET_ALPHABET` 完全一致：去掉 0/O/1/l/I。 */
export const SHARE_SECRET_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789';
export const SHARE_SECRET_LENGTH = 8;

/**
 * 把弹窗里的有效期选择换算成绝对过期时间；`null` 表示永久有效。
 *
 * 自定义值非法（非数字、<= 0）时按「不过期」处理：宁可让分享活得久一点，
 * 也不要因为输入框空着就悄悄生成一个 1 毫秒后失效的链接。
 */
export function resolveExpiry(choice: ExpiryChoice, now: Date = new Date()): Date | null {
  if (choice.mode === 'permanent') return null;
  if (choice.mode === 'custom') {
    const value = Number(choice.value);
    if (!Number.isFinite(value) || value <= 0) return null;
    const unitMs = choice.unit === 'hour' ? HOUR_MS : DAY_MS;
    return new Date(now.getTime() + value * unitMs);
  }
  const preset = (choice.preset || '7d') as ExpiryPresetValue;
  if (preset === 'permanent') return null;
  const delta = PRESET_MS[preset];
  return delta ? new Date(now.getTime() + delta) : null;
}

/** 给人看的有效期文案，如「7 天后失效」「永久有效」。 */
export function describeExpiry(choice: ExpiryChoice): string {
  if (choice.mode === 'permanent') return '永久有效';
  if (choice.mode === 'custom') {
    const value = Number(choice.value);
    return `${value} ${choice.unit === 'hour' ? '小时后' : '天后'}失效`;
  }
  const preset = EXPIRY_PRESETS.find((item) => item.value === (choice.preset || '7d'));
  if (!preset) return '7 天后失效';
  if (preset.value === 'permanent') return '永久有效';
  return `${preset.label}后失效`;
}

/**
 * 生成访问密码：字符集与服务端一致，去掉易混字符，避免用户抄写时把 0 当成 O。
 *
 * `random` 可注入，测试用它验证字符集与长度，生产默认 `Math.random`。
 */
export function generateShareSecret(
  length: number = SHARE_SECRET_LENGTH,
  random: () => number = Math.random,
): string {
  let out = '';
  for (let i = 0; i < length; i += 1) {
    const index =
      Math.floor(random() * SHARE_SECRET_ALPHABET.length) % SHARE_SECRET_ALPHABET.length;
    out += SHARE_SECRET_ALPHABET[index];
  }
  return out;
}

/**
 * 由列表行推导展示状态：作废优先于过期——已作废的链接不该再说「已过期」，
 * 否则用户会以为是等一等就能恢复。
 */
export function deriveShareStatus(
  row: { status?: string | null; expires_at?: string | null },
  now: Date = new Date(),
): ShareStatus {
  if (String(row?.status || '') === 'revoked') return 'revoked';
  const expiresAt = row?.expires_at ? new Date(row.expires_at) : null;
  if (expiresAt && !Number.isNaN(expiresAt.getTime()) && expiresAt.getTime() <= now.getTime()) {
    return 'expired';
  }
  return 'active';
}

/** 剩余时间文案，如「2 天后过期」「5 小时后过期」「已过期」。 */
export function formatRemaining(
  expiresAt: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!expiresAt) return '永久有效';
  const target = new Date(expiresAt);
  if (Number.isNaN(target.getTime())) return '永久有效';
  const diff = target.getTime() - now.getTime();
  if (diff <= 0) return '已过期';
  const minutes = Math.floor(diff / 60000);
  if (minutes < 60) return `${Math.max(1, minutes)} 分钟后过期`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时后过期`;
  return `${Math.floor(hours / 24)} 天后过期`;
}
