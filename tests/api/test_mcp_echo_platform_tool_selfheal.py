"""回归：平台 Echo 的工具行被改名后，创建入口必须按 tool_id 自愈而不是重复插入。

事故背景：编辑平台 Echo 会把它改名（前端 bug），`_migrate_server_name_references`
连带把工具标识从 `NanZi Echo 测试 MCP:echo` 改成新前缀；再次点「创建 Echo 测试 MCP」
时按常量名查不到旧行，代码用同一个 tool_id 新建 → 主键冲突 500。
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.portal.endpoints import mcp
from app.models.mcp import McpServer, McpToolCache


pytestmark = pytest.mark.no_infrastructure


def _one(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


def _platform_echo_server(server_name: str) -> McpServer:
    return McpServer(
        id=mcp.ECHO_SERVER_ID,
        server_name=server_name,
        sse_url="https://nanzi.example.com/mcp/echo/mcp",
        auth_headers="{}",
        credential_mode="fixed_token_signed_user",
        fixed_token_encrypted="enc-token",
        user_assertion_enabled=True,
        user_assertion_header="X-Nanzi-User-Assertion",
        user_assertion_audience=f"mcp:{mcp.ECHO_SERVER_ID}",
        user_assertion_key_id="echo-key",
        user_assertion_issuer="nanzi-platform",
        user_assertion_private_key_encrypted="enc-key",
        enabled_status=1,
        scope="global",
    )


@pytest.mark.asyncio
async def test_platform_echo_reuses_renamed_tool_row_instead_of_inserting_same_primary_key():
    tool_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "nanzi:mcp:echo:tool:echo"))
    server = _platform_echo_server("mcp-public-admin-NanZi-Echo-MCP")
    renamed_tool = McpToolCache(
        id=tool_id,
        server_id=mcp.ECHO_SERVER_ID,
        tool_name="mcp-public-admin-NanZi-Echo-MCP:echo",
        tool_description=mcp.ECHO_TOOL_DESCRIPTION,
        parameter_schema=mcp.echo_tool_schema(),
        is_published=True,
        is_available=True,
    )
    db = MagicMock()
    # server 查询 → 按常量名查工具（查不到）→ 按 tool_id 兜底（命中旧行）
    db.execute = AsyncMock(
        side_effect=[
            _one(server),
            _one(None),
            _one(renamed_tool),
            # 平台 Echo 的进度测试工具（echo_slow）：尚未注册 → 走插入。
            _one(None),
        ]
    )
    db.commit = AsyncMock()

    with patch.object(mcp, "_clear_runtime_tool_cache"), patch.object(
        mcp, "get_api_key_manager", return_value=MagicMock()
    ):
        response = await mcp.create_echo_test_mcp(
            SimpleNamespace(base_url="https://nanzi.example.com/"),
            db,
            {"role": "admin"},
        )

    assert response.id == mcp.ECHO_SERVER_ID
    assert server.server_name == mcp.ECHO_SERVER_NAME
    assert renamed_tool.tool_name == mcp.ECHO_TOOL_NAME
    assert not any(
        isinstance(call.args[0], McpToolCache) and call.args[0].id == renamed_tool.id
        for call in db.add.call_args_list
    ), "命中旧工具行时不能再插入同一个 tool_id"
