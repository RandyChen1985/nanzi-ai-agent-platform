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
