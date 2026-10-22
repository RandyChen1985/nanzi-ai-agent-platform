"""回归：保存 MCP 配置后必须先开会话缓存再同步。

事故背景：`get_session()` 在建立连接时缓存 `sse_url` 与解密后的 Header，唯一兜底
是 300s 空闲回收。`update_mcp_server` 保存后直接 `sync_tools()`，改地址或轮换 Token
后仍会连旧主机、带旧凭据，最长 5 分钟。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.portal.endpoints import mcp


pytestmark = pytest.mark.no_infrastructure


def _one(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


def _scalar(value):
    return SimpleNamespace(scalar=lambda: value)


@pytest.mark.asyncio
async def test_update_evicts_cached_session_before_resync():
    server = mcp.McpServer(
        id="srv-1",
        server_name="mcp-public-admin-a",
        sse_url="https://old.example.com/mcp",
        auth_headers="{}",
        credential_mode="static",
        enabled_status=1,
        scope="global",
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[_one(server), _one(None), _scalar(0), _scalar(0), _scalar(0)]
    )
    db.commit = AsyncMock()

    calls: list[tuple[str, str]] = []

    async def fake_evict(server_id: str) -> None:
        calls.append(("evict", server_id))

    async def fake_sync(server_id: str, **_kwargs) -> dict:
        calls.append(("sync", server_id))
        return {}

    data = mcp.McpServerWrite(
        server_name="mcp-public-admin-a",
        sse_url="https://new.example.com/mcp",
    )
    with patch.object(mcp, "_clear_runtime_tool_cache"), patch.object(
        mcp.McpClientService, "evict_session", fake_evict
    ), patch.object(mcp.McpClientService, "sync_tools", fake_sync):
        await mcp.update_mcp_server("srv-1", data, db, {"role": "admin"})

    assert calls == [("evict", "srv-1"), ("sync", "srv-1")], (
        "必须先淘汰旧会话（旧 URL / 旧 Header）再同步，否则同步仍走旧连接"
    )
