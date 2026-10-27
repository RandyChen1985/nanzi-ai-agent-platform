"""统一资源访问申请接口：提交、我的、待审批、审批、撤回。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.main import app
from app.models.knowledge import KnowledgeAccessRequest
from app.models.metadata import MetaDataset
from app.models.permission import ResourcePermission
from app.services.auth_service import AuthService


@pytest.fixture
async def keys(db_session):
    suffix = uuid.uuid4().hex[:8]
    admin = await AuthService.generate_api_key(f"rs_admin_{suffix}", role="admin", db=db_session)
    user = await AuthService.generate_api_key(f"rs_user_{suffix}", role="user", db=db_session)
    return admin, user


@pytest.fixture
async def dataset(db_session):
    row = MetaDataset(
        name=f"rs_api_{uuid.uuid4().hex[:8]}",
        display_name="接口测试集",
        status=1,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


async def _cleanup(db_session, dataset_id: int | None = None) -> None:
    # 端点用的是另一个 session；MySQL 默认 REPEATABLE READ，本 session 若还开着
    # 旧事务就会看不到对方新提交的行，先结束当前事务再删。
    await db_session.rollback()
    if dataset_id is not None:
        await db_session.execute(
            delete(KnowledgeAccessRequest).where(
                KnowledgeAccessRequest.dataset_id == str(dataset_id)
            )
        )
        await db_session.execute(
            delete(ResourcePermission).where(
                ResourcePermission.resource_type == "metadata",
                ResourcePermission.resource_id == str(dataset_id),
            )
        )
        await db_session.execute(delete(MetaDataset).where(MetaDataset.id == dataset_id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_create_approve_flow_end_to_end(db_session, keys, dataset):
    admin_key, user_key = keys
    dataset_id = dataset.id
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/portal/access-requests",
                json={"resource_type": "metadata", "resource_id": str(dataset_id), "reason": "做分析"},
                headers={"X-API-Key": user_key},
            )
            assert created.status_code == 200, created.text
            request_id = created.json()["data"]["id"]

            mine = await client.get(
                "/api/portal/access-requests/mine", headers={"X-API-Key": user_key}
            )
            assert mine.status_code == 200
            mine_items = mine.json()["data"]["items"]
            target = next(item for item in mine_items if item["id"] == request_id)
            assert target["resource_type"] == "metadata"
            assert target["resource_name"] == "接口测试集"

            pending = await client.get(
                "/api/portal/access-requests/pending", headers={"X-API-Key": admin_key}
            )
            assert pending.status_code == 200
            assert any(item["id"] == request_id for item in pending.json()["data"]["items"])

            approved = await client.post(
                f"/api/portal/access-requests/{request_id}/approve",
                json={"comment": "同意"},
                headers={"X-API-Key": admin_key},
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["data"]["status"] == "approved"

            # 授权已落库：审批通过等价于手工授权
            await db_session.rollback()
            perm = (
                await db_session.execute(
                    select(ResourcePermission).where(
                        ResourcePermission.resource_type == "metadata",
                        ResourcePermission.resource_id == str(dataset_id),
                    )
                )
            ).scalars().first()
            assert perm is not None and perm.enabled is True

            reviews = await client.get(
                "/api/portal/access-requests/reviews", headers={"X-API-Key": admin_key}
            )
            assert reviews.status_code == 200
            assert any(item["id"] == request_id for item in reviews.json()["data"]["items"])
    finally:
        await _cleanup(db_session, dataset_id)


@pytest.mark.asyncio
async def test_non_admin_cannot_read_pending_or_reviews(db_session, keys):
    _admin_key, user_key = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pending = await client.get(
            "/api/portal/access-requests/pending", headers={"X-API-Key": user_key}
        )
        assert pending.status_code == 403
        reviews = await client.get(
            "/api/portal/access-requests/reviews", headers={"X-API-Key": user_key}
        )
        assert reviews.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_request_returns_400(db_session, keys, dataset):
    _admin_key, user_key = keys
    dataset_id = dataset.id
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body = {"resource_type": "metadata", "resource_id": str(dataset_id)}
            first = await client.post(
                "/api/portal/access-requests", json=body, headers={"X-API-Key": user_key}
            )
            assert first.status_code == 200, first.text
            second = await client.post(
                "/api/portal/access-requests", json=body, headers={"X-API-Key": user_key}
            )
            assert second.status_code == 400
            assert "等待审批" in second.json()["detail"]
    finally:
        await _cleanup(db_session, dataset_id)


@pytest.mark.asyncio
async def test_applicant_can_cancel_own_request(db_session, keys, dataset):
    _admin_key, user_key = keys
    dataset_id = dataset.id
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/portal/access-requests",
                json={"resource_type": "metadata", "resource_id": str(dataset_id)},
                headers={"X-API-Key": user_key},
            )
            request_id = created.json()["data"]["id"]

            cancelled = await client.post(
                f"/api/portal/access-requests/{request_id}/cancel",
                headers={"X-API-Key": user_key},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["data"]["status"] == "cancelled"
    finally:
        await _cleanup(db_session, dataset_id)
