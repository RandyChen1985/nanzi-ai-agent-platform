"""R1：审计日志 Redis 共享队列的专项测试。

覆盖多节点下进程内队列不可见问题的改造行为：
- Redis 可用时 enqueue 走 RPUSH（跨节点共享排队）
- Redis 不可用 / 配置关闭时回退进程内队列（单机/降级可用）
- Redis 消费解码（BLPOP + JSON 解码，脏数据跳过）
- 落库失败重试（_retry_count+1 重新入队）与超上限丢弃（防死循环）

全程使用 monkeypatch 隔离，不打真实 Redis / DB（no_infrastructure）。
"""
import asyncio
import json

import pytest
from unittest.mock import Mock

from app.services.audit_service import AuditService


pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _reset_queue_state():
    """每个用例前复位进程内队列 / worker 状态，避免用例间串扰。"""
    # 重新绑定进程内回退队列与停止事件，规避全局类变量跨用例污染。
    AuditService._queue = asyncio.Queue()
    AuditService._stop_event = asyncio.Event()
    AuditService._worker_task = None
    yield
    # 收尾：停止可能残留的 worker 任务
    if AuditService._worker_task is not None:
        AuditService._worker_task.cancel()


class _FakeRedis:
    """可编程的假 Redis 客户端，记录受到的写操作并允许注入出队数据。"""

    def __init__(self):
        self.rpush_calls = []
        self.pushed_items = []
        self.pop_items = []   # 供 BLPOP 依次弹出的 (key, payload) 或 None（模拟超时）
        self.llen_value = 0

    async def rpush(self, key, *payloads):
        self.rpush_calls.append((key, list(payloads)))
        self.pushed_items.extend(payloads)
        return len(payloads)

    async def blpop(self, key, timeout=0.0):
        if not self.pop_items:
            return None
        return self.pop_items.pop(0)

    async def llen(self, key):
        return self.llen_value


@pytest.mark.asyncio
async def test_enqueue_writes_to_redis_when_available(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(AuditService, "_redis_client", lambda: _awaitable(fake))

    await AuditService.enqueue_log({"trace_id": "t1", "user_name": "admin"})

    # 应走 Redis RPUSH，内存队列保持为空
    assert fake.rpush_calls and fake.rpush_calls[0][0] == AuditService._REDIS_QUEUE_KEY
    payload = json.loads(fake.pushed_items[0])
    assert payload["trace_id"] == "t1"
    assert AuditService._queue.empty()


@pytest.mark.asyncio
async def test_enqueue_falls_back_to_inmemory_when_redis_unavailable(monkeypatch):
    # _redis_client 返回 None → 应回退进程内队列
    monkeypatch.setattr(AuditService, "_redis_client", lambda: _awaitable(None))

    await AuditService.enqueue_log({"trace_id": "t2", "user_name": "admin"})

    assert not AuditService._queue.empty()
    item = AuditService._queue.get_nowait()
    assert item["trace_id"] == "t2"


@pytest.mark.asyncio
async def test_redis_pop_batch_decodes_json_and_skips_malformed(monkeypatch):
    fake = _FakeRedis()
    fake.pop_items = [
        (AuditService._REDIS_QUEUE_KEY, json.dumps({"trace_id": "ok"}, ensure_ascii=False)),
        (AuditService._REDIS_QUEUE_KEY, "{::not-json::"),   # 脏数据应被跳过
        None,  # 无更多数据 → 停止
    ]
    monkeypatch.setattr(AuditService, "_redis_client", lambda: _awaitable(fake))

    out = await AuditService._redis_pop_batch(batch_size=50)

    assert out == [{"trace_id": "ok"}]


@pytest.mark.asyncio
async def test_redis_len_falls_back_to_zero_when_redis_missing(monkeypatch):
    monkeypatch.setattr(AuditService, "_redis_client", lambda: _awaitable(None))
    assert await AuditService._redis_len() == 0

    fake = _FakeRedis()
    fake.llen_value = 7
    monkeypatch.setattr(AuditService, "_redis_client", lambda: _awaitable(fake))
    assert await AuditService._redis_len() == 7


@pytest.mark.asyncio
async def test_flush_batch_retries_then_drops_after_max_retries(monkeypatch):
    """落库失败时按 _retry_count 重试，超上限丢弃且不带内部字段回库。"""
    import app.services.audit_service as audit_module

    call_count = {"n": 0}

    class _FailingSession:
        """__aenter__ 抛异常，模拟落库失败。"""
        async def __aenter__(self):
            raise RuntimeError("db down")
        async def __aexit__(self, *exc):
            return False

    def _session_factory():
        return _FailingSession()

    monkeypatch.setattr(audit_module, "AsyncSessionLocal", _session_factory)
    call_count["n"] += 1

    # 用 fake Redis 观察 requeue 是否携带 retry_count
    fake = _FakeRedis()
    monkeypatch.setattr(AuditService, "_redis_client", lambda: _awaitable(fake))

    batch = [{"trace_id": "r1", "user_name": "u"}]

    # 第一次落库失败 → requeue 到 Redis，retry_count=1
    await AuditService._flush_batch(list(batch))
    assert fake.pushed_items, "失败批次应重新入队"
    requeued = json.loads(fake.pushed_items[-1])
    assert requeued["_retry_count"] == 1
    # requeue 不应惊扰原始 batch 对象（_flush_batch 内就地改的是其副本引用，
    # 我们断言的是 Redis 载荷携带字段，且 trace_id 保留）
    assert requeued["trace_id"] == "r1"

    # 模拟已达最大重试上限 → 不应再入队，直接丢弃
    fake2 = _FakeRedis()
    monkeypatch.setattr(AuditService, "_redis_client", lambda: _awaitable(fake2))
    maxed = [{"trace_id": "drop", "user_name": "u", "_retry_count": AuditService._MAX_RETRIES}]
    await AuditService._flush_batch(list(maxed))
    assert fake2.pushed_items == [], "超过最大重试次数的日志应被丢弃，不得死循环入队"


@pytest.mark.asyncio
async def test_flush_combines_redis_and_inmemory(monkeypatch):
    """flush 应先取 Redis 再取内存，合并落库。"""
    import app.services.audit_service as audit_module
    from sqlalchemy import insert

    fake = _FakeRedis()
    fake.pop_items = [
        (AuditService._REDIS_QUEUE_KEY, json.dumps({"trace_id": "r1"}, ensure_ascii=False)),
        None,
    ]
    monkeypatch.setattr(AuditService, "_redis_client", lambda: _awaitable(fake))

    captured = {}

    class _OkSession:
        def __init__(self):
            self.stmt = None
            self.mappings = None
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def execute(self, stmt, mappings):
            captured["stmt"] = stmt
            captured["mappings"] = mappings
        async def commit(self):
            captured["committed"] = True

    # 保留 insert（来自 sqlalchemy import），仅替换会话工厂
    monkeypatch.setattr(audit_module, "AsyncSessionLocal", lambda: _OkSession())

    # 内存也放一条
    await AuditService._queue.put({"trace_id": "m1"})

    await AuditService.flush()

    ids = sorted(m["trace_id"] for m in captured["mappings"])
    assert ids == ["m1", "r1"]
    assert captured.get("committed") is True


def _awaitable(value):
    """构造一个 await 后直接返回 value 的协程对象，避免 async lambda。"""
    async def _a():
        return value
    return _a()