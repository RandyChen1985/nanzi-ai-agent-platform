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


@pytest.mark.asyncio
async def test_list_active_marks_stale_when_renewal_stopped(monkeypatch):
    """续约间隔是 ttl/3：剩余 TTL 跌破 2/3·ttl 说明续约已停，应判为 stale。"""
    fake = FakeRedis()
    lane = ConversationRunLane()
    fresh = f"conv-fresh-{uuid.uuid4().hex}"
    stale = f"conv-stale-{uuid.uuid4().hex}"
    fake.store[lane._lock_key("u1", fresh)] = str(uuid.uuid4())
    fake.store[lane._lock_key("u1", stale)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", fresh)] = 590
    fake.ttls[lane._lock_key("u1", stale)] = 60

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_run_lock_enabled":
            return "true"
        if key == "agent_session_run_lock_ttl_seconds":
            return "600"
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")

    assert snapshot["ttl_seconds"] == 600
    assert snapshot["stale_before_ttl_seconds"] == 300
    states = {item["conversation_id"]: item["state"] for item in snapshot["items"]}
    assert states[fresh] == "active"
    assert states[stale] == "stale"
    assert set(states) == {fresh, stale}


@pytest.mark.asyncio
async def test_list_active_ignores_other_users_and_expired_keys(monkeypatch):
    """只返回本人会话；TTL=-2（已过期）跳过，TTL=-1（无过期）保守判活跃。"""
    fake = FakeRedis()
    lane = ConversationRunLane()
    mine = f"conv-mine-{uuid.uuid4().hex}"
    other = f"conv-other-{uuid.uuid4().hex}"
    expired = f"conv-expired-{uuid.uuid4().hex}"
    no_expiry = f"conv-noexp-{uuid.uuid4().hex}"
    fake.store[lane._lock_key("u1", mine)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", mine)] = 500
    fake.store[lane._lock_key("u2", other)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u2", other)] = 500
    fake.store[lane._lock_key("u1", expired)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", expired)] = -2
    fake.store[lane._lock_key("u1", no_expiry)] = str(uuid.uuid4())
    fake.ttls.pop(lane._lock_key("u1", no_expiry), None)

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_run_lock_enabled":
            return "true"
        if key == "agent_session_run_lock_ttl_seconds":
            return "600"
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")
    ids = {item["conversation_id"] for item in snapshot["items"]}

    assert mine in ids
    assert no_expiry in ids
    assert other not in ids
    assert expired not in ids
    by_id = {item["conversation_id"]: item for item in snapshot["items"]}
    assert by_id[no_expiry]["state"] == "active"
    assert by_id[no_expiry]["ttl_seconds"] is None


@pytest.mark.asyncio
async def test_list_active_returns_empty_when_disabled(monkeypatch):
    """锁功能关闭时列表为空，且不触碰 Redis。"""
    fake = FakeRedis()
    lane = ConversationRunLane()
    fake.store[lane._lock_key("u1", "conv-x")] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", "conv-x")] = 500

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        return "false" if key == "agent_session_run_lock_enabled" else default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")
    assert snapshot["items"] == []


@pytest.mark.asyncio
async def test_list_active_tolerates_renewal_jitter(monkeypatch):
    """续约抖动绝不能把活跃锁误判成僵死。

    续约间隔恰好是 ttl/3，所以正常活跃锁的剩余 TTL 下限（2/3·ttl）与「续约已停」
    只差一个事件循环延迟。若阈值贴着 2/3·ttl，流式输出/工具密集期的一次调度延迟
    就会把正在跑的会话标成「疑似僵死」——用户点「强制释放」会真取消这个 run
    （cancel 接口对存在 handle 的会话是硬取消，不是只删锁）。
    因此阈值必须留出余量：剩余 TTL 掉到一半以下才算续约异常。
    """
    fake = FakeRedis()
    lane = ConversationRunLane()
    # 380 已经低于一个续约周期（400），但远高于阈值 300 → 属于正常抖动，判活跃
    jittered = f"conv-jitter-{uuid.uuid4().hex}"
    # 280 低于阈值 → 续约确实已停
    stalled = f"conv-stalled-{uuid.uuid4().hex}"
    fake.store[lane._lock_key("u1", jittered)] = str(uuid.uuid4())
    fake.store[lane._lock_key("u1", stalled)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", jittered)] = 380
    fake.ttls[lane._lock_key("u1", stalled)] = 280

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_run_lock_enabled":
            return "true"
        if key == "agent_session_run_lock_ttl_seconds":
            return "600"
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")
    by_id = {item["conversation_id"]: item["state"] for item in snapshot["items"]}

    assert by_id[jittered] == "active", "续约抖动被误判为僵死，会让用户误取消正在运行的会话"
    assert by_id[stalled] == "stale"
    assert snapshot["stale_before_ttl_seconds"] == 300


@pytest.mark.asyncio
async def test_list_active_parses_bytes_keys_and_rejects_foreign_uid(monkeypatch):
    """Redis 返回 bytes key 时要能正常解析；uid 段必须二次校验，防止 match 扩大后串号。"""

    class BytesKeyFakeRedis(FakeRedis):
        """模拟真实 redis-py：key 以 bytes 返回。

        同时故意让 scan_iter 多吐一条「前缀相同但 uid 不同」的 key，
        用来验证实现层必须按 uid 段二次校验，而不是把匹配结果照单全收。
        """

        async def scan_iter(self, match=None, count=50):
            for k in list(self.store.keys()):
                yield k.encode("utf-8")
            yield b"nanzi:conv_run:u1-other:conv-foreign"

    fake = BytesKeyFakeRedis()
    lane = ConversationRunLane()
    mine = f"conv-bytes-{uuid.uuid4().hex}"
    fake.store[lane._lock_key("u1", mine)] = str(uuid.uuid4())
    fake.ttls[lane._lock_key("u1", mine)] = 500
    fake.store["nanzi:conv_run:u1-other:conv-foreign"] = str(uuid.uuid4())
    fake.ttls["nanzi:conv_run:u1-other:conv-foreign"] = 500

    async def _redis():
        return fake

    async def _config_get(key, default=None):
        if key == "agent_session_run_lock_enabled":
            return "true"
        if key == "agent_session_run_lock_ttl_seconds":
            return "600"
        return default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")
    ids = {item["conversation_id"] for item in snapshot["items"]}

    # 精确断言：多出任何一条都说明 uid 段没被二次校验（会把别人的会话串进来）
    assert ids == {mine}


def test_glob_escape_neutralizes_redis_wildcards():
    """uid 里的 glob 元字符必须被转义，否则 SCAN 模式会扩散到别人的会话键。"""
    from app.services.ai.runtime.session_run_lane import ConversationRunLane

    escape = getattr(ConversationRunLane, "_glob_escape")
    assert escape("u1") == "u1"
    assert escape("u*") == "u\\*"
    assert escape("u?") == "u\\?"
    assert escape("u[1]") == "u\\[1\\]"
    assert escape("u\\x") == "u\\\\x"


@pytest.mark.asyncio
async def test_list_active_returns_empty_when_redis_unavailable(monkeypatch):
    """Redis 不可用时返回空列表而不是抛异常。"""
    lane = ConversationRunLane()

    async def _redis():
        return None

    async def _config_get(key, default=None):
        return "true" if key == "agent_session_run_lock_enabled" else default

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr("app.services.config_service.ConfigService.get", _config_get)

    snapshot = await lane.list_active(user_id="u1")
    assert snapshot["items"] == []
