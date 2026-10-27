"""旧知识库审批端点的兼容与收口。

两件事必须成立：
1. 审批权归管理员——归属人不能再从旧接口批；
2. 旧端点不再自己清缓存/发通知/写审计——否则审批一次会发两条通知。

这里用**知识库**申请来验证：旧端点的兼容层只服务知识库视角，
数据集申请走新的统一接口，不该出现在旧前端列表里。
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.main import app
from app.models.knowledge import KnowledgeAccessRequest, KnowledgeBaseMetadata
from app.models.permission import ResourcePermission
from app.models.portal_notification import PortalNotification
from app.services.auth_service import AuthService


@pytest.fixture
async def keys(db_session):
    suffix = uuid.uuid4().hex[:8]
    admin = await AuthService.generate_api_key(
        f"lg_admin_{suffix}", role="admin", db=db_session
    )
    user = await AuthService.generate_api_key(f"lg_user_{suffix}", role="user", db=db_session)
    return admin, user


@pytest.fixture
async def public_kb(db_session):
    suffix = uuid.uuid4().hex[:8]
    kb_id = f"lg_kb_{suffix}"
    db_session.add(
        KnowledgeBaseMetadata(
            ragflow_dataset_id=kb_id,
            name=f"旧接口公共库_{suffix}",
            visibility="public",
            status="active",
        )
    )
    await db_session.commit()
    yield kb_id
    await db_session.rollback()
    await db_session.execute(
        delete(KnowledgeAccessRequest).where(KnowledgeAccessRequest.dataset_id == kb_id)
    )
    await db_session.execute(
        delete(ResourcePermission).where(
            ResourcePermission.resource_type == "dataset",
            ResourcePermission.resource_id == kb_id,
        )
    )
    await db_session.execute(
        delete(PortalNotification).where(PortalNotification.resource_id == kb_id)
    )
    await db_session.execute(
        delete(KnowledgeBaseMetadata).where(KnowledgeBaseMetadata.ragflow_dataset_id == kb_id)
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_non_admin_gets_403_on_legacy_pending(db_session, keys):
    _admin_key, user_key = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/ragflow/access-requests/pending", headers={"X-API-Key": user_key}
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_list_legacy_pending(db_session, keys):
    admin_key, _user_key = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/ragflow/access-requests/pending", headers={"X-API-Key": admin_key}
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_legacy_item_keeps_old_field_names(db_session, keys, public_kb):
    _admin_key, user_key = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/portal/access-requests",
            json={"resource_type": "dataset", "resource_id": public_kb, "reason": "看制度"},
            headers={"X-API-Key": user_key},
        )
        assert created.status_code == 200, created.text

        resp = await client.get(
            "/api/portal/ragflow/access-requests/mine", headers={"X-API-Key": user_key}
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        target = next(item for item in items if item["dataset_id"] == public_kb)
        # 旧前端读的是这些键，少一个就会渲染成 undefined
        for key in (
            "id",
            "dataset_id",
            "dataset_name",
            "status",
            "reason",
            "review_comment",
            "reviewer_user_name",
            "reviewed_at",
            "created_at",
        ):
            assert key in target, f"兼容结构缺字段 {key}"
        assert target["dataset_name"].startswith("旧接口公共库_")


@pytest.mark.asyncio
async def test_legacy_approve_sends_exactly_one_result_notification(db_session, keys, public_kb):
    admin_key, user_key = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/portal/access-requests",
            json={"resource_type": "dataset", "resource_id": public_kb},
            headers={"X-API-Key": user_key},
        )
        request_id = created.json()["data"]["id"]

        approved = await client.post(
            f"/api/portal/ragflow/access-requests/{request_id}/approve",
            json={"comment": "同意"},
            headers={"X-API-Key": admin_key},
        )
        assert approved.status_code == 200, approved.text

    await db_session.rollback()
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(PortalNotification)
            .where(
                PortalNotification.category == "resource_access_result",
                PortalNotification.resource_id == public_kb,
            )
        )
    ).scalar()
    assert count == 1, "旧端点与统一服务重复发送了结果通知"
