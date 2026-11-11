/**
 * 凭据选择器的「过滤 + 命中高亮」纯逻辑。
 *
 * 抽成纯函数而不是写在 `CredentialPicker.vue` 里，是因为它有三个没法靠
 * 截图验的边界：多个关键词要同时命中（AND 语义）、场景过滤要先于搜索生效、
 * 高亮区间重叠时不能把同一段字切碎。这些都由 `tests/frontend/credential_picker_search.mjs`
 * 用 Node 直接跑，组件只负责把结果渲染出来。
 *
 * 搜索范围只含 **名称 / 标识 / 分类**：凭据内容是密钥，既不参与匹配也不出现在
 * 候选里，避免「搜什么就能验证什么」的探测面。
 */

/** 与 `GET /api/portal/secrets/picker` 返回结构一致；不含任何值。 */
export interface CredentialPickerItem {
  id: string
  secret_key: string
  name: string
  secret_type: string
  category: string | null
  status: string
  allowed_scenarios: string[]
  value_masked: string
}

export interface CredentialHighlightSegment {
  text: string
  hit: boolean
}

/**
 * 拆分搜索词：按空白切分并转小写，重复词去掉。
 *
 * 「支付 key」这种输入要同时收窄到两个词都命中的那条，所以调用方拿到的是
 * 数组而不是一个字符串——AND 语义在多词时才有意义。
 */
export function normalizeCredentialQuery(query: string): string[] {
  const tokens = query
    .toLowerCase()
    .split(/\s+/)
    .map((token) => token.trim())
    .filter((token) => token.length > 0)
  return Array.from(new Set(tokens))
}

/** 场景过滤：凭据必须启用了当前使用场景，否则选中也用不了。 */
export function credentialMatchesScene(item: CredentialPickerItem, scene: string): boolean {
  if (!scene) return true
  return (item.allowed_scenarios || []).includes(scene)
}

/** 参与搜索的字段。刻意不含 `value_masked`：那是值的影子，不拿来匹配。 */
export function credentialSearchFields(item: CredentialPickerItem): string[] {
  return [item.name, item.secret_key, item.category || '']
}

/** 每个关键词都要在至少一个字段里命中（AND），关键词内部为子串匹配。 */
export function credentialMatchesQuery(item: CredentialPickerItem, tokens: string[]): boolean {
  if (!tokens.length) return true
  const haystacks = credentialSearchFields(item).map((field) => field.toLowerCase())
  return tokens.every((token) => haystacks.some((field) => field.includes(token)))
}

/**
 * 场景过滤 + 搜索过滤。
 *
 * 顺序固定：先按场景筛掉不可用的，再按关键词筛。反过来的话，用户搜到一条
 * 看起来对、选中却被后端拒的凭据——那不叫搜索，那叫挖坑。
 */
export function filterCredentialItems(
  items: CredentialPickerItem[],
  scene: string,
  query: string,
): CredentialPickerItem[] {
  const tokens = normalizeCredentialQuery(query)
  return items.filter(
    (item) => credentialMatchesScene(item, scene) && credentialMatchesQuery(item, tokens),
  )
}

/**
 * 把文本按命中区间切成片段，供组件用 `<span>` 标出来（不用 v-html）。
 *
 * 多个关键词的命中区间可能重叠（搜「api key」时 "api" 与 "key" 相邻或包含），
 * 这里先排序再合并，保证返回的片段首尾相接、不重不漏。
 */
export function highlightCredentialField(
  text: string,
  tokens: string[],
): CredentialHighlightSegment[] {
  if (!text) return []
  const validTokens = tokens.filter((token) => token.length > 0)
  if (!validTokens.length) return [{ text, hit: false }]

  const lower = text.toLowerCase()
  const ranges: Array<[number, number]> = []
  for (const token of validTokens) {
    let from = lower.indexOf(token)
    while (from >= 0) {
      ranges.push([from, from + token.length])
      from = lower.indexOf(token, from + token.length)
    }
  }
  if (!ranges.length) return [{ text, hit: false }]

  ranges.sort((a, b) => a[0] - b[0])
  const merged: Array<[number, number]> = []
  for (const range of ranges) {
    const last = merged[merged.length - 1]
    if (last && range[0] <= last[1]) {
      last[1] = Math.max(last[1], range[1])
    } else {
      merged.push([range[0], range[1]])
    }
  }

  const segments: CredentialHighlightSegment[] = []
  let cursor = 0
  for (const [start, end] of merged) {
    if (start > cursor) segments.push({ text: text.slice(cursor, start), hit: false })
    segments.push({ text: text.slice(start, end), hit: true })
    cursor = end
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), hit: false })
  return segments
}
