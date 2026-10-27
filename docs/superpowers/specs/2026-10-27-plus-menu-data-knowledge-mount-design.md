# 加号菜单「数据&知识库」一键挂载（设计）

- 日期：2026-10-27
- 状态：待评审
- 涉及模块：输入框加号菜单、知识库挂载、元数据集挂载、会话资源范围

## 1. 背景

当前要把数据集或知识库挂到本轮提问，必须分别打开两个抽屉：

- 知识库面板（`KnowledgePortalDrawer`）里逐个拨开关 → 写 `knowledge_base` chip；
- 数据门户（`DatasetPortalDrawer`）里勾「📊 数据集」→ 写 `metadata_dataset` chip。

两个抽屉都占满右侧版面、会遮挡对话，且一次挂载要来回开合。加号菜单里已经有「技能中心 / MCP 工具 / 专家中心」三个可展开浮层，挂载动作放在这里最省事。

## 2. 目标

1. 加号菜单「选择记忆记录」之后新增「数据&知识库」入口，展开后是「数据集 / 知识库」两个 tab。
2. tab 内直接勾选/取消，等同两个面板里的挂载动作：写/删输入框附件 chip，本轮提问即生效。
3. 勾选后浮层保持打开，支持连续多选；已挂载项高亮，tab 上显示「已挂载 / 总数」。
4. 列表数据与两个面板同源；会话设置了「项目会话资源范围」时只列范围内的资源。

## 3. 非目标

- 不改「项目会话资源范围」本身（`ResourceScopeModal` 的会话级锁定语义不动）。
- 不做「选到表级」（本轮仍是元数据集粒度，表选择留在数据门户）。
- 不改发送链、不改后端接口、不加数据库字段。
- 不在本次把「上传本地文件 / 浏览工作空间」搬进浮层。

## 4. 交互

```
+ 菜单
  打开数据门户
  打开知识库中心
  浏览工作空间
  上传本地文件
  选择记忆记录
  数据&知识库        >   ← 新增（任一 tab 可用时才显示）
  技能中心           >
  MCP 工具           >
  专家中心           >
```

- 桌面端：贴菜单右侧的 flyout（`left-full`，与菜单上下对齐，同技能 / MCP）。
- 移动端：底部抽屉（同技能 / MCP 的 `Teleport` 抽屉）。
- 浮层内：顶部搜索框 → 两个 tab（带计数）→ 列表（名称 + 描述 + 勾选框）→ 底部提示文案。
- 点击条目行即切换挂载；**不关闭浮层**。Esc / 点击外部 / 再点加号关闭。
- 智能体不支持数据查询时「数据集」tab 置灰并说明；不支持知识库时同理。两个都不支持则整个菜单项不显示。

## 5. 数据源与字段

| tab | 接口 | 过滤 | 展示字段 |
|---|---|---|---|
| 数据集 | `GET /api/portal/metadata/datasets/accessible` | `status` 为空 / `1` / `'1'` / `active` | `display_name \|\| name \|\| dataset_name`、`description \|\| remark \|\| notes` |
| 知识库 | `GET /api/portal/ragflow/datasets?page=N&page_size=1000&include_missing=false`，按返回的 `total` 续拉到齐 | `code === 0` 取 `data.datasets \|\| data`，`status` 为空 / `active` / `1` | `name \|\| display_name \|\| dataset_name`、`description \|\| summary \|\| notes` |

两个请求按 tab 懒加载并缓存；列表接口失败时 tab 内显示错误文案 + 「重试」，不弹 toast。

**不依赖任何面板已加载的 state**：浮层自己发请求，用户从未打开过知识库面板 / 数据门户也必须能看到自己有权限的全部资源。两个接口都在服务端按当前用户权限过滤（数据集走 `ResourcePermission` 用户/角色授权 + 仅启用；知识库走 `get_knowledge_base_access().accessible_ids`，含私有仅创建人、部门级、公共级与显式授权），非管理员拿不到全量。

知识库列表**不能**沿用面板的 `page_size: 100` 窗口：RAGFlow `/datasets` 里混着成百上千个 `meta-*` 元数据集，只取第一页会在过滤后变空（后端接口注释专门警告过，项目口径见「KB Permission Guard」统一 `page_size: 1000`）。浮层从 1000 起步、按 `total` 续拉。

## 6. 附件 chip 契约（唯一事实源）

新增 `frontend/src/utils/sessionMountChips.ts`：

```ts
readKnowledgeBaseIds(files): string[]
toggleKnowledgeBaseChip(files, id): any[]        // 单个汇总 chip，filename 为「已选择 N 个知识库」
readMetadataDatasetIds(files): string[]
toggleMetadataDatasetChip(files, id, name?): any[] // 每个数据集一个 chip
```

保持现有格式不变：

- `{ type: 'knowledge_base', url: 'id1,id2', filename: '已选择 N 个知识库', size: 0, ext: 'knowledge_base' }`
- `{ type: 'metadata_dataset', url: '<id>', filename: '<数据集名>', size: 0, ext: 'metadata_dataset' }`

`useKnowledgePortal.toggleDatasetActive`、`useDatasetMount.toggleMetadataDatasetActive` 改为复用这批纯函数，`ChatInput` 也调用同一份，避免三处各写一遍导致发送侧解析（`useChatAttachments`）对不上。

## 7. 组件边界

| 单元 | 职责 | 依赖 |
|---|---|---|
| `utils/sessionMountChips.ts` | chip 读写纯函数 | 无 |
| `components/embed/DataKnowledgeCascadeMenu.vue` | 拉两个列表、搜索、tab、展示已挂载态，只 emit 选择事件 | axios、`sessionMountChips` 的类型 |
| `ChatInput.vue` | 菜单项、开关状态、调用纯函数写 chip、桌面/移动端容器 | 上面两者 |
| `EmbedChat.vue` / `AgentDebug.vue` | 按会话资源范围传可选过滤 props | 现有 `resourceScope` 计算 |

`DataKnowledgeCascadeMenu` 的 props / emits：

- props：`activeDatasetIds: string[]`、`activeKnowledgeBaseIds: string[]`、`allowedDatasetIds?: string[] | null`、`allowedKnowledgeBaseIds?: string[] | null`（`null` = 不过滤）、`datasetDisabled?: boolean`、`knowledgeDisabled?: boolean`、`compact/fullWidth/fillHeight`。
- emits：`toggle-dataset(id, name)`、`toggle-knowledge-base(id)`。

## 8. 范围过滤口径

与两个面板一致（`EmbedChat.vue` 现有计算）：

- 仅当 `resourceScope.project_name` 存在且对应分组非空时过滤；
- 数据集：`sessionMountedMetadataDatasetIds`；
- 知识库：`scopedKnowledgeDatasets.map(item => String(item.id))`。

不传 props 时不过滤（`AgentDebug.vue` 可按需接入）。

## 9. 错误与空态

- 加载中：骨架 + 「加载中…」。
- 空列表：数据集提示去「数据门户」创建/申请、知识库提示去「知识库中心」创建，带链接。
- 接口失败：红色/灰色提示 + 「重试」按钮。
- 已挂载但不在返回列表里的 id（库被删、被撤权）：不静默丢弃——tab 底部计数含它，条目位置给一条「N 项已失效，点击移除」提示。

## 10. 测试

新增 `tests/frontend/test_plus_menu_data_knowledge_mount_contract.py`：

1. 菜单项在「选择记忆记录」之后、「技能中心」之前，带可展开箭头。
2. 两个 tab 文案为「数据集」「知识库」，各带计数。
3. 两个接口路径与关键参数正确，且按 tab 懒加载。
4. `sessionMountChips` 生成的 chip `type` / `url` / `filename` 与现有面板一致（汇总 chip 文案、单项 chip 数据集名）。
5. 能力不足时对应 tab 置灰；两者都不可用时菜单项不渲染。
6. 勾选后不关闭浮层（不 emit `select-knowledge-base`、不置 `showPlusMenu=false`）。
7. 会话资源范围 props 传入时列表按其过滤。

回归：`pytest --confcutdir=tests/frontend tests/frontend`、`vue-tsc --noEmit`。不跑 `./dev.sh`、不提交 git，收尾补 `tests/CHECKLIST.md`。

## 11. 风险

- **chip 格式漂移**：三处写入必须共用纯函数，契约测试锁定字符串格式。
- **范围过滤不一致**：若只在前端过滤而发送侧仍接受范围外 id，会出现「挂了但检索不到」；本次与面板保持一致（面板同样只做前端过滤），行为不变。
- **菜单高度**：新增一项后桌面加号菜单变高，flyout 用 `top-0 bottom-0` 跟随，需要窄屏（<640px）抽屉回归一次。
