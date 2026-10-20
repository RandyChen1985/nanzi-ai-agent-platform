from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.main import app


pytestmark = pytest.mark.no_infrastructure


def _override_auth(user_id: int = 7):
    app.dependency_overrides[require_api_key] = lambda: {
        "user_id": user_id,
        "user_name": f"user-{user_id}",
        "role": "user",
    }


async def _override_db():
    yield None


@pytest.fixture(autouse=True)
def _clean_overrides():
    app.dependency_overrides[get_db_session] = _override_db
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_returns_null_when_no_record():
    _override_auth()
    with patch(
        "app.api.v1.endpoints.chat.ConversationRoutingService.get",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/chat/conversation/conv-1/routing")

    assert resp.status_code == 200
    assert resp.json()["data"] is None


@pytest.mark.asyncio
async def test_get_returns_existing_record():
    _override_auth()
    record = {"routing_mode": "expert", "expert_agent_id": "agent-1"}
    with patch(
        "app.api.v1.endpoints.chat.ConversationRoutingService.get",
        new=AsyncMock(return_value=record),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/chat/conversation/conv-1/routing")

    assert resp.status_code == 200
    assert resp.json()["data"] == record


@pytest.mark.asyncio
async def test_put_rejects_expert_mode_without_agent():
    _override_auth()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/v1/chat/conversation/conv-1/routing",
            json={"routing_mode": "expert", "expert_agent_id": ""},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_rejects_unpublished_agent_with_404():
    _override_auth()
    with patch(
        "app.api.v1.endpoints.chat.AgentManagerService.resolve_embed_agent_access",
        new=AsyncMock(side_effect=LookupError("agent_not_ready")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/v1/chat/conversation/conv-1/routing",
                json={"routing_mode": "expert", "expert_agent_id": "agent-1"},
            )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_rejects_forbidden_agent_with_403():
    _override_auth()
    with patch(
        "app.api.v1.endpoints.chat.AgentManagerService.resolve_embed_agent_access",
        new=AsyncMock(side_effect=PermissionError("forbidden")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/v1/chat/conversation/conv-1/routing",
                json={"routing_mode": "expert", "expert_agent_id": "agent-1"},
            )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_auto_mode_writes_empty_agent():
    _override_auth()
    replace = AsyncMock(return_value={"routing_mode": "auto", "expert_agent_id": ""})
    with patch(
        "app.api.v1.endpoints.chat.ConversationRoutingService.replace",
        new=replace,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/v1/chat/conversation/conv-1/routing",
                json={"routing_mode": "auto", "expert_agent_id": "agent-1"},
            )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"routing_mode": "auto", "expert_agent_id": ""}
    assert replace.await_args.args[2] == "auto"
    assert replace.await_args.args[3] == ""
