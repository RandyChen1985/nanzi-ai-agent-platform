"""MCP SSE 会话状态防护回归测试。

覆盖三类曾经会静默劣化的情形：
1. SSE 握手失败后残留半初始化状态，使 connect() 的幂等短路误判「已连接」；
2. SSE 分支直接访问可能为 None 的 session，抛出难以理解的 AttributeError；
3. 网关重连后已降级为 Direct HTTP 时，本次调用直接失败，而不是自动兜底。
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.tools.mcp_client import McpClientService, McpSseSession


pytestmark = pytest.mark.no_infrastructure

SERVER_ID = "guard-server"
SSE_URL = "https://example.test/mcp"


class _FakeAsyncContextManager:
    """最小 async 上下文管理器，用于伪造 sse_client / ClientSession。"""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc_info):
        return False


def _patch_fake_sse(stack, initialize):
    """在给定 ExitStack 上伪造 SSE 建链的外部依赖，只保留 connect() 自身逻辑。"""
    # connect() 现在会先做 TCP 可达性预检，不可达时直接降级、不再尝试 SSE；
    # 本模块只关心 SSE 状态机，伪造端点可达让流程走到 SSE 分支。
    stack.enter_context(
        patch.object(
            McpSseSession, "_tcp_endpoint_reachable", AsyncMock(return_value=True)
        )
    )
    # connect() 现在会先试 Streamable HTTP；本模块只关心 SSE 状态机，直接让它失败。
    stack.enter_context(
        patch(
            "app.services.ai.tools.mcp_client.streamable_http_client",
            MagicMock(side_effect=RuntimeError("no streamable here")),
        )
    )
    stack.enter_context(
        patch(
            "app.services.ai.tools.mcp_client.sse_client",
            MagicMock(return_value=_FakeAsyncContextManager(("read", "write"))),
        )
    )
    stack.enter_context(
        patch(
            "app.services.ai.tools.mcp_client.ClientSession",
            MagicMock(
                return_value=_FakeAsyncContextManager(
                    SimpleNamespace(initialize=initialize)
                )
            ),
        )
    )


@pytest.mark.asyncio
async def test_connect_clears_stale_sse_state_when_handshake_fails():
    """握手失败后必须清空（并发/历史路径留下的）SSE 状态，否则下次 connect() 会被幂等短路误判为已连接。"""
    session = McpSseSession(SERVER_ID, SSE_URL, {"Authorization": "Bearer test"})
    stale_session = SimpleNamespace(name="stale")

    async def _probe_injects_stale_state():
        # 模拟探测期间另一条路径（例如并发 Direct HTTP 初始化）留下的脏状态
        session.session = stale_session
        session.mcp_session_id = "stale-session-id"
        return True

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                session,
                "_looks_like_sse_endpoint",
                AsyncMock(side_effect=_probe_injects_stale_state),
            )
        )
        _patch_fake_sse(stack, AsyncMock(side_effect=RuntimeError("handshake boom")))
        await session.connect()

    assert session.is_direct_http is True
    assert session.session is None
    assert session.mcp_session_id is None
    # connect() 开头的幂等条件不得再被残留状态满足
    assert not (session.session or session.mcp_session_id)


@pytest.mark.asyncio
async def test_connect_does_not_publish_sse_session_before_handshake_completes():
    """握手进行中不得对外发布 SSE session，否则失败后会留下不可用的会话对象。"""
    session = McpSseSession(SERVER_ID, SSE_URL, {"Authorization": "Bearer test"})
    observed = []

    async def _initialize():
        observed.append(session.session)
        raise RuntimeError("handshake boom")

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                session, "_looks_like_sse_endpoint", AsyncMock(return_value=True)
            )
        )
        _patch_fake_sse(stack, AsyncMock(side_effect=_initialize))
        await session.connect()

    assert observed == [None]
    assert session.is_direct_http is True
    assert session.session is None


@pytest.mark.asyncio
async def test_connect_publishes_sse_session_after_successful_handshake():
    """握手成功后才允许发布 SSE 状态。"""
    session = McpSseSession(SERVER_ID, SSE_URL, {"Authorization": "Bearer test"})
    initialize = AsyncMock()

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                session, "_looks_like_sse_endpoint", AsyncMock(return_value=True)
            )
        )
        _patch_fake_sse(stack, initialize)
        await session.connect()

    assert session.is_direct_http is False
    assert session.session is not None
    assert initialize.await_count == 1


@pytest.mark.asyncio
async def test_call_remote_tool_reports_missing_sse_session(monkeypatch):
    """SSE session 缺失时给出明确错误，而不是 'NoneType' has no attribute 'call_tool'。"""
    session_mgr = SimpleNamespace(
        server_id=SERVER_ID,
        is_direct_http=False,
        session=None,
        close=AsyncMock(),
    )
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))

    with pytest.raises(RuntimeError, match="MCP SSE session is unavailable"):
        await McpClientService.call_remote_tool(SERVER_ID, "get-data", {})

    session_mgr.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_remote_tool_falls_back_to_direct_http_after_reconnect_downgrade(monkeypatch):
    """SSE 重连后若网关已降级为 Direct HTTP，本次调用应改走 Direct HTTP 完成，而不是白失败。"""
    sse_session = SimpleNamespace(
        call_tool=AsyncMock(side_effect=ConnectionError("sse dropped"))
    )
    session_mgr = SimpleNamespace(
        server_id=SERVER_ID,
        is_direct_http=False,
        session=sse_session,
        close=AsyncMock(),
    )

    async def _reconnect_switches_to_direct_http():
        session_mgr.is_direct_http = True
        session_mgr.session = None

    session_mgr.connect = AsyncMock(side_effect=_reconnect_switches_to_direct_http)
    direct_http_call = AsyncMock(return_value={"success": True, "content": "via http"})
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))
    monkeypatch.setattr(McpClientService, "_call_direct_http_tool", direct_http_call)

    result = await McpClientService.call_remote_tool(SERVER_ID, "get-data", {})

    assert result == {"success": True, "content": "via http"}
    direct_http_call.assert_awaited_once()
    assert sse_session.call_tool.await_count == 1


@pytest.mark.asyncio
async def test_list_remote_tools_reports_missing_sse_session(monkeypatch):
    """工具列表同样不得对 None session 直接取属性。"""
    session_mgr = SimpleNamespace(is_direct_http=False, session=None)
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))

    with pytest.raises(RuntimeError, match="MCP SSE session is unavailable"):
        await McpClientService.list_remote_tools(SERVER_ID)


@pytest.mark.asyncio
async def test_list_remote_tools_downgrade_after_reconnect_uses_direct_http(monkeypatch):
    """工具列表重连后降级 Direct HTTP 的既有口径保持不变。"""
    sse_session = SimpleNamespace(
        list_tools=AsyncMock(side_effect=ConnectionError("sse dropped"))
    )
    session_mgr = SimpleNamespace(
        server_id=SERVER_ID,
        is_direct_http=False,
        session=sse_session,
        mcp_session_id=None,
        close=AsyncMock(),
    )

    async def _reconnect_switches_to_direct_http():
        session_mgr.is_direct_http = True
        session_mgr.session = None

    session_mgr.connect = AsyncMock(side_effect=_reconnect_switches_to_direct_http)
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))
    monkeypatch.setattr(McpClientService, "_ensure_direct_http_initialized", AsyncMock())
    monkeypatch.setattr(
        McpClientService,
        "_direct_http_rpc",
        AsyncMock(return_value={"tools": [{"name": "ping"}]}),
    )

    tools = await McpClientService.list_remote_tools(SERVER_ID)

    assert tools == [{"name": "ping"}]


@pytest.mark.asyncio
async def test_direct_http_tool_helper_normalizes_structured_content(monkeypatch):
    """Direct HTTP 调用归一化沿用原实现：正文 + 结构化内容一并返回。"""
    session_mgr = SimpleNamespace(is_direct_http=True, mcp_session_id="session-1")
    rpc = AsyncMock(
        return_value={
            "content": [{"type": "text", "text": "hello"}],
            "structuredContent": {"rows": [1]},
        }
    )
    monkeypatch.setattr(McpClientService, "_ensure_direct_http_initialized", AsyncMock())
    monkeypatch.setattr(McpClientService, "_direct_http_rpc", rpc)

    result = await McpClientService._call_direct_http_tool(session_mgr, "get-data", {"q": 1})

    assert result == {
        "success": True,
        "content": "hello",
        "structured_content": {"rows": [1]},
    }
    rpc.assert_awaited_once_with(
        session_mgr,
        "tools/call",
        {"name": "get-data", "arguments": {"q": 1}},
    )


@pytest.mark.asyncio
async def test_direct_http_tool_helper_maps_null_result_to_empty_success(monkeypatch):
    session_mgr = SimpleNamespace(is_direct_http=True, mcp_session_id="session-1")
    monkeypatch.setattr(McpClientService, "_ensure_direct_http_initialized", AsyncMock())
    monkeypatch.setattr(McpClientService, "_direct_http_rpc", AsyncMock(return_value=None))

    result = await McpClientService._call_direct_http_tool(session_mgr, "get-data", {})

    assert result == {"success": True, "content": ""}


@pytest.mark.asyncio
async def test_direct_http_tool_helper_raises_on_error_payload(monkeypatch):
    session_mgr = SimpleNamespace(is_direct_http=True, mcp_session_id="session-1")
    monkeypatch.setattr(McpClientService, "_ensure_direct_http_initialized", AsyncMock())
    monkeypatch.setattr(
        McpClientService,
        "_direct_http_rpc",
        AsyncMock(
            return_value={
                "isError": True,
                "content": [{"type": "text", "text": "remote boom"}],
            }
        ),
    )

    with pytest.raises(RuntimeError, match="remote boom"):
        await McpClientService._call_direct_http_tool(session_mgr, "get-data", {})
