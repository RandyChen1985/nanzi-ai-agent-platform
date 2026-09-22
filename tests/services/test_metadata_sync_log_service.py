import pytest

from app.services.metadata_sync_log_service import MetadataSyncLogService

pytestmark = pytest.mark.no_infrastructure


class _FakePipeline:
    """create_task 用 pipeline 原子提交 hset + expire，这里只负责顺序执行。"""

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
        return [
            await getattr(self._redis, name)(*args, **kwargs)
            for name, args, kwargs in self._ops
        ]


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.streams = {}
        self.expirations = {}

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    async def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    async def hgetall(self, key):
        return self.hashes.get(key, {}).copy()

    async def expire(self, key, seconds):
        self.expirations[key] = seconds
        return True

    async def xadd(self, key, fields, maxlen=None, approximate=None):
        entries = self.streams.setdefault(key, [])
        entry_id = f"{len(entries) + 1}-0"
        entries.append((entry_id, fields.copy()))
        return entry_id

    async def xrange(self, key, min="-", max="+", count=None):
        entries = self.streams.get(key, [])
        if min.startswith("("):
            min = min[1:]
        result = [entry for entry in entries if entry[0] > min]
        return result[:count] if count else result


@pytest.mark.asyncio
async def test_create_task_returns_unpredictable_id_and_initial_state():
    service = MetadataSyncLogService(FakeRedis())

    task = await service.create_task(dataset_id=17)

    assert task.task_id.startswith("sync_")
    assert len(task.task_id) > len("sync_")
    assert task.dataset_id == 17
    assert task.status == "running"


@pytest.mark.asyncio
async def test_publish_and_read_events_preserve_order():
    service = MetadataSyncLogService(FakeRedis())
    task = await service.create_task(dataset_id=17)

    await service.publish(
        task.task_id,
        event="started",
        stage="queued",
        message="同步任务已开始",
        progress=0,
    )
    await service.publish(
        task.task_id,
        event="progress",
        stage="metadata",
        message="正在读取元数据",
        progress=30,
    )

    events = await service.read_events(task.task_id, after_id="0-0")

    assert [item["event"] for item in events] == ["started", "progress"]
    assert events[1]["progress"] == 30
    assert events[0]["dataset_id"] == 17
    assert all(item["elapsed_ms"] >= 0 for item in events)


@pytest.mark.asyncio
async def test_task_binding_rejects_wrong_dataset():
    service = MetadataSyncLogService(FakeRedis())
    task = await service.create_task(dataset_id=17)

    assert await service.belongs_to_dataset(task.task_id, 17) is True
    assert await service.belongs_to_dataset(task.task_id, 18) is False


@pytest.mark.asyncio
async def test_terminal_event_updates_task_status():
    service = MetadataSyncLogService(FakeRedis())
    task = await service.create_task(dataset_id=17)

    await service.publish(
        task.task_id,
        event="completed",
        stage="completed",
        message="同步成功",
        progress=100,
    )

    state = await service.get_task(task.task_id)
    assert state["status"] == "completed"


@pytest.mark.asyncio
async def test_publish_preserves_document_progress_counts():
    service = MetadataSyncLogService(FakeRedis())
    task = await service.create_task(dataset_id=17)

    event = await service.publish(
        task.task_id,
        event="progress",
        stage="documents",
        message="已同步文档：orders.txt",
        progress=55,
        completed_documents=3,
        total_documents=8,
    )

    assert event["completed_documents"] == 3
    assert event["total_documents"] == 8
