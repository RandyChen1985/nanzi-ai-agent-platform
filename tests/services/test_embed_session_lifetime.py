"""嵌入会话的绝对寿命与滑动续期口径。

链路背景：embed 会话令牌（`emb_ses_`）会落在 iframe 的 sessionStorage 里（JS 可读），
此前只有 24h **滑动** TTL——只要持续活跃，令牌永不过期，泄露后只能靠管理员禁用账号收场。
本文件锁定两条新口径：

1. 超过 `SESSION_TOKEN_MAX_LIFETIME_SECONDS`（7 天）必须作废，与活跃度无关；
2. 滑动续期只跟随「回源复核」发生，不再每个请求发两条 EXPIRE。
"""

from __future__ import annotations

import time

import pytest

from app.services import auth_service
from app.services.auth_service import AuthService
from app.services.embed_service import (
    SESSION_TOKEN_MAX_LIFETIME_SECONDS,
    SESSION_TOKEN_TTL_SECONDS,
)

pytestmark = pytest.mark.no_infrastructure

DAY = 86400


class FakeRedis:
    def __init__(self, data: dict[str, dict[str, str]]) -> None:
        self.hashes = {k: dict(v) for k, v in data.items()}
        self.expires: dict[str, int] = {}
        self.deleted: list[str] = []
        self.sets: dict[str, set[str]] = {}

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hset(self, key, mapping=None, **kwargs):
        self.hashes.setdefault(key, {}).update(mapping or {})

    async def expire(self, key, seconds):
        self.expires[key] = int(seconds)
        return True

    async def delete(self, *keys):
        for key in keys:
            self.hashes.pop(key, None)
            self.deleted.append(key)
        return len(keys)

    async def sadd(self, key, *values):
        self.sets.setdefault(key, set()).update(values)

    async def srem(self, key, *values):
        self.sets.setdefault(key, set()).difference_update(values)


def _session_row(*, issued_days_ago: float, verified_seconds_ago: float = 0) -> dict[str, str]:
    now = int(time.time())
    return {
        "user_id": "7",
        "user_name": "alice",
        "real_name": "Alice",
        "role": "user",
        "dept_code": "D1",
        "org_path": "/D1",
        "dept_name": "研发部",
        "extra_data": "",
        "status": "1",
        "session_type": "embed",
        "agent_id": "",
        "created_by_user_id": "7",
        "issued_at": str(now - int(issued_days_ago * DAY)),
        "verified_at": str(now - int(verified_seconds_ago)),
    }


def _patch(monkeypatch, redis):
    async def _get_redis():
        return redis

    monkeypatch.setattr(auth_service, "get_redis", _get_redis)


def _cache_key(token: str) -> str:
    manager = auth_service.get_api_key_manager()
    return f"auth:api_key:{manager.hash_api_key(token)}"


async def test_session_expires_at_absolute_lifetime_even_if_active(monkeypatch):
    token = "emb_ses_expired"
    key = _cache_key(token)
    # 8 天前签发，但 1 秒前刚复核过（活跃）——绝对寿命优先
    redis = FakeRedis({key: _session_row(issued_days_ago=8, verified_seconds_ago=1)})
    _patch(monkeypatch, redis)

    assert await AuthService.verify_api_key(token) is None
    assert key in redis.deleted, "超龄会话必须就地删除，而不是每次请求都重新判断"
    assert key not in redis.hashes


async def test_session_within_lifetime_is_returned(monkeypatch):
    token = "emb_ses_alive"
    key = _cache_key(token)
    redis = FakeRedis({key: _session_row(issued_days_ago=1, verified_seconds_ago=1)})
    _patch(monkeypatch, redis)

    user = await AuthService.verify_api_key(token)
    assert user is not None and user["user_name"] == "alice"


async def test_no_expire_on_every_request(monkeypatch):
    """复核窗口内不得续期：这是「每请求两条 EXPIRE」被去掉的直接断言。"""
    token = "emb_ses_hot"
    key = _cache_key(token)
    redis = FakeRedis({key: _session_row(issued_days_ago=1, verified_seconds_ago=1)})
    _patch(monkeypatch, redis)

    await AuthService.verify_api_key(token)
    assert redis.expires == {}


async def test_expiry_renewed_along_with_reverify(monkeypatch):
    token = "emb_ses_reverify"
    key = _cache_key(token)
    redis = FakeRedis({key: _session_row(issued_days_ago=1, verified_seconds_ago=10 * DAY)})
    _patch(monkeypatch, redis)

    cached = redis.hashes[key]

    async def _reverify(_redis, _key, session_data, _db=None):
        return dict(session_data)

    monkeypatch.setattr(AuthService, "_reverify_session", staticmethod(_reverify))

    user = await AuthService.verify_api_key(token)
    assert user is not None
    assert key in redis.expires, "复核时顺手续期"
    assert 0 < redis.expires[key] <= SESSION_TOKEN_TTL_SECONDS


async def test_renewal_never_exceeds_absolute_lifetime(monkeypatch):
    """临近 7 天时续期只能续到「绝对寿命剩余」，不能把 TTL 又拉回 24h。"""
    token = "emb_ses_near_limit"
    key = _cache_key(token)
    redis = FakeRedis(
        {key: _session_row(issued_days_ago=6.9, verified_seconds_ago=10 * DAY)}
    )
    _patch(monkeypatch, redis)

    async def _reverify(_redis, _key, session_data, _db=None):
        return dict(session_data)

    monkeypatch.setattr(AuthService, "_reverify_session", staticmethod(_reverify))

    await AuthService.verify_api_key(token)
    remaining = SESSION_TOKEN_MAX_LIFETIME_SECONDS - int(6.9 * DAY)
    assert redis.expires[key] <= remaining + 1
    assert redis.expires[key] < SESSION_TOKEN_TTL_SECONDS
