"""所有者端点的行为契约：归属校验、回显密码、作废幂等、列表状态。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionHistory
from app.services import conversation_share_service as svc
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio

CONV_ID = "conv-share-api-0001"


@pytest.fixture
async def owner(valid_api_key):
    async with AsyncSessionLocal() as session:
        info = await AuthService.verify_api_key(valid_api_key, session)
    assert info and info.get("user_id")
    yield str(info["user_id"]), str(info.get("username") or info.get("user_name") or "tester")


@pytest.fixture
async def seeded_history(owner):
    user_id, _name = owner
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        for index, (query, summary) in enumerate([("问一", "答一"), ("问二", "答二"), ("问三", "答三")]):
            session.add(AgentExecutionHistory(
                agent_id="agent-api-test", trace_id=f"trace-api-{index}",
                conversation_id=CONV_ID, user_id=user_id, username="tester",
                query=query, summary=summary, status="success",
                created_at=datetime.now() - timedelta(minutes=30 - index),
            ))
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        await session.execute(delete(svc.ConversationShare).where(
            svc.ConversationShare.conversation_id == CONV_ID
        ))
        await session.commit()


def _headers(valid_api_key: str) -> dict:
    return {"X-API-Key": valid_api_key}


async def test_create_share_returns_url_and_secret(client, valid_api_key, seeded_history):
    resp = await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={
            "conversation_id": CONV_ID,
            "access_mode": "secret",
            "secret": "abcd1234",
            "expiry": {"mode": "preset", "preset": "7d"},
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["share_id"]
    assert f"/share/{data['share_id']}" in data["url"]
    assert data["secret"] == "abcd1234"
    assert data["expires_at"] is not None
    assert data["turn_count"] == 3


async def test_create_public_permanent_share_has_no_expiry(client, valid_api_key, seeded_history):
    resp = await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": CONV_ID, "access_mode": "public", "expiry": {"mode": "permanent"}},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["expires_at"] is None
    assert data["secret"] is None
    assert data["access_mode"] == "public"


async def test_create_rejects_missing_or_foreign_conversation(client, valid_api_key):
    resp = await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": "conv-not-mine", "access_mode": "public",
              "expiry": {"mode": "preset", "preset": "7d"}},
    )
    assert resp.status_code == 404


async def test_create_rejects_bad_secret_length(client, valid_api_key, seeded_history):
    resp = await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": CONV_ID, "access_mode": "secret", "secret": "ab",
              "expiry": {"mode": "preset", "preset": "7d"}},
    )
    assert resp.status_code == 400


async def test_list_and_revoke_flow(client, valid_api_key, seeded_history):
    created = (await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": CONV_ID, "access_mode": "secret", "secret": "abcd1234",
              "expiry": {"mode": "preset", "preset": "24h"}},
    )).json()["data"]

    listing = (await client.get("/api/portal/chat-shares", headers=_headers(valid_api_key))).json()["data"]
    item = next(row for row in listing["items"] if row["share_id"] == created["share_id"])
    assert item["status"] == "active"
    assert item["view_count"] == 0
    assert item["secret"] == "abcd1234"

    revoked = await client.post(
        f"/api/portal/chat-shares/{created['share_id']}/revoke",
        headers=_headers(valid_api_key),
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["revoked"] is True

    again = (await client.get("/api/portal/chat-shares", headers=_headers(valid_api_key))).json()["data"]
    item = next(row for row in again["items"] if row["share_id"] == created["share_id"])
    assert item["status"] == "revoked"


async def test_revoke_foreign_share_returns_404(client, valid_api_key, seeded_history):
    created = (await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": CONV_ID, "access_mode": "public",
              "expiry": {"mode": "preset", "preset": "7d"}},
    )).json()["data"]

    async with AsyncSessionLocal() as session:
        row = await svc.get_share(session, created["share_id"])
        row.owner_user_id = "another-user"
        await session.commit()

    resp = await client.post(
        f"/api/portal/chat-shares/{created['share_id']}/revoke",
        headers=_headers(valid_api_key),
    )
    assert resp.status_code == 404
