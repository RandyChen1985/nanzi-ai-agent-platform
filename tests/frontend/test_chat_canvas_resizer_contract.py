from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_chat_canvas_resizer_contract():
    canvas_file = ROOT / "frontend/src/components/embed/ChatCanvas.vue"
    assert canvas_file.exists(), "ChatCanvas.vue file must exist"

    content = canvas_file.read_text(encoding="utf-8")

    # Verify split-screen resizer storage key and state
    assert "nanzi_canvas_preferred_width" in content
    assert "customWidth" in content
    assert "isResizing" in content

    # Verify drag handlers
    assert "startResize" in content
    assert "handleResizing" in content
    assert "stopResize" in content
    assert "resetWidth" in content

    # Verify style and template handle
    assert "panelStyle" in content
    assert "cursor-col-resize" in content
    assert "@mousedown=\"startResize\"" in content
    assert "@dblclick=\"resetWidth\"" in content

    # Verify pinning support
    assert "defineModel<boolean>('pinned'" in content
    assert "pinned = !pinned" in content

    embed_chat = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    assert "canvasPinned" in embed_chat
    assert 'v-model:pinned="canvasPinned"' in embed_chat
    assert "canvasPinnedWidthPx" in embed_chat
