"""「我的知识库配额」独立入口的契约。

左栏配额条只关心「我还能建几个库」，与具体某个库无关。原先前端借第一个可读库
去问 ``/datasets/{id}/quota``，那个库一旦失联或被删就拿不到数，于是额度条忽有忽无。
这里锁住：入口不依赖 dataset_id、只统计自己拥有的库、上限取配置项。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "app/api/portal/endpoints/ragflow.py"
SERVICE = ROOT / "app/services/knowledge_quota_service.py"


@pytest.fixture(scope="module")
def source() -> str:
    return SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def service() -> str:
    return SERVICE.read_text(encoding="utf-8")


def _endpoint_block(source: str, func: str) -> str:
    start = source.index(f"async def {func}(")
    end = source.find("\n@router.", start)
    return source[start : end if end != -1 else len(source)]


def test_route_does_not_depend_on_dataset_id(source: str) -> None:
    """路由里不能出现 {dataset_id}：这是全局额度，跟某一个库无关。"""
    assert '@router.get("/datasets/quota/me")' in source
    block = _endpoint_block(source, "get_my_dataset_quota")
    assert "dataset_id" not in block


def test_quota_counts_only_owned_datasets(source: str, service: str) -> None:
    """被授权进来的库不占自己的额度，统计口径必须和建库预检一致。"""
    assert "KnowledgeQuotaService.owned_dataset_count" in _endpoint_block(
        source, "get_my_dataset_quota"
    )
    count_block = service.split("async def owned_dataset_count", 1)[1].split("\n    @classmethod", 1)[0]
    assert "owner_user_id == user_id" in count_block


def test_limit_comes_from_config_key(source: str, service: str) -> None:
    """上限来自 knowledge_quota_max_datasets_per_user，不在端点上硬编码。"""
    assert 'DATASET_LIMIT_KEY = "knowledge_quota_max_datasets_per_user"' in service
    block = _endpoint_block(source, "get_my_dataset_quota")
    assert "KnowledgeQuotaService.limits" in block
    assert "max_datasets_per_user" in block


def test_route_declared_before_dataset_scoped_quota(source: str) -> None:
    """``/datasets/quota/me`` 要排在 ``/datasets/{dataset_id}/quota`` 前面，避免被变量段吃掉。"""
    assert source.index('@router.get("/datasets/quota/me")') < source.index(
        '@router.get("/datasets/{dataset_id}/quota")'
    )
