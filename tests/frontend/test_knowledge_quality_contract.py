"""检索质量与治理巡检的前端契约。

运营分析回答「被用了多少」，这两块回答「用起来效果如何、库里有没有烂摊子」。
零结果率必须能看见——只看命中数会掩盖"很多问题根本搜不到"。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeMetrics.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_zero_result_rate_is_visible(page: str) -> None:
    assert "/retrieval-stats" in page
    assert "zero_result_rate" in page
    assert "零结果率" in page


def test_top_zero_queries_are_listed(page: str) -> None:
    assert "top_zero_queries" in page


def test_health_panel_can_trigger_scan(page: str) -> None:
    assert "/health/scan" in page
    assert "runHealthScan" in page
    assert "立即巡检" in page


def test_panels_degrade_silently_for_non_admin(page: str) -> None:
    """非管理员打开这一页不该看到两个报错卡片。"""
    for fn in ("loadRetrievalStats", "loadHealthSummary"):
        block = page.split(f"const {fn}", 1)[1].split("\n}", 1)[0]
        assert "showToast" not in block
