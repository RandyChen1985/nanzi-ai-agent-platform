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


def test_card_offers_a_copy_affordance_for_the_resource_name():
    """卡片标题旁要能一键复制资源名。

    复制的是物理名（`meta_pgdemo_ds` 这类），不是中文副标题——后者复制出去
    没法拿去查库、写 SQL 或配数据源。
    """
    card = _read(CARD)
    assert "import { copyToClipboard } from '@/utils/clipboard'" in card
    assert "await copyToClipboard(props.name)" in card
    # 必须是 stop：卡片标题自己有点击行为，不拦会被一起触发
    assert '@click.stop="copyName"' in card
    assert "复制 ${name}" in card


def test_copy_affordance_only_shows_on_hover_but_not_on_touch():
    """每张卡都常显一个复制图标会把标题挤掉；触屏没有 hover 又必须常显。"""
    card = _read(CARD)
    assert 'class="group flex flex-col' in card
    assert "group-hover:opacity-100" in card
    assert "max-sm:opacity-100" in card


def test_copy_success_gives_short_feedback_then_resets():
    card = _read(CARD)
    assert "copied.value = true" in card
    # 提示在 title/aria-label 里，不占标题行的宽度
    assert "copied ? '已复制'" in card
    assert '<span v-if="copied" class="text-emerald-600">已复制</span>' not in card
    # 定时器要在卸载时清掉，避免已销毁组件里 setState
    assert "onBeforeUnmount" in card
    assert "clearTimeout(copyTimer)" in card


def test_title_row_is_flex_so_long_names_do_not_clip_the_copy_button():
    """h4 上的 truncate 是 overflow:hidden，标题一长会把浮标整块裁掉。"""
    card = _read(CARD)
    assert "flex min-w-0 items-baseline gap-1" in card
    assert "min-w-0 truncate text-left" in card
    assert "ml-auto inline-flex shrink-0" in card
