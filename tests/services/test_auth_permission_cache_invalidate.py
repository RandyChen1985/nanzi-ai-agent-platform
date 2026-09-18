"""角色/权限变更后应主动失效 auth:api_key 与权限缓存。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.auth_service import AuthService
from app.services.permission_service import PermissionService


@pytest.mark.asyncio
async def test_invalidate_user_auth_cache_deletes_api_key_hash():
    redis = AsyncMock()
    user = MagicMock(api_key_hash="abc123hash")
    session = AsyncMock()
    session.get = AsyncMock(return_value=user)

    with patch("app.services.auth_service.get_redis", AsyncMock(return_value=redis)), patch(
        "app.services.auth_service.AuthService._get_session",
        AsyncMock(return_value=(session, False)),
    ):
        await AuthService.invalidate_user_auth_cache(7)

    redis.delete.assert_any_await("auth:api_key:abc123hash")
    # 同时必须按用户吊销会话令牌：会话缓存键是「令牌哈希」，删 api_key_hash 覆盖不到，
    # 少了这一步，被禁用/降权的用户会带着旧会话继续访问。
    redis.delete.assert_any_await("auth:user_sessions:7")


@pytest.mark.asyncio
async def test_invalidate_user_auth_cache_accepts_explicit_hash():
    redis = AsyncMock()

    with patch("app.services.auth_service.get_redis", AsyncMock(return_value=redis)):
        await AuthService.invalidate_user_auth_cache(7, api_key_hash="explicit-hash")

    redis.delete.assert_any_await("auth:api_key:explicit-hash")
    redis.delete.assert_any_await("auth:user_sessions:7")


@pytest.mark.asyncio
async def test_permission_invalidate_also_clears_auth_cache_and_legacy_v2_key():
    redis = AsyncMock()
    db = AsyncMock()
    service = PermissionService(db)
    service._redis = redis

    with patch(
        "app.services.auth_service.AuthService.invalidate_user_auth_cache",
        new_callable=AsyncMock,
    ) as invalidate_auth, patch(
        "app.services.ai.config.AgentConfigProvider.invalidate_dataset_menu_cache",
        new_callable=AsyncMock,
    ):
        await service._invalidate_user_cache(42)

    redis.delete.assert_any_await("sys:auth:permissions:v3:user:42")
    redis.delete.assert_any_await("sys:auth:permissions:v2:user:42")
    invalidate_auth.assert_awaited_once()
    assert invalidate_auth.await_args.args[0] == 42
