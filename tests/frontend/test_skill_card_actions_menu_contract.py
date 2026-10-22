from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "frontend/src/views/SkillsManagement.vue"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_card_actions_collapsed_into_more_menu():
    """卡片的分享/导出/重命名/删除收进「⋯」，避免右上角一排图标挤压标题。"""
    template = _source()[_source().index("<template>"):]
    assert 'openActionMenu($event, skill)' in template
    assert 'role="menu"' in template
    for action in ("share", "export", "rename", "delete"):
        assert f"runSkillAction('{action}')" in template
    # 原独立按钮不再直接绑定动作
    assert '@click.stop="exportSkill(' not in template
    assert '@click.stop="renameSkill(' not in template
    assert '@click.stop="deleteSkill(' not in template
    assert '@click.stop="openShareDialog(skill)"' not in template


def test_more_menu_is_accessible_and_unanchored():
    """菜单 fixed 挂根级（不被卡片裁切），⋯ 有可读标签和展开态。"""
    source = _source()
    template = source[source.index("<template>"):]
    assert ':aria-label="`${skill.name} 的更多操作`"' in template
    assert ':aria-expanded="actionMenu?.skill?.id === skill.id"' in template
    assert 'fixed inset-0 z-[60]' in template
    assert 'z-[61]' in template
    assert ':style="{ left: `${actionMenu.x}px`, top: `${actionMenu.y}px` }"' in template


def test_share_entry_only_for_personal_skills():
    """平台技能不显示「分享给同事」，与原来的 scope 条件一致。"""
    template = _source()[_source().index("<template>"):]
    assert 'v-if="actionMenu.skill.scope === \'personal\'"' in template


def test_publish_action_still_visible():
    """「提交为平台技能」是卡片主操作，不能被收进菜单。"""
    template = _source()[_source().index("<template>"):]
    assert "submitSkillPublication(skill)" in template
    assert "提交为平台技能" in template
