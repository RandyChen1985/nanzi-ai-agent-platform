"""MCP 管理端读接口鉴权：公共服务的清单/工具/使用情况/调用日志只给管理员。

此前这些接口只依赖 require_api_key：任意登录用户都能枚举公共 MCP，并读取其他用户
调用工具时的入参、出参与调用人。个人服务继续只对 owner/admin 开放。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import mcp as mcp_endpoints
from app.models.mcp import McpServer

pytestmark = pytest.mark.no_infrastructure


def _db_with_server(server):
    result = MagicMock()
    result.scalar_one_or_none.return_value = server
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _global_server() -> McpServer:
    return McpServer(
        id="global-server",
        server_name="mcp-public-admin-crm",
        sse_url="https://crm.example.com/mcp",
        scope="global",
        enabled_status=1,
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_list_global_mcp_servers():
    db = MagicMock()
    db.execute = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await mcp_endpoints.list_mcp_servers(
            scope="global", db=db, user={"role": "user", "user_id": 1}
        )
    assert exc.value.status_code == 403
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_admin_cannot_read_global_server_tools():
    with pytest.raises(HTTPException) as exc:
        await mcp_endpoints.list_mcp_server_tools(
            server_id="global-server",
            db=_db_with_server(_global_server()),
            user={"role": "user", "user_id": 1},
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_cannot_read_global_server_usage():
    with pytest.raises(HTTPException) as exc:
        await mcp_endpoints.get_mcp_server_usage(
            server_id="global-server",
            db=_db_with_server(_global_server()),
            user={"role": "user", "user_id": 1},
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_cannot_read_global_server_outbound_logs():
    """调用流水含 tool_input/output 与调用人，是最敏感的一条。"""
    with pytest.raises(HTTPException) as exc:
        await mcp_endpoints.get_mcp_server_outbound_logs(
            server_id="global-server",
            db=_db_with_server(_global_server()),
            user={"role": "user", "user_id": 1},
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_personal_owner_is_allowed_but_another_user_is_not():
    owner = {"role": "user", "user_id": 7}
    other = {"role": "user", "user_id": 8}
    personal = McpServer(
        id="personal-server",
        server_name="mcp-private-alice-crm",
        sse_url="https://crm.example.com/mcp",
        scope="personal",
        user_id=7,
        enabled_status=1,
    )

    # owner 通过权限检查后会继续查工具；用空结果收尾
    tools_result = MagicMock()
    tools_result.scalars.return_value.all.return_value = []
    usage_map_result = MagicMock()
    usage_map_result.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=lambda: personal),
            tools_result,
            usage_map_result,
        ]
    )
    result = await mcp_endpoints.list_mcp_server_tools(
        server_id="personal-server", db=db, user=owner
    )
    assert result == []

    with pytest.raises(HTTPException) as exc:
        await mcp_endpoints.list_mcp_server_tools(
            server_id="personal-server",
            db=_db_with_server(personal),
            user=other,
        )
    assert exc.value.status_code == 403
