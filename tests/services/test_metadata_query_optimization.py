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

    assert "partition_fields:" in schema
    assert "- event_date" in schema
    assert "index_fields:" in schema
    assert "- tenant_id" in schema
    assert "禁止无界全表扫描" in schema