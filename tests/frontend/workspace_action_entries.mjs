// 用 Node 真跑菜单项构造：分支组合（公共只读 / 管理员 / 系统保护 / 回收站）
// 用源码字符串断言看不出来，必须调用真实实现。
import assert from 'node:assert/strict'

import { buildWorkspaceActionEntries, buildWorkspaceItemMenuContext, isPublicWorkspaceItem, normalizeComparablePath } from '../../frontend/src/utils/workspaceActionEntries.ts'

const keys = (ctx) => buildWorkspaceActionEntries(ctx).map((entry) => entry.key)

// --- 1. 普通文件：7 项（分享紧跟下载），重命名/删除前有分隔线，删除为危险项 ---
const file = buildWorkspaceActionEntries({
  scene: 'regular-item',
  target: { name: 'report.md', path: '/w/report.md', isDir: false, isPreviewable: true },
})
assert.deepEqual(file.map((e) => e.key), ['preview', 'download', 'share', 'copy-path', 'mount', 'rename', 'delete'])
assert.equal(file.find((e) => e.key === 'rename').separatorBefore, true)
assert.equal(file.find((e) => e.key === 'delete').danger, true)
assert.equal(file.find((e) => e.key === 'delete').separatorBefore, undefined)

// --- 2. zip：预览禁用但下载可用，并提供「解压到…」---
const zip = buildWorkspaceActionEntries({
  scene: 'regular-item',
  target: { name: 'a.zip', path: '/w/a.zip', isDir: false, isPreviewable: false },
})
assert.equal(zip.find((e) => e.key === 'preview').disabled, true)
assert.equal(zip.find((e) => e.key === 'preview').disabledHint, '该格式不支持预览')
assert.equal(zip.find((e) => e.key === 'download').disabled, undefined)
assert.deepEqual(
  zip.map((e) => e.key),
  ['preview', 'download', 'share', 'extract', 'copy-path', 'mount', 'rename', 'delete'],
)
assert.equal(zip.find((e) => e.key === 'extract').label, '解压到…')
assert.equal(zip.find((e) => e.key === 'extract').disabled, undefined)

// --- 2b. rar/7z：保留入口但置灰，并给出可执行的原因 ---
for (const name of ['a.rar', 'a.7z']) {
  const entry = buildWorkspaceActionEntries({
    scene: 'regular-item',
    target: { name, path: `/w/${name}`, isDir: false, isPreviewable: false },
  }).find((e) => e.key === 'extract')
  assert.ok(entry, `${name} 应保留解压入口`)
  assert.equal(entry.disabled, true)
  assert.match(entry.disabledHint, /rar|7z/i)
}

// --- 2c. tar / tar.gz / tgz 都算可解压 ---
for (const name of ['a.tar', 'a.tar.gz', 'a.tgz', 'A.ZIP']) {
  const entry = buildWorkspaceActionEntries({
    scene: 'regular-item',
    target: { name, path: `/w/${name}`, isDir: false, isPreviewable: false },
  }).find((e) => e.key === 'extract')
  assert.ok(entry, `${name} 应可解压`)
  assert.equal(entry.disabled, undefined)
}

// --- 2d. 目录即使叫 x.zip 也不出现解压 ---
assert.ok(
  !keys({ scene: 'regular-item', target: { name: 'x.zip', path: '/w/x.zip', isDir: true } }).includes(
    'extract',
  ),
)

// --- 2e. 公共只读目录里的压缩包也能解压（源只读，目标是自己的目录）---
const pubZip = buildWorkspaceActionEntries({
  scene: 'public-item',
  target: { name: 'bundle.tar.gz', path: '/docs/bundle.tar.gz', isDir: false, isPreviewable: false },
})
assert.ok(pubZip.some((e) => e.key === 'extract' && !e.disabled))

// --- 2f. 回收站里的压缩包不给解压 ---
assert.ok(
  !keys({
    scene: 'trash-item',
    target: { name: 'a.zip', path: '/t/a.zip', isDir: false, isPreviewable: false },
  }).includes('extract'),
)

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
  ['preview', 'download', 'share', 'copy-path', 'mount'],
)

// --- 4. 公共只读：有表头、无重命名/删除 ---
const pub = buildWorkspaceActionEntries({
  scene: 'public-item',
  target: { name: 'guidelines.md', path: '/d/guidelines.md', isDir: false, isPreviewable: true },
})
assert.equal(pub[0].key, 'header')
assert.equal(pub[0].label, '公共只读资源')
assert.deepEqual(pub.map((e) => e.key), ['header', 'preview', 'download', 'share', 'mount', 'copy-path'])

// --- 5. 公共目录（管理员）+ 系统保护目录：无重命名/永久删除 ---
const admin = buildWorkspaceActionEntries({
  scene: 'public-item',
  isAdminScope: true,
  isSystemProtected: true,
  target: { name: 'docs', path: '/data/docs', isDir: true },
})
assert.deepEqual(admin.map((e) => e.key), ['header', 'open-dir', 'mount', 'copy-path'])
assert.equal(admin[0].label, '公共目录（管理员）')

// --- 6. 公共目录（管理员）非保护目录：补回重命名/删除 ---
// 公共条目的删除必须走 delete（= /fs/delete-entry，后端对 admin + 公共路径直接永删），
// 不能复用回收站专用的 purge（= /fs/purge-entry，后端只允许回收站内路径，会 403）。
const adminPublic = buildWorkspaceActionEntries({
  scene: 'public-item',
  isAdminScope: true,
  isSystemProtected: false,
  target: { name: 'reports', path: '/data/reports', isDir: true },
})
assert.deepEqual(
  adminPublic.map((e) => e.key),
  ['header', 'open-dir', 'mount', 'copy-path', 'rename', 'delete'],
)
assert.equal(adminPublic.find((e) => e.key === 'delete').label, '永久删除')
assert.equal(adminPublic.find((e) => e.key === 'delete').danger, true)
assert.ok(
  !adminPublic.some((e) => e.key === 'purge'),
  '公共目录条目不得派发到回收站专属的 purge 接口',
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

// --- 9. 公共条目判定：抽屉与管理页必须同口径 ---
// 管理员删公共目录 = 后端直接永删（不进回收站），两个入口的确认文案都依赖这个判定，
// 一旦各写一份就会再次分叉（本次 403 就是这么来的）。
const ROOT = '/data/agent_workspaces/alice'
assert.equal(isPublicWorkspaceItem({ path: '/data/docs/88', is_public: true }, ROOT), true)
// 后端明确下发 false 时以后端为准，不再用路径猜
assert.equal(isPublicWorkspaceItem({ path: '/data/docs/88', is_public: false }, ROOT), false)
// 用户工作区容器本身不是「公共资源」，哪怕它不在本人私有根下
assert.equal(isPublicWorkspaceItem({ path: '/data/agent_workspaces/bob', is_user_workspace: true }, ROOT), false)
// 字段缺失时的路径兜底
assert.equal(isPublicWorkspaceItem({ path: '/data/docs/88' }, ROOT), true)
assert.equal(isPublicWorkspaceItem({ path: '/data/skills/x/SKILL.md' }, ROOT), true)
assert.equal(isPublicWorkspaceItem({ path: '/data/agent_workspaces/alice/88' }, ROOT), false)
assert.equal(isPublicWorkspaceItem({ path: '/data/agent_workspaces/alice' }, ROOT), false)
// 尾部斜杠 / 反斜杠 / 大小写无关
assert.equal(isPublicWorkspaceItem({ path: '/data/agent_workspaces/alice/sub/' }, `${ROOT}/`), false)
assert.equal(isPublicWorkspaceItem({ path: '\\data\\agent_workspaces\\alice\\sub' }, ROOT), false)
// 判定不出来时保守返回 false：不能凭空说「公共目录，将永久删除」
assert.equal(isPublicWorkspaceItem(null, ROOT), false)
assert.equal(isPublicWorkspaceItem({ path: '/data/docs/x' }, ''), false)
assert.equal(isPublicWorkspaceItem({}, ROOT), false)
assert.equal(normalizeComparablePath('/a/b//'), '/a/b')

// --- 10. 条目 → 菜单场景：抽屉与管理页行操作必须落到同一个分支 ---
const root = '/data/agent_workspaces/alice'
const target = (name, path, isDir = false) => ({ name, path, isDir, isPreviewable: !isDir })

// 管理员看公共目录：public-item + 重命名/永久删除（走 delete 键）
assert.deepEqual(
  keys(buildWorkspaceItemMenuContext({
    item: { path: '/data/docs/88', name: '88', is_dir: true },
    target: target('88', '/data/docs/88', true),
    userWorkspaceRoot: root,
    isAdminScope: true,
    isSystemProtected: false,
  })),
  ['header', 'open-dir', 'mount', 'copy-path', 'rename', 'delete'],
)

// 普通用户看同一个公共目录：只剩只读项，不再出现点了就 403 的删除
assert.deepEqual(
  keys(buildWorkspaceItemMenuContext({
    item: { path: '/data/docs/88', name: '88', is_dir: true },
    target: target('88', '/data/docs/88', true),
    userWorkspaceRoot: root,
    isAdminScope: false,
  })),
  ['header', 'open-dir', 'mount', 'copy-path'],
)

// 系统保护目录：管理员也没有重命名/永久删除
assert.deepEqual(
  keys(buildWorkspaceItemMenuContext({
    item: { path: '/data/docs', name: 'docs', is_dir: true },
    target: target('docs', '/data/docs', true),
    userWorkspaceRoot: root,
    isAdminScope: true,
    isSystemProtected: true,
  })),
  ['header', 'open-dir', 'mount', 'copy-path'],
)

// 自己的文件：regular-item，可以删（进回收站）
assert.deepEqual(
  keys(buildWorkspaceItemMenuContext({
    item: { path: `${root}/a.md`, name: 'a.md', is_dir: false, is_public: false },
    target: target('a.md', `${root}/a.md`),
    userWorkspaceRoot: root,
    isAdminScope: true,
  })),
  ['preview', 'download', 'share', 'copy-path', 'mount', 'rename', 'delete'],
)

// 回收站优先于公共判定：trash 条目即使路径不在本人工作区内也是 trash-item
assert.deepEqual(
  keys(buildWorkspaceItemMenuContext({
    item: { path: `${root}/.trash/1_ab_x.md`, name: 'x.md', is_dir: false },
    target: target('x.md', `${root}/.trash/1_ab_x.md`),
    userWorkspaceRoot: root,
    inTrash: true,
  })),
  ['restore', 'purge', 'preview', 'copy-path'],
)
assert.deepEqual(
  keys(buildWorkspaceItemMenuContext({
    item: { path: `${root}/.trash`, name: '.trash', is_dir: true },
    target: target('.trash', `${root}/.trash`, true),
    userWorkspaceRoot: root,
    isTrashRoot: true,
  })),
  ['open-dir', 'empty-trash'],
)

// canManage=false（用户工作区根目录本身）不得给出重命名/删除
assert.deepEqual(
  keys(buildWorkspaceItemMenuContext({
    item: { path: root, name: 'alice', is_dir: true },
    target: target('alice', root, true),
    userWorkspaceRoot: root,
    canManage: false,
  })),
  ['open-dir', 'copy-path', 'mount'],
)

console.log('工作区行操作菜单项测试全部通过')
