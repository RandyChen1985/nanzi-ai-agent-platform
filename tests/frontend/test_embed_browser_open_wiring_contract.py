"""AI 消息 URL 打开到 EmbedChat/WebPreviewPanel 的契约测试。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_embed_chat_wires_browser_url_to_web_preview_for_current_and_history_messages():
    source = _source("frontend/src/views/EmbedChat.vue")
    assert source.count(':enable-browser-open="true"') >= 2
    assert source.count('@open-browser-url="handleOpenWebPreviewUrl"') >= 2
    assert "import WebPreviewPanel from \"@/components/embed/WebPreviewPanel.vue\";" in source
    assert "const webPreviewUrl = ref<string | null>(null);" in source
    assert "const handleOpenWebPreviewUrl = (url: string) =>" in source
    assert "webPreviewUrl.value = url;" in source
    assert "webPreviewVisible.value = true;" in source
    assert ":url=" in source
    assert ":url=\"webPreviewUrl\"" in source
    assert "BrowserPanel" in source
    assert ':open-url="browserOpenUrl"' not in source


def test_automation_browser_panel_does_not_receive_web_preview_url():
    source = _source("frontend/src/components/embed/BrowserPanel.vue")
    assert "openUrl?: string | null;" not in source
    assert "messageBrowserLinks" not in source
    assert "consumeOpenUrl" not in source
