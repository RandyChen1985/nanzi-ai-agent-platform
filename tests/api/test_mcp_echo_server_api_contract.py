from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import mcp
from app.models.mcp import McpServer, McpToolCache


pytestmark = pytest.mark.no_infrastructure


def test_echo_test_mcp_creation_route_is_available_and_not_a_user_server():
    route = next(
        route
        for route in mcp.router.routes
        if getattr(route, "path", None) == "/servers/echo-test"
    )

    assert "POST" in route.methods
    assert mcp.ECHO_SERVER_ID
    assert mcp.ECHO_SERVER_NAME == "NanZi Echo 测试 MCP"


def test_echo_creation_contract_is_global_idempotent_and_write_only():
    source = Path("app/api/portal/endpoints/mcp.py").read_text(encoding="utf-8")

    assert "personal_echo_server_id(" in source
    assert 'scope: str = Query("global"' in source
    assert 'is_personal = scope == "personal"' in source
    assert "fixed_token_encrypted" in source
    assert "user_assertion_private_key_encrypted" in source
    assert "ECHO_TOOL_NAME" in source
    assert "echo_tool_schema()" in source
    assert "只有系统管理员才能创建平台 Echo 测试 MCP" in source
    assert "固定 Token 与签名私钥只加密保存" in source
    assert "fixed_token" not in mcp.McpServerResponse.model_fields
    assert "user_assertion_private_key_encrypted" not in mcp.McpServerResponse.model_fields


def test_main_mounts_echo_streamable_http_mcp():
    source = Path("app/main.py").read_text(encoding="utf-8")

    # mcp 2.x：端点参数挪到 streamable_http_app()，统一走封装工厂
    assert "echo_mcp_http_app()" in source
    assert 'app.mount("/mcp/echo"' in source
    assert "echo_mcp_lifespan" in source
    assert "async with echo_mcp_lifespan()" in source


def test_echo_transport_security_reads_app_public_url():
    source = Path("app/services/mcp/echo_server.py").read_text(encoding="utf-8")

    # mcp 2.x 起由模块级常量持有该对象，streamable_http_app() 时传入；
    # 启动/保存配置后由 refresh_mcp_public_url() 原地刷新。
    assert "_ECHO_TRANSPORT_SECURITY = mutable_transport_security(settings.APP_PUBLIC_URL)" in source
    assert "transport_security=_ECHO_TRANSPORT_SECURITY" in source

    main_source = Path("app/main.py").read_text(encoding="utf-8")
    assert "refresh_mcp_public_url()" in main_source


@pytest.mark.asyncio
async def test_echo_creation_is_admin_only_and_returns_no_credentials():
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    manager = MagicMock()
    manager.encrypt_api_key.return_value = "encrypted-value"

    # 未配置对外地址时才回退到当前请求地址（系统配置 app_public_url 优先）
    with patch.object(
        mcp, "get_app_public_url", AsyncMock(return_value="")
    ), patch.object(mcp, "get_api_key_manager", return_value=manager), patch.object(
        mcp, "_clear_runtime_tool_cache"
    ):
        response = await mcp.create_echo_test_mcp(
            SimpleNamespace(base_url="https://nanzi.example.com/"),
            db,
            {"role": "admin"},
        )

    assert response.id == mcp.ECHO_SERVER_ID
    assert response.sse_url == "https://nanzi.example.com/mcp/echo/mcp"
    assert response.scope == "global"
    assert response.credential_mode == "fixed_token_signed_user"
    assert response.user_assertion_enabled is True
    assert response.tool_count == 2  # echo + echo_slow（进度验收工具）
    assert "fixed_token_encrypted" not in response.model_dump()
    assert "user_assertion_private_key_encrypted" not in response.model_dump()
    added = [call.args[0] for call in db.add.call_args_list]
    assert any(isinstance(item, McpServer) for item in added)
    assert any(isinstance(item, McpToolCache) and item.is_published for item in added)
    assert manager.encrypt_api_key.call_count == 2


@pytest.mark.asyncio
async def test_echo_creation_prefers_configured_app_public_url():
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
    )
    db.commit = AsyncMock()
    manager = MagicMock()
    manager.encrypt_api_key.return_value = "encrypted-value"

    with patch.object(
        mcp, "get_app_public_url", AsyncMock(return_value="https://mcp.example.com")
    ), patch.object(mcp, "get_api_key_manager", return_value=manager), patch.object(
        mcp, "_clear_runtime_tool_cache"
    ):
        response = await mcp.create_echo_test_mcp(
            SimpleNamespace(base_url="http://proxy.internal:8001/"),
            db,
            {"role": "admin"},
        )

    assert response.sse_url == "https://mcp.example.com/mcp/echo/mcp"


@pytest.mark.asyncio
async def test_echo_creation_rejects_non_admin_before_database_access():
    db = MagicMock()
    db.execute = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await mcp.create_echo_test_mcp(
            SimpleNamespace(base_url="https://nanzi.example.com"),
            db,
            {"role": "user", "user_id": 123},
        )

    assert exc_info.value.status_code == 403
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_echo_creation_reuses_existing_credentials_and_tool():
    server = McpServer(
        id=mcp.ECHO_SERVER_ID,
        server_name="旧 Echo 名称",
        sse_url="http://old/mcp",
        auth_headers="{}",
        credential_mode="static",
        fixed_token_encrypted="existing-token",
        user_assertion_enabled=False,
        user_assertion_header="X-Nanzi-User-Assertion",
        user_assertion_audience="mcp:existing",
        user_assertion_key_id="existing-key",
        user_assertion_issuer="old-issuer",
        user_assertion_private_key_encrypted="existing-private-key",
        enabled_status=0,
        scope="global",
    )
    tool = McpToolCache(
        id="tool-1",
        server_id=mcp.ECHO_SERVER_ID,
        tool_name=mcp.ECHO_TOOL_NAME,
        tool_description="old",
        parameter_schema="{}",
        is_published=False,
        is_available=False,
    )
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: server),
            SimpleNamespace(scalar_one_or_none=lambda: tool),
            # 第三次查询是平台 Echo 的进度测试工具（echo_slow）：尚未注册 → 走插入。
            SimpleNamespace(scalar_one_or_none=lambda: None),
        ]
    )
    manager = MagicMock()

    with patch.object(mcp, "get_api_key_manager", return_value=manager), patch.object(
        mcp, "_clear_runtime_tool_cache"
    ):
        response = await mcp.create_echo_test_mcp(
            SimpleNamespace(base_url="https://nanzi.example.com"),
            db,
            {"role": "admin"},
        )

    assert response.enabled_status == 1
    assert server.fixed_token_encrypted == "existing-token"
    assert server.user_assertion_private_key_encrypted == "existing-private-key"
    assert server.credential_mode == "fixed_token_signed_user"
    assert tool.is_published is True
    assert tool.is_available is True
    manager.encrypt_api_key.assert_not_called()


def test_personal_echo_id_and_url_are_derived_from_user():
    from app.services.mcp.echo_server import (
        ECHO_SERVER_ID,
        build_echo_server_url,
        is_echo_server_sse_url,
        personal_echo_server_id,
    )

    personal_id = personal_echo_server_id(42)
    assert personal_id == personal_echo_server_id("42")
    assert personal_id != ECHO_SERVER_ID
    assert build_echo_server_url("https://nanzi.example.com/", personal_id) == (
        f"https://nanzi.example.com/mcp/echo/{personal_id}/mcp"
    )
    assert build_echo_server_url("https://nanzi.example.com/", ECHO_SERVER_ID) == (
        "https://nanzi.example.com/mcp/echo/mcp"
    )
    assert is_echo_server_sse_url(f"https://nanzi.example.com/mcp/echo/{personal_id}/mcp")
    assert not is_echo_server_sse_url("https://business.example.com/mcp")


@pytest.mark.asyncio
async def test_personal_echo_creation_uses_owner_scoped_id_and_name():
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    db.commit = AsyncMock()
    manager = MagicMock()
    manager.encrypt_api_key.return_value = "encrypted-value"

    with patch.object(
        mcp, "get_app_public_url", AsyncMock(return_value="")
    ), patch.object(mcp, "get_api_key_manager", return_value=manager), patch.object(
        mcp, "_clear_runtime_tool_cache"
    ), patch(
        "app.services.permission_service.PermissionService"
    ) as permission_service:
        permission_service.return_value.check_permission = AsyncMock(return_value=True)
        response = await mcp.create_echo_test_mcp(
            SimpleNamespace(base_url="https://nanzi.example.com/"),
            db,
            {"role": "user", "user_id": 7, "user_name": "alice"},
            "personal",
        )

    expected_id = mcp.personal_echo_server_id(7)
    assert response.id == expected_id
    assert response.scope == "personal"
    assert response.user_id == 7
    assert response.server_name == "mcp-private-alice-echo"
    assert response.sse_url == f"https://nanzi.example.com/mcp/echo/{expected_id}/mcp"
    assert response.user_assertion_audience == f"mcp:{expected_id}"
    assert response.credential_mode == "fixed_token_signed_user"
    assert "fixed_token_encrypted" not in response.model_dump()
    assert manager.encrypt_api_key.call_count == 2
    permission_service.return_value.check_permission.assert_awaited_once_with(
        7, "element", "element:mcp_management:create"
    )
    added = [call.args[0] for call in db.add.call_args_list]
    tool = next(item for item in added if isinstance(item, McpToolCache))
    assert tool.tool_name == "mcp-private-alice-echo:echo"


@pytest.mark.asyncio
async def test_personal_echo_migrates_legacy_shared_tool_name():
    """旧版个人 Echo 复用了平台 Echo 的工具名，重建入口时要就地改名。"""
    server = McpServer(
        id=mcp.personal_echo_server_id(7),
        server_name="mcp-private-admin-echo",
        remark="个人 Echo 测试 MCP",
        sse_url="https://nanzi.example.com/mcp/echo/x/mcp",
        auth_headers="{}",
        credential_mode="fixed_token_signed_user",
        fixed_token_encrypted="existing-token",
        user_assertion_enabled=True,
        user_assertion_header="X-Nanzi-User-Assertion",
        user_assertion_audience="mcp:x",
        user_assertion_key_id="existing-key",
        user_assertion_issuer="nanzi-platform",
        user_assertion_private_key_encrypted="existing-private-key",
        enabled_status=1,
        scope="personal",
        user_id=7,
    )
    legacy = McpToolCache(
        id="tool-legacy",
        server_id=server.id,
        tool_name=mcp.ECHO_TOOL_NAME,
        tool_description="old",
        parameter_schema="{}",
        is_published=True,
        is_available=True,
    )
    db = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: server),
            SimpleNamespace(scalar_one_or_none=lambda: None),
            SimpleNamespace(scalar_one_or_none=lambda: legacy),
        ]
    )

    with patch.object(mcp, "get_api_key_manager", return_value=MagicMock()), patch.object(
        mcp, "_clear_runtime_tool_cache"
    ), patch(
        "app.services.conversation_resource_service.get_redis",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = await mcp.create_echo_test_mcp(
            SimpleNamespace(base_url="https://nanzi.example.com"),
            db,
            {"role": "admin", "user_id": 7},
            "personal",
        )

    assert legacy.tool_name == "mcp-private-admin-echo:echo"
    assert response.id == server.id
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_personal_echo_creation_requires_create_permission():
    db = MagicMock()
    db.execute = AsyncMock()

    with patch(
        "app.services.permission_service.PermissionService"
    ) as permission_service:
        permission_service.return_value.check_permission = AsyncMock(return_value=False)
        with pytest.raises(HTTPException) as exc_info:
            await mcp.create_echo_test_mcp(
                SimpleNamespace(base_url="https://nanzi.example.com"),
                db,
                {"role": "user", "user_id": 7},
                "personal",
            )

    assert exc_info.value.status_code == 403
    assert "没有创建个人 MCP 服务的权限" in exc_info.value.detail
    db.execute.assert_not_called()
