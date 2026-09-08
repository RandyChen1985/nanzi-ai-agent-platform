/**
 * 等保二级/三级密码复杂度规则工具
 * 规范要求：
 * 1. 长度为 8 到 32 个字符
 * 2. 至少包含大写字母、小写字母、数字、特殊符号中的 3 种
 * 3. 不能包含空格等不可见空白字符
 * 4. 不能包含用户名（不区分大小写）
 */

export const PASSWORD_POLICY_DESC = '密码长度须为 8-32 位，且必须包含大写字母、小写字母、数字、特殊符号中的至少 3 种';

export interface PasswordPolicyCheckResult {
  valid: boolean;
  message: string;
  shortMessage: string;
  hasLength: boolean;
  hasUpper: boolean;
  hasLower: boolean;
  hasDigit: boolean;
  hasSpecial: boolean;
  noWhitespace: boolean;
  notContainUsername: boolean;
  categoryCount: number;
}

export function checkPasswordPolicy(password: string, username?: string): PasswordPolicyCheckResult {
  const pwd = password || '';
  const hasLength = pwd.length >= 8 && pwd.length <= 32;
  const noWhitespace = pwd.length > 0 && !/\s/.test(pwd);

  const hasUpper = /[A-Z]/.test(pwd);
  const hasLower = /[a-z]/.test(pwd);
  const hasDigit = /[0-9]/.test(pwd);
  const hasSpecial = /[^A-Za-z0-9]/.test(pwd);

  const categoryCount = [hasUpper, hasLower, hasDigit, hasSpecial].filter(Boolean).length;

  let notContainUsername = true;
  if (username && username.trim().length >= 3) {
    const cleanUser = username.trim().toLowerCase();
    if (pwd.toLowerCase().includes(cleanUser)) {
      notContainUsername = false;
    }
  }

  let valid = false;
  let message = '';
  let shortMessage = '';

  if (!pwd) {
    message = '请输入密码';
    shortMessage = '请输入密码';
  } else if (!hasLength) {
    message = '密码长度必须为 8 到 32 个字符';
    shortMessage = '长度须为 8-32 位';
  } else if (!noWhitespace) {
    message = '密码不能包含空格或空白字符';
    shortMessage = '不能包含空格';
  } else if (categoryCount < 3) {
    message = '密码须至少包含大写字母、小写字母、数字、特殊符号中的 3 种';
    shortMessage = `需至少 3 种类别 (当前 ${categoryCount}/4)`;
  } else if (!notContainUsername) {
    message = '密码不能包含用户名';
    shortMessage = '不能包含用户名';
  } else {
    valid = true;
    message = '密码符合等保复杂度要求';
    shortMessage = '符合等保要求';
  }

  return {
    valid,
    message,
    shortMessage,
    hasLength,
    hasUpper,
    hasLower,
    hasDigit,
    hasSpecial,
    noWhitespace,
    notContainUsername,
    categoryCount,
  };
}
