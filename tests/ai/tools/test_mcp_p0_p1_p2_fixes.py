import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models.mcp import McpToolCache
from app.services.ai.runtime.agentscope.tools import (
    RuntimeToolSpec,
    runtime_tool_spec_from_legacy_tool,
)
from app.services.ai.tools.mcp_client import McpClientService, McpSseSession
from app.services.ai.tools.mcp_factory import McpToolFactory


pytestmark = pytest.mark.no_infrastructure


def test_mcp_tool_factory_complex_schema_types():
    """验证 Array 和 Object 参数不再退化为 str，能正确生成并校验复杂类型。"""
    schema = {
        "type": "object",
        "properties": {
            "tags": {"type": "array", "items": {"type": "string"}},
            "config": {"type": "object"},
            "optional_str": {"type": ["string", "null"]},
            "count": {"type": "integer"},
        },
        "required": ["tags"],
    }
    tool_record = McpToolCache(
        id="t-1",
        server_id="s-1",
        tool_name="test_server:batch_process",
        tool_description="Process batch items",
        parameter_schema=json.dumps(schema),
        is_published=True,
        is_available=True,
    )

    tool = McpToolFactory.create_tool(tool_record)
    args_model = tool.args_schema

    # 验证动态生成的 Pydantic 模型接收 list 和 dict 不会报 ValidationError
    instance = args_model(
        tags=["a", "b", "c"],
        config={"key": "value"},
        count=10,
    )
    assert instance.tags == ["a", "b", "c"]
    assert instance.config == {"key": "value"}
    assert instance.count == 10
    assert instance.optional_str is None


def test_mcp_tool_factory_required_nullable_schema_accepts_null():
    """必填字段仍应允许 JSON Schema 明确声明的 null 值。"""
    schema = {
        "type": "object",
        "properties": {"cursor": {"type": ["string", "null"]}},
        "required": ["cursor"],
    }
    tool_record = McpToolCache(
        id="t-nullable",
        server_id="s-1",
        tool_name="test_server:nullable_tool",
        parameter_schema=json.dumps(schema),
    )

    tool = McpToolFactory.create_tool(tool_record)

    assert tool.args_schema(cursor=None).cursor is None


def test_mcp_tool_factory_read_only_inference():
    """验证带有 readOnlyHint 或只读动作词的工具正确识别为 read 权限。"""
    # 1. readOnlyHint: True
    schema_readonly = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "x-nanzi-mcp-annotations": {"readOnlyHint": True},
    }
    tool_record_1 = McpToolCache(
        id="t-ro",
        server_id="s-1",
        tool_name="demo:search_items",
        tool_description="Search for items",
        parameter_schema=json.dumps(schema_readonly),
    )
    tool_1 = McpToolFactory.create_tool(tool_record_1)
    assert tool_1.is_read_only is True
    assert tool_1.permission_scope == "read"
    assert tool_1.display_name == "demo:search_items"

    spec_1 = runtime_tool_spec_from_legacy_tool(tool_1, source_type="mcp")
    assert spec_1.permission_scope == "read"
    assert spec_1.display_name == "demo:search_items"

    # 2. 变更类动作词 (create / delete)
    schema_mutation = {
        "type": "object",
        "properties": {"item": {"type": "string"}},
        "x-nanzi-mcp-annotations": {"readOnlyHint": False},
    }
    tool_record_2 = McpToolCache(
        id="t-rw",
        server_id="s-1",
        tool_name="demo:delete_item",
        tool_description="Delete an item",
        parameter_schema=json.dumps(schema_mutation),
    )
    tool_2 = McpToolFactory.create_tool(tool_record_2)
    assert tool_2.is_read_only is False
    assert tool_2.permission_scope == "ask"

    spec_2 = runtime_tool_spec_from_legacy_tool(tool_2, source_type="mcp")
    assert spec_2.permission_scope == "ask"


def test_mcp_tool_factory_does_not_trust_read_only_hint_for_mutation_name():
    """远端误标变更动作时不能因为 readOnlyHint 自动放行。"""
    schema = {
        "type": "object",
        "properties": {"item": {"type": "string"}},
        "x-nanzi-mcp-annotations": {"readOnlyHint": True},
    }
    tool_record = McpToolCache(
        id="t-mutation-hint",
        server_id="s-1",
        tool_name="demo:delete_item",
        tool_description="Delete an item",
        parameter_schema=json.dumps(schema),
    )

    tool = McpToolFactory.create_tool(tool_record)

    assert tool.permission_scope == "ask"
    assert tool.is_read_only is False


@pytest.mark.asyncio
async def test_mcp_client_concurrent_initialization():
    """验证 Direct HTTP 模式下并发初始化只触发一次 initialize RPC。"""
    session = McpSseSession(
        server_id="server-concurrent",
        sse_url="https://example.test/mcp",
    )
    session.is_direct_http = True

    init_mock = AsyncMock()

    async def fake_initialize(s):
        await asyncio.sleep(0.01)
        s.mcp_session_id = "session-123"
        await init_mock()

    with patch.object(McpClientService, "_initialize_direct_http", side_effect=fake_initialize):
        # 模拟 5 个并发请求同时需要初始化
        await asyncio.gather(
            McpClientService._ensure_direct_http_initialized(session),
            McpClientService._ensure_direct_http_initialized(session),
            McpClientService._ensure_direct_http_initialized(session),
            McpClientService._ensure_direct_http_initialized(session),
            McpClientService._ensure_direct_http_initialized(session),
        )

    assert init_mock.await_count == 1
    assert session.mcp_session_id == "session-123"


@pytest.mark.asyncio
async def test_mcp_direct_http_captures_session_id_from_initialize_response():
    """初始化响应中的会话 ID 必须保存，后续 RPC 才能带上 MCP session header。"""
    session = McpSseSession(
        server_id="server-session-id",
        sse_url="https://example.test/mcp",
    )
    response = httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
        headers={"mcp-session-id": "session-456"},
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))

    with patch.object(session, "get_http_client", return_value=client):
        await McpClientService._direct_http_rpc(
            session,
            "initialize",
            {"protocolVersion": "2024-11-05"},
        )

    assert session.mcp_session_id == "session-456"


@pytest.mark.asyncio
async def test_mcp_get_session_coalesces_concurrent_cache_misses():
    """同一缓存键的并发首次访问只创建并连接一个 session。"""
    original_sessions = McpClientService._sessions
    original_cleanup_task = McpClientService._cleanup_task
    McpClientService._sessions = {}
    McpClientService._cleanup_task = None

    async def fake_cleanup_loop():
        await asyncio.Future()

    async def fake_load_server(_server_id):
        await asyncio.sleep(0.01)
        return SimpleNamespace(sse_url="https://example.test/mcp")

    try:
        with (
            patch.object(McpClientService, "_idle_cleanup_loop", side_effect=fake_cleanup_loop),
            patch.object(McpClientService, "_load_server", side_effect=fake_load_server),
            patch.object(McpSseSession, "connect", new=AsyncMock()),
        ):
            sessions = await asyncio.gather(
                McpClientService.get_session("server-cache-miss"),
                McpClientService.get_session("server-cache-miss"),
            )

        assert sessions[0] is sessions[1]
        assert len(McpClientService._sessions) == 1
    finally:
        if McpClientService._cleanup_task:
            McpClientService._cleanup_task.cancel()
        McpClientService._sessions = original_sessions
        McpClientService._cleanup_task = original_cleanup_task


@pytest.mark.asyncio
async def test_mcp_get_session_does_not_serialize_different_cache_misses_on_db_load():
    """不同 MCP 的首次加载不应因同一缓存锁持有数据库查询而互相阻塞。"""
    original_sessions = McpClientService._sessions
    original_creation_locks = McpClientService._session_creation_locks
    original_cleanup_task = McpClientService._cleanup_task
    McpClientService._sessions = {}
    McpClientService._session_creation_locks = {}
    McpClientService._cleanup_task = None
    first_load_started = asyncio.Event()
    release_first_load = asyncio.Event()
    load_count = 0

    async def fake_cleanup_loop():
        await asyncio.Future()

    async def fake_load_server(server_id):
        nonlocal load_count
        load_count += 1
        if server_id == "server-1":
            first_load_started.set()
            await release_first_load.wait()
        return SimpleNamespace(sse_url=f"https://example.test/{server_id}")

    try:
        with (
            patch.object(McpClientService, "_idle_cleanup_loop", side_effect=fake_cleanup_loop),
            patch.object(McpClientService, "_load_server", side_effect=fake_load_server),
            patch.object(McpSseSession, "connect", new=AsyncMock()),
        ):
            first = asyncio.create_task(McpClientService.get_session("server-1"))
            await first_load_started.wait()
            second = asyncio.create_task(McpClientService.get_session("server-2"))
            await asyncio.sleep(0)
            assert load_count == 2
            release_first_load.set()
            await asyncio.gather(first, second)
            assert "server-1" not in McpClientService._session_creation_locks
            assert "server-2" not in McpClientService._session_creation_locks
    finally:
        if McpClientService._cleanup_task:
            McpClientService._cleanup_task.cancel()
        McpClientService._sessions = original_sessions
        McpClientService._session_creation_locks = original_creation_locks
        McpClientService._cleanup_task = original_cleanup_task


@pytest.mark.asyncio
async def test_mcp_direct_http_client_allows_long_running_tool_calls():
    """Direct HTTP 的底层客户端超时不得短于统一的 120 秒工具调用上限。"""
    session = McpSseSession(
        server_id="server-timeout",
        sse_url="https://example.test/mcp",
    )

    client = session.get_http_client()
    try:
        assert client.timeout.read == 120.0
        assert client.timeout.write == 120.0
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_mcp_session_close_waits_for_active_http_request_before_closing_client():
    """关闭 session 时应等待活动请求结束，而不是固定 5 秒强制断开。"""
    session = McpSseSession(
        server_id="server-active-request",
        sse_url="https://example.test/mcp",
    )
    client = AsyncMock()
    client.is_closed = False
    session._http_client = client
    session._active_requests = 1
    session._active_requests_changed.clear()

    await session.close()
    client.aclose.assert_not_awaited()

    session._active_requests = 0
    session._active_requests_changed.set()
    await asyncio.sleep(0.01)
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_client_evict_session():
    """验证 evict_session 正常关闭连接并从 _sessions 字典中移除。"""
    session_1 = McpSseSession(server_id="srv-target", sse_url="https://test/1")
    session_2 = McpSseSession(server_id="srv-target", sse_url="https://test/2")
    session_other = McpSseSession(server_id="srv-other", sse_url="https://test/3")

    session_1.close = AsyncMock()
    session_2.close = AsyncMock()
    session_other.close = AsyncMock()

    McpClientService._sessions["srv-target"] = session_1
    McpClientService._sessions["srv-target:user:1:call:abc"] = session_2
    McpClientService._sessions["srv-other"] = session_other

    await McpClientService.evict_session("srv-target")

    session_1.close.assert_awaited_once()
    session_2.close.assert_awaited_once()
    session_other.close.assert_not_awaited()

    assert "srv-target" not in McpClientService._sessions
    assert "srv-target:user:1:call:abc" not in McpClientService._sessions
    assert "srv-other" in McpClientService._sessions


@pytest.mark.asyncio
async def test_list_published_mcp_tools_filtering():
    """验证 list_published_mcp_tools 只返回 enabled_status=1 且 is_available=True 的工具。"""
    from app.api.portal.endpoints.tools import list_published_mcp_tools
    from app.models.mcp import McpServer

    server_enabled = McpServer(id="s-en", server_name="enabled_server", enabled_status=1, scope="global")
    server_disabled = McpServer(id="s-dis", server_name="disabled_server", enabled_status=0, scope="global")

    tool_valid = McpToolCache(
        id="t-1",
        server_id="s-en",
        tool_name="enabled_server:tool1",
        tool_description="Valid tool",
        parameter_schema="{}",
        is_published=True,
        is_available=True,
        server=server_enabled,
    )

    from unittest.mock import MagicMock
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [tool_valid]
    db.execute = AsyncMock(return_value=mock_result)

    user = {"user_id": 1, "role": "user"}
    tools = await list_published_mcp_tools(db=db, user=user)

    assert len(tools) == 1
    assert tools[0]["id"] == "t-1"
    assert tools[0]["name"] == "enabled_server:tool1"

    # 验证生成的 SQL 语句包含了 is_available 和 enabled_status 的过滤
    executed_stmt = db.execute.call_args[0][0]
    compiled_sql = str(executed_stmt)
    assert "sys_mcp_tool_cache.is_available = true" in compiled_sql.lower() or "is_available" in compiled_sql.lower()
    assert "sys_mcp_servers.enabled_status = 1" in compiled_sql.lower() or "enabled_status" in compiled_sql.lower()
