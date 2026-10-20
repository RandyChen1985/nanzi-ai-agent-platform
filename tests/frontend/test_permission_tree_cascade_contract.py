"""权限树的层级表达与「菜单 → 功能点」级联（契约）。

历史背景：角色页与用户页各自内联一套卡片式权限树。用户页的「界面访问」改成可折叠
两层树之后，渲染搬到 `components/user/MenuAccessTree.vue`，级联算法搬到纯函数
`applyMenuToggle()`（`utils/permissionLists.ts`）——所以这条契约按**职责**拆开断言：

- 角色页：仍内联渲染，断言按原样保留；
- 用户页：只负责「取子项 id + 调纯函数 + 写直配缓冲」，渲染断言指向树组件。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLES_VUE = "frontend/src/views/Roles.vue"
USERS_VUE = "frontend/src/views/Users.vue"
TREE_COMPONENT = "frontend/src/components/user/MenuAccessTree.vue"

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_roles_page_cascades_menu_selection_to_child_elements():
    """角色页保持内联级联：勾菜单同时勾全部子项。"""
    source = _source(ROLES_VUE)

    assert "getMenuDescendantIds" in source
    assert "isMenuPartiallySelected" in source
    assert "descendantIds" in source
    assert "permissionData.value.elements" in source
    assert "menu.children" in source


def test_users_page_cascades_through_the_shared_pure_function():
    """用户页的级联走纯函数（勾选时跳过角色已授予的子项），不再内联复制一份。"""
    users = _source(USERS_VUE)

    assert "getMenuDescendantIds" in users, "父层仍负责取子项 id"
    assert "applyMenuToggle(" in users, "级联必须复用共享纯函数"
    assert "permissionData.value.elements" in users, "写回直配缓冲"
    assert "inheritedElements" in users, "级联需要知道哪些子项已由角色授予"

    component = _source(TREE_COMPONENT)
    assert "menu.children" in component, "子项渲染在树组件里"


def test_roles_page_explains_menu_and_element_permission_levels():
    """角色页保留两级的文案与图标说明。"""
    source = _source(ROLES_VUE)

    assert "菜单权限" in source
    assert "功能点权限" in source
    assert 'viewBox="0 0 24 24"' in source
    assert "isMenuPartiallySelected(menu.id)" in source


def test_users_page_explains_levels_inside_the_tree_component():
    """用户页的层级说明随渲染一起搬到树组件。"""
    component = _source(TREE_COMPONENT)

    assert "菜单权限" in component
    assert "功能点权限" in component
    assert 'viewBox="0 0 24 24"' in component
    assert "isMenuPartiallySelected(menu.id)" in component
