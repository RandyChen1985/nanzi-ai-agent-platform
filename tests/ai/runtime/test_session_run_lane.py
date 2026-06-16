import uuid

import pytest

from app.services.ai.runtime.session_run_lane import (
    ConversationRunBusyError,
    ConversationRunLane,
)

pytestmark = pytest.mark.no_infrastructure


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def eval(self, script, numkeys, key, token):
        if self.store.get(key) == token:
            self.store.pop(key, None)
            return 1
        return 0


@pytest.mark.asyncio
async def test_conversation_run_lane_acquire_and_release(monkeypatch):
    fake = FakeRedis()

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)
    lane = ConversationRunLane()
    conversation_id = f"conv-test-{uuid.uuid4().hex}"

    handle = await lane.acquire(
        user_id="u1",
        conversation_id=conversation_id,
        trace_id="trace-1",
    )
    assert handle is not None
    key, token = handle
    assert fake.store[key] == token

    second = await lane.acquire(
        user_id="u1",
        conversation_id=conversation_id,
        trace_id="trace-2",
        wait_seconds=0.2,
    )
    assert second is None

    await lane.release(key, token)
    assert key not in fake.store

    third = await lane.acquire(
        user_id="u1",
        conversation_id=conversation_id,
        trace_id="trace-3",
        wait_seconds=0.2,
    )
    assert third is not None


@pytest.mark.asyncio
async def test_conversation_run_lane_hold_raises_when_busy(monkeypatch):
    fake = FakeRedis()
    lane = ConversationRunLane()
    key = lane._lock_key("u1", "conv-2")
    fake.store[key] = "occupied"

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    with pytest.raises(ConversationRunBusyError):
        async with lane.hold(
            user_id="u1",
            conversation_id="conv-2",
            trace_id="trace-busy",
        ):
            pass


@pytest.mark.asyncio
async def test_conversation_run_lane_skips_without_conversation_id():
    lane = ConversationRunLane()
    async with lane.hold(
        user_id="u1",
        conversation_id=None,
        trace_id="trace-none",
    ) as acquired:
        assert acquired is False
