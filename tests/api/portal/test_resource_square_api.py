"""资源广场目录查询：可见范围、启用状态、我的申请状态回显。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.main import app
from app.models.knowledge import KnowledgeBaseMetadata
from app.models.metadata import MetaDataset, MetaTable
from app.services.auth_service import AuthService


@pytest.fixture
async def user_key(db_session):
    return await AuthService.generate_api_key(
        f"rs_sq_{uuid.uuid4().hex[:8]}", role="user", db=db_session
    )


@pytest.fixture
async def seeded_knowledge(db_session):
    suffix = uuid.uuid4().hex[:8]
    rows = [
        KnowledgeBaseMetadata(
            ragflow_dataset_id=f"pub_{suffix}",
            name=f"公共库_{suffix}",
            visibility="public",
            status="active",
        ),
        KnowledgeBaseMetadata(
            ragflow_dataset_id=f"team_{suffix}",
            name=f"部门库_{suffix}",
            visibility="team",
            status="active",
        ),
        KnowledgeBaseMetadata(
            ragflow_dataset_id=f"priv_{suffix}",
            name=f"私有库_{suffix}",
            visibility="private",
            status="active",
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    yield suffix
    await db_session.rollback()
    await db_session.execute(
        delete(KnowledgeBaseMetadata).where(
            KnowledgeBaseMetadata.ragflow_dataset_id.in_(
                [f"pub_{suffix}", f"team_{suffix}", f"priv_{suffix}"]
            )
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_knowledge_square_lists_public_and_team_only(db_session, user_key, seeded_knowledge):
    suffix = seeded_knowledge
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/resource-square/knowledge",
            params={"keyword": suffix, "size": 50},
            headers={"X-API-Key": user_key},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        names = {item["name"] for item in data["items"]}
        assert f"公共库_{suffix}" in names
        assert f"部门库_{suffix}" in names
        # 个人私有库既不出现在广场，也不接受申请
        assert f"私有库_{suffix}" not in names
        assert data["summary"]["apply"] >= 2


@pytest.mark.asyncio
async def test_knowledge_square_visibility_filter(db_session, user_key, seeded_knowledge):
    suffix = seeded_knowledge
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/resource-square/knowledge",
            params={"keyword": suffix, "visibility": "public", "size": 50},
            headers={"X-API-Key": user_key},
        )
        assert resp.status_code == 200
        names = {item["name"] for item in resp.json()["data"]["items"]}
        assert names == {f"公共库_{suffix}"}


@pytest.mark.asyncio
async def test_dataset_square_lists_enabled_only(db_session, user_key):
    suffix = uuid.uuid4().hex[:8]
    on = MetaDataset(name=f"on_{suffix}", display_name="启用集", status=1)
    off = MetaDataset(name=f"off_{suffix}", display_name="停用集", status=0)
    db_session.add_all([on, off])
    await db_session.commit()
    on_id, off_id = on.id, off.id

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/portal/resource-square/datasets",
                params={"keyword": suffix},
                headers={"X-API-Key": user_key},
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["enabled"] is True
            names = {item["name"] for item in data["items"]}
            assert f"on_{suffix}" in names
            assert f"off_{suffix}" not in names
            target = next(item for item in data["items"] if item["name"] == f"on_{suffix}")
            assert target["has_access"] is False
            assert target["request_status"] is None
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(MetaDataset).where(MetaDataset.id.in_([on_id, off_id]))
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_dataset_square_reports_request_status(db_session, user_key):
    suffix = uuid.uuid4().hex[:8]
    dataset = MetaDataset(name=f"req_{suffix}", display_name="申请中集", status=1)
    db_session.add(dataset)
    await db_session.commit()
    dataset_id = dataset.id

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/portal/access-requests",
                json={"resource_type": "metadata", "resource_id": str(dataset_id), "reason": "测试"},
                headers={"X-API-Key": user_key},
            )
            assert created.status_code == 200, created.text

            resp = await client.get(
                "/api/portal/resource-square/datasets",
                params={"keyword": suffix},
                headers={"X-API-Key": user_key},
            )
            assert resp.status_code == 200
            item = next(i for i in resp.json()["data"]["items"] if i["name"] == f"req_{suffix}")
            assert item["request_status"] == "pending"
            assert item["request_id"] is not None
            # 审批中的资源不该再出现在「可申请」计数里
            summary = resp.json()["data"]["summary"]
            assert summary["pending"] >= 1
    finally:
        from app.models.knowledge import KnowledgeAccessRequest

        await db_session.rollback()
        await db_session.execute(
            delete(KnowledgeAccessRequest).where(
                KnowledgeAccessRequest.dataset_id == str(dataset_id)
            )
        )
        await db_session.execute(delete(MetaDataset).where(MetaDataset.id == dataset_id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_dataset_square_counts_tables(db_session, user_key):
    suffix = uuid.uuid4().hex[:8]
    dataset = MetaDataset(name=f"cnt_{suffix}", display_name="计数集", status=1)
    db_session.add(dataset)
    await db_session.commit()
    dataset_id = dataset.id
    db_session.add_all(
        [
            MetaTable(dataset_id=dataset_id, physical_name=f"t1_{suffix}", term="表一"),
            MetaTable(dataset_id=dataset_id, physical_name=f"t2_{suffix}", term="表二"),
        ]
    )
    await db_session.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/portal/resource-square/datasets",
                params={"keyword": suffix},
                headers={"X-API-Key": user_key},
            )
            item = next(i for i in resp.json()["data"]["items"] if i["name"] == f"cnt_{suffix}")
            assert item["table_count"] == 2
    finally:
        await db_session.rollback()
        await db_session.execute(delete(MetaTable).where(MetaTable.dataset_id == dataset_id))
        await db_session.execute(delete(MetaDataset).where(MetaDataset.id == dataset_id))
        await db_session.commit()
