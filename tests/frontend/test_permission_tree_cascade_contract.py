"""权限树的层级表达与「菜单 → 页面 → 功能点」勾选（契约）。

历史演进：角色页与用户页原先各自内联一套卡片式权限树。用户页的「界面访问」
改成可折叠树之后渲染搬到 `components/user/MenuAccessTree.vue`，勾选算法搬到纯函数
（`utils/permissionLists.ts`）。2026-10-20 用户反馈「无法只勾菜单、不勾功能点」与
「勾上的功能点取消后行为反复」之后，勾选拆成两个显式动作：

- `applyMenuEntryToggle()`：只切菜单/页面入口（取消时清掉它的直配子项）；
- `applyMenuCascadeToggle()`：菜单行「全选 / 清空」按钮才级联整棵子树。

所以这条契约按**职责**拆开断言：

- 两页都只负责「取后代 id + 调纯函数 + 写自己的缓冲」；
- 渲染、层级表达与两个动作的按钮都在树组件 + 递归节点组件里。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLES_VUE = "frontend/src/views/Roles.vue"
USERS_VUE = "frontend/src/views/Users.vue"
TREE_COMPONENT = "frontend/src/components/user/MenuAccessTree.vue"
TREE_NODE = "frontend/src/components/user/MenuTreeNode.vue"

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_roles_page_reuses_the_shared_tree_and_cascade():
    """角色页不再内联一份树：渲染用组件，入口切换与显式级联都用共享纯函数。"""
    source = _source(ROLES_VUE)

    assert "components/user/MenuAccessTree.vue" in source
    assert "applyMenuEntryToggle(" in source, "点复选框只切入口"
    assert "applyMenuCascadeToggle(" in source, "「全选 / 清空」走显式级联"
    assert "getMenuDescendantIds" in source
    assert "permissionData.value.elements" in source
    assert "isItemSelected" not in source, "旧的单选判定应随内联树一起删除"


def test_users_page_cascades_through_the_shared_pure_function():
    """用户页两种动作都走纯函数（级联跳过角色已授予的子项），不再内联复制一份。"""
    users = _source(USERS_VUE)

    assert "getMenuDescendantIds" in users, "父层仍负责取后代 id"
    assert "applyMenuEntryToggle(" in users, "勾菜单只授予入口"
    assert "applyMenuCascadeToggle(" in users, "「全选 / 清空」必须复用共享纯函数"
    assert "toggleMenuDescendants" in users, "父层要接住级联事件并写回缓冲"
    assert "permissionData.value.elements" in users, "写回直配缓冲"
    assert "inheritedElements" in users, "级联需要知道哪些子项已由角色授予"

    component = _source(TREE_COMPONENT)
    assert "menu.children" in component, "子层渲染在树组件里"
    assert "toggle-menu-descendants" in component, "树要发出显式级联事件"


def test_tree_renders_submenu_level_between_menu_and_elements():
    """子菜单（页面）在视觉与结构上都与功能点区分开。"""
    component = _source(TREE_COMPONENT)
    node = _source(TREE_NODE)

    assert "MenuTreeNode" in component, "子层交给递归节点组件"
    assert "MenuTreeNode" in node, "节点组件自递归，层级深度不受限"
    assert "页面" in node, "子菜单行要有「页面」标识"
    assert "countLeafElements" in component, "卡片上要显示功能点数量（不含子菜单）"
    assert "countGrantedLeafElements" in component, "还要显示已授予 M/N，才能看出只给了入口"


def test_tree_separates_entry_checkbox_from_cascade_button():
    """菜单行必须同时有「入口复选框」与「全选/清空」按钮，两个动作不能混。"""
    component = _source(TREE_COMPONENT)
    node = _source(TREE_NODE)

    for source in (component, node):
        assert "isPermissionNodeSelfGranted" in source, "复选框只看节点自身"
        assert "isPermissionNodeGranted" in source, "「全选 / 清空」文案看整棵子树"
        assert "全选" in source, "要有显式全选入口（否则勾菜单只剩入口会让人以为权限丢了）"
        assert "清空" in source
        assert "toggle-menu-descendants" in source

    assert "只授予菜单入口" in component, "卡片标题要说明勾选语义，避免再被理解成「勾菜单=全选」"


def test_roles_page_explains_menu_and_page_permission_levels():
    """层级说明（菜单 / 页面 / 功能点）随渲染一起收进组件。"""
    component = _source(TREE_COMPONENT)

    assert "菜单权限" in component
    assert "页面" in component
    assert "功能点权限" in component
    assert 'viewBox="0 0 24 24"' in component
    assert "isPartial(menu)" in component


def test_users_page_explains_levels_inside_the_tree_component():
    """用户页的层级说明同样在组件里，页面本身不再重复一份文案。"""
    component = _source(TREE_COMPONENT)

    assert "菜单权限" in component
    assert "功能点权限" in component
    assert 'viewBox="0 0 24 24"' in component
    assert ":indeterminate=" in component
