/**
 * 登录密码到期提醒的统一文案解析。
 *
 * 同一个密码状态此前在「登录即时 Toast」与「常驻横幅」两处各写了一份判定与文案，
 * 已经出现用词不一致（有效周期 / 修改周期、是否包含账号安全提示），
 * 这里收敛为唯一实现，两处共用同一份判定结果。
 */

export interface PasswordInfo {
  has_password?: boolean;
  is_expired?: boolean;
  days_until_next_change?: number;
  days_since_last_change?: number;
  password_expire_days?: number;
}

export type PasswordExpiryLevel = 'expired' | 'urgent' | 'warning' | 'notice';

export interface PasswordExpiryNotice {
  level: PasswordExpiryLevel;
  /** 常驻横幅文案 */
  message: string;
  /** 登录即时 Toast 文案（更简短，省去冗余限定语） */
  toastMessage: string;
  isExpired: boolean;
  daysUntil: number;
  daysSince: number;
  expireDays: number;
}

/** 触发提醒的阈值：已过期，或剩余有效天数 <= 7 天（覆盖 7/3/1 天与过期） */
export const PASSWORD_EXPIRY_THRESHOLD_DAYS = 7;

/**
 * 解析密码到期提醒。返回 null 表示无需提醒（没有密码、或剩余天数充足）。
 */
export const resolvePasswordExpiryNotice = (
  pwdInfo?: PasswordInfo | null,
): PasswordExpiryNotice | null => {
  if (!pwdInfo || !pwdInfo.has_password) return null;

  const isExpired =
    !!pwdInfo.is_expired ||
    (typeof pwdInfo.days_until_next_change === 'number' && pwdInfo.days_until_next_change <= 0);
  const daysUntil =
    typeof pwdInfo.days_until_next_change === 'number' ? pwdInfo.days_until_next_change : 999;
  const daysSince = pwdInfo.days_since_last_change ?? 0;
  const expireDays = pwdInfo.password_expire_days ?? 30;

  if (!isExpired && daysUntil > PASSWORD_EXPIRY_THRESHOLD_DAYS) return null;

  let level: PasswordExpiryLevel = 'notice';
  let message = '';
  let toastMessage = '';

  if (isExpired) {
    level = 'expired';
    message = `安全警示：您的登录密码已超过有效周期（已过 ${daysSince} 天），为了账号安全请尽快修改密码！`;
    toastMessage = `安全警示：您的登录密码已超过有效周期（已过 ${daysSince} 天），为了账号安全请尽快修改密码！`;
  } else if (daysUntil <= 1) {
    level = 'urgent';
    message = '安全预警：您的登录密码还有最后 1 天即将过期，请尽快修改密码！';
    toastMessage = '安全预警：您的登录密码还有最后 1 天即将过期，请尽快修改！';
  } else if (daysUntil <= 3) {
    level = 'warning';
    message = `安全提醒：您的登录密码还有 ${daysUntil} 天即将过期，建议及时前往修改。`;
    toastMessage = `安全提醒：您的登录密码还有 ${daysUntil} 天即将过期，建议及时修改。`;
  } else {
    level = 'notice';
    message = `安全提醒：您的登录密码将在 ${daysUntil} 天后到期（有效周期 ${expireDays} 天），建议提前修改。`;
    toastMessage = `安全提醒：您的登录密码将在 ${daysUntil} 天后到期（有效周期 ${expireDays} 天），请提前修改。`;
  }

  return { level, message, toastMessage, isExpired, daysUntil, daysSince, expireDays };
};

/** 提醒等级对应的 Toast 类型 */
export const passwordExpiryToastType = (
  notice: PasswordExpiryNotice,
): 'error' | 'warning' | 'info' => {
  if (notice.isExpired) return 'error';
  return notice.level === 'notice' ? 'info' : 'warning';
};
