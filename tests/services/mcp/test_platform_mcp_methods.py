from types import SimpleNamespace

import pytest

from app.services.mcp import platform_mcp as platform_mcp_module
from app.services.mcp.platform_mcp import (
    PLATFORM_MCP_METHODS,
    _metadata_search_items,
    get_method_definition,
    platform_mcp,
)
from app.services.mcp.platform_mcp_support import (
    build_platform_user_info,
    decode_platform_cursor,
    encode_platform_cursor,
    intersect_resource_ids,
    serialize_metadata_dataset,
    serialize_metadata_schema,
)


pytestmark = pytest.mark.no_infrastructure


def test_all_platform_mcp_methods_are_implemented_and_registered_by_definition():
    expected = {
        "agent.list_allowed": ("agent:list", "agent"),
        "agent.invoke": ("agent:invoke", "agent"),
        "conversation.continue": ("conversation:continue", "conversation"),
        "knowledge.search": ("knowledge:search", "knowledge"),
        "metadata.list_datasets": ("metadata:read", "metadata"),
        "metadata.search": ("metadata:search", "metadata"),
        "metadata.get_dataset": ("metadata:read", "metadata"),
        "metadata.get_schema": ("metadata:read", "metadata"),
        "metadata.get_metrics": ("metadata:metrics:read", "metadata"),
    }

    assert {item.name for item in PLATFORM_MCP_METHODS} == set(expected)
    for name, (scope, capability_group) in expected.items():
        definition = get_method_definition(name)
        assert definition is not None
        assert definition.implemented is True
        assert definition.scope == scope
        assert definition.capability_group == capability_group
        assert definition.requires_user is True


@pytest.mark.asyncio
async def test_all_implemented_methods_are_visible_when_platform_and_groups_are_enabled(monkeypatch):
    async def enabled(_name=None):
        return True

    monkeypatch.setattr(platform_mcp_module, "is_platform_mcp_enabled", enabled)
    monkeypatch.setattr(platform_mcp_module, "is_platform_mcp_capability_enabled", enabled)

    tools = await platform_mcp.list_tools()

    assert {tool.name for tool in tools} == {
        item.name for item in PLATFORM_MCP_METHODS
    }


def test_platform_cursor_is_signed_and_rejects_tampering():
    cursor = encode_platform_cursor("agent.list_allowed", 20)

    assert cursor
    assert decode_platform_cursor("agent.list_allowed", cursor) == 20
    assert decode_platform_cursor("metadata.search", cursor) is None
    assert decode_platform_cursor(
        "agent.list_allowed", cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    ) is None


def test_resource_intersection_distinguishes_unrestricted_from_explicit_empty():
    assert intersect_resource_ids(None, ["a", "b"], None) == ["a", "b"]
    assert intersect_resource_ids(["a", "b"], ["b"], None) == ["b"]
    assert intersect_resource_ids(None, [], None) == []
    assert intersect_resource_ids(["a"], None, ["b"]) == []


def test_platform_user_info_comes_from_user_record_without_credentials():
    info = build_platform_user_info(
        SimpleNamespace(
            id=123,
            user_name="alice",
            real_name="Alice",
            role="user",
            dept_code="sales",
            org_path="company/sales",
            extra_data='{"region_code":"east","api_key":"must-not-forward"}',
            api_key="must-not-forward",
        )
    )

    assert info == {
        "user_id": "123",
        "user_name": "alice",
        "real_name": "Alice",
        "role": "user",
        "dept_code": "sales",
        "org_path": "company/sales",
        "extra_data": {"region_code": "east"},
    }
    assert "api_key" not in info


def test_metadata_serializers_do_not_expose_internal_connection_or_row_policy():
    dataset = SimpleNamespace(
        id=7,
        name="sales",
        display_name="销售数据集",
        description="订单元数据",
        data_source="mysql",
        status=1,
        tags=["sales"],
        row_filter_config={"user_id": "..."},
        rag_dataset_id="internal-rag-id",
        tables=[
            SimpleNamespace(
                id=70,
                physical_name="orders",
                term="订单表",
                description="订单",
                status=1,
                columns=[
                    SimpleNamespace(
                        physical_name="order_id",
                        term="订单 ID",
                        type="bigint",
                        description="主键",
                        is_primary=1,
                        enums=[{"value": 1}],
                    )
                ],
            )
        ],
        metrics=[],
    )

    safe_dataset = serialize_metadata_dataset(dataset)
    safe_schema = serialize_metadata_schema(dataset)

    assert safe_dataset["dataset_id"] == "7"
    assert safe_dataset["table_count"] == 1
    assert "row_filter_config" not in safe_dataset
    assert "rag_dataset_id" not in safe_dataset
    assert safe_schema["tables"][0]["columns"][0]["name"] == "order_id"
    assert safe_schema["tables"][0]["columns"][0]["sensitive"] is False
    assert "enums" not in safe_schema["tables"][0]["columns"][0]


def test_metadata_search_matches_authorized_resources_and_skips_disabled_tables():
    dataset = SimpleNamespace(
        id=7,
        name="sales",
        display_name="销售数据集",
        description="订单元数据",
        tables=[
            SimpleNamespace(
                physical_name="orders",
                term="订单表",
                description="记录客户订单",
                status=1,
                columns=[
                    SimpleNamespace(
                        physical_name="customer_id",
                        term="客户 ID",
                        type="bigint",
                        description="客户标识",
                    )
                ],
            ),
            SimpleNamespace(
                physical_name="secret_orders",
                term="内部订单表",
                description="不应返回",
                status=0,
                columns=[],
            ),
        ],
        metrics=[],
    )

    items = _metadata_search_items(
        [dataset],
        "客户",
        {"table", "column"},
        10,
    )

    assert {item["resource_type"] for item in items} == {"table", "column"}
    assert all(item.get("table_name") != "secret_orders" for item in items)


@pytest.mark.asyncio
async def test_agent_invoke_passes_no_oauth_token_to_agent_runtime(monkeypatch):
    from app.services.mcp.platform_mcp import agent_invoke
    from app.services.mcp.platform_oauth import McpPrincipal

    principal = McpPrincipal(
        client_id="crm",
        user_id="123",
        scopes=("agent:invoke",),
        resource="mcp:nanzi-platform",
        auth_type="user_delegated",
    )
    captured = {}

    async def fake_validate(_principal, _method):
        return None

    async def fake_call(_principal, **kwargs):
        captured.update(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(platform_mcp_module, "_principal_from_context", lambda: principal)
    monkeypatch.setattr(platform_mcp_module, "_validate_platform_method", fake_validate)
    monkeypatch.setattr(platform_mcp_module, "_agent_call_common", fake_call)

    result = await agent_invoke(
        agent_id="agent-1",
        message="测试",
        conversation_id=None,
        client_request_id="crm-1",
    )

    assert result["status"] == "completed"
    assert captured["agent_id"] == "agent-1"
    assert captured["message"] == "测试"


@pytest.mark.asyncio
async def test_agent_runtime_receives_identity_context_without_oauth_access_token(monkeypatch):
    from app.services.ai import agent_service as agent_service_module

    captured = {}

    async def fake_chat_completion(**kwargs):
        captured.update(kwargs)
        return {
            "status": "success",
            "content": "已处理",
            "trace_id": "trace-1",
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        }

    monkeypatch.setattr(
        agent_service_module.agent_service,
        "chat_completion",
        fake_chat_completion,
    )

    result = await platform_mcp_module._invoke_agent(
        object(),
        agent=SimpleNamespace(id="agent-1"),
        message="测试",
        conversation_id="mcp-conv-1",
        user_info={"user_id": "123", "user_name": "alice"},
    )

    assert result["status"] == "completed"
    assert captured["user_info"]["user_id"] == "123"
    assert captured["api_key"] is None


class _ScalarResult:
    def __init__(self, values=None):
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return list(self.values)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return _ScalarResult()


@pytest.mark.asyncio
async def test_agent_list_allowed_applies_client_whitelist_to_user_allowed_agents(monkeypatch):
    from app.services.mcp.platform_mcp import agent_list_allowed
    from app.services.mcp.platform_oauth import McpPrincipal

    principal = McpPrincipal(
        client_id="crm",
        user_id="123",
        scopes=("agent:list",),
        resource="mcp:nanzi-platform",
        auth_type="user_delegated",
    )
    allowed = [
        SimpleNamespace(
            id="agent-allowed",
            name="allowed",
            display_name="允许的助手",
            description="可用",
            is_enabled=True,
        ),
        SimpleNamespace(
            id="agent-blocked",
            name="blocked",
            display_name="Client 禁止的助手",
            description="不可用",
            is_enabled=True,
        ),
    ]

    async def fake_validate(_principal, _method):
        return None

    async def fake_client(_db, _principal):
        return SimpleNamespace(allowed_agent_ids=["agent-allowed"])

    async def fake_user(_db, _principal):
        return SimpleNamespace(id=123), {"user_id": "123", "user_name": "alice"}

    async def fake_list(_db, **_kwargs):
        return allowed

    async def fake_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(platform_mcp_module, "_principal_from_context", lambda: principal)
    monkeypatch.setattr(platform_mcp_module, "_validate_platform_method", fake_validate)
    monkeypatch.setattr(platform_mcp_module, "_load_client", fake_client)
    monkeypatch.setattr(platform_mcp_module, "_load_principal_user", fake_user)
    monkeypatch.setattr(platform_mcp_module, "AsyncSessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        "app.services.ai.agent_manager.AgentManagerService.list_allowed_agents",
        fake_list,
    )
    monkeypatch.setattr(platform_mcp_module, "_write_audit", fake_audit)

    result = await agent_list_allowed(limit=20)

    assert [item["agent_id"] for item in result["items"]] == ["agent-allowed"]


@pytest.mark.asyncio
async def test_metadata_scope_intersects_user_access_and_client_whitelist(monkeypatch):
    from app.services.mcp.platform_oauth import McpPrincipal

    principal = McpPrincipal(
        client_id="crm",
        user_id="123",
        scopes=("metadata:read",),
        resource="mcp:nanzi-platform",
        auth_type="user_delegated",
    )

    async def fake_client(_db, _principal):
        return SimpleNamespace(allowed_metadata_dataset_ids=["7", "8"])

    async def fake_user(_db, _principal):
        return SimpleNamespace(id=123), {"user_id": "123", "role": "user"}

    async def fake_accessible(*_args, **_kwargs):
        return [SimpleNamespace(id=7), SimpleNamespace(id=9)]

    monkeypatch.setattr(platform_mcp_module, "_load_client", fake_client)
    monkeypatch.setattr(platform_mcp_module, "_load_principal_user", fake_user)
    monkeypatch.setattr(
        platform_mcp_module.MetadataService,
        "list_accessible_dataset_options",
        fake_accessible,
    )

    assert await platform_mcp_module._resolve_metadata_dataset_ids(object(), principal) == ["7"]
    assert await platform_mcp_module._resolve_metadata_dataset_ids(object(), principal, ["9"]) == []


@pytest.mark.asyncio
async def test_metadata_scope_requires_a_user_principal(monkeypatch):
    from app.services.mcp.platform_oauth import McpPrincipal

    principal = McpPrincipal(
        client_id="client-1",
        user_id=None,
        scopes=("metadata:read",),
        resource="mcp:nanzi-platform",
        auth_type="unknown",
    )

    async def fake_client(_db, _principal):
        return SimpleNamespace(allowed_metadata_dataset_ids=None)

    monkeypatch.setattr(platform_mcp_module, "_load_client", fake_client)

    with pytest.raises(PermissionError, match="用户授权"):
        await platform_mcp_module._resolve_metadata_dataset_ids(object(), principal)
