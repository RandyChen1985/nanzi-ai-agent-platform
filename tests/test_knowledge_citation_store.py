"""知识库引用详情旁路存储的单测。

刻意不连真 Redis：本文件要断言的正是「key 名、TTL、去重与截断上限」这类细节，
用最小替身更直白（沿用 tests/test_password_reset.py 的做法）。
"""
from __future__ import annotations

import json

import pytest

from app.services.ai.knowledge_citation_store import (
    KnowledgeCitationStore,
    accumulate_citation_details,
)

pytestmark = pytest.mark.no_infrastructure


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
