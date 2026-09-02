"""轻量网页预览面板的安全 iframe 和交互契约测试。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _source() -> str:
    return (ROOT / "frontend/src/components/embed/WebPreviewPanel.vue").read_text(encoding="utf-8")


def test_web_preview_panel_is_independent_from_server_browser_session():
    source = _source()
    assert "defineProps" in source
    assert "url: string | null" in source
    assert "isBrowserOpenableUrl" in source
    assert "iframe" in source
    assert ":src=\"safeUrl\"" in source
    assert "sandbox=" in source
    assert "referrerpolicy=\"no-referrer\"" in source
    assert "/api/v1/chat/browser/sessions/open" not in source
    assert "WebSocket" not in source


def test_web_preview_panel_exposes_new_window_fallback_and_close_action():
    source = _source()
    assert "在新窗口打开" in source
    assert "target=\"_blank\"" in source
    assert "emit('close')" in source
    assert "禁止嵌入" in source or "不支持面板预览" in source
