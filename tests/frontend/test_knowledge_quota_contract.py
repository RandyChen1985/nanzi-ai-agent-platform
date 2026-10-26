"""配额展示契约。

接近上限要提前可见：用户是在"传不进去"那一刻才知道有配额，就太晚了。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_quota_endpoint_is_loaded_per_dataset(page: str) -> None:
    assert "loadDatasetQuota" in page
    assert "/quota`" in page


def test_quota_bar_has_warning_thresholds(page: str) -> None:
    assert "docQuotaRatio" in page
    assert "快满了" in page


def test_quota_failure_is_silent(page: str) -> None:
    """配额取不到不能影响文档列表——它只是辅助信息。"""
    block = page.split("const loadDatasetQuota", 1)[1].split("\n}", 1)[0]
    assert "catch {" in block
    assert "showToast" not in block
