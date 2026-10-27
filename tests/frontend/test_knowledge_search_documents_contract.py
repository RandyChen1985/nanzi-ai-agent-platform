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


def test_search_matches_dataset_id(page: str) -> None:
    """详情页展示的 Dataset ID 必须能被搜索框命中。

    详情页现在完整展示 RAGFlow Dataset ID，用户会把它复制到搜索框里定位库；
    不参与匹配的话这个 ID 就只能看不能用。
    """
    assert "idMatch" in page
    assert "dsId.toLowerCase().includes(query)" in page


def test_dataset_id_search_skips_document_prefetch(page: str) -> None:
    """按 ID 搜索时不该再把所有库的文档列表拉一遍。

    ID 命中说明用户在找库，文档预加载纯属浪费；短词仍然走正常文档搜索。
    """
    assert "query.length >= 8" in page
