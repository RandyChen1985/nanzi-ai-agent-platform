# EmbedChat 切页面后实时续显（Redis 事件日志 + 增量轮询）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 EmbedChat 在平台内切走页面再切回后，不仅能立刻看到已输出的思考与正文，还能**持续实时看到后续的思考、工具时间线与正文推进**，直到本轮结束由落库历史无缝接管。

**Architecture:** 后端 producer 把流式事件按单调递增 `seq` 追加进 Redis 事件日志（限长 + TTL，高频增量事件按时间窗口合并后写入），并新增一个按 `after_seq` 增量拉取的只读端点；前端在运行恢复器之外增加一条"事件续显"轮询（约 1s），把增量事件喂给**已有的消息分发器** `applyPermissionStreamEvent(msg, data)`，复用与正常流完全一致的渲染逻辑。事件日志只负责"过程可视化"，**最终内容一律以落库历史为准**，从而保证日志丢失/重复不会污染终态。

**Tech Stack:** FastAPI + redis.asyncio（`app.core.redis.get_redis`）、`ConfigService` 读配置、pytest（`no_infrastructure` 纯单测 + 源码契约测）、Vue 3 + TypeScript、`pytest --confcutdir=tests/frontend` + `vue-tsc --noEmit`。

**关键前置事实（已核实，实施时不要假设相反）:**
- SSE 帧格式：`data: {json}\n\n`，结束帧 `data: [DONE]\n\n`，静默期 keepalive `data: {"type":"keepalive"}\n\n`（`app/api/v1/endpoints/chat.py:1793-1840`）。
- producer 在客户端断开后**继续消费生成器**，但当前会丢弃输出：`if not client_disconnected_event.is_set(): await queue.put(("chunk", chunk))`（`chat.py:1654`）。事件日志必须写在**这个 if 之外**，否则断开后无日志可拉。
- 前端已存在组件级通用分发器 `applyPermissionStreamEvent(msg: Message, data: any)`（`frontend/src/views/EmbedChat.vue:8222`），内部已处理 `run_status` / `error` / `meta` / `log` / `router_log` / citations / `reusable_result_status` / 正文增量，并转交 `dispatchAgentscopeStreamEvent`（`frontend/src/utils/agentscopeSseHandlers.ts:837`）。**恢复路径直接复用它，不要另写渲染逻辑。**
- 上一轮已实现的流式快照 `frontend/src/utils/embedStreamSnapshot.ts` 与本计划配合：快照负责切回瞬间的"已有内容"，事件日志负责其后的"持续推进"。
- `applyPermissionStreamEvent(msg, data)` 内部已完整覆盖 `log` / `router_log` / `meta` / `error` / `citation` / `reusable_result_status` / 正文（`answer` 与 `data.content`）/ `run_status`，并把 `answer_delta` / `reasoning_content` / `thinking` / `tool_result_data` 等转交 `dispatchAgentscopeStreamEvent`（`EmbedChat.vue:8222-8338`）。恢复路径逐条调用它即可，**不得另写渲染分支**。
- 幂等有第二道防线：`appendAssistantBodyDelta` 内置 `isDuplicateAssistantBodyDelta` 去重（`frontend/src/utils/agentscopeSseHandlers.ts:712-744`），即使游标丢失导致重复回放，正文也不会被叠加两次。
- 鉴权 helper 的真实名字是 `embedAuthHeaders()`（`EmbedChat.vue:4625-4631`，返回 `{ Authorization, X-API-Key }`，无 token 时返回 `undefined`）；`run-status` 轮询用 `axios.get` 而非 `fetch`，事件拉取保持一致。
- Redis key 与配置约定：key 形如 `nanzi:conv_run:{uid}:{safe_cid}`（`safe_cid = conversation_id.replace(":", "_")`），TTL/开关通过 `ConfigService.get("<key>", "<default>")` 读取（`app/services/ai/runtime/session_run_lane.py:32-61`）。Redis 惰性获取：`from app.core.redis import get_redis; redis = await get_redis(); if redis is None: return`。
- Agent **不执行** `./dev.sh`、部署脚本或 `git commit`；每个 Task 末尾只做本地验证，提交由用户自行完成。
- **后端测试必须用 `python3 -m pytest tests/...`**（或先 `export PYTHONPATH=$PWD`，即 `tests/run_tests.sh:15` 的做法）。直接用 `pytest tests/...` 会报 `ModuleNotFoundError: No module named 'app'`——这是调用方式问题，**不得**用 `sys.path` 补丁去"修"它。前端契约测试不受影响（`pytest --confcutdir=tests/frontend ...` 可正常使用）。

---

## 文件结构

**后端**
- Create: `app/services/ai/runtime/conversation_stream_journal.py` —— 会话流事件日志的唯一实现（seq 分配、追加、读取、清理、限长与 TTL），可注入 redis 以便测试。
- Modify: `app/api/v1/endpoints/chat.py` —— ① producer 把事件写入日志（含高频增量合并）；② 新增 `GET /conversation/{conversation_id}/stream-events` 增量拉取端点。
- Create: `tests/ai/runtime/test_conversation_stream_journal.py` —— 日志服务纯单测（内存 FakeRedis + monkeypatch `app.core.redis.get_redis`）。
- Create: `tests/test_chat_stream_event_journal_contract.py` —— producer 接线与端点存在的契约测。
- Modify: `tests/CHECKLIST.md`

**前端**
- Create: `frontend/src/utils/conversationStreamReplay.ts` —— 纯函数：进度合并、事件过滤、断层判定。
- Modify: `frontend/src/views/EmbedChat.vue` —— 恢复期"事件续显"轮询，复用 `applyPermissionStreamEvent`。
- Modify: `frontend/src/utils/embedStreamSnapshot.ts` —— 快照增加 `lastSeq` 字段（幂等重放的关键）。
- Create: `tests/frontend/test_conversation_stream_replay_contract.py` —— 纯函数行为 + EmbedChat 接线契约。
- Modify: `docs/superpowers/plans/2026-09-23-embed-run-recovery.md`（追加关联说明）

---

## Task 1: 会话流事件日志服务

**Files:**
- Create: `app/services/ai/runtime/conversation_stream_journal.py`
- Test: `tests/ai/runtime/test_conversation_stream_journal.py`

**设计约束（必须遵守）：**
- seq 由 Redis `INCR` 分配，按 `(user_id, conversation_id)` 维度单调递增；每条事件写入时把 `_seq` 注入 payload 副本。这与既有先例完全同构：`app/services/ai/memory_service.py:741-755` 就是「独立计数器 INCR 分配单调 seq + RPUSH + LTRIM + EXPIRE」，其 docstring（`:246-251`）明确说明「与 list 索引解耦，即使 ltrim 压缩了索引，seq 仍严格单调递增」——本服务沿用同一思路。
- 写入走 pipeline（`async with redis.pipeline() as pipe:` + `await pipe.execute()`），与 `app/services/ai/context_compaction_log_service.py:137-141` 的 `rpush/ltrim/expire` 三连写法一致：既减少 RTT，也让「追加 + 截断 + 续期」原子化。
- 列表用 `LTRIM -N -1` 限长。**限长只丢最旧的过程事件，不影响终态正确性**（终态以落库历史为准）。
- 读取按 `after_seq` 过滤并返回 `next_seq`（本次返回的最大 seq，无事件时回显入参），前端据此推进。
- 任何 Redis 异常都不得影响对话主流程：`append` 吞掉异常并 `logger.warning`，`read_after` 异常返回空批次。
- Redis 惰性获取（`from app.core.redis import get_redis; redis = await get_redis(); if redis is None: return`），与 `session_run_lane.py:172-176` 一致；测试按项目惯例用 `monkeypatch.setattr("app.core.redis.get_redis", ...)` 替换（参照 `tests/ai/runtime/test_session_run_lane.py:55-65`）。

- [x] **Step 1: 写失败测试**

创建 `tests/ai/runtime/test_conversation_stream_journal.py`：

```python
"""会话流事件日志服务单测（内存 FakeRedis，按项目惯例 monkeypatch get_redis）。"""
from __future__ import annotations

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

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
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


def test_defaults_are_sane():
    assert DEFAULT_MAX_EVENTS >= 500
    assert DEFAULT_TTL_SECONDS >= 300
```

- [x] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/ai/runtime/test_conversation_stream_journal.py -q`
Expected: FAIL（`ModuleNotFoundError: app.services.ai.runtime.conversation_stream_journal`）

- [x] **Step 3: 实现日志服务**

创建 `app/services/ai/runtime/conversation_stream_journal.py`：

```python
"""会话流事件日志：把正在运行的对话流事件按序暂存到 Redis，供断线恢复期增量拉取。

设计要点：
- 事件日志**只用于过程可视化**。本轮最终内容一律以落库历史为准，因此日志被
  LTRIM 截断、TTL 过期或 Redis 抖动都不会破坏终态正确性。
- seq 由 Redis INCR 分配，(user_id, conversation_id) 维度单调递增，与
  memory_service 的会话消息 seq 同一思路：计数器与 list 索引解耦，ltrim 压缩
  索引不影响序号单调性。前端按 after_seq 增量拉取，避免重复渲染。
- 任何异常都不得影响对话主流程：写入失败只告警，读取失败返回空批次。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services.ai.conversation_identity import require_user_id

logger = logging.getLogger(__name__)

DEFAULT_MAX_EVENTS = 2000
DEFAULT_TTL_SECONDS = 1800
CONFIG_KEY_MAX_EVENTS = "chat_stream_journal_max_events"
CONFIG_KEY_TTL_SECONDS = "chat_stream_journal_ttl_seconds"


class ConversationStreamJournal:
    """按 (user, conversation) 保存流式事件的有界日志。"""

    def __init__(self, *, max_events: int | None = None, ttl_seconds: int | None = None) -> None:
        self._max_events = max_events
        self._ttl_seconds = ttl_seconds

    @staticmethod
    async def _redis():
        from app.core.redis import get_redis

        return await get_redis()

    @staticmethod
    def _base_key(user_id: str | int | None, conversation_id: str) -> str:
        uid = require_user_id(user_id)
        safe_cid = str(conversation_id or "").replace(":", "_")
        return f"nanzi:conv_stream:{uid}:{safe_cid}"

    @classmethod
    def _list_key(cls, user_id: str | int | None, conversation_id: str) -> str:
        return cls._base_key(user_id, conversation_id)

    @classmethod
    def _seq_key(cls, user_id: str | int | None, conversation_id: str) -> str:
        return f"{cls._base_key(user_id, conversation_id)}:seq"

    async def _limits(self) -> tuple[int, int]:
        max_events = self._max_events
        ttl_seconds = self._ttl_seconds
        try:
            from app.services.config_service import ConfigService

            if max_events is None:
                raw = await ConfigService.get(CONFIG_KEY_MAX_EVENTS, str(DEFAULT_MAX_EVENTS))
                try:
                    max_events = max(50, int(raw))
                except (TypeError, ValueError):
                    max_events = DEFAULT_MAX_EVENTS
            if ttl_seconds is None:
                raw = await ConfigService.get(CONFIG_KEY_TTL_SECONDS, str(DEFAULT_TTL_SECONDS))
                try:
                    ttl_seconds = max(60, int(raw))
                except (TypeError, ValueError):
                    ttl_seconds = DEFAULT_TTL_SECONDS
        except Exception:  # noqa: BLE001 - 配置读取失败时退回默认值
            max_events = max_events or DEFAULT_MAX_EVENTS
            ttl_seconds = ttl_seconds or DEFAULT_TTL_SECONDS
        return max_events, ttl_seconds

    async def append(
        self,
        user_id: str | int | None,
        conversation_id: str,
        events: list[dict[str, Any]],
    ) -> None:
        """追加事件；失败只告警，不影响对话主流程。"""
        if not conversation_id or not events:
            return
        try:
            redis = await self._redis()
            if redis is None:
                return
            list_key = self._list_key(user_id, conversation_id)
            seq_key = self._seq_key(user_id, conversation_id)
            payloads: list[str] = []
            for event in events:
                if not isinstance(event, dict):
                    continue
                seq = await redis.incr(seq_key)
                payload = dict(event)
                payload["_seq"] = int(seq)
                payloads.append(json.dumps(payload, ensure_ascii=False))
            if not payloads:
                return
            max_events, ttl_seconds = await self._limits()
            async with redis.pipeline() as pipe:
                pipe.rpush(list_key, *payloads)
                pipe.ltrim(list_key, -max_events, -1)
                pipe.expire(list_key, ttl_seconds)
                pipe.expire(seq_key, ttl_seconds)
                await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - 事件日志是尽力而为的旁路
            logger.warning(
                "[StreamJournal] append failed for conversation=%s: %s",
                conversation_id,
                exc,
            )

    async def read_after(
        self,
        user_id: str | int | None,
        conversation_id: str,
        *,
        after_seq: int = 0,
    ) -> dict[str, Any]:
        """读取 seq > after_seq 的事件；失败返回空批次。"""
        cursor = int(after_seq or 0)
        try:
            redis = await self._redis()
            if redis is None:
                return {"events": [], "next_seq": cursor}
            raw_items = await redis.lrange(self._list_key(user_id, conversation_id), 0, -1)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[StreamJournal] read failed for conversation=%s: %s",
                conversation_id,
                exc,
            )
            return {"events": [], "next_seq": cursor}

        events: list[dict[str, Any]] = []
        next_seq = cursor
        for raw in raw_items or []:
            try:
                event = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            try:
                seq = int(event.get("_seq") or 0)
            except (TypeError, ValueError):
                continue
            if seq <= cursor:
                continue
            events.append(event)
            next_seq = max(next_seq, seq)
        return {"events": events, "next_seq": next_seq}

    async def clear(self, user_id: str | int | None, conversation_id: str) -> None:
        if not conversation_id:
            return
        try:
            redis = await self._redis()
            if redis is None:
                return
            await redis.delete(
                self._list_key(user_id, conversation_id),
                self._seq_key(user_id, conversation_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[StreamJournal] clear failed for conversation=%s: %s",
                conversation_id,
                exc,
            )


conversation_stream_journal = ConversationStreamJournal()
```

- [x] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/ai/runtime/test_conversation_stream_journal.py -q`
Expected: PASS（8 passed）

- [x] **Step 5: 本地验证（提交由用户执行）**

Run: `python3 -m pytest tests/ai/runtime/test_conversation_stream_journal.py -q`
Expected: PASS。**不要执行 `git commit`。**

---

## Task 2: producer 写入事件日志（含高频增量合并）

**Files:**
- Modify: `app/api/v1/endpoints/chat.py`（`_producer_task`，约 1629-1690；`_producer_task` 定义前的信号量/上下文声明处）
- Test: `tests/test_chat_stream_event_journal_contract.py`

**设计约束：**
- 写入必须发生在 `if not client_disconnected_event.is_set()` **之外**，否则断开后拿不到日志。
- 高频事件（`answer_delta` / `answer` / `reasoning_content` / `thinking` / `process_narration*`）**不逐条写**：累积到缓冲，按时间窗口（默认 300ms）或长度阈值（默认 2000 字符）flush；其余事件立即写入。
- 终态（`run_status` 或循环结束）时强制 flush，保证过程事件完整。
- 事件写入失败必须静默（journal 已吞异常），不得影响落库与锁释放。

- [x] **Step 1: 写失败测试**

创建 `tests/test_chat_stream_event_journal_contract.py`：

```python
"""契约：聊天流 producer 必须把流式事件写入会话事件日志，并暴露增量拉取端点。"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

CHAT_ENDPOINT = Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/chat.py"


def _source() -> str:
    return CHAT_ENDPOINT.read_text(encoding="utf-8")


def test_producer_writes_events_even_after_client_disconnect():
    source = _source()
    assert "conversation_stream_journal" in source
    # 写入必须独立于 client_disconnected_event 判定，否则断开后无日志可拉
    assert "await _flush_stream_journal(force=True)" in source
    assert "def _record_stream_event(" in source


def test_producer_coalesces_high_frequency_deltas():
    source = _source()
    assert "STREAM_JOURNAL_COALESCE_INTERVAL_SECONDS" in source
    assert "STREAM_JOURNAL_COALESCE_MAX_CHARS" in source
    assert "STREAM_JOURNAL_COALESCED_TYPES" in source


def test_stream_events_endpoint_reads_journal_after_cursor():
    source = _source()
    assert '"/conversation/{conversation_id}/stream-events"' in source
    assert "class ConversationStreamEventsResponse(BaseModel):" in source
    assert "after_seq: int = 0" in source
```

- [x] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_chat_stream_event_journal_contract.py -q`
Expected: FAIL（断言不成立）

- [x] **Step 3: 实现 producer 接线**

在 `app/api/v1/endpoints/chat.py` 的 `_producer_task` 定义**之前**加入常量与缓冲声明（紧邻 `client_disconnected_event = asyncio.Event()`）：

```python
        # ---- 流式事件日志（断线恢复期增量拉取）----
        # 高频增量不逐条落 Redis：按时间窗口/长度阈值合并后再写，避免 token 级
        # 事件把 Redis 压垮；其余事件立即写入。写入独立于 client_disconnected_event，
        # 否则客户端断开后就没有日志可供切回时拉取。
        STREAM_JOURNAL_COALESCED_TYPES = {
            "answer",
            "answer_delta",
            "reasoning_content",
            "thinking",
            "process_narration",
            "process_narration_commit",
            "process_narration_promote",
        }
        STREAM_JOURNAL_COALESCE_INTERVAL_SECONDS = 0.3
        STREAM_JOURNAL_COALESCE_MAX_CHARS = 2000
        stream_journal_pending: list[dict[str, Any]] = []
        stream_journal_chars = 0
        stream_journal_last_flush = time.monotonic()

        async def _flush_stream_journal(*, force: bool = False) -> None:
            nonlocal stream_journal_chars, stream_journal_last_flush
            if not stream_journal_pending:
                return
            now = time.monotonic()
            if (
                not force
                and now - stream_journal_last_flush < STREAM_JOURNAL_COALESCE_INTERVAL_SECONDS
                and stream_journal_chars < STREAM_JOURNAL_COALESCE_MAX_CHARS
            ):
                return
            batch = list(stream_journal_pending)
            stream_journal_pending.clear()
            stream_journal_chars = 0
            stream_journal_last_flush = now
            from app.services.ai.runtime.conversation_stream_journal import (
                conversation_stream_journal,
            )

            # 注意：_producer_task 作用域内没有裸 `user_id`，真实变量是 lane_user_id
            # （1660 行 `lane_user_id = chat_user_id`，即 _require_chat_user_id 解析出的稳定用户 ID，
            #  按计划字面写 user_id 会 NameError；且它满足 journal 的 require_user_id fail-closed 要求）。
            await conversation_stream_journal.append(lane_user_id, conversation_id, batch)

        async def _record_stream_event(chunk: Any) -> None:
            nonlocal stream_journal_chars
            if not isinstance(chunk, dict):
                return
            event_type = str(chunk.get("type") or "")
            if event_type in STREAM_JOURNAL_COALESCED_TYPES:
                stream_journal_pending.append(chunk)
                stream_journal_chars += len(str(chunk.get("content") or ""))
                await _flush_stream_journal()
                return
            await _flush_stream_journal(force=True)
            from app.services.ai.runtime.conversation_stream_journal import (
                conversation_stream_journal,
            )

            await conversation_stream_journal.append(lane_user_id, conversation_id, [chunk])
```

然后在 `_producer_task` 的 `async for chunk in ...` 循环体里，把记录放在**断开判定之前**：

```python
                    if isinstance(chunk, dict):
                        if chunk.get("trace_id"):
                            claim_trace_id = str(chunk["trace_id"])
                        if chunk.get("type") == "error" or chunk.get("status") == "error":
                            claim_status = "failed"
                    # 事件日志独立于连接状态：客户端断开后 producer 仍继续跑，
                    # 这些事件是切回页面时"继续实时显示"的唯一来源。
                    await _record_stream_event(chunk)
                    if not client_disconnected_event.is_set():
                        await queue.put(("chunk", chunk))
```

并在循环结束、`terminal_enqueued` 收尾处补一次强制 flush（保证最后一批增量入库）：

```python
                if not terminal_enqueued and not client_disconnected_event.is_set():
                    await queue.put(("done", None))
                await _flush_stream_journal(force=True)
```

`chat.py` 顶部已导入所需符号，**无需新增导入**：`import time`（`chat.py:3`）、`from typing import List, Optional, AsyncGenerator, Dict, Any, Union, Literal`（`chat.py:7`）、`from pydantic import BaseModel, Field`（`chat.py:10`）。

- [x] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_chat_stream_event_journal_contract.py -q`
Expected: **2 passed, 1 failed**。其中 `test_stream_events_endpoint_reads_journal_after_cursor` 断言的是
Task 3 的产物（路由字符串、`ConversationStreamEventsResponse`、`after_seq`），此时尚未实现，保持红是正常的；
它在 Task 3 实现端点后转绿。（原稿此处写「3 passed」是计划自身的排序缺陷，已修正。）

- [x] **Step 5: 本地验证（提交由用户执行）**

Run: `python3 -m pytest tests/test_chat_stream_event_journal_contract.py tests/ai/runtime/test_conversation_stream_journal.py -q`
Expected: PASS。**不要执行 `git commit`。**

---

## Task 3: 增量拉取端点

**Files:**
- Modify: `app/api/v1/endpoints/chat.py`（紧跟 `get_conversation_run_status` 之后，约 913-943）
- Test: `tests/test_chat_stream_event_journal_contract.py`（复用 Task 2 的文件，追加用例）

- [x] **Step 1: 追加失败测试**

在 `tests/test_chat_stream_event_journal_contract.py` 末尾追加：

```python
def test_stream_events_endpoint_reuses_run_status_auth_and_shape():
    source = _source()
    assert "async def get_conversation_stream_events(" in source
    assert "user_info: Dict[str, Any] = Depends(require_api_key)" in source
    assert "_require_chat_user_id(user_info)" in source
    assert "StandardResponse(data=ConversationStreamEventsResponse(**payload))" in source
    assert "run_active" in source
```

- [x] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_chat_stream_event_journal_contract.py -q`
Expected: FAIL（新用例失败）

- [x] **Step 3: 实现端点**

在 `chat.py` 中 `get_conversation_run_status` 之后插入：

```python
class ConversationStreamEventsResponse(BaseModel):
    events: List[Dict[str, Any]] = Field(default_factory=list)
    next_seq: int = 0
    run_active: bool = False


@router.get(
    "/conversation/{conversation_id}/stream-events",
    response_model=StandardResponse[ConversationStreamEventsResponse],
    summary="增量拉取会话正在运行的过程事件",
    description=(
        "读取 Redis 中的会话流事件日志，返回 seq 大于 after_seq 的事件，"
        "供 EmbedChat 在切回页面后继续显示思考、工具时间线与正文推进。"
        "事件日志只用于过程可视化，本轮最终内容仍以历史接口为准。"
    ),
)
async def get_conversation_stream_events(
    conversation_id: str,
    after_seq: int = 0,
    user_info: Dict[str, Any] = Depends(require_api_key),
):
    from app.services.ai.runtime.conversation_stream_journal import conversation_stream_journal
    from app.services.ai.runtime.session_run_lane import conversation_run_lane

    user_id = _require_chat_user_id(user_info)
    batch = await conversation_stream_journal.read_after(
        user_id=user_id,
        conversation_id=conversation_id,
        after_seq=after_seq,
    )
    try:
        run_status = await conversation_run_lane.get_status(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        run_active = bool(run_status.get("active"))
    except Exception:
        run_active = False
    payload = {
        "events": batch.get("events") or [],
        "next_seq": int(batch.get("next_seq") or 0),
        "run_active": run_active,
    }
    return StandardResponse(data=ConversationStreamEventsResponse(**payload))
```

同样无需新增导入（`List` / `Field` / `Dict` / `Any` / `BaseModel` 见 `chat.py:7-10`）。

- [x] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_chat_stream_event_journal_contract.py -q`
Expected: PASS（4 passed）

- [x] **Step 5: 本地验证（提交由用户执行）**

Run: `python3 -m pytest tests/test_chat_stream_event_journal_contract.py -q`
Expected: PASS。**不要执行 `git commit`。**

---

## Task 4: 前端进度合并纯函数

**Files:**
- Create: `frontend/src/utils/conversationStreamReplay.ts`
- Modify: `frontend/src/utils/embedStreamSnapshot.ts`（快照新增 `lastSeq`）
- Test: `tests/frontend/test_conversation_stream_replay_contract.py`

**设计约束：**
- 事件应用必须**幂等**：进度游标 `lastSeq` 随快照持久化，恢复时用 `snapshot.lastSeq` 作为 `after_seq`，避免切回/刷新后重复叠加正文。
- 必须能识别**断层**：当服务端返回的最小 seq 大于 `lastSeq + 1`（日志被 LTRIM 截断）时标记 `gap`，由调用方决定是否退回"快照 + 终态同步"路径。
- 必须能识别**异轮事件**：事件带 `trace_id` 且与当前草稿不一致时，不得写入当前消息。

- [x] **Step 1: 写失败测试**

创建 `tests/frontend/test_conversation_stream_replay_contract.py`：

```python
"""契约：EmbedChat 事件续显的进度合并纯函数。"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

MODULE_PATH = "frontend/src/utils/conversationStreamReplay.ts"


def _call(expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(MODULE_PATH)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_keeps_events_after_cursor_and_advances():
    result = _call(
        """
return api.planStreamReplay(
  [
    { _seq: 1, type: 'log', title: 'a' },
    { _seq: 2, type: 'log', title: 'b' },
    { _seq: 3, type: 'log', title: 'c' },
  ],
  { lastSeq: 1, traceId: 't1' },
)
"""
    )
    assert [event["title"] for event in result["events"]] == ["b", "c"]
    assert result["lastSeq"] == 3
    assert result["gap"] is False


def test_detects_gap_when_journal_trimmed():
    result = _call(
        """
return api.planStreamReplay(
  [ { _seq: 9, type: 'log', title: 'x' } ],
  { lastSeq: 2, traceId: 't1' },
)
"""
    )
    assert result["gap"] is True
    assert result["lastSeq"] == 9


def test_drops_events_from_another_turn():
    result = _call(
        """
return api.planStreamReplay(
  [
    { _seq: 1, type: 'log', title: 'mine', trace_id: 't1' },
    { _seq: 2, type: 'log', title: 'stale', trace_id: 't0' },
    { _seq: 3, type: 'log', title: 'no-trace' },
  ],
  { lastSeq: 0, traceId: 't1' },
)
"""
    )
    assert [event["title"] for event in result["events"]] == ["mine", "no-trace"]


def test_ignores_malformed_events_and_keeps_cursor():
    result = _call(
        """
return api.planStreamReplay(
  [ null, 'nope', { type: 'log', title: 'no-seq' }, { _seq: 4, type: 'log', title: 'ok' } ],
  { lastSeq: 3, traceId: '' },
)
"""
    )
    assert [event["title"] for event in result["events"]] == ["ok"]
    assert result["lastSeq"] == 4


def test_empty_batch_echoes_cursor():
    result = _call(
        """
return api.planStreamReplay([], { lastSeq: 12, traceId: 't1' })
"""
    )
    assert result["events"] == []
    assert result["lastSeq"] == 12
    assert result["gap"] is False
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_conversation_stream_replay_contract.py -q`
Expected: FAIL（模块不存在）

- [x] **Step 3: 实现纯函数**

创建 `frontend/src/utils/conversationStreamReplay.ts`：

```ts
/**
 * 会话流事件续显的进度合并：把服务端增量返回的事件过滤成"可安全写入本轮草稿"的一批。
 *
 * 幂等性来自 lastSeq：它随流式快照一起持久化，恢复时作为 after_seq 起点，
 * 因此切回页面或刷新后不会把已渲染过的正文再叠加一次。
 */

export interface StreamReplayEvent {
  _seq?: number;
  type?: string;
  trace_id?: string;
  content?: string;
  [key: string]: unknown;
}

export interface StreamReplayCursor {
  lastSeq?: number;
  traceId?: string;
}

export interface StreamReplayPlan {
  events: StreamReplayEvent[];
  lastSeq: number;
  gap: boolean;
}

const readSeq = (event: unknown): number | null => {
  if (!event || typeof event !== "object") return null;
  const raw = (event as StreamReplayEvent)._seq;
  const seq = Number(raw);
  return Number.isFinite(seq) && seq > 0 ? seq : null;
};

export function planStreamReplay(
  events: unknown,
  cursor: StreamReplayCursor | null | undefined,
): StreamReplayPlan {
  const afterSeq = Number(cursor?.lastSeq ?? 0) || 0;
  const expectedTraceId = String(cursor?.traceId || "").trim();
  const list = Array.isArray(events) ? events : [];

  const accepted: StreamReplayEvent[] = [];
  let lastSeq = afterSeq;
  let gap = false;

  for (const raw of list) {
    const seq = readSeq(raw);
    if (seq === null || seq <= afterSeq) continue;
    const event = raw as StreamReplayEvent;
    const eventTraceId = String(event.trace_id || "").trim();
    // 上一轮残留事件不得写入本轮草稿；不带 trace 的过程事件（如 keepalive 类）照常放行。
    if (expectedTraceId && eventTraceId && eventTraceId !== expectedTraceId) {
      lastSeq = Math.max(lastSeq, seq);
      continue;
    }
    if (!accepted.length && seq > afterSeq + 1) gap = true;
    accepted.push(event);
    lastSeq = Math.max(lastSeq, seq);
  }

  return { events: accepted, lastSeq, gap };
}
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_conversation_stream_replay_contract.py -q`
Expected: PASS（5 passed）

- [x] **Step 5: 给快照增加 lastSeq 字段**

修改 `frontend/src/utils/embedStreamSnapshot.ts`：在 `EmbedStreamSnapshot` 接口加 `lastSeq?: number;`，在 `EmbedStreamSnapshotInput` 加 `lastSeq?: number;`，并在 `buildEmbedStreamSnapshot` 的 `base` 对象里加入：

```ts
    ...(Number.isFinite(Number(input?.lastSeq)) && Number(input?.lastSeq) > 0
      ? { lastSeq: Number(input?.lastSeq) }
      : {}),
```

随后在 `tests/frontend/test_embed_stream_snapshot_contract.py` 追加一条断言用例：

```python
def test_build_keeps_replay_cursor():
    snapshot = _call(
        """
return api.buildEmbedStreamSnapshot({
  conversationId: 'c1',
  traceId: 't1',
  lastSeq: 42,
  draft: { content: '正文' },
  now: 1000,
})
"""
    )
    assert snapshot["lastSeq"] == 42
```

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_embed_stream_snapshot_contract.py tests/frontend/test_conversation_stream_replay_contract.py -q`
Expected: PASS

- [x] **Step 6: 本地验证（提交由用户执行）**

Run: `pytest --confcutdir=tests/frontend tests/frontend -q`
Expected: 全绿。**不要执行 `git commit`。**

---

## Task 5: EmbedChat 恢复期增量消费

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`
- Test: `tests/frontend/test_conversation_stream_replay_contract.py`（追加接线断言）

**设计约束：**
- 复用现有分发器 `applyPermissionStreamEvent(msg, data)`（`EmbedChat.vue:8222`）与 `appendAssistantBodyDelta`，**不要新写渲染分支**。
- 轮询只在"run-status active 且本地存在本轮草稿"时运行；间隔 1000ms；终态（`run_active === false` 或收到 `run_status` 事件）立即停止并转交既有的历史终态同步。
- 每批事件应用后节流写回快照（含 `lastSeq`），保证刷新/切走再次恢复时不重复渲染。
- 检测到 `gap` 时不中断用户，但记录告警并按现有逻辑继续（终态仍以历史为准）。

- [x] **Step 1: 追加失败测试**

在 `tests/frontend/test_conversation_stream_replay_contract.py` 末尾追加：

```python
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _embed_source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_embed_chat_polls_stream_events_with_cursor():
    source = _embed_source()
    assert 'from "@/utils/conversationStreamReplay"' in source
    assert "const pollConversationStreamEvents = async (" in source
    assert "STREAM_REPLAY_POLL_INTERVAL_MS" in source
    assert "/conversation/${encodeURIComponent(cid)}/stream-events" in source
    assert "params: { after_seq: streamReplayCursor.value.lastSeq }" in source


def test_embed_chat_applies_replay_events_through_shared_dispatcher():
    source = _embed_source()
    # 必须复用既有分发器，避免恢复期与正常流的渲染逻辑分叉
    assert "applyPermissionStreamEvent(replayTarget, event)" in source
    assert "planStreamReplay(" in source


def test_embed_chat_persists_replay_cursor_into_snapshot():
    source = _embed_source()
    assert "lastSeq: streamReplayCursor.value.lastSeq" in source
```

- [x] **Step 2: 运行测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_conversation_stream_replay_contract.py -q`
Expected: FAIL（接线断言不成立）

- [x] **Step 3: 实现恢复期事件轮询**

在 `EmbedChat.vue` 顶部 import 区加入：

```ts
import { planStreamReplay } from "@/utils/conversationStreamReplay";
```

**注意 `frontend/tsconfig.app.json:13-14` 开启了 `noUnusedLocals` 与 `noUnusedParameters`**：只导入实际用到的符号（`StreamReplayEvent` 类型本任务用不到，不要导入），否则 `vue-tsc --noEmit` 会直接失败。

在流式快照 helper 区域（`clearStreamSnapshot` 附近）加入状态与轮询实现：

```ts
const STREAM_REPLAY_POLL_INTERVAL_MS = 1000;

/** 事件续显游标：lastSeq 会随快照持久化，保证重复恢复不会叠加正文。 */
const streamReplayCursor = ref<{ lastSeq: number; traceId: string }>({ lastSeq: 0, traceId: "" });
let streamReplayTimer: ReturnType<typeof setTimeout> | null = null;
let streamReplayInFlight = false;

const clearStreamReplayTimer = () => {
  if (streamReplayTimer !== null) {
    clearTimeout(streamReplayTimer);
    streamReplayTimer = null;
  }
};

/**
 * 切回页面后继续显示本轮正在产生的思考/工具时间线/正文。
 * 事件日志只做过程可视化：终态仍由 syncLatestSessionHistory 以落库历史为准。
 */
const pollConversationStreamEvents = async () => {
  const cid = conversationId.value;
  if (!cid || streamReplayInFlight) return;
  streamReplayInFlight = true;
  try {
    const response = await axios.get(
      `/api/v1/chat/conversation/${encodeURIComponent(cid)}/stream-events`,
      {
        params: { after_seq: streamReplayCursor.value.lastSeq },
        headers: embedAuthHeaders(),
      },
    );
    const data = (response.data?.data || {}) as Record<string, any>;
    const plan = planStreamReplay(data.events, streamReplayCursor.value);
    if (plan.gap) {
      console.warn("[EmbedChat] 事件日志出现断层，过程展示可能不完整，终态仍以历史为准");
    }
    const replayTarget = activeStreamDraft?.msg ?? null;
    if (replayTarget) {
      for (const event of plan.events) {
        applyPermissionStreamEvent(replayTarget, event as Record<string, any>);
      }
      streamReplayCursor.value = { lastSeq: plan.lastSeq, traceId: String(replayTarget.trace_id || "") };
      maybePersistStreamSnapshot(true);
      await nextTick();
      scrollToBottom();
    } else {
      streamReplayCursor.value = { ...streamReplayCursor.value, lastSeq: plan.lastSeq };
    }
    if (data.run_active === false) {
      // 本轮已结束：停止续显，转交既有历史终态同步（syncLatestSessionHistory）。
      clearStreamReplayTimer();
    }
  } catch (error) {
    console.warn("[EmbedChat] 拉取会话流事件失败", error);
  } finally {
    streamReplayInFlight = false;
  }
};

const scheduleStreamReplay = () => {
  clearStreamReplayTimer();
  if (document.visibilityState !== "visible") return;
  streamReplayTimer = setTimeout(async () => {
    await pollConversationStreamEvents();
    if (streamReplayTimer !== null) scheduleStreamReplay();
  }, STREAM_REPLAY_POLL_INTERVAL_MS);
};
```

续显目标取自 `activeStreamDraft.msg`（模块级 `let`，`EmbedChat.vue:4495`），因此 `restoreEmbedStreamSnapshot` 在注入草稿后**必须登记**它，否则切回后没有可推进的目标消息、卸载时也无法强制落盘：

```ts
  const restoredAgent = restored[restored.length - 1];
  activeStreamDraft = { conversationId: cid, msg: restoredAgent };
```

请求一律走既有的 `axios` 实例与 `embedAuthHeaders()`，与同文件的 `run-status` 轮询（`4638-4651`）和 history 拉取（`9306-9309`）保持一致。

在恢复器启用处（`scheduleRunRecoverySync` 的 active 分支）追加：

```ts
      scheduleStreamReplay();
```

在 `watch(remoteRunActive, ...)` 的 false 分支、`onVisibilityChange` 的 hidden 分支、`onUnmountHandlers` 与 `onUnmounted` 中，均追加 `clearStreamReplayTimer();`（hidden 时仅暂停轮询，不丢弃游标）。

在 `maybePersistStreamSnapshot` 写入快照时带上游标：

```ts
  persistStreamSnapshot(active.conversationId, active.msg, currentStreamUserMessage(), {
    lastSeq: streamReplayCursor.value.lastSeq,
  });
```

`persistStreamSnapshot` 相应扩展第四个可选参数，把游标写进快照（完整修改后实现）：

```ts
const persistStreamSnapshot = (
  cid: string,
  draft: Message,
  userMsg?: Message,
  cursor?: { lastSeq?: number },
) => {
  if (!cid) return;
  const snapshot = buildEmbedStreamSnapshot({
    conversationId: cid,
    traceId: draft.trace_id,
    lastSeq: cursor?.lastSeq,
    user: userMsg ? { content: userMsg.content, timestamp: userMsg.timestamp } : null,
    draft: {
      content: draft.content,
      reasoningContent: draft.reasoningContent,
      processTimeline: draft.processTimeline,
      agentName: draft.agentName,
      agentDisplayName: draft.agentDisplayName,
      agentType: draft.agentType,
      agentAvatarUrl: draft.agentAvatarUrl,
    },
  });
  if (!snapshot) return;
  try {
    sessionStorage.setItem(streamSnapshotStorageKey(cid), JSON.stringify(snapshot));
  } catch {
    // 超配额或隐私模式：放弃本次快照，不影响对话本身。
  }
};
```

`restoreEmbedStreamSnapshot` 在注入草稿后必须同时完成「登记续显目标」与「恢复游标」两件事，插入位置是 `messages.value = [...messages.value, ...restored];` 之后：

```ts
  const restoredAgent = restored[restored.length - 1];
  activeStreamDraft = { conversationId: cid, msg: restoredAgent };
  streamReplayCursor.value = {
    lastSeq: Number(snapshot.lastSeq || 0) || 0,
    traceId: String(snapshot.traceId || ""),
  };
```

- [x] **Step 4: 运行测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_conversation_stream_replay_contract.py -q`
Expected: PASS

- [x] **Step 5: 类型检查与全量回归**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: exit 0，无输出

Run: `pytest --confcutdir=tests/frontend tests/frontend -q`
Expected: 全绿（含既有 1130 项）

- [x] **Step 6: 本地验证（提交由用户执行）**

**不要执行 `git commit`。**

---

## Task 6: 文档与验收清单

**Files:**
- Modify: `tests/CHECKLIST.md`
- Modify: `docs/superpowers/plans/2026-09-23-embed-run-recovery.md`

- [x] **Step 1: 追加 CHECKLIST 行**

在 `tests/CHECKLIST.md` 末尾追加一行，说明：现象（切回后过程不再推进）、方案（Redis 事件日志 + 1s 增量轮询 + 复用 `applyPermissionStreamEvent`）、安全性质（终态以历史为准）、配置项（`chat_stream_journal_max_events` / `chat_stream_journal_ttl_seconds`）、验证结果（后端 N passed、前端契约 M passed、vue-tsc 0 报错）。

- [x] **Step 2: 关联既有计划**

在 `docs/superpowers/plans/2026-09-23-embed-run-recovery.md` 末尾追加一节"Task 7: 实时续显（事件日志 + 增量轮询）"，交叉引用本计划文件路径，并记录两条边界：
- 事件日志是旁路：Redis 不可用时降级为"快照 + 终态同步"（即当前行为）；
- 前端轮询粒度约 1s，非严格流式；若需真流请改走 SSE 断点续订方案。

- [x] **Step 3: 本地验证（提交由用户执行）**

Run: `pytest --confcutdir=tests/frontend tests/frontend -q && python3 -m pytest tests/test_chat_stream_event_journal_contract.py tests/ai/runtime/test_conversation_stream_journal.py -q`
Expected: 全绿。**不要执行 `git commit`。**

---

## 验收（用户手动执行）

1. 控制台重启服务（Agent 不代跑 `./dev.sh`）。
2. EmbedChat 发起一个长任务（如"ping 百度，休息 30 秒，再 ping 163.com"）。
3. 执行中切到"用户管理"页，停留 10 秒以上再切回。
4. 期望：
   - 立刻看到切走前已输出的思考与正文（快照）；
   - 随后**每 1 秒左右**看到新的思考片段、工具时间线步骤、正文持续推进；
   - 任务结束时输入框解锁，内容被落库历史接管，**不出现重复正文**；
   - 全过程无控制台报错；Redis 不可用时应静默降级（界面回到"快照 + 终态"行为）。
