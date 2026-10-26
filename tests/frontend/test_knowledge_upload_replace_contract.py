"""同名上传的三选一契约。

RAGFlow 对重名上传不报错，只会静默多存一份或加 "(1)" 后缀；
用户看到的结果却是「我替换了怎么有两份」。所以必须在上传前问清楚。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_upload_detects_duplicate_names(page: str) -> None:
    assert "uploadConflicts" in page
    assert "fetchDatasetDocumentsForUpload" in page


def test_conflict_dialog_offers_three_choices(page: str) -> None:
    assert "resolveUploadConflicts('skip')" in page
    assert "resolveUploadConflicts('replace')" in page
    assert "resolveUploadConflicts('keep')" in page


def test_replace_uses_dedicated_endpoint_with_old_document_id(page: str) -> None:
    segment = page.split("const runUpload", 1)[1].split("const confirmDeleteDocument", 1)[0]
    assert "/documents/replace" in segment
    assert "old_document_id" in segment


def test_replace_does_not_double_trigger_parse(page: str) -> None:
    """替换端点内部已经触发解析，前端再调一次会把刚起的任务打断。"""
    segment = page.split("if (replaceMap.has(file)) {", 1)[1].split("const payload = new FormData()", 1)[0]
    assert "/documents/parse" not in segment


def test_replace_warns_about_broken_history_references(page: str) -> None:
    assert "历史引用" in page
    assert "已删除的旧文档" in page
