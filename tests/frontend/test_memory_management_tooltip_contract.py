"""契约：记忆工作台配置项的自定义 tooltip 不得被承载卡片裁剪。

tooltip 采用 `absolute left-0 top-full` 向下弹出，而卡片若带 `overflow-hidden`，
由于 `overflow: hidden` 的裁剪发生在绘制阶段，tooltip 上的 `z-[100]` 无法逃逸，
结果就是提示框只剩一行、右侧文字也被切掉（「启用会话摘要」最明显，因为它是
占满整行的最后一个布尔项，向下弹出立刻撞上卡片底边）。

修复方式：卡片不再裁剪（overflow-visible），改为让头部背景自行贴合圆角。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

VIEW = ROOT / "frontend/src/views/MemoryManagement.vue"


def _source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _config_card_anchor(source: str) -> int:
    """定位配置卡片 `v-for="group in visibleConfigGroups"` 的偏移。

    注意 `<section` 开标签位于 v-for 之前，因此按 v-for 定位后必须向前回溯。
    """
    return source.index('v-for="group in visibleConfigGroups"')


def test_config_card_does_not_clip_downward_tooltips():
    source = _source()
    anchor = _config_card_anchor(source)
    start = source.rindex("<section", 0, anchor)
    open_tag = source[start : source.index(">", start)]
    assert "overflow-hidden" not in open_tag, (
        "配置卡片不得使用 overflow-hidden：它会裁掉向下弹出的 tooltip，"
        "z-index 无法逃逸 overflow 裁剪"
    )


def test_config_card_header_keeps_rounded_corners_without_clipping():
    """去掉裁剪后，头部背景需自行贴合卡片圆角，否则卡片的圆角会变成直角。"""
    source = _source()
    header_start = source.index("px-4 py-3 border-b", _config_card_anchor(source))
    header_tag = source[header_start : source.index(">", header_start)]
    assert "rounded-t-lg" in header_tag, "头部需补 rounded-t-lg 以维持卡片圆角"


def test_tooltips_keep_explicit_layer_above_following_cards():
    """浮出卡片后仍需要显式层级，否则会被后面的配置卡片盖住。"""
    source = _source()
    assert source.count("group-hover:block") >= 4
    assert "z-[100]" in source
