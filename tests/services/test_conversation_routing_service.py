import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.conversation_routing_service import (
    TTL_SECONDS,
    ConversationRoutingService,
)


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_get_returns_none_when_no_record():
    """没记录必须返回 None：前端要区分「无记录」与「记录为 auto」。"""
    redis = AsyncMock()
    redis.get.return_value = None
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        assert await ConversationRoutingService.get(7, "conv-1") is None


@pytest.mark.asyncio
async def test_replace_uses_shared_ttl_and_conversation_key_shape():
    redis = AsyncMock()
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        await ConversationRoutingService.replace(7, "conv-1", "expert", "agent-1")

    assert TTL_SECONDS == 2592000
    assert ConversationRoutingService.TTL_SECONDS == TTL_SECONDS
    assert redis.set.await_args.kwargs["ex"] == TTL_SECONDS
    assert redis.set.await_args.args[0] == "conversation:7:conv-1:routing_v1"


@pytest.mark.asyncio
async def test_auto_mode_clears_expert_agent_id():
    redis = AsyncMock()
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        payload = await ConversationRoutingService.replace(7, "conv-1", "auto", "agent-1")

    assert payload == {"routing_mode": "auto", "expert_agent_id": ""}


@pytest.mark.asyncio
async def test_get_decodes_expert_record():
    redis = AsyncMock()
    redis.get.return_value = json.dumps(
        {"routing_mode": "expert", "expert_agent_id": " agent-1 "}
    )
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        record = await ConversationRoutingService.get(7, "conv-1")

    assert record == {"routing_mode": "expert", "expert_agent_id": "agent-1"}


@pytest.mark.asyncio
async def test_expert_without_agent_id_is_treated_as_missing():
    """脏数据（expert 但没有专家 ID）不能把界面锁死在一个空专家上。"""
    redis = AsyncMock()
    redis.get.return_value = json.dumps({"routing_mode": "expert", "expert_agent_id": ""})
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        assert await ConversationRoutingService.get(7, "conv-1") is None


@pytest.mark.asyncio
async def test_redis_unavailable_degrades_without_raising():
    with patch(
        "app.services.conversation_routing_service.get_redis",
        new_callable=AsyncMock,
        side_effect=RuntimeError("redis down"),
    ):
        assert await ConversationRoutingService.get(7, "conv-1") is None
        payload = await ConversationRoutingService.replace(7, "conv-1", "expert", "agent-1")
        assert payload == {"routing_mode": "expert", "expert_agent_id": "agent-1"}
