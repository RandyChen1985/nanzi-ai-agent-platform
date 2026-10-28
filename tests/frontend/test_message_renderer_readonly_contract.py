"""只读渲染契约：分享页复用同一渲染内核，但必须摘掉需要登录态的增强。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure
SOURCE = "frontend/src/components/MessageRenderer.vue"


def _source() -> str:
    return (ROOT / SOURCE).read_text(encoding="utf-8")


def test_readonly_prop_exists_and_defaults_to_false():
    source = _source()
    assert "readonly?: boolean" in source
    assert "readonly: false" in source


def test_readonly_short_circuits_login_dependent_processing():
    source = _source()
    # 两处短路：渲染前的 citation 保护、渲染后的路径重映射/下载按钮/角标兜底
    assert source.count("props.readonly") >= 2
    assert "if (props.readonly) {" in source
    assert "return res;" in source


def test_canvas_cards_degrade_in_readonly_mode():
    source = _source()
    assert source.count('v-if="readonly"') >= 2, "两块画布卡片都要降级为源码"
    assert "segment.content" in source


def test_public_share_page_uses_readonly_renderer():
    source = (ROOT / "frontend/src/views/SharedConversation.vue").read_text(encoding="utf-8")
    assert "MessageRenderer" in source
    assert "readonly" in source
    assert "hide-quick-buttons" in source or "hideQuickButtons" in source
