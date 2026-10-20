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
        await AuthService.invalidate_user_auth_cache(7, revoke_sessions=True)

    redis.delete.assert_any_await("auth:api_key:abc123hash")
    # 管理路径（禁用/删除/降权/重置 Key）必须显式要求吊销会话：会话缓存键是
    # 「令牌哈希」，删 api_key_hash 覆盖不到，少了这一步，被禁用/降权的用户会
    # 带着旧会话继续访问。默认值刻意不是 True —— 见下一个用例。
    redis.delete.assert_any_await("auth:user_sessions:7")


@pytest.mark.asyncio
async def test_invalidate_user_auth_cache_accepts_explicit_hash():
    redis = AsyncMock()

    with patch("app.services.auth_service.get_redis", AsyncMock(return_value=redis)):
        await AuthService.invalidate_user_auth_cache(
            7, api_key_hash="explicit-hash", revoke_sessions=True
        )

    redis.delete.assert_any_await("auth:api_key:explicit-hash")
    redis.delete.assert_any_await("auth:user_sessions:7")



def _empty_async_scan_iter(*_args, **_kwargs):
    """真实 redis-py 的 scan_iter 是异步生成器；AsyncMock 会退化成 coroutine。

    PermissionService._invalidate_user_cache 用 ``async for ... in redis.scan_iter(...)``
    清理智能体作用域键族（v6 的 ``...:agent:*``），因此桩必须提供异步生成器，
    否则测到的是"mock 不支持"而不是真实行为。
    """
    async def _gen():
        for _ in ():
            yield ""

    return _gen()

@pytest.mark.asyncio
async def test_permission_invalidate_also_clears_auth_cache_and_legacy_v2_key():
    redis = AsyncMock()
    db = AsyncMock()
    service = PermissionService(db)
    redis.scan_iter = _empty_async_scan_iter
    service._redis = redis

    with patch(
        "app.services.auth_service.AuthService.invalidate_user_auth_cache",
        new_callable=AsyncMock,
    ) as invalidate_auth, patch(
        "app.services.ai.config.AgentConfigProvider.invalidate_dataset_menu_cache",
        new_callable=AsyncMock,
    ):
        await service._invalidate_user_cache(42)

    redis.delete.assert_any_await("sys:auth:permissions:v4:user:42")
    redis.delete.assert_any_await("sys:auth:permissions:v2:user:42")
    # v6 是「菜单 id 混进 elements」的旧口径，升级后必须一并清掉，否则 TTL 内看起来没修好
    redis.delete.assert_any_await("sys:auth:permissions:v6:user:42")
    invalidate_auth.assert_awaited_once()
    assert invalidate_auth.await_args.args[0] == 42


@pytest.mark.asyncio
async def test_permission_invalidate_does_not_revoke_user_sessions():
    """清权限缓存只失效缓存，绝不能吊销会话。

    回归：_invalidate_user_cache 复用了 invalidate_user_auth_cache，而后者曾无条件按
    user_id 吊销全部会话。于是「普通用户新建知识库数据集」「按角色授予数据集权限」
    这类日常操作，会把操作者（乃至整批用户）的所有设备踢到登录页。
    """
    redis = AsyncMock()
    db = AsyncMock()
    service = PermissionService(db)
    redis.scan_iter = _empty_async_scan_iter
    service._redis = redis

    with patch(
        "app.services.auth_service.AuthService.invalidate_user_auth_cache",
        new_callable=AsyncMock,
    ) as invalidate_auth, patch(
        "app.services.ai.config.AgentConfigProvider.invalidate_dataset_menu_cache",
        new_callable=AsyncMock,
    ):
        await service._invalidate_user_cache(42)

    # 仍要清认证缓存：角色/权限变了，认证缓存里的 role 必须立即失效
    invalidate_auth.assert_awaited_once()
    assert invalidate_auth.await_args.args[0] == 42
    # 但不得顺带吊销会话
    assert invalidate_auth.await_args.kwargs.get("revoke_sessions") is False
