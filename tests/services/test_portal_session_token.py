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
    """只实现会话链路用到的 hash / expire / delete 语义。"""

    def __init__(self):
        self.hashes = {}
        self.ttls = {}

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

    async def delete(self, key):
        self.hashes.pop(key, None)
        self.ttls.pop(key, None)
        return 1


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
    服务端会话依旧有效 —— 这正是本次改造要消除的问题。
    """
    from pathlib import Path

    source = Path("app/api/portal/endpoints/auth.py").read_text(encoding="utf-8")

    assert 'cookies.get("admin_token")' in source
    assert "AuthService.revoke_portal_session" in source


@pytest.mark.asyncio
async def test_cookie_issuance_is_gated_by_the_feature_flag():
    """开关默认关闭时必须回退到真实 API Key（行为与改造前一致）。"""
    from pathlib import Path

    source = Path("app/api/portal/endpoints/auth.py").read_text(encoding="utf-8")

    assert "settings.PORTAL_SESSION_TOKEN_ENABLED" in source
    # 所有 cookie 下发放都必须走统一入口，避免遗漏某条登录路径
    assert source.count("_issue_admin_token_cookie(") == 6  # 1 处定义 + 5 处调用


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
        issued = await auth_module._issue_admin_token_cookie(
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
        issued = await auth_module._issue_admin_token_cookie(
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
