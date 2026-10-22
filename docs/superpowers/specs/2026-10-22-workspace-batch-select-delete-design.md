# 工作区批量模式：全选 + 批量删除（二次确认）

- 日期：2026-10-22
- 涉及：`frontend/src/components/embed/WorkspaceBrowserDrawer.vue`、`frontend/src/utils/workspaceBatchSelection.ts`（新增）、`tests/frontend/test_workspace_batch_actions_contract.py`（新增）、`tests/frontend/workspace_batch_selection.mjs`（新增）

## 背景

工作区浏览器的多选模式此前只有「批量挂载」：进入多选后必须逐条勾选，文件多时只能手点；也没有批量删除，用户只能一条一条走 ⋮ 菜单删除。截图里红圈标出的正是表头复选框列（此前只是一个占位、不可点）和右下角的批量操作按钮。

## 目标

1. 多选模式支持**全选 / 取消全选**，并正确显示**半选态**。
2. 新增**批量删除**，必须**二次确认**后才执行。
3. 全选的生效范围与「批量挂载」取数一致，两个批量动作操作的是同一批条目。
4. 不新增后端接口：复用既有 `POST /api/v1/chat/fs/delete-entry`，单条语义不变。

## 非目标

- 不改单条删除、回收站恢复/永久删除/清空回收站的既有流程。
- 不改搜索、排序、分页、类型筛选、网格视图的取数与布局。
- 不引入「撤销删除」：删除仍进入回收站，恢复走回收站。

## 交互设计

### 全选

- 列表视图：表头第一列在 `multiSelectMode` 时变成复选框；`checked` 取 `batchSelectAllState === 'all'`，`indeterminate` 取 `'partial'`，无条目时禁用，`aria-label` 随状态切换「全选当前列表 / 取消全选」。
- 文件夹（网格）视图：没有表头，使用右下角操作条里的「全选 / 取消全选」。
- 右下角多选操作条在 `multiSelectMode` 期间常驻（否则没勾任何项时无从全选）：
  `全选 | 批量删除 (n) | 批量挂载 (n)`，最右侧仍是圆形的「取消多选」。
- 点击语义：`none`/`partial` → 补齐全选；`all` → 取消当前列表的全选。列表外的历史勾选既不参与判定，也不会被顺手清掉。
- 生效范围为当前列表（搜索词 + 类型筛选 + 排序后）的全部条目，包含「加载更多」尚未渲染的项；计数显示真实数量。

### 批量删除

- 入口：操作条上的红色「批量删除 (n)」，`n` 为**可删除**的选中项数量；为 0 时禁用并用 title 说明原因。
- 二次确认：复用仓库通用 `ConfirmModal`（`z-13000`，Teleport 到 body），标题「移入回收站？」，正文按可删项拆分说明，`details` 列出待删项名称（默认 5 个、可展开）。
- 只读/受保护项（公共只读目录、系统保护目录 `docs/skills/branding/agent_workspaces`、用户根目录本身）不参与删除，计入「另有 N 项只读或受保护，将自动跳过」。
- 管理员删除公共目录内容时后端直接永久删除，此时标题改为「确认删除选中项？」并明确「将被永久删除且不可恢复」。
- 回收站视图不提供批量删除（回收站走「清空回收站」）；该视图下操作条只保留全选与挂载。

### 执行与失败处理

- 逐项调用 `deleteWorkspaceEntry(path)`，不新增批量接口；单项失败不中断其余条目。
- 确认框在提交期间显示 `正在删除 i/n 项…`，按钮进入 loading 禁用态，重复点击无效。
- 成功项移出勾选并刷新当前目录（保留搜索条件）；失败项**保持勾选**，可直接重试，并以第一条错误提示失败数量。
- 成功后按「已移入回收站 / 已永久删除 / 只读已跳过」分段汇总 toast。

### 选择失效

勾选按 `path` 记录，以下场景清空，避免「看不见却被操作」：

- 切换目录（`fetchDirectory` 的目标路径与当前路径不同）；
- 修改搜索词、切换类型筛选；
- 关闭/重开抽屉、退出多选模式（`clearMultiSelect`）。

批量动作与计数只取「当前列表内」的勾选（`batchSelectedItems`），跨目录残留路径不会虚增数量。

## 实现要点

- 半选态、全选范围、只读跳过这类分支抽到 `frontend/src/utils/workspaceBatchSelection.ts`：
  - `selectAllState(selectablePaths, selectedPaths)` → `'none' | 'partial' | 'all'`
  - `toggleSelectAllPaths(...)` → 新 `Set`，只增删当前列表内路径
  - `planBatchDelete(items, canDelete)` → `{ deletable, skipped }`
- 模块零框架依赖，可被 `node --experimental-strip-types` 直接加载，行为测试见 `tests/frontend/workspace_batch_selection.mjs`。
- 组件侧只负责接线：`batchSelectablePaths` / `batchSelectAllState` / `batchSelectedItems` / `batchDeletePlan` / `batchDeletePermanentCount` / `batchDeleteDetails` / `batchDeleteMessage`。

## 测试

- `tests/frontend/workspace_batch_selection.mjs`：空列表、none/partial/all 判定、半选补全、取消全选保留列表外勾选、只读跳过、全不可删。
- `tests/frontend/test_workspace_batch_actions_contract.py`：模块无框架依赖；表头全选框与半选绑定；操作条三按钮；`ConfirmModal` 二次确认（title/message/details/loading/confirm）；入口不直接调用删除；失败隔离与失败项保留；回收站隐藏；范围变化清空勾选。
- 回归：`pytest tests/frontend --confcutdir=tests/frontend`（2766 passed）、`vue-tsc --noEmit`。
