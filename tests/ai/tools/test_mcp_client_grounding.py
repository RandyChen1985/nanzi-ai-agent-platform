from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai.tools.mcp_client import McpClientService


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_mcp_sdk_error_result_raises_instead_of_returning_fact_payload(monkeypatch):
    response = SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text="remote query failed")],
    )
    session_mgr = SimpleNamespace(
        is_direct_http=False,
        session=SimpleNamespace(call_tool=AsyncMock(return_value=response)),
        close=AsyncMock(),
    )
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))

    with pytest.raises(RuntimeError, match="remote query failed"):
        await McpClientService.call_remote_tool("server-1", "get-data", {})


@pytest.mark.asyncio
async def test_mcp_call_uses_user_scoped_session_and_signed_headers(monkeypatch):
    server = SimpleNamespace(
        id="server-1",
        auth_headers='{"Authorization":"Bearer fixed-token"}',
        credential_mode="fixed_token_signed_user",
        user_assertion_enabled=True,
        user_assertion_header="X-Nanzi-User-Assertion",
        user_assertion_audience="mcp:crm",
        user_assertion_key_id="key-1",
    )
    session_mgr = SimpleNamespace(
        is_direct_http=False,
        session=SimpleNamespace(
            call_tool=AsyncMock(return_value=SimpleNamespace(isError=False, content=[]))
        ),
        close=AsyncMock(),
    )
    private_key = object()
    monkeypatch.setattr(McpClientService, "_load_server", AsyncMock(return_value=server))
    monkeypatch.setattr(
        "app.services.ai.tools.mcp_client.build_mcp_headers",
        lambda *args, **kwargs: {
            "Authorization": "Bearer fixed-token",
            "X-Nanzi-User-Assertion": "signed-user",
            "X-Request-ID": "req-1",
        },
    )
    get_session = AsyncMock(return_value=session_mgr)
    monkeypatch.setattr(McpClientService, "get_session", get_session)

    result = await McpClientService.call_remote_tool(
        "server-1",
        "get-data",
        {},
        user_info={"user_id": "123"},
        agent_info={"agent_id": "agent-1"},
        request_id="req-1",
        private_key=private_key,
    )

    assert result == {"success": True, "content": ""}
    get_session.assert_awaited_once()
    call_args = get_session.await_args
    assert call_args.args == ("server-1",)
    assert call_args.kwargs["session_key"].startswith("server-1:user:123:call:")
    assert call_args.kwargs["auth_headers"] == {
        "Authorization": "Bearer fixed-token",
        "X-Nanzi-User-Assertion": "signed-user",
        "X-Request-ID": "req-1",
    }
    assert session_mgr.close.await_count == 1


@pytest.mark.asyncio
async def test_signed_mcp_call_fails_closed_without_runtime_identity(monkeypatch):
    server = SimpleNamespace(
        id="server-1",
        credential_mode="fixed_token_signed_user",
        user_assertion_enabled=True,
    )
    load_server = AsyncMock(return_value=server)
    get_session = AsyncMock()
    monkeypatch.setattr(McpClientService, "_load_server", load_server)
    monkeypatch.setattr(McpClientService, "get_session", get_session)

    with pytest.raises(ValueError, match="authenticated user_id"):
        await McpClientService.call_remote_tool(
            "server-1",
            "get-data",
            {},
            require_user_context=True,
        )

    load_server.assert_awaited_once_with("server-1")
    get_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_call_rejects_disabled_server_before_recreating_session(monkeypatch):
    """旧运行时工具对象不能绕过服务停用状态重新建立 MCP 会话。"""
    server = SimpleNamespace(
        id="server-1",
        enabled_status=0,
        user_assertion_enabled=False,
    )
    load_server = AsyncMock(return_value=server)
    get_session = AsyncMock()
    monkeypatch.setattr(McpClientService, "_load_server", load_server)
    monkeypatch.setattr(McpClientService, "get_session", get_session)

    with pytest.raises(ValueError, match="MCP 服务已禁用"):
        await McpClientService.call_remote_tool(
            "server-1",
            "get-data",
            {},
            require_user_context=True,
        )

    load_server.assert_awaited_once_with("server-1")
    get_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_sdk_exception_is_raised_instead_of_returned_as_text(monkeypatch):
    session_mgr = SimpleNamespace(
        is_direct_http=False,
        session=SimpleNamespace(call_tool=AsyncMock(side_effect=OSError("network down"))),
        close=AsyncMock(),
    )
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))

    with pytest.raises(RuntimeError, match="network down"):
        await McpClientService.call_remote_tool("server-1", "get-data", {})


@pytest.mark.asyncio
async def test_mcp_direct_error_result_raises(monkeypatch):
    session_mgr = SimpleNamespace(is_direct_http=True, mcp_session_id="session-1")
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))
    monkeypatch.setattr(
        McpClientService,
        "_direct_http_rpc",
        AsyncMock(
            return_value={
                "isError": True,
                "content": [{"type": "text", "text": "tool rejected request"}],
            }
        ),
    )

    with pytest.raises(RuntimeError, match="tool rejected request"):
        await McpClientService.call_remote_tool("server-1", "get-data", {})


@pytest.mark.asyncio
async def test_mcp_direct_error_without_content_raises(monkeypatch):
    session_mgr = SimpleNamespace(is_direct_http=True, mcp_session_id="session-1")
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))
    monkeypatch.setattr(
        McpClientService,
        "_direct_http_rpc",
        AsyncMock(return_value={"isError": True, "message": "tool failed"}),
    )

    with pytest.raises(RuntimeError, match="tool failed"):
        await McpClientService.call_remote_tool("server-1", "get-data", {})


@pytest.mark.asyncio
async def test_mcp_successful_empty_content_returns_explicit_success_envelope(monkeypatch):
    response = SimpleNamespace(isError=False, content=[])
    session_mgr = SimpleNamespace(
        is_direct_http=False,
        session=SimpleNamespace(call_tool=AsyncMock(return_value=response)),
        close=AsyncMock(),
    )
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))

    result = await McpClientService.call_remote_tool("server-1", "get-data", {})

    assert result == {"success": True, "content": ""}


@pytest.mark.asyncio
async def test_mcp_direct_empty_success_returns_explicit_success_envelope(monkeypatch):
    session_mgr = SimpleNamespace(is_direct_http=True, mcp_session_id="session-1")
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))
    monkeypatch.setattr(
        McpClientService,
        "_direct_http_rpc",
        AsyncMock(return_value=None),
    )

    result = await McpClientService.call_remote_tool("server-1", "get-data", {})

    assert result == {"success": True, "content": ""}


@pytest.mark.asyncio
async def test_mcp_sdk_structured_content_is_preserved(monkeypatch):
    response = SimpleNamespace(
        isError=False,
        content=[],
        structuredContent={"trains": [{"number": "G1", "price": 661}]},
    )
    session_mgr = SimpleNamespace(
        is_direct_http=False,
        session=SimpleNamespace(call_tool=AsyncMock(return_value=response)),
        close=AsyncMock(),
    )
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))

    result = await McpClientService.call_remote_tool("server-1", "get-data", {})

    assert result == {
        "success": True,
        "content": "",
        "structured_content": {"trains": [{"number": "G1", "price": 661}]},
    }


@pytest.mark.asyncio
async def test_mcp_direct_structured_content_is_preserved(monkeypatch):
    session_mgr = SimpleNamespace(is_direct_http=True, mcp_session_id="session-1")
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))
    monkeypatch.setattr(
        McpClientService,
        "_direct_http_rpc",
        AsyncMock(
            return_value={
                "content": [],
                "structuredContent": {"trains": [{"number": "G1"}]},
            }
        ),
    )

    result = await McpClientService.call_remote_tool("server-1", "get-data", {})

    assert result == {
        "success": True,
        "content": "",
        "structured_content": {"trains": [{"number": "G1"}]},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_http", [False, True])
async def test_mcp_structured_content_allows_null_text_content(monkeypatch, direct_http):
    structured = {"trains": [{"number": "G1"}]}
    if direct_http:
        session_mgr = SimpleNamespace(is_direct_http=True, mcp_session_id="session-1")
        monkeypatch.setattr(
            McpClientService,
            "_direct_http_rpc",
            AsyncMock(return_value={"content": None, "structuredContent": structured}),
        )
    else:
        response = SimpleNamespace(
            isError=False,
            content=None,
            structuredContent=structured,
        )
        session_mgr = SimpleNamespace(
            is_direct_http=False,
            session=SimpleNamespace(call_tool=AsyncMock(return_value=response)),
            close=AsyncMock(),
        )
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=session_mgr))

    result = await McpClientService.call_remote_tool("server-1", "get-data", {})

    assert result == {
        "success": True,
        "content": "",
        "structured_content": structured,
    }
