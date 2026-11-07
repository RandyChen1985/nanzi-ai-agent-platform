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
    """全选在「全部知识库」列表页表头，左栏树只负责导航。"""
    assert "toggleLibrarySelectAll" in page
    assert "librarySelectableRows" in page
    assert "取消全选" in page


def test_select_all_is_scoped_to_current_tab_and_search(page: str) -> None:
    """全选不能跨 tab / 跨搜索 / 跨页：把看不见的库也选上，等于给自己埋雷。

    列表页改成服务端分页后，「可见范围」由后端按 keyword/visibility 过滤后返回，
    前端只持有当前页；全选的作用域因此收敛为「当前页里能管理的行」。
    """
    # 分页参数与过滤条件必须一起下发给后端（本地再过滤会漏掉其他页的命中）
    assert "const fetchLibraryList = async" in page
    fetch_block = page.split("const fetchLibraryList = async", 1)[1].split("let librarySearchTimer", 1)[0]
    assert "page:" in fetch_block and "page_size:" in fetch_block
    assert "keyword" in fetch_block and "visibility" in fetch_block

    # 全选只落在当前页的可管理行上：不能直接用 datasets（全量）做笛卡尔积
    block = page.split("const toggleLibrarySelectAll = ", 1)[1].split("const libraryListTitle", 1)[0]
    assert "librarySelectableRows" in block
    assert "datasets.value" not in block

    block2 = page.split("const librarySelectableRows = computed(", 1)[1].split("\n)", 1)[0]
    assert "canManageDataset" in block2
    assert "libraryRows" in block2
