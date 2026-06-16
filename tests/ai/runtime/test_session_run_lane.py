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
        # reject 模式：会话繁忙时立即拒绝
        if key == "agent_session_queue_mode":
            return "reject"
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
async def test_followup_mode_waits_then_proceeds_when_released(monkeypatch):
    import asyncio

    fake = FakeRedis()
    lane = ConversationRunLane()
    key = lane._lock_key("u1", "conv-follow")
    fake.store[key] = "occupied"

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_queue_mode":
            return "followup"
        if key == "agent_session_queue_followup_wait_seconds":
            return "2"
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    async def _release_soon():
        await asyncio.sleep(0.15)
        fake.store.pop(key, None)

    asyncio.create_task(_release_soon())

    # followup 模式应等待当前 run 释放后再获取，而非立即拒绝
    async with lane.hold(
        user_id="u1",
        conversation_id="conv-follow",
        trace_id="trace-follow",
    ) as acquired:
        assert acquired is True


@pytest.mark.asyncio
async def test_followup_mode_raises_after_wait_timeout(monkeypatch):
    fake = FakeRedis()
    lane = ConversationRunLane()
    key = lane._lock_key("u1", "conv-timeout")
    fake.store[key] = "occupied"

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_queue_mode":
            return "followup"
        if key == "agent_session_queue_followup_wait_seconds":
            return "0.2"
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    # 等待超时仍未释放，回退为繁忙拒绝
    with pytest.raises(ConversationRunBusyError):
        async with lane.hold(
            user_id="u1",
            conversation_id="conv-timeout",
            trace_id="trace-timeout",
        ):
            pass


@pytest.mark.asyncio
async def test_followup_wait_mode_accepts_new_config_names(monkeypatch):
    fake = FakeRedis()
    lane = ConversationRunLane()
    key = lane._lock_key("u1", "conv-new-config")
    fake.store[key] = "occupied"

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_followup_wait_mode":
            return "reject"
        if key == "agent_session_queue_mode":
            raise AssertionError("new followup wait mode config should be checked first")
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    with pytest.raises(ConversationRunBusyError):
        async with lane.hold(
            user_id="u1",
            conversation_id="conv-new-config",
            trace_id="trace-new-config",
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
