# 知识库引用详情持久化（Redis 旁路）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识库引用详情在页面刷新后仍可恢复——历史消息的 `[ID:n]` 徽章能继续点开弹层、引用列表能继续展示；恢复失败时给出分状态的准确提示。

**Architecture:** 引用详情不进 DB、不进会话消息体，改用独立 Redis key `conversation:citation_detail:{trace_id}` 按 trace_id 旁路保存，TTL 跟随 `memory_service.ttl`（30 天）。在 `execution_step` 一处累积 `type == "citation"` 事件到 `shared_state`，在 `finalize_step` 与消息落库同一判定下写入 Redis；两个历史接口按 trace_id 批量 `MGET` 合并回响应，前端映射到 `msg.citations` 并标记历史消息，使徽章点击在解析失败时按「已过期 / 加载中」分别提示。

**Tech Stack:** Python 3.11 / FastAPI / pytest（`asyncio_mode=auto`、自制 `FakeRedis` 替身）/ Vue 3 + TypeScript / Redis

**Spec:** [`docs/superpowers/specs/2026-10-09-knowledge-citation-detail-persistence-design.md`](../specs/2026-10-09-knowledge-citation-detail-persistence-design.md)

---

## 执行约定（必读）

1. **不要执行 `git commit`。** 本项目 `AGENTS.md` 明确禁止 Agent 自行提交。每个任务末尾的「汇报」步骤只输出「改了哪些文件、验证命令与结果」，提交时机由用户决定。
2. **不要执行 `./dev.sh` 或任何部署脚本。** 服务启停由用户在控制台操作。
3. 本项目**无 SQL 迁移**：不要新增 `db-prod/` 或 `db-prod-pg/` 脚本。
4. Python 目标版本 3.11，**禁止使用 3.12+ 语法**。
5. 所有新增注释、文档、测试说明**使用中文**。

## File Structure

**新增**

| 文件 | 职责 |
|---|---|
| `app/services/ai/knowledge_citation_store.py` | 引用详情旁路存储：key 构造、`sanitize` 纯函数、Redis 读写、`accumulate_citation_details` 累积纯函数。单一职责，不依赖 pipeline 与会话服务（`memory_service` 仅在取 TTL 时延迟 import） |
| `tests/test_knowledge_citation_store.py` | store 单测：`sanitize`、`accumulate_citation_details`、Redis 读写与失败兜底（用自制 `FakeRedis`） |
| `tests/test_knowledge_citation_history_contract.py` | 后端契约：接入点存在且位置正确（仿 `tests/test_chat_stream_event_journal_contract.py` 的源码断言风格） |
| `tests/frontend/test_knowledge_citation_history_contract.py` | 前端契约：历史映射、`isHistory` 标记、降级提示分支、`Fig.` 不误报 |

**修改**

| 文件 | 改动 |
|---|---|
| `app/services/ai/pipeline/steps/execution_step.py` | 事件循环内累积 citation 详情 |
| `app/services/ai/pipeline/steps/finalize_step.py` | 与消息落库同判定写入 Redis |
| `app/schemas/agent.py` | `AgentExecutionHistoryResponse` 新增 `citations` |
| `app/api/v1/endpoints/chat.py` | 两个历史接口合并 + 三处删除清理 |
| `frontend/src/views/EmbedChat.vue` | 历史映射、`isHistory`、降级提示 |
| `frontend/src/views/AgentDebug.vue` | 历史映射、降级提示 |
| `tests/CHECKLIST.md` | 项目硬性要求：登记本次改动 |

**分解说明**：存储与累积逻辑集中在 `knowledge_citation_store.py`，使 Redis 交互、上限防御、去重三件事各有独立入口且可脱离 pipeline 单测；pipeline 与 endpoint 只保留一行式接入，避免把业务逻辑散落到三个层里。

---

## Task 1: 存储层骨架 —— sanitize 纯函数

**Files:**
- Create: `app/services/ai/knowledge_citation_store.py`
- Test: `tests/test_knowledge_citation_store.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_knowledge_citation_store.py`：

```python
"""知识库引用详情旁路存储的单测。

刻意不连真 Redis：本文件要断言的正是「key 名、TTL、去重与截断上限」这类细节，
用最小替身更直白（沿用 tests/test_password_reset.py 的做法）。
"""
from __future__ import annotations

import pytest

from app.services.ai.knowledge_citation_store import KnowledgeCitationStore

pytestmark = pytest.mark.no_infrastructure


def _chunk(chunk_id: str, content: str = "正文", doc_name: str = "手册.pdf") -> dict:
    return {
        "id": chunk_id,
        "chunk_id": chunk_id,
        "doc_id": f"doc-{chunk_id}",
        "dataset_id": "ds-1",
        "doc_name": doc_name,
        "content": content,
        "similarity": 0.91,
        "page_no": 3,
        "source_type": "knowledge",
        # 不在白名单里的字段应被丢弃，避免 value 被 RAGFlow 原始大对象撑大
        "huge_internal_blob": "x" * 100,
    }


def test_sanitize_dedupes_by_chunk_id_and_drops_unknown_fields():
    store = KnowledgeCitationStore()

    result = store.sanitize([_chunk("c1"), _chunk("c1"), _chunk("c2")])

    assert len(result) == 2
    assert [item["chunk_id"] for item in result] == ["c1", "c2"]
    assert "huge_internal_blob" not in result[0]


def test_sanitize_tolerates_non_list_and_non_dict_payloads():
    store = KnowledgeCitationStore()

    assert store.sanitize(None) == []
    assert store.sanitize("not-a-list") == []
    assert store.sanitize([None, "x", 3]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_citation_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ai.knowledge_citation_store'`

- [ ] **Step 3: Write minimal implementation**

创建 `app/services/ai/knowledge_citation_store.py`：

```python
"""知识库引用详情的 Redis 旁路存储。

背景：引用详情（citations）此前只存在于实时 SSE 流与前端内存中，刷新页面后历史
消息里的 ``[ID:n]`` 徽章点不开、引用列表整块消失。落库会让
``ai_agent_execution_history`` 随切片全文明显膨胀，因此改为独立 Redis key 按
trace_id 旁路保存，TTL 跟随会话历史。

命名空间刻意选用 ``conversation:`` 前缀：Redis 浏览器分组规则表（受
``tests/frontend/test_redis_browser_grouping_contract.py`` 约束，45 条前缀必须按
长度严格降序）已把该前缀映射到「会话记忆与上下文」，新增 key 无需改动规则表。

旁路定位意味着：任何读写失败都不得影响对话主流程与历史接口可用性，失败只告警。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# 只保留弹层展示与「查看原档」反查必需的字段，其余（RAGFlow 原始大对象等）一律丢弃。
_ALLOWED_FIELDS = (
    "id",
    "chunk_id",
    "doc_id",
    "dataset_id",
    "doc_name",
    "document_name",
    "content",
    "similarity",
    "page_no",
    "source_type",
    "link",
    "positions",
)


class KnowledgeCitationStore:
    """按 trace_id 保存单轮引用详情的旁路缓存。"""

    KEY_PREFIX = "conversation"
    KEY_SUFFIX = "citation_detail"

    @classmethod
    def _key(cls, trace_id: str) -> str:
        return f"{cls.KEY_PREFIX}:{cls.KEY_SUFFIX}:{trace_id}"

    @classmethod
    def _dedupe_key(cls, item: Dict[str, Any]) -> str:
        chunk_id = str(item.get("chunk_id") or "").strip()
        if chunk_id:
            return f"chunk:{chunk_id}"
        head = str(item.get("content") or "")[:200]
        return f"body:{item.get('doc_name') or ''}|{head}"

    def sanitize(self, citations: Any) -> List[Dict[str, Any]]:
        """去重并收敛字段。纯函数，便于单测。"""
        if not isinstance(citations, list):
            return []

        deduped: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw in citations:
            if not isinstance(raw, dict):
                continue
            key = self._dedupe_key(raw)
            if key in seen:
                continue
            seen.add(key)
            deduped.append({field: raw[field] for field in _ALLOWED_FIELDS if field in raw})
        return deduped


knowledge_citation_store = KnowledgeCitationStore()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_citation_store.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 汇报**

输出：新增文件、验证命令与结果。**不执行 git commit。**

---

## Task 2: sanitize 的上限防御

**Files:**
- Modify: `app/services/ai/knowledge_citation_store.py`
- Test: `tests/test_knowledge_citation_store.py`

- [ ] **Step 1: Write the failing test**

先在 `tests/test_knowledge_citation_store.py` 的 import 区补 `import json`（下一个测试要用 `json.dumps` 量序列化体积），再追加：

```python
def test_sanitize_truncates_long_content_and_caps_item_count():
    store = KnowledgeCitationStore()

    long_items = [_chunk("c1", content="字" * (KnowledgeCitationStore.MAX_CONTENT_CHARS + 500))]
    assert len(long_items[0]["content"]) > KnowledgeCitationStore.MAX_CONTENT_CHARS
    assert len(store.sanitize(long_items)[0]["content"]) == KnowledgeCitationStore.MAX_CONTENT_CHARS

    many = [_chunk(f"c{i}") for i in range(KnowledgeCitationStore.MAX_ITEMS + 30)]
    assert len(store.sanitize(many)) == KnowledgeCitationStore.MAX_ITEMS


def test_sanitize_shrinks_payload_below_size_cap():
    store = KnowledgeCitationStore()
    # 每条 8000 字符、共 100 条 ≈ 800KB 字符，按 UTF-8 计远超 1MiB
    heavy = [
        _chunk(f"c{i}", content="字" * KnowledgeCitationStore.MAX_CONTENT_CHARS)
        for i in range(KnowledgeCitationStore.MAX_ITEMS)
    ]

    result = store.sanitize(heavy)

    size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    assert size <= KnowledgeCitationStore.MAX_PAYLOAD_BYTES
    assert len(result) < KnowledgeCitationStore.MAX_ITEMS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_citation_store.py -v`
Expected: FAIL — `AttributeError: type object 'KnowledgeCitationStore' has no attribute 'MAX_CONTENT_CHARS'`（Task 1 只实现了去重与字段收敛，上限逻辑正是本任务要驱动出来的）

- [ ] **Step 3: Write minimal implementation**

先在 `app/services/ai/knowledge_citation_store.py` 的 import 区补 `import json`（`_serialize` 要用），再在 `KnowledgeCitationStore` 上新增三个上限常量：

```python
    KEY_PREFIX = "conversation"
    KEY_SUFFIX = "citation_detail"
    MAX_ITEMS = 100
    MAX_CONTENT_CHARS = 8000
    MAX_PAYLOAD_BYTES = 1024 * 1024
```

把 `sanitize` 替换为带截断与限条的版本，并新增 `_serialize` / `_fit_payload`：

```python
    def sanitize(self, citations: Any) -> List[Dict[str, Any]]:
        """去重、限条数、截断正文，保证写入 value 有界。纯函数，便于单测。"""
        if not isinstance(citations, list):
            return []

        deduped: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw in citations:
            if not isinstance(raw, dict):
                continue
            key = self._dedupe_key(raw)
            if key in seen:
                continue
            seen.add(key)
            item = {field: raw[field] for field in _ALLOWED_FIELDS if field in raw}
            content = item.get("content")
            if isinstance(content, str) and len(content) > self.MAX_CONTENT_CHARS:
                item["content"] = content[: self.MAX_CONTENT_CHARS]
            deduped.append(item)
            if len(deduped) >= self.MAX_ITEMS:
                break
        return self._fit_payload(deduped)

    @staticmethod
    def _serialize(items: List[Dict[str, Any]]) -> str | None:
        try:
            return json.dumps(items, ensure_ascii=False)
        except (TypeError, ValueError):
            return None

    def _fit_payload(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按序列化后大小从尾部削减；不可序列化时整体放弃（旁路数据，宁缺勿错）。"""
        while items:
            payload = self._serialize(items)
            if payload is not None and len(payload.encode("utf-8")) <= self.MAX_PAYLOAD_BYTES:
                return items
            items = items[:-1]
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_citation_store.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果。**不执行 git commit。**

---

## Task 3: Redis 读写（save / load_many / delete_many）

**Files:**
- Modify: `app/services/ai/knowledge_citation_store.py`
- Test: `tests/test_knowledge_citation_store.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_knowledge_citation_store.py` 顶部 import 区补充，并追加测试：

```python
class FakeRedis:
    """最小 Redis 替身：只需 set / mget / delete，用于断言 key 名与 TTL。"""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.mget_calls: list[list[str]] = []

    async def set(self, key, value, ex=None):
        self.data[key] = value
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    async def mget(self, keys):
        self.mget_calls.append(list(keys))
        return [self.data.get(key) for key in keys]

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            if self.data.pop(key, None) is not None:
                removed += 1
        return removed


@pytest.fixture
def fake_redis(monkeypatch):
    """替换 store 模块内的 get_redis。"""
    from app.services.ai import knowledge_citation_store as store_module

    redis = FakeRedis()

    async def _get():
        return redis

    monkeypatch.setattr(store_module, "get_redis", _get)
    return redis


async def test_save_writes_key_with_conversation_namespace_and_history_ttl(fake_redis):
    from app.services.ai.memory_service import memory_service

    ok = await KnowledgeCitationStore().save("trace-abc", [_chunk("c1")])

    assert ok is True
    assert "conversation:citation_detail:trace-abc" in fake_redis.data
    assert fake_redis.ttls["conversation:citation_detail:trace-abc"] == memory_service.ttl
    assert json.loads(fake_redis.data["conversation:citation_detail:trace-abc"])[0]["chunk_id"] == "c1"


async def test_save_skips_empty_payload_without_touching_redis(fake_redis):
    store = KnowledgeCitationStore()

    assert await store.save("trace-abc", []) is False
    assert await store.save("", [_chunk("c1")]) is False
    assert fake_redis.data == {}


async def test_load_many_uses_single_mget_and_skips_broken_values(fake_redis):
    store = KnowledgeCitationStore()
    await store.save("t1", [_chunk("c1")])
    await store.save("t2", [_chunk("c2")])
    fake_redis.data["conversation:citation_detail:t3"] = "{not-json"

    result = await store.load_many(["t1", "t2", "t3", "missing"])

    assert len(fake_redis.mget_calls) == 1, "必须一次 MGET，避免历史分页逐条往返"
    assert [item["chunk_id"] for item in result["t1"]] == ["c1"]
    assert [item["chunk_id"] for item in result["t2"]] == ["c2"]
    assert "t3" not in result, "反序列化失败的 key 只跳过，不影响其它 trace"
    assert "missing" not in result


async def test_delete_many_removes_keys(fake_redis):
    store = KnowledgeCitationStore()
    await store.save("t1", [_chunk("c1")])

    assert await store.delete_many(["t1", "t2"]) == 1
    assert fake_redis.data == {}


async def test_redis_unavailable_is_swallowed(monkeypatch):
    """Redis 不可用属于旁路故障，绝不能向上抛。"""
    from app.services.ai import knowledge_citation_store as store_module

    async def _none():
        return None

    monkeypatch.setattr(store_module, "get_redis", _none)
    store = KnowledgeCitationStore()

    assert await store.save("t1", [_chunk("c1")]) is False
    assert await store.load_many(["t1"]) == {}
    assert await store.delete_many(["t1"]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_citation_store.py -v`
Expected: FAIL — `AttributeError: 'KnowledgeCitationStore' object has no attribute 'save'`

- [ ] **Step 3: Write minimal implementation**

先把 `app/services/ai/knowledge_citation_store.py` 的 import 区替换为下面这段（Task 2 已加 `json`；读写还需要 `logging`、`Iterable` 与 `get_redis`）：

```python
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List

from app.core.redis import get_redis

logger = logging.getLogger(__name__)
```

然后在 `KnowledgeCitationStore` 类中追加（`_fit_payload` 之后）：

```python
    async def save(self, trace_id: str, citations: Any) -> bool:
        """保存单轮引用详情；任何失败只告警并返回 False。"""
        tid = str(trace_id or "").strip()
        if not tid:
            return False
        items = self.sanitize(citations)
        if not items:
            return False
        payload = self._serialize(items)
        if payload is None:
            return False
        try:
            redis = await get_redis()
            if not redis:
                return False
            # TTL 跟随会话历史，避免引用详情比会话本身活得更久或更短
            from app.services.ai.memory_service import memory_service

            await redis.set(self._key(tid), payload, ex=memory_service.ttl)
            return True
        except Exception as exc:  # noqa: BLE001 - 旁路数据失败不得影响主流程
            logger.warning("[KnowledgeCitationStore] save failed trace=%s: %s", tid, exc)
            return False

    async def load_many(self, trace_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
        """按 trace_id 批量取回；单条损坏只跳过该条。"""
        unique = list(dict.fromkeys(str(t).strip() for t in trace_ids if str(t or "").strip()))
        if not unique:
            return {}
        try:
            redis = await get_redis()
            if not redis:
                return {}
            raw_values = await redis.mget([self._key(t) for t in unique])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[KnowledgeCitationStore] load failed: %s", exc)
            return {}

        result: Dict[str, List[Dict[str, Any]]] = {}
        for tid, raw in zip(unique, raw_values or []):
            if not raw:
                continue
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("[KnowledgeCitationStore] decode failed trace=%s", tid)
                continue
            if isinstance(parsed, list):
                result[tid] = parsed
        return result

    async def delete_many(self, trace_ids: Iterable[str]) -> int:
        """删除引用详情（会话/历史被删除时调用）；失败只告警。"""
        keys = [self._key(t) for t in (str(x).strip() for x in trace_ids) if t]
        if not keys:
            return 0
        try:
            redis = await get_redis()
            if not redis:
                return 0
            return int(await redis.delete(*keys) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[KnowledgeCitationStore] delete failed: %s", exc)
            return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_citation_store.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果。**不执行 git commit。**

---

## Task 4: 累积纯函数 accumulate_citation_details

**Files:**
- Modify: `app/services/ai/knowledge_citation_store.py`
- Test: `tests/test_knowledge_citation_store.py`

- [ ] **Step 1: Write the failing test**

先把 `tests/test_knowledge_citation_store.py` 的 import 改为：

```python
from app.services.ai.knowledge_citation_store import (
    KnowledgeCitationStore,
    accumulate_citation_details,
)
```

再追加：

```python
def test_accumulate_ignores_non_citation_events():
    state: dict = {}

    assert accumulate_citation_details(state, {"type": "log", "id": "x"}) is False
    assert accumulate_citation_details(state, "not-a-dict") is False
    assert state == {}


def test_accumulate_appends_and_dedupes_across_executors():
    """同一 chunk 被多个 executor 重复投递时只能保留一条。"""
    state: dict = {}

    assert accumulate_citation_details(state, {"type": "citation", "data": [_chunk("c1"), _chunk("c2")]}) is True
    assert accumulate_citation_details(state, {"type": "citation", "data": [_chunk("c2"), _chunk("c3")]}) is True

    assert [item["chunk_id"] for item in state["knowledge_citations"]] == ["c1", "c2", "c3"]


def test_accumulate_consumes_citation_event_without_usable_payload():
    """载荷异常也要消费掉事件（与前端 mergeStreamCitations 语义一致），但不写垃圾数据。"""
    state: dict = {}

    assert accumulate_citation_details(state, {"type": "citation", "data": "bad"}) is True
    assert state.get("knowledge_citations") in (None, [])


def test_accumulate_repairs_corrupted_bucket():
    state = {"knowledge_citations": "corrupted"}

    assert accumulate_citation_details(state, {"type": "citation", "data": [_chunk("c1")]}) is True
    assert [item["chunk_id"] for item in state["knowledge_citations"]] == ["c1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_citation_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'accumulate_citation_details'`

- [ ] **Step 3: Write minimal implementation**

追加到 `app/services/ai/knowledge_citation_store.py` 文件末尾（类定义之外）：

```python
def accumulate_citation_details(state: Dict[str, Any], chunk: Dict[str, Any]) -> bool:
    """把 ``type == "citation"`` 事件里的切片累积进 shared_state，按 chunk_id 去重。

    返回该事件是否被消费。抽成纯函数有两个目的：让 execution_step 只留一行接入，
    以及让去重与容错可以脱离 pipeline 单测。
    """
    if not isinstance(chunk, dict) or chunk.get("type") != "citation":
        return False

    data = chunk.get("data")
    if not isinstance(data, list):
        # 载荷不可用也算消费：事件不该再落到下游正文分支
        return True

    bucket = state.get("knowledge_citations")
    if not isinstance(bucket, list):
        bucket = []
        state["knowledge_citations"] = bucket

    known = {
        KnowledgeCitationStore._dedupe_key(item)
        for item in bucket
        if isinstance(item, dict)
    }
    for item in data:
        if not isinstance(item, dict):
            continue
        key = KnowledgeCitationStore._dedupe_key(item)
        if key in known:
            continue
        known.add(key)
        bucket.append(item)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_citation_store.py -v`
Expected: PASS（13 passed）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果。**不执行 git commit。**

---

## Task 5: 接入 execution_step（累积）

**Files:**
- Modify: `app/services/ai/pipeline/steps/execution_step.py`（import 区 `:17` 之后、事件循环 `:189` 附近）
- Test: `tests/test_knowledge_citation_history_contract.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_knowledge_citation_history_contract.py`：

```python
"""契约：引用详情必须被累积、落 Redis，并能在两个历史接口回填。

背景：引用详情此前只活在实时 SSE 流里，刷新页面后历史消息的 [ID:n] 徽章点不开。
这些断言锁住「累积 → 写入 → 读取」三处接入点不被后续重构删掉。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
EXECUTION_STEP = ROOT / "app/services/ai/pipeline/steps/execution_step.py"
FINALIZE_STEP = ROOT / "app/services/ai/pipeline/steps/finalize_step.py"
CHAT_ENDPOINT = ROOT / "app/api/v1/endpoints/chat.py"
AGENT_SCHEMA = ROOT / "app/schemas/agent.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_execution_step_accumulates_citation_events():
    source = _read(EXECUTION_STEP)

    assert "accumulate_citation_details(shared_state, chunk)" in source, "execution_step 必须累积引用详情"
    # 累积调用必须在遍历 executor 事件的循环内——只 import 了函数不算接入。
    # 注意不能用 source.index("accumulate_citation_details")：import 行出现在文件顶部，
    # 会先于循环命中，让断言变成假绿。
    loop_index = source.index("async for chunk in executor_stream:")
    call_index = source.index('if chunk.get("type") == "citation":')
    assert call_index > loop_index
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: FAIL — `AssertionError: execution_step 必须累积引用详情`

- [ ] **Step 3: Write minimal implementation**

在 `app/services/ai/pipeline/steps/execution_step.py` 的 import 区（`:17` 的 `)` 之后）新增：

```python
from app.services.ai.knowledge_citation_store import accumulate_citation_details
```

在事件循环内、现有 `if chunk.get("type") == "reusable_result_status":`（`:189`）**之前**插入：

```python
                        # 引用详情只存在于流事件里，必须在此累积，否则刷新后无法回放。
                        # 与下方 reusable_result_status 同一处收口，覆盖所有 executor。
                        if chunk.get("type") == "citation":
                            accumulate_citation_details(shared_state, chunk)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果。**不执行 git commit。**

---

## Task 6: 接入 finalize_step（写入 Redis）

**Files:**
- Modify: `app/services/ai/pipeline/steps/finalize_step.py`（`should_persist` 分支 `:151-181`）
- Test: `tests/test_knowledge_citation_history_contract.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_knowledge_citation_history_contract.py`：

```python
def test_finalize_step_persists_citations_only_when_turn_is_persisted():
    """写入必须与消息落库同一判定，保证「有历史消息才有引用详情」的对称性。"""
    source = _read(FINALIZE_STEP)

    persist_branch = source.index("if should_persist:")
    save_index = source.index("knowledge_citation_store.save")
    assert save_index > persist_branch, "引用详情必须在 should_persist 分支内写入"
    assert "shared_state.get(\"knowledge_citations\")" in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: FAIL — `ValueError: substring not found`（尚无 `knowledge_citation_store.save`）

- [ ] **Step 3: Write minimal implementation**

在 `app/services/ai/pipeline/steps/finalize_step.py` 的 `if should_persist:` 块内，`await await_unless_cancelling(...)` 持久化调用（`:156-181`）**之后**插入：

```python
                # 引用详情与消息同判定落 Redis：不落库的消息不产生引用详情，
                # 落库的消息其引用详情必然写入，避免「有正文没引用」的不对称。
                citation_details = shared_state.get("knowledge_citations")
                if citation_details:
                    from app.services.ai.knowledge_citation_store import (
                        knowledge_citation_store,
                    )

                    await knowledge_citation_store.save(trace_id, citation_details)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果。**不执行 git commit。**

---

## Task 7: DB 历史接口回填（/history）

**Files:**
- Modify: `app/schemas/agent.py:177-205`
- Modify: `app/api/v1/endpoints/chat.py:2243`（`items` 组装完成之后、`return` 之前）
- Test: `tests/test_knowledge_citation_history_contract.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_knowledge_citation_history_contract.py`：

```python
def test_history_schema_exposes_citations():
    source = _read(AGENT_SCHEMA)

    assert "class AgentExecutionHistoryResponse(BaseModel):" in source
    assert "citations: Optional[list] = None" in source


def test_db_history_endpoint_backfills_citations_by_trace_id():
    source = _read(CHAT_ENDPOINT)
    fetch = source[source.index("async def get_history("):source.index("async def delete_history(")]

    assert "knowledge_citation_store" in fetch, "DB 历史必须回填引用详情"
    assert "load_many" in fetch
    assert "update={\"citations\"" in fetch, "回填必须并入响应对象"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: 两个新测试 FAIL（schema 无 `citations`、端点无 `load_many`）

- [ ] **Step 3: Write minimal implementation**

**3a.** `app/schemas/agent.py` 的 `AgentExecutionHistoryResponse` 中，在 `agent_avatar_url` 声明（`:203`）之后新增：

```python
    # 知识库引用详情（Redis 旁路存储，见 knowledge_citation_store）；缺失表示已过期
    citations: Optional[list] = None
```

**3b.** `app/api/v1/endpoints/chat.py` 的 `get_history` 中，在非分组分支的 `items.append(item)` 循环结束、`return StandardResponse(...)`（`:2244`）**之前**插入：

```python
    # 引用详情走 Redis 旁路存储（见 knowledge_citation_store）：DB 历史本身不含它，
    # 这里统一回填一次，让刷新后的历史消息仍能展开引用弹层。
    # 放在组装循环之后而非其中，是因为 result.all() 只能消费一次，且分组/非分组
    # 两个分支都要覆盖。
    if items:
        citations_by_trace = await knowledge_citation_store.load_many(
            [str(item.trace_id) for item in items if item.trace_id]
        )
        if citations_by_trace:
            items = [
                item.model_copy(update={"citations": citations_by_trace[str(item.trace_id)]})
                if str(item.trace_id or "") in citations_by_trace
                else item
                for item in items
            ]
```

同时在该文件顶部（或函数内延迟 import，与该文件既有风格一致）确保可用：

```python
    from app.services.ai.knowledge_citation_store import knowledge_citation_store
```

> 放置位置：与 `get_history` 内的其它延迟 import 并列（该函数已有 `from app.services.ai.agent_manager import ...` 等写法），保持文件既有风格。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果。**不执行 git commit。**

---

## Task 8: 会话历史接口回填（/conversation/{id}/history）

**Files:**
- Modify: `app/api/v1/endpoints/chat.py:1167`（三条来源汇合之后、`return` 之前）
- Test: `tests/test_knowledge_citation_history_contract.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_knowledge_citation_history_contract.py`：

```python
def test_conversation_history_endpoint_backfills_citations_for_all_sources():
    """该接口有三条来源（Redis / 审计合并 / DB 兜底），回填必须在汇合点统一做一次。"""
    source = _read(CHAT_ENDPOINT)
    fetch = source[
        source.index("async def get_conversation_history("):source.index("async def get_conversation_context_usage(")
    ]

    assert "load_many" in fetch
    assert 'message["citations"] = found' in fetch
    # 回填必须在 return 之前
    assert fetch.index('message["citations"] = found') < fetch.index("return StandardResponse(data=ConversationHistoryResponse(")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: FAIL — `AssertionError`

- [ ] **Step 3: Write minimal implementation**

在 `app/api/v1/endpoints/chat.py` 的 `get_conversation_history` 中，把 `history = fallback_history`（`:1166`）与 `return StandardResponse(data=ConversationHistoryResponse(`（`:1168`）之间的空档替换为：

```python
    # 引用详情走 Redis 旁路存储，覆盖上面三条来源（Redis 正常 / 审计合并 / DB 兜底），
    # 在此汇合点统一回填一次，避免每个分支各补一遍。
    detail_trace_ids = [
        str(message.get("trace_id"))
        for message in history
        if message.get("role") == "assistant" and message.get("trace_id")
    ]
    if detail_trace_ids:
        from app.services.ai.knowledge_citation_store import knowledge_citation_store

        details_by_trace = await knowledge_citation_store.load_many(detail_trace_ids)
        for message in history:
            if message.get("role") != "assistant":
                continue
            found = details_by_trace.get(str(message.get("trace_id") or ""))
            if found:
                message["citations"] = found

```

> `ConversationMessage` 已声明 `model_config = {"extra": "allow"}`（`app/api/v1/endpoints/chat.py:875`），因此新增的 `citations` 键无需改该模型即可原样下发。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果。**不执行 git commit。**

---

## Task 9: 删除历史时清理引用详情

**Files:**
- Modify: `app/api/v1/endpoints/chat.py`（`delete_history:2290`、`batch_delete_history:2356`、`truncate_history_endpoint:2419`）
- Test: `tests/test_knowledge_citation_history_contract.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_knowledge_citation_history_contract.py`：

```python
def test_delete_endpoints_clean_up_citation_details():
    """删除会话/历史后引用详情不得残留，否则与「删除即删除」语义不符。"""
    source = _read(CHAT_ENDPOINT)

    single = source[source.index("async def delete_history("):source.index("class BatchDeleteHistoryRequest")]
    batch = source[source.index("async def batch_delete_history("):source.index("class TruncateHistoryRequest")]
    truncate = source[source.index("async def truncate_history_endpoint("):source.index('@router.get("/logs/{trace_id}"')]

    assert "delete_many" in single, "单条删除要清理引用详情"
    assert "delete_many" in batch, "批量删除要清理引用详情"
    assert "delete_many" in truncate, "截断历史要清理引用详情"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: FAIL — 三处断言均缺失

- [ ] **Step 3: Write minimal implementation**

**3a.** `delete_history`：在 `await db.commit()`（`:2288`）之后、`return`（`:2290`）之前插入：

```python
    # Redis 旁路存储的引用详情不随 DB 级联删除，需显式清理（失败只告警）
    from app.services.ai.knowledge_citation_store import knowledge_citation_store

    await knowledge_citation_store.delete_many([trace_id])
```

**3b.** `batch_delete_history`：在 `await db.commit()`（`:2340`）之后、Redis 会话清理循环（`:2342`）之前插入：

```python
    if trace_ids:
        # trace_ids 已在上方按权限范围筛出（见 stmt 的 user_id 过滤）
        from app.services.ai.knowledge_citation_store import knowledge_citation_store

        await knowledge_citation_store.delete_many(trace_ids)
```

**3c.** `truncate_history_endpoint`：在 `await db.commit()`（`:2417`）之后、`return`（`:2419`）之前插入（该块内已有 `trace_ids`）：

```python
        if trace_ids:
            from app.services.ai.knowledge_citation_store import knowledge_citation_store

            await knowledge_citation_store.delete_many(trace_ids)
```

> 注意 `3c` 的缩进：它位于 `if len(rows) > keep_turns:` 块内，需与块内语句对齐。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_knowledge_citation_history_contract.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果。**不执行 git commit。**

---

## Task 10: 前端历史映射与历史标记

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue:7600`、`:9884`、`:9903`、`:2639`（`Message` 接口）
- Modify: `frontend/src/views/AgentDebug.vue`（历史构建处，`isHistory: true` 附近）
- Test: `tests/frontend/test_knowledge_citation_history_contract.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/frontend/test_knowledge_citation_history_contract.py`：

```python
"""契约：刷新后的历史消息必须恢复引用详情并标记为历史，点不开时按状态提示。

背景：引用详情此前只在实时流里存在，刷新页面后历史消息的 [ID:n] 徽章点了没反应、
下方引用列表整块消失。这些断言锁住前端三件事：历史映射、历史标记、降级提示。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"
RENDERER = ROOT / "frontend/src/components/MessageRenderer.vue"

pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_embed_history_maps_citations_and_marks_history():
    source = _read(EMBED)

    assert "citations: item.citations ?? undefined" in source, "主历史必须映射引用详情"
    assert re.search(r"isHistory:\s*true", source), "历史消息必须带 isHistory 标记"
    assert "isHistory?: boolean" in source, "Message 接口需声明 isHistory"


def test_embed_visibility_sync_restores_citations():
    source = _read(EMBED)

    assert "currentMsg.citations = latestServerItem.citations ?? currentMsg.citations" in source
    assert "citations: latestServerItem.citations ?? undefined" in source


def test_agent_debug_history_maps_citations():
    source = _read(DEBUG)

    assert "citations: m.citations ?? undefined" in source


def test_both_chat_views_prompt_when_citation_detail_unavailable():
    """两处 handleShowCitation 都必须按状态提示，而不是静默返回。"""
    for path in (EMBED, DEBUG):
        source = _read(path)

        assert "引用详情已过期" in source, f"{path.name} 缺少过期提示"
        assert "引用详情加载中" in source, f"{path.name} 缺少加载中提示"
        assert "isKnowledgeCitationBadge" in source, f"{path.name} 缺少引用标记形态校验"


def test_figure_reference_is_not_treated_as_citation_badge():
    """Fig. n 只是渲染器的兼容匹配面，不是知识库引用，不能据此弹「已过期」。"""
    source = _read(EMBED)

    match = re.search(r"const isKnowledgeCitationBadge = \(text: string\) =>\s*(.+);", source)
    assert match, "必须存在引用标记形态校验函数"
    pattern = match.group(1)
    assert "ID" in pattern
    assert "Fig" not in pattern, "形态校验不得把 Fig. 也算作引用标记"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_citation_history_contract.py -v`
Expected: FAIL — 5 个测试均因缺少映射/标记/提示而失败

- [ ] **Step 3: Write minimal implementation**

**3a.** `frontend/src/views/EmbedChat.vue` 的 `Message` 接口（`citations?: any[];` 所在处 `:2639` 附近）补充：

```ts
  isHistory?: boolean;
```

**3b.** 主历史构建（`:7600` 起的 `newHistoryBatch.push({...})`）中，在 `processTimeline: hydratedTimeline,` 之后补充：

```ts
                  citations: item.citations ?? undefined,
                  isHistory: true,
```

**3c.** 可见性同步的 update 分支（`:9884`）中，在 `currentMsg.processTimeline = ...` 之后补充：

```ts
            currentMsg.citations = latestServerItem.citations ?? currentMsg.citations;
```

**3d.** 可见性同步的 append 分支（`:9903`）中，在 `processTimeline: hydrateHistoryProcessTimeline(...)` 之后补充：

```ts
            citations: latestServerItem.citations ?? undefined,
```

**3e.** `frontend/src/views/AgentDebug.vue` 历史构建的 `return { ... }` 中，在 `processTimeline: hydratedTimeline,` 之后补充：

```ts
            citations: m.citations ?? undefined,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_citation_history_contract.py -v`
Expected: 5 项中 3 passed、2 failed（失败的两项 `test_both_chat_views_prompt_when_citation_detail_unavailable` 与 `test_figure_reference_is_not_treated_as_citation_badge` 都属于 Task 11 范围，它们要等 `isKnowledgeCitationBadge` 与分状态提示落地）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果（说明剩余失败项由 Task 11 处理）。**不执行 git commit。**

---

## Task 11: 前端降级提示（区分加载中 / 已过期）

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue:8050`（`handleShowCitation`）
- Modify: `frontend/src/views/AgentDebug.vue:3004`（`handleShowCitation`）
- Test: `tests/frontend/test_knowledge_citation_history_contract.py`

- [ ] **Step 1: Write the failing test**

`test_both_chat_views_prompt_when_citation_detail_unavailable` 与 `test_figure_reference_is_not_treated_as_citation_badge`（Task 10 已写入，此刻正是那 2 项失败）即为本任务的失败测试。补充一条断言历史分支语义的用例：

```python
def test_prompt_distinguishes_history_from_in_flight():
    """历史消息提示已过期，流式进行中提示加载中，两者不可混为一谈。"""
    source = _read(EMBED)

    assert 'msg.isHistory ? "引用详情已过期' in source
    assert '"引用详情加载中' in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_citation_history_contract.py -v`
Expected: FAIL — `AssertionError: EmbedChat.vue 缺少过期提示`

- [ ] **Step 3: Write minimal implementation**

**3a.** `frontend/src/views/EmbedChat.vue`：在 `handleShowCitation`（`:8050`）**之前**新增形态校验函数，并把函数体替换为：

```ts
/** Fig. n 只是 Markdown 渲染器的兼容匹配面，并非知识库引用，不能据此提示「已过期」 */
const isKnowledgeCitationBadge = (text: string) => /^\s*[\[【]\s*ID\s*:/i.test(text);

const handleShowCitation = async (msg: Message, citeId: string, anchor?: HTMLElement) => {
  const anchorEl = anchor || (document.querySelector(`[data-cite-id="${citeId}"]`) as HTMLElement | null);

  const target = resolveCitation(msg, citeId);
  if (!target) {
    // 引用详情走 Redis 旁路存储且有 TTL：历史消息解析不到即已过期，
    // 流式进行中则可能只是事件还没到，两种状态必须区分。
    if (!isKnowledgeCitationBadge(anchorEl?.textContent || "")) return;
    showToast(
      msg.isHistory ? "引用详情已过期，正文中的引用标记已无法展开" : "引用详情加载中，请稍候",
      msg.isHistory ? "warning" : "info",
    );
    return;
  }

  msg.isCitationsExpanded = true;
  await nextTick();
  if (anchorEl) {
    anchorEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    openCitationPopover(target, anchorEl);
  }
};
```

**3b.** `frontend/src/views/AgentDebug.vue`：同样在 `handleShowCitation`（`:3004`）之前新增 `isKnowledgeCitationBadge`，并把解析失败分支改为同一策略：

```ts
/** Fig. n 只是 Markdown 渲染器的兼容匹配面，并非知识库引用，不能据此提示「已过期」 */
const isKnowledgeCitationBadge = (text: string) => /^\s*[\[【]\s*ID\s*:/i.test(text);

const handleShowCitation = async (msg: Message, citeId: string, anchor?: HTMLElement) => {
  const anchorEl = anchor || (document.querySelector(`[data-cite-id="${citeId}"]`) as HTMLElement | null);

  const target = resolveCitation(msg, citeId);
  if (!target) {
    // 引用详情走 Redis 旁路存储且有 TTL：历史消息解析不到即已过期，
    // 流式进行中则可能只是事件还没到，两种状态必须区分。
    if (!isKnowledgeCitationBadge(anchorEl?.textContent || "")) return;
    showToast(
      msg.isHistory ? "引用详情已过期，正文中的引用标记已无法展开" : "引用详情加载中，请稍候",
      msg.isHistory ? "warning" : "info",
    );
    return;
  }

  msg.isCitationsExpanded = true;
  await nextTick();
  if (anchorEl) {
    anchorEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    openCitationPopover(target, anchorEl);
  }
};
```

> `AgentDebug.vue` 的 `Message` 接口已声明 `isHistory?: boolean`（`:1447`），无需新增。
> 两个文件各自保留原 `handleShowCitation` 中除「解析失败静默返回」之外的既有行为（展开引用列表、滚动定位、打开弹层），不要删除。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_citation_history_contract.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 汇报**

输出：改动文件、验证结果。**不执行 git commit。**

---

## Task 12: 收尾 —— CHECKLIST、类型检查与全量回归

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 更新 tests/CHECKLIST.md**

在该文件表格的**首行数据位置**（表头下方）插入下面这一行。它是完整内容，直接粘贴即可，无需再补写：

```markdown
| 知识库引用详情刷新后丢失：Redis 旁路存储 + 分状态降级提示 (Knowledge Citation Detail Persistence) | `app/services/ai/knowledge_citation_store.py`（新增）、`app/services/ai/pipeline/steps/execution_step.py`、`app/services/ai/pipeline/steps/finalize_step.py`、`app/schemas/agent.py`、`app/api/v1/endpoints/chat.py`、`frontend/src/views/EmbedChat.vue`、`frontend/src/views/AgentDebug.vue`、`tests/test_knowledge_citation_store.py`（新增）、`tests/test_knowledge_citation_history_contract.py`（新增）、`tests/frontend/test_knowledge_citation_history_contract.py`（新增）、`docs/superpowers/specs/2026-10-09-knowledge-citation-detail-persistence-design.md`（新增）、`tests/CHECKLIST.md` | **用户反馈**：刷新页面后历史消息里的 `[ID:n]` 引用徽章点不开，下方引用列表整块消失。**根因**：引用详情从未进入任何持久化路径——`memory_service.add_message` 的参数与消息体都没有 `citations`，`ai_agent_execution_history` 没有该列，`process_timeline` 快照也没有 citation 分支，前端历史构建同样不映射；正文 `content` 里含 `[ID:n]` 所以徽章照常渲染，但 `msg.citations` 为空使 `resolveCitation` 首行直接返回 null（表现为点了没反应），`v-if="msg.citations?.length"` 为假使引用列表消失。**修法**：不落库（切片全文会让历史表明显膨胀）、也不塞会话消息体（会被 `get_history` 逐条读出并参与每轮 LLM 上下文重建），改用独立 Redis key `conversation:citation_detail:{trace_id}` 按 trace_id 旁路保存，TTL 跟随会话历史（30 天）；`execution_step` 事件循环一处累积 `type == "citation"` 到 `shared_state`（与既有 `reusable_result_status` 同构），`finalize_step` 与消息落库共用同一 `should_persist` 判定写入，保证「有历史消息才有引用详情」的对称性；两个历史接口（`/history` 的 DB 路径、`/conversation/{id}/history` 三条来源的汇合点）按 trace_id 批量 `MGET` 回填；删除单条/批量/截断历史时同步清理 key。key 复用 `conversation:` 命名空间，因此 Redis 浏览器分组规则表（45 条前缀严格降序契约）**零改动**。前端历史映射 `citations` 并打 `isHistory` 标记；解析不到时按状态提示——历史消息「引用详情已过期」、流式进行中「引用详情加载中」，并用 `isKnowledgeCitationBadge` 形态校验避免把渲染器兼容匹配的 `Fig. n` 误报为过期引用。**验证**：后端 store 单测 **13 项**（去重、字段白名单、单条正文截断、条数上限、总量削减、键名与 TTL 断言、单次 MGET、损坏值只跳过、Redis 不可用兜底）、后端契约 **6 项**（累积在事件循环内、写入在 `should_persist` 分支内、schema 暴露字段、两个接口回填、三处删除清理）、前端契约 **6 项**（历史映射与 `isHistory`、可见性同步恢复、调试台映射、两处分状态提示、`Fig.` 不误报）；`vue-tsc` 维持既有基线（当前仓库基线 62）且改动文件零错误；前端契约全量（含 Redis 分组契约）通过。**无 SQL 迁移**。**未跑 `./dev.sh`、未执行 git commit**。 | ✅ 引用详情改为 Redis 旁路持久化并按 trace_id 回填，刷新后历史消息的徽章与引用列表恢复；详情缺失时按「已过期 / 加载中」分状态提示，`Fig. n` 不误报；key 复用 `conversation:` 命名空间故 Redis 分组契约不变。**待用户重启服务后浏览器实测** | 2026-10-09 |
```

- [ ] **Step 2: 前端类型检查**

Run: `cd frontend && NODE_OPTIONS="--max-old-space-size=4096" ./node_modules/.bin/vue-tsc -b --pretty false`

> 执行时踩到两点：① 本项目 tsconfig 用 project references，必须 `-b`，`--noEmit` 与之互斥；② 裸跑 `vue-tsc` 会因默认堆上限直接崩溃（输出只剩 V8 堆栈），`build` 脚本本身就带 `--max-old-space-size=4096`，这里同样要带。

Expected: 错误总数 = 62（既有基线），且改动行不出现在报错列表中——两个改动文件的既有报错行（EmbedChat 947 / 4621 / 4700 / 4925-4948、AgentDebug 1563 / 1637 / 4831）都落在本次改动区之外。

- [ ] **Step 3: 后端相关测试全量回归**

Run: `pytest tests/test_knowledge_citation_store.py tests/test_knowledge_citation_history_contract.py -v`
Expected: 全部 PASS

- [ ] **Step 4: 前端契约测试全量回归（含 Redis 分组契约）**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_knowledge_citation_history_contract.py tests/frontend/test_redis_browser_grouping_contract.py -v`
Expected: 全部 PASS（Redis 分组契约未受影响，因为复用了 `conversation:` 前缀）

- [ ] **Step 5: 变异验证（证明契约不是假绿）**

临时改坏 5 处，逐一确认对应用例失败，随后还原源码并复跑：

| 变异 | 应失败的用例 |
| --- | --- |
| `finalize_step` 的 `if should_persist:` 改为 `if True:` | `test_finalize_step_persists_citations_only_when_turn_is_persisted` |
| 删掉 `execution_step` 里的 `accumulate_citation_details(shared_state, chunk)` | `test_execution_step_accumulates_citation_events` |
| `chat.py` 里 `load_many` 的调用结果改成空字典 | `test_db_history_endpoint_backfills_citations_by_trace_id` |
| 删掉 EmbedChat 历史构建里的 `citations: item.citations ?? undefined,` | `test_embed_history_maps_citations_and_marks_history` |
| 形态校验正则里加入 `Fig` | `test_figure_reference_is_not_treated_as_citation_badge` |

Expected: 5/5 全部被捕获；源码还原后 `pytest tests/test_knowledge_citation_store.py tests/test_knowledge_citation_history_contract.py -q` 回到 19 passed。

- [ ] **Step 6: 汇报与提醒**

输出汇总：改动文件清单、验证命令与结果、Spec 与本计划的偏差（若有）。提醒用户**自行执行 `./dev.sh`** 重启服务后在页面验证：

1. 发起一次知识库问答 → 点击正文 `[ID:n]` 徽章，弹层正常；
2. **刷新页面** → 历史消息徽章仍可点开、下方引用列表仍在；
3. 人为清掉该 trace 的 Redis key（`DEL conversation:citation_detail:<trace_id>`）后刷新 → 点击徽章提示「引用详情已过期」；
4. 点击正文里的 `Fig. 1` 之类文本 → **不应**出现「已过期」提示。

**不执行 git commit。**

---

## 附：与 Spec 的唯一偏差说明

Spec 第 5.4 节描述 `/history` 的合并「仿 `reusable_metadata_by_trace`：采集 → `model_copy`」。本计划 Task 7 改为**在 items 组装完成后统一 `load_many` + `model_copy`**，原因是：

- `result.all()` 对同一个 SQLAlchemy `Result` 只能消费一次，而采集动作必须发生在 `items` 组装前，两者位置冲突；
- 引用详情 key 按 trace_id 全局索引，不依赖用户命名空间，因此不需要像 `reusable_metadata_by_trace` 那样在组装前按 Redis 窗口预取；
- 组装后统一处理天然覆盖分组与非分组两个分支，只多一次列表推导。

合并手段（`model_copy(update=...)`）与 Spec 一致，属等价实现。
