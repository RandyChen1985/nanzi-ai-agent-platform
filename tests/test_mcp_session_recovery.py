import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.tools.mcp_client import McpClientService, McpSseSession
from httpx import Response

pytestmark = pytest.mark.no_infrastructure

# Mock server info
SERVER_ID = "test-server"
SSE_URL = "http://mock-mcp-server/mcp"
AUTH_HEADERS = {"Authorization": "Bearer test-token"}

@pytest.fixture
def mock_session():
    session = McpSseSession(SERVER_ID, SSE_URL, AUTH_HEADERS)
    session.mcp_session_id = "old-session-id"
    session.is_direct_http = True
    return session

@pytest.mark.asyncio
async def test_session_expired_retry(mock_session):
    # Setup mock for httpx.AsyncClient
    with patch("httpx.AsyncClient") as MockClient:
        # Mock context manager
        mock_client_instance = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client_instance
        
        # Define responses: 
        # 1. 401 SessionExpired
        # 2-3. Reinitialize handshake
        # 4. Retry succeeds
        
        expired_response = Response(401, json={
            "RequestId": "req-1",
            "Code": "SessionExpired",
            "Message": "Session is expired"
        })
        
        initialize_response = Response(
            200,
            headers={"mcp-session-id": "new-session-id"},
            json={"result": {}},
        )
        initialized_response = Response(202)
        success_response = Response(200, json={
            "result": {"tools": []}
        })
        
        mock_client_instance.post.side_effect = [
            expired_response,
            initialize_response,
            initialized_response,
            success_response,
        ]

        # Call the method
        result = await McpClientService._direct_http_rpc(mock_session, "tools/list", {})

        # Verify retry happened (called twice)
        assert mock_client_instance.post.call_count == 4
        
        # Verify first call had session ID
        call_args_list = mock_client_instance.post.call_args_list
        first_call_headers = call_args_list[0].kwargs["headers"]
        assert first_call_headers.get("mcp-session-id") == "old-session-id"
        
        # Verify second call did NOT have session ID (cleared)
        second_call_headers = call_args_list[1].kwargs["headers"]
        assert "mcp-session-id" not in second_call_headers
        assert call_args_list[3].kwargs["headers"]["mcp-session-id"] == "new-session-id"
        
        # Verify result
        assert result == {"tools": []}

@pytest.mark.asyncio
async def test_session_expired_retry_fail(mock_session):
    # Test that it doesn't loop infinitely
    with patch("httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client_instance
        
        expired_response = Response(401, json={
            "RequestId": "req-2",
            "Code": "SessionExpired",
            "Message": "Session is expired"
        })
        
        mock_client_instance.post.return_value = expired_response

        # Expect exception after retries exhausted
        with pytest.raises(Exception) as excinfo:
            await McpClientService._direct_http_rpc(mock_session, "tools/list", {})
        
        # Should call 2 times (initial + 1 retry)
        assert mock_client_instance.post.call_count == 2
        assert "HTTP 401" in str(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 404, 410])
async def test_direct_http_tool_call_reinitializes_after_server_loses_session(
    mock_session,
    status_code,
):
    """服务端重启丢失会话后，应重新握手并只重放一次原工具调用。"""
    expired_response = Response(
        status_code,
        json={"jsonrpc": "2.0", "error": {"code": -32001, "message": "Session not found"}},
    )
    initialize_response = Response(
        200,
        headers={"mcp-session-id": "new-session-id"},
        json={"jsonrpc": "2.0", "id": 2, "result": {}},
    )
    initialized_response = Response(202)
    success_response = Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        },
    )

    with patch.object(
        McpClientService,
        "get_session",
        AsyncMock(return_value=mock_session),
    ), patch("httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.side_effect = [
            expired_response,
            initialize_response,
            initialized_response,
            success_response,
        ]

        result = await McpClientService.call_remote_tool(
            SERVER_ID,
            "lookup",
            {"query": "yunshu"},
        )

    assert result == "ok"
    requests = mock_client_instance.post.call_args_list
    assert [request.kwargs["json"]["method"] for request in requests] == [
        "tools/call",
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert requests[0].kwargs["headers"]["mcp-session-id"] == "old-session-id"
    assert "mcp-session-id" not in requests[1].kwargs["headers"]
    assert requests[3].kwargs["headers"]["mcp-session-id"] == "new-session-id"


@pytest.mark.asyncio
async def test_direct_http_json_rpc_session_error_reinitializes(mock_session):
    """部分 MCP 服务用 HTTP 200 JSON-RPC error 表示旧会话失效。"""
    rpc_error = Response(
        200,
        json={"jsonrpc": "2.0", "id": 2, "error": {"code": -32001, "message": "Session expired"}},
    )
    initialize_response = Response(
        200,
        headers={"mcp-session-id": "new-session-id"},
        json={"jsonrpc": "2.0", "id": 3, "result": {}},
    )
    initialized_response = Response(202)
    success_response = Response(
        200,
        json={"jsonrpc": "2.0", "id": 4, "result": {"tools": []}},
    )

    with patch.object(
        McpClientService,
        "get_session",
        AsyncMock(return_value=mock_session),
    ), patch("httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.side_effect = [
            rpc_error,
            initialize_response,
            initialized_response,
            success_response,
        ]

        result = await McpClientService.list_remote_tools(SERVER_ID)

    assert result == []
    assert [
        request.kwargs["json"]["method"]
        for request in mock_client_instance.post.call_args_list
    ] == ["tools/list", "initialize", "notifications/initialized", "tools/list"]


@pytest.mark.asyncio
async def test_sse_list_tools_reconnects_once_after_transport_failure(mock_session):
    stale_client = MagicMock()
    stale_client.list_tools = AsyncMock(side_effect=ConnectionError("stream closed"))
    fresh_client = MagicMock()
    fresh_client.list_tools = AsyncMock(return_value=MagicMock(tools=["lookup"]))
    mock_session.is_direct_http = False
    mock_session.session = stale_client
    mock_session.close = AsyncMock(side_effect=lambda: setattr(mock_session, "session", None))
    mock_session.connect = AsyncMock(side_effect=lambda: setattr(mock_session, "session", fresh_client))

    with patch.object(
        McpClientService,
        "get_session",
        AsyncMock(return_value=mock_session),
    ):
        tools = await McpClientService.list_remote_tools(SERVER_ID)

    assert tools == ["lookup"]
    mock_session.close.assert_awaited_once()
    mock_session.connect.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_clears_cached_state_even_if_transport_cleanup_fails(mock_session):
    exit_stack = MagicMock()
    exit_stack.aclose = AsyncMock(side_effect=RuntimeError("cancel scope closed"))
    mock_session._exit_stack = exit_stack
    mock_session.session = MagicMock()

    await mock_session.close()

    assert mock_session.session is None
    assert mock_session.mcp_session_id is None
    assert mock_session._exit_stack is None


@pytest.mark.asyncio
async def test_failed_initialized_notification_does_not_reenter_recovery_lock(mock_session):
    initialize_response = Response(
        200,
        headers={"mcp-session-id": "new-session-id"},
        json={"jsonrpc": "2.0", "id": 2, "result": {}},
    )
    notification_error = Response(
        404,
        json={"jsonrpc": "2.0", "error": {"code": -32001, "message": "Session not found"}},
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client_instance
        mock_client_instance.post.side_effect = [initialize_response, notification_error]

        with pytest.raises(Exception, match="HTTP 404"):
            await asyncio.wait_for(
                McpClientService._recover_direct_http_session(
                    mock_session,
                    mock_session.mcp_session_id,
                ),
                timeout=0.2,
            )
