/**
 * 工作区路径的纯计算工具（不发请求、不碰 DOM）。
 *
 * 单独成文件而不是塞进 `workspaceFilePreview.ts`：那边 import 了 axios，
 * 任何要验证路径逻辑的 Node 探针都会被 `require('axios')` 挡住，只能退化成
 * 「读源码做字符串断言」——路径算错的代价是跳到不存在的目录，值得真跑一遍。
 */

/**
 * 取路径的父目录。
 *
 * 使用场景：`copy-to-workspace` 不指定 `target_dir` 时副本落在**本人工作区根**，
 * 而触发「复制并分享」的场景恰恰是用户正在浏览公共目录——刷新当前目录永远看不到
 * 副本，必须跳到副本所在目录。管理页与浏览器抽屉两处都要算这个父目录，
 * 各写一份 `slice` 迟早算出不一样的结果。
 *
 * 边界：返回空串表示已到顶层（工作区虚拟根）。
 *   `/a.md`       -> `''`
 *   `/dir/a.md`   -> `/dir`
 *   `/dir/a.md/`  -> `/dir`   （容忍尾斜杠）
 *   `a.md`        -> `''`     （相对路径没有父目录信息）
 *   `''`          -> `''`
 */
export function parentDirectoryOf(path: string): string {
  const trimmed = String(path || '').replace(/\\/g, '/').replace(/\/+$/, '')
  const idx = trimmed.lastIndexOf('/')
  return idx <= 0 ? '' : trimmed.slice(0, idx)
}

/** 路径归一化：统一正斜杠，去尾斜杠（根 `/` 保留）。 */
export function normalizeWorkspacePath(path: string): string {
  const unified = String(path || '').replace(/\\/g, '/')
  if (unified === '/') return '/'
  return unified.replace(/\/+$/, '')
}

/** 判断 path 是否等于或位于 dir 目录下（严格按 `/` 边界，避免 u1 误命中 u10）。 */
export function isWithinDirectory(path: string, dir: string): boolean {
  const normPath = normalizeWorkspacePath(path)
  const normDir = normalizeWorkspacePath(dir)
  if (!normPath || !normDir) return false
  if (normDir === '/') return normPath.startsWith('/')
  return normPath === normDir || normPath.startsWith(`${normDir}/`)
}

/**
 * 从用户工作区根推算平台 data 根目录。
 *
 * 用户私有工作区根固定落在 `<data_base>/agent_workspaces/<user_key>`，
 * 据此可以把公共目录与管理员浏览路径还原成相对 data 根的可读面包屑，
 * 而不会暴露宿主机物理路径或把绝对路径错误拼到用户根后面。
 */
export function inferDataBaseDir(userWorkspaceRoot?: string | null): string {
  const normRoot = normalizeWorkspacePath(userWorkspaceRoot || '')
  if (!normRoot) return ''
  const marker = '/agent_workspaces/'
  const idx = normRoot.indexOf(marker)
  if (idx <= 0) return ''
  return normRoot.slice(0, idx)
}

export interface WorkspaceBreadcrumbSegment {
  label: string
  path: string
}

export interface BuildWorkspaceBreadcrumbsInput {
  currentPath: string
  userWorkspaceRoot?: string | null
  isVirtualRoot?: boolean
  searchMode?: 'current' | 'all'
  keyword?: string
}

/**
 * 构造工作空间面包屑。
 *
 * 每一级的 `path` 都必须是后端 `/fs/list` 可直接接受的真实路径（或空串回「公共资源」）：
 * - 全目录搜索：单级搜索标题；
 * - 虚拟根：单级「公共资源」（空串）；
 * - 本人工作区及子目录：「我的目录」为首级，按相对路径逐级累加；
 * - 公共目录 / 管理员浏览其他目录：「公共资源」为首级，按真实物理路径逐级累加，
 *   绝不把外部绝对路径拼到 `userWorkspaceRoot` 下。
 */
export function buildWorkspaceBreadcrumbs(
  input: BuildWorkspaceBreadcrumbsInput,
): WorkspaceBreadcrumbSegment[] {
  const current = normalizeWorkspacePath(input.currentPath)
  const root = normalizeWorkspacePath(input.userWorkspaceRoot || '')

  if (input.searchMode === 'all') {
    return [{ label: `全目录搜索：${String(input.keyword || '').trim()}`, path: current }]
  }
  if (input.isVirtualRoot) {
    return [{ label: '公共资源', path: '' }]
  }
  if (!current) return []

  if (root && isWithinDirectory(current, root)) {
    const relative = current.slice(root.length).replace(/^\/+/, '')
    const segments: WorkspaceBreadcrumbSegment[] = [{ label: '我的目录', path: root }]
    let acc = root
    const parts = relative.split('/').filter(Boolean)
    for (let i = 0; i < parts.length; i += 1) {
      const part = parts[i]!
      acc = `${acc}/${part}`
      segments.push({ label: i === 0 && part === '.trash' ? '回收站' : part, path: acc })
    }
    return segments
  }

  const segments: WorkspaceBreadcrumbSegment[] = [{ label: '公共资源', path: '' }]
  const dataBase = inferDataBaseDir(root)
  if (dataBase && isWithinDirectory(current, dataBase)) {
    if (current === dataBase) return segments
    const relative = current.slice(dataBase.length).replace(/^\/+/, '')
    let acc = dataBase
    for (const part of relative.split('/').filter(Boolean)) {
      acc = `${acc}/${part}`
      segments.push({ label: part, path: acc })
    }
    return segments
  }

  // 平台外部公共根（例如本地开发时的 ~/.agents/skills）：从 skills 级起保留真实路径
  const skillsIdx = current.lastIndexOf('/skills')
  if (skillsIdx >= 0) {
    const afterSkills = current.slice(skillsIdx + '/skills'.length)
    if (afterSkills === '' || afterSkills.startsWith('/')) {
      const skillsRoot = current.slice(0, skillsIdx + '/skills'.length)
      segments.push({ label: 'skills', path: skillsRoot })
      let acc = skillsRoot
      for (const part of afterSkills.replace(/^\/+/, '').split('/').filter(Boolean)) {
        acc = `${acc}/${part}`
        segments.push({ label: part, path: acc })
      }
      return segments
    }
  }

  // 兜底：保留真实路径，仅以末级名称展示，避免暴露宿主机绝对路径前缀
  const label = current.split('/').filter(Boolean).pop() || current
  segments.push({ label, path: current })
  return segments
}

/** 根目录级别的系统保留名（平台 data 根与用户私有根直接子目录）。 */
export const WORKSPACE_ROOT_PROTECTED_NAMES = new Set([
  'docs',
  'skills',
  'branding',
  'agent_workspaces',
  '.trash',
  'sessions',
  'uploads',
  'sandbox',
])

export interface WorkspaceProtectCheckItem {
  name: string
  path: string
  is_dir?: boolean
  is_user_workspace?: boolean
  is_public?: boolean
}

/**
 * 判断条目是否为系统保留/受保护目录（不可重命名、不可删除）。
 *
 * 必须结合所在目录层级判定，而不是裸查 `SET.has(item.name)`：
 * - 保护：本人私有根直接子目录（docs / uploads / sessions / sandbox / skills / .trash）、
 *   平台 data 根直接子目录（docs / skills / branding / agent_workspaces）、
 *   任意用户工作区根目录（agent_workspaces/<user_key> 或 is_user_workspace=true）；
 * - 放行：用户在项目子目录里自建的同名文件夹（如 `my-project/docs`、`project/uploads`）。
 */
export function isProtectedWorkspaceEntry(
  item: WorkspaceProtectCheckItem | null | undefined,
  userWorkspaceRoot?: string | null,
  protectedNames: ReadonlySet<string> = WORKSPACE_ROOT_PROTECTED_NAMES,
): boolean {
  if (!item) return false
  if (item.is_user_workspace) return true
  const normPath = normalizeWorkspacePath(item.path)
  const normRoot = normalizeWorkspacePath(userWorkspaceRoot || '')
  const dataBase = inferDataBaseDir(normRoot)

  if (normPath && normRoot && normPath === normRoot) return true

  const parent = parentDirectoryOf(normPath)
  // 任意用户工作区根目录（<data_base>/agent_workspaces/<user_key>）本身受保护
  if (dataBase && parent === `${dataBase}/agent_workspaces`) return true

  if (!protectedNames.has(item.name)) return false

  // 本人私有工作区根的直接子保留目录
  if (normRoot && parent === normRoot) return true
  // 平台 data 根的直接子保留目录
  if (dataBase && parent === dataBase) return true
  // 外部平台技能根（如 ~/.agents/skills）本身
  if (item.is_dir && item.is_public && item.name === 'skills' && normPath.endsWith('/skills')) {
    return !parent.includes('/skills/') && !parent.endsWith('/skills')
  }
  return false
}

/**
 * 将工作空间物理路径格式化为对用户可读的相对路径（不暴露宿主机绝对路径前缀）。
 *
 * 主要用于「含子目录搜索」结果副标题和「恢复位置」提示，避免出现 `/app/data/...` 或 `/Users/...`。
 */
export function formatRelativeWorkspacePath(
  path: string | null | undefined,
  userWorkspaceRoot?: string | null,
): string {
  const norm = normalizeWorkspacePath(path || '')
  if (!norm) return ''
  const root = normalizeWorkspacePath(userWorkspaceRoot || '')

  if (root && isWithinDirectory(norm, root)) {
    const rel = norm.slice(root.length).replace(/^\/+/, '')
    if (!rel) return '我的目录'
    const parts = rel.split('/').filter(Boolean)
    if (parts[0] === '.trash') parts[0] = '回收站'
    return `我的目录/${parts.join('/')}`
  }

  const dataBase = inferDataBaseDir(root)
  if (dataBase && isWithinDirectory(norm, dataBase)) {
    const rel = norm.slice(dataBase.length).replace(/^\/+/, '')
    return rel ? `公共资源/${rel}` : '公共资源'
  }

  const skillsIdx = norm.lastIndexOf('/skills')
  if (skillsIdx >= 0) {
    const afterSkills = norm.slice(skillsIdx + '/skills'.length)
    if (afterSkills === '' || afterSkills.startsWith('/')) {
      return `公共资源/skills${afterSkills}`
    }
  }

  return norm.split('/').filter(Boolean).pop() || norm
}
