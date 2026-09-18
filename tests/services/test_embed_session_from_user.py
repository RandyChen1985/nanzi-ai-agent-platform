"""嵌入场景会话令牌签发测试。

背景：`?token=` 兼容入口需要保留，但用真实 API Key 校验通过后，不应让这个长期凭据
在客户端长期驻留。这里验证「校验通过后签发可过期的 embed 会话令牌」的行为。
"""
import pytest
from httpx import AsyncClient

from app.services.embed_service import EmbedService
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio


async def test_issue_session_from_user_creates_verifiable_token(db_session):
    """签发出的 embed 会话令牌必须能被 verify_api_key 直接校验，无需任何前缀分支。"""
    user = {
        "user_id": "1",
        "user_name": "test_admin",
        "real_name": "Test Admin",
        "role": "admin",
    }

    issued = await EmbedService.issue_session_from_user(user)

    assert issued is not None, "Redis 可用时应能签发会话令牌"
    assert issued["session_token"].startswith("emb_ses_")
    assert issued["expires_in"] > 0

    verified = await AuthService.verify_api_key(issued["session_token"], db=db_session)
    assert verified is not None
    assert str(verified.get("user_id")) == "1"
    assert verified.get("session_type") == "embed"


async def test_issued_session_token_does_not_carry_the_real_api_key(db_session):
    """签发结果不得回显调用方的真实凭据。"""
    user = {
        "user_id": "1",
        "user_name": "test_admin",
        "role": "admin",
        "api_key": "TestAdmin_should_not_leak",
    }

    issued = await EmbedService.issue_session_from_user(user)

    assert issued is not None
    assert "TestAdmin_should_not_leak" not in str(issued)


async def test_user_apikey_issues_session_token_for_real_key(client: AsyncClient, admin_api_key: str):
    """用真实 API Key 校验时，响应应额外带上 embed 会话令牌。"""
    response = await client.get(
        "/api/portal/auth/user_apikey",
        headers={"X-API-Key": admin_api_key},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid"] is True
    assert data.get("session_token", "").startswith("emb_ses_")
    assert admin_api_key not in data.get("session_token", "")


async def test_user_apikey_does_not_reissue_for_existing_session(client: AsyncClient, admin_api_key: str):
    """已持有会话令牌时不应重复签发，避免每次调用都新写入一条 Redis 会话。"""
    first = await client.get(
        "/api/portal/auth/user_apikey",
        headers={"X-API-Key": admin_api_key},
    )
    session_token = first.json()["data"]["session_token"]
    assert session_token.startswith("emb_ses_")

    second = await client.get(
        "/api/portal/auth/user_apikey",
        headers={"X-API-Key": session_token},
    )

    assert second.status_code == 200
    assert "session_token" not in second.json()["data"]


async def test_issued_embed_session_is_indexed_for_revocation():
    """嵌入会话必须登记到用户索引，否则禁用/删号时吊销不到它。

    会话缓存键是令牌哈希，无法从 user_id 反查；没有索引，管理后台的禁用/降权
    对这个会话完全无效（且它会随活跃调用持续滑动续期）。
    """
    from unittest.mock import AsyncMock, patch

    redis = AsyncMock()
    user = {"user_id": "42", "user_name": "bob", "role": "user"}

    with patch("app.services.embed_service.get_redis", AsyncMock(return_value=redis)):
        issued = await EmbedService.issue_session_from_user(user)

    assert issued is not None
    redis.sadd.assert_awaited()
    assert redis.sadd.await_args.args[0] == f"{AuthService.SESSION_INDEX_PREFIX}42"


async def test_issued_embed_session_records_verified_at():
    """签发时写入 verified_at，避免登录后第一次请求就触发回源复核。"""
    from unittest.mock import AsyncMock, patch

    redis = AsyncMock()
    user = {"user_id": "42", "user_name": "bob", "role": "user"}

    with patch("app.services.embed_service.get_redis", AsyncMock(return_value=redis)):
        await EmbedService.issue_session_from_user(user)

    mapping = redis.hset.await_args.kwargs["mapping"]
    assert mapping.get("verified_at"), "缺少 verified_at，回源复核间隔无法生效"
