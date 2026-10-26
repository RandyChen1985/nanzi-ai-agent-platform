"""批量操作契约。

后端 DELETE /datasets、documents/parse、documents 早就接受 ids 数组，
前端一直只传单个 —— 库一多，逐个删是纯体力活。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_dataset_tree_has_bulk_selection(page: str) -> None:
    assert "selectedDatasetIds" in page
    assert "bulkDeleteDatasets" in page
    assert "已选" in page


def test_document_tree_has_bulk_selection(page: str) -> None:
    assert "selectedDocIds" in page
    assert "bulkParseDocuments" in page
    assert "bulkDeleteDocuments" in page


def test_bulk_delete_uses_confirm_modal_not_window_confirm(page: str) -> None:
    assert "pendingBulkDelete" in page
    assert "window.confirm" not in page


def test_bulk_doc_ops_are_scoped_to_one_dataset(page: str) -> None:
    # 跨库混选会把 A 库的 docId 发到 B 库
    assert "bulkDocDatasetId" in page


def test_bulk_selection_supports_select_all(page: str) -> None:
    assert "toggleSelectAll" in page
    assert "bulkSelectableDatasets" in page
    assert "取消全选" in page


def test_select_all_is_scoped_to_current_tab_and_search(page: str) -> None:
    """全选不能跨 tab / 跨搜索：把看不见的库也选上，等于给自己埋雷。"""
    block = page.split("const bulkSelectableDatasets", 1)[1].split("const toggleSelectAll", 1)[0]
    assert "filteredDatasets.value" in block
    assert "canManageDataset" in block
