/**
 * 知识库虚拟文件夹的层级解析。
 *
 * 后端 `local_metadata.extra_config.folder_structure` 一直是
 * `{ 文件夹名: [文档ID] }` 的扁平结构，只能表达一层。这里沿用同一份数据，
 * 但允许 key 用 "/" 表示层级（`制度文件/2025 年度`）：
 *
 * - 老数据不带 "/"，解析出来就是根级文件夹，行为与改造前完全一致；
 * - 新数据带 "/"，前端自动补出中间层级，不需要后端改表、也不需要迁移 SQL。
 *
 * 真正的多级目录（folders[] + assignments）留给后续版本，这里的解析函数
 * 是纯函数，届时只换数据来源、调用方不用动。
 */

export const FOLDER_PATH_SEPARATOR = '/'

/** 文件夹节点：path 是唯一键，name 只是末段名（不同层可以有同名文件夹） */
export interface FolderNode {
  /** 完整路径，例如 `制度文件/2025 年度` */
  path: string
  /** 末段名，例如 `2025 年度` */
  name: string
  /** 层级，根级为 0 */
  depth: number
  /** 父级路径，根级为空串 */
  parentPath: string
  /** 子文件夹路径（已按名称排序） */
  children: string[]
  /** 直接归在本文件夹下的文档 ID */
  docIds: string[]
  /** 含所有子孙文件夹的文档数 */
  totalDocCount: number
}

export interface FolderIndex {
  byPath: Map<string, FolderNode>
  /** 根级文件夹路径（已按名称排序） */
  topLevel: string[]
  /** 出现在任何文件夹里的文档 ID（用于算「未分类」） */
  assigned: Set<string>
}

/** 把 `a / b ` 这类手写路径归一成 `a/b`，空段直接丢掉 */
export function splitFolderPath(path: string): string[] {
  return String(path ?? '')
    .split(FOLDER_PATH_SEPARATOR)
    .map((segment) => segment.trim())
    .filter(Boolean)
}

export function joinFolderPath(segments: string[]): string {
  return segments.filter(Boolean).join(FOLDER_PATH_SEPARATOR)
}

/** 父级路径；根级返回空串 */
export function parentFolderPath(path: string): string {
  const segments = splitFolderPath(path)
  segments.pop()
  return joinFolderPath(segments)
}

/** 末段名；空路径返回空串 */
export function folderNameOf(path: string): string {
  const segments = splitFolderPath(path)
  return segments[segments.length - 1] ?? ''
}

/**
 * 把扁平的 `folder_structure` 展开成带层级索引的节点表。
 *
 * 关键约定：
 * - key 里出现 `A/B` 时，即使没有 `A` 这个 key，也要补出 `A` 节点（否则界面上
 *   二层文件夹会挂在一个不存在的一级节点上，直接看不到）；
 * - 同名不同层互不影响（`制度文件/年度` 与 `模板中心/年度` 是两个节点）；
 * - 只按结构建节点，文档是否存在由调用方决定（`docIds` 里可能残留已删除的 ID）。
 */
export function buildFolderIndex(
  structure: Record<string, string[]> | undefined | null,
): FolderIndex {
  const byPath = new Map<string, FolderNode>()

  const ensureNode = (path: string): FolderNode | null => {
    const segments = splitFolderPath(path)
    if (!segments.length) return null
    const normalized = joinFolderPath(segments)
    const existing = byPath.get(normalized)
    if (existing) return existing

    // 逐段补出祖先节点，保证任何一层都能被单独渲染
    let cursor = ''
    segments.forEach((segment, depth) => {
      cursor = cursor ? `${cursor}${FOLDER_PATH_SEPARATOR}${segment}` : segment
      if (!byPath.has(cursor)) {
        byPath.set(cursor, {
          path: cursor,
          name: segment,
          depth,
          parentPath: parentFolderPath(cursor),
          children: [],
          docIds: [],
          totalDocCount: 0,
        })
      }
    })
    return byPath.get(normalized) ?? null
  }

  for (const [rawPath, ids] of Object.entries(structure ?? {})) {
    const node = ensureNode(rawPath)
    if (!node) continue
    const docIds = Array.isArray(ids) ? ids.filter((id) => typeof id === 'string' && id) : []
    // 同一路径重复出现时合并而不是覆盖（后端 JSON 理论上不会，但手工改配置会）
    for (const id of docIds) {
      if (!node.docIds.includes(id)) node.docIds.push(id)
    }
  }

  // 挂父子关系
  for (const node of byPath.values()) {
    if (!node.parentPath) continue
    const parent = byPath.get(node.parentPath)
    if (parent && !parent.children.includes(node.path)) parent.children.push(node.path)
  }

  // 排序：同一层按名称排，保证每次渲染顺序稳定
  for (const node of byPath.values()) {
    node.children.sort((a, b) => folderNameOf(a).localeCompare(folderNameOf(b), 'zh-Hans-CN'))
  }

  // 自底向上累计文档数：先按深度倒序，保证子节点先算完
  const ordered = [...byPath.values()].sort((a, b) => b.depth - a.depth)
  for (const node of ordered) {
    node.totalDocCount =
      node.docIds.length +
      node.children.reduce((sum, childPath) => sum + (byPath.get(childPath)?.totalDocCount ?? 0), 0)
  }

  const topLevel = [...byPath.values()]
    .filter((node) => !node.parentPath)
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
    .map((node) => node.path)

  const assigned = new Set<string>()
  for (const node of byPath.values()) {
    for (const id of node.docIds) assigned.add(id)
  }

  return { byPath, topLevel, assigned }
}

/**
 * 判断把 `sourcePath` 移动到 `targetPath` 是否合法：
 * 目标就是自己、或者目标在自己的子树里，都会造成环。
 */
export function canMoveFolderInto(sourcePath: string, targetPath: string): boolean {
  const source = joinFolderPath(splitFolderPath(sourcePath))
  const target = joinFolderPath(splitFolderPath(targetPath))
  if (!source || !target) return true
  if (source === target) return false
  return !target.startsWith(`${source}${FOLDER_PATH_SEPARATOR}`)
}
