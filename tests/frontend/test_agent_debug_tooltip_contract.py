"""契约：智能体调试页的 tooltip 不得被消息卡片裁切、也不得在悬停整条消息时集体弹出。

用户反馈（截图）：鼠标放在消息上，复制 / 点赞 / 点踩三个 tooltip 同时出现，且只露出上半截，
黑块压在正文与卡片底边上。两个根因叠在一起：

1. **裁切**：消息卡片带 `overflow-hidden`（用来贴合圆角），而手写 tooltip 是
   `absolute top-full` 向下弹出的 DOM 子节点；`overflow:hidden` 的裁剪发生在绘制阶段，
   子节点的 `z-50` 逃不出去，于是气泡被卡片底边切掉。
2. **集体弹出**：手写 tooltip 靠 `group-hover:opacity-100` 显隐，而整条消息行本身就是
   `.group`；嵌套的 `group` 不隔离，悬停消息任意位置都会命中「某个祖先 group 在 hover」，
   三个气泡一起亮起，看起来像三块黑疙瘩。

修复方式：统一改用平台 `v-tooltip` 指令（气泡挂在 body 上、`position: fixed`，且按触发器
自身 hover 显示），不再在卡片里手写绝对定位气泡。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

VIEW = ROOT / "frontend/src/views/AgentDebug.vue"


def _source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _message_bubble_card(source: str) -> str:
    """截取「Agent 消息卡片」的 :class 绑定（overflow-hidden 写在绑定表达式里）。"""
    anchor = source.index("<!-- Agent Message Bubble (Unified Card Style) -->")
    return source[anchor : source.index("<ChatExecutionTimeline", anchor)]


def test_message_toolbar_uses_platform_tooltip_directive():
    source = _source()
    for label in ("'复制'", "'很有帮助'", "'回答不准确'", "'重新生成'", "'复制内容'"):
        assert f"v-tooltip=\"{label}\"" in source, f"消息操作按钮「{label}」需改用平台 v-tooltip"


def test_no_handwritten_bubbles_inside_message_card():
    """手写气泡（深色块 + 绝对定位）会同时引入裁切与集体弹出两个问题。"""
    source = _source()
    assert "pointer-events-none absolute" not in source, "卡片内不得再手写绝对定位 tooltip"
    assert "bg-gray-900/90" not in source, "tooltip 样式统一由 v-tooltip 指令提供"
    assert "group-hover:opacity-100" in source, "用户消息的编辑/复制仍按需在悬停时出现"


def test_message_card_keeps_rounded_clipping_but_tooltips_escape_it():
    """卡片继续保留 overflow-hidden（圆角 + 内容裁切），气泡由指令挂到 body 上逃逸。"""
    card = _message_bubble_card(_source())
    assert "overflow-hidden" in card, "消息卡片的圆角裁切不应为了 tooltip 被去掉"
    tooltip_source = (ROOT / "frontend/src/directives/tooltip.ts").read_text(encoding="utf-8")
    assert "document.body.appendChild(bubble)" in tooltip_source


def test_history_toggle_is_icon_only_with_hover_hint():
    """历史记录入口只留图标：文字占位让标题区拥挤，状态由高亮 + tooltip 表达。"""
    source = _source()
    header_start = source.index("showHistorySidebar = !showHistorySidebar")
    button = source[source.rindex("<button", 0, header_start) : source.index("</button>", header_start)]

    assert ">历史记录</span>" not in source, "按钮里不应再有「历史记录」文字"
    assert "v-tooltip=\"showHistorySidebar ? '收起历史记录' : '历史记录'\"" in button
    assert ":aria-expanded=\"showHistorySidebar\"" in button, "展开状态需对读屏可见"
    # 时钟回拨箭头（history）图标，而不是原来的双箭头
    assert "M3 3v5h5" in button
    assert "M12 7v5l4 2" in button
