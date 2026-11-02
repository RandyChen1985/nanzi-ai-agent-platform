"""「最近访问」的契约（Redis ZSET，保留 30 天）。

它不是访问日志，只回答「我上次打开过哪几个库」，所以：
- 用 ZSET 而不是列表：同一库反复访问只更新时间戳，不产生多条
- 写和读都要裁一次过期成员：否则只写不读的库永远留在榜单里
- Redis 故障必须降级为空，不能让知识库首页跟着挂
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "app/services/knowledge_recent_visit_service.py"
ENDPOINTS = ROOT / "app/api/portal/endpoints/ragflow.py"


@pytest.fixture(scope="module")
def service() -> str:
    return SERVICE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def endpoints() -> str:
    return ENDPOINTS.read_text(encoding="utf-8")


def test_retention_is_thirty_days(service: str) -> None:
    assert "RETENTION_SECONDS = 30 * 24 * 60 * 60" in service
    # 写入时裁过期 + 续期
    assert "zremrangebyscore" in service
    assert "expire(" in service


def test_revisit_updates_timestamp_instead_of_appending(service: str) -> None:
    """zadd 覆盖写：同一个库访问 10 次也只占一个位置。"""
    assert "zadd(" in service
    assert "lpush" not in service.lower()


def test_read_also_trims_expired(service: str) -> None:
    """读之前裁一次：只写不读的库不会自己消失。"""
    list_block = service.split("async def list_ids", 1)[1]
    assert "zremrangebyscore" in list_block
    assert "zrevrange" in list_block  # 最近的排前面


def test_redis_failure_degrades_to_empty(service: str) -> None:
    """Redis 不可用时返回空列表，而不是抛 500。"""
    assert "except Exception" in service
    assert "return []" in service


def test_visit_endpoint_requires_read_access(endpoints: str) -> None:
    """能看就能记：只要求可读，不要求管理权。"""
    start = endpoints.index("async def record_dataset_visit(")
    block = endpoints[start: endpoints.find("\n@router.", start)]
    assert "require_dataset_access" in block
    assert "KnowledgeRecentVisitService.record" in block


def test_recent_endpoint_returns_ids_only(endpoints: str) -> None:
    """只回顺序，库数据仍由 datasets 接口提供，避免两套可见范围打架。"""
    start = endpoints.index("async def list_recent_datasets(")
    block = endpoints[start: endpoints.find("\n@router.", start)]
    assert "snapshot(" in block
    assert "@router.get(\"/datasets/recent\")" in endpoints
