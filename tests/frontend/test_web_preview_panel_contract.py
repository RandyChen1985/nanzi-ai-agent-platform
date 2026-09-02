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


def test_web_preview_panel_matches_workspace_drawer_responsive_interactions():
    source = _source()
    assert "defineModel<boolean>('pinned'" in source
    assert "defineModel<number>('panelWidth'" in source
    assert "const isMobile = ref(" in source
    assert "const syncMobile = () =>" in source
    assert "const startResize = (event: MouseEvent) =>" in source
    assert "const handleResizing = (event: MouseEvent) =>" in source
    assert "const resetWidth = () =>" in source
    assert "localStorage" in source
    assert "pinnedContainerClass" in source
    assert "maxWidth" in source
    assert "双击重置" in source
    assert "isMobile ? 'flex-1 min-h-0 w-full' : 'absolute inset-0'" in source


def test_web_preview_panel_contains_page_overflow_and_supports_maximized_view():
    source = _source()
    assert "overflow-hidden" in source
    assert "const isMaximized = ref(false)" in source
    assert "最大化查看" in source
    assert "isMaximized" in source
    assert "maxWidth: '100vw'" in source


def test_web_preview_panel_supports_scaled_page_preview():
    source = _source()
    assert "const previewZoom = ref<'auto' | number>('auto')" in source
    assert "PREVIEW_ZOOM_OPTIONS" in source
    assert "自动适配" in source
    assert "effectiveZoom" in source
    assert "frameScaleStyle" in source
    assert "transformOrigin: 'top left'" in source
    assert "v-model=\"previewZoom\"" in source
    assert "缩放" in source
