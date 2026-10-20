"""用户权限「界面访问」树组件的契约（源码级 + 真渲染）。

用户诉求：卡片式列表看不出「菜单 → 功能点」的层级；且编辑用户时看不到哪些页面
已由所属角色授予。树组件负责两件事：可折叠的两层缩进树、角色来源节点的只读标注。
它**不持有任何写通道**——状态机与 PUT 组装都留在父层，组件只 emit 事件。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend/src/components/user/MenuAccessTree.vue"
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


def test_component_renders_two_level_tree_with_collapse():
    """两层缩进树：菜单行带折叠按钮与 aria-expanded，子项缩进并用竖线连接。"""
    source = _component()

    assert "MENU_TREE" in source
    assert "menu.children" in source, "子项必须遍历 menu.children"
    assert "aria-expanded" in source, "折叠按钮要有可访问状态"
    assert "collapsed" in source
    assert "border-l" in source, "二级层级需要竖线表达父子关系"


def test_component_marks_inherited_nodes_as_read_only():
    """角色来源节点：灰勾 + 「角色」徽标 + 只读拦截事件。"""
    source = _component()

    assert "角色" in source, "缺少「角色」来源徽标"
    assert "isMenuInheritedOnly" in source
    assert "isInheritedOnly" in source
    assert "inherited-blocked" in source
    # MENU_TREE 的子项里混有 menu:* 子菜单（用户管理/角色管理）：判定必须两边都查
    assert "props.menus.includes(id) || props.elements.includes(id)" in source


def test_component_renders_three_state_checkboxes():
    """菜单勾选框需要「全选 / 部分 / 未选」三态，部分态用 indeterminate。"""
    source = _component()

    assert ":indeterminate=" in source
    assert "isMenuPartiallySelected(menu.id)" in source


def test_component_keeps_permission_level_copy():
    """保留「菜单权限 / 功能点权限」的级别说明，避免树形化后丢失语义。"""
    source = _component()

    assert "菜单权限" in source
    assert "功能点权限" in source
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
    """真渲染：两级都出来、继承节点灰化并带「角色」徽标、禁用态可下发。"""
    if not _node_ready():
        pytest.skip("node / vue SSR 依赖不可用")

    proc = subprocess.run(
        ["node", str(SSR_SCRIPT)],
        cwd=ROOT / "frontend",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    checks = json.loads(proc.stdout)["checks"]
    assert checks["rendersAllTopLevelMenus"], "顶层菜单没有全部渲染"
    assert checks["keepsLevelLegend"], "菜单权限 / 功能点权限的级别说明丢了"
    assert checks["collapseButtonsMatchMenusWithChildren"], "有子项的菜单都要有折叠按钮且默认展开"
    assert checks["rendersRoleBadges"], "角色授予的菜单与功能点都要有「角色」徽标"
    assert checks["inheritedMenuRowIsDimmed"], "纯继承的菜单行要淡化，和可编辑行区分开"
    assert checks["inheritedElementIsDimmed"], "纯继承的功能点要淡化"
    assert checks["directElementIsChecked"], "直配的功能点仍要正常显示为已勾选"
    assert checks["disabledPropagates"], "disabled 必须传到可访问状态上"
    assert checks["noWriteChannelInMarkup"], "渲染结果里不该出现父层的权限缓冲"

