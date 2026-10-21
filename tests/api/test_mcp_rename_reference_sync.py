"""服务改名时，工具缓存 / 智能体版本 / 会话资源三处引用必须一起迁移。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.portal.endpoints import mcp
from app.models.mcp import McpToolCache
from app.services.conversation_resource_service import ConversationResourceService


pytestmark = pytest.mark.no_infrastructure


def _scalars_all(rows):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


def _tool(tool_name: str = "old-name:echo") -> McpToolCache:
    return McpToolCache(
        id="tool-1",
        server_id="srv-1",
        tool_name=tool_name,
        tool_description="echo",
        parameter_schema="{}",
        is_published=True,
        is_available=True,
    )


@pytest.mark.asyncio
async def test_rename_reports_tool_ids_so_conversation_mounts_can_follow():
    """改名只改库不够：会话资源里存的旧工具名要拿 id 去纠。"""
    tool = _tool()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_scalars_all([tool]), _scalars_all([])])

    renamed = await mcp._migrate_server_name_references(
        db, "srv-1", "old-name", "new-name"
    )

    assert tool.tool_name == "new-name:echo"
    assert renamed == [("tool-1", "new-name:echo")]


@pytest.mark.asyncio
async def test_rename_sync_is_scoped_to_owner_for_personal_servers():
    rewrite = AsyncMock(return_value=1)
    with patch.object(
        ConversationResourceService, "rewrite_mcp_tool_name", new=rewrite
    ):
        await mcp._sync_renamed_tools_to_conversations(
            [("tool-1", "new-name:echo")], owner_id=7
        )

    rewrite.assert_awaited_once_with(
        owner_id=7, tool_id="tool-1", new_name="new-name:echo"
    )


@pytest.mark.asyncio
async def test_rename_sync_uses_full_scan_for_platform_servers():
    rewrite = AsyncMock(return_value=1)
    with patch.object(
        ConversationResourceService, "rewrite_mcp_tool_name", new=rewrite
    ):
        await mcp._sync_renamed_tools_to_conversations(
            [("tool-1", "new-name:echo")], owner_id=None
        )

    rewrite.assert_awaited_once_with(
        owner_id=None, tool_id="tool-1", new_name="new-name:echo"
    )
