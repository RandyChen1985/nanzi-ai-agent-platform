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
    }
    entries.push(mountEntry(isDir))
    entries.push(entry('copy-path', '复制路径', '📋'))

    if (isAdminScope && !ctx.isSystemProtected) {
      entries.push(entry('rename', '重命名', '✏️', { separatorBefore: true }))
      entries.push(entry('purge', '永久删除', '🗑️', { danger: true }))
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

  return [
    previewEntry(target),
    entry('download', '下载文件', '⬇️'),
    entry('copy-path', '复制路径', '📋'),
    mountEntry(isDir),
    ...manageEntries,
  ]
}
