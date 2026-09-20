"""知识库运营分析：批量落库与环比区间的纯逻辑契约。

注意：本页面的访问控制由接口层 `require_admin` 统一负责（平台级运营视图，
不做按知识库的行级过滤），因此这里不再有授权过滤子句相关的断言。
"""

import asyncio
from datetime import date

import pytest
from sqlalchemy.dialects import mysql, postgresql

from app.services import knowledge_metrics_service as metrics_module
from app.services.knowledge_metrics_service import (
    BATCH_UPSERT_CHUNK_SIZE,
    MAX_RANGE_DAYS,
    KnowledgeMetricsService,
    build_knowledge_metrics_batch_upsert_statement,
    build_knowledge_metrics_upsert_statement,
)

pytestmark = pytest.mark.no_infrastructure


def _sql(statement, dialect):
    return str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def _rows(count: int):
    return [
        {
            "metric_date": date(2026, 7, 27),
            "target_type": "document",
            "target_id": f"doc-{index}",
            "target_name": f"知识库A / 文档-{index}",
            "search_count": 3,
            "citation_count": 2,
        }
        for index in range(count)
    ]


def test_batch_upsert_keeps_accumulate_semantics_for_both_dialects():
    for dialect, dialect_module, accumulate_expression in (
        ("mysql", mysql.dialect(), "knowledge_base_metrics.search_count + VALUES(search_count)"),
        (
            "postgresql",
            postgresql.dialect(),
            "knowledge_base_metrics.search_count + excluded.search_count",
        ),
    ):
        statement = build_knowledge_metrics_batch_upsert_statement(
            rows=_rows(3),
            dialect_name=dialect,
        )
        compiled = _sql(statement, dialect_module)

        # 一行 round trip 写入整块，同时保留逐行累加语义
        assert accumulate_expression in compiled
        assert compiled.count("doc-") >= 3


def test_single_row_upsert_still_compiles_for_both_dialects():
    row = _rows(1)[0]

    mysql_sql = _sql(
        build_knowledge_metrics_upsert_statement(**row, dialect_name="mysql"),
        mysql.dialect(),
    )
    postgresql_sql = _sql(
        build_knowledge_metrics_upsert_statement(**row, dialect_name="postgresql"),
        postgresql.dialect(),
    )

    assert "ON DUPLICATE KEY UPDATE" in mysql_sql
    assert "ON CONFLICT ON CONSTRAINT uix_kb_metric_date_target DO UPDATE" in postgresql_sql


def test_upsert_does_not_touch_table_schema_beyond_existing_columns():
    """回归护栏：本页只改展示与查询，不应再引入新的指标表列（此前一度新增 dataset_id）。"""
    from app.models.knowledge import KnowledgeBaseMetric

    columns = set(KnowledgeBaseMetric.__table__.c.keys())
    assert columns == {
        "id",
        "metric_date",
        "target_type",
        "target_id",
        "target_name",
        "citation_count",
        "search_count",
        "created_at",
        "updated_at",
    }


def test_batch_upsert_rejects_empty_rows():
    with pytest.raises(ValueError):
        build_knowledge_metrics_batch_upsert_statement(rows=[], dialect_name="mysql")


def test_batch_chunk_size_is_bounded():
    assert 0 < BATCH_UPSERT_CHUNK_SIZE <= 1000
    assert MAX_RANGE_DAYS >= 366


def test_previous_period_covers_equal_length_window_without_overlap():
    prev_start, prev_end = KnowledgeMetricsService._previous_period("2026-07-20", "2026-07-26")
    assert (prev_start, prev_end) == ("2026-07-13", "2026-07-19")

    prev_start, prev_end = KnowledgeMetricsService._previous_period("2026-07-01", "2026-07-30")
    assert (prev_start, prev_end) == ("2026-06-01", "2026-06-30")


# ---------------------------------------------------------------------------
# 埋点写入：工具型检索（search_knowledge_base）与执行器型检索共用同一份口径
# ---------------------------------------------------------------------------


class _FakeRedis:
    """只实现埋点用到的命令，用于断言写入的 key / field / 增量。"""

    def __init__(self):
        self.counters: dict = {}
        self.hashes: dict = {}

    async def hincrby(self, key, field, amount=1):
        bucket = self.counters.setdefault(key, {})
        bucket[field] = bucket.get(field, 0) + amount

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value


def _install_fake_redis(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()

    async def _fake_get_redis():
        return fake

    monkeypatch.setattr(metrics_module, "get_redis", _fake_get_redis)
    return fake


_KNOWLEDGE_HIT = {
    "source_type": "knowledge",
    "dataset_id": "ds-1",
    "doc_id": "doc-1",
    "doc_name": "制度.pdf",
}


def test_record_search_hits_counts_dataset_and_document(monkeypatch):
    """检索量在拿到切片时即可确定：按 chunk 计入知识库与文档两个维度。"""
    fake = _install_fake_redis(monkeypatch)
    citations = [
        dict(_KNOWLEDGE_HIT),
        dict(_KNOWLEDGE_HIT),
        {"source_type": "knowledge", "dataset_id": "ds-2", "doc_id": "doc-2", "doc_name": "手册.pdf"},
        {"source_type": "business_data", "dataset_id": "ds-x", "doc_id": "doc-9"},
        {"source_type": "knowledge", "dataset_id": "ds-3", "doc_id": None, "doc_name": "无 doc_id"},
    ]

    asyncio.run(KnowledgeMetricsService.record_search_hits(citations, metric_date="2026-07-27"))

    prefix = "kb:citation:stats:2026-07-27"
    assert fake.counters[f"{prefix}:dataset:search"] == {"ds-1": 2, "ds-2": 1, "ds-3": 1}
    # 非知识库来源不计入；缺 doc_id 的切片只进知识库维度
    assert fake.counters[f"{prefix}:document:search"] == {"doc-1": 2, "doc-2": 1}
    assert fake.hashes["kb:citation:doc_names"]["doc-1"] == "制度.pdf"
    assert fake.hashes["kb:citation:doc_datasets"]["doc-2"] == "ds-2"


def test_record_search_hits_skips_redis_write_without_knowledge_hits(monkeypatch):
    fake = _install_fake_redis(monkeypatch)

    asyncio.run(
        KnowledgeMetricsService.record_search_hits(
            [{"source_type": "business_data", "dataset_id": "ds-x", "doc_id": "doc-9"}],
            metric_date="2026-07-27",
        )
    )

    assert fake.counters == {}
    assert fake.hashes == {}


def test_record_citation_hits_keeps_original_citation_numbering(monkeypatch):
    """`[ID:n]` 的 n 来自**未过滤**的 citations 顺序。

    这里把非知识库项放在首位（占 ID:1），实际引用 ID:3。若实现先过滤掉非知识库项
    再编号，ID:3 会被错配到 doc-1 上——本用例就是这个错位的护栏。
    """
    fake = _install_fake_redis(monkeypatch)
    citations = [
        {"source_type": "business_data", "dataset_id": "biz", "doc_id": "doc-0"},
        {"source_type": "knowledge", "dataset_id": "ds-1", "doc_id": "doc-1", "doc_name": "制度.pdf"},
        {"source_type": "knowledge", "dataset_id": "ds-1", "doc_id": "doc-2", "doc_name": "手册.pdf"},
    ]

    asyncio.run(
        KnowledgeMetricsService.record_citation_hits(citations, {"3"}, metric_date="2026-07-27")
    )

    prefix = "kb:citation:stats:2026-07-27"
    assert fake.counters[f"{prefix}:document:citation"] == {"doc-2": 1}
    assert fake.counters[f"{prefix}:dataset:citation"] == {"ds-1": 1}


def test_record_citation_hits_is_noop_without_cited_ids(monkeypatch):
    fake = _install_fake_redis(monkeypatch)

    asyncio.run(KnowledgeMetricsService.record_citation_hits([dict(_KNOWLEDGE_HIT)], set()))

    assert fake.counters == {}


def test_record_citation_hits_for_refs_uses_explicit_global_numbers(monkeypatch):
    """工具路径的编号由台账全局发放，与切片在列表中的位置无关。"""
    fake = _install_fake_redis(monkeypatch)
    entries = {
        "7": {"source_type": "knowledge", "dataset_id": "ds-1", "doc_id": "doc-1"},
        "8": {"source_type": "knowledge", "dataset_id": "ds-2", "doc_id": "doc-2"},
    }

    asyncio.run(
        KnowledgeMetricsService.record_citation_hits_for_refs(
            entries, {"8"}, metric_date="2026-07-27"
        )
    )

    prefix = "kb:citation:stats:2026-07-27"
    assert fake.counters[f"{prefix}:document:citation"] == {"doc-2": 1}
    assert fake.counters[f"{prefix}:dataset:citation"] == {"ds-2": 1}


def test_record_citation_hits_for_refs_is_noop_without_cited_ids(monkeypatch):
    fake = _install_fake_redis(monkeypatch)
    entries = {"1": {"source_type": "knowledge", "dataset_id": "ds-1", "doc_id": "doc-1"}}

    asyncio.run(KnowledgeMetricsService.record_citation_hits_for_refs(entries, set()))

    assert fake.counters == {}


def test_agent_hook_maps_answer_refs_through_ledger(monkeypatch):
    """端到端接线：回答里的 `[ID:2]` 必须经台账映射回第 2 个切片。

    这是工具路径引用量正确性的关键一环——编号由台账全局发放，回答收尾按编号反查，
    任何一段接线错位都会把引用记到错误的文档上。
    """
    import time

    from app.core.context import AgentContext
    from app.services.ai.knowledge_citation_ledger import new_turn_knowledge_citation_ledger
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner

    fake = _install_fake_redis(monkeypatch)
    ctx = AgentContext(agent_id="a1", agent_name="n1")
    ledger = new_turn_knowledge_citation_ledger(ctx)
    ledger.register({"doc_id": "doc-1", "doc_name": "A.pdf", "dataset_id": "ds-1"})
    ledger.register({"doc_id": "doc-2", "doc_name": "B.pdf", "dataset_id": "ds-2"})

    # self 未被该方法使用，传 None 即可直接验证接线
    asyncio.run(
        AssistantAgentRunner._record_knowledge_citation_metrics(
            None, "结论以制度为准 [ID:2]。", ctx
        )
    )

    prefix = f"kb:citation:stats:{time.strftime('%Y-%m-%d')}"
    assert fake.counters[f"{prefix}:document:citation"] == {"doc-2": 1}
    assert fake.counters[f"{prefix}:dataset:citation"] == {"ds-2": 1}
