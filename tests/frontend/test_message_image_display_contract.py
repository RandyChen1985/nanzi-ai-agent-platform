"""消息流图片显示尺寸契约。

出图工具产出的是 1664x928 这类大图，若只靠 Tailwind preflight 的 `max-width: 100%`，
一张图就会撑满整个对话区宽度、把后面的内容挤出一屏。这里把「等比缩放 + 限制最大高度」
钉住，避免以后清理样式时把它顺手删掉。
"""

from pathlib import Path

import pytest

STYLE_CSS = Path("frontend/src/style.css")


@pytest.fixture(scope="module")
def css() -> str:
    return STYLE_CSS.read_text(encoding="utf-8")


def _image_rule(css: str) -> str:
    """取出 `.markdown-body img { ... }` 这段声明块。"""
    marker = ".markdown-body img {"
    start = css.find(marker)
    assert start != -1, "frontend/src/style.css 缺少 .markdown-body img 规则"
    end = css.find("}", start)
    assert end != -1, ".markdown-body img 规则没有闭合"
    return css[start:end]


def test_message_image_is_height_capped(css: str) -> None:
    """必须限制最大高度，否则大图占满整个对话区。"""
    rule = _image_rule(css)
    assert "max-height:" in rule, "缺少 max-height，大图会撑满聊天空间"


def test_message_image_keeps_aspect_ratio(css: str) -> None:
    """宽高都交给 auto，配合 max-* 才能等比缩放；写死一边会变形。"""
    rule = _image_rule(css)
    assert "width: auto" in rule
    assert "height: auto" in rule


def test_message_image_never_exceeds_container(css: str) -> None:
    """小图不放大，大图不溢出气泡。"""
    rule = _image_rule(css)
    assert "max-width: 100%" in rule


def test_message_image_signals_clickable(css: str) -> None:
    """点图片能开画布看原图，光标要给出提示。"""
    rule = _image_rule(css)
    assert "cursor: zoom-in" in rule
