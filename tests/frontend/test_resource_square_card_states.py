"""卡片四种状态：可申请 / 审批中 / 已授权 / 已驳回，按钮与徽标必须互斥。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CARD = ROOT / "frontend/src/components/resource-square/ResourceSquareCard.vue"
MODAL = ROOT / "frontend/src/components/resource-square/ApplyAccessModal.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_card_has_four_mutually_exclusive_states():
    card = _read(CARD)
    assert "已有权限" in card
    assert "审批中" in card
    assert "已驳回" in card
    assert "重新申请" in card
    for state in ("has", "pending", "rejected"):
        assert f"state === '{state}'" in card


def test_apply_button_only_rendered_in_apply_state():
    card = _read(CARD)
    assert 'v-if="state === \'apply\'"' in card
    # 有权限时不该再出现申请入口
    apply_index = card.index("申请访问")
    has_index = card.index("已有权限")
    assert has_index < apply_index


def test_only_granted_card_is_clickable():
    """已授权才可点；知识库这一侧当前不挂概览入口（点开是空白）。"""
    card = _read(CARD)
    assert "if (state.value === 'has' && props.kind !== 'kb') emit('open')" in card


def test_apply_modal_collects_optional_reason_and_says_where_to_track():
    modal = _read(MODAL)
    assert "申请理由" in modal
    assert "我的申请" in modal
    assert "不能重复提交" in modal


def test_apply_modal_states_are_reset_when_reopened():
    modal = _read(MODAL)
    # 上一次的报错与理由不能带到下一张卡
    assert "reason.value = ''" in modal
    assert "error.value = ''" in modal
