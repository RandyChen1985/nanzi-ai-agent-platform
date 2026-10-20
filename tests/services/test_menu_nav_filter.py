"""侧栏导航的裁剪规则（`MenuService.filter_visible` / `resolve_landing`）。

这两条规则直接决定用户能不能看到菜单、以及登录后被送到哪个页面，因此单独钉死。
改造前它们是 `Dashboard.vue` 里的 `hasMenuPerm` + `filteredMenuGroups` 和路由里的
`MENU_HOME_CANDIDATES`，现在收在服务端，行为必须与改造前一致。
"""

from __future__ import annotations

import copy

import pytest

from app.services.menu_service import MenuService, _builtin_nodes_payload

pytestmark = pytest.mark.no_infrastructure


def _codes(nodes) -> list[str]:
    return [node["code"] for node in nodes]


def _payload() -> list[dict]:
    return copy.deepcopy(_builtin_nodes_payload())


def test_workbench_is_visible_to_everyone_because_it_has_no_permission_code():
    """「我的工作台」不带权限码：新用户没有任何菜单授权也必须看得到入口。"""
    nodes = _payload()

    visible = MenuService.filter_visible(nodes, [], is_admin=False)

    assert "menu:workbench" in _codes(visible)
    assert MenuService.resolve_landing(visible) == "/dashboard/workbench"


def test_granted_menu_is_visible_and_ungranted_sibling_is_not():
    nodes = _payload()

    visible = MenuService.filter_visible(nodes, ["menu:ai_chat"], is_admin=False)
    codes = _codes(visible)

    assert "menu:ai_chat" in codes
    assert "group:assistant" in codes
    assert "menu:metadata" not in codes
    assert "group:chatbi" not in codes


def test_group_shows_up_when_only_a_child_is_granted():
    """分组自身的码（如 menu:system）不参与侧栏过滤，子项可见即显示分组标题。"""
    nodes = _payload()

    visible = MenuService.filter_visible(nodes, ["menu:system:users"], is_admin=False)
    codes = _codes(visible)

    assert "group:system" in codes
    assert "menu:system:users" in codes
    assert "menu:system:roles" not in codes


def test_disabling_an_ancestor_hides_the_whole_subtree():
    """停用分组后，即便用户有子菜单权限也不下发——否则会出现「点进去父级已关闭」的怪状态。"""
    nodes = _payload()
    for node in nodes:
        if node["code"] == "group:system":
            node["enabled"] = False

    visible = MenuService.filter_visible(
        nodes,
        ["menu:system:users", "menu:system:roles"],
        is_admin=False,
    )
    codes = _codes(visible)

    assert "group:system" not in codes
    assert "menu:system:users" not in codes


def test_disabling_a_leaf_menu_hides_only_itself():
    nodes = _payload()
    for node in nodes:
        if node["code"] == "menu:metadata":
            node["enabled"] = False

    visible = MenuService.filter_visible(
        nodes,
        ["menu:metadata", "menu:data_sources"],
        is_admin=False,
    )
    codes = _codes(visible)

    assert "menu:metadata" not in codes
    assert "menu:data_sources" in codes


def test_admin_sees_every_menu_but_elements_never_reach_the_sidebar():
    """管理员绕过菜单授权；功能点是页面内部按钮，不属于侧栏节点。"""
    nodes = _payload()

    visible = MenuService.filter_visible(nodes, [], is_admin=True)
    codes = _codes(visible)

    assert "menu:system:roles" in codes
    assert "menu:metadata" in codes
    assert all(not code.startswith("element:") for code in codes)


def test_elements_are_never_returned_as_nav_nodes():
    nodes = _payload()

    visible = MenuService.filter_visible(nodes, ["element:user:edit"], is_admin=False)

    assert all(node["node_type"] != "element" for node in visible)
    assert "menu:system:users" not in _codes(visible)


def test_landing_falls_back_to_the_first_visible_page_when_none_is_marked():
    """`is_landing` 是数据；即便有人把它关掉，也不能让登录落到一个不存在的地址。"""
    nodes = _payload()
    for node in nodes:
        if node["code"] == "menu:workbench":
            node["is_landing"] = False

    visible = MenuService.filter_visible(nodes, [], is_admin=False)

    assert MenuService.resolve_landing(visible) == "/dashboard/workbench"


def test_landing_skips_pages_the_user_cannot_see():
    nodes = _payload()
    # 把落地页换成需要权限的菜单：无授权的用户不能被送到那里
    for node in nodes:
        if node["code"] == "menu:workbench":
            node["is_landing"] = False
        if node["code"] == "menu:ai_chat":
            node["is_landing"] = True

    visible = MenuService.filter_visible(nodes, [], is_admin=False)

    assert MenuService.resolve_landing(visible) == "/dashboard/workbench"
