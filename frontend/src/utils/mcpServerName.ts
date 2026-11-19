export type McpServerScope = 'global' | 'personal'

/**
 * 名字里的归属段：用户 id 的 base36（与后端 `owner_name_segment()` 逐字符一致）。
 *
 * 为什么不用用户名：截断用户名会造成**误认**——`chenxiaolong` 与 `chenxiao` 都会变成
 * `chenxiao`，别人看到 `priv-chenxiao-hcp` 会以为服务是另一个人的；不截断又太长
 * （`priv-chenxiaolong-nanzi-platform`）。
 * 为什么不用随机串：同一用户新建的每个服务、以及接受分享时的改名，都必须算出同一个前缀，
 * 随机串做不到（每个服务前缀都不同，等于没有归属信息）。
 *
 * 拿不到 user_id 时退化成一个固定段，撞名交给后端全局唯一约束兜底。
 */
export const FALLBACK_OWNER_SEGMENT = 'user'

const ownerNameSegment = (userId: unknown) => {
  const numeric = Number(userId)
  if (!Number.isFinite(numeric) || numeric <= 0) return FALLBACK_OWNER_SEGMENT
  return Math.trunc(numeric).toString(36)
}

const normalizeNamePart = (value: unknown, fallback: string) => {
  const normalized = String(value || '')
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')

  return normalized || fallback
}

/**
 * 固定前缀，含尾部 `-`，例如 `priv-13zk-hcp` 的 `priv-13zk-`。
 *
 * 2026-11 起把 `mcp-private-<完整用户名>-` / `mcp-public-<完整用户名>-` 换成
 * `priv-<base36(user_id)>-` / `pub-<base36(user_id)>-`：典型名字从 29–37 字符降到 13–21，
 * 且归属段不可被误认成别人。
 */
export const buildMcpServerNamePrefix = (
  scope: McpServerScope,
  userId: unknown,
) => {
  const ownerPart = ownerNameSegment(userId)
  if (scope === 'personal') {
    return `priv-${ownerPart}-`
  }
  return `pub-${ownerPart}-`
}

/**
 * 旧前缀（`mcp-private-<完整用户名>-` / `mcp-public-<用户名>-`）。
 *
 * 只用于**剥离存量名字**，不再参与新名字组装：库里已有的服务不迁移，但编辑它们时
 * 必须仍能剥出业务后缀，否则弹窗会把整串旧名当后缀显示，看起来像系统要改名。
 */
const legacyPrefixes = (
  scope: McpServerScope,
  username: unknown,
): string[] => {
  const raw = String(username || '').trim()
  const sanitized = raw.replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
  const parts = [raw, sanitized].filter((part, index, arr) => part && arr.indexOf(part) === index)
  const prefixes: string[] = []
  for (const part of parts) {
    prefixes.push(scope === 'personal' ? `mcp-private-${part}-` : `mcp-public-${part}-`)
  }
  return prefixes
}

export const normalizeMcpServerNameSuffix = (value: unknown) =>
  normalizeNamePart(value, '')

/** 服务名允许的字符集，与后端 `app/services/mcp/naming.py` 的 `SERVER_NAME_ALLOWED_PATTERN` 一致 */
export const MCP_SERVER_NAME_PATTERN = /^[A-Za-z0-9_-]+$/

/**
 * 校验服务名后缀，返回空串表示合法，否则返回给用户看的原因。
 *
 * 为什么不用 `normalizeMcpServerNameSuffix` 就地归一化：输入「工具」时归一化会得到空串，
 * 用户只看到保存按钮点不动，或者名字悄悄少了一半，却不知道为什么。这里直接把原因讲清楚。
 */
export const describeMcpServerNameSuffixError = (value: unknown): string => {
  const text = String(value ?? '').trim()
  if (!text) return '请填写服务名称后缀'
  if (!MCP_SERVER_NAME_PATTERN.test(text)) {
    return '后缀只能用英文字母、数字、连字符（-）和下划线（_），不支持中文、空格与全角字符'
  }
  return ''
}

export const composeMcpServerName = (
  scope: McpServerScope,
  userId: unknown,
  suffix: unknown,
) => {
  const prefix = buildMcpServerNamePrefix(scope, userId)
  const normalizedSuffix = normalizeMcpServerNameSuffix(suffix)
  if (!normalizedSuffix) return ''
  return `${prefix}${normalizedSuffix}`
}

/** 编辑时从完整名剥离当前用户前缀；无法匹配则返回原文以便用户改后缀 */
export const stripMcpServerNamePrefix = (
  fullName: unknown,
  scope: McpServerScope,
  userId: unknown,
  username?: unknown,
) => {
  const name = String(fullName || '').trim()
  if (!name) return ''
  const lowerName = name.toLowerCase()

  // ① 当前前缀（priv-<base36>- / pub-<base36>-）
  const prefix = buildMcpServerNamePrefix(scope, userId)
  if (lowerName.startsWith(prefix.toLowerCase())) {
    return name.slice(prefix.length)
  }
  // ② 存量名字的旧前缀（完整用户名）。存量不迁移，但不能因此改名。
  for (const legacy of legacyPrefixes(scope, username)) {
    if (lowerName.startsWith(legacy.toLowerCase())) {
      return name.slice(legacy.length)
    }
  }
  // ③ 兜底：任意归属段的新前缀（分享来的副本可能带分享人的码），无用户名的历史
  //    mcp-public-{suffix}，以及旧格式任意用户名前缀。
  if (scope === 'global' && /^mcp-(?:public|pub)-/i.test(name)) {
    return name.replace(/^mcp-(?:public|pub)-/i, '')
  }
  if (scope === 'personal' && /^mcp-private-[^-]+-/i.test(name)) {
    return name.replace(/^mcp-private-[^-]+-/i, '')
  }
  if (/^(?:priv|pub)-[^-]+-/i.test(name)) {
    return name.replace(/^(?:priv|pub)-[^-]+-/i, '')
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
  userId: unknown,
  suffix: unknown,
  username?: unknown,
) => {
  const original = String(originalName || '').trim()
  const suffixText = String(suffix ?? '').trim()
  if (original && suffixText === stripMcpServerNamePrefix(original, scope, userId, username)) {
    return original
  }
  return composeMcpServerName(scope, userId, suffix)
}

/**
 * 按服务地址推导一个不冲突的名字后缀（hostname 归一化；重名时加 -2/-3）。
 *
 * 向导第一步没有后缀输入框，探测/保存前用它补齐默认值。
 *
 * 注意 `existingServerNames` 通常只含**当前用户可见**的服务（个人页按 user_id 过滤），
 * 所以这里算出来的名字仍可能被别人的服务占用——那种情况由后端全局唯一约束拒绝，
 * 用户改个后缀即可。
 */
export const suggestUniqueServerNameSuffix = (
  sseUrl: unknown,
  scope: McpServerScope,
  userId: unknown,
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
  while (taken.has(composeMcpServerName(scope, userId, suffix).toLowerCase())) {
    counter += 1
    suffix = `${base}-${counter}`
  }
  return suffix
}

/** @deprecated 优先用 composeMcpServerName；保留给旧调用方 */
export const buildDefaultMcpServerName = (
  scope: McpServerScope,
  userId: unknown,
  mcpName: unknown,
) => composeMcpServerName(scope, userId, mcpName) || buildMcpServerNamePrefix(scope, userId) + 'server'
