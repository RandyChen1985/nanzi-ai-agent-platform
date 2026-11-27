"""「我的技能 / 我的 MCP」页的一级 Tab 与二级导航形态契约。

平台技能 / 平台 MCP 页签现在对所有登录用户可见（无写权限时只读），因此一级 Tab
行不再只给管理员：

- 技能页：一级 Tab 行按 `!personalOnly` 渲染；「平台技能」对所有人可见，
  「待审核」「已发布」仍然只给 `element:skills:admin`；
- MCP 页：一级 Tab 行按 `!personalOnly` 渲染；普通用户的二级视图（我的 MCP /
  收到的分享 / 我的分享 / 平台服务）在控制台与个人中心（`personalOnly`）下统一用
  `variant="tabs"` 顶部 tab，避免在个人中心被深链困在「收到的分享」；左侧栏与内容区
  胶囊只在管理员出现；
- 个人中心 / 嵌入式个人资源面板（`personalOnly`）下不出现一级 Tab 行。
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


def test_skill_page_keeps_tab_bar_for_every_logged_in_user() -> None:
    view = _read(SKILL_VIEW)
    tab_bar = view.split("<!-- Scope Tab 切换", 1)[1].split(
        "<!-- 已发布 / 下架管理面板", 1
    )[0]
    # 一级 Tab 行只按 personalOnly 收起
    assert 'v-if="!personalOnly"' in tab_bar
    # 「待审核」「已发布」两个管理页签仍然要 element:skills:admin
    assert 'v-if="canManagePlatformSkills"' in tab_bar
    # 普通用户不再掉进「单 Tab 胶囊」特例状态
    assert (
        "!personalOnly && !canManagePlatformSkills && activeWorkspaceTab === 'personal'"
        not in view
    )
    # 二级导航：非个人中心页面一律有左侧栏（个人中心保持原形态）
    assert (
        "activeWorkspaceTab === 'personal' && (canManagePlatformSkills || !personalOnly)"
        in view
    )


def test_mcp_page_keeps_tab_bar_for_every_logged_in_user() -> None:
    view = _read(MCP_VIEW)
    # 一级 Tab 行只按 personalOnly 收起
    assert (
        'v-if="!personalOnly"\n      class="flex flex-shrink-0 items-center overflow-x-auto'
        in view
    )
    # 普通用户在「我的 MCP」（含 personalOnly 个人中心 / 嵌入弹窗）下用顶部 tabs 切二级视图
    assert 'v-if="!isAdmin && activeScope === \'personal\'"' in view
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
