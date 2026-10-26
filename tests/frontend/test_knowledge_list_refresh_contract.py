"""列表缓存与降级提示契约。

60s 缓存让首屏快，但同步/刷新必须能绕过它；而引擎挂了要显示
「这是缓存数据」而不是整页报错 —— 后者看起来像所有库都被删了。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_fetch_datasets_accepts_force_flag(page: str) -> None:
    assert "const fetchDatasets = async (force = false)" in page
    assert "refresh: force ? 1 : 0" in page


def test_sync_forces_refresh(page: str) -> None:
    block = page.split("datasets/sync", 1)[1].split("fetchDatasets(", 1)[1]
    assert block.startswith("true")


def test_stale_state_is_surfaced(page: str) -> None:
    assert "datasetsStale" in page
    assert "上次成功获取的数据" in page
