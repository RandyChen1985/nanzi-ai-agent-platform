"""切片增删改的前端契约。

切片是召回不准时唯一能"就地修"的抓手：改内容、临时停用、删噪音。
停用必须是"保留但不召回"，不能靠删除来实现（删了就找不回来）。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_chunk_drawer_supports_edit_and_delete(page: str) -> None:
    assert "startEditChunk" in page
    assert "saveChunk" in page
    assert "removeChunk" in page


def test_chunk_can_be_disabled_without_deleting(page: str) -> None:
    assert "toggleChunk" in page
    assert "参与召回" in page
    assert "内容仍保留" in page


def test_new_chunk_form_exists(page: str) -> None:
    assert "createChunk" in page
    assert "showNewChunkForm" in page


def test_chunk_writes_are_scoped_to_viewing_document(page: str) -> None:
    segment = page.split("const chunkTargetPath", 1)[1].split("const startEditChunk", 1)[0]
    assert "viewingDocument.value?.id" in segment
    assert "chunks" in segment


def test_chunk_save_warns_about_vector_rebuild(page: str) -> None:
    assert "向量重建需数秒" in page
