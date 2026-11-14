"""数据集列表关系数批量统计的行为测试。

get_datasets 原先对每个数据集循环 count（N+1），数据集一多列表加载明显变慢。
在临时 MySQL 或 PostgreSQL 库中执行批量查询，覆盖与详情页一致的计数口径。

- 同一数据集内的关系计 1 次
- 跨数据集关系的两侧数据集各计 1 次
- 自关联（源表 = 目标表）只计 1 次
"""

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import insert

from app.models.metadata import MetaDataset, MetaRelationship, MetaTable
from app.services.metadata_service import build_relationship_count_stmt
from tests.temporary_database import init_infrastructure, temporary_database  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "app/services/metadata_service.py"


pytestmark = pytest.mark.no_infrastructure


@pytest_asyncio.fixture
async def metadata_db():
    async with temporary_database(MetaDataset.__table__, MetaTable.__table__, MetaRelationship.__table__) as engine:
        async with engine.begin() as connection:
            yield connection


async def _seed(conn):
    await conn.execute(
        insert(MetaDataset).values(
            [
                {"id": 1, "name": "ds_a"},
                {"id": 2, "name": "ds_b"},
            ]
        )
    )
    await conn.execute(
        insert(MetaTable).values(
            [
                {"id": 1, "dataset_id": 1, "physical_name": "t1", "term": "T1"},
                {"id": 2, "dataset_id": 1, "physical_name": "t2", "term": "T2"},
                {"id": 3, "dataset_id": 2, "physical_name": "t3", "term": "T3"},
            ]
        )
    )
    await conn.execute(
        insert(MetaRelationship).values(
            [
                # 同数据集
                {"id": 1, "source_table_id": 1, "target_table_id": 2, "join_condition": "t1.id = t2.id"},
                # 跨数据集：A 的源表 -> B 的目标表，两侧都应计入
                {"id": 2, "source_table_id": 2, "target_table_id": 3, "join_condition": "t2.id = t3.id"},
                # 自关联：只应计 1 次
                {"id": 3, "source_table_id": 1, "target_table_id": 1, "join_condition": "t1.id = t1.pid"},
            ]
        )
    )


async def test_relationship_counts_cover_same_dataset_cross_dataset_and_self_join(metadata_db):
    await _seed(metadata_db)
    rows = (await metadata_db.execute(build_relationship_count_stmt())).all()

    counts = {dataset_id: count for dataset_id, count in rows}
    assert counts[1] == 3, "同数据集 1 条 + 跨库 1 条 + 自关联 1 条"
    assert counts[2] == 1, "跨库关系的目标表属于 B 数据集"


async def test_datasets_without_relationships_are_absent_from_result(metadata_db):
    await metadata_db.execute(insert(MetaDataset).values({"id": 9, "name": "empty_ds"}))
    await metadata_db.execute(
        insert(MetaTable).values({"id": 90, "dataset_id": 9, "physical_name": "t", "term": "T"})
    )
    rows = (await metadata_db.execute(build_relationship_count_stmt())).all()

    assert rows == []


def test_get_datasets_uses_single_batch_query_instead_of_n_plus_one():
    """防止回退成「每个数据集 await 一次 count」。"""
    source = SERVICE.read_text(encoding="utf-8")
    block = source[source.index("async def get_datasets"):source.index("async def list_accessible_dataset_options")]

    assert "build_relationship_count_stmt()" in block
    assert "relationship_counts.get(ds.id, 0)" in block
    assert "await db.scalar(rel_count_stmt" not in block
    assert "table_ids = [t.id for t in ds.tables]" not in block
