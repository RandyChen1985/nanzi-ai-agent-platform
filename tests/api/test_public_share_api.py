"""访客端点契约：元信息不含内容、密钥解锁、计数时机、限流、失效统一 404。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, update

from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionHistory
from app.services import conversation_share_service as svc
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio

CONV_ID = "conv-share-public-0001"


async def _clear_unlock_failures(share_id: str) -> None:
    """清掉本用例在 Redis 里留下的解锁失败计数，避免影响后续重复运行。"""
    from app.core.redis import get_redis

    redis = await get_redis()
    if redis is None:
        return
    keys = [key async for key in redis.scan_iter(match=f"share:unlock:fail:{share_id}:*")]
    if keys:
        await redis.delete(*keys)


@pytest.fixture
async def seeded(valid_api_key):
    async with AsyncSessionLocal() as session:
        info = await AuthService.verify_api_key(valid_api_key, session)
        user_id = str(info["user_id"])
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        for index in range(2):
            session.add(AgentExecutionHistory(
                agent_id="agent-public-test", trace_id=f"trace-public-{index}",
                conversation_id=CONV_ID, user_id=user_id, username="tester",
                query=f"问{index}", summary=f"答{index}", status="success",
                created_at=datetime.now() - timedelta(minutes=10 - index),
            ))
        await session.commit()
        row = await svc.create_share(
            session, owner_user_id=user_id, owner_username="tester",
            conversation_id=CONV_ID, access_mode="secret", secret="abcd1234",
            expires_at=datetime.now() + timedelta(days=1),
        )
    yield row.share_id
    await _clear_unlock_failures(row.share_id)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        await session.execute(delete(svc.ConversationShare).where(
            svc.ConversationShare.conversation_id == CONV_ID
        ))
        await session.commit()


async def test_meta_never_leaks_messages(client, seeded):
    resp = await client.get(f"/api/v1/share/{seeded}")
    assert resp.status_code == 200, resp.text
    # 访客页面不允许被搜索引擎收录
    assert resp.headers.get("x-robots-tag") == "noindex, nofollow"
    data = resp.json()["data"]
    assert data["access_mode"] == "secret"
    assert "messages" not in data and "content_json" not in data
    assert data["owner_display"] == "tester"


async def test_content_requires_token_for_secret_share(client, seeded):
    assert (await client.get(f"/api/v1/share/{seeded}/content")).status_code == 401

    bad = await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "wrongpass"})
    assert bad.status_code == 401

    ok = await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "abcd1234"})
    assert ok.status_code == 200, ok.text
    token = ok.json()["data"]["token"]

    content = await client.get(f"/api/v1/share/{seeded}/content", params={"token": token})
    assert content.status_code == 200, content.text
    messages = content.json()["data"]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]


async def test_view_count_only_increases_on_content(client, seeded):
    async with AsyncSessionLocal() as session:
        before = await svc.get_share(session, seeded)
        assert before.view_count == 0

    await client.get(f"/api/v1/share/{seeded}")                       # 元信息不计数
    await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "wrongpass"})  # 失败不计数
    async with AsyncSessionLocal() as session:
        mid = await svc.get_share(session, seeded)
        assert mid.view_count == 0

    token = (await client.post(
        f"/api/v1/share/{seeded}/unlock", json={"secret": "abcd1234"}
    )).json()["data"]["token"]
    await client.get(f"/api/v1/share/{seeded}/content", params={"token": token})
    await client.get(f"/api/v1/share/{seeded}/content", params={"token": token})
    async with AsyncSessionLocal() as session:
        after = await svc.get_share(session, seeded)
    assert after.view_count == 2
    assert after.last_viewed_at is not None


async def test_revoked_and_expired_share_return_404(client, seeded):
    async with AsyncSessionLocal() as session:
        await session.execute(update(svc.ConversationShare).where(
            svc.ConversationShare.share_id == seeded
        ).values(status="revoked"))
        await session.commit()
    gone = await client.get(f"/api/v1/share/{seeded}")
    assert gone.status_code == 404
    # 失效响应同样带 noindex，避免被搜索引擎留下快照
    assert gone.headers.get("x-robots-tag") == "noindex, nofollow"
    assert "链接不存在或已失效" in gone.text
    assert (await client.get(f"/api/v1/share/{seeded}/content")).status_code == 404

    async with AsyncSessionLocal() as session:
        await session.execute(update(svc.ConversationShare).where(
            svc.ConversationShare.share_id == seeded
        ).values(status="active", expires_at=datetime.now() - timedelta(minutes=1)))
        await session.commit()
    assert (await client.get(f"/api/v1/share/{seeded}")).status_code == 404


async def test_unlock_is_rate_limited(client, seeded):
    for _ in range(svc.UNLOCK_FAIL_LIMIT):
        await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "wrongpass"})
    blocked = await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "abcd1234"})
    assert blocked.status_code == 429


async def test_owner_with_login_can_read_secret_share_without_token(client, seeded, valid_api_key):
    resp = await client.get(
        f"/api/v1/share/{seeded}/content",
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 200, resp.text
