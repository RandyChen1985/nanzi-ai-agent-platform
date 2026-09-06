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
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    async def get(self, key):
        return self.store.get(key)

    async def ttl(self, key):
        return self.ttls.get(key, -1) if key in self.store else -2

    async def eval(self, script, numkeys, key, token):
        if self.store.get(key) == token:
            self.store.pop(key, None)
            return 1
        return 0

    async def delete(self, key):
        if key in self.store:
            del self.store[key]
            return 1
        return 0

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def scan_iter(self, match=None, count=50):
        prefix = (match or "").rstrip("*")
        for k in list(self.store.keys()):
            if not prefix or k.startswith(prefix):
                yield k


@pytest.mark.asyncio
async def test_conversation_run_lane_force_release(monkeypatch):
    fake = FakeRedis()
    lane = ConversationRunLane()
    conversation_id = f"conv-force-{uuid.uuid4().hex}"
    key = lane._lock_key("u1", conversation_id)
    fake.store[key] = "trace-held"

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    released = await lane.force_release(user_id="u1", conversation_id=conversation_id)
    assert released is True
    assert key not in fake.store


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
async def test_conversation_run_lane_acquire_preserves_agent_locks(monkeypatch):
    # 回归：成功取得会话 lane 后，不能自动清理同会话的 per-agent 会话锁。
    # 正常 run 持锁期间若其 lane 因续约故障 / 进程暂停 / 显式取消而先行失效，
    # 旧 run 的 executor 仍可能握着自己的 agent_lock 在跑，无条件清理会误删活跃锁、
    # 破坏互斥并并发操作同一 AgentState。孤儿 agent_lock 依靠自身 TTL 过期自愈。
    fake = FakeRedis()
    lane = ConversationRunLane()
    conversation_id = f"conv-stale-{uuid.uuid4().hex}"

    from app.services.ai.memory_service import memory_service

    agent_key = (
        f"{memory_service.KEY_PREFIX}:u1:{conversation_id}:agent_lock:main_agent"
    )
    fake.store[agent_key] = "orphan-token"

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    handle = await lane.acquire(
        user_id="u1",
        conversation_id=conversation_id,
        trace_id="trace-stale",
    )
    assert handle is not None
    # 遗留的 agent_lock 必须保留，交由 TTL 自愈，而不是被 acquire 误删
    assert fake.store.get(agent_key) == "orphan-token"


@pytest.mark.asyncio
async def test_conversation_run_lane_acquire_does_not_call_force_release(monkeypatch):
    # 取得 lane 时绝不应触发 force_release_all_for_conversation（外层取消路径
    # conversation_run_cancel 才是唯一清理点）。若误触发，活跃 run 的 agent_lock
    # 会被误删，破坏互斥。
    fake = FakeRedis()
    lane = ConversationRunLane()
    conversation_id = f"conv-norelease-{uuid.uuid4().hex}"

    from app.services.ai.memory_service import memory_service

    agent_key = (
        f"{memory_service.KEY_PREFIX}:u1:{conversation_id}:agent_lock:main_agent"
    )
    fake.store[agent_key] = "active-token"

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        return default

    called = {"flag": False}

    async def _should_not_be_called(**_kwargs):
        called["flag"] = True
        return 1

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.session_lock.AgentScopeSessionLock."
        "force_release_all_for_conversation",
        _should_not_be_called,
    )

    handle = await lane.acquire(
        user_id="u1",
        conversation_id=conversation_id,
        trace_id="trace-norelease",
    )
    assert handle is not None
    assert called["flag"] is False
    assert fake.store.get(agent_key) == "active-token"


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


@pytest.mark.asyncio
async def test_conversation_run_lane_is_locked(monkeypatch):
    fake = FakeRedis()
    lane = ConversationRunLane()
    conversation_id = f"conv-locked-{uuid.uuid4().hex}"
    key = lane._lock_key("u1", conversation_id)

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    assert await lane.is_locked(user_id="u1", conversation_id=conversation_id) is False
    fake.store[key] = "trace-locked"
    assert await lane.is_locked(user_id="u1", conversation_id=conversation_id) is True
    assert await lane.is_locked(user_id="u1", conversation_id=None) is False


@pytest.mark.asyncio
async def test_conversation_run_lane_status_exposes_trace_and_ttl(monkeypatch):
    fake = FakeRedis()
    lane = ConversationRunLane()
    conversation_id = f"conv-status-{uuid.uuid4().hex}"

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_run_lock_enabled":
            return "true"
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    assert await lane.get_status(user_id="u1", conversation_id=conversation_id) == {
        "active": False,
        "trace_id": None,
        "ttl_seconds": None,
    }
    key = lane._lock_key("u1", conversation_id)
    trace_id = str(uuid.uuid4())
    fake.store[key] = trace_id
    fake.ttls[key] = 321
    assert await lane.get_status(user_id="u1", conversation_id=conversation_id) == {
        "active": True,
        "trace_id": trace_id,
        "ttl_seconds": 321,
    }
    fake.store[key] = "untrusted-arbitrary-value"
    assert (await lane.get_status(user_id="u1", conversation_id=conversation_id))["trace_id"] is None


@pytest.mark.asyncio
async def test_conversation_run_lane_status_is_safe_when_disabled_or_unavailable(monkeypatch):
    async def _config_get(key, default=None):
        return "false" if key == "agent_session_run_lock_enabled" else default

    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)
    lane = ConversationRunLane()
    assert await lane.get_status(user_id="u1", conversation_id="disabled") == {
        "active": False,
        "trace_id": None,
        "ttl_seconds": None,
    }

    async def _redis_unavailable():
        return None

    async def _enabled_config(key, default=None):
        return "true" if key == "agent_session_run_lock_enabled" else default

    monkeypatch.setattr("app.services.config_service.ConfigService.get", _enabled_config)
    monkeypatch.setattr("app.core.redis.get_redis", _redis_unavailable)
    assert await lane.get_status(user_id="u1", conversation_id="unavailable") == {
        "active": False,
        "trace_id": None,
        "ttl_seconds": None,
    }
