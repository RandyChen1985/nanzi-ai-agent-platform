"""搜索文档名的懒加载契约。

文档是「展开才加载」的，但搜索框承诺能搜文档名：不补懒加载，
没展开过的库里的文档永远搜不到。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_search_triggers_lazy_document_loading(page: str) -> None:
    assert "ensureDocumentsForSearch" in page
    assert "searchDocLoading" in page


def test_search_loading_is_debounced_and_bounded(page: str) -> None:
    assert "SEARCH_DOC_CONCURRENCY" in page
    assert "SEARCH_DOC_DEBOUNCE_MS" in page


def test_search_fetch_is_silent(page: str) -> None:
    # 一次性拉多个库，失败时逐个弹 toast 会刷屏
    assert "silent: true" in page
