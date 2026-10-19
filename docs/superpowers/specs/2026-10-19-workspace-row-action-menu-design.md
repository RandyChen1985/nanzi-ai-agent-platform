# 工作区文件行操作收成 ⋮ 竖向菜单

- 日期：2026-10-19
- 涉及：`frontend/src/components/embed/WorkspaceBrowserDrawer.vue`、`frontend/src/components/embed/WorkspaceActionMenu.vue`（新增）、`frontend/src/utils/workspaceActionEntries.ts`（新增）、`tests/frontend/workspace_action_entries.mjs`（新增）、`tests/frontend/test_workspace_row_action_menu_contract.py`（新增）、`tests/CHECKLIST.md`

## 背景

工作区浏览器（`WorkspaceBrowserDrawer.vue`）里选中某一行后，行内会平铺出四个文字按钮：

```
画布打开 | 下载 | 复制路径 | 挂载文件
```

它们渲染在「大小 / 修改时间」列下方（`shouldShowRowActions(item)` 判 `selectedItem`），宽度不够时 `flex-wrap` 换行——实测 1001px 宽的抽屉里「挂载文件」会掉到第二行，行高被撑成两排，且四个按钮与旁边的类型徽章、时间戳在视觉上互相争抢。

同时，右键菜单里已经有同一批动作（还多出「重命名 / 删除」），两处入口能力不一致：移动端没有右键，用户就找不到重命名/删除。

## 目标

1. 行内四个平铺按钮移除，改为**选中行时行尾出现一个 ⋮ 按钮**（不改变行高）。
2. 点 ⋮ 弹出**竖向浮层菜单**，条目与右键菜单**完全同源**（同一份构造器），并补齐重命名 / 删除。
3. 菜单竖排、可滚动、自动夹取视口（下方空间不足时向上翻转），Teleport 到 `body`。
4. 桌面与移动端一致：移动端不依赖 hover，⋮ 随选中行常显，热区 ≥ 40px。
5. 右键菜单语义一条不丢：公共只读表头、公共目录（管理员）表头、系统保护目录禁止重命名/删除、回收站分支、空白处「新建/上传」。

## 非目标

- 不改目录列表的列布局（名称/类型/大小三列宽度不变），不改排序、搜索、最近文件、网格视图。
- 不改任何后端接口；⋮ 菜单里的动作全部复用现有函数（`previewItem` / `downloadItem` / `copyItemPath` / `mountItemToSession` / `startRenameEntry` / `confirmDeleteEntry` / `restoreTrashItem` / `confirmPurgeEntry` / `confirmEmptyTrash` / `startCreateEntry` / `openUploadPicker`；回收站根的「打开」并入 `open-dir`，原 `openTrashFolder` 包装随之删除）。
- 不新增长按以外的移动端手势；既有 550ms 长按（`handleTouchStart`）**保留**，它现在复用同一个 `actionMenu` 状态，与点 ⋮ 打开的是同一个菜单，因此不必改动。
- 不改批量选择模式：多选时不显示 ⋮，批量操作仍走底部「批量挂载」横幅。

## 交互设计

```
选中行（原）                          选中行（新）
┌──────────────────────────────┐     ┌──────────────────────────────┐
│ 📄 glob-test.md   Markdown 1B│     │ 📄 glob-test.md  Markdown 1B ⋮│
│    公共目录      14:17        │     │    公共目录      14:17        │
│    画布打开|下载|复制路径      │     └──────────────────────────────┘
│    挂载文件                   │              │ 点击 ⋮
└──────────────────────────────┘              ▼
                                       ┌────────────────────┐
                                       │ 👁️ 画布打开预览     │
                                       │ ⬇️ 下载文件         │
                                       │ 📋 复制路径         │
                                       │ 📎 挂载文件到会话   │
                                       ├────────────────────┤
                                       │ ✏️ 重命名           │
                                       │ 🗑️ 删除             │
                                       └────────────────────┘
```

- ⋮ 位于「大小/修改时间」列右端、与两行文字垂直居中，`28×28` 视觉、`40×40` 触控热区（用 `p-2 -m-1` 扩大可点区域），`aria-haspopup="menu"`、`aria-expanded`、`title="更多操作"`。
- 只有 `selectedItem === item` 且非多选模式时显示；点击 `@click.stop`，不触发行选中/双击逻辑。
- 菜单定位：锚定 ⋮ 的 `getBoundingClientRect()` 右下角；右键时锚定鼠标坐标。复用现有 `adjustContextMenuPosition` 的测量与翻转逻辑（迁入子组件）。
- 关闭时机：Esc、点击外部（`mousedown`）、列表滚动、切换目录、选中其它行、执行完动作、再次点 ⋮（toggle）。
- 键盘：打开后焦点落在首项，↑↓ 移动，Enter 执行，Esc 关闭。

## 菜单项矩阵（单一来源）

由 `buildWorkspaceActionEntries(ctx)` 生成，`ctx` 为纯数据（不含组件函数）：

| 场景 | 条目（按顺序） |
| --- | --- |
| 普通文件 | 画布打开预览（不可预览 → `disabled` + 提示「该格式不支持预览」）/ 下载文件 / 复制路径 / 挂载文件到会话 ─ 重命名 / 删除 |
| 普通文件夹 | 打开目录 / 复制路径 / 挂载文件夹到会话 ─ 重命名 / 删除 |
| 公共只读 | 表头「🔒 公共只读资源」+ 画布打开预览 / 下载文件 / 挂载文件到会话 / 复制路径 |
| 公共目录（管理员） | 表头「🗂️ 公共目录（管理员）」+ 打开目录 / 画布打开预览 / 下载文件 / 挂载 / 复制路径 ─ 重命名 / 永久删除（`isSystemProtected` 时不含后两项） |
| 回收站条目 | 恢复 / 永久删除 / 画布打开预览 / 复制路径 |
| 回收站根 | 打开 / 清空回收站 |
| 空白处右键 | 新建文件 / 新建文件夹 / 上传到此目录（`canCreateHere` 为真时） |

`─` 表示 `separatorBefore: true` 的分隔线；`danger: true` 用红色。

## 文件结构与职责

| 文件 | 职责 |
| --- | --- |
| `frontend/src/utils/workspaceActionEntries.ts`（新增） | **零依赖**类型 + `buildWorkspaceActionEntries(ctx)` 纯函数：场景 → 条目列表。可被 Node 直接加载做行为断言。 |
| `frontend/src/components/embed/WorkspaceActionMenu.vue`（新增） | 只负责「怎么显示」：Teleport、定位/翻转/夹取、Esc/点外关闭、键盘导航、`role="menu"`、`separator`/`danger`/`disabled` 样式。不关心业务场景。Props：`entries`、`anchor:{x,y}|null`、`open`；Emits：`select(key)`、`close()`。 |
| `WorkspaceBrowserDrawer.vue`（修改） | ① 行内 4 个文字按钮 → 单个 ⋮；② 新增 `rowActionMenu` 状态（item + anchor）；③ 原 `contextMenu` 改为「菜单数据 + 锚点」，右键与 ⋮ 共用 `<WorkspaceActionMenu>`；④ 组件内提供 `runWorkspaceAction(key, item)` 把 key 映射到既有函数。 |
| `tests/frontend/workspace_action_entries.mjs`（新增） | Node 行为测试：6 类场景的 key 序列、禁用态、危险项、分隔位置、系统保护目录、公共只读、回收站。 |
| `tests/frontend/test_workspace_row_action_menu_contract.py`（新增） | 契约：行内平铺按钮已删除、⋮ 唯一且条件正确、两处入口调用同一构造器、子组件 Teleport/role/Esc、移动端热区。 |

## 错误处理与边界

- `entries` 为空时菜单不渲染（理论上不会发生，防御性处理）。
- 菜单打开期间列表刷新（`loading`）→ 直接关闭，避免菜单指向已消失的行。
- 动作抛错：沿用各动作函数内部的 `showToast`，菜单先关再执行。
- 视口高度不足以容纳全部条目 → 菜单内部滚动（`maxHeight` 夹取，`overflow-y-auto`），与现有右键菜单一致。
- 触屏点击 ⋮ 后立刻触发 `touchend` 的双击判定？⋮ 上 `@click.stop` + 行内 `@dblclick` 只挂在行容器，按钮点击不会冒泡到行（`.stop`），不会误开文件。

## 测试策略

1. **行为（Node，先 RED）**：`workspace_action_entries.mjs` 直接 import 纯函数，断言：
   - 普通文件 6 项顺序与 `separatorBefore` 位置；
   - `.zip` 的 `preview` 为 `disabled`、`download` 可用（延续上一轮下载修复）；
   - 普通目录无 `preview/download`，有 `open-dir`；
   - 公共只读无 `rename/delete` 且首项为表头标记（用 `header` 字段表达）；
   - `isSystemProtected` 时管理员菜单无 `rename/purge`；
   - 回收站条目/根、空白处三种场景的条目。
2. **契约（pytest 源码形状，先 RED）**：行内不再出现四个文字按钮的模板片段；⋮ 只在 `shouldShowRowActions` 下渲染；`WorkspaceBrowserDrawer` 对行内与右键都使用 `buildWorkspaceActionEntries` + `<WorkspaceActionMenu>`；子组件含 `Teleport`、`role="menu"`、`Escape`、`min-h-` 热区类。
3. **回归**：`pytest --confcutdir=tests/frontend tests/frontend` 全量；`vue-tsc -b --force`（抬堆）改动文件零新增错误；手工核对右键菜单六类场景与改动前逐项一致。

## 验收标准

- [ ] 选中行只出现一个 ⋮，行高与未选中行一致（不再换行）。
- [ ] ⋮ 与右键点击弹出一模一样的竖向菜单（同一份条目、同样顺序、同样禁用态）。
- [ ] 普通文件菜单含 6 项（含重命名/删除）；公共只读、公共目录管理员、回收站、回收站根、空白处分别符合上表。
- [ ] `.zip` 的「画布打开预览」置灰且不可点，下载可用。
- [ ] Esc / 点外部 / 滚动 / 切目录 / 选其它行都会关闭菜单；键盘 ↑↓ + Enter 可用。
- [ ] 移动端 ⋮ 常显、40px 热区、菜单项 ≥40px 高。
- [ ] 行为测试与契约测试先 RED 后 GREEN；`tests/frontend` 全量通过。
