# 资源广场：知识库与数据集的发现、申请、审批（设计）

- 日期：2026-10-27
- 状态：待评审
- 交互原型：[2026-10-27-resource-square.html](../../mockups/2026-10-27-resource-square.html)
- 涉及模块：个人工作台、知识库、元数据/数据集、用户管理、个人中心、站内通知

## 1. 背景

知识库的访问申请链路已经完整（发现 → 申请 → 审批 → 落 `ResourcePermission`），但入口藏在「知识库管理」页，需要 `menu:knowledge_management`。普通用户没有这个菜单，等于没有申请入口，只能线下找人。

数据集（`meta_datasets`）更彻底：只有按用户/角色的显式授权，没有任何申请入口，也没有「我为什么看不到这个数据集」的解释。

「我的工作台」不带权限码、所有登录用户可见，是放「找资源 + 申请」最自然的位置。

## 2. 目标

1. 工作台新增 **资源广场** tab：公共级/部门级知识库 + 全部启用数据集的概览卡片，支持搜索、筛选、申请。
2. 数据集首次支持申请；知识库与数据集的申请 **统一在用户管理页审批**，审批人统一为管理员。
3. 新增两个 **只读概览页**（知识库 / 数据集），让「已有权限」的用户真的能看到内容，而不是只有一句「已有权限」。
4. 个人中心「我的申请」整合两类资源，带类型标识。
5. 保留知识库管理页原有的「发现」入口，避免改动老用户习惯。
6. 工作台资源条新增「我的知识库」「我的数据集」两个入口卡，点击进入资源广场对应段。

## 3. 非目标

- 不给 `meta_datasets` 增加归属部门/归属人/可见范围字段（本期数据集审批统一走管理员）。
- 不做按角色申请、批量审批、多级审批流。
- 只读页不提供字段明细、示例数据、YAML、原文预览与下载。
- 不改知识库现有 private / team / public 三档可见性模型。

## 4. 术语与取值

| 概念 | 存储取值 | 说明 |
|---|---|---|
| `resource_type = dataset` | 知识库 | 与 `ResourcePermission.resource_type` 一致，`resource_id` 为 RAGFlow Dataset ID |
| `resource_type = metadata` | 数据集 | 与 `ResourcePermission.resource_type` 一致，`resource_id` 为 `meta_datasets.id` 的字符串形式 |

申请与授权共用同一套取值，审批通过时直接写对应的 `ResourcePermission`，不引入第二套权限事实。

## 5. 信息架构

### 5.1 路由

| 路径 | 页面 | 权限 |
|---|---|---|
| `/dashboard/workbench` | 我的工作台（默认 tab：工作台；`?tab=square` 打开资源广场） | 无（所有登录用户） |
| `/dashboard/resource-square/knowledge/:datasetId` | 知识库只读概览 | 登录 + 该库可访问 |
| `/dashboard/resource-square/datasets/:id` | 数据集只读概览 | 登录 + 该数据集已授权 |
| `/dashboard/users?review=1` | 用户管理 · 审批弹窗 | `menu:system:users`（管理员） |

### 5.2 工作台 tab

```
我的工作台
[ 工作台 ] [ 资源广场 ]
```

- 默认 `?tab=workbench`，保持现有内容一行不动。
- 切到资源广场时写 `?tab=square`，刷新/分享可复原。
- 资源广场内的只读页是独立路由，返回时回到 `?tab=square`。

### 5.3 资源广场结构

```
搜索框  [全部|知识库|数据集]  [全部范围|公共级|部门级]  [全部状态|可申请|审批中|已授权]
────────────────────────────────
▸ 知识库（N）   公共级与部门级；私有库不出现，也不接受申请
  卡片网格（每段后端分页，12 条/页）
▸ 数据集（N）   全部启用数据集；审批通过后可进入只读概览
  卡片网格
```

顶部统计条：可申请 / 审批中 / 已有权限 三个数（全量口径，不随筛选变化）。

## 6. 交互规则

### 6.1 卡片状态机

| 状态 | 展示 | 可执行动作 |
|---|---|---|
| 可申请 | 「申请访问」主按钮 | 打开理由弹窗 → 提交 |
| 审批中 | 琥珀色「审批中 · 时间」 | 去个人中心撤回；卡片不重复申请 |
| 已授权 | 绿色「已有权限」，卡片淡绿底 + 绿边 | 点击卡片进只读概览 |
| 已驳回 | 红色「已驳回」+「重新申请」 | 重新申请；hover 显示驳回意见 |

同一人对同一资源只允许一条 `pending`（沿用现有服务规则）。驳回后重新申请会生成新记录，旧记录保留。

### 6.2 允许进入只读概览的条件

- 知识库：`has_access = true`（管理员恒为 true）。
- 数据集：管理员，或存在启用的 `ResourcePermission("metadata", id, user)`。

未授权时卡片不提供跳转，只给申请按钮。直接访问只读 URL 时后端 403，前端显示「暂无访问权限，可回到资源广场申请」。

### 6.3 搜索与筛选

- 搜索走后端 `keyword`：知识库匹配名称/描述/归属文本/标签；数据集匹配 `name` / `display_name` / `description` / 标签。
- 范围筛选只对知识库生效（数据集没有可见范围概念）。
- 状态筛选在前后端都支持；前端切换筛选不再请求，翻页才请求。
- 筛选条件不持久化，刷新后回到默认。

### 6.4 空态与异常

- 某段无匹配：段内虚线空态「没有匹配的知识库/数据集，试试换个关键词或清空筛选」。
- 数据集段被系统开关关闭：显示「管理员未开放数据集目录」。
- 知识库引擎不可用：返回上次缓存快照并标记 `stale`，页面顶部提示「知识库数据来自缓存，可能不是最新」，不整段消失。
- 数据集查询失败：该段显示重试按钮，知识库段不受影响。

### 6.5 管理员视角

管理员对所有知识库/数据集都有权限，资源广场对其主要是「目录浏览」：

- 所有卡片显示「已有权限」，不出现申请按钮。
- 顶部统计中「可申请」恒为 0。

### 6.6 工作台资源卡

资源条由 7 项变 9 项，新增两项排在最前：`我的知识库`、`我的数据集`（单位都是「个」）。

| 项 | 口径 |
|---|---|
| 我的知识库 | 非管理员：`get_knowledge_base_access().accessible_ids` 长度；管理员：`knowledge_base_metadata` 中 `status != 'deleted'` 的总数（含私有库，管理员确实可访问） |
| 我的数据集 | `list_accessible_dataset_options(status=1)` 的长度；管理员为全部启用数据集 |

- 数量为 0 时显示 0 并置 `empty`，不显示「暂无」文案，保持与其他卡一致。
- 统计失败置 `error`，卡片显示 `--`，不影响其他资源卡与下方三个板块。
- 点击：`knowledge` → `/dashboard/workbench?tab=square&section=knowledge`，`datasets` → `...&section=datasets`；广场面板读取 `section` 把对应段滚入视野。
- 布局：资源条 `xl` 断点由 6 列改 5 列，9 项排成 5+4，避免第二行只剩一张。
- Embed 欢迎页不展示这两项（`EMBED_WELCOME_HIDDEN_RESOURCE_KEYS` 追加），iframe 内跳工作台会脱离嵌入上下文。

## 7. 权限与审批

### 7.1 谁能看到什么

- 知识库：`visibility in (public, team)` 全部可见（不含 private，也不含引擎侧失联的库、平台内置库）。
- 数据集：`status = 1` 全部可见；受系统配置 `resource_square_show_datasets` 控制，默认开。

### 7.2 申请前置校验（后端）

- `resource_type` 必须是 `dataset` / `metadata`。
- 资源必须存在：知识库元数据未删除；数据集 `status = 1`。
- 知识库 `visibility != private`；数据集无可见范围限制。
- 当前用户尚未拥有权限（管理员直接拒绝申请，提示已有权限）。
- 不存在同资源同用户的 `pending` 记录。

### 7.3 审批人

**统一为管理员**（`is_admin_user(roles)`，即 `admin` 角色）。

- 知识库不再要求「库归属人」审批，`_require_reviewer` 简化为管理员判定。
- 数据集同样只允许管理员审批。
- 待审批列表对非管理员返回空（不再按 `writable_ids` 过滤知识库申请）。

### 7.4 审批通过

1. 写 `ResourcePermission`：
   - 知识库：`resource_type="dataset"`, `resource_id=ragflow_dataset_id`。
   - 数据集：`resource_type="metadata"`, `resource_id=str(dataset.id)`。
   - 已存在被禁用的记录则 `enabled=True`，不插重复行。
2. 清两处缓存（缺一不可）：
   - `PermissionService.invalidate_cached_permissions_for_users([applicant_id])`
   - `AgentConfigProvider.invalidate_dataset_menu_cache(user_id=applicant_id)`（对话侧数据集菜单缓存）
3. 审批前二次校验资源状态：知识库在引擎侧失联、数据集已停用 → 拒绝通过并提示「资源已不可用」。
4. 写审批字段（审批人、时间、意见），发通知。
5. 写业务审计：知识库走 `KnowledgeAuditService`（`datasets:grant`），数据集走 `MetadataAuditService`（`metadata:grant`）。

### 7.5 驳回与撤回

- 驳回：仅写状态与意见，发通知，不动权限。
- 撤回：仅申请人本人、仅 `pending`，状态置 `cancelled`，不通知。
- 管理员不能审批自己的申请（沿用）。

## 8. 数据模型与迁移

### 8.1 申请表扩展

复用 `ai_agent_knowledge_access_requests`，新增一列区分资源类型。表名沿用历史命名（改名会牵动模型、索引与线上数据），在模型与迁移注释里说明它已扩展为「资源访问申请」。

- `resource_type VARCHAR(20) NOT NULL DEFAULT 'dataset'`：`dataset` = 知识库，`metadata` = 数据集。
- 新增索引 `(resource_type, status)`，支撑统一待审批查询。
- `dataset_id` 列继续存资源 ID：知识库存 RAGFlow Dataset ID，数据集存 `str(meta_datasets.id)`。
- `dataset_name` 列继续存资源名称快照：知识库存库名，数据集存 `display_name`（无则 `name`），保证审批列表与「我的申请」在资源改名后仍能显示提交时的名称。

### 8.2 数据集审计表（新增）

新建 `ai_agent_metadata_audit_logs`，字段与 `ai_agent_knowledge_audit_logs` 对齐：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT 自增 | 主键 |
| `metadata_dataset_id` | BIGINT | `meta_datasets.id` |
| `action` | VARCHAR(64) | 动作码，见 §10.3 |
| `user_id` / `user_name` | BIGINT / VARCHAR(64) | 操作人 |
| `status_code` | INT | 结果状态 |
| `detail` | JSON / JSONB | 目标用户、申请 ID、审批意见等 |
| `client_ip` | VARCHAR(50) | 客户端 IP |
| `error_message` | TEXT | 失败原因 |
| `created_at` | DATETIME / TIMESTAMP | 时间 |

索引：`(metadata_dataset_id, created_at)`、`(action, created_at)`。

迁移文件（两件事放一个文件，减少执行次数）：

- MySQL：`db-prod/V192-resource_square_and_metadata_audit.sql`
- PostgreSQL：`db-prod-pg/V92-resource_square_and_metadata_audit.sql`

存量行默认 `resource_type='dataset'`，无需回填。建表用 `IF NOT EXISTS`，加列失败由 `apply_sql.py` 跳过 1060/1061，可重复执行。

## 9. 后端接口

### 9.1 资源广场

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/portal/resource-square/knowledge` | 登录 | 分页返回 public/team 库 + 权限与申请状态 |
| GET | `/api/portal/resource-square/datasets` | 登录 | 分页返回启用数据集 + 权限与申请状态 |

`GET /resource-square/knowledge` 参数：`keyword`、`visibility=all|public|team`、`status=all|apply|pending|has`、`page`、`size`。
返回项字段：`dataset_id`、`name`、`description`、`visibility`、`owner`、`owner_dept_id`、`owner_user_name`、`doc_count`、`chunk_count`、`updated_at`、`has_access`、`request_status`、`request_id`。整体带 `stale` 标记。

`GET /resource-square/datasets` 参数：`keyword`、`status=all|apply|pending|has`、`page`、`size`。
返回项字段：`id`、`name`、`display_name`、`description`、`data_source`、`tags`、`quality_score`、`table_count`、`metric_count`、`updated_at`、`has_access`、`request_status`、`request_id`；整体带 `enabled`（系统开关）。

数据集统计用一条聚合查询（`group by dataset_id` 计表数与指标数），不做逐条查询。

### 9.2 申请

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/portal/access-requests` | body：`{resource_type, resource_id, reason}`，提交申请 |
| POST | `/api/portal/access-requests/{id}/cancel` | 撤回本人 pending |

### 9.3 统一审批

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portal/access-requests/pending?resource_type=all|dataset|metadata` | 待审批（仅管理员，非管理员返回空） |
| GET | `/api/portal/access-requests/reviews?status=&resource_type=` | 已处理记录 |
| GET | `/api/portal/access-requests/mine` | 我的申请（两类混排，带 `resource_type`） |
| POST | `/api/portal/access-requests/{id}/approve` | body：`{comment}` |
| POST | `/api/portal/access-requests/{id}/reject` | body：`{comment}` |

列表项统一结构：`id`、`resource_type`、`resource_id`、`resource_name`、`applicant_user_id`、`applicant_user_name`、`reason`、`status`、`reviewer_user_name`、`review_comment`、`created_at`、`reviewed_at`，并带类型相关的审批依据：

- 知识库：`visibility`、`owner`、`doc_count`、`chunk_count`、`resource_exists`。
- 数据集：`data_source`、`table_count`、`metric_count`、`quality_score`、`resource_enabled`。

`resource_exists=false` 或 `resource_enabled=false` 时前端标注并禁用「通过」。

### 9.4 只读概览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portal/resource-square/knowledge/{dataset_id}` | 元数据 + 文档目录 + 统计 |
| GET | `/api/portal/resource-square/datasets/{id}` | 基本信息 + 表 + 指标 + 关系计数 |

知识库返回：`dataset_id`、`name`、`description`、`visibility`、`owner`、`tags`、`doc_count`、`chunk_count`、`updated_at`、`search_count_30d`、`documents[]`。每个文档只给 `id`、`name`、`file_type`、`size`、`chunk_count`、`run_status`、`updated_at`。

- `documents` 默认返回前 100 条，支持 `page` / `size` 翻页；排序按更新时间倒序。
- `search_count_30d` 来自 `knowledge_base_metrics.search_count` 近 30 天求和，取不到时返回 `null`，前端显示「—」。

数据集返回：`id`、`name`、`display_name`、`description`、`data_source`、`tags`、`quality_score`、`updated_at`、`tables[]`（`physical_name`、`term`、`field_count`、`description`）、`metrics[]`（`name`、`display_name`、`description`、`unit`）、`relationship_count`。

权限：登录 + 知识库可访问 / 数据集已授权 / 管理员。

### 9.5 旧接口兼容

- `/ragflow/datasets/discover`、`/ragflow/access-requests/*` 保留，内部委托新服务或复用新查询，响应结构不变。
- 「发现」弹窗继续只列未授权库；广场列全量并标状态。

## 10. 服务与副作用

### 10.1 服务分层

- `ResourceAccessRequestService`（新增，`app/services/resource_access_request_service.py`）：统一处理两类资源的申请、审批、撤回、授权。
- 原 `KnowledgeAccessRequestService` 保留为薄封装，内部调用新服务，旧引用不用改。

### 10.2 通知

| 时机 | 收件人 | category | 点击去向 |
|---|---|---|---|
| 提交申请 | 所有管理员（排除申请人本人） | `resource_access` | `/dashboard/users?review=1` |
| 审批通过 | 申请人 | `resource_access_result` | `/dashboard/personal?tab=permissions&subtab=requests` |
| 审批驳回 | 申请人 | `resource_access_result` | 同上 |

知识库保留 `knowledge_access` / `knowledge_access_result` 两个旧 category 的兼容映射，前端铃铛同时识别新老 category，避免历史消息点不动。

通知失败只记日志，不影响审批事务。

### 10.3 审计

**知识库**：沿用 `KnowledgeAuditService`（`datasets:request_access` / `datasets:grant`）。

**数据集**：新增独立审计表 `ai_agent_metadata_audit_logs`，新增 `MetadataAuditService`（实现照 `KnowledgeAuditService`：独立 session、失败只记日志不抛）。

为什么不用「申请表 + 访问日志」代替：

- 访问日志每个请求一条，默认只查 90 天且会被清理，`resource_id` 埋在 `request_params` JSON 里，按资源查不动；
- `ResourcePermission` 只有当前状态、没有历史，撤销后就查不到「谁在什么时候给的权限」；
- 申请表只覆盖「申请 → 审批」这一条路径，管理员手工授权/撤销不经过它。

本期写入动作：

| 动作码 | 触发点 | `detail` 关键内容 |
|---|---|---|
| `metadata:request_access` | 提交申请 | `request_id`、`reason` |
| `metadata:grant` | 审批通过 | `request_id`、`applicant_user_id`、`applicant_user_name` |
| `metadata:reject` | 审批驳回 | `request_id`、`comment` |
| `metadata:cancel` | 申请人撤回 | `request_id` |
| `metadata:grant` | 手工授权（`POST /datasets/{id}/permissions`） | `target_type`、`target_ids` |
| `metadata:revoke` | 手工撤销（`DELETE /datasets/{id}/permissions`） | `target_type`、`target_id` |

本期只写入，不新增审计展示页；后续可在数据集详情加「操作日志」tab，按 `metadata_dataset_id` 查询即可。

### 10.4 配置开关

新增配置项 `resource_square_show_datasets`（布尔，默认 `true`）：

- `config_service.py`：常量 + 布尔校验分支。
- `SystemConfig.vue`：说明文案、布尔开关渲染列表、分组注册三处。
- 广场数据集接口读取该开关；关闭时返回空列表 + `enabled=false`。

## 11. 前端改动

### 11.1 新增

| 文件 | 职责 |
|---|---|
| `components/resource-square/ResourceSquarePanel.vue` | 资源广场主体：搜索、筛选、两段卡片、分页、统计条 |
| `components/resource-square/ResourceSquareCard.vue` | 统一卡片：图标、徽标、描述、指标、状态位 |
| `components/resource-square/ApplyAccessModal.vue` | 申请理由弹窗（含常用理由快捷项） |
| `views/ResourceSquareKnowledge.vue` | 知识库只读概览 |
| `views/ResourceSquareDataset.vue` | 数据集只读概览 |

### 11.2 改造

| 文件 | 改动 |
|---|---|
| `views/PersonalWorkbench.vue` | 顶部加 tab（工作台/资源广场）、`?tab=square` 同步、资源广场挂载面板；资源卡 `knowledge` / `datasets` 点击跳广场对应段 |
| `components/workbench/WorkbenchPersonalResources.vue` | 新增两个图标分支（书本 / 数据库）、配色（蓝 / 靛）、`xl` 断点 6 列改 5 列 |
| `constants/personalResources.ts` | 与后端同步新增 `knowledge`、`datasets` 两项定义 |
| `types/workbench.ts` | `WorkbenchPersonalResourceKey` 联合类型加两个 key |
| `app/services/workbench_home_service.py`（后端） | `PERSONAL_RESOURCE_DEFS` 加两项并实现计数（见 §6.6） |
| `views/Users.vue` | 审批弹窗改「资源访问审批」：类型筛选、类型徽标、审批依据、状态校验、入口文案 |
| `views/PersonalCenter.vue` | 「我的申请」类型徽标、文案改为资源访问申请、空态指向资源广场 |
| `components/PortalNotificationBell.vue` | 新 category 映射与跳转；兼容旧知识库 category |
| `router/index.ts` | 两条只读页路由（无权限码） |
| `views/SystemConfig.vue` | 数据集目录开关 |
| `views/KnowledgeBaseManagement.vue` | 「发现」弹窗文案加一句指向资源广场 |

### 11.3 状态与错误处理

- 两个查询独立加载、独立报错，一段失败不影响另一段。
- 申请提交成功后本地把该卡片置为「审批中」，不整页刷新。
- 只读页 403 时给出「去资源广场申请」按钮。
- 移动端：筛选栏换行、卡片单列、表格横向滚动。

## 12. 只读页的边界

明确**不返回、不展示**：

- 数据集：字段明细、示例数据、YAML、行级权限配置、数据源连接信息、元数据编辑入口。
- 知识库：文档正文、切片内容、原文下载/预览地址、上传/删除/改可见范围入口。
- 两类都不提供「申请他人已授权资源」或「转授权」。

原因：行级权限与字段脱敏都在 ChatBI 查询链路实现，只读页直接给原始行/字段等于绕过管控；知识库原文下载绕过检索审计。

## 13. 兼容与上线顺序

1. 迁移先行（MySQL V192 / PG V92），旧代码不受影响（新列有默认值）。
2. 后端：服务 + 新接口 + 旧接口委托；此时新接口可用，旧页面行为不变。
3. 前端：工作台 tab 与广场面板 → 两个只读页 → 审核弹窗与我的申请改造 → 通知映射。
4. 配置开关最后接（不阻塞主流程）。

回滚：前端隐藏 tab 即可；后端新增接口与列可保留，不影响旧链路。

## 14. 测试计划

### 后端（pytest）

- 申请：知识库/数据集各一条正常申请；重复 pending 拒绝；私有库拒绝；无权限校验；停用数据集拒绝。
- 审批：管理员通过后 `ResourcePermission` 落库；驳回不授权；非管理员审批 403；审批自己的申请拒绝。
- 撤回：本人 pending 可撤回；他人/非 pending 拒绝。
- 列表：pending 仅管理员可见；mine 混排两类且带 `resource_type`；reviews 可按类型过滤。
- 只读页：无权限 403；知识库文档列表不含下载地址；数据集不返回字段/样例数据。
- 审计：申请/通过/驳回/撤回各写一条 `ai_agent_metadata_audit_logs`；手工授权/撤销也落表；审计写入失败不影响主流程返回 200。
- 工作台：home 返回含 `knowledge` / `datasets` 两项且顺序在最前；普通用户口径与管理员口径分别断言；统计异常时该项为 `error` 且其余项正常。
- 兼容：旧 `/ragflow/datasets/discover` 与旧审批接口返回结构不变。

### 前端

- `vue-tsc --noEmit` 通过。
- `pytest --confcutdir=tests/frontend` 契约用例：新增广场接口字段契约、卡片状态分支、只读页 403 分支。
- 更新 `tests/CHECKLIST.md`。

## 15. 风险与取舍

| 风险 | 处理 |
|---|---|
| 数据集全量目录暴露资产清单 | 系统开关可关；只暴露名称/描述/规模，不含字段与数据 |
| 申请表混存两类资源，语义变宽 | 列注释与模型 docstring 说明；`resource_type` 索引保证查询效率 |
| 审批集中到管理员形成瓶颈 | 本期接受；列表支持类型筛选，后续可加批量审批 |
| 知识库引擎抖动导致广场不完整 | 复用缓存快照 + `stale` 提示，不整段消失 |
| 数据集授权后 AI 侧仍看不到 | 审批通过同时清 `PermissionService` 与 `AgentConfigProvider` 两处缓存 |
| 只读页成为绕开行级权限的后门 | 只给表/指标目录，不给字段明细与样例行 |
