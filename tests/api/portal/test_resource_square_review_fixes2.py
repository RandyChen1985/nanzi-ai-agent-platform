"""资源广场 review 修复（批次 2）：健壮性、分页、引擎失联、开关口径。"""

import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.main import app
from app.models.knowledge import KnowledgeAccessRequest, KnowledgeBaseMetadata
from app.models.metadata import MetaDataset
from app.models.permission import ResourcePermission
from app.services.auth_service import AuthService

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def keys(db_session):
    suffix = uuid.uuid4().hex[:8]
    user = await AuthService.generate_api_key(f"fix2_u_{suffix}", role="user", db=db_session)
    admin = await AuthService.generate_api_key(f"fix2_a_{suffix}", role="admin", db=db_session)
    return suffix, user, admin


@pytest.fixture
async def dataset(db_session):
    row = MetaDataset(name=f"fix2_ds_{uuid.uuid4().hex[:8]}", display_name="批2数据集", status=1)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    dataset_id = int(row.id)
    yield dataset_id
    await db_session.rollback()
    await db_session.execute(delete(KnowledgeAccessRequest).where(
        KnowledgeAccessRequest.dataset_id == str(dataset_id)))
    await db_session.execute(delete(ResourcePermission).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.resource_id == str(dataset_id)))
    await db_session.execute(delete(MetaDataset).where(MetaDataset.id == dataset_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_long_reason_returns_400(db_session, keys, dataset):
    _, user_key, _ = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/api/portal/access-requests", json={
            "resource_type": "metadata", "resource_id": str(dataset),
            "reason": "x" * 1001,
        }, headers={"X-API-Key": user_key})
    assert resp.status_code == 400
    assert "1000" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_long_review_comment_returns_400(db_session, keys, dataset):
    _, user_key, admin_key = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        created = await c.post("/api/portal/access-requests", json={
            "resource_type": "metadata", "resource_id": str(dataset),
        }, headers={"X-API-Key": user_key})
        rid = created.json()["data"]["id"]
        resp = await c.post(f"/api/portal/access-requests/{rid}/approve",
                            json={"comment": "y" * 1001}, headers={"X-API-Key": admin_key})
    assert resp.status_code == 400
    assert "1000" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dataset_named_meta_prefix_is_listed(db_session, keys):
    """数据集没有「平台内置」概念，知识库那套命名规则不该套过来。"""
    _, _, admin_key = keys
    tag = uuid.uuid4().hex[:8]
    row = MetaDataset(name=f"meta-fix-{tag}", display_name="带前缀的数据集", status=1)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    dataset_id = int(row.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/portal/resource-square/datasets",
                               params={"keyword": tag, "size": 50},
                               headers={"X-API-Key": admin_key})
        ids = [item["id"] for item in resp.json()["data"]["items"]]
        assert dataset_id in ids
    finally:
        await db_session.rollback()
        await db_session.execute(delete(MetaDataset).where(MetaDataset.id == dataset_id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_workbench_dataset_count_zero_when_square_disabled(db_session, keys, monkeypatch):
    """广场关掉数据集目录后，工作台卡片不能再显示一个点进去是「未开放」的数字。"""
    from app.services import config_service
    from app.services.workbench_home_service import _count_accessible_metadata_datasets

    async def fake_get(cls, key, default=None):
        if key == config_service.RESOURCE_SQUARE_SHOW_DATASETS_KEY:
            return "false"
        return default

    monkeypatch.setattr(config_service.ConfigService, "get", classmethod(fake_get))
    suffix, _, _ = keys
    user = {"user_id": 0, "user_name": f"fix2_u_{suffix}", "role": "admin"}
    assert await _count_accessible_metadata_datasets(db_session, user) == 0


@pytest.mark.asyncio
async def test_pending_list_supports_pagination(db_session, keys, dataset):
    _, user_key, admin_key = keys
    tag = uuid.uuid4().hex[:8]
    other = MetaDataset(name=f"fix2_pg_{tag}", display_name="分页集", status=1)
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    other_id = int(other.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for ds_id in (dataset, other_id):
                await c.post("/api/portal/access-requests", json={
                    "resource_type": "metadata", "resource_id": str(ds_id),
                }, headers={"X-API-Key": user_key})
            first = await c.get("/api/portal/access-requests/pending",
                                params={"page": 1, "size": 1}, headers={"X-API-Key": admin_key})
            second = await c.get("/api/portal/access-requests/pending",
                                 params={"page": 2, "size": 1}, headers={"X-API-Key": admin_key})
        body = first.json()["data"]
        assert len(body["items"]) == 1
        assert body["total"] >= 2
        assert body["page"] == 1 and body["size"] == 1
        other_body = second.json()["data"]
        assert len(other_body["items"]) == 1
        assert other_body["items"][0]["id"] != body["items"][0]["id"]
    finally:
        await db_session.rollback()
        await db_session.execute(delete(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.dataset_id.in_([str(dataset), str(other_id)])))
        await db_session.execute(delete(MetaDataset).where(MetaDataset.id == other_id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_reviews_pagination_reports_real_total(db_session, keys, dataset):
    _, user_key, admin_key = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        created = await c.post("/api/portal/access-requests", json={
            "resource_type": "metadata", "resource_id": str(dataset),
        }, headers={"X-API-Key": user_key})
        rid = created.json()["data"]["id"]
        await c.post(f"/api/portal/access-requests/{rid}/reject",
                     json={"comment": "no"}, headers={"X-API-Key": admin_key})
        resp = await c.get("/api/portal/access-requests/reviews",
                           params={"page": 1, "size": 1}, headers={"X-API-Key": admin_key})
    body = resp.json()["data"]
    assert len(body["items"]) == 1
    assert body["total"] >= 1
    assert body["page"] == 1 and body["size"] == 1


@pytest.mark.asyncio
async def test_pending_flags_engine_missing_kb(db_session, keys, monkeypatch):
    """引擎侧已经没有的库，审批人必须能看出来（否则批了也打不开）。"""
    from app.services.ai import ragflow_client

    suffix, user_key, admin_key = keys
    ds_id = f"fix2_engine_{suffix}"
    db_session.add(KnowledgeBaseMetadata(
        ragflow_dataset_id=ds_id, name=f"批2引擎库_{suffix}",
        visibility="public", status="active",
    ))
    await db_session.commit()

    async def fake_list(self, *args, **kwargs):
        return [{"id": "some-other-dataset", "document_count": 1, "chunk_count": 2}]

    monkeypatch.setattr(ragflow_client.RagFlowClient, "list_all_datasets_cached", fake_list)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/api/portal/access-requests", json={
                "resource_type": "dataset", "resource_id": ds_id,
            }, headers={"X-API-Key": user_key})
            resp = await c.get("/api/portal/access-requests/pending",
                               headers={"X-API-Key": admin_key})
        item = next(i for i in resp.json()["data"]["items"] if i["resource_id"] == ds_id)
        assert item["resource_exists"] is True
        assert item["resource_enabled"] is False
    finally:
        await db_session.rollback()
        await db_session.execute(delete(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.dataset_id == ds_id))
        await db_session.execute(delete(KnowledgeBaseMetadata).where(
            KnowledgeBaseMetadata.ragflow_dataset_id == ds_id))
        await db_session.commit()


def test_migration_adds_pending_uniqueness():
    """同一人同一资源的 pending 必须在数据库层唯一，应用层 check-then-insert 挡不住并发。"""
    mysql = (ROOT / "db-prod" / "V194-unique_pending_access_request.sql").read_text()
    pg = (ROOT / "db-prod-pg" / "V94-unique_pending_access_request.sql").read_text()
    for sql in (mysql, pg):
        assert "pending" in sql.lower()
        assert "UNIQUE" in sql.upper()
    # 存量重复数据必须先收敛，否则加唯一索引会直接失败
    assert "cancelled" in mysql and "cancelled" in pg


def test_decide_locks_the_row_before_mutating():
    src = (ROOT / "app/services/resource_access_request_service.py").read_text()
    assert "with_for_update()" in src
