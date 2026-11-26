"""MCP 管理端读接口鉴权：平台（global）服务清单与工具清单对登录用户只读开放。

平台 MCP 页签对所有登录用户可见（只读模式），因此：

- ``GET /servers?scope=global`` 普通用户可读，但响应被**脱敏**（地址、鉴权 Header、
  创建人、探测错误原文不下发）；
- ``GET /servers/{id}/tools`` 普通用户可读（平台服务），个人服务仍只给 owner/admin；
- 使用情况、调用流水（含 tool_input/output 与调用人）继续只给管理员；
- 写操作另有 ``element:mcp_management:*`` 与管理员身份约束，不在本文件范围。
"""
from __future__ import annotations

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
    # 内存对象没有 DB 侧默认值（credential_mode 等要 flush 后才落），显式补齐，
    # 否则 McpServerResponse.model_validate 会因 None 触发校验错误。
    return McpServer(
        id="global-server",
        server_name="mcp-public-admin-crm",
        sse_url="https://crm.example.com/mcp",
        scope="global",
        enabled_status=1,
        credential_mode="static",
        user_assertion_enabled=False,
        user_assertion_header="X-Nanzi-User-Assertion",
        user_assertion_issuer="nanzi-platform",
    )


def _list_db(servers):
    """模拟列表接口的三次查询：服务行、工具计数聚合、待确认变更计数聚合。

    平台服务没有 ``user_id``，因此 ``_mcp_owner_profiles`` 直接返回空、不再查库。
    """
    servers_result = MagicMock()
    servers_result.scalars.return_value.all.return_value = servers
    empty_all = MagicMock()
    empty_all.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[servers_result, empty_all, empty_all])
    return db


@pytest.mark.asyncio
async def test_non_admin_can_list_global_mcp_servers_but_masked():
    """只读用户能枚举平台服务，但拿不到地址、鉴权信息与创建人。"""
    items = await mcp_endpoints.list_mcp_servers(
        scope="global", db=_list_db([_global_server()]), user={"role": "user", "user_id": 1}
    )

    assert len(items) == 1
    item = items[0]
    # 只读可见：标识与运行状态
    assert item.id == "global-server"
    assert item.server_name == "mcp-public-admin-crm"
    assert item.enabled_status == 1
    # 脱敏：地址、鉴权 Header 名、创建人
    assert item.sse_url == ""
    assert item.masked_auth_headers == {}
    assert item.owner_user_name is None
    assert item.owner_display_name is None
    assert item.user_assertion_header == ""


@pytest.mark.asyncio
async def test_admin_list_keeps_connection_fields():
    """管理员视图不受脱敏影响（否则配置页会看不到自己的服务地址）。"""
    items = await mcp_endpoints.list_mcp_servers(
        scope="global", db=_list_db([_global_server()]), user={"role": "admin", "user_id": 1}
    )

    assert items[0].sse_url == "https://crm.example.com/mcp"
    assert items[0].user_assertion_header == "X-Nanzi-User-Assertion"


@pytest.mark.asyncio
async def test_invalid_scope_is_rejected():
    db = MagicMock()
    db.execute = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await mcp_endpoints.list_mcp_servers(
            scope="audit", db=db, user={"role": "user", "user_id": 1}
        )
    assert exc.value.status_code == 400
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_admin_can_read_global_server_tools():
    """平台工具清单（含参数 Schema 与发布状态）对登录用户只读开放。"""
    tools_result = MagicMock()
    tools_result.scalars.return_value.all.return_value = []
    usage_map_result = MagicMock()
    usage_map_result.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=lambda: _global_server()),
            tools_result,
            usage_map_result,
        ]
    )

    result = await mcp_endpoints.list_mcp_server_tools(
        server_id="global-server",
        db=db,
        user={"role": "user", "user_id": 1},
    )
    assert result == []


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
