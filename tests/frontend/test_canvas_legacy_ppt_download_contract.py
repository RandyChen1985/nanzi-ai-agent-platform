from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_legacy_ppt_is_downloaded_not_read_as_text_in_chat_link_path():
    composable = _source("frontend/src/composables/chat/useWorkspaceCanvas.ts")
    # 判定实现已抽到零依赖模块 documentPreviewFormats.ts（workspaceFilePreview.ts 仅 re-export）
    preview = _source("frontend/src/utils/documentPreviewFormats.ts")

    # util 侧提供判定：仅在 OFFICE 但不在 DOCUMENT_VIEWER
    assert "export function shouldDownloadInsteadOfPreview" in preview
    assert "OFFICE_EXTENSIONS.has(ext) && !DOCUMENT_VIEWER_EXTENSIONS.has(ext)" in preview

    # 聊天链接路径必须用它拦截，且必须出现在文本兜底分支之前
    assert "shouldDownloadInsteadOfPreview(filename)" in composable
    assert composable.index("shouldDownloadInsteadOfPreview(filename)") < composable.index(
        "const content = await axios.get(resolvedUrl).then"
    )


def test_canvas_copy_button_labels_documents_as_link():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    # Office/PDF 复制的是链接，而不是「复制代码」
    assert "data?.type === 'image' || isDocumentPreview ? '复制链接' : '复制代码'" in canvas


def test_resolved_content_also_rewrites_document_urls():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    assert "data.type === 'csv' || data.type === 'document'" in canvas
