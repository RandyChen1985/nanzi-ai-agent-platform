from types import SimpleNamespace

import pytest

from app.services.metadata_rag_service import MetadataRagService

pytestmark = pytest.mark.no_infrastructure


def test_table_schema_includes_query_optimization_metadata():
    dataset = SimpleNamespace(
        name="orders_ds",
        display_name="订单数据集",
        data_source="clickhouse",
    )
    table = SimpleNamespace(
        id=1,
        physical_name="orders",
        term="订单表",
        description="订单明细",
        synonyms=[],
        partition_fields=["event_date"],
        index_fields=["tenant_id", "user_id"],
        columns=[],
    )

    schema = MetadataRagService.render_table_schema_yaml(dataset, table)

    # 字段语义随 Schema 走：模型要能看到这张表有哪些分区/索引字段
    assert "partition_fields:" in schema
    assert "- event_date" in schema
    assert "index_fields:" in schema
    assert "- tenant_id" in schema

    # 行为规则不再写进 chunk。这段文本会进入向量索引正文，把「怎么用」放这里会导致
    # 每次调整查询策略都要全量重建索引，且与代码侧 GLOBAL_GUARDRAILS 各自表述时
    # 容易出现口径冲突。行为规则统一由 tests/test_chatbi_guardrails_contract.py 锁定。
    assert "禁止无界全表扫描" not in schema
    assert "先收窄" not in schema