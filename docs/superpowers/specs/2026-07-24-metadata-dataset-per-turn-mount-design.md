# 设计：ChatBI 元数据集本轮挂载 + 会话固定

**日期：** 2026-07-24  
**状态：** 已批准（待实现）  
**范围：** EmbedChat + AgentDebug；不改独立 `DataPortalHome` 整页

## 1. 背景与问题

`get_dataset_schema` 已支持通过 `metadata_dataset_ids` 把检索范围限定到指定 MetaDataset，但该参数目前只能来自：

1. 智能体工具高级配置（长期绑定）
2. 会话 `resource_scope.datasets`（会话级挂载，经 `tool_gate_wrapper` 注入）

缺少与知识库门户对齐的能力：

- **本轮请求级**勾选 1 个或多个数据集，只影响当前提问
- 数据门户抽屉里像知识库一样挂载 / 取消挂载
- 可选「固定到会话」升为会话资源范围

聊天请求已有 `knowledge_dataset_ids` 先例；元数据侧尚无对称字段。且客户端经 `debug_options.resource_scope` 注入会被服务端丢弃，不能作为本轮通道。

## 2. 目标

1. 支持单次请求限定：聊天 API 增加 `metadata_dataset_ids`。
2. 数据门户（`DatasetPortalDrawer`）支持勾选单个或多个数据集，限定本轮问数只在这些数据集内检索/执行。
3. 支持「固定到会话」：写入现有 conversation resource-scope，后续轮次回退使用。
4. 入口：`EmbedChat` 与 `AgentDebug`。

非目标：

- 不改独立数据门户整页（`DataPortalHome`）
- 不向 LLM 暴露可自由填写的 `dataset_id` 工具参数
- 不改变 Agent 管理端「工具绑定元数据集」的配置模型

## 3. 决策摘要

| 议题 | 决策 |
|---|---|
| 生命周期 | 本轮挂载 + 会话固定（两者都要） |
| 合并优先级 | **本轮优先**：本轮非空用本轮；本轮空则回退会话挂载；再空则 Agent 工具配置；再空则用户权限内全量检索 |
| 入口 | EmbedChat + AgentDebug |
| ID 类型 | MetaDataset 数字 ID 的字符串形式（与现有 `metadata_dataset_ids` / resource_scope.datasets[].id 一致） |

与知识库路径的刻意差异：知识库当前是「会话 scope 非空则覆盖请求字段」；本功能按产品确认采用 **本轮优先**。

## 4. 范围合并规则

```
effective_metadata_dataset_ids =
  request.metadata_dataset_ids   # 本轮，非空则采用
  or session.resource_scope.datasets[].id
  or agent_tool_config.metadata_dataset_ids
  or None                        # None = 不额外白名单，仅按用户权限检索
```

之后在 `fetch_dataset_schema_core` / SQL 闸门中：

1. 与用户可访问数据集求交
2. 求交为空 → 明确错误/提示（无权限或不在范围内），禁止静默扩大到全量

`execute_sql_query` 的会话/本轮数据集白名单预检与 schema 工具使用同一 `effective` 列表，避免「能搜 schema 却不能跑 SQL」或反向不一致。

## 5. 后端设计

### 5.1 API

在 `ChatCompletionRequest`（`app/api/v1/endpoints/chat.py`）增加：

```python
metadata_dataset_ids: Optional[List[str]] = Field(
    default=None,
    description="本轮结构化指定的 MetaDataset ID 列表（优先于会话挂载；仍需通过权限校验）",
)
```

合并逻辑（与知识库并列，但优先级相反）：

```python
session_dataset_ids = [str(item["id"]) for item in conversation_scope.get("datasets", []) if item.get("id")]
request_dataset_ids = list(completion_request.metadata_dataset_ids or [])
effective_metadata_dataset_ids = request_dataset_ids or session_dataset_ids
```

禁止客户端用 `debug_options.resource_scope` 伪造范围（现有行为保持）。

### 5.2 上下文传递

- `AgentContext` 增加 `metadata_dataset_ids: Optional[List[str]]`
- `AgentContextManager.setup_context` / `agent_service` 把 `effective_metadata_dataset_ids` 写入上下文
- ChatBI runner 从 context 或服务端写入的 debug 快照读取，**不信任**客户端 debug 注入

可选：本轮 ID 也可镜像进服务端组装的 `debug_options["resource_scope"]["datasets"]` 仅当本轮为空且会话有值时（保持现有会话路径）；本轮非空时以 context 字段为准注入工具，避免与会话 Redis 互相覆盖。

### 5.3 工具闸门

`tool_gate_wrapper.wrap_tools_with_schema_gate`：

- 解析 `effective`（context 本轮/合并结果优先于仅会话 mounted ids）
- `get_dataset_schema` 调用前强制 `kwargs["metadata_dataset_ids"] = effective`
- `execute_sql_query` 的数据集范围预检使用同一列表

`schema_prefetch` 自动调用 schema 工具时走同一 wrapper，无需单独传参。

Agent 工具配置注入（`ToolRegistry._configure_runtime_tool_spec`）保留为兜底：仅当 context/gate 未提供列表时生效。

### 5.4 权限

沿用 `_normalize_conversation_resource_scope` / `MetadataService.search_datasets` 的权限语义：无效 ID 丢弃或导致空交集；不得扩大权限。

## 6. 前端设计

### 6.1 数据门户抽屉

扩展 `DatasetPortalDrawer` + `useDatasetPortal`（或新建 `useDatasetMount` 与知识库 `useKnowledgePortal` 对称）：

- 每条数据集：**本轮启用**开关（多选）
- **固定到会话**：将当前本轮选中（或单项）写入 `PUT .../resource-scope` 的 `datasets`
- **从会话移除**：更新 resource-scope
- 视觉：本轮勾选 / 已固定到会话 状态可区分（对齐知识库 active / pin 语义即可，不新造交互范式）

### 6.2 发送聊天

`EmbedChat` / `AgentDebug` 在 completion 请求体增加：

```ts
metadata_dataset_ids?: string[]  // 本轮勾选；空则不传或传 []
```

收集规则：

1. 取数据门户「本轮启用」ID 列表
2. 若为空，不传字段（让服务端回退会话）
3. 输入区可展示本轮已挂载芯片（可选，建议做，避免用户不知道范围）

会话固定仍走现有 resource-scope modal / PUT，不经过 `debug_options.resource_scope`。

### 6.3 AgentDebug

与 EmbedChat 共用同一 composable 行为，保证调试可复现生产路径。

## 7. 测试计划

后端：

- 本轮 ID 非空 → `fetch_dataset_schema_core` / gate 收到该列表，忽略会话列表
- 本轮空 + 会话有值 → 使用会话列表
- 本轮与会话皆空 → 不注入白名单（或仅 Agent 配置）
- 无权限 ID → 求交后为空时失败/提示，不静默全量
- API 字段契约 / context 传递单测

前端：

- 抽屉勾选后请求带 `metadata_dataset_ids`
- 「固定到会话」触发 PUT 且后续空本轮时仍受限
- EmbedChat / AgentDebug 契约测试（若已有类似 knowledge 契约则镜像）

## 8. 风险与兼容

- **与知识库优先级不一致**：文档与 UI 文案需写清「本轮优先」
- **联邦查数 / 跨库**：effective 列表若只有单库，应阻止或提示跨库升级；与现有 `resource_scope` 行为对齐
- **旧客户端**：不传新字段 → 行为与今日会话挂载 + Agent 绑定一致

## 9. 实现切分建议

1. 后端 API + context + gate 合并（可先 API/单测）
2. EmbedChat 数据门户本轮勾选 + 请求字段
3. 「固定到会话」按钮接到现有 PUT
4. AgentDebug 对齐
5. 契约/回归测试与 `tests/CHECKLIST.md` 更新
