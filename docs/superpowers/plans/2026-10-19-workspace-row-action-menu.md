# 工作区行操作 ⋮ 菜单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工作区浏览器选中行的四个平铺按钮收成行尾一个 ⋮，弹出与右键菜单同源的竖向浮层菜单。

**Architecture:** 菜单项构造抽成零依赖纯函数 `buildWorkspaceActionEntries(ctx)`（可被 Node 直接断言）；显示逻辑抽成 `WorkspaceActionMenu.vue`（Teleport + 定位 + 键盘 + role=menu）；`WorkspaceBrowserDrawer.vue` 只保留状态与动作映射，行内 ⋮ 与右键都复用同一份 entries。

**Tech Stack:** Vue 3 SFC + TypeScript、Tailwind、heroicons、Node `--experimental-strip-types` 行为测试、pytest 源码契约测试。

**Spec:** `docs/superpowers/specs/2026-10-19-workspace-row-action-menu-design.md`

---

### Task 1: 菜单项构造纯函数（行为测试先行）

**Files:**
- Create: `frontend/src/utils/workspaceActionEntries.ts`
- Test: `tests/frontend/workspace_action_entries.mjs`

- [ ] **Step 1: 写失败的行为测试**

```js
// tests/frontend/workspace_action_entries.mjs
import assert from 'node:assert/strict'
import { buildWorkspaceActionEntries } from '../../frontend/src/utils/workspaceActionEntries.ts'

const keys = (ctx) => buildWorkspaceActionEntries(ctx).map((entry) => entry.key)

// 1. 普通文件：6 项，rename/delete 前有分隔线
const file = buildWorkspaceActionEntries({
  scene: 'regular-item',
  target: { name: 'report.md', path: '/w/report.md', isDir: false, isPreviewable: true },
})
assert.deepEqual(file.map((e) => e.key), ['preview', 'download', 'copy-path', 'mount', 'rename', 'delete'])
assert.equal(file.find((e) => e.key === 'rename').separatorBefore, true)
assert.equal(file.find((e) => e.key === 'delete').danger, true)

// 2. zip：预览禁用但下载可用（延续上一轮下载修复）
const zip = buildWorkspaceActionEntries({
  scene: 'regular-item',
  target: { name: 'a.zip', path: '/w/a.zip', isDir: false, isPreviewable: false },
})
assert.equal(zip.find((e) => e.key === 'preview').disabled, true)
assert.equal(zip.find((e) => e.key === 'download').disabled, undefined)

// 3. 普通目录：无 preview/download，有 open-dir
assert.deepEqual(keys({ scene: 'regular-item', target: { name: 'd', path: '/w/d', isDir: true } }),
  ['open-dir', 'copy-path', 'mount', 'rename', 'delete'])

// 4. 公共只读：有表头、无 rename/delete
const pub = buildWorkspaceActionEntries({
  scene: 'public-item',
  target: { name: 'guidelines.md', path: '/d/guidelines.md', isDir: false, isPreviewable: true },
})
assert.equal(pub[0].key, 'header')
assert.equal(pub[0].label, '公共只读资源')
assert.ok(!keys({ scene: 'public-item', target: { name: 'a', path: '/p/a', isDir: false, isPreviewable: true } }).includes('rename'))

// 5. 公共目录（管理员）+ 系统保护目录：无 rename/purge
const admin = buildWorkspaceActionEntries({
  scene: 'public-item', isAdminScope: true, isSystemProtected: true,
  target: { name: 'docs', path: '/data/docs', isDir: true },
})
assert.deepEqual(admin.map((e) => e.key), ['header', 'open-dir', 'mount', 'copy-path'])
assert.equal(admin[0].label, '公共目录（管理员）')

// 6. 回收站条目 / 回收站根 / 空白处
assert.deepEqual(keys({ scene: 'trash-item', target: { name: 'x.md', path: '/t/x.md', isDir: false, isPreviewable: true } }),
  ['restore', 'purge', 'preview', 'copy-path'])
assert.deepEqual(keys({ scene: 'trash-root', target: { name: '.trash', path: '/w/.trash', isDir: true } }),
  ['open-dir', 'empty-trash'])
assert.deepEqual(keys({ scene: 'empty-area', canCreateHere: true }), ['create-file', 'create-dir', 'upload'])
assert.deepEqual(keys({ scene: 'empty-area', canCreateHere: false }), [])

console.log('工作区行操作菜单项测试全部通过')
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --experimental-strip-types --no-warnings tests/frontend/workspace_action_entries.mjs`
Expected: `ERR_MODULE_NOT_FOUND`（模块还不存在）

- [ ] **Step 3: 写最小实现**

```ts
// frontend/src/utils/workspaceActionEntries.ts
export type WorkspaceActionKey =
  | 'header' | 'open-dir' | 'preview' | 'download' | 'copy-path' | 'mount'
  | 'rename' | 'delete' | 'restore' | 'purge' | 'empty-trash'
  | 'create-file' | 'create-dir' | 'upload'

export type WorkspaceMenuScene = 'regular-item' | 'public-item' | 'trash-item' | 'trash-root' | 'empty-area'

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
  isSystemProtected?: boolean
  canCreateHere?: boolean
}

export interface WorkspaceActionEntry {
  key: WorkspaceActionKey
  label: string
  icon: string
  disabled?: boolean
  disabledHint?: string
  danger?: boolean
  separatorBefore?: boolean
}

export function buildWorkspaceActionEntries(ctx: WorkspaceMenuContext): WorkspaceActionEntry[] {
  // 按 spec 的矩阵逐场景返回；header 用 key='header'。
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `node --experimental-strip-types --no-warnings tests/frontend/workspace_action_entries.mjs`
Expected: `工作区行操作菜单项测试全部通过`

---

### Task 2: 菜单显示子组件

**Files:**
- Create: `frontend/src/components/embed/WorkspaceActionMenu.vue`
- Test: `tests/frontend/test_workspace_row_action_menu_contract.py`（本任务部分断言）

- [ ] **Step 1: 写失败的契约测试（子组件部分）**

断言：`Teleport` 到 body、`role="menu"`、`Escape` 关闭、点外关闭（`mousedown`）、`separatorBefore` 渲染分隔线、`danger` 用红色类、`disabled` 项不可点且带 `aria-disabled`、菜单项 `min-h-[40px]`。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/frontend/test_workspace_row_action_menu_contract.py -q --confcutdir=tests/frontend`
Expected: FAIL（文件不存在）

- [ ] **Step 3: 实现子组件**

Props：`open: boolean`、`entries: WorkspaceActionEntry[]`、`anchor: {x:number;y:number} | null`。
Emits：`select(key)`、`close`。
内部：`Teleport to="body"`、`fixed z-[130]`、`role="menu"`、`v-for` 渲染、`separatorBefore` 上边线、`danger` 红色、`disabled` 置灰 + `aria-disabled` + `disabled`、`keydown.esc` 关闭、`document.mousedown` 外部关闭、`nextTick` 测量并夹取视口（照搬现有 `adjustContextMenuPosition` 的上下翻转 + `maxHeight` 逻辑）。

- [ ] **Step 4: 运行确认通过**

---

### Task 3: 父组件接线（行内 ⋮ + 右键共用）

**Files:**
- Modify: `frontend/src/components/embed/WorkspaceBrowserDrawer.vue`（行渲染 ~2144-2251、右键菜单 ~2570-2660、脚本 ~811-880）
- Test: `tests/frontend/test_workspace_row_action_menu_contract.py`（补父组件断言）

- [ ] **Step 1: 补失败的契约测试**

断言：行内不再出现 `画布打开`/`下载`/`复制路径`/`挂载文件` 四个平铺按钮的原始片段（`shouldShowRowActions` 块内只有一个按钮）；`buildWorkspaceActionEntries` 在组件中被调用且喂给 `<WorkspaceActionMenu>`；`contextMenu` 模板里的硬编码条目标签（`📋 复制路径`、`✏️ 重命名`、`🗑️ 删除`）不再逐条手写。

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 改父组件**

1. 新增状态：`rowActionMenu = ref<{ item, anchor } | null>(null)`、`contextMenu` 改为 `{ anchor, ctx }`。
2. 行内：删掉 `<div v-if="shouldShowRowActions(item)" …>…</div>` 整块，在「大小/时间」列内改为 `flex items-center justify-end gap-1.5`，右侧放 ⋮ 按钮（`v-if="shouldShowRowActions(item) && !multiSelectMode"`，`EllipsisVerticalIcon`，`aria-haspopup="menu"`，`@click.stop="toggleRowActionMenu($event, item)"`，热区 `p-2 -m-1`）。
3. 右键：`openContextMenu` 不再存 `parentPath/item` 供模板分支，而是构造 `ctx`（scene 判定 + `canManageItem` / `isPublicItem` / `isTrashListItem` / `isTrashRootItem` / `isSystemProtectedDir` / `canCreateInPath`）与 `anchor`；模板改为 `<WorkspaceActionMenu :open="!!contextMenu" :entries="contextMenuEntries" :anchor="contextMenu?.anchor ?? null" @select="runContextMenuAction" @close="closeContextMenu" />`。
4. 新增 `runWorkspaceAction(key, item)`：`preview→previewItem`、`download→downloadItem`、`copy-path→copyItemPath`、`mount→mountItemToSession`、`rename→startRenameEntry`、`delete/purge→confirmDeleteEntry/confirmPurgeEntry`、`restore→restoreTrashItem`、`empty-trash→confirmEmptyTrash`、`open-dir→fetchDirectory`、`create-file/create-dir→startCreateEntry`、`upload→openUploadPicker`；执行前先关菜单。
5. 关闭联动：`watch(currentPath)`、列表滚动（`@scroll`）、`handleRowClick` 里关闭菜单。
6. `empty-area` 场景沿用 `canUseCreateMenu` / `canCreateInPath` 的既有判定。

- [ ] **Step 4: 运行确认通过 + 手工核对右键六类场景**

Run: `pytest tests/frontend/test_workspace_row_action_menu_contract.py -q --confcutdir=tests/frontend && node --experimental-strip-types --no-warnings tests/frontend/workspace_action_entries.mjs`

---

### Task 4: 回归与记录

- [ ] **Step 1:** `pytest --confcutdir=tests/frontend tests/frontend -q` → 全绿（基线 2241 passed / 1 skipped）。
- [ ] **Step 2:** `cd frontend && NODE_OPTIONS=--max-old-space-size=8192 ./node_modules/.bin/vue-tsc -b --force` → 改动文件零新增错误。
- [ ] **Step 3:** 更新 `tests/CHECKLIST.md`（含 RED/GREEN 与变异验证记录）。
- [ ] **Step 4:** 汇报改动文件与验证结果（不执行 `git commit`，由用户决定）。
