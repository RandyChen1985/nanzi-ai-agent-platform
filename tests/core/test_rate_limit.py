"""计数限流工具单测（内存 FakeRedis）。"""

from __future__ import annotations

import pytest

from app.core import rate_limit
from app.core.rate_limit import hit_rate_limit, reset_rate_limit

pytestmark = pytest.mark.no_infrastructure


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.counters: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.fail = fail
        self.deleted: list[str] = []

    async def incr(self, key: str) -> int:
        if self.fail:
            raise RuntimeError("redis down")
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = int(seconds)
        return True

    async def delete(self, key: str) -> int:
        self.deleted.append(key)
        self.counters.pop(key, None)
        return 1

    async def ttl(self, key: str) -> int:
        # 与 Redis 一致：缺 TTL 返回 -1
        return self.expires.get(key, -1)


def _patch(monkeypatch, redis):
    async def _get_redis():
        return redis

    monkeypatch.setattr(rate_limit, "get_redis", _get_redis)


async def test_allows_until_limit_then_blocks(monkeypatch):
    redis = FakeRedis()
    _patch(monkeypatch, redis)

    results = [await hit_rate_limit("k", 3) for _ in range(3)]
    assert results == [False, False, False], "未达上限不得拦截"
    assert await hit_rate_limit("k", 3) is True, "超过上限必须拦截"
    assert redis.counters["k"] == 4


async def test_first_hit_sets_expiry_only_once(monkeypatch):
    redis = FakeRedis()
    _patch(monkeypatch, redis)

    await hit_rate_limit("k", 10, window_seconds=90)
    await hit_rate_limit("k", 10, window_seconds=90)
    assert redis.expires["k"] == 90


async def test_keys_are_independent(monkeypatch):
    redis = FakeRedis()
    _patch(monkeypatch, redis)

    await hit_rate_limit("a", 1)
    assert await hit_rate_limit("a", 1) is True
    assert await hit_rate_limit("b", 1) is False, "不同维度必须互不影响"


async def test_fail_open_when_redis_missing(monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(rate_limit, "get_redis", _none)
    assert await hit_rate_limit("k", 1) is False


async def test_fail_open_when_redis_raises(monkeypatch):
    _patch(monkeypatch, FakeRedis(fail=True))
    assert await hit_rate_limit("k", 1) is False, "限流自身故障不得阻断业务"


async def test_non_positive_limit_is_disabled(monkeypatch):
    redis = FakeRedis()
    _patch(monkeypatch, redis)
    assert await hit_rate_limit("k", 0) is False
    assert redis.counters == {}, "limit<=0 时不应产生计数"


async def test_missing_ttl_is_healed_when_over_limit(monkeypatch):
    """INCR 成功但 EXPIRE 失败会留下永久计数的键；超限时应自愈补上 TTL。"""
    redis = FakeRedis()
    _patch(monkeypatch, redis)

    # 模拟「键已存在、且没有 TTL」的残留：直接把计数打到上限之上
    redis.counters["k"] = 5
    assert await hit_rate_limit("k", 3) is True
    assert redis.expires.get("k") == 60, "超限路径应补回 TTL，避免永久 429"

    # 未超限时不补（热路径不做额外往返）
    redis2 = FakeRedis()
    _patch(monkeypatch, redis2)
    redis2.counters["k2"] = 1
    await hit_rate_limit("k2", 10)
    assert "k2" not in redis2.expires or redis2.expires.get("k2") is None


async def test_reset_clears_counter(monkeypatch):
    redis = FakeRedis()
    _patch(monkeypatch, redis)
    await hit_rate_limit("k", 1)
    await reset_rate_limit("k")
    assert redis.deleted == ["k"]
