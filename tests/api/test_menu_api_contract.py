"""菜单管理 API 的契约：路由、权限门槛与外链校验。

菜单是「谁能看到什么」的唯一入口，所以这层测试关注三件事：
1. 读导航只要登录（否则用户登进来没有侧栏）；
2. 权限树与写操作要有权限（否则普通用户能改别人的菜单）；
3. 外链地址必须过协议白名单（javascript: 之类绝不能被存进库再渲染成 href）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.portal.endpoints.menus import router as menu_router

ENDPOINT = Path("app/api/portal/endpoints/menus.py")
SERVICE = Path("app/services/menu_service.py")
pytestmark = pytest.mark.no_infrastructure


def _menu_routes() -> dict[str, set[str]]:
    """menus 子路由内的相对路径（前缀 `/menus` 由 api.py 的 include_router 提供）。"""
    routes: dict[str, set[str]] = {}
    for route in menu_router.routes:
        path = getattr(route, "path", "")
        routes.setdefault(path, set()).update(getattr(route, "methods", set()) or set())
    return routes


def test_all_menu_routes_are_registered_under_the_portal_router():
    routes = _menu_routes()

    assert {"GET"} <= routes["/nav"]
    assert {"GET"} <= routes["/tree"]
    assert {"GET"} <= routes["/catalog"]
    assert {"GET", "POST"} <= routes[""]
    assert {"PUT", "DELETE"} <= routes["/{node_id}"]
    assert {"POST"} <= routes["/reorder"]
    assert {"POST"} <= routes["/reset"]


def test_nav_only_requires_login():
    """`/nav` 是登录后的第一跳，不能要求任何菜单权限，否则没有授权的用户进不来。"""
    source = ENDPOINT.read_text(encoding="utf-8")
    nav_block = source[source.index('@router.get("/nav")') : source.index('@router.get("/tree")')]

    assert "require_api_key" in nav_block
    assert "require_permission" not in nav_block


def test_permission_tree_is_admin_scoped_but_catalog_is_not():
    """权限树要管理员/角色编辑权限；个人中心的骨架只读、登录即可。"""
    source = ENDPOINT.read_text(encoding="utf-8")

    tree_block = source[source.index('@router.get("/tree")') : source.index('@router.get("/catalog")')]
    assert "element:role:edit" in tree_block
    assert "menu:system:menus" in tree_block

    catalog_block = source[source.index('@router.get("/catalog")') : source.index('@router.get("")')]
    assert "require_api_key" in catalog_block
    assert "require_permission" not in catalog_block


def test_write_endpoints_require_the_menu_edit_permission():
    source = ENDPOINT.read_text(encoding="utf-8")

    for marker in (
        '@router.post("", ',
        '@router.put("/{node_id}")',
        '@router.delete("/{node_id}"',
        '@router.post("/reorder")',
        '@router.post("/reset")',
    ):
        block = source[source.index(marker) : source.index(marker) + 400]
        assert "element:menu:edit" in block, f"{marker} 缺少 element:menu:edit 门槛"


def test_external_url_validation_rejects_executable_schemes():
    """外链最终会进入 href 与 iframe src，协议白名单必须在服务端强制。"""
    source = SERVICE.read_text(encoding="utf-8")

    assert "ALLOWED_EXTERNAL_SCHEMES" in source
    assert "_validate_external_url" in source
    # 必须按前缀白名单放行，而不是「非空就存」——后者会把 javascript: 写进 href
    assert "startswith(scheme)" in source
    assert "外链菜单必须填写地址" in source


def test_builtin_nodes_cannot_be_deleted_and_identity_fields_are_immutable():
    """内置项只允许改展示属性：改 path/code 会让代码里的路由与权限校验脱钩。"""
    source = SERVICE.read_text(encoding="utf-8")
    update_block = source[source.index("async def update_node"):]
    update_block = update_block[: update_block.index("async def delete_node")]

    assert "内置菜单的路由地址不可修改" in update_block
    # Update 模型不接受 code / node_type：身份字段从接口层就改不了
    assert "path" in update_block

    delete_block = source[source.index("async def delete_node"):]
    delete_block = delete_block[: delete_block.index("async def reorder")]
    assert "row.builtin" in delete_block
    assert "内置菜单不可删除" in delete_block
    assert "只允许删除外链菜单" in delete_block


def test_group_nodes_must_stay_at_root_and_cycles_are_rejected():
    source = SERVICE.read_text(encoding="utf-8")

    assert "_normalize_parent" in source
    assert "_assert_not_cycle" in source
    assert "分组只能挂在最外层" in source
    assert "父级必须是分组" in source


def test_renaming_an_external_permission_code_moves_grants_with_it():
    """外链 slug 改名后授权要跟着走，否则「改了标识符 = 所有人丢权限」。"""
    source = SERVICE.read_text(encoding="utf-8")

    assert "_rename_permission_code" in source
    assert "menu:ext:" in source
    # 授权行必须一起搬，否则改完 slug 所有人都看不到菜单
    assert "UPDATE ai_agent_resource_permissions" in source


def test_empty_custom_groups_can_be_deleted_but_never_with_children():
    """误建的分组要能清掉，但绝不能连带删子树——一次误点会带走一批菜单与授权。"""
    source = SERVICE.read_text(encoding="utf-8")
    delete_block = source[source.index("async def delete_node"):]
    delete_block = delete_block[: delete_block.index("async def reorder")]

    assert "NODE_GROUP" in delete_block
    # 先查子节点，非空直接拒绝
    assert "AgentMenu.parent_id == row.id" in delete_block
    assert "该分组下还有菜单，请先移走或删除子项" in delete_block
    # 内置项仍然不可删（判断在分组分支之前）
    assert delete_block.index("内置菜单不可删除") < delete_block.index("NODE_GROUP")
