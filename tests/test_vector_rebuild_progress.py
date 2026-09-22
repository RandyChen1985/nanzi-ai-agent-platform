"""向量重构进度通道（TaskLogService + 两处重构编排）契约测试。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai import local_vector_rebuild as lvr
from app.services.ai import memory_vector_rebuild as mvr
from app.services.ai.redis_index_utils import index_vector_dim
from app.services.task_log_service import TaskLogService

pytestmark = pytest.mark.no_infrastructure


# --------------------------------------------------------------------------
# 最小可用的 Redis 替身：覆盖 TaskLogService 用到的命令
# --------------------------------------------------------------------------


class _FakePipeline:
    """记录命令、execute 时统一执行；用于断言「hset + expire 是原子提交的」。"""

    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def hset(self, *args, **kwargs):
        self._ops.append(("hset", args, kwargs))
        return self

    def expire(self, *args, **kwargs):
        self._ops.append(("expire", args, kwargs))
        return self

    async def execute(self):
        out = []
        for name, args, kwargs in self._ops:
            out.append(await getattr(self._redis, name)(*args, **kwargs))
        return out


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.streams = {}
        self.values = {}
        self.expires = []
        self.pipeline_calls = 0

    async def hset(self, key, mapping=None, **kwargs):
        self.hashes.setdefault(key, {}).update(mapping or {})

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def expire(self, key, ttl):
        self.expires.append((key, ttl))

    async def xadd(self, key, fields, maxlen=None, approximate=None):
        stream = self.streams.setdefault(key, [])
        entry_id = f"{len(stream) + 1}-0"
        stream.append((entry_id, fields))
        return entry_id

    async def xrange(self, key, min=None, max=None, count=None):
        entries = self.streams.get(key, [])
        after = str(min).lstrip("(") if min else "0-0"
        out = [e for e in entries if _id_gt(e[0], after)]
        return out[:count] if count else out

    async def xread(self, streams, block=None, count=None):
        # 测试中不阻塞等待：直接返回空批次。
        return []

    def pipeline(self, transaction=True):
        self.pipeline_calls += 1
        return _FakePipeline(self)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
        return len(keys)


def _id_gt(left: str, right: str) -> bool:
    def parse(value):
        a, _, b = str(value).partition("-")
        return int(a or 0), int(b or 0)

    return parse(left) > parse(right)


class RecordingPublisher:
    """记录所有 publish 调用，供编排断言使用。"""

    def __init__(self, task_id="task_test"):
        self.task_id = task_id
        self.events = []

    def new_task_id(self):
        return self.task_id

    async def create_task(self, scope, *, task_id=None, meta=None):
        self.scope = scope
        self.meta = meta or {}
        return SimpleNamespace(task_id=task_id or self.task_id, scope=scope, status="running")

    async def publish(self, task_id, **kwargs):
        self.events.append(kwargs)
        return kwargs

    def stages(self):
        return [e.get("stage") for e in self.events]

    def terminal(self):
        return [e for e in self.events if e.get("event") in ("completed", "failed")][-1]


# --------------------------------------------------------------------------
# TaskLogService
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_log_publish_and_replay_events():
    redis = FakeRedis()
    service = TaskLogService(redis_client=redis)
    record = await service.create_task("unit_scope", meta={"trigger": "test"})

    await service.publish(
        record.task_id, event="started", stage="lock", message="开始", progress=0
    )
    await service.publish(
        record.task_id,
        event="progress",
        stage="metadata",
        message="数据集 A 完成",
        progress=50,
        done=1,
        total=2,
    )
    await service.publish(
        record.task_id,
        event="completed",
        stage="completed",
        message="全部完成",
        progress=100,
        done=2,
        total=2,
        failed_items=[{"name": "x", "error": "y"}],
    )

    events = await service.read_events(record.task_id)
    assert [e["event"] for e in events] == ["started", "progress", "completed"]
    assert events[1]["done"] == 1 and events[1]["total"] == 2
    assert events[2]["failed_items"] == [{"name": "x", "error": "y"}]
    assert all("elapsed_ms" in e for e in events)

    task = await service.get_task(record.task_id)
    assert task["status"] == "completed"
    assert task["scope"] == "unit_scope"

    # 回放语义：after_id 之后的事件不应重复返回
    assert await service.read_events(record.task_id, after_id=events[-1]["id"]) == []


@pytest.mark.asyncio
async def test_task_log_rejects_invalid_and_mismatched_terminal_event():
    redis = FakeRedis()
    service = TaskLogService(redis_client=redis)
    record = await service.create_task("unit_scope")

    with pytest.raises(ValueError):
        await service.publish(record.task_id, event="weird", stage="x", message="x")
    with pytest.raises(ValueError):
        await service.publish(record.task_id, event="completed", stage="summary", message="x")
    with pytest.raises(ValueError):
        await service.publish("missing_task", event="started", stage="lock", message="x")


@pytest.mark.asyncio
async def test_metadata_sync_log_service_keeps_legacy_keys_and_fields():
    """元数据同步任务的 Redis 键与事件字段必须保持原样（既有 SSE 契约依赖它）。"""
    from app.services.metadata_sync_log_service import MetadataSyncLogService

    redis = FakeRedis()
    service = MetadataSyncLogService(redis_client=redis)
    assert service.task_key("abc") == "metadata_sync:task:abc"
    assert service.stream_key("abc") == "metadata_sync:events:abc"

    record = await service.create_task(17)
    assert record.dataset_id == 17
    assert await service.belongs_to_dataset(record.task_id, 17) is True
    assert await service.belongs_to_dataset(record.task_id, 18) is False

    await service.publish(
        record.task_id,
        event="progress",
        stage="upload",
        message="上传中",
        progress=30,
        completed_documents=1,
        total_documents=3,
    )
    payload = json.loads(redis.streams[service.stream_key(record.task_id)][0][1]["data"])
    assert payload["dataset_id"] == 17
    assert payload["completed_documents"] == 1
    assert payload["total_documents"] == 3


# --------------------------------------------------------------------------
# 锁：释放、续期、不误删他人锁、不同用途互不阻塞
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_lock_hold_releases_and_never_steals_others_lock():
    from app.services.task_lock import TaskLock

    redis = FakeRedis()
    lock = TaskLock("test:lock", redis_client=redis)

    assert await lock.acquire("task_a") is True
    assert await lock.acquire("task_b") is False  # 已被占用
    assert await lock.renew("task_b") is False  # 续期只对持有者生效
    assert await lock.renew("task_a") is True

    async with lock.hold("task_a"):
        assert await lock.owner() == "task_a"
    # 任务结束（含异常路径）必须释放
    assert await lock.owner() is None

    # 别人的锁不能被误删
    await lock.acquire("task_a")
    await lock.release("task_b")
    assert await lock.owner() == "task_a"


@pytest.mark.asyncio
async def test_manual_task_locks_expire_much_faster_than_startup_lock():
    """手动任务锁有续期心跳，进程死掉后只需等一个短 TTL 即可重试，而非 30 分钟。"""
    from app.services.task_lock import (
        DEFAULT_TTL_SECONDS,
        local_vectors_lock,
        memory_vectors_lock,
        startup_local_vectors_lock,
    )

    manual = local_vectors_lock()
    assert manual.ttl_seconds <= 300
    assert manual.renew_interval_seconds < manual.ttl_seconds
    assert memory_vectors_lock().ttl_seconds == manual.ttl_seconds
    # 启动锁守护 fire-and-forget 同步，保持长 TTL
    assert startup_local_vectors_lock().ttl_seconds == DEFAULT_TTL_SECONDS


@pytest.mark.asyncio
async def test_task_lock_hold_releases_even_when_body_raises():
    from app.services.task_lock import TaskLock

    redis = FakeRedis()
    lock = TaskLock("test:lock", redis_client=redis)
    await lock.acquire("task_a")

    with pytest.raises(RuntimeError):
        async with lock.hold("task_a"):
            raise RuntimeError("重构中途炸了")

    assert await lock.owner() is None


@pytest.mark.asyncio
async def test_create_task_sets_ttl_atomically_so_records_never_leak():
    """hset 与 expire 必须在同一个 pipeline 里提交。

    否则进程在两者之间被杀（开发环境 --reload 常见）会留下一个**永久无 TTL**
    的任务 Hash，在 Redis 里再也清理不掉——现场就抓到过一个 TTL=-1 的残留。
    """
    redis = FakeRedis()
    service = TaskLogService(redis_client=redis)
    record = await service.create_task("unit_scope")

    assert redis.pipeline_calls == 1
    task_key = service.task_key(record.task_id)
    assert task_key in redis.hashes
    assert (task_key, service.TASK_TTL_SECONDS) in redis.expires


@pytest.mark.asyncio
async def test_publish_refreshes_task_ttl_so_long_tasks_keep_streaming():
    """任务 Hash 的 TTL 必须随每个事件续期，否则超过 30 分钟的重构会断流。"""
    redis = FakeRedis()
    service = TaskLogService(redis_client=redis)
    record = await service.create_task("unit_scope")

    await service.publish(record.task_id, event="progress", stage="metadata", message="第 1 条")
    await service.publish(record.task_id, event="progress", stage="metadata", message="第 2 条")

    task_key = service.task_key(record.task_id)
    refreshed = [key for key, _ in redis.expires if key == task_key]
    # create_task 一次 + 每次 publish 一次
    assert len(refreshed) >= 3
    assert redis.expires[-1][1] == service.TASK_TTL_SECONDS


@pytest.mark.asyncio
async def test_start_rebuild_releases_lock_when_task_record_creation_fails():
    """抢到锁之后建任务失败，必须立刻还锁，不能把用户自己挡 30 分钟。"""
    import app.services.ai.local_vector_rebuild as lvr_mod

    redis = FakeRedis()
    publisher = RecordingPublisher("vrb_fail")

    async def boom(*args, **kwargs):
        raise RuntimeError("Redis 抖动")

    publisher.create_task = boom

    with patch.object(lvr_mod, "_client", AsyncMock(return_value=redis)), \
         patch.object(lvr_mod, "task_log_service", publisher):
        with pytest.raises(RuntimeError):
            await lvr_mod.start_local_vector_rebuild(trigger="manual")

    assert await redis.get(lvr_mod.REBUILD_LOCK_KEY) is None


@pytest.mark.asyncio
async def test_startup_sync_uses_its_own_lock_key_so_manual_rebuild_is_not_blocked():
    """启动自动同步故意把锁留到 TTL，因此必须与手动重构用不同的键。"""
    from app.services.ai.local_vector_rebuild import rebuild_local_vector_indexes
    from app.services.task_lock import LOCAL_VECTORS_LOCK_KEY, STARTUP_LOCAL_VECTORS_LOCK_KEY

    assert LOCAL_VECTORS_LOCK_KEY != STARTUP_LOCAL_VECTORS_LOCK_KEY

    redis = FakeRedis()
    with patch("app.services.ai.local_vector_rebuild.settings.REDIS_ENABLE", True), \
         patch(
             "app.services.ai.local_vector_rebuild.redis.get_redis",
             AsyncMock(return_value=redis),
         ), \
         patch("app.core.orm.AsyncSessionLocal", _fake_session_ctx(1)), \
         patch(
             "app.services.metadata_service.MetadataService.get_datasets",
             AsyncMock(return_value=[_dataset(1)]),
         ), \
         patch(
             "app.services.ai.metadata_index_service.MetadataIndexService.ensure_index",
             AsyncMock(return_value=True),
         ), \
         patch(
             "app.services.ai.example_index_service.ExampleIndexService.ensure_index",
             AsyncMock(return_value=True),
         ), \
         patch(
             "app.services.ai.metadata_index_service.MetadataIndexService.sync_all_datasets",
             AsyncMock(return_value={"total": 1, "success": 1, "failed": 0}),
         ), \
         patch(
             "app.services.ai.example_index_service.ExampleIndexService.sync_all_examples",
             AsyncMock(return_value={"total": 1, "success": 1, "failed": 0, "skipped": 0}),
         ):
        res = await rebuild_local_vector_indexes(
            drop_indexes=False, acquire_lock=True, trigger="startup"
        )

    assert res["status"] == "success"
    # 启动锁保留到 TTL（防 reload 反复触发），但不占用手动重构的锁
    assert await redis.get(STARTUP_LOCAL_VECTORS_LOCK_KEY) == "startup"
    assert await redis.get(LOCAL_VECTORS_LOCK_KEY) is None


# --------------------------------------------------------------------------
# 本地向量重构编排
# --------------------------------------------------------------------------


def _fake_session_ctx(example_count=2):
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar=lambda: example_count))
    )

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    return lambda: _Ctx()


def _dataset(ds_id=1, name="销售数据集", tables=2, metrics=True, status=1):
    return SimpleNamespace(
        id=ds_id,
        name=name,
        display_name=name,
        status=status,
        tables=[SimpleNamespace(status=1, physical_name=f"t{i}") for i in range(tables)],
        metrics=[SimpleNamespace()] if metrics else [],
    )


@pytest.mark.asyncio
async def test_local_vector_rebuild_publishes_stages_and_summary():
    publisher = RecordingPublisher("vrb_test")
    redis = SimpleNamespace(
        execute_command=AsyncMock(return_value="OK"),
        get=AsyncMock(return_value="vrb_test"),
        delete=AsyncMock(),
    )

    async def fake_sync_datasets(*, progress_cb=None, wait=False):
        await progress_cb(
            {
                "phase": "dataset_done",
                "dataset_id": 1,
                "dataset_name": "销售数据集",
                "tables": 2,
                "metrics": 1,
                "failed": 0,
                "elapsed_ms": 120,
            }
        )
        return {"total": 1, "success": 1, "failed": 0}

    async def fake_sync_examples(*, progress_cb=None, wait=False):
        await progress_cb(
            {
                "phase": "example_done",
                "example_id": 7,
                "dataset_name": "销售数据集",
                "done": 1,
                "total": 2,
                "elapsed_ms": 80,
            }
        )
        await progress_cb(
            {
                "phase": "example_skipped",
                "example_id": 8,
                "dataset_name": "销售数据集",
                "done": 2,
                "total": 2,
                "reason": "无可用问题文本",
            }
        )
        return {"total": 2, "success": 1, "failed": 0, "skipped": 1}

    with patch.object(lvr, "task_log_service", publisher), \
         patch("app.core.orm.AsyncSessionLocal", _fake_session_ctx(2)), \
         patch(
             "app.services.metadata_service.MetadataService.get_datasets",
             AsyncMock(return_value=[_dataset(1), _dataset(2, "停用", status=0)]),
         ), \
         patch(
             "app.services.ai.metadata_index_service.MetadataIndexService.index_name",
             AsyncMock(return_value="nanzi:idx:metadata:dataset"),
         ), \
         patch(
             "app.services.ai.metadata_index_service.MetadataIndexService.ensure_index",
             AsyncMock(return_value=True),
         ), \
         patch(
             "app.services.ai.example_index_service.ExampleIndexService.index_name",
             AsyncMock(return_value="nanzi:idx:example:local"),
         ), \
         patch(
             "app.services.ai.example_index_service.ExampleIndexService.ensure_index",
             AsyncMock(return_value=True),
         ), \
         patch(
             "app.services.ai.metadata_index_service.MetadataIndexService.sync_all_datasets",
             fake_sync_datasets,
         ), \
         patch(
             "app.services.ai.example_index_service.ExampleIndexService.sync_all_examples",
             fake_sync_examples,
         ):
        await lvr._run_local_vector_rebuild("vrb_test", "manual", redis)

    stages = publisher.stages()
    assert stages[0] == "lock"
    assert "drop" in stages and "index" in stages and "count" in stages
    assert "metadata" in stages and "examples" in stages

    count_event = [e for e in publisher.events if e.get("stage") == "count"][0]
    # 仅统计启用数据集（1 个）与其表/指标，案例 2 条
    assert count_event["total"] == 3
    assert count_event["extra"]["dataset_count"] == 1
    assert count_event["extra"]["table_count"] == 2

    done_events = [e for e in publisher.events if e.get("done") is not None]
    assert done_events[-1]["done"] == 3
    assert done_events[-1]["total"] == 3

    terminal = publisher.terminal()
    assert terminal["event"] == "completed"
    assert terminal["stage"] == "completed"
    assert "成功 2 项" in terminal["message"]
    assert "跳过 1 项" in terminal["message"]
    assert terminal["extra"]["example_skipped"] == 1
    # 整条任务只能有一个终态事件（前端抽屉读到即停止订阅）
    terminals = [e for e in publisher.events if e.get("event") in ("completed", "failed")]
    assert len(terminals) == 1
    # 锁必须被释放（且只删自己的锁）
    redis.delete.assert_awaited()


@pytest.mark.asyncio
async def test_local_vector_rebuild_keeps_going_when_one_dataset_fails():
    publisher = RecordingPublisher("vrb_test")
    redis = SimpleNamespace(
        execute_command=AsyncMock(return_value="OK"),
        get=AsyncMock(return_value="vrb_test"),
        delete=AsyncMock(),
    )

    async def fake_sync_datasets(*, progress_cb=None, wait=False):
        await progress_cb(
            {"phase": "dataset_failed", "dataset_id": 1, "dataset_name": "坏数据集", "error": "连接超时"}
        )
        await progress_cb(
            {
                "phase": "dataset_done",
                "dataset_id": 2,
                "dataset_name": "好数据集",
                "tables": 1,
                "metrics": 0,
                "failed": 0,
                "elapsed_ms": 10,
            }
        )
        return {"total": 2, "success": 1, "failed": 1}

    with patch.object(lvr, "task_log_service", publisher), \
         patch("app.core.orm.AsyncSessionLocal", _fake_session_ctx(0)), \
         patch(
             "app.services.metadata_service.MetadataService.get_datasets",
             AsyncMock(return_value=[_dataset(1), _dataset(2, "好数据集", metrics=False)]),
         ), \
         patch(
             "app.services.ai.metadata_index_service.MetadataIndexService.index_name",
             AsyncMock(return_value="nanzi:idx:metadata:dataset"),
         ), \
         patch(
             "app.services.ai.metadata_index_service.MetadataIndexService.ensure_index",
             AsyncMock(return_value=True),
         ), \
         patch(
             "app.services.ai.example_index_service.ExampleIndexService.index_name",
             AsyncMock(return_value="nanzi:idx:example:local"),
         ), \
         patch(
             "app.services.ai.example_index_service.ExampleIndexService.ensure_index",
             AsyncMock(return_value=True),
         ), \
         patch(
             "app.services.ai.metadata_index_service.MetadataIndexService.sync_all_datasets",
             fake_sync_datasets,
         ), \
         patch(
             "app.services.ai.example_index_service.ExampleIndexService.sync_all_examples",
             AsyncMock(return_value={"total": 0, "success": 0, "failed": 0, "skipped": 0}),
         ):
        await lvr._run_local_vector_rebuild("vrb_test", "manual", redis)

    terminal = publisher.terminal()
    # 部分失败仍以 completed 收尾，但失败明细必须如实上报
    assert terminal["event"] == "completed"
    assert terminal["failed_items"] == [{"name": "数据集【坏数据集】", "error": "连接超时"}]
    assert terminal["extra"]["failed_total"] == 1
    assert any(e.get("error_detail") == "连接超时" for e in publisher.events)


@pytest.mark.asyncio
async def test_start_local_vector_rebuild_returns_none_when_lock_held():
    redis = SimpleNamespace(set=AsyncMock(return_value=None))
    with patch.object(lvr, "_client", AsyncMock(return_value=redis)):
        assert await lvr.start_local_vector_rebuild(trigger="manual") is None


# --------------------------------------------------------------------------
# 记忆向量重构编排
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_rebuild_publishes_reembed_progress():
    publisher = RecordingPublisher("mrb_test")
    redis = SimpleNamespace(
        set=AsyncMock(return_value=True),
        get=AsyncMock(return_value="mrb_test"),
        delete=AsyncMock(),
    )

    async def fake_reembed(progress_cb=None):
        await progress_cb({"phase": "scan", "total": 2})
        await progress_cb(
            {
                "phase": "item_done",
                "key": "memory:summary:1:a",
                "summary_type": "session",
                "conversation_id": "a",
                "dim": 1536,
                "done": 1,
                "total": 2,
                "elapsed_ms": 90,
            }
        )
        await progress_cb(
            {
                "phase": "item_failed",
                "key": "memory:summary:1:b",
                "summary_type": "daily",
                "conversation_id": "b",
                "done": 2,
                "total": 2,
                "elapsed_ms": 30,
                "error": "embedding 服务 502",
            }
        )
        return {"total": 2, "success": 1, "failed": 1, "skipped": 0}

    with patch.object(mvr, "task_log_service", publisher), \
         patch(
             "app.services.ai.memory_vector_rebuild.MemoryIndexService.ensure_index",
             AsyncMock(return_value=True),
         ), \
         patch(
             "app.services.ai.memory_vector_rebuild.last_ensure_result",
             return_value={
                 "ok": True,
                 "action": "ok",
                 "index_name": "nanzi:idx:memory:session_summary",
                 "index_dim": 1536,
                 "configured_dim": 1536,
             },
         ), \
         patch(
             "app.services.ai.memory_vector_rebuild.MemoryIndexService.reembed_all",
             fake_reembed,
         ), \
         patch(
             "app.services.ai.embedding_client.EmbeddingClient.get_dimensions",
             AsyncMock(return_value=1536),
         ):
        await mvr._run_memory_vector_rebuild("mrb_test", redis)

    stages = publisher.stages()
    assert stages[0] == "index"  # started 事件先宣告进入索引检查阶段
    assert "scan" in stages and "reembed" in stages
    # 抽屉读到第一个终态事件就会停止订阅：整条任务只能有一个终态
    terminals = [e for e in publisher.events if e.get("event") in ("completed", "failed")]
    assert len(terminals) == 1

    reembed_events = [e for e in publisher.events if e.get("stage") == "reembed"]
    assert reembed_events[0]["done"] == 1
    assert reembed_events[1]["error_detail"] == "embedding 服务 502"

    terminal = publisher.terminal()
    # 1 成功 1 失败属于部分失败：仍以 completed 收尾，但失败明细必须如实上报
    assert terminal["event"] == "completed"
    assert terminal["failed_items"][0]["error"] == "embedding 服务 502"
    assert terminal["done"] == 2 and terminal["total"] == 2
    assert terminal["extra"]["failed"] == 1
    redis.delete.assert_awaited()


@pytest.mark.asyncio
async def test_memory_rebuild_reports_failed_when_nothing_succeeds():
    """全部失败时必须给出 failed 终态，而不是把失败伪装成完成。"""
    publisher = RecordingPublisher("mrb_allfail")
    redis = SimpleNamespace(
        set=AsyncMock(return_value=True),
        get=AsyncMock(return_value="mrb_allfail"),
        delete=AsyncMock(),
    )

    async def fake_reembed(progress_cb=None):
        await progress_cb({"phase": "scan", "total": 1})
        await progress_cb(
            {
                "phase": "item_failed",
                "key": "memory:summary:1:x",
                "summary_type": "session",
                "conversation_id": "x",
                "done": 1,
                "total": 1,
                "error": "embedding 服务不可用",
            }
        )
        return {"total": 1, "success": 0, "failed": 1, "skipped": 0}

    with patch.object(mvr, "task_log_service", publisher), \
         patch(
             "app.services.ai.memory_vector_rebuild.MemoryIndexService.ensure_index",
             AsyncMock(return_value=True),
         ), \
         patch(
             "app.services.ai.memory_vector_rebuild.last_ensure_result",
             return_value={
                 "ok": True,
                 "action": "ok",
                 "index_name": "nanzi:idx:memory:session_summary",
                 "index_dim": 1536,
                 "configured_dim": 1536,
             },
         ), \
         patch(
             "app.services.ai.memory_vector_rebuild.MemoryIndexService.reembed_all",
             fake_reembed,
         ), \
         patch(
             "app.services.ai.embedding_client.EmbeddingClient.get_dimensions",
             AsyncMock(return_value=1536),
         ):
        await mvr._run_memory_vector_rebuild("mrb_allfail", redis)

    terminal = publisher.terminal()
    assert terminal["event"] == "failed"
    assert terminal["stage"] == "failed"
    assert terminal["failed_items"]


@pytest.mark.asyncio
async def test_memory_index_check_task_records_events_and_result():
    publisher = RecordingPublisher("mchk_test")
    with patch.object(mvr, "task_log_service", publisher), \
         patch(
             "app.services.ai.memory_vector_rebuild.MemoryIndexService.ensure_index",
             AsyncMock(return_value=True),
         ), \
         patch(
             "app.services.ai.memory_vector_rebuild.last_ensure_result",
             return_value={
                 "ok": True,
                 "action": "recreated",
                 "index_name": "nanzi:idx:memory:session_summary",
                 "index_dim": 1024,
                 "configured_dim": 1536,
             },
         ), \
         patch(
             "app.services.ai.embedding_client.EmbeddingClient.get_dimensions",
             AsyncMock(return_value=1536),
         ):
        result = await mvr.run_memory_index_check()

    assert result["task_id"] == "mchk_test"
    assert result["ok"] is True
    assert result["action"] == "recreated"
    assert "1024 → 1536" in result["message"]
    assert publisher.terminal()["event"] == "completed"
    assert any("1024 → 1536" in e.get("message", "") for e in publisher.events)


@pytest.mark.asyncio
async def test_start_memory_vector_rebuild_returns_none_when_lock_held():
    redis = SimpleNamespace(set=AsyncMock(return_value=None))
    with patch.object(mvr, "_client", AsyncMock(return_value=redis)):
        assert await mvr.start_memory_vector_rebuild(trigger="manual") is None


# --------------------------------------------------------------------------
# reembed_all：文本口径 + 写回 + 失败继续
# --------------------------------------------------------------------------


class FakeTextRedis:
    def __init__(self, keys):
        self._keys = keys
        self.writes = []

    async def scan_iter(self, match=None, count=None):
        for key in self._keys:
            yield key

    async def hset(self, key, mapping=None, **kwargs):
        self.writes.append((key, mapping))


class FakeBinaryRedis:
    def __init__(self, hashes):
        self._hashes = hashes

    async def hgetall(self, key):
        return self._hashes.get(key, {})


@pytest.mark.asyncio
async def test_reembed_all_reuses_creation_text_convention_and_keeps_going():
    from app.services.ai.memory_index_service import MemoryIndexService

    session_key = "memory:summary:1:conv-1"
    daily_key = "memory:summary:daily:1:2026-09-22"
    boom_key = "memory:summary:1:conv-boom"

    text_redis = FakeTextRedis([session_key, daily_key, boom_key])
    binary_redis = FakeBinaryRedis(
        {
            session_key: {
                b"user_id": b"1",
                b"conversation_id": b"conv-1",
                b"summary_type": b"session",
                b"title": "会话标题".encode(),
                b"summary": "会话摘要正文".encode(),
                b"key_facts": json.dumps(["事实A"], ensure_ascii=False).encode(),
                b"decisions": b"[]",
                b"open_items": b"[]",
                b"entities": b"[]",
                b"memory_type": b"general",
                b"embedding": b"\x00\x00\x80?",
            },
            daily_key: {
                b"user_id": b"1",
                b"conversation_id": b"daily",
                b"summary_type": b"daily",
                b"title": "每日标题".encode(),
                b"summary": "每日摘要".encode(),
                b"topics": json.dumps(["主题A"], ensure_ascii=False).encode(),
                b"decisions": b"[]",
                b"open_items": b"[]",
                b"entities": b"[]",
                b"embedding": b"\x00\x00\x80?",
            },
            boom_key: {
                b"user_id": b"1",
                b"conversation_id": b"conv-boom",
                b"summary_type": b"session",
                b"summary": "会失败的摘要".encode(),
                b"embedding": b"\x00\x00\x80?",
            },
        }
    )

    seen_texts = []

    async def fake_embed(text, use_global=True):
        seen_texts.append(text)
        if "会失败" in text:
            raise RuntimeError("embedding 服务 502")
        return [0.1] * 1536

    progress = []

    async def on_progress(payload):
        progress.append(payload)

    with patch(
        "app.services.ai.memory_index_service.get_redis", AsyncMock(return_value=text_redis)
    ), patch(
        "app.services.ai.memory_index_service.get_redis_binary",
        AsyncMock(return_value=binary_redis),
    ), patch(
        "app.services.ai.memory_index_service.EmbeddingClient.embed_text", fake_embed
    ):
        stats = await MemoryIndexService.reembed_all(progress_cb=on_progress)

    assert stats == {"total": 3, "success": 2, "failed": 1, "skipped": 0}
    # 会话摘要使用 SessionSummaryService 口径（标题 + 摘要 + 事实 + 类型）
    assert "会话标题" in seen_texts[0] and "事实A" in seen_texts[0] and "general" in seen_texts[0]
    # 每日摘要使用 DailySummaryService 口径（标题 + 摘要 + topics）
    assert "每日标题" in seen_texts[1] and "主题A" in seen_texts[1]
    # 失败不中断，后续记录继续处理由 total/success/failed 体现
    writes = {key: mapping for key, mapping in text_redis.writes}
    assert writes[session_key]["embedding_missing"] == "0"
    assert len(writes[session_key]["embedding"]) == 1536 * 4
    assert boom_key not in writes
    assert [p["phase"] for p in progress] == [
        "scan",
        "item_done",
        "item_done",
        "item_failed",
    ]


# --------------------------------------------------------------------------
# 端点契约（源码级断言，避免依赖完整鉴权栈）
# --------------------------------------------------------------------------


def test_endpoints_declare_task_id_and_sse_routes():
    system_src = open("app/api/portal/endpoints/system.py", encoding="utf-8").read()
    assert "/redis/rebuild-vectors/{task_id}/events" in system_src
    assert "start_local_vector_rebuild" in system_src
    assert "status_code=409" in system_src

    memory_src = open("app/api/portal/endpoints/memory.py", encoding="utf-8").read()
    assert "/vectors/rebuild" in memory_src
    assert "/vectors/rebuild/{task_id}/events" in memory_src
    assert "/index/rebuild/{task_id}/events" in memory_src
    assert "run_memory_index_check" in memory_src


def test_index_vector_dim_still_parses_async_dict_payload():
    """redis.asyncio 的 FT.INFO 返回 dict（attributes 为 dict 列表），解析必须兼容。"""
    payload = {
        "index_name": "nanzi:idx:memory:session_summary",
        "attributes": [
            {"identifier": "user_id", "type": "TAG"},
            {
                "identifier": "embedding",
                "attribute": "embedding",
                "type": "VECTOR",
                "algorithm": "HNSW",
                "dim": 1536,
                "distance_metric": "COSINE",
            },
        ],
    }
    assert index_vector_dim(payload) == 1536


@pytest.mark.asyncio
async def test_sync_helpers_default_to_background_behaviour():
    """护栏：不传 progress_cb/wait 时，同步链路必须沿用 fire-and-forget 行为。

    旧的「一键重构」与启动流程都依赖这一点：调用立即返回，真正的同步在后台跑。
    """
    from app.services.ai.example_index_service import ExampleIndexService
    from app.services.ai.metadata_index_service import MetadataIndexService

    created = []

    def _capture(coro):
        created.append(coro)
        coro.close()
        return SimpleNamespace()

    with patch("asyncio.create_task", side_effect=_capture):
        assert await MetadataIndexService.sync_local_redis_vector(1) is True
        stats = await ExampleIndexService.sync_all_examples()

    # 两条链路都走 asyncio.create_task 而不是 await 完成
    assert len(created) == 2
    assert stats == {"total": 0, "success": 0, "failed": 0, "skipped": 0}
