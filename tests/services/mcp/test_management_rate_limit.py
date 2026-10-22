"""MCP 管理操作限流：超阈值 429，Redis 不可用时 fail-open。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.mcp.management_rate_limit import enforce_mcp_management_rate_limit

pytestmark = pytest.mark.no_infrastructure


class _FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True


@pytest.mark.asyncio
async def test_verify_limit_returns_429_after_threshold():
    fake = _FakeRedis()
    with patch("app.core.redis.get_redis", AsyncMock(return_value=fake)):
        for _ in range(20):
            await enforce_mcp_management_rate_limit(7, "verify")
        with pytest.raises(HTTPException) as exc:
            await enforce_mcp_management_rate_limit(7, "verify")
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_limit_is_scoped_per_user():
    fake = _FakeRedis()
    with patch("app.core.redis.get_redis", AsyncMock(return_value=fake)):
        for _ in range(20):
            await enforce_mcp_management_rate_limit(7, "verify")
        await enforce_mcp_management_rate_limit(8, "verify")


@pytest.mark.asyncio
async def test_redis_unavailable_fails_open():
    with patch("app.core.redis.get_redis", AsyncMock(return_value=None)):
        for _ in range(100):
            await enforce_mcp_management_rate_limit(7, "verify")
