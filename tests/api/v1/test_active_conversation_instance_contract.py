"""Active conversation API forwards the optional Embed instance scope."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints import chat
from app.services.ai.memory_service import memory_service


pytestmark = pytest.mark.no_infrastructure


def _db_with_ownership_hit(row, own_row=None):
    """归属守卫用的最小 DB 替身：``row`` 为 None 表示查不到他人会话。

    守卫会依次问两个问题——「这个 cid 里有没有别人的轮次」「有没有我自己的轮次」——
    替身必须能分别作答：只让第一个问题命中（``own_row=None``）才是越权写入；两个都命中
    是「混杂会话」（历史遗留的多人共用一个 cid），属于放行的情况。
    """
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    own_result = MagicMock()
    own_result.scalar_one_or_none.return_value = own_row
    pending = [result, own_result]

    async def _execute(*args, **kwargs):
        return pending.pop(0) if pending else own_result

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


@pytest.fixture(autouse=True)
def _no_redis_history(monkeypatch):
    """归属判定会回退查 Redis 记忆；单测里固定成「没有」，避免依赖真实 Redis。"""
    monkeypatch.setattr(memory_service, "history_exists", AsyncMock(return_value=False))


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


@pytest.mark.asyncio
async def test_set_active_conversation_allows_a_mixed_conversation_i_participate_in(monkeypatch):
    """混杂会话（同一个 cid 里也有别人的轮次）只要我自己也有轮次，就必须能继续登记。

    这类数据来自打补丁前「会话 ID 与登录身份无关」的共享键，以及宿主下发的共享 resume id。
    读侧按用户过滤，放行不会泄露他人内容；一律拦下反而让用户接手不了自己参与过的会话。
    """
    calls = []

    async def fake_set(user_id, conversation_id, instance_id=None):
        calls.append((user_id, conversation_id, instance_id))

    monkeypatch.setattr(memory_service, "set_active_conversation", fake_set)

    response = await chat.set_active_conversation(
        chat.ActiveConversationRequest(conversation_id="conv-mixed"),
        {"user_id": 7},
        instance_id="ops-assistant",
        db=_db_with_ownership_hit(12345, own_row=67890),
    )

    assert response.data["status"] == "success"
    assert calls == [(7, "conv-mixed", "ops-assistant")]
