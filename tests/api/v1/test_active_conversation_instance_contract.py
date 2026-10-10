"""Active conversation API forwards the optional Embed instance scope."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints import chat
from app.services.ai.memory_service import memory_service


pytestmark = pytest.mark.no_infrastructure


def _db_with_ownership_hit(row):
    """归属守卫用的最小 DB 替身：``row`` 为 None 表示查不到他人会话。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_get_active_conversation_forwards_instance_id(monkeypatch):
    calls = []

    async def fake_get(user_id, instance_id=None):
        calls.append((user_id, instance_id))
        return "conv-1"

    monkeypatch.setattr(memory_service, "get_active_conversation", fake_get)

    response = await chat.get_active_conversation(
        {"user_id": 7},
        instance_id="ops-assistant",
    )

    assert response.data["conversation_id"] == "conv-1"
    assert calls == [(7, "ops-assistant")]


@pytest.mark.asyncio
async def test_set_active_conversation_forwards_instance_id(monkeypatch):
    calls = []

    async def fake_set(user_id, conversation_id, instance_id=None):
        calls.append((user_id, conversation_id, instance_id))

    monkeypatch.setattr(memory_service, "set_active_conversation", fake_set)

    response = await chat.set_active_conversation(
        chat.ActiveConversationRequest(conversation_id="conv-2"),
        {"user_id": 7},
        instance_id="ops-assistant",
        db=_db_with_ownership_hit(None),
    )

    assert response.data["status"] == "success"
    assert calls == [(7, "conv-2", "ops-assistant")]


@pytest.mark.asyncio
async def test_set_active_conversation_rejects_another_users_conversation(monkeypatch):
    """换用户后本地残留的会话 ID 不得被登记成新用户的活跃会话。"""
    from fastapi import HTTPException

    calls = []

    async def fake_set(user_id, conversation_id, instance_id=None):
        calls.append((user_id, conversation_id, instance_id))

    monkeypatch.setattr(memory_service, "set_active_conversation", fake_set)

    with pytest.raises(HTTPException) as excinfo:
        await chat.set_active_conversation(
            chat.ActiveConversationRequest(conversation_id="conv-of-someone-else"),
            {"user_id": 7},
            instance_id="ops-assistant",
            db=_db_with_ownership_hit(12345),
        )

    assert excinfo.value.status_code == 403
    assert calls == []
