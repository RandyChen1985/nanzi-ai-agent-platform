export type RedisKeyListItem = {
  name: string
  type: string
}

export type RedisKeyCategory = {
  id: string
  label: string
  description: string
  match: (key: string) => boolean
}

/** 业务前缀分类，用于 key 旁展示标签 */
export const REDIS_KEY_CATEGORIES: RedisKeyCategory[] = [
  {
    id: 'conversation',
    label: '对话',
    description: '多轮对话上下文与查数结果',
    match: (key) => key.startsWith('conversation:'),
  },
  {
    id: 'auth',
    label: '认证',
    description: 'API Key 与登录态缓存',
    match: (key) => key.startsWith('auth:'),
  },
  {
    id: 'permissions',
    label: '权限',
    description: '用户权限集缓存',
    match: (key) => key.startsWith('sys:auth:permissions:'),
  },
  {
    id: 'sys_config',
    label: '系统配置',
    description: '系统参数缓存',
    match: (key) => key.startsWith('sys_config:'),
  },
  {
    id: 'dataset_menu',
    label: '数据集菜单',
    description: 'Agent 数据集 Schema 菜单',
    match: (key) => key.startsWith('agent:dataset_menu'),
  },
  {
    id: 'dataset_navigation',
    label: '数据门户',
    description: '数据门户导航 Markdown 缓存、点击统计与换一批去重（按 user_id 单 Key）',
    match: (key) => key.startsWith('agent:dataset_navigation'),
  },
  {
    id: 'memory',
    label: '记忆',
    description: '会话摘要、长期记忆与防抖状态',
    match: (key) =>
      key.startsWith('memory:') ||
      key.startsWith('nanzi:agent:ltm:') ||
      key.startsWith('memory_config:'),
  },
  {
    id: 'vector',
    label: '向量索引',
    description: '元数据/案例/记忆向量文档',
    match: (key) =>
      key.startsWith('nanzi:idx:') ||
      key.startsWith('idx:') ||
      key.startsWith('metadata:dataset:') ||
      key.startsWith('nanzi:example:'),
  },
  {
    id: 'sql_cache',
    label: 'SQL 缓存',
    description: '查数结果临时缓存',
    match: (key) => key.startsWith('sql_result:'),
  },
  {
    id: 'session_lock',
    label: '会话锁',
    description: '并发会话与运行锁',
    match: (key) =>
      key.startsWith('nanzi:conv_run:') ||
      key.startsWith('lock:') ||
      key.includes(':session_lock:'),
  },
  {
    id: 'portal_prefs',
    label: '门户偏好',
    description: '用户数据门户偏好设置',
    match: (key) => key.startsWith('portal:prefs:'),
  },
  {
    id: 'workspace_recent',
    label: '工作空间最近文件',
    description: '浏览工作空间时按用户隔离的最近访问文件记录',
    match: (key) => key.startsWith('agent:workspace_recent_files:'),
  },
  {
    id: 'workspace_browser_prefs',
    label: '工作空间浏览偏好',
    description: '工作空间文件浏览器的筛选与搜索偏好（按用户隔离）',
    match: (key) => key.startsWith('agent:workspace_browser_prefs:'),
  },
  {
    id: 'rate_limit',
    label: '限流',
    description: '接口限流计数',
    match: (key) => key.startsWith('rate_limit:'),
  },
]

export const REDIS_TYPE_LABELS: Record<string, string> = {
  string: 'String',
  hash: 'Hash',
  list: 'List',
  set: 'Set',
  zset: 'Sorted Set',
  stream: 'Stream',
  none: 'Unknown',
}

export function getRedisKeyCategoryLabel(key: string): string {
  const category = REDIS_KEY_CATEGORIES.find((item) => item.match(key))
  return category?.label ?? '其他'
}

export type RedisKeyGroupMode = 'redis_type' | 'business'

export type RedisKeyGroup = {
  id: string
  label: string
  description: string
  keys: RedisKeyListItem[]
}

export function groupRedisKeys(
  keys: RedisKeyListItem[],
  mode: RedisKeyGroupMode,
): RedisKeyGroup[] {
  const buckets = new Map<string, RedisKeyGroup>()

  for (const item of keys) {
    let groupId = 'other'
    let label = '其他'
    let description = '未匹配到已知业务前缀的键'

    if (mode === 'redis_type') {
      groupId = item.type || 'none'
      label = REDIS_TYPE_LABELS[groupId] || groupId.toUpperCase()
      description = `Redis 数据类型：${label}`
    } else {
      const category = REDIS_KEY_CATEGORIES.find((entry) => entry.match(item.name))
      if (category) {
        groupId = category.id
        label = category.label
        description = category.description
      }
    }

    if (!buckets.has(groupId)) {
      buckets.set(groupId, { id: groupId, label, description, keys: [] })
    }
    buckets.get(groupId)!.keys.push(item)
  }

  const groups = Array.from(buckets.values())
  groups.sort((a, b) => {
    if (mode === 'redis_type') {
      const order = ['string', 'hash', 'list', 'set', 'zset', 'stream', 'none', 'other']
      return order.indexOf(a.id) - order.indexOf(b.id)
    }
    return b.keys.length - a.keys.length || a.label.localeCompare(b.label, 'zh-CN')
  })

  for (const group of groups) {
    group.keys.sort((a, b) => a.name.localeCompare(b.name))
  }

  return groups
}
