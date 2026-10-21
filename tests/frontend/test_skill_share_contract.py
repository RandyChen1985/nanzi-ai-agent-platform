"""个人技能分享的前端契约：左菜单、弹窗、两个面板与接口路径必须保持连通。

这些断言是静态检查：改模板/改接口路径时，测试会先报错，而不是等用户点开页面
才发现「收到的分享」是个空壳。
"""

from pathlib import Path

import pytest


FRONTEND = Path("frontend/src")
SKILLS_VIEW = FRONTEND / "views" / "SkillsManagement.vue"
PERSONAL_MENU = FRONTEND / "components" / "skill" / "SkillPersonalMenu.vue"
SHARE_DIALOG = FRONTEND / "components" / "skill" / "SkillShareDialog.vue"
RECEIVED_PANEL = FRONTEND / "components" / "skill" / "ReceivedSharesPanel.vue"
SENT_PANEL = FRONTEND / "components" / "skill" / "SentSharesPanel.vue"
PERSONAL_CENTER = FRONTEND / "views" / "PersonalCenter.vue"


pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_skills_view_renders_two_column_personal_layout():
    source = _read(SKILLS_VIEW)
    assert "md:flex-row md:items-start" in source
    assert "SkillPersonalMenu" in source
    assert 'activeWorkspaceTab === \'personal\' && personalMenu === \'received\'' in source
    assert 'activeWorkspaceTab === \'personal\' && personalMenu === \'sent\'' in source


def test_skills_view_keeps_platform_tabs_and_exposes_share_entry():
    source = _read(SKILLS_VIEW)
    assert "平台技能" in source
    assert "待审核" in source
    assert "已发布" in source
    assert "openShareDialog" in source
    assert "分享给同事" in source


def test_personal_menu_lists_three_sections():
    source = _read(PERSONAL_MENU)
    assert "我的技能" in source
    assert "收到的分享" in source
    assert "我的分享" in source
    # 宽屏左栏 + 窄屏胶囊两种变体
    assert "variant === 'aside'" in source
    assert "'aside' | 'chips'" in source
    assert "hidden w-[200px]" in source


def test_share_dialog_sends_targets_and_message_with_limit():
    source = _read(SHARE_DIALOG)
    assert "/api/portal/skills/personal/share-targets" in source
    assert "target_user_ids" in source
    assert 'maxlength="200"' in source
    assert "查看我的分享" in source


def test_received_panel_accepts_and_rejects():
    source = _read(RECEIVED_PANEL)
    assert "/api/portal/skills/personal/shares/received" in source
    assert "/accept" in source
    assert "/reject" in source
    assert "接受并安装到我的技能" in source
    assert "pending_count" in source


def test_sent_panel_filters_and_withdraws():
    source = _read(SENT_PANEL)
    assert "/api/portal/skills/personal/shares/sent" in source
    assert "status_counts" in source
    assert "撤回" in source
    assert "shareStatusMeta" in source


def test_personal_center_forwards_skills_tab_deep_link():
    source = _read(PERSONAL_CENTER)
    assert "skills_tab" in source
    assert "initial-personal-menu" in source


def test_share_status_meta_covers_all_states():
    source = _read(FRONTEND / "utils" / "skillShare.ts")
    for status in ("PENDING", "ACCEPTED", "REJECTED", "WITHDRAWN"):
        assert status in source
    assert "待接受" in source
    assert "已接受" in source


def test_received_panel_refreshes_skill_list_and_locates_accepted_skill():
    """接受分享后必须刷新个人技能列表，并让「去我的技能看看」直接打开新技能。

    只刷新菜单角标（旧实现）会让用户切回「我的技能」看不到刚接受的技能，
    必须按 F5 才能出现。
    """
    source = _read(SKILLS_VIEW)
    assert '@updated="handleShareChanged"' in source
    assert '@go-mine="openSharedSkill"' in source
    share_handler = source.split("const handleShareChanged", 1)[1]
    assert "fetchPersonalSkills()" in share_handler


def test_open_shared_skill_refetches_when_list_is_stale():
    source = _read(SKILLS_VIEW)
    body = source.split("const openSharedSkill", 1)[1].split("const ", 1)[0]
    assert "fetchPersonalSkills()" in body


def test_received_panel_emits_accepted_skill_id():
    source = _read(RECEIVED_PANEL)
    assert "emit('go-mine', item.accepted_skill_id" in source


def test_skills_view_reads_skills_tab_query_for_direct_links():
    """直接访问 /dashboard/skills?skills_tab=received 也要落到对应二级菜单。"""
    source = _read(SKILLS_VIEW)
    assert "route.query.skills_tab" in source


def test_share_components_support_dark_theme():
    """项目是 class 策略的暗黑主题（tailwind.config.js），新组件不能只有亮色样式。"""
    for path in (PERSONAL_MENU, SHARE_DIALOG, RECEIVED_PANEL, SENT_PANEL):
        source = _read(path)
        assert "dark:" in source, f"{path.name} 缺少暗黑模式样式"
        assert "dark:text-" in source, f"{path.name} 缺少暗黑模式文字颜色"
        assert "dark:bg-" in source or "dark:border-" in source, f"{path.name} 缺少暗黑模式背景/边框"
