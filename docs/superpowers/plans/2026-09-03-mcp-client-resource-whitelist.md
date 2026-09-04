# MCP Client 资源白名单恢复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 恢复每个 Platform MCP OAuth Client 对智能体、知识库和元数据数据集的独立白名单限制，并在服务台提供勾选式资源编辑弹框。

**架构：** 复用现有 `sys_mcp_oauth_clients` 的三个 JSON 字段，不新增数据库迁移。服务台增加一个按资源类型返回当前用户可访问资源的选项接口；Client 更新接口保留 `NULL`、空数组和非空数组三态，Platform MCP 运行时执行“用户权限 ∩ Client 白名单 ∩ 请求范围”。资源白名单变更沿用现有 Client 安全变更流程，撤销旧 Token 和 OAuth Grant。

**技术栈：** Python 3.11、FastAPI、Pydantic 2、SQLAlchemy 2.x async、Vue 3、TypeScript、pytest、前端契约测试、`vue-tsc`。

---

## 文件变更总览

- 修改：`app/models/platform_mcp.py`
  - 恢复 `McpOAuthClient` 的三个 JSON 白名单字段。
- 修改：`app/api/portal/endpoints/mcp_service.py`
  - 恢复 DTO、Client 序列化、Client 创建/更新字段；增加资源选项接口；接入白名单变更的 Token/Grant 撤销。
- 修改：`app/services/mcp/platform_oauth.py`
  - 恢复带 Client 白名单参数的资源交集辅助函数，或将现有交集函数扩展为三方交集。
- 修改：`app/services/mcp/platform_mcp.py`
  - 恢复 Agent、知识库和 Metadata 的 Client 白名单运行时过滤。
- 修改：`frontend/src/views/McpServiceDesk.vue`
  - 恢复 Client 类型字段；为三类资源增加按钮、状态、勾选弹框和独立 PATCH 保存。
- 修改：`tests/api/test_mcp_service_desk_contract.py`
  - 增加字段、三态、选项接口和安全变更测试。
- 修改：`tests/services/mcp/test_platform_oauth.py`
  - 增加三态资源交集测试。
- 修改：`tests/services/mcp/test_platform_mcp_methods.py`
  - 增加 Agent、知识库、Metadata 的 Client 白名单运行时契约。
- 修改：`tests/frontend/test_frontend_mcp_service_desk_contract.py`
  - 将“资源白名单不存在”的旧断言替换为勾选式资源配置契约。
- 修改：`tests/CHECKLIST.md`
  - 更新 Platform MCP 服务台条目，说明复用已有字段、无新增迁移和验证边界。
- 不修改：`db-prod/V137-create_platform_mcp_oauth.sql`、`db-prod-pg/V38-create_platform_mcp_oauth.sql`
  - 两个文件已经包含所需字段，本次不新增 DDL。

## 任务 1：先建立资源白名单三态和 Client DTO 的失败测试

**文件：**

- 修改：`tests/api/test_mcp_service_desk_contract.py`
- 修改：`tests/services/mcp/test_platform_oauth.py`

- [x] **步骤 1：为 Client DTO 增加三个字段的失败契约**

在 `tests/api/test_mcp_service_desk_contract.py` 增加测试，先断言当前实现缺少资源白名单字段：

```python
def test_client_payload_accepts_resource_whitelist_fields_and_preserves_three_states():
    unrestricted = mcp_service.McpOAuthClientCreate(
        client_name="resource-policy-client",
        redirect_uris=["https://example.com/oauth/callback"],
        allowed_scopes=["agent:list"],
        allowed_agent_ids=None,
        allowed_knowledge_base_ids=None,
        allowed_metadata_dataset_ids=None,
    )
    empty = mcp_service.McpOAuthClientUpdate(allowed_agent_ids=[])
    selected = mcp_service.McpOAuthClientUpdate(
        allowed_knowledge_base_ids=[" kb-1 ", "kb-1", "kb-2"]
    )

    assert unrestricted.allowed_agent_ids is None
    assert unrestricted.allowed_knowledge_base_ids is None
    assert unrestricted.allowed_metadata_dataset_ids is None
    assert empty.allowed_agent_ids == []
    assert selected.allowed_knowledge_base_ids == ["kb-1", "kb-2"]


def test_client_payload_still_rejects_unknown_resource_policy_fields():
    with pytest.raises(ValueError):
        mcp_service.McpOAuthClientCreate(
            client_name="unknown-policy-client",
            redirect_uris=["https://example.com/oauth/callback"],
            allowed_scopes=["agent:list"],
            allowed_resource_ids=["resource-1"],
        )
```

为 `McpOAuthClientCreate` 和 `McpOAuthClientUpdate` 分别断言 `None`、`[]` 和非空数组不被混淆；列表中的空格和重复项应归一化。

- [x] **步骤 2：为资源交集辅助函数增加失败测试**

在 `tests/services/mcp/test_platform_oauth.py` 增加：

```python
from app.services.mcp.platform_oauth import intersect_authorized_ids
```

```python
def test_intersect_authorized_ids_applies_user_client_and_requested_sets():
    assert intersect_authorized_ids(
        ["a", "b", "c"],
        ["b", "c", "d"],
        ["c", "d"],
    ) == ["c"]


def test_intersect_authorized_ids_distinguishes_none_and_empty_client_policy():
    assert intersect_authorized_ids(["a", "b"], None) == ["a", "b"]
    assert intersect_authorized_ids(["a", "b"], []) == []
    assert intersect_authorized_ids(None, ["a", "b"]) == ["a", "b"]
```

如果当前函数签名只接受两个参数，先让测试失败，再在实现任务中统一为 `user_allowed, client_allowed, requested` 三个边界。

- [x] **步骤 3：运行新增失败测试**

运行：

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/api/test_mcp_service_desk_contract.py \
  tests/services/mcp/test_platform_oauth.py
```

预期：新增字段测试因 DTO 不接受字段或交集函数签名不匹配而失败；不要修改生产代码来掩盖测试失败。

## 任务 2：恢复 ORM、DTO、归一化和 Client 序列化

**文件：**

- 修改：`app/models/platform_mcp.py:35-52`
- 修改：`app/api/portal/endpoints/mcp_service.py:139-255`
- 修改：`app/api/portal/endpoints/mcp_service.py:258-301`
- 修改：`app/services/mcp/platform_oauth.py:125-139`
- 测试：`tests/api/test_mcp_service_desk_contract.py`
- 测试：`tests/services/mcp/test_platform_oauth.py`

- [x] **步骤 1：恢复 ORM 字段，不创建迁移**

在 `McpOAuthClient` 的 `allowed_scopes` 后恢复：

```python
allowed_agent_ids = Column(JSON, nullable=True)
allowed_knowledge_base_ids = Column(JSON, nullable=True)
allowed_metadata_dataset_ids = Column(JSON, nullable=True)
```

不要修改 `db-prod/V137-create_platform_mcp_oauth.sql` 或 `db-prod-pg/V38-create_platform_mcp_oauth.sql`，不要创建新的 SQL 文件。

- [x] **步骤 2：增加统一资源 ID 归一化函数**

在 `app/api/portal/endpoints/mcp_service.py` 的 DTO 之前增加一个仅负责清洗列表的函数：

```python
def _normalize_resource_ids(value: list[str] | None, field_name: str) -> list[str] | None:
    if value is None:
        return None
    cleaned = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    if len(cleaned) > 500:
        raise ValueError(f"{field_name} 最多允许 500 个资源")
    return cleaned
```

`None` 必须原样保留；空列表必须返回空列表，不能使用 `value or None`。

- [x] **步骤 3：在创建和更新 DTO 中恢复字段**

`McpOAuthClientCreate` 和 `McpOAuthClientUpdate` 都增加：

```python
allowed_agent_ids: list[str] | None = None
allowed_knowledge_base_ids: list[str] | None = None
allowed_metadata_dataset_ids: list[str] | None = None
```

为三个字段分别调用 `_normalize_resource_ids`，保留现有 `ConfigDict(extra="forbid")`。字段未提交时 `McpOAuthClientUpdate.model_dump(exclude_unset=True)` 不能产生任何对应键；显式 JSON `null` 和 `[]` 必须分别进入 `changes`。

- [x] **步骤 4：恢复创建参数与返回序列化**

在 `create_client` 调用 `PlatformMcpOAuthService.create_client` 时传入三个字段；在 `_serialize_client` 返回：

```python
"allowed_agent_ids": list(client.allowed_agent_ids) if client.allowed_agent_ids is not None else None,
"allowed_knowledge_base_ids": (
    list(client.allowed_knowledge_base_ids)
    if client.allowed_knowledge_base_ids is not None
    else None
),
"allowed_metadata_dataset_ids": (
    list(client.allowed_metadata_dataset_ids)
    if client.allowed_metadata_dataset_ids is not None
    else None
),
```

- [x] **步骤 5：恢复 OAuth Client 创建参数**

在 `PlatformMcpOAuthService.create_client` 增加三个可选参数，并在 `McpOAuthClient(...)` 中保存归一化后的列表。创建时不允许把不受支持的 Scope 混入 `allowed_scopes`；资源白名单字段只保存资源 ID，不改变 Scope 过滤。

- [x] **步骤 6：运行任务 1 测试并确认三态通过**

运行：

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/api/test_mcp_service_desk_contract.py \
  tests/services/mcp/test_platform_oauth.py
```

预期：任务 1 的新增测试通过；尚未恢复运行时过滤的测试暂不在本任务中加入。

## 任务 3：增加服务台资源选项接口

**文件：**

- 修改：`app/api/portal/endpoints/mcp_service.py`
- 测试：`tests/api/test_mcp_service_desk_contract.py`

- [x] **步骤 1：先增加资源类型和返回结构测试**

增加契约测试，要求源码包含资源类型校验、当前用户权限服务和统一分页返回：

```python
def test_resource_options_endpoint_uses_user_scoped_sources_and_pagination():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert 'resource_type: Literal["agent", "knowledge_base", "metadata_dataset"]' in source
    assert "AgentManagerService.list_allowed_agents" in source
    assert "get_knowledge_base_access" in source
    assert "list_accessible_dataset_options" in source
    assert '"resource_type": resource_type' in source
    assert '"items": items' in source
    assert '"has_more":' in source
```

- [x] **步骤 2：实现 Client 所有权和服务台权限检查**

新增：

```python
@router.get("/clients/{client_id}/resource-options")
async def list_client_resource_options(
    client_id: str,
    resource_type: Literal["agent", "knowledge_base", "metadata_dataset"],
    keyword: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user: dict = Depends(require_mcp_service_permission("element:mcp_service:client:manage")),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
```

使用 `_get_owned_client(db, client_id, user)`；普通用户不能读取其他 Client 的资源选项，管理员沿用现有管理权限。`keyword` 为空时不做名称过滤，非空时按资源名称或 ID 做大小写不敏感匹配。

- [x] **步骤 3：实现三类资源源查询**

使用现有服务，不复制权限逻辑：

- `agent`：调用 `AgentManagerService.list_allowed_agents(db, user=user_info, keyword=keyword)`，返回 `id`、展示名称、描述和启用状态；
- `knowledge_base`：调用 `PermissionService(db).get_knowledge_base_access(user_id, user_name)`，管理员查询未删除知识库，普通用户按 `accessible_ids` 过滤 `KnowledgeBaseMetadata`；
- `metadata_dataset`：调用 `MetadataService.list_accessible_dataset_options(db, user_id=..., is_admin=..., status=1)`，返回数据集 ID、名称和描述。

返回字段只允许轻量展示数据：`id`、`name`、`description`、`summary`，禁止连接配置、内部地址、凭据和文档正文。

- [x] **步骤 4：实现内存分页和统一返回**

对三类已完成权限过滤的结果按稳定 ID/名称排序，再切片：

```python
offset = (page - 1) * page_size
page_items = items[offset:offset + page_size]
return {
    "resource_type": resource_type,
    "items": page_items,
    "page": page,
    "page_size": page_size,
    "total": len(items),
    "has_more": offset + len(page_items) < len(items),
}
```

- [x] **步骤 5：运行服务台 API 契约测试**

运行：

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/api/test_mcp_service_desk_contract.py
```

预期：资源选项接口契约通过；不启动服务，不连接真实数据库。

## 任务 4：恢复 Platform MCP 运行时三方资源交集

**文件：**

- 修改：`app/services/mcp/platform_oauth.py:125-139`
- 修改：`app/services/mcp/platform_mcp.py:265-308`
- 修改：`app/services/mcp/platform_mcp.py:450-516`
- 修改：`app/services/mcp/platform_mcp.py:615-635`
- 修改：`app/services/mcp/platform_mcp.py:704-735`
- 修改：`app/services/mcp/platform_mcp.py:860-883`
- 测试：`tests/services/mcp/test_platform_oauth.py`
- 测试：`tests/services/mcp/test_platform_mcp_methods.py`

- [x] **步骤 1：扩展交集函数并保持三态语义**

将 `intersect_authorized_ids` 调整为：

```python
def intersect_authorized_ids(
    user_allowed: Iterable[str] | None,
    client_allowed: Iterable[str] | None,
    requested: Iterable[str] | None = None,
) -> list[str]:
    def _clean(values: Iterable[str] | None) -> set[str] | None:
        if values is None:
            return None
        return {str(value).strip() for value in values if str(value).strip()}

    user_set, client_set, requested_set = map(
        _clean,
        (user_allowed, client_allowed, requested),
    )
    result = user_set
    if client_set is not None:
        result = client_set if result is None else result & client_set
    if requested_set is not None:
        result = requested_set if result is None else result & requested_set
    return sorted(result or set())
```

空集合必须保留为明确无权限，不能因 `or` 把 `[]` 解释成未配置；只有 `None` 表示不增加边界。

- [x] **步骤 2：恢复知识库 Client 白名单**

在 `_resolve_knowledge_scope` 加载 Client 后读取 `client.allowed_knowledge_base_ids`，然后调用：

```python
effective = intersect_authorized_ids(
    user_allowed,
    client.allowed_knowledge_base_ids,
    requested or None,
)
```

管理员在 Client 白名单为 `None` 且没有请求范围时继续从平台目录得到可检索数据集；Client 白名单为空时直接得到空集并返回统一无权限错误。

- [x] **步骤 3：恢复 Metadata Client 白名单**

在 `_resolve_metadata_dataset_ids` 读取 `client.allowed_metadata_dataset_ids`，将它与用户可访问数据集和请求范围交集。`metadata_list_datasets`、`metadata_search`、`metadata_get_dataset`、`metadata_get_schema`、`metadata_get_metrics` 必须继续共用该解析入口。

- [x] **步骤 4：恢复 Agent Client 白名单**

在 `_load_authorized_agent` 增加 Client 参数，在通过 `AgentManagerService._user_can_execute_agent` 后检查：

```python
client_allowed = client.allowed_agent_ids
if client_allowed is not None and normalized_agent_id not in {
    str(value).strip() for value in client_allowed if str(value).strip()
}:
    raise PermissionError("agent_forbidden")
```

`agent_list_allowed` 先通过 `list_allowed_agents` 得到用户可执行列表，再按同一 Client 白名单过滤。`agent_invoke` 和 `conversation_continue` 都必须将当前 Client 传入 `_load_authorized_agent`。

- [x] **步骤 5：增加运行时失败测试**

在 `tests/services/mcp/test_platform_mcp_methods.py` 增加源码/辅助函数契约，覆盖：

```python
def test_platform_mcp_runtime_restores_client_resource_whitelist_checks():
    source = Path("app/services/mcp/platform_mcp.py").read_text(encoding="utf-8")

    assert "allowed_agent_ids" in source
    assert "allowed_knowledge_base_ids" in source
    assert "allowed_metadata_dataset_ids" in source
    assert "AgentManagerService.list_allowed_agents" in source
    assert "intersect_authorized_ids" in source
    assert "agent_forbidden" in source
```

补充可独立调用的交集测试，确保用户权限之外不能因 Client 配置而扩权，空数组会拒绝全部资源。

- [x] **步骤 6：运行 MCP 方法测试**

运行：

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/services/mcp/test_platform_oauth.py \
  tests/services/mcp/test_platform_mcp.py \
  tests/services/mcp/test_platform_mcp_methods.py
```

## 任务 5：接入白名单更新的安全失效流程

**文件：**

- 修改：`app/api/portal/endpoints/mcp_service.py:1528-1645`
- 测试：`tests/api/test_mcp_service_desk_contract.py`

- [x] **步骤 1：先增加资源策略变更契约**

增加测试断言更新接口会比较三个字段，且会撤销三类凭证关系：

```python
def test_resource_whitelist_changes_revoke_tokens_and_grants():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "allowed_agent_ids" in source
    assert "allowed_knowledge_base_ids" in source
    assert "allowed_metadata_dataset_ids" in source
    assert "resource_policy_changed" in source
    assert "McpOAuthAccessToken.revoked_at" in source
    assert "McpOAuthRefreshToken.revoked_at" in source
    assert "McpOAuthGrant.status" in source
```

- [x] **步骤 2：实现旧值/新值归一化比较**

在 `update_client` 中对三个资源字段分别执行：

```python
resource_policy_changed = False
changed_resource_types: list[str] = []
resource_type_by_field = {
    "allowed_agent_ids": "agent",
    "allowed_knowledge_base_ids": "knowledge_base",
    "allowed_metadata_dataset_ids": "metadata_dataset",
}
for field_name in (
    "allowed_agent_ids",
    "allowed_knowledge_base_ids",
    "allowed_metadata_dataset_ids",
):
    if field_name not in changes:
        continue
    normalized_next = _normalize_resource_ids(changes[field_name], field_name)
    normalized_current = _normalize_resource_ids(
        getattr(client, field_name),
        field_name,
    )
    if normalized_next != normalized_current:
        resource_policy_changed = True
        changed_resource_types.append(resource_type_by_field[field_name])
    changes[field_name] = normalized_next
```

顺序变化不触发变更；`None`、`[]` 和非空数组之间的变化必须触发变更。

- [x] **步骤 3：把资源策略加入安全变更**

将现有判断改为：

```python
security_changed = (
    scope_changed
    or grant_types_changed
    or redirect_uris_changed
    or status_changed
    or resource_policy_changed
)
```

沿用现有 SQLAlchemy 更新逻辑撤销 Access Token、Refresh Token 和活动 Grant。资源白名单变化不递增 `scope_version`，因为该字段只表示方法 Scope 版本。

- [x] **步骤 4：写入脱敏安全审计**

资源策略发生变化时，为每个发生变化的资源类型记录字段名和新值数量，例如：

```python
details={
    "resource_policy_changed": True,
    "resource_types": changed_resource_types,
}
```

仅当 `resource_policy_changed` 为真时写入事件；不记录资源 ID、Token、Secret 或原始请求体。复用当前服务台安全审计写入方式。

- [x] **步骤 5：运行 API 回归测试**

运行：

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/api/test_mcp_service_desk_contract.py
```

## 任务 6：实现服务台资源分组和勾选弹框

**文件：**

- 修改：`frontend/src/views/McpServiceDesk.vue`
- 测试：`tests/frontend/test_frontend_mcp_service_desk_contract.py`

- [x] **步骤 1：先增加前端契约测试**

将旧的“不得出现资源白名单”断言替换为：

```python
def test_service_desk_exposes_resource_whitelist_editors():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "allowed_agent_ids" in view
    assert "allowed_knowledge_base_ids" in view
    assert "allowed_metadata_dataset_ids" in view
    assert "编辑智能体白名单" in view
    assert "编辑知识库白名单" in view
    assert "编辑数据集白名单" in view
    assert "resource-options" in view
    assert "type=\"checkbox\"" in view
    assert "全选当前结果" in view
    assert "恢复全部用户可访问资源" in view
```

增加保存行为契约：

```python
def test_service_desk_resource_whitelist_save_is_field_scoped():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "resourceWhitelistModal.field" in view
    assert "resourceWhitelistModal.selectedIds" in view
    assert "{ [resourceWhitelistModal.field]: value }" in view
    assert "await loadClients()" in view
```

- [x] **步骤 2：恢复 Client 类型和资源配置元数据**

在 `Client` 类型加入：

```ts
allowed_agent_ids: string[] | null
allowed_knowledge_base_ids: string[] | null
allowed_metadata_dataset_ids: string[] | null
```

新增固定配置：

```ts
type ResourceWhitelistType = 'agent' | 'knowledge_base' | 'metadata_dataset'
type ResourceWhitelistField =
  | 'allowed_agent_ids'
  | 'allowed_knowledge_base_ids'
  | 'allowed_metadata_dataset_ids'

const resourceWhitelistConfigs = [
  {
    type: 'agent' as ResourceWhitelistType,
    field: 'allowed_agent_ids' as ResourceWhitelistField,
    title: '智能体白名单',
    scopes: ['agent:list', 'agent:invoke'],
  },
  {
    type: 'knowledge_base' as ResourceWhitelistType,
    field: 'allowed_knowledge_base_ids' as ResourceWhitelistField,
    title: '知识库白名单',
    scopes: ['knowledge:search'],
  },
  {
    type: 'metadata_dataset' as ResourceWhitelistType,
    field: 'allowed_metadata_dataset_ids' as ResourceWhitelistField,
    title: '数据集白名单',
    scopes: ['metadata:read', 'metadata:search', 'metadata:metrics:read'],
  },
] as const
```

- [x] **步骤 3：新增弹框状态和资源选项加载**

新增状态：

```ts
const resourceWhitelistModal = reactive({
  open: false,
  client: null as Client | null,
  type: null as ResourceWhitelistType | null,
  field: null as ResourceWhitelistField | null,
  keyword: '',
  page: 1,
  pageSize: 50,
  items: [] as Array<{ id: string; name: string; description?: string; summary?: string }>,
  total: 0,
  selectedIds: [] as string[],
  unrestricted: false,
  loading: false,
  saving: false,
})
```

`openResourceWhitelistEditor(client, config)` 初始化：

- `unrestricted = client[config.field] === null`；
- `selectedIds = [...(client[config.field] || [])]`；
- 调用 `/api/portal/mcp-service/clients/${client.client_id}/resource-options`，携带 `resource_type`、`keyword`、`page`、`page_size`；
- 加载失败时显示错误并保持弹框草稿，不提交空数组。

- [x] **步骤 4：实现三态 UI 操作**

在弹框中实现：

- 复选框勾选/取消勾选时设置 `unrestricted = false`；
- “保存无权限”设置 `unrestricted = false`、`selectedIds = []`；
- “恢复全部用户可访问资源”设置 `unrestricted = true`；
- 保存时根据状态映射：
  - `unrestricted === true` -> `null`；
  - 否则 -> `selectedIds`；
- 选择状态始终由 `selectedIds.includes(item.id)` 决定，不使用 truthiness 判断空数组。

- [x] **步骤 5：实现字段范围明确的保存函数**

保存函数必须只构造当前字段：

```ts
const saveResourceWhitelist = async () => {
  if (!resourceWhitelistModal.client || !resourceWhitelistModal.field) return
  const value = resourceWhitelistModal.unrestricted
    ? null
    : [...new Set(resourceWhitelistModal.selectedIds)]
  resourceWhitelistModal.saving = true
  try {
    await api.patch(
      `/api/portal/mcp-service/clients/${resourceWhitelistModal.client.client_id}`,
      { [resourceWhitelistModal.field]: value },
    )
    closeResourceWhitelistEditor()
    await loadClients()
    error.value = ''
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '资源白名单保存失败'
  } finally {
    resourceWhitelistModal.saving = false
  }
}
```

保存成功后复用现有 Client 列表的 `needs_token_regeneration` 展示；不要在保存失败时关闭弹框或清空草稿。

- [x] **步骤 6：实现资源分组卡片和弹框模板**

在 Client 权限摘要处增加三类资源分组：

- 展示对应 Scope；
- 展示“未配置”“无权限”或“已选 N 项”；
- 每类独立“编辑白名单”按钮；
- Agent 两个 Scope 共享一个按钮和字段；Metadata 三个 Scope 共享一个按钮和字段；
- 弹框内包含搜索框、资源复选框、已选数量、全选当前结果、清空、恢复全部、取消和保存。

保持现有移动端卡片布局、按钮 `shrink-0` 和横向不溢出约束；不把资源复选框直接塞入 Scope 选择表格。

- [x] **步骤 7：运行前端契约和类型检查**

运行：

```bash
.venv/bin/pytest --confcutdir=tests/frontend -q \
  tests/frontend/test_frontend_mcp_service_desk_contract.py
cd frontend && ./node_modules/.bin/vue-tsc --noEmit
```

## 任务 7：补齐安全、兼容和回归测试

**文件：**

- 修改：`tests/api/test_mcp_service_desk_contract.py`
- 修改：`tests/services/mcp/test_platform_oauth.py`
- 修改：`tests/services/mcp/test_platform_mcp_methods.py`
- 修改：`tests/frontend/test_frontend_mcp_service_desk_contract.py`
- 修改：`tests/test_platform_mcp_migrations.py`

- [x] **步骤 1：验证旧迁移字段存在且不新增迁移**

在 `tests/test_platform_mcp_migrations.py` 增加：

```python
def test_existing_platform_mcp_migrations_contain_resource_whitelist_columns():
    mysql = (ROOT / "db-prod/V137-create_platform_mcp_oauth.sql").read_text(encoding="utf-8")
    postgres = (ROOT / "db-prod-pg/V38-create_platform_mcp_oauth.sql").read_text(encoding="utf-8")

    for field in (
        "allowed_agent_ids",
        "allowed_knowledge_base_ids",
        "allowed_metadata_dataset_ids",
    ):
        assert field in mysql
        assert field in postgres
```

不要添加 `DROP COLUMN` 或新增版本 SQL。

- [x] **步骤 2：覆盖越权资源与资源存在性保护**

测试保存和运行时都不能只相信前端传入的 ID；非法、停用或当前用户不可访问的资源不能通过服务台选项或 Platform MCP 运行时边界。错误消息保持统一，不区分资源不存在和无权访问。

- [x] **步骤 3：覆盖审计脱敏**

断言资源策略变更审计只包含资源类型/数量等摘要，不包含 Access Token、Refresh Token、Client Secret、原始 Header 或完整资源列表。

- [x] **步骤 4：运行完整聚焦回归**

运行：

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/api/test_mcp_service_desk_contract.py \
  tests/services/mcp/test_platform_oauth.py \
  tests/services/mcp/test_platform_mcp.py \
  tests/services/mcp/test_platform_mcp_methods.py \
  tests/test_platform_mcp_migrations.py

.venv/bin/pytest --confcutdir=tests/frontend -q \
  tests/frontend/test_frontend_mcp_service_desk_contract.py

cd frontend && ./node_modules/.bin/vue-tsc --noEmit
```

## 任务 8：更新测试清单并做最终静态检查

**文件：**

- 修改：`tests/CHECKLIST.md`

- [x] **步骤 1：更新 Platform MCP 条目**

在现有 Platform MCP 服务台条目中补充：

- Client 独立 Agent/知识库/Metadata 白名单；
- 复用 V137/V38 已有字段；
- 无新增数据库迁移；
- 勾选式资源选项弹框；
- 白名单变更撤销 Token/Grant；
- 聚焦测试已执行，真实数据库迁移、OAuth 浏览器、Redis、RAGFlow、生产 MCP 客户端和部署验收未执行。

- [x] **步骤 2：执行静态检查**

运行：

```bash
python3 -m compileall -q app/models/platform_mcp.py \
  app/api/portal/endpoints/mcp_service.py \
  app/services/mcp/platform_oauth.py \
  app/services/mcp/platform_mcp.py
git diff --check
git status --short
```

预期：编译和 diff 检查通过；只出现本需求范围内的文件变化，不自动 `git add` 或 `git commit`。

## 完成标准

- [x] 三个已有 JSON 字段重新由 ORM、API、OAuth Service 和运行时使用。
- [x] `NULL`、空数组和非空数组语义在前后端保持一致。
- [x] Agent、知识库、Metadata 运行时都执行用户权限与 Client 白名单交集。
- [x] 资源白名单变更撤销旧 Token 和 OAuth Grant，并显示重新生成提示。
- [x] 前端每个资源分组都有独立按钮和勾选弹框，保存只提交当前字段。
- [x] 后端、运行时、前端契约测试和类型检查通过。
- [x] 没有新增数据库迁移，没有启动 `./dev.sh`，没有宣称 live 环境验收通过。
