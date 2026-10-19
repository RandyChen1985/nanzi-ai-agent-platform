// 用 Node 真跑菜单项构造：分支组合（公共只读 / 管理员 / 系统保护 / 回收站）
// 用源码字符串断言看不出来，必须调用真实实现。
import assert from 'node:assert/strict'

import { buildWorkspaceActionEntries } from '../../frontend/src/utils/workspaceActionEntries.ts'

const keys = (ctx) => buildWorkspaceActionEntries(ctx).map((entry) => entry.key)

// --- 1. 普通文件：6 项，重命名/删除前有分隔线，删除为危险项 ---
const file = buildWorkspaceActionEntries({
  scene: 'regular-item',
  target: { name: 'report.md', path: '/w/report.md', isDir: false, isPreviewable: true },
})
assert.deepEqual(file.map((e) => e.key), ['preview', 'download', 'copy-path', 'mount', 'rename', 'delete'])
assert.equal(file.find((e) => e.key === 'rename').separatorBefore, true)
assert.equal(file.find((e) => e.key === 'delete').danger, true)
assert.equal(file.find((e) => e.key === 'delete').separatorBefore, undefined)

// --- 2. zip：预览禁用但下载可用（延续上一轮下载修复）---
const zip = buildWorkspaceActionEntries({
  scene: 'regular-item',
  target: { name: 'a.zip', path: '/w/a.zip', isDir: false, isPreviewable: false },
})
assert.equal(zip.find((e) => e.key === 'preview').disabled, true)
assert.equal(zip.find((e) => e.key === 'preview').disabledHint, '该格式不支持预览')
assert.equal(zip.find((e) => e.key === 'download').disabled, undefined)

// --- 3. 普通目录：无预览/下载，有打开目录 ---
assert.deepEqual(
  keys({ scene: 'regular-item', target: { name: 'd', path: '/w/d', isDir: true } }),
  ['open-dir', 'copy-path', 'mount', 'rename', 'delete'],
)

// --- 3b. 普通条目但不可管理（用户工作区根目录本身等）：不得提供重命名/删除 ---
assert.deepEqual(
  keys({
    scene: 'regular-item',
    canManage: false,
    target: { name: 'r.md', path: '/w/r.md', isDir: false, isPreviewable: true },
  }),
  ['preview', 'download', 'copy-path', 'mount'],
)

// --- 4. 公共只读：有表头、无重命名/删除 ---
const pub = buildWorkspaceActionEntries({
  scene: 'public-item',
  target: { name: 'guidelines.md', path: '/d/guidelines.md', isDir: false, isPreviewable: true },
})
assert.equal(pub[0].key, 'header')
assert.equal(pub[0].label, '公共只读资源')
assert.deepEqual(pub.map((e) => e.key), ['header', 'preview', 'download', 'mount', 'copy-path'])

// --- 5. 公共目录（管理员）+ 系统保护目录：无重命名/永久删除 ---
const admin = buildWorkspaceActionEntries({
  scene: 'public-item',
  isAdminScope: true,
  isSystemProtected: true,
  target: { name: 'docs', path: '/data/docs', isDir: true },
})
assert.deepEqual(admin.map((e) => e.key), ['header', 'open-dir', 'mount', 'copy-path'])
assert.equal(admin[0].label, '公共目录（管理员）')

// --- 6. 公共目录（管理员）非保护目录：补回重命名/永久删除 ---
assert.deepEqual(
  keys({
    scene: 'public-item',
    isAdminScope: true,
    isSystemProtected: false,
    target: { name: 'reports', path: '/data/reports', isDir: true },
  }),
  ['header', 'open-dir', 'mount', 'copy-path', 'rename', 'purge'],
)

// --- 7. 回收站条目 / 回收站根 / 空白处 ---
assert.deepEqual(
  keys({ scene: 'trash-item', target: { name: 'x.md', path: '/t/x.md', isDir: false, isPreviewable: true } }),
  ['restore', 'purge', 'preview', 'copy-path'],
)
assert.deepEqual(
  keys({ scene: 'trash-root', target: { name: '.trash', path: '/w/.trash', isDir: true } }),
  ['open-dir', 'empty-trash'],
)
assert.deepEqual(keys({ scene: 'empty-area', canCreateHere: true }), ['create-file', 'create-dir', 'upload'])
assert.deepEqual(keys({ scene: 'empty-area', canCreateHere: false }), [])

// --- 8. 无 target 时不应伪造条目 ---
assert.deepEqual(keys({ scene: 'regular-item' }), [])
assert.deepEqual(keys({ scene: 'trash-item' }), [])

console.log('工作区行操作菜单项测试全部通过')
