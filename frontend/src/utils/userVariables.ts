/**
 * 用户变量占位符的唯一来源。
 *
 * 元数据行级权限与 API 工具参数都支持 ``{user.xxx}``，两处必须共用同一套
 * 字段名与 extra_data 展平规则；否则界面上能选的变量，运行时可能取不到。
 *
 * 这里只做「展示候选」的收集：真正的取值在后端
 * ``app/services/ai/tools/user_placeholders.py``，敏感键不会进入变量表。
 */

export type UserVariable = {
  label: string
  value: string
  sample?: string
}

export const BUILTIN_USER_VARIABLES: UserVariable[] = [
  { label: '用户 ID', value: '{user.id}' },
  { label: '用户名', value: '{user.user_name}' },
  { label: '真实姓名', value: '{user.real_name}' },
  { label: '部门代码', value: '{user.dept_code}' },
  { label: '组织全路径', value: '{user.org_path}' },
  { label: '角色', value: '{user.role}' },
]

/** 与后端 ``user_extra_data.REDACTED_KEY_PARTS`` 对齐：这些键不能作为变量。 */
const SENSITIVE_KEY_PARTS = [
  'password',
  'passwd',
  'secret',
  'token',
  'api_key',
  'apikey',
  'authorization',
  'credential',
  'private_key',
]

export const isSensitiveUserVariableKey = (key: string): boolean => {
  const normalized = key.toLowerCase().replace(/-/g, '_')
  return SENSITIVE_KEY_PARTS.some((part) => normalized.includes(part))
}

/** 这些键属于用户自身维度，不再作为 extra_data「扩展字段」重复展示。 */
export const CORE_USER_DIMENSION_KEYS = new Set([
  'id',
  'user_id',
  'user_name',
  'real_name',
  'dept_code',
  'org_path',
  'role',
  'extra_data',
])

export const parseUserExtraDataObject = (raw: unknown): Record<string, unknown> => {
  if (!raw) return {}
  if (typeof raw === 'string') {
    const text = raw.trim()
    if (!text) return {}
    try {
      const parsed = JSON.parse(text)
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? parsed
        : {}
    } catch {
      return {}
    }
  }
  if (typeof raw === 'object' && !Array.isArray(raw)) {
    return raw as Record<string, unknown>
  }
  return {}
}

export const formatExtraDataSample = (value: unknown): string | undefined => {
  if (value == null) return undefined
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value).trim()
  if (!text) return undefined
  return text.length > 40 ? `${text.slice(0, 37)}...` : text
}

/**
 * 汇总所有用户（含当前登录用户 localStorage 兜底）的 extra_data 键。
 *
 * ``excludeSensitive`` 用于工具参数：运行时不会把敏感键暴露成 ``{user.xxx}``，
 * 菜单里也就不该列出来，避免用户选中后调用时才报错。
 */
export const collectExtraDataVariables = (
  users: Array<{ extra_data?: unknown }>,
  options: { excludeSensitive?: boolean } = {},
): UserVariable[] => {
  const keySamples = new Map<string, string | undefined>()

  const collectFromExtraData = (extraData: unknown) => {
    const extra = parseUserExtraDataObject(extraData)
    for (const [key, value] of Object.entries(extra)) {
      if (CORE_USER_DIMENSION_KEYS.has(key)) continue
      if (options.excludeSensitive && isSensitiveUserVariableKey(key)) continue
      if (!keySamples.has(key)) {
        keySamples.set(key, formatExtraDataSample(value))
      }
    }
  }

  for (const user of users) {
    collectFromExtraData(user.extra_data)
  }

  // 兜底：当前登录用户 localStorage 中的 extra_data
  try {
    const userInfoStr = localStorage.getItem('user_info')
    if (userInfoStr) {
      collectFromExtraData(JSON.parse(userInfoStr)?.extra_data)
    }
  } catch {
    // ignore
  }

  return Array.from(keySamples.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, sample]) => ({
      label: key,
      value: `{user.${key}}`,
      sample,
    }))
}
