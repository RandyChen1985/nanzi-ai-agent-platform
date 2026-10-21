export type McpServerScope = 'global' | 'personal'

const normalizeNamePart = (value: unknown, fallback: string) => {
  const normalized = String(value || '')
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')

  return normalized || fallback
}

/** 固定前缀，含尾部 `-`，例如 mcp-private-admin- / mcp-public-admin- */
export const buildMcpServerNamePrefix = (
  scope: McpServerScope,
  username: unknown,
) => {
  const userPart = normalizeNamePart(username, 'user')
  if (scope === 'personal') {
    return `mcp-private-${userPart}-`
  }
  return `mcp-public-${userPart}-`
}

export const normalizeMcpServerNameSuffix = (value: unknown) =>
  normalizeNamePart(value, '')

export const composeMcpServerName = (
  scope: McpServerScope,
  username: unknown,
  suffix: unknown,
) => {
  const prefix = buildMcpServerNamePrefix(scope, username)
  const normalizedSuffix = normalizeMcpServerNameSuffix(suffix)
  if (!normalizedSuffix) return ''
  return `${prefix}${normalizedSuffix}`
}

/** 编辑时从完整名剥离当前用户前缀；无法匹配则返回原文以便用户改后缀 */
export const stripMcpServerNamePrefix = (
  fullName: unknown,
  scope: McpServerScope,
  username: unknown,
) => {
  const name = String(fullName || '').trim()
  if (!name) return ''
  const prefix = buildMcpServerNamePrefix(scope, username)
  if (name.toLowerCase().startsWith(prefix.toLowerCase())) {
    return name.slice(prefix.length)
  }
  // 兼容历史：mcp-public-{suffix}（无用户名）
  if (scope === 'global' && /^mcp-public-/i.test(name)) {
    return name.replace(/^mcp-public-/i, '')
  }
  if (scope === 'personal' && /^mcp-private-[^-]+-/i.test(name)) {
    return name.replace(/^mcp-private-[^-]+-/i, '')
  }
  return name
}

/**
 * 编辑态回填完整名：用户没改后缀时保持库里的原名。
 *
 * 编辑弹窗只暴露"后缀"输入框，回填时会把完整名拆成前缀 + 后缀。若库里的名字本来
 * 就不带当前 scope 前缀（平台内置 Echo、手工命名、历史无用户名前缀），拆出来的
 * 后缀等于整个名字；此时重新拼前缀会把服务改名，后端还会连带迁移工具标识与所有
 * 智能体版本引用。这里用"后缀是否仍等于回填值"判断用户有没有动过名字。
 */
export const resolveEditedServerName = (
  originalName: unknown,
  scope: McpServerScope,
  username: unknown,
  suffix: unknown,
) => {
  const original = String(originalName || '').trim()
  const suffixText = String(suffix ?? '').trim()
  if (original && suffixText === stripMcpServerNamePrefix(original, scope, username)) {
    return original
  }
  return composeMcpServerName(scope, username, suffix)
}

/**
 * 按服务地址推导一个不冲突的名字后缀（hostname 归一化；重名时加 -2/-3）。
 *
 * 向导第一步没有后缀输入框，探测/保存前用它补齐默认值。
 */
export const suggestUniqueServerNameSuffix = (
  sseUrl: unknown,
  scope: McpServerScope,
  username: unknown,
  existingServerNames: readonly unknown[] = [],
) => {
  const taken = new Set(
    (existingServerNames || [])
      .map((name) => String(name || '').trim().toLowerCase())
      .filter(Boolean),
  )
  let base = 'server'
  try {
    const url = new URL(String(sseUrl || '').trim())
    base = normalizeMcpServerNameSuffix(url.hostname) || 'server'
  } catch {
    base = 'server'
  }

  let suffix = base
  let counter = 1
  while (taken.has(composeMcpServerName(scope, username, suffix).toLowerCase())) {
    counter += 1
    suffix = `${base}-${counter}`
  }
  return suffix
}

/** @deprecated 优先用 composeMcpServerName；保留给旧调用方 */
export const buildDefaultMcpServerName = (
  scope: McpServerScope,
  username: unknown,
  mcpName: unknown,
) => composeMcpServerName(scope, username, mcpName) || buildMcpServerNamePrefix(scope, username) + 'server'
