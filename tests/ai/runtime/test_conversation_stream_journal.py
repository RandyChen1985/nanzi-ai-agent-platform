"""会话流事件日志服务单测（内存 FakeRedis，按项目惯例 monkeypatch get_redis）。"""
from __future__ import annotations

import logging

import pytest

from app.services.ai.runtime.conversation_stream_journal import (
    ConversationStreamJournal,
    DEFAULT_MAX_EVENTS,
    DEFAULT_TTL_SECONDS,
)

pytestmark = pytest.mark.no_infrastructure


class FakePipeline:
    """只收集命令，退出上下文时不自动执行（与 redis.asyncio 语义一致）。"""

    def __init__(self, client: "FakeRedis") -> None:
        self._client = client
        self._commands: list[tuple] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        self._commands.clear()
        return False

    def rpush(self, key: str, *values: str) -> "FakePipeline":
        self._commands.append(("rpush", key, values))
        return self

    def ltrim(self, key: str, start: int, end: int) -> "FakePipeline":
        self._commands.append(("ltrim", key, start, end))
        return self

    def expire(self, key: str, seconds: int) -> "FakePipeline":
        self._commands.append(("expire", key, seconds))
        return self

    async def execute(self) -> list:
        results = []
        for name, key, *rest in self._commands:
            if name == "rpush":
                results.append(await self._client.rpush(key, *rest[0]))
            elif name == "ltrim":
                results.append(await self._client.ltrim(key, rest[0], rest[1]))
            elif name == "expire":
                results.append(await self._client.expire(key, rest[0]))
        self._commands.clear()
        return results


class FakeRedis:
    """实现 journal 用到的最小命令集：incr / rpush / lrange / ltrim / expire / delete / pipeline。"""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}
        self.fail_on_write = False
        self.incr_calls = 0
        self.incrby_calls = 0

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    async def incr(self, key: str) -> int:
        self.incr_calls += 1
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def incrby(self, key: str, amount: int) -> int:
        self.incrby_calls += 1
        self.counters[key] = self.counters.get(key, 0) + int(amount)
        return self.counters[key]

    async def rpush(self, key: str, *values: str) -> int:
        if self.fail_on_write:
            raise RuntimeError("redis down")
        self.lists.setdefault(key, []).extend(str(value) for value in values)
        return len(self.lists[key])

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        return list(values[start:]) if end == -1 else list(values[start : end + 1])

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        values = self.lists.get(key, [])
        self.lists[key] = values[start:] if end == -1 else values[start : end + 1]
        return True

    async def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.lists:
                removed += 1
                self.lists.pop(key, None)
            if key in self.counters:
                removed += 1
                self.counters.pop(key, None)
        return removed


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _get_redis)
    return fake


def _journal() -> ConversationStreamJournal:
    return ConversationStreamJournal(max_events=5, ttl_seconds=60)


async def test_append_assigns_monotonic_seq_and_reads_after(fake_redis):
    journal = _journal()

    await journal.append("u1", "c1", [{"type": "log", "title": "a"}, {"type": "log", "title": "b"}])
    await journal.append("u1", "c1", [{"type": "log", "title": "c"}])

    batch = await journal.read_after("u1", "c1", after_seq=0)
    assert [event["title"] for event in batch["events"]] == ["a", "b", "c"]
    assert [event["_seq"] for event in batch["events"]] == [1, 2, 3]
    assert batch["next_seq"] == 3

    tail = await journal.read_after("u1", "c1", after_seq=2)
    assert [event["title"] for event in tail["events"]] == ["c"]
    assert tail["next_seq"] == 3


async def test_read_after_with_no_events_echoes_cursor(fake_redis):
    journal = _journal()
    batch = await journal.read_after("u1", "c1", after_seq=7)
    assert batch["events"] == []
    assert batch["next_seq"] == 7


async def test_append_trims_to_max_events_and_keeps_newest(fake_redis):
    journal = _journal()
    for index in range(8):
        await journal.append("u1", "c1", [{"type": "log", "title": f"t{index}"}])

    batch = await journal.read_after("u1", "c1", after_seq=0)
    assert [event["title"] for event in batch["events"]] == ["t3", "t4", "t5", "t6", "t7"]
    assert batch["next_seq"] == 8


async def test_append_sets_ttl_and_isolates_conversations(fake_redis):
    journal = _journal()
    await journal.append("u1", "c1", [{"type": "log", "title": "c1"}])
    await journal.append("u1", "c2", [{"type": "log", "title": "c2"}])
    await journal.append("u2", "c1", [{"type": "log", "title": "other-user"}])

    c1 = await journal.read_after("u1", "c1", after_seq=0)
    assert [event["title"] for event in c1["events"]] == ["c1"]
    assert fake_redis.expires, "必须为事件日志设置 TTL"


async def test_append_swallows_redis_failure(fake_redis):
    journal = _journal()
    fake_redis.fail_on_write = True
    await journal.append("u1", "c1", [{"type": "log", "title": "x"}])  # 不得抛出


async def test_append_returns_quietly_when_redis_unavailable(monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr("app.core.redis.get_redis", _none)
    journal = _journal()
    await journal.append("u1", "c1", [{"type": "log", "title": "x"}])
    batch = await journal.read_after("u1", "c1", after_seq=0)
    assert batch["events"] == []


async def test_clear_removes_log(fake_redis):
    journal = _journal()
    await journal.append("u1", "c1", [{"type": "log", "title": "x"}])
    await journal.clear("u1", "c1")
    batch = await journal.read_after("u1", "c1", after_seq=0)
    assert batch["events"] == []


async def test_clear_retries_once_before_giving_up(fake_redis):
    """清空失败只告警，但后果隐蔽：上一轮事件残留，而前端新一轮已把游标归零、traceId
    置空（否则本轮事件会被上一轮 traceId 过滤掉），残留事件就可能被当成「本轮过程」重放。

    所以首次失败后必须重试一次，成功即返回。
    """
    journal = _journal()
    await journal.append("u1", "c1", [{"type": "log", "title": "x"}])

    calls = {"count": 0}
    original_delete = fake_redis.delete

    async def flaky_delete(*keys):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("redis down")
        return await original_delete(*keys)

    fake_redis.delete = flaky_delete
    await journal.clear("u1", "c1")

    assert calls["count"] == 2, "首次失败后必须重试一次"
    batch = await journal.read_after("u1", "c1", after_seq=0)
    assert batch["events"] == [], "重试成功后日志与计数器都应被清空"


async def test_clear_reports_at_error_level_when_both_attempts_fail(fake_redis, caplog):
    """两次都失败时不抛异常（事件日志是旁路），但要用 error 级日志留下线索：
    便于从「正文里混入上一轮内容」这一现象反查到清空失败。"""
    journal = _journal()
    await journal.append("u1", "c1", [{"type": "log", "title": "x"}])

    async def always_fail(*keys):
        raise RuntimeError("redis down")

    fake_redis.delete = always_fail
    with caplog.at_level(logging.ERROR):
        await journal.clear("u1", "c1")

    messages = [record.getMessage() for record in caplog.records]
    assert any("clear failed" in message for message in messages)
    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_defaults_are_sane():
    assert DEFAULT_MAX_EVENTS >= 500
    assert DEFAULT_TTL_SECONDS >= 300


def test_event_log_capacity_covers_a_long_task_with_headroom():
    """默认容量按真实任务量级选定：2000 → 5000 → 8000。

    依据（实测 Redis 中一次真实长任务，31 步 + 长报告）：该轮共 652 条事件、71.6KB、
    平均 113B/条，其中 89% 是合并后的过程叙述增量——容量是被「生成正文期间的高频
    叙述」吃掉的。每轮 producer 启动都会 clear()，所以要覆盖的是**单轮**量：合并窗口
    1000ms 把高频增量压到约 1800 条，加上不参与合并的 log / tool / model_call 等约
    2000-2500 条。8000 条约 0.9MB/会话。锁定具体值是为了让后续再调时必须同步这处说明。

    注意单轮量并非总在这个量级内：工具输出逐条入日志，实测出现过单轮 3.6 万条。那时
    list 会被 LTRIM 截断、read_after 报 gap=true，前端据此不清空重建，缺口交给历史
    终态兜底——过程可视化有损，终态仍准确。
    """
    assert DEFAULT_MAX_EVENTS == 8000

    # 容量必须盖得住「窗口推导出的 TTL 内上限」并留出余量给不参与合并的事件，
    # 否则 key 还没过期就会先被 LTRIM 截断（两本账对不上）。
    coalesce_interval_seconds = 1.0
    ttl_window_ceiling = DEFAULT_TTL_SECONDS / coalesce_interval_seconds
    assert DEFAULT_MAX_EVENTS > 2 * ttl_window_ceiling


async def test_append_allocates_batch_with_single_incrby(fake_redis):
    """一批事件只分配一次序号，避免逐条 INCR 带来的 N 次网络往返。

    合并窗口最多能攒十几条高频增量，逐条 INCR 会让每次 flush 变成 N 次 RTT。
    用 INCRBY 预留整段序号同样保持严格单调，且只花 1 次 RTT。
    """
    journal = _journal()
    await journal.append("u1", "c1", [{"type": "log", "title": f"t{index}"} for index in range(5)])

    assert fake_redis.incrby_calls == 1
    assert fake_redis.incr_calls == 0
    batch = await journal.read_after("u1", "c1", after_seq=0)
    assert [event["_seq"] for event in batch["events"]] == [1, 2, 3, 4, 5]
    assert batch["next_seq"] == 5


async def test_append_ignores_non_dict_events_without_gaps(fake_redis):
    """非 dict 事件被跳过时不得在序号上留下空洞。"""
    journal = _journal()
    await journal.append(
        "u1", "c1", [{"type": "log", "title": "a"}, "bad", None, {"type": "log", "title": "b"}]
    )

    batch = await journal.read_after("u1", "c1", after_seq=0)
    assert [event["title"] for event in batch["events"]] == ["a", "b"]
    assert [event["_seq"] for event in batch["events"]] == [1, 2]
