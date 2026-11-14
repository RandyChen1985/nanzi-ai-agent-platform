from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.mcp import McpServer, McpToolCache
from app.services.ai.tools.mcp_client import McpClientService


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_sync_tools_unpublishes_tools_removed_from_remote_server():
    server = McpServer(
        id="server-sync",
        server_name="sync-server",
        sse_url="https://example.test/mcp",
        enabled_status=1,
    )
    stale_tool = McpToolCache(
        id="stale-tool",
        server_id=server.id,
        tool_name="sync-server:removed_tool",
        tool_description="No longer remote",
        parameter_schema='{"type":"object"}',
        is_published=True,
        is_available=True,
    )
    live_tool = SimpleNamespace(
        name="live_tool",
        description="Still available",
        inputSchema={"type": "object"},
        annotations=None,
    )

    server_result = MagicMock()
    server_result.scalar_one_or_none.return_value = server
    cached_result = MagicMock()
    cached_result.scalars.return_value.all.return_value = [stale_tool]
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.execute.side_effect = [server_result, cached_result]
    db_context = AsyncMock()
    db_context.__aenter__.return_value = db
    db_context.__aexit__.return_value = None

    async def invalidate_after_commit():
        db.commit.assert_awaited_once()

    with patch("app.services.mcp.tool_cache_revision.advance_tool_revision",
               AsyncMock(side_effect=invalidate_after_commit)) as notify, patch.object(
        McpClientService,
        "list_remote_tools",
        AsyncMock(return_value=[live_tool]),
    ), patch(
        "app.services.ai.tools.mcp_client.AsyncSessionLocal",
        return_value=db_context,
    ):
        result = await McpClientService.sync_tools(server.id)

    assert result["stale_unpublished"] == 1
    assert stale_tool.is_published is False
    assert stale_tool.is_available is False
    assert db.add.call_count == 1
    db.commit.assert_awaited_once()
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_tools_does_not_reenable_disabled_server():
    server = McpServer(
        id="disabled-server-sync",
        server_name="disabled-sync-server",
        sse_url="https://example.test/mcp",
        enabled_status=0,
    )
    live_tool = SimpleNamespace(
        name="live_tool",
        description="Still available",
        inputSchema={"type": "object"},
        annotations=None,
    )

    server_result = MagicMock()
    server_result.scalar_one_or_none.return_value = server
    cached_result = MagicMock()
    cached_result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[server_result, cached_result])
    db.commit = AsyncMock()
    db_context = AsyncMock()
    db_context.__aenter__.return_value = db
    db_context.__aexit__.return_value = None

    with patch.object(
        McpClientService,
        "list_remote_tools",
        AsyncMock(return_value=[live_tool]),
    ), patch(
        "app.services.ai.tools.mcp_client.AsyncSessionLocal",
        return_value=db_context,
    ):
        await McpClientService.sync_tools(server.id)

    assert server.enabled_status == 0
