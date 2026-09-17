"""登录失败次数达到阈值后应临时锁定，且 Redis 故障不能阻塞正常登录。"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.auth_service import AuthService


pytestmark = pytest.mark.no_infrastructure


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value).encode()
        return value

    async def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True

    async def delete(self, key):
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return 1


def _patch_redis(redis):
    return patch("app.services.auth_service.get_redis", AsyncMock(return_value=redis))


@pytest.mark.asyncio
async def test_account_locks_only_after_configured_failures():
    redis = _FakeRedis()

    with _patch_redis(redis):
        for _ in range(AuthService.LOGIN_FAILURE_LIMIT - 1):
            await AuthService.record_login_failure("alice")

        assert await AuthService.is_login_locked("alice") is False

        await AuthService.record_login_failure("alice")
        assert await AuthService.is_login_locked("alice") is True


@pytest.mark.asyncio
async def test_failure_counter_expires_so_lock_is_temporary():
    redis = _FakeRedis()

    with _patch_redis(redis):
        await AuthService.record_login_failure("alice")

    assert redis.ttls["auth:login_fail:alice"] == AuthService.LOGIN_FAILURE_WINDOW_SECONDS


@pytest.mark.asyncio
async def test_successful_login_clears_failures():
    redis = _FakeRedis()

    with _patch_redis(redis):
        await AuthService.record_login_failure("alice")
        await AuthService.clear_login_failures("alice")

        assert await AuthService.get_login_failure_count("alice") == 0
        assert await AuthService.is_login_locked("alice") is False


@pytest.mark.asyncio
async def test_username_key_is_normalized_and_case_insensitive():
    redis = _FakeRedis()

    with _patch_redis(redis):
        await AuthService.record_login_failure("  Alice ")

        assert await AuthService.get_login_failure_count("ALICE") == 1


@pytest.mark.asyncio
async def test_missing_redis_fails_open():
    """Redis 未配置时不能因为限流把所有人挡在门外。"""
    with _patch_redis(None):
        assert await AuthService.is_login_locked("alice") is False
        assert await AuthService.record_login_failure("alice") == 0
        await AuthService.clear_login_failures("alice")


@pytest.mark.asyncio
async def test_redis_error_fails_open():
    """Redis 抖动同样必须 fail-open，否则会演变成全站无法登录。"""
    broken = AsyncMock()
    broken.get = AsyncMock(side_effect=RuntimeError("redis down"))
    broken.incr = AsyncMock(side_effect=RuntimeError("redis down"))
    broken.delete = AsyncMock(side_effect=RuntimeError("redis down"))

    with _patch_redis(broken):
        assert await AuthService.is_login_locked("alice") is False
        assert await AuthService.record_login_failure("alice") == 0
        await AuthService.clear_login_failures("alice")


@pytest.mark.asyncio
async def test_password_login_endpoint_actually_enforces_lockout():
    """接入契约：密码登录分支必须真正使用这套计数，否则等于没做。"""
    source = Path("app/api/portal/endpoints/auth.py").read_text(encoding="utf-8")

    assert "AuthService.is_login_locked" in source
    assert "AuthService.record_login_failure" in source
    assert "AuthService.clear_login_failures" in source
