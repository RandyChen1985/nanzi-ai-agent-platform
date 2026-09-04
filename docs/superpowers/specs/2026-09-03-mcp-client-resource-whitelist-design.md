# MCP Client 资源白名单恢复设计

**日期：** 2026-09-03
**状态：** 已实现并完成聚焦验证

实现已覆盖 ORM、服务台 API、Platform MCP 运行时和三个独立资源配置弹框；复用 MySQL V137 / PostgreSQL V38 已有字段，不新增数据库迁移。当前聚焦验证包括后端/API/运行时/迁移测试 82 项、前端契约测试 60 项、`vue-tsc --noEmit` 和前端构建；真实数据库迁移、浏览器视觉、Redis、生产 MCP 客户端和部署验收仍需在目标环境执行。

## 目标

恢复 Platform MCP 每个 OAuth Client 对智能体、知识库和元数据数据集的独立资源限制，同时保留当前 Scope、用户绑定 OAuth 和用户资源权限校验。

本次不新增数据库迁移。MySQL `db-prod/V137-create_platform_mcp_oauth.sql` 和 PostgreSQL `db-prod-pg/V38-create_platform_mcp_oauth.sql` 已经包含所需的三个 JSON 字段；本次恢复直接重新接入现有字段。

## 范围与非目标

### 本次范围

- 恢复 `allowed_agent_ids`、`allowed_knowledge_base_ids`、`allowed_metadata_dataset_ids` 的模型、API、运行时读取和前端配置。
- 在 MCP 服务台的能力与 Scope 页面增加资源白名单入口。
- 每个资源类型使用独立的勾选弹框：智能体、知识库、元数据数据集。
- 保存白名单后撤销该 Client 的旧 Access Token、Refresh Token、OAuth 授权关系和未消费授权码。
- 保留当前用户权限作为最终边界，Client 白名单不能扩大用户原有权限。

### 非目标

- 不新增资源权限表或数据库列。
- 不改变 OAuth Authorization Code + PKCE、Scope 版本和用户绑定 Token 模型。
- 不改变现有平台用户、角色、智能体、知识库或元数据权限配置。
- 不允许 Client 白名单绕过当前用户权限。

## 权限语义

每个资源类型独立使用三态配置：

| 存储值 | 语义 | 运行时结果 |
| --- | --- | --- |
| `NULL` | Client 不增加该资源类型的限制 | 使用当前用户可访问资源 |
| `[]` | Client 明确不允许该资源类型 | 该资源类型全部拒绝 |
| 非空数组 | Client 只允许列出的资源 | 当前用户权限与白名单取交集 |

最终资源集合为：

```text
当前用户权限 ∩ Client 资源白名单 ∩ 请求指定的资源范围
```

其中请求指定的资源范围只能进一步缩小结果，不能扩大范围。

## 后端设计

### ORM 与请求模型

在 `app/models/platform_mcp.py` 的 `McpOAuthClient` 中恢复三个 JSON 字段：

```python
allowed_agent_ids = Column(JSON, nullable=True)
allowed_knowledge_base_ids = Column(JSON, nullable=True)
allowed_metadata_dataset_ids = Column(JSON, nullable=True)
```

在 `app/api/portal/endpoints/mcp_service.py` 中：

- `McpOAuthClientCreate` 接受三个可选列表字段；
- `McpOAuthClientUpdate` 接受三个可选列表字段，并区分字段未提交、`null` 和空数组；
- 所有字段做去重、去空格和格式归一化；
- 返回 Client 时序列化三个字段；
- `extra="forbid"` 继续保留，避免未声明的资源权限字段静默写入。

更新示例：

```json
{
  "allowed_knowledge_base_ids": ["kb-sales-001", "kb-support-002"]
}
```

恢复“不增加 Client 限制”时发送：

```json
{
  "allowed_knowledge_base_ids": null
}
```

明确禁止全部资源时发送空数组：

```json
{
  "allowed_knowledge_base_ids": []
}
```

### 资源选项接口

新增服务台专用接口：

```text
GET /api/portal/mcp-service/clients/{client_id}/resource-options
```

请求参数：

- `resource_type`：`agent`、`knowledge_base`、`metadata_dataset`；
- `keyword`：可选名称或 ID 搜索词；
- `page`、`page_size`：分页参数，避免资源规模较大时一次返回全部数据。

接口先使用现有 Client 所有权和服务台权限校验，再按当前登录用户返回资源：

- `agent`：复用 `AgentManagerService.list_allowed_agents`；
- `knowledge_base`：复用 `PermissionService.get_knowledge_base_access`，管理员查询启用知识库，普通用户查询其可访问知识库；
- `metadata_dataset`：复用 `MetadataService.list_accessible_dataset_options`。

返回统一的轻量结构，不包含连接凭据、内部地址、完整文档内容或其他敏感字段：

```json
{
  "resource_type": "knowledge_base",
  "items": [
    {
      "id": "kb-sales-001",
      "name": "销售知识库",
      "description": "",
      "summary": "128 篇文档"
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 1,
  "has_more": false
}
```

### 运行时校验

在 `app/services/mcp/platform_mcp.py` 中恢复 Client 白名单读取，并保留用户权限校验：

- `agent_list_allowed`：先获取当前用户可执行智能体，再与 `allowed_agent_ids` 取交集；
- `agent_invoke` 和 `conversation_continue`：加载 Client 后，智能体必须同时满足启用、当前用户可执行和 Client 白名单；
- `knowledge_search`：将当前用户知识库权限、Client `allowed_knowledge_base_ids` 和请求范围取交集；
- `metadata_list_datasets`、`metadata_search`、`metadata_get_dataset`、`metadata_get_schema`、`metadata_get_metrics`：将当前用户可访问元数据数据集与 Client `allowed_metadata_dataset_ids` 和请求范围取交集。

任何 Client 白名单拒绝都统一返回资源无权访问类错误，不泄露资源是否存在；同时写入现有入站审计日志。

### 白名单变更与 Token 生命周期

在 `update_client` 中分别比较三个字段的新旧归一化值：

- 字段未提交：不改变原值；
- `NULL` 与空数组视为不同配置；
- 内容顺序变化但集合相同：不视为变更；
- 任一资源白名单实际变化：设置 `resource_policy_changed`。

`resource_policy_changed` 与现有 Scope、授权模式、Redirect URI、启停状态一样：

1. 撤销该 Client 的 Access Token；
2. 撤销该 Client 的 Refresh Token；
3. 撤销该 Client 的有效 OAuth Grant；
4. 写入脱敏安全审计事件，只记录资源类型和数量，不记录凭证；
5. 让服务台现有 `needs_token_regeneration` 机制显示重新生成提示。

资源白名单变化不递增 `scope_version`，因为 `scope_version` 继续表示 MCP 方法 Scope 版本；但旧 Token 必须立即失效。

## 前端设计

### Scope 与资源分组

在 `frontend/src/views/McpServiceDesk.vue` 中增加资源配置元数据：

| 资源分组 | 对应 Scope | Client 字段 | 弹框标题 |
| --- | --- | --- | --- |
| 智能体 | `agent:list`、`agent:invoke` | `allowed_agent_ids` | 编辑智能体白名单 |
| 知识库 | `knowledge:search` | `allowed_knowledge_base_ids` | 编辑知识库白名单 |
| 元数据数据集 | `metadata:read`、`metadata:search`、`metadata:metrics:read` | `allowed_metadata_dataset_ids` | 编辑数据集白名单 |

Agent 的两个 Scope 共用智能体白名单；Metadata 的三个 Scope 共用数据集白名单，避免同一资源类别出现多份互相矛盾的配置。

每个资源分组卡片展示：

- Scope 名称和当前授权状态；
- 当前白名单状态：未配置、明确无权限或已选择 N 项；
- 独立“编辑白名单”按钮。

### 勾选弹框

弹框打开时请求对应 `resource_type` 的资源选项，并以 Client 当前字段初始化勾选状态。

弹框提供：

- 名称或 ID 搜索；
- 资源列表复选框；
- 已选数量；
- 全选当前搜索结果；
- 取消全选；
- “保存无权限”：保存空数组；
- “恢复全部用户可访问资源”：保存 `null`；
- 取消和保存按钮。

保存时只提交当前字段，其他两个资源白名单不参与请求。保存成功后：

1. 关闭当前弹框；
2. 重新加载 Client 列表；
3. 显示保存成功提示；
4. 若已有 Token 被撤销，显示现有“需要重新生成 MCP Access Token”提示。

服务端返回的资源列表和保存结果是最终依据；前端不做越权判断，只用于提升选择体验。

## 数据流

```text
McpServiceDesk.vue
  -> GET resource-options(resource_type)
  -> 当前用户可访问资源列表
  -> 用户勾选资源
  -> PATCH clients/{client_id} 只提交一个 allowed_* 字段
  -> 服务端归一化并校验 Client 所有权
  -> 资源白名单变化则撤销 Token / Grant
  -> Platform MCP 调用
  -> 当前用户权限 ∩ Client 白名单 ∩ 请求范围
  -> 返回结果并记录审计
```

## 错误处理与安全边界

- 非 Client 所有人或无服务台管理权限：返回现有 403/404，不返回白名单详情；
- 资源 ID 不存在、已停用或不属于当前用户权限：保存时拒绝，不能只依赖前端列表；
- Agent 不存在、停用、用户无权访问或不在 Client 白名单内：统一返回资源无权访问语义，不泄露资源是否存在；
- Client 列表对非所有者不返回完整白名单 ID，仅返回每类资源的 `mode` 与 `count` 摘要；
- 资源列表加载失败：弹框显示错误并保持旧配置，不提交空数组；
- 保存失败：弹框保持打开，保留用户当前勾选，不覆盖 Client 卡片旧状态；
- 空数组和 `null` 必须在前后端明确区分，不能使用 `value || null` 之类逻辑丢失空数组；
- 任何运行时资源访问都必须从已验证的 `McpPrincipal.user_id` 恢复用户身份；
- 不接受工具参数中的 `user_id`、角色、用户名或其他身份覆盖字段；
- 日志和审计不保存 Client Secret、Access Token、Refresh Token 或完整请求头。

## 测试设计

### 后端 API 契约

更新或新增 `tests/api/test_mcp_service_desk_contract.py` 覆盖：

- Client 创建、更新和序列化包含三个白名单字段；
- `NULL`、空数组、非空数组三态能够被区分；
- 未声明字段仍被 `extra="forbid"` 拒绝；
- 资源选项接口按 `resource_type` 返回统一结构；
- 非法资源类型、越权 Client 和非法资源 ID 被拒绝；
- 白名单变更撤销 Access Token、Refresh Token 和 Grant；
- 白名单字段集合相同但顺序变化时不触发安全变更。

### Platform MCP 运行时

更新 `tests/services/mcp/test_platform_mcp_methods.py` 和相关 OAuth 测试覆盖：

- Agent 列表受 Client 白名单和用户权限双重限制；
- Agent 调用不能访问白名单外智能体；
- 知识库检索执行用户权限、Client 白名单和请求范围三者交集；
- Metadata 五个方法只访问白名单与用户权限交集；
- `NULL` 不增加 Client 限制，空数组拒绝全部资源；
- 资源不存在与无权访问不泄露存在性。

### 前端契约

更新 `tests/frontend/test_frontend_mcp_service_desk_contract.py` 覆盖：

- 三个资源分组和对应独立按钮存在；
- Agent 和 Metadata 的 Scope 共用白名单字段；
- 弹框使用复选框、搜索、全选、清空和恢复全部操作；
- 保存请求只提交当前资源字段；
- `null`、空数组和非空数组的 UI 操作映射正确；
- 保存成功后重新加载 Client 列表并显示 Token 重新生成提示。

### 验证边界

实现后运行聚焦 pytest、前端契约测试、`vue-tsc --noEmit`、Python 编译检查和 `git diff --check`。不主动启动 `./dev.sh`，不执行数据库迁移，不宣称真实 OAuth 浏览器、数据库、Redis、RAGFlow、生产 MCP 客户端或部署验收已经通过。

## 交付拆分

实现按以下顺序拆分：

1. 恢复 ORM、API DTO、Client 序列化和白名单归一化测试；
2. 增加资源选项接口和 API 契约测试；
3. 恢复 Agent、知识库、Metadata 运行时交集和安全变更撤销；
4. 实现服务台三个资源分组的勾选弹框；
5. 补充前端契约、类型检查和聚焦回归验证；
6. 更新 `tests/CHECKLIST.md`，说明复用已有字段、无新增迁移及未执行的 live 验收范围。
