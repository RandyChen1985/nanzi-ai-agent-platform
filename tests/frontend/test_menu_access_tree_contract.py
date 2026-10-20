"""用户权限「界面访问」树组件的契约（源码级 + 真渲染）。

用户诉求演进：
1. 卡片式列表看不出层级 → 改成可折叠的缩进树；
2. 编辑用户时看不到哪些权限已由所属角色授予 → 节点内标「角色」并只读；
3. 子菜单（用户管理/角色管理/系统配置/审计日志）和功能点长得一样 → 改成三层，
   子菜单行带「页面」标识、功能点缩进挂在它下面。

组件只负责展示与 emit，**不持有任何写通道**——状态机与 PUT 组装都留在父层。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend/src/components/user/MenuAccessTree.vue"
TREE_NODE = ROOT / "frontend/src/components/user/MenuTreeNode.vue"
CONSTANTS = ROOT / "frontend/src/constants/permissions.ts"
USERS = ROOT / "frontend/src/views/Users.vue"
SSR_SCRIPT = ROOT / "tests/frontend/menu_access_tree_ssr.cjs"

pytestmark = pytest.mark.no_infrastructure


def _node_ready() -> bool:
    if shutil.which("node") is None:
        return False
    node_modules = ROOT / "frontend/node_modules"
    return all(
        (node_modules / name).exists() for name in ("vue", "esbuild", "@vue/server-renderer")
    )


def _component() -> str:
    assert COMPONENT.exists(), "用户权限树组件不存在：frontend/src/components/user/MenuAccessTree.vue"
    return COMPONENT.read_text(encoding="utf-8")


def _tree_node() -> str:
    assert TREE_NODE.exists(), "递归节点组件不存在：frontend/src/components/user/MenuTreeNode.vue"
    return TREE_NODE.read_text(encoding="utf-8")


def test_component_declares_direct_and_inherited_inputs():
    """直配与继承必须分成两组 prop：直配可提交，继承只读。"""
    source = _component()

    for name in ("menus", "elements", "inheritedMenus", "inheritedElements", "disabled"):
        assert name in source, f"组件缺少 prop：{name}"
    assert "defineProps" in source
    assert "withDefaults" in source, "disabled 需要默认值，避免调用方漏传时树被锁死"


def test_component_only_emits_and_never_writes():
    """组件不得读写权限数据或发请求：它只 emit，写通道在父层。"""
    source = _component()

    assert "defineEmits" in source
    for event in ("toggle-menu", "toggle-element", "inherited-blocked"):
        assert event in source, f"组件缺少 emit：{event}"
    assert "permissionData" not in source, "组件不该直接操作父层的权限缓冲"
    assert "axios" not in source, "组件不该自己发请求"
    assert "api/portal" not in source


def test_component_renders_collapsible_indented_tree():
    """缩进树：菜单行带折叠按钮与 aria-expanded，子层用竖线表达父子关系。"""
    source = _component()

    assert "MENU_TREE" in source
    assert "menu.children" in source, "子层必须遍历 menu.children"
    assert "aria-expanded" in source, "折叠按钮要有可访问状态"
    assert "collapsed" in source
    assert "border-l" in source, "层级需要竖线表达父子关系"


def test_tree_renders_submenu_level_between_menu_and_elements():
    """三层：一级菜单 → 子菜单（页面）→ 功能点，子层交给递归节点组件。"""
    component = _component()
    node = _tree_node()
    constants = CONSTANTS.read_text(encoding="utf-8")

    assert "MenuTreeNode" in component, "子层应交给递归节点组件渲染"
    assert "MenuTreeNode" in node, "节点组件必须能自递归，才能支持任意层级"
    assert "页面" in node, "子菜单行要有「页面」标识，和功能点区分开"
    assert "countLeafElements" in component, "卡片右上角要能显示功能点数量（子菜单不计数）"

    # 数据结构层面：子菜单自己挂功能点，而不是和功能点平铺
    assert "children: [" in constants
    assert "menu:system:users" in constants
    assert "label: '用户管理'" in constants


def test_component_marks_inherited_nodes_as_read_only():
    """角色来源节点：灰勾 + 「角色」徽标 + 只读拦截事件。"""
    source = _component()
    node = _tree_node()

    assert "角色" in source or "角色" in node, "缺少「角色」来源徽标"
    assert "hasDirectGrantIn" in node, "判定「可编辑」要看自己或子树有没有直配"
    assert "hasInheritedGrantIn" in node, "判定「只读」要看自己或子树有没有继承"
    assert "inherited-blocked" in node, "点只读节点要能通知父层提示"


def test_component_renders_three_state_checkboxes():
    """勾选框需要「全选 / 部分 / 未选」三态，部分态用 indeterminate。"""
    source = _component()
    node = _tree_node()

    assert ":indeterminate=" in source
    assert "isPartial(menu)" in source
    assert ":indeterminate=" in node, "子菜单也要有三态，否则看不出它的功能点只勾了一部分"


def test_component_keeps_permission_level_copy():
    """保留「菜单权限 / 页面 / 功能点权限」的三级说明，避免树形化后丢失语义。"""
    source = _component()

    assert "菜单权限" in source
    assert "功能点权限" in source
    assert "页面" in source
    assert 'viewBox="0 0 24 24"' in source


def test_users_page_delegates_rendering_to_the_tree_component():
    """用户页只接线：引入组件、传直配与继承、处理三种事件。"""
    source = USERS.read_text(encoding="utf-8")

    assert "components/user/MenuAccessTree.vue" in source
    assert "MenuAccessTree" in source
    assert 'v-model' not in source.split("MenuAccessTree", 1)[1][:400], (
        "树是单向数据流（props in / emit out），不应使用 v-model 双向绑定"
    )


def test_rendered_tree_marks_inherited_nodes_and_keeps_three_states():
    """真渲染：三层都出来、子菜单带「页面」标识、继承节点灰化并带「角色」徽标。"""
    if not _node_ready():
        pytest.skip("node / vue SSR 依赖不可用")

    proc = subprocess.run(
        ["node", str(SSR_SCRIPT)],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    payload = json.loads(proc.stdout)
    checks = payload["checks"]
    assert checks["rendersAllTopLevelMenus"], "顶层菜单没有全部渲染"
    assert checks["keepsLevelLegend"], "菜单权限 / 页面 / 功能点权限的级别说明丢了"
    assert checks["collapseButtonsMatchMenusWithChildren"], "有子项的菜单都要有折叠按钮且默认展开"
    assert checks["rendersRoleBadges"], "角色授予的菜单与功能点都要有「角色」徽标"
    assert checks["inheritedMenuRowIsDimmed"], "纯继承的菜单行要淡化，和可编辑行区分开"
    assert checks["inheritedElementIsDimmed"], "纯继承的功能点要淡化"
    assert checks["directElementIsChecked"], "直配的功能点仍要正常显示为已勾选"
    assert checks["rendersSubmenuPageBadges"], "4 个子菜单都要带「页面」标识：%s" % payload["pageBadgeCount"]
    assert checks["indentsThirdLevel"], "子菜单下的功能点要再缩进一层"
    assert checks["disabledPropagates"], "disabled 必须传到可访问状态上"
    assert checks["noWriteChannelInMarkup"], "渲染结果里不该出现父层的权限缓冲"
