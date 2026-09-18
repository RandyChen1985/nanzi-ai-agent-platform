"""P0：不透明会话令牌的签发、校验与吊销。

设计要点（复用现有认证链，真 key 路径零改动）：
- 令牌写进 `auth:api_key:{sha256(token)}` —— 正是 verify_api_key 读取的键空间，
  因此下游 `ctx.api_key`（代表用户回调平台自身）无需任何改动即可继续工作。
- 登出即删除该键，实现真正的即时吊销。
- Redis 不可用时签发返回 None，调用方回退到真实 API Key，保证仍能登录。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.auth_service import AuthService


pytestmark = pytest.mark.no_infrastructure


class _FakeRedis:
    """只实现会话链路用到的 hash / set / expire / delete 语义。"""

    def __init__(self):
        self.hashes = {}
        self.ttls = {}
        self.sets = {}

    async def hset(self, key, mapping=None):
        bucket = self.hashes.setdefault(key, {})
        for field, value in (mapping or {}).items():
            bucket[field] = value if isinstance(value, bytes) else str(value)
        return len(mapping or {})

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True

    async def delete(self, *keys):
        # 与真实 Redis 一致：按「键」计数，同一键的多个存储视作一个键
        removed = 0
        for key in keys:
            if any(key in store for store in (self.hashes, self.ttls, self.sets)):
                removed += 1
            self.hashes.pop(key, None)
            self.ttls.pop(key, None)
            self.sets.pop(key, None)
        return removed

    async def sadd(self, key, *values):
        bucket = self.sets.setdefault(key, set())
        added = 0
        for value in values:
            if value not in bucket:
                bucket.add(value)
                added += 1
        return added

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def srem(self, key, *values):
        bucket = self.sets.get(key, set())
        removed = 0
        for value in values:
            if value in bucket:
                bucket.discard(value)
                removed += 1
        return removed


def _fake_user(**overrides):
    user = MagicMock()
    user.id = 7
    user.user_name = "alice"
    user.real_name = "Alice"
    user.role = "admin"
    user.dept_code = "D1"
    user.org_path = "/D1"
    user.extra_data = ""
    user.created_at = None
    user.remark = ""
    user.status = 1
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def _patch_session(user):
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    session.execute = AsyncMock(return_value=result)
    return patch(
        "app.services.auth_service.AuthService._get_session",
        AsyncMock(return_value=(session, False)),
    )


def _patch_redis(redis):
    return patch("app.services.auth_service.get_redis", AsyncMock(return_value=redis))


def _session_key(token: str) -> str:
    from app.utils.encryption import get_api_key_manager

    return f"auth:api_key:{get_api_key_manager().hash_api_key(token)}"


@pytest.mark.asyncio
async def test_create_portal_session_returns_prefixed_token_with_24h_ttl():
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    assert token is not None
    assert token.startswith(AuthService.PORTAL_SESSION_PREFIX)
    assert redis.ttls[_session_key(token)] == AuthService.PORTAL_SESSION_TTL_SECONDS
    assert AuthService.PORTAL_SESSION_TTL_SECONDS == 86400


@pytest.mark.asyncio
async def test_session_token_has_no_relationship_to_real_api_key():
    """令牌必须是独立随机值，不能由真实 API Key 推导或与之相等。"""
    redis = _FakeRedis()
    real_api_key = "real-api-key-value"

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    assert token != real_api_key
    assert real_api_key not in token
    # 同一用户两次签发也必须不同（不可预测、不可重放）
    with _patch_redis(redis), _patch_session(_fake_user()):
        second = await AuthService.create_portal_session(7)
    assert second != token


@pytest.mark.asyncio
async def test_session_token_passes_existing_verify_api_key():
    """核心集成点：现有 verify_api_key 必须直接接受会话令牌（下游调用链依赖于此）。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    # 校验阶段 Redis 命中，不应再查数据库
    session = AsyncMock()
    with _patch_redis(redis), patch(
        "app.services.auth_service.AuthService._get_session",
        AsyncMock(return_value=(session, False)),
    ):
        user_info = await AuthService.verify_api_key(token)

    assert user_info is not None
    assert user_info["user_id"] == "7"
    assert user_info["role"] == "admin"
    assert user_info.get("session_type") == "portal"
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_token_slides_ttl_on_activity():
    """活跃调用要续期，否则用户会在 24 小时后被强制登出。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    key = _session_key(token)
    redis.ttls[key] = 10  # 模拟即将过期

    with _patch_redis(redis):
        await AuthService.verify_api_key(token)

    assert redis.ttls[key] == AuthService.PORTAL_SESSION_TTL_SECONDS


@pytest.mark.asyncio
async def test_revoke_portal_session_invalidates_immediately():
    """登出必须真正吊销，而不是只删客户端 cookie。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    with _patch_redis(redis):
        await AuthService.revoke_portal_session(token)

    assert _session_key(token) not in redis.hashes

    # 吊销后校验失败：Redis 未命中 -> 回落到 DB 也查不到（会话令牌不是真 key）
    with _patch_redis(redis), _patch_session(None):
        assert await AuthService.verify_api_key(token) is None


@pytest.mark.asyncio
async def test_forged_session_token_is_rejected():
    """伪造的 sess_ 令牌不能通过校验（Redis 无对应键，DB 也查不到）。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(None):
        assert await AuthService.verify_api_key("sess_forged-token-value") is None


@pytest.mark.asyncio
async def test_missing_redis_returns_none_so_caller_can_fall_back():
    """Redis 不可用时不能阻断登录，调用方应回退到真实 API Key。"""
    with _patch_redis(None), _patch_session(_fake_user()):
        assert await AuthService.create_portal_session(7) is None


@pytest.mark.asyncio
async def test_disabled_user_cannot_get_session():
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user(status=0)):
        assert await AuthService.create_portal_session(7) is None


@pytest.mark.asyncio
async def test_real_api_key_path_is_untouched():
    """兼容性护栏：真实 API Key 的缓存命中路径不能被会话改造影响。"""
    redis = _FakeRedis()
    real_key = "real-api-key-value"
    # 模拟真 key 已有的缓存条目（无 session_type 字段）
    redis.hashes[_session_key(real_key)] = {
        "user_id": "9",
        "user_name": "bob",
        "role": "user",
        "status": "1",
    }

    session = AsyncMock()
    with _patch_redis(redis), patch(
        "app.services.auth_service.AuthService._get_session",
        AsyncMock(return_value=(session, False)),
    ):
        user_info = await AuthService.verify_api_key(real_key)

    assert user_info is not None
    assert user_info["user_id"] == "9"
    session.execute.assert_not_awaited()
    # 非会话类型不应被续期逻辑改写 TTL
    assert _session_key(real_key) not in redis.ttls


@pytest.mark.asyncio
async def test_logout_endpoint_revokes_session_from_cookie():
    """登出必须能从 cookie 取到凭据并真正吊销服务端会话。

    否则浏览器仅凭 cookie 认证时（不传 X-API-Key），登出只是本地删了 cookie，
    服务端会话依旧有效 —— 这正是本次改造要消除的问题。两个会话 Cookie 都要读：
    嵌入页刷新后只带 embed_session，漏了它同样会留下可用的会话。
    """
    from pathlib import Path

    source = Path("app/api/portal/endpoints/auth.py").read_text(encoding="utf-8")

    # 用常量读取，改名后不会因字面量不同而失效
    assert "cookies.get(PORTAL_SESSION_COOKIE_NAME)" in source
    assert "cookies.get(EMBED_SESSION_COOKIE_NAME)" in source
    assert "AuthService.revoke_portal_session" in source


@pytest.mark.asyncio
async def test_cookie_issuance_is_gated_by_the_feature_flag():
    """开关默认关闭时必须回退到真实 API Key（行为与改造前一致）。"""
    from pathlib import Path

    source = Path("app/api/portal/endpoints/auth.py").read_text(encoding="utf-8")

    assert "settings.PORTAL_SESSION_TOKEN_ENABLED" in source
    # 所有 cookie 下发放都必须走统一入口，避免遗漏某条登录路径。
    # 用 >= 而非 ==：新增登录路径不应因为「数量对不上」而误报，漏走统一入口才是问题。
    assert source.count("_issue_portal_session_cookie(") >= 6  # 1 处定义 + 5 处调用


def _fake_response():
    response = MagicMock()
    response.set_cookie = MagicMock()
    return response


@pytest.mark.asyncio
async def test_flag_off_issues_real_api_key_and_prewarms_cache():
    """P0 的核心承诺：开关关闭时行为与改造前完全一致。"""
    from app.api.portal.endpoints import auth as auth_module

    response = _fake_response()

    with patch.object(
        auth_module.settings, "PORTAL_SESSION_TOKEN_ENABLED", False
    ), patch.object(
        auth_module.AuthService, "register_online_state", AsyncMock()
    ) as prewarm:
        issued = await auth_module._issue_portal_session_cookie(
            MagicMock(), response, 7, {"user_id": "7"}, None, "real-api-key"
        )

    assert issued == "real-api-key"
    assert response.set_cookie.call_args.kwargs["value"] == "real-api-key"
    prewarm.assert_awaited_once()


@pytest.mark.asyncio
async def test_flag_on_issues_opaque_token_instead_of_real_key():
    """开关开启时 cookie 只放不透明令牌，真实 API Key 不出现在下发凭据里。"""
    from app.api.portal.endpoints import auth as auth_module

    response = _fake_response()

    with patch.object(
        auth_module.settings, "PORTAL_SESSION_TOKEN_ENABLED", True
    ), patch.object(
        auth_module.AuthService,
        "create_portal_session",
        AsyncMock(return_value="sess_opaque-token"),
    ), patch.object(
        auth_module.AuthService, "register_online_state", AsyncMock()
    ) as prewarm:
        issued = await auth_module._issue_portal_session_cookie(
            MagicMock(), response, 7, {"user_id": "7"}, None, "real-api-key"
        )

    assert issued == "sess_opaque-token"
    assert response.set_cookie.call_args.kwargs["value"] == "sess_opaque-token"
    assert "real-api-key" not in response.set_cookie.call_args.kwargs["value"]
    # 令牌已在 create_portal_session 内写好缓存，不应再按真 key 预热
    prewarm.assert_not_awaited()


def test_portal_session_token_is_enabled_by_default():
    """默认开启：浏览器不应默认持有真实 API Key。

    断言类定义的默认值，不受本地 .env 覆盖影响。
    """
    from app.core.config import Settings

    field = Settings.model_fields["PORTAL_SESSION_TOKEN_ENABLED"]
    assert field.default is True


# --- 按用户吊销索引 与 低频回源复核 ---
#
# 修复背景：会话缓存键是 auth:api_key:{sha256(token)}，无法从 user_id 反查，
# 因此 invalidate_user_auth_cache（禁用/删除用户、改角色、重置 Key 都走它）只删了
# 真实 Key 的缓存键，对会话完全无效；又因为 verify_api_key 缓存命中不查库、会话还
# 参与 24h 滑动续期，被禁用/降权的用户会带着旧身份一直用下去。下面锁定两组保护：
#   1. 签发即登记 auth:user_sessions:{user_id}，吊销时按索引成批删除；
#   2. 命中缓存也会按间隔回源复核 status/role，作为吊销遗漏路径的兜底。


def _patch_session_get(user):
    """patch `_get_session`，使回源复核用的 `session.get(User, id)` 返回给定用户。"""
    session = AsyncMock()
    session.get = AsyncMock(return_value=user)
    session.execute = AsyncMock()
    return patch(
        "app.services.auth_service.AuthService._get_session",
        AsyncMock(return_value=(session, False)),
    )


def _index_key(user_id) -> str:
    return f"{AuthService.SESSION_INDEX_PREFIX}{user_id}"


@pytest.mark.asyncio
async def test_issued_sessions_are_indexed_by_user():
    """签发时必须登记索引，否则禁用/删号时根本找不到这些会话键。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    assert _session_key(token) in redis.sets[_index_key(7)]


@pytest.mark.asyncio
async def test_revoke_sessions_for_user_kills_all_its_sessions():
    """按用户成批吊销：禁用 / 删除 / 降权 / 重置 Key 全部依赖这条路径。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        first = await AuthService.create_portal_session(7)
        second = await AuthService.create_portal_session(7)

    with _patch_redis(redis):
        removed = await AuthService.revoke_sessions_for_user(7)

    assert removed == 2
    assert _session_key(first) not in redis.hashes
    assert _session_key(second) not in redis.hashes
    assert _index_key(7) not in redis.sets


@pytest.mark.asyncio
async def test_invalidate_user_auth_cache_also_revokes_sessions():
    """核心修复：清认证缓存必须连带吊销会话，否则禁用后旧会话仍然长期有效。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    with _patch_redis(redis), _patch_session_get(_fake_user()):
        await AuthService.invalidate_user_auth_cache(7, api_key_hash="real-hash")

    assert _session_key(token) not in redis.hashes


@pytest.mark.asyncio
async def test_invalidate_revokes_sessions_even_without_api_key_hash():
    """删除用户时可能拿不到旧 hash，但会话仍必须被吊销（不能因此提前 return）。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    with _patch_redis(redis), _patch_session_get(None):
        await AuthService.invalidate_user_auth_cache(7)

    assert _session_key(token) not in redis.hashes


@pytest.mark.asyncio
async def test_disabled_user_session_is_revoked_on_reverify():
    """兜底：即使某条变更路径漏调吊销，回源复核也会在间隔后让会话失效。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    key = _session_key(token)
    redis.hashes[key]["verified_at"] = "1"  # 迫使下次校验回源

    with _patch_redis(redis), _patch_session_get(_fake_user(status=0)):
        assert await AuthService.verify_api_key(token) is None

    assert key not in redis.hashes


@pytest.mark.asyncio
async def test_reverify_refreshes_role_from_db():
    """降权后角色必须及时生效，不能一直沿用缓存里的旧 role。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    assert redis.hashes[_session_key(token)]["role"] == "admin"
    redis.hashes[_session_key(token)]["verified_at"] = "1"

    with _patch_redis(redis), _patch_session_get(_fake_user(role="user")):
        user_info = await AuthService.verify_api_key(token)

    assert user_info is not None
    assert user_info["role"] == "user"


@pytest.mark.asyncio
async def test_reverify_is_skipped_within_interval():
    """间隔内不得回源查库：热路径不能被每次请求的 DB 查询拖累。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    session = AsyncMock()
    session.get = AsyncMock(return_value=_fake_user())
    with _patch_redis(redis), patch(
        "app.services.auth_service.AuthService._get_session",
        AsyncMock(return_value=(session, False)),
    ):
        assert await AuthService.verify_api_key(token) is not None

    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_reverify_failure_does_not_kick_users_offline():
    """复核查库异常时必须 fail-open（沿用缓存），不能把在线用户整批踢下线。"""
    redis = _FakeRedis()

    with _patch_redis(redis), _patch_session(_fake_user()):
        token = await AuthService.create_portal_session(7)

    redis.hashes[_session_key(token)]["verified_at"] = "1"

    session = AsyncMock()
    session.get = AsyncMock(side_effect=RuntimeError("db down"))
    with _patch_redis(redis), patch(
        "app.services.auth_service.AuthService._get_session",
        AsyncMock(return_value=(session, False)),
    ):
        user_info = await AuthService.verify_api_key(token)

    assert user_info is not None
    assert user_info["user_id"] == "7"


@pytest.mark.asyncio
async def test_revoke_portal_session_accepts_embed_sessions():
    """登出时凭据也可能是 emb_ses_（嵌入页刷新后只带它），必须一并可吊销。"""
    redis = _FakeRedis()
    token = "emb_ses_abcdefghijklmnop"
    redis.hashes[_session_key(token)] = {"user_id": "7", "status": "1", "session_type": "embed"}
    redis.sets[_index_key(7)] = {_session_key(token)}

    with _patch_redis(redis):
        assert await AuthService.revoke_portal_session(token) is True

    assert _session_key(token) not in redis.hashes
    assert _session_key(token) not in redis.sets.get(_index_key(7), set())
