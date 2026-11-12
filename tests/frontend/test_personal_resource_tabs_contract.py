"""「我的技能 / 我的 MCP」单 Tab 时的顶部 tab 契约。

普通用户进入这两页时，一级 Tab 只有一枚（我的技能 / 我的 MCP），却还挂着一条
200px 的左侧二级导航——一个 Tab 撑起一整列。这里锁住改后的形态：

- 没有平台管理权限时，一级 Tab 行整行不出现；
- 二级视图（我的技能 / 收到的分享 / 我的分享）用 `variant="tabs"` 直接当顶部 tab，
  样式与「智能体中心」一致（选中 `bg-primary/10 text-primary`，不是下划线）；
- 左侧栏只在管理员保留；管理员的多 Tab 形态不受影响。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SKILL_VIEW = ROOT / "frontend/src/views/SkillsManagement.vue"
MCP_VIEW = ROOT / "frontend/src/views/McpManagement.vue"
SKILL_MENU = ROOT / "frontend/src/components/skill/SkillPersonalMenu.vue"
MCP_MENU = ROOT / "frontend/src/components/mcp/McpPersonalMenu.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [SKILL_MENU, MCP_MENU])
def test_menu_component_supports_tabs_variant(path: Path) -> None:
    source = _read(path)
    assert "variant?: 'aside' | 'chips' | 'tabs'" in source
    assert "variant === 'tabs'" in source
    # 与智能体中心同一套选中样式
    assert "bg-primary/10 text-primary" in source


def test_skill_page_hides_single_tab_bar_and_uses_top_tabs() -> None:
    view = _read(SKILL_VIEW)
    # 一级 Tab 行只在平台管理员时出现
    assert 'v-if="!personalOnly && canManagePlatformSkills"' in view
    # 普通用户：二级视图直接当顶部 tab
    assert 'v-if="!personalOnly && !canManagePlatformSkills && activeWorkspaceTab === \'personal\'"' in view
    assert 'variant="tabs"' in view
    # 左侧栏只给管理员
    assert 'v-if="activeWorkspaceTab === \'personal\' && canManagePlatformSkills"' in view


def test_mcp_page_hides_single_tab_bar_and_uses_top_tabs() -> None:
    view = _read(MCP_VIEW)
    assert 'v-if="!personalOnly && isAdmin"' in view
    assert 'v-if="!personalOnly && !isAdmin && activeScope === \'personal\'"' in view
    assert 'variant="tabs"' in view
    # 左侧栏与内容区胶囊都只在管理员出现，避免与顶部 tabs 重复
    assert view.count('v-if="isAdmin"') >= 2


def test_admin_multi_tab_form_is_untouched() -> None:
    """管理员仍走原形态：多枚一级 Tab + 左侧二级栏。"""
    skill = _read(SKILL_VIEW)
    assert 'id="tab-global-skills"' in skill
    assert 'id="tab-skill-publication-review"' in skill
    assert 'id="tab-skill-publications"' in skill
    mcp = _read(MCP_VIEW)
    assert 'id="tab-global-mcp"' in mcp
    assert 'id="tab-audit-logs"' in mcp

def test_personal_menu_aside_can_collapse() -> None:
    """「个人技能」左栏要能折叠。

    内容区加了分组页签后，200px 侧栏在小屏上会把卡片挤成一条；折叠状态由父级持有
    （宽度变化影响右侧布局，且要跨刷新记住），组件只负责渲染两态并回传点击。
    """
    menu = _read(SKILL_MENU)
    view = _read(SKILL_VIEW)

    # 组件：两态宽度 + 折叠开关 + 无障碍属性
    assert "collapsed?: boolean" in menu
    assert "toggle-collapsed" in menu
    assert "w-[64px]" in menu and "w-[200px]" in menu
    assert "aria-expanded" in menu
    # 折叠后只剩图标，角标必须换成一个点，否则 64px 会被撑开
    assert "collapsed && badgeText(item.badge)" in menu
    # 折叠态下每个按钮要有 title/aria-label，否则只剩图标等于没有文字
    assert ':title="collapsed ? item.label : undefined"' in menu

    # 页面：状态 + 持久化 + 接线
    assert "const personalMenuCollapsed = ref(readPersonalMenuCollapsed())" in view
    assert "PERSONAL_MENU_COLLAPSED_KEY" in view
    assert "localStorage.setItem(PERSONAL_MENU_COLLAPSED_KEY" in view
    assert ':collapsed="personalMenuCollapsed"' in view
    assert '@toggle-collapsed="personalMenuCollapsed = !personalMenuCollapsed"' in view
