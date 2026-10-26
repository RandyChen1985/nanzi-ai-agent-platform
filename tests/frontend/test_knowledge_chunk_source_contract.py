"""切片 → 原文联动契约。

改了切片想核对原文时，只能自己翻 PDF 找那一页。
PDF 的 #page=N 只有浏览器原生阅读器认，file-viewer 的 pdf 渲染器不认，
所以这种跳页场景必须让位给 iframe —— 否则点了没反应。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_chunk_shows_source_jump(page: str) -> None:
    assert "openChunkSource" in page
    assert "查看原文" in page


def test_page_number_resolved_from_page_no_or_positions(page: str) -> None:
    block = page.split("const resolveChunkPage", 1)[1].split("const openChunkSource", 1)[0]
    assert "page_no" in block
    assert "positions" in block


def test_pdf_page_jump_forces_iframe(page: str) -> None:
    assert "mustUseIframeForPage" in page
    block = page.split("const canPreviewSelectedDocument", 1)[1].split("})", 1)[0]
    assert "mustUseIframeForPage" in block


def test_preview_url_carries_page_fragment(page: str) -> None:
    block = page.split("const previewDocUrl", 1)[1].split("const isPdfSelectedDocument", 1)[0]
    assert "#page=" in block


def test_preview_page_resets_on_close(page: str) -> None:
    block = page.split("const closeDocPreview", 1)[1].split("const resolveChunkPage", 1)[0]
    assert "previewPage.value = null" in block
