"""数据源 / MCP 权限码拆分后的接线契约（源码级）。

这两处改动都涉及「后端端点要求什么码」与「权限树里能授出什么码」的一致性：

- 数据源连接管理从 `element:metadata:import` 拆成 view / edit / delete 三个码；
  其中列表、测试连接、SQL 预览三个端点被元数据导入弹窗复用，必须允许
  「数据源权限 或 元数据导入权限」，否则只勾了导入的角色一进导入弹窗就 403。
- MCP 工具集的写端点此前只校验登录，现按动作加码。

行为层的验证靠真实请求（tests/api/portal 其它用例），这里只锁装饰器口径，
防止后续改动悄悄放宽或收紧。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
METADATA = ROOT / "app/api/portal/endpoints/metadata.py"
MCP = ROOT / "app/api/portal/endpoints/mcp.py"

pytestmark = pytest.mark.no_infrastructure


def _route_dependencies(path: Path) -> dict[str, str]:
    """提取 `{函数名: 装饰器文本}`，装饰器跨行时拼回一行。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    routes: dict[str, str] = {}
    for index, line in enumerate(lines):
        if not line.startswith("@router."):
            continue
        end = index
        buffer = line
        while buffer.count("(") > buffer.count(")") and end + 1 < len(lines):
            end += 1
            buffer += lines[end]
        match = re.match(r"async def (\w+)", lines[end + 1]) if end + 1 < len(lines) else None
        if match:
            routes[match.group(1)] = buffer
    return routes


def _single(code: str) -> str:
    return f'require_permission("element", "{code}")'


def _menu(code: str) -> str:
    return f'require_permission("menu", "{code}")'


def _either(code: str) -> str:
    return (
        f'require_any_permission(("element", "{code}"), '
        f'("element", "element:metadata:import"))'
    )


def test_data_source_read_endpoints_follow_the_menu_permission():
    """只读能力跟菜单走：有「数据源管理」菜单就能看列表与表画像。

    产品口径（用户明确要求）：勾了菜单即隐含只读，功能点只管写操作，
    与 MCP 工具集一致，避免「能进页面却看不到任何数据」。
    """
    routes = _route_dependencies(METADATA)

    for func in [
        "list_db_table_profiles",
        "get_db_table_profile_detail",
        "get_db_table_profile_related",
        "get_db_table_profile_stats",
        "get_db_table_profiling_task",
    ]:
        assert _menu("menu:data_sources") in routes[func], func
        assert "element:data_sources:view" not in routes[func], func


def test_data_source_write_endpoints_require_edit():
    routes = _route_dependencies(METADATA)

    for func in [
        "create_db_connection_config",
        "update_db_connection_config",
        "trigger_db_table_profiling",
        "cancel_db_table_profiling",
        "import_preview_from_profiles",
        "toggle_table_profile_ignore",
    ]:
        assert _single("element:data_sources:edit") in routes[func], func


def test_data_source_delete_endpoint_requires_delete():
    routes = _route_dependencies(METADATA)

    assert _single("element:data_sources:delete") in routes["delete_db_connection_config"]


def test_import_flow_endpoints_accept_either_source_or_import_permission():
    """元数据导入弹窗也会调这三个端点，只勾「智能导入数据」的角色必须能用。"""
    routes = _route_dependencies(METADATA)

    assert 'require_any_permission(("menu", "menu:data_sources"), ("element", "element:metadata:import"))' in routes["list_db_connection_configs"]
    assert _either("element:data_sources:edit") in routes["test_db_connection"]


def test_previewing_table_data_only_needs_the_menu_permission():
    """预览表数据是只读操作：有「数据源管理」菜单即可，不需要 edit。

    产品口径（用户明确要求）：只看数据不该拦；这个接口同时承载「SQL 调试」，
    所以两者一起放开到菜单权限。
    """
    routes = _route_dependencies(METADATA)

    assert (
        'require_any_permission(("menu", "menu:data_sources"), '
        '("element", "element:data_sources:edit"), ("element", "element:metadata:import"))'
    ) in routes["preview_db_connection_sql"]


def test_metadata_import_endpoints_are_not_affected_by_the_split():
    """导入 / 表结构读取仍归元数据导入码，别被顺手改掉。"""
    routes = _route_dependencies(METADATA)

    for func in ["import_ddl", "list_db_tables", "get_db_ddl"]:
        assert _single("element:metadata:import") in routes[func], func
        assert "element:data_sources:" not in routes[func], func


def test_mcp_write_endpoints_are_gated_per_action():
    routes = _route_dependencies(MCP)

    expected = {
        "create_mcp_server": "create",
        "verify_mcp_server": "edit",
        "create_echo_test_mcp": "edit",
        "update_mcp_server": "edit",
        "delete_mcp_server": "delete",
        "sync_mcp_tools": "sync",
        "execute_mcp_tool": "execute",
        "toggle_tool_publish": "publish",
    }
    for func, action in expected.items():
        assert _single(f"element:mcp_management:{action}") in routes[func], func


def test_mcp_read_endpoints_stay_on_menu_permission():
    """读接口仍由菜单权限控制，不额外加元素码，避免列个服务都要特殊授权。"""
    routes = _route_dependencies(MCP)

    for func in [
        "list_mcp_servers",
        "get_mcp_server_usage",
        "list_mcp_server_tools",
        "get_mcp_server_outbound_logs",
        "get_all_mcp_outbound_logs",
    ]:
        assert "require_permission" not in routes[func], func
