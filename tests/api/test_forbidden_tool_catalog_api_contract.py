"""禁用工具目录接口：内置优先、动态并入、按 id 去重。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.portal.endpoints import tools as tools_endpoint


pytestmark = pytest.mark.no_infrastructure


def _db_with(api_tools, mcp_tools):
    """按调用顺序依次返回自定义 API 工具与 MCP 工具的结果集。"""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: api_tools)),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: mcp_tools)),
        ]
    )
    return db


@pytest.mark.asyncio
async def test_catalog_endpoint_returns_builtin_first_then_dynamic_candidates():
    api_tool = SimpleNamespace(name="fetch_weather", description="天气查询", group_name="外部服务")
    mcp_tool = SimpleNamespace(
        tool_name="crm:query",
        tool_description="CRM 查询",
        server=SimpleNamespace(server_name="CRM", scope="global"),
    )

    response = await tools_endpoint.list_forbidden_tool_catalog(
        _db_with([api_tool], [mcp_tool]),
        {"role": "admin", "user_id": 1},
    )

    ids = [candidate["id"] for candidate in response["candidates"]]
    assert ids[0] == "exec_command"
    # 内置候选恒在最前；动态候选（API → 脚本 → MCP）并入其后。这里不写死
    # ids[-2:]：每新增一个内置脚本工具都会改变动态段长度，断言会假红。
    assert "fetch_weather" in ids and "crm:query" in ids
    assert ids.index("fetch_weather") < ids.index("crm:query")
    assert response["candidates"][0]["aliases"] == ["exec_command", "Bash", "bash"]


@pytest.mark.asyncio
async def test_catalog_endpoint_keeps_builtin_entry_when_dynamic_id_collides():
    api_tool = SimpleNamespace(name="read_file", description="同名自定义工具", group_name=None)

    response = await tools_endpoint.list_forbidden_tool_catalog(
        _db_with([api_tool], []),
        {"role": "admin", "user_id": 1},
    )

    read_file_entries = [
        candidate for candidate in response["candidates"] if candidate["id"] == "read_file"
    ]
    assert len(read_file_entries) == 1
    assert read_file_entries[0]["category"] == "file"
