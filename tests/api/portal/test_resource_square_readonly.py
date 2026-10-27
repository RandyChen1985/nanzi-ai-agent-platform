"""只读概览：数据集与知识库的授权校验、以及「不暴露字段明细」的边界。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.main import app
from app.models.knowledge import KnowledgeBaseMetadata
from app.models.metadata import MetaColumn, MetaDataset, MetaMetric, MetaTable
from app.models.permission import ResourcePermission
from app.models.user import User
from app.services.auth_service import AuthService


@pytest.fixture
async def dataset_with_schema(db_session):
    dataset = MetaDataset(
        name=f"ro_{uuid.uuid4().hex[:8]}",
        display_name="只读测试集",
        status=1,
        quality_score=88,
    )
    db_session.add(dataset)
    await db_session.commit()
    dataset_id = dataset.id

    table = MetaTable(
        dataset_id=dataset_id,
        physical_name="dwd_order",
        term="订单",
        description="订单主表",
    )
    db_session.add(table)
    await db_session.commit()
    table_id = table.id
    db_session.add(
        MetaColumn(
            table_id=table_id,
            physical_name="order_id",
            term="订单号",
            examples=["A1"],
        )
    )
    db_session.add(MetaMetric(dataset_id=dataset_id, name="gmv", display_name="GMV", unit="元"))
    await db_session.commit()

    yield dataset_id

    await db_session.rollback()
    await db_session.execute(delete(MetaColumn).where(MetaColumn.table_id == table_id))
    await db_session.execute(delete(MetaTable).where(MetaTable.dataset_id == dataset_id))
    await db_session.execute(delete(MetaMetric).where(MetaMetric.dataset_id == dataset_id))
    await db_session.execute(
        delete(ResourcePermission).where(
            ResourcePermission.resource_type == "metadata",
            ResourcePermission.resource_id == str(dataset_id),
        )
    )
    await db_session.execute(delete(MetaDataset).where(MetaDataset.id == dataset_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_dataset_readonly_requires_permission(db_session, dataset_with_schema):
    dataset_id = dataset_with_schema
    key = await AuthService.generate_api_key(
        f"ro_u_{uuid.uuid4().hex[:8]}", role="user", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/portal/resource-square/datasets/{dataset_id}",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dataset_readonly_granted_user_can_open(db_session, dataset_with_schema):
    dataset_id = dataset_with_schema
    suffix = uuid.uuid4().hex[:8]
    key = await AuthService.generate_api_key(f"ro_g_{suffix}", role="user", db=db_session)
    user_id = (
        await db_session.execute(
            select(User.id).where(User.user_name == f"ro_g_{suffix}")
        )
    ).scalar()

    db_session.add(
        ResourcePermission(
            user_id=user_id,
            resource_type="metadata",
            resource_id=str(dataset_id),
            enabled=True,
        )
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/portal/resource-square/datasets/{dataset_id}",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["display_name"] == "只读测试集"
        assert data["tables"][0]["physical_name"] == "dwd_order"
        assert data["tables"][0]["field_count"] == 1
        # 只读边界：不给字段明细与样例值
        assert "columns" not in data["tables"][0]
        assert "examples" not in str(data)
        assert "row_filter_config" not in data


@pytest.mark.asyncio
async def test_knowledge_readonly_requires_permission(db_session):
    kb_id = f"ro_kb_{uuid.uuid4().hex[:8]}"
    db_session.add(
        KnowledgeBaseMetadata(
            ragflow_dataset_id=kb_id,
            name=f"只读知识库_{kb_id}",
            visibility="public",
            status="active",
        )
    )
    await db_session.commit()

    try:
        key = await AuthService.generate_api_key(
            f"ro_kb_u_{uuid.uuid4().hex[:8]}", role="user", db=db_session
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/api/portal/resource-square/knowledge/{kb_id}",
                headers={"X-API-Key": key},
            )
            assert resp.status_code == 403
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(KnowledgeBaseMetadata).where(
                KnowledgeBaseMetadata.ragflow_dataset_id == kb_id
            )
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_missing_dataset_returns_404(db_session):
    key = await AuthService.generate_api_key(
        f"ro_404_{uuid.uuid4().hex[:8]}", role="admin", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/resource-square/datasets/99999999",
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 404
