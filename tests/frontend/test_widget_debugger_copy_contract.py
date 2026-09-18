"""WidgetDebugger 集成指南弹层与接入文档的术语一致性契约。

「Embed Ticket」在平台内的统一叫法是**票据**。此前弹层顶部红框叫「门票」，
`docs/md/embed_integration_guide.md` 的对比表与 FAQ 也叫「门票」，同一概念在
不同位置出现两种叫法容易让接入方以为是两个不同东西。

这里锁定术语统一，避免后续编辑时又混回「门票」。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WIDGET_DEBUGGER = ROOT / "frontend/src/views/WidgetDebugger.vue"
INTEGRATION_GUIDE = ROOT / "docs/md/embed_integration_guide.md"

pytestmark = pytest.mark.no_infrastructure


def test_widget_debugger_avoids_alternative_terminology():
    """弹层文案一律称「票据」，不得混用「门票」。"""
    source = WIDGET_DEBUGGER.read_text(encoding="utf-8")

    assert "门票" not in source


def test_widget_debugger_banner_uses_unified_terminology():
    """顶部安全提示条必须用统一术语描述一次性凭证。"""
    source = WIDGET_DEBUGGER.read_text(encoding="utf-8")

    banner_start = source.index("安全规范提示：")
    banner = source[banner_start : banner_start + 400]

    assert "一次性票据" in banner


def test_integration_guide_avoids_alternative_terminology():
    """接入文档同样不得混用「门票」。"""
    source = INTEGRATION_GUIDE.read_text(encoding="utf-8")

    assert "门票" not in source
