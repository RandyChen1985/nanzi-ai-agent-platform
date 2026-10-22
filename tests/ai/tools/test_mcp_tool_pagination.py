"""MCP tools/list 分页、去重、UserContext 同步与空结果保护。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.tools.mcp_client import (
    MAX_REMOTE_TOOL_PAGES,
    McpClientService,
    McpSyncGuardError,
)

pytestmark = pytest.mark.no_infrastructure


class _FakeSseSession:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls: list[str | None] = []

    async def list_tools(self, cursor=None):
        self.calls.append(cursor)
        return self.pages.pop(0)


@pytest.mark.asyncio
async def test_sse_tool_list_follows_next_cursor():
    session = _FakeSseSession(
        [
            SimpleNamespace(tools=["a"], nextCursor="c1"),
            SimpleNamespace(tools=["b"], nextCursor=None),
        ]
    )

    tools = await McpClientService._list_sse_tools(session)

    assert tools == ["a", "b"]
    assert session.calls == [None, "c1"]


@pytest.mark.asyncio
async def test_sse_tool_list_rejects_repeated_cursor():
    session = _FakeSseSession(
        [
            SimpleNamespace(tools=["a"], nextCursor="c1"),
            SimpleNamespace(tools=["b"], nextCursor="c1"),
        ]
    )

    with pytest.raises(RuntimeError, match="重复的 cursor"):
        await McpClientService._list_sse_tools(session)


@pytest.mark.asyncio
async def test_direct_http_tool_list_follows_next_cursor():
    session_mgr = SimpleNamespace()
    rpc = AsyncMock(
        side_effect=[
            {"tools": ["a"], "nextCursor": "c1"},
            {"tools": ["b"]},
        ]
    )
    with patch.object(
        McpClientService, "_ensure_direct_http_initialized", AsyncMock()
    ), patch.object(McpClientService, "_direct_http_rpc", rpc):
        tools = await McpClientService._list_direct_http_tools(session_mgr)

    assert tools == ["a", "b"]
    assert rpc.await_args_list[0].args[2] == {}
    assert rpc.await_args_list[1].args[2] == {"cursor": "c1"}


def test_remote_tool_dedupe_keeps_first_and_drops_duplicates():
    tools = [
        SimpleNamespace(name="a"),
        SimpleNamespace(name="a"),
        SimpleNamespace(name="b"),
        "c",
    ]

    deduped = McpClientService._dedupe_remote_tools(tools)

    assert [McpClientService._remote_tool_name(item) for item in deduped] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_user_assertion_sync_uses_a_short_lived_signed_session():
    server = SimpleNamespace(user_assertion_enabled=True)
    session_mgr = SimpleNamespace(is_direct_http=True, close=AsyncMock())
    get_session = AsyncMock(return_value=session_mgr)

    with patch.object(McpClientService, "_load_server", AsyncMock(return_value=server)), patch(
        "app.services.ai.tools.mcp_client.build_mcp_headers",
        return_value={"X-Nanzi-User-Assertion": "signed"},
    ), patch.object(
        McpClientService, "get_session", get_session
    ), patch.object(
        McpClientService, "_list_direct_http_tools", AsyncMock(return_value=[])
    ), patch.object(
        McpClientService, "_sessions", {}
    ):
        await McpClientService.list_remote_tools("srv", user_info={"user_id": "9"})

    kwargs = get_session.await_args.kwargs
    assert kwargs["session_key"].startswith("srv:sync:user:9:")
    assert kwargs["auth_headers"] == {"X-Nanzi-User-Assertion": "signed"}
    session_mgr.close.assert_awaited_once()


def _sync_db(server, cached_tools):
    server_result = MagicMock()
    server_result.scalar_one_or_none.return_value = server
    cached_result = MagicMock()
    cached_result.scalars.return_value.all.return_value = cached_tools
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[server_result, cached_result])
    db.commit = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = db
    context.__aexit__.return_value = None
    return db, context


@pytest.mark.asyncio
async def test_sync_refuses_empty_remote_result_when_local_tools_exist():
    server = SimpleNamespace(
        id="srv-empty",
        server_name="empty-srv",
        sse_url="https://example.test/mcp",
        enabled_status=1,
    )
    cached = SimpleNamespace(
        tool_name="empty-srv:tool",
        is_available=True,
        is_published=True,
    )
    db, context = _sync_db(server, [cached])

    with patch.object(
        McpClientService, "list_remote_tools", AsyncMock(return_value=[])
    ), patch("app.services.ai.tools.mcp_client.AsyncSessionLocal", return_value=context):
        with pytest.raises(McpSyncGuardError):
            await McpClientService.sync_tools("srv-empty")

    db.commit.assert_not_awaited()
    assert cached.is_available is True


@pytest.mark.asyncio
async def test_sync_force_allows_empty_remote_result():
    server = SimpleNamespace(
        id="srv-force",
        server_name="force-srv",
        sse_url="https://example.test/mcp",
        enabled_status=1,
    )
    cached = SimpleNamespace(
        tool_name="force-srv:tool",
        is_available=True,
        is_published=True,
    )
    db, context = _sync_db(server, [cached])

    with patch.object(
        McpClientService, "list_remote_tools", AsyncMock(return_value=[])
    ), patch("app.services.ai.tools.mcp_client.AsyncSessionLocal", return_value=context):
        result = await McpClientService.sync_tools("srv-force", force=True)

    assert result["remote_deleted_count"] == 1
    assert result["stale_unpublished"] == 1
    assert cached.is_available is False
    db.commit.assert_awaited_once()
