/**
 * 工作区行操作菜单项构造 —— 刻意保持零运行时依赖。
 *
 * 行内 ⋮ 与右键菜单共用本函数，保证两个入口的条目、顺序、禁用态完全一致
 * （此前行内是四个平铺按钮、右键是模板里的硬编码分支，能力并不相同：
 * 移动端没有右键，就等于没有重命名/删除）。
 *
 * 放在零依赖模块里，是为了能被 `node --experimental-strip-types` 直接加载，
 * 对「公共只读 / 管理员 / 系统保护目录 / 回收站」这些分支组合做真实行为断言
 * （见 tests/frontend/workspace_action_entries.mjs）。
 */

export type WorkspaceActionKey =
  | 'header'
  | 'open-dir'
  | 'preview'
  | 'download'
  | 'share'
  | 'copy-and-share'
  | 'extract'
  | 'copy-path'
  | 'mount'
  | 'rename'
  | 'delete'
  | 'restore'
  | 'purge'
  | 'empty-trash'
  | 'create-file'
  | 'create-dir'
  | 'upload'

export type WorkspaceMenuScene =
  | 'regular-item'
  | 'public-item'
  | 'trash-item'
  | 'trash-root'
  | 'empty-area'

export interface WorkspaceActionTarget {
  name: string
  path: string
  isDir: boolean
  isPreviewable?: boolean
}

export interface WorkspaceMenuContext {
  scene: WorkspaceMenuScene
  target?: WorkspaceActionTarget
  isAdminScope?: boolean
  /**
   * 本人私有工作区根。
   *
   * 管理员的范围是 `admin_all`，能浏览任何人的目录，但「能看」不等于「能分享」：
   * 分享链接在 public_router 上公开可读、access_mode 还能选 public 免密码，
   * 后端因此把分享收紧成 owner 严格相等。场景判定要拿它区分「我的」与「别人的」。
   */
  userWorkspaceRoot?: string | null
  /** 系统保护目录（docs / skills / branding / agent_workspaces）不可重命名或删除。 */
  isSystemProtected?: boolean
  /** 当前条目是否可管理（用户工作区根目录本身不可重命名/删除）。缺省视为可管理。 */
  canManage?: boolean
  canCreateHere?: boolean
}

export interface WorkspaceActionEntry {
  key: WorkspaceActionKey
  label: string
  icon: string
  disabled?: boolean
  /** 禁用原因，渲染为 title，避免「灰了但不知道为什么」。 */
  disabledHint?: string
  danger?: boolean
  separatorBefore?: boolean
}

type EntryExtras = Omit<WorkspaceActionEntry, 'key' | 'label' | 'icon'>

function entry(
  key: WorkspaceActionKey,
  label: string,
  icon: string,
  extras: EntryExtras = {},
): WorkspaceActionEntry {
  return { key, label, icon, ...extras }
}

/**
 * 路径比较用的归一化：统一分隔符、去掉尾部斜杠。
 *
 * 与抽屉里的历史内联实现保持一致（`\` → `/`，尾斜杠去掉），
 * 抽到这里是为了让抽屉与管理页共用同一份口径。
 */
export function normalizeComparablePath(path: string): string {
  return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '')
}

/** 判断路径是否落在某个根目录（含根本身）下。 */
export function isPathUnderRoot(path: string, root: string): boolean {
  const target = normalizeComparablePath(path)
  const base = normalizeComparablePath(root)
  if (!target || !base) return false
  return target === base || target.startsWith(`${base}/`)
}

/**
 * 条目是否落在本人工作区根下——只有这种文件才允许分享。
 *
 * 判不出来（没有本人工作区根）时返回 true：宁可让后端拒绝，也不能凭空
 * 禁掉用户分享自己的文件。
 */
export function canShareOwnedItem(
  target: { path?: string } | null | undefined,
  userWorkspaceRoot?: string | null,
): boolean {
  if (!target?.path) return false
  if (!normalizeComparablePath(userWorkspaceRoot || '')) return true
  return isPathUnderRoot(target.path, userWorkspaceRoot || '')
}

export interface WorkspacePublicityItem {
  path?: string
  is_public?: boolean
  is_user_workspace?: boolean
}

/**
 * 条目是否属于「公共目录」（平台只读资源，如 docs / skills / branding）。
 *
 * 这是「管理员删它 = 永久删除、不进任何人回收站」的唯一判定来源，
 * 抽屉的菜单/确认框与管理页的确认框都必须走这里。
 * 后端下发的 `is_public` 优先；字段缺失时才按「是否在本人工作区根内」兜底，
 * 判不出来一律返回 false —— 宁可提示「移入回收站」，也不能凭空宣称永久删除。
 */
export function isPublicWorkspaceItem(
  item: WorkspacePublicityItem | null | undefined,
  userWorkspaceRoot?: string | null,
): boolean {
  if (!item) return false
  // 用户工作区容器本身：别人的私有目录不是公共资源
  if (item.is_user_workspace) return false
  if (typeof item.is_public === 'boolean') return item.is_public
  if (!item.path) return false
  // 拿不到本人工作区根就无法判断「是不是我的」，此时不下「公共」结论
  if (!normalizeComparablePath(userWorkspaceRoot || '')) return false
  return !isPathUnderRoot(item.path, userWorkspaceRoot || '')
}

/** 一条文件记录里与本模块判定相关的字段（抽屉与管理页的条目结构都满足）。 */
export interface WorkspaceItemLike {
  path?: string
  name?: string
  is_dir?: boolean
  is_public?: boolean
  is_user_workspace?: boolean
}

export interface WorkspaceItemMenuInput {
  item: WorkspaceItemLike
  /** 已解析好的展示名（列表返回的 name 与展示名不一定相同，交给调用方解析）。 */
  target: WorkspaceActionTarget
  /** 本人私有工作区根，用于在 `is_public` 缺失时兜底判定公共性。 */
  userWorkspaceRoot?: string | null
  isAdminScope?: boolean
  /** 回收站条目（在 .trash 下，但不是 .trash 本身）。 */
  inTrash?: boolean
  /** 回收站根目录本身。 */
  isTrashRoot?: boolean
  /** 系统保护目录（docs / skills / branding / agent_workspaces 等），由调用方判定。 */
  isSystemProtected?: boolean
  /** 该条目是否可管理（用户工作区根目录本身不可重命名/删除），缺省视为可管理。 */
  canManage?: boolean
}

/**
 * 条目 → 菜单场景。
 *
 * 抽屉（⋮ / 右键 / 批量）与独立页行操作共用这一份判定，避免「同一个公共目录
 * 在抽屉里是只读、在管理页却能点删除」这类分叉。判定顺序有含义：
 * 回收站优先（.trash 里的路径不在任何「公共目录」语义内），其后才是公共性。
 */
export function buildWorkspaceItemMenuContext(input: WorkspaceItemMenuInput): WorkspaceMenuContext {
  const { item, target } = input
  const userWorkspaceRoot = input.userWorkspaceRoot
  if (input.isTrashRoot) return { scene: 'trash-root', target, userWorkspaceRoot }
  if (input.inTrash) return { scene: 'trash-item', target, userWorkspaceRoot }
  if (isPublicWorkspaceItem(item, input.userWorkspaceRoot)) {
    return {
      scene: 'public-item',
      target,
      userWorkspaceRoot,
      isAdminScope: !!input.isAdminScope,
      isSystemProtected: !!input.isSystemProtected,
    }
  }
  return { scene: 'regular-item', target, userWorkspaceRoot, canManage: input.canManage !== false }
}

function mountEntry(isDir: boolean): WorkspaceActionEntry {
  return isDir
    ? entry('mount', '挂载文件夹到会话', '📎')
    : entry('mount', '挂载文件到会话', '📎')
}

function previewEntry(target: WorkspaceActionTarget): WorkspaceActionEntry {
  const disabled = target.isPreviewable === false
  return entry('preview', '画布打开预览', '👁️', {
    disabled,
    disabledHint: disabled ? '该格式不支持预览' : undefined,
  })
}

/** 服务端能解的格式，必须与 app/services/workspace_archive.py 的白名单一致。 */
const EXTRACTABLE_SUFFIXES = ['.tar.gz', '.tgz', '.tar', '.zip']
/** 认识但解不了的格式：保留入口、置灰并说明，而不是凭空消失。 */
const UNSUPPORTED_ARCHIVE_SUFFIXES = ['.rar', '.7z']

export function isExtractableArchive(name: string): boolean {
  const lower = String(name || '').toLowerCase()
  return EXTRACTABLE_SUFFIXES.some((suffix) => lower.endsWith(suffix))
}

export function isUnsupportedArchive(name: string): boolean {
  const lower = String(name || '').toLowerCase()
  return UNSUPPORTED_ARCHIVE_SUFFIXES.some((suffix) => lower.endsWith(suffix))
}

function extractEntry(target: WorkspaceActionTarget): WorkspaceActionEntry | null {
  if (isExtractableArchive(target.name)) {
    return entry('extract', '解压到…', '🗜️')
  }
  if (isUnsupportedArchive(target.name)) {
    return entry('extract', '解压到…', '🗜️', {
      disabled: true,
      disabledHint: '暂不支持 rar / 7z，请先转成 zip',
    })
  }
  return null
}

export function buildWorkspaceActionEntries(ctx: WorkspaceMenuContext): WorkspaceActionEntry[] {
  const { scene, target } = ctx

  if (scene === 'empty-area') {
    if (!ctx.canCreateHere) return []
    return [
      entry('create-file', '新建文件', '📄'),
      entry('create-dir', '新建文件夹', '📁'),
      entry('upload', '上传到此目录', '⬆️'),
    ]
  }

  if (!target) return []

  const isDir = !!target.isDir

  if (scene === 'trash-root') {
    return [
      entry('open-dir', '打开', '📂'),
      entry('empty-trash', '清空回收站', '🗑️', { danger: true }),
    ]
  }

  if (scene === 'trash-item') {
    const entries = [
      entry('restore', '恢复', '♻️'),
      entry('purge', '永久删除', '🗑️', { danger: true }),
    ]
    // 与既有模板一致：不可预览的回收站文件不显示预览项，而不是置灰。
    if (!isDir && target.isPreviewable !== false) {
      entries.push(entry('preview', '画布打开预览', '👁️'))
    }
    entries.push(entry('copy-path', '复制路径', '📋'))
    return entries
  }

  if (scene === 'public-item') {
    const isAdminScope = !!ctx.isAdminScope
    const entries: WorkspaceActionEntry[] = [
      entry('header', isAdminScope ? '公共目录（管理员）' : '公共只读资源', isAdminScope ? '🗂️' : '🔒'),
    ]
    if (isDir) {
      entries.push(entry('open-dir', '打开目录', '📂'))
    } else {
      entries.push(previewEntry(target))
      entries.push(entry('download', '下载文件', '⬇️'))
      // 公共目录只读，且不在分享支持的路径范围内（分享记录按工作区内的相对路径定位文件），
      // 直接「分享文件」会在填完密码和有效期之后才报「文件不在工作区目录内」。
      // 改成「复制并分享」：先落到本人工作区，再对副本分享，一步可达。
      entries.push(entry('copy-and-share', '复制并分享', '🔗'))
      // 公共目录只读的是「源」，解压目标另行选择，普通用户也能解到自己的目录
      const extract = extractEntry(target)
      if (extract) entries.push(extract)
    }
    entries.push(mountEntry(isDir))
    entries.push(entry('copy-path', '复制路径', '📋'))

    if (isAdminScope && !ctx.isSystemProtected) {
      entries.push(entry('rename', '重命名', '✏️', { separatorBefore: true }))
      // 表单上写「永久删除」，派发的却是 delete：公共目录不属于任何人的回收站，
      // 后端 /fs/delete-entry 对 admin + 公共路径直接 rmtree/remove（trashed_path 为空）。
      // 不能派发 purge —— /fs/purge-entry 只接受回收站内路径，会 403「只能操作回收站内的文件或文件夹」。
      // 抽屉侧据 is_public 切换确认文案，这里只需保证走对接口。
      entries.push(entry('delete', '永久删除', '🗑️', { danger: true }))
    }
    return entries
  }

  // regular-item：用户自己的文件/目录
  const canManage = ctx.canManage !== false
  const manageEntries = canManage
    ? [
        entry('rename', '重命名', '✏️', { separatorBefore: true }),
        entry('delete', '删除', '🗑️', { danger: true }),
      ]
    : []

  if (isDir) {
    return [
      entry('open-dir', '打开目录', '📂'),
      entry('copy-path', '复制路径', '📋'),
      mountEntry(isDir),
      ...manageEntries,
    ]
  }

  // 管理员浏览他人工作区时也走 regular-item（`is_public_fs_path` 只认公共目录，
  // 别人的私有目录一律 false），所以分享权限必须在这里再判一次归属——
  // 否则菜单可点、点完才收到「只能分享本人工作区的文件」。
  const shareable = canShareOwnedItem(target, ctx.userWorkspaceRoot)
  const fileEntries: WorkspaceActionEntry[] = [
    previewEntry(target),
    entry('download', '下载文件', '⬇️'),
    // 分享紧跟下载：都属于「把这份文件交给别人」，挨着放才顺手
    shareable
      ? entry('share', '分享文件', '🔗')
      : entry('share', '分享文件', '🔗', {
          disabled: true,
          disabledHint: '只能分享本人工作区的文件；要分享他人或公共内容，先复制到我的工作区',
        }),
  ]
  const extract = extractEntry(target)
  if (extract) fileEntries.push(extract)
  fileEntries.push(entry('copy-path', '复制路径', '📋'), mountEntry(isDir), ...manageEntries)
  return fileEntries
}
