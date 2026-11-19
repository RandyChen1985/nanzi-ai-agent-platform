/**
 * 工具分组的**唯一实现**：智能体编辑页「工具能力」与权限页「功能限定」必须显示同一套分组。
 *
 * 两处各写一份必然漂移——智能体页把工具归到「系统自治工具」，权限弹框却把它们堆在一个
 * 「内置工具」里，管理员在两个页面看到的结构对不上，也就无法判断"我到底限定了哪些"。
 *
 * 分组规则（与智能体页改造前完全一致，顺序即展示顺序）：
 * 1. 显式业务分组优先：注册表页给 API 工具填的 `group_name`；
 * 2. 否则按工具名关键字猜能力域；
 * 3. 都落不上就进兜底组「其他扩展工具」，永远排在最后。
 */

export interface ToolCapabilityGroupDef {
  key: string
  label: string
  icon: string
}

export interface ToolDisplayGroup<T> {
  key: string
  label: string
  icon: string
  kind: 'capability' | 'business' | 'fallback'
  items: T[]
}

/** 兜底组：既没有业务分组、关键字也认不出来。 */
export const TOOL_GROUP_FALLBACK_KEY = 'tools:__other__'
export const TOOL_GROUP_FALLBACK_LABEL = '其他扩展工具'
/** 业务分组统一图标：这些是使用者在注册表里自建的域，没有语义可猜。 */
export const TOOL_GROUP_BUSINESS_ICON = '🔌'

/** 内置工具的能力域（顺序即展示顺序）。 */
export const TOOL_CAPABILITY_GROUPS: readonly ToolCapabilityGroupDef[] = [
  { key: 'chatbi', label: 'ChatBI 数据分析', icon: '📊' },
  { key: 'knowledge', label: '知识库检索 (RAG)', icon: '📖' },
  { key: 'web', label: '联网搜索', icon: '🌐' },
  { key: 'browser', label: '浏览器自动化', icon: '🧭' },
  { key: 'system', label: '系统自治工具', icon: '💻' },
  { key: 'office', label: '办公协作', icon: '💼' },
  { key: 'notification', label: '消息通知', icon: '💬' },
  { key: 'memory', label: '长期事实与记忆引擎', icon: '🧠' },
  { key: 'delegation', label: '多智能体协同与调度', icon: '🤖' },
]

function includesAny(name: string, needles: readonly string[]): boolean {
  return needles.some((needle) => name.includes(needle))
}

function startsWithAny(name: string, prefixes: readonly string[]): boolean {
  return prefixes.some((prefix) => name.startsWith(prefix))
}

/**
 * 按工具名猜能力域。
 *
 * 判定顺序有依赖：`browser_` 与 `sub_agent` 必须排在最前（否则会被后面的 `search_`
 * 之类前缀截胡），而 `system` 一组的匹配面最宽，必须排在 `notification` / `office`
 * 之前——这些顺序都是改造前就成立的，改动会直接改变用户看到的分组。
 */
export function capabilityKeyOf(toolName: unknown): string {
  const name = String(toolName ?? '').trim().toLowerCase()
  if (!name) return 'other'

  if (name.startsWith('browser_') || name.includes('browser')) return 'browser'
  if (name.includes('sub_agent')) return 'delegation'
  if (includesAny(name, ['sql', 'dataset', 'bi_', 'olap'])) return 'chatbi'
  if (startsWithAny(name, ['excel_document', 'word_document'])) return 'office'
  if (
    includesAny(name, ['knowledge', '_rag', 'rag_', 'ragflow', 'kb_', 'document'])
    || name === 'rag'
  ) {
    return 'knowledge'
  }
  if (
    includesAny(name, ['web_search', 'static_web', 'http_request', 'web_renderer'])
    || name === 'fetch_static_web_url'
  ) {
    return 'web'
  }
  if (
    includesAny(name, [
      'local_file',
      // read_image / generate_image 都归这里：一对多模态能力，和管理员的认知一致。
      // 不加这条的话 generate_image 认不出前缀，会掉进「其他扩展工具」兜底组。
      'image',
      'system_command',
      'system_process',
      'search_text',
      'edit_file',
      'glob_files',
      'exec_command',
      'list_process',
      'manage_process',
      'scratchpad',
      'navigator',
      'linter',
    ])
    || startsWithAny(name, ['search_', 'read_', 'write_', 'exec_', 'list_', 'execute_', 'manage_'])
  ) {
    return 'system'
  }
  if (includesAny(name, ['dingtalk', 'email', 'wechat', 'message', 'mail'])) return 'notification'
  if (includesAny(name, ['jira', 'feishu'])) return 'office'
  if (includesAny(name, ['preference', 'memory', 'fact'])) return 'memory'
  return 'other'
}

/** 把一组工具按「业务分组 → 能力域 → 兜底」聚合成展示分组。 */
export function groupToolsForDisplay<T>(
  items: readonly T[] | null | undefined,
  getId: (item: T) => string,
  getGroupName?: (item: T) => string | null | undefined,
): ToolDisplayGroup<T>[] {
  const capabilityBuckets = new Map<string, T[]>(
    TOOL_CAPABILITY_GROUPS.map((group) => [group.key, []]),
  )
  const businessBuckets = new Map<string, T[]>()
  const fallback: T[] = []

  ;(items || []).forEach((item) => {
    const business = String(getGroupName?.(item) ?? '').trim()
    if (business) {
      const bucket = businessBuckets.get(business)
      if (bucket) bucket.push(item)
      else businessBuckets.set(business, [item])
      return
    }
    const key = capabilityKeyOf(getId(item))
    const bucket = capabilityBuckets.get(key)
    if (bucket) bucket.push(item)
    else fallback.push(item)
  })

  const capabilityGroups: ToolDisplayGroup<T>[] = TOOL_CAPABILITY_GROUPS.map((group) => ({
    key: `tools:capability:${group.key}`,
    label: group.label,
    icon: group.icon,
    kind: 'capability' as const,
    items: capabilityBuckets.get(group.key) || [],
  })).filter((group) => group.items.length > 0)

  // 业务分组按工具数降序（工具多的业务域一眼可见），同数量按名称稳定排序
  const sortedBusinessGroups: ToolDisplayGroup<T>[] = Array.from(businessBuckets.entries())
    .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], 'zh-Hans-CN'))
    .map(([label, groupItems]) => ({
      key: `tools:business:${label}`,
      label,
      icon: TOOL_GROUP_BUSINESS_ICON,
      kind: 'business' as const,
      items: groupItems,
    }))

  const fallbackGroups: ToolDisplayGroup<T>[] =
    fallback.length > 0
      ? [
          {
            key: TOOL_GROUP_FALLBACK_KEY,
            label: TOOL_GROUP_FALLBACK_LABEL,
            icon: '🔧',
            kind: 'fallback' as const,
            items: fallback,
          },
        ]
      : []

  // 展示顺序：能力域（固定顺序）→ 业务分组 → 兜底组殿后
  return [...capabilityGroups, ...sortedBusinessGroups, ...fallbackGroups]
}
