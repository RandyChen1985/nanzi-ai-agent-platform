"""会话历史「删除确认层」可读性契约。

删除确认原先用 `bg-white/95` 盖在白色会话卡片上，几乎和卡片融合，
取消按钮也没有边界，用户反馈「背景过白，看不清」。契约锁住：
不透明底色 + 警示图标 + 描边按钮，避免以后被改回低对比样式。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

SIDEBAR = ROOT / "frontend/src/components/ChatHistorySidebar.vue"


def _delete_overlay_block() -> str:
    source = SIDEBAR.read_text(encoding="utf-8")
    start = source.index("<!-- Inline Delete Confirmation Overlay -->")
    end = source.index("<!-- Loading More Indicator -->")
    return source[start:end]


def test_delete_confirm_overlay_is_opaque_and_has_warning_cue():
    block = _delete_overlay_block()

    # 半透明白底会和白色卡片融合，确认层边界消失
    assert "bg-white/95" not in block
    assert "bg-red-50" in block
    assert "dark:bg-red-950" in block
    # 警示图标，让「这是删除确认」一眼可辨
    assert "text-red-500" in block
    # 文案与两个动作都在
    assert "确认删除该会话" in block
    assert "cancelDelete" in block
    assert "confirmDelete(item)" in block


def test_cancel_button_has_visible_border():
    block = _delete_overlay_block()

    assert "border border-red-200" in block, "取消按钮需要描边，否则在浅底上不可见"
    assert "bg-red-600" in block, "删除按钮保持实心红，主次动作要能区分"
