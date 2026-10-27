"""资源广场 review 修复的回归用例（批次 1）。

每个用例对应一条已确认缺陷：summary 口径、访问计数对齐、私有化拦截、
resource_id 归一化、停用数据集、pending 双计、排序、老库归属人。
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.main import app
from app.models.knowledge import KnowledgeAccessRequest, KnowledgeBaseMetadata
from app.models.metadata import MetaDataset
from app.models.permission import ResourcePermission
from app.services.auth_service import AuthService
from app.services.workbench_home_service import _count_accessible_knowledge_bases


@pytest.fixture
async def keys(db_session):
    suffix = uuid.uuid4().hex[:8]
    user = await AuthService.generate_api_key(f"fix_u_{suffix}", role="user", db=db_session)
    admin = await AuthService.generate_api_key(f"fix_a_{suffix}", role="admin", db=db_session)
    return suffix, user, admin


@pytest.fixture
async def public_kb(db_session):
    suffix = uuid.uuid4().hex[:8]
    ds_id = f"fix_pub_{suffix}"
    db_session.add(KnowledgeBaseMetadata(
        ragflow_dataset_id=ds_id, name=f"修复公共库_{suffix}",
        visibility="public", status="active",
    ))
    await db_session.commit()
    yield ds_id, suffix
    await db_session.rollback()
    await db_session.execute(delete(KnowledgeAccessRequest).where(
        KnowledgeAccessRequest.dataset_id == ds_id))
    await db_session.execute(delete(ResourcePermission).where(
        ResourcePermission.resource_id == ds_id))
    await db_session.execute(delete(KnowledgeBaseMetadata).where(
        KnowledgeBaseMetadata.ragflow_dataset_id == ds_id))
    await db_session.commit()


@pytest.fixture
async def active_dataset(db_session):
    row = MetaDataset(name=f"fix_ds_{uuid.uuid4().hex[:8]}", display_name="修复数据集", status=1)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    dataset_id = int(row.id)
    yield row, dataset_id
    await db_session.rollback()
    await db_session.execute(delete(KnowledgeAccessRequest).where(
        KnowledgeAccessRequest.resource_type == "metadata",
        KnowledgeAccessRequest.dataset_id == str(dataset_id)))
    await db_session.execute(delete(ResourcePermission).where(
        ResourcePermission.resource_type == "metadata",
        ResourcePermission.resource_id == str(dataset_id)))
    await db_session.execute(delete(MetaDataset).where(MetaDataset.id == dataset_id))
    await db_session.commit()


# ---------------------------------------------------------------- summary 口径
@pytest.mark.asyncio
async def test_summary_keeps_counts_across_state_filter(db_session, keys, public_kb):
    ds_id, suffix = public_kb
    _, user_key, _ = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        created = await c.post("/api/portal/access-requests", json={
            "resource_type": "dataset", "resource_id": ds_id,
        }, headers={"X-API-Key": user_key})
        assert created.status_code == 200, created.text

        seen = {}
        for state in ("all", "apply", "pending"):
            resp = await c.get("/api/portal/resource-square/knowledge",
                               params={"keyword": suffix, "status": state},
                               headers={"X-API-Key": user_key})
            assert resp.status_code == 200
            seen[state] = resp.json()["data"]["summary"]
        assert seen["all"]["pending"] == 1
        # 切到任意状态，另两个角标都不能被过滤逻辑抹掉
        assert seen["apply"]["pending"] == 1
        assert seen["pending"]["pending"] == 1
        assert seen["apply"]["apply"] == 0


# ------------------------------------------------------------ 工作台/广场计数
@pytest.mark.asyncio
async def test_workbench_knowledge_count_matches_square_has(db_session, keys, public_kb):
    """管理员口径：工作台卡片数字必须等于广场「已授权」的 total。"""
    _, _, admin_key = keys
    card = await _count_accessible_knowledge_bases(db_session, {"user_id": 0, "role": "admin"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/portal/resource-square/knowledge",
                           params={"status": "has"}, headers={"X-API-Key": admin_key})
        assert resp.status_code == 200
        total = resp.json()["data"]["total"]
    assert card == total


@pytest.mark.asyncio
async def test_workbench_count_ignores_internal_library_for_user(db_session, keys):
    """普通用户口径：内置库被授权也不该被工作台计入（广场不展示它）。"""
    suffix, user_key, _ = keys
    internal_id = f"fix_internal_{suffix}"
    normal_id = f"fix_normal_{suffix}"
    db_session.add_all([
        KnowledgeBaseMetadata(ragflow_dataset_id=internal_id,
                              name="chatbi-example-fix-probe", visibility="public", status="active"),
        KnowledgeBaseMetadata(ragflow_dataset_id=normal_id,
                              name=f"修复普通库_{suffix}", visibility="public", status="active"),
    ])
    await db_session.commit()
    try:
        from app.models.user import User
        applicant = (await db_session.execute(
            select(User).where(User.user_name.like(f"fix_u_{suffix}%")))).scalars().first()
        db_session.add_all([
            ResourcePermission(user_id=int(applicant.id), resource_type="dataset",
                               resource_id=internal_id, enabled=True),
            ResourcePermission(user_id=int(applicant.id), resource_type="dataset",
                               resource_id=normal_id, enabled=True),
        ])
        await db_session.commit()
        user = {"user_id": int(applicant.id), "user_name": applicant.user_name, "role": "user"}
        card = await _count_accessible_knowledge_bases(db_session, user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/portal/resource-square/knowledge",
                               params={"status": "has"}, headers={"X-API-Key": user_key})
            total = resp.json()["data"]["total"]
        assert total >= 1          # 普通库确实在
        assert card == total       # 内置库不参与，两边一致
    finally:
        await db_session.rollback()
        for ds in (internal_id, normal_id):
            await db_session.execute(delete(ResourcePermission).where(
                ResourcePermission.resource_id == ds))
            await db_session.execute(delete(KnowledgeBaseMetadata).where(
                KnowledgeBaseMetadata.ragflow_dataset_id == ds))
        await db_session.commit()


# --------------------------------------------------------- 私有化后不可批准
@pytest.mark.asyncio
async def test_approve_rejected_when_visibility_became_private(db_session, keys, public_kb):
    ds_id, _ = public_kb
    _, user_key, admin_key = keys
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        created = await c.post("/api/portal/access-requests", json={
            "resource_type": "dataset", "resource_id": ds_id,
        }, headers={"X-API-Key": user_key})
        rid = created.json()["data"]["id"]

        row = (await db_session.execute(select(KnowledgeBaseMetadata).where(
            KnowledgeBaseMetadata.ragflow_dataset_id == ds_id))).scalars().first()
        row.visibility = "private"
        await db_session.commit()

        resp = await c.post(f"/api/portal/access-requests/{rid}/approve",
                            json={"comment": "ok"}, headers={"X-API-Key": admin_key})
        assert resp.status_code == 400
        assert "私有" in resp.json()["detail"]

        # 申请必须保持 pending，不能让管理员以为批过了
        await db_session.rollback()
        after = (await db_session.execute(select(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.id == rid))).scalars().first()
        assert after.status == "pending"


# ------------------------------------------------------ resource_id 归一化
@pytest.mark.asyncio
async def test_dataset_resource_id_is_normalized(db_session, keys, active_dataset):
    _, user_key, _ = keys
    _, dataset_id = active_dataset
    padded = f"0{dataset_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        created = await c.post("/api/portal/access-requests", json={
            "resource_type": "metadata", "resource_id": padded,
        }, headers={"X-API-Key": user_key})
        assert created.status_code == 200, created.text
        rid = created.json()["data"]["id"]

        await db_session.rollback()
        stored = (await db_session.execute(select(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.id == rid))).scalars().first()
        assert stored.dataset_id == str(dataset_id)

        # 已存在 pending 时，用规范值再提一次必须被识别为重复
        again = await c.post("/api/portal/access-requests", json={
            "resource_type": "metadata", "resource_id": str(dataset_id),
        }, headers={"X-API-Key": user_key})
        assert again.status_code == 400
        assert "等待审批" in again.json()["detail"]


# --------------------------------------------------------- 停用数据集不可申请
@pytest.mark.asyncio
async def test_disabled_dataset_cannot_be_requested(db_session, keys):
    _, user_key, _ = keys
    row = MetaDataset(name=f"fix_off_{uuid.uuid4().hex[:8]}", display_name="停用集", status=0)
    db_session.add(row)
    await db_session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/portal/access-requests", json={
                "resource_type": "metadata", "resource_id": str(row.id),
            }, headers={"X-API-Key": user_key})
        assert resp.status_code == 400
        assert "停用" in resp.json()["detail"]
    finally:
        await db_session.rollback()
        await db_session.execute(delete(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.dataset_id == str(row.id)))
        await db_session.execute(delete(MetaDataset).where(MetaDataset.id == row.id))
        await db_session.commit()


# ------------------------------------------------ 已授权但仍有 pending 不双计
@pytest.mark.asyncio
async def test_pending_ignored_when_access_already_granted(db_session, keys):
    """管理员手工授权后，残留的 pending 不能再算「审批中」。"""
    suffix, user_key, _ = keys
    from app.models.user import User

    applicant = (await db_session.execute(
        select(User).where(User.user_name.like(f"fix_u_{suffix}%")))).scalars().first()
    assert applicant is not None
    applicant_id = int(applicant.id)

    tag = f"fix_grant_{uuid.uuid4().hex[:8]}"
    dataset = MetaDataset(name=tag, display_name="双计检查集", status=1)
    db_session.add(dataset)
    await db_session.commit()
    await db_session.refresh(dataset)
    dataset_id = int(dataset.id)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/api/portal/access-requests", json={
                "resource_type": "metadata", "resource_id": str(dataset_id),
            }, headers={"X-API-Key": user_key})
            # 管理员手工授权（不经过审批），申请仍挂 pending
            db_session.add(ResourcePermission(
                user_id=applicant_id, resource_type="metadata",
                resource_id=str(dataset_id), enabled=True,
            ))
            await db_session.commit()

            resp = await c.get("/api/portal/resource-square/datasets",
                               params={"keyword": tag}, headers={"X-API-Key": user_key})
            data = resp.json()["data"]
            assert data["summary"]["has"] == 1
            assert data["summary"]["pending"] == 0
            assert data["items"][0]["request_status"] == "pending"  # 卡片仍能显示审批中
    finally:
        await db_session.rollback()
        await db_session.execute(delete(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.dataset_id == str(dataset_id)))
        await db_session.execute(delete(ResourcePermission).where(
            ResourcePermission.resource_type == "metadata",
            ResourcePermission.resource_id == str(dataset_id)))
        await db_session.execute(delete(MetaDataset).where(MetaDataset.id == dataset_id))
        await db_session.commit()


# --------------------------------------------------------------- 数据集排序
@pytest.mark.asyncio
async def test_dataset_square_lists_newest_first(db_session, keys):
    _, _, admin_key = keys
    tag = uuid.uuid4().hex[:6]
    old = MetaDataset(name=f"fix_old_{tag}", display_name="旧集", status=1)
    new = MetaDataset(name=f"fix_new_{tag}", display_name="新集", status=1)
    db_session.add_all([old, new])
    await db_session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/portal/resource-square/datasets",
                               params={"keyword": tag, "size": 50},
                               headers={"X-API-Key": admin_key})
        ids = [item["id"] for item in resp.json()["data"]["items"]]
        assert ids[0] == new.id and ids[-1] == old.id
    finally:
        await db_session.rollback()
        await db_session.execute(delete(MetaDataset).where(MetaDataset.id.in_([old.id, new.id])))
        await db_session.commit()


# ------------------------------------------- 老库归属人（owner_user_id 为空）
@pytest.mark.asyncio
async def test_legacy_owner_cannot_request_own_library(db_session, keys):
    suffix, user_key, _ = keys
    from app.models.user import User
    owner = (await db_session.execute(
        select(User).where(User.user_name.like(f"fix_u_{suffix}%")))).scalars().first()
    ds_id = f"fix_legacy_{suffix}"
    db_session.add(KnowledgeBaseMetadata(
        ragflow_dataset_id=ds_id, name=f"修复老库_{suffix}", visibility="public",
        status="active", created_by=owner.user_name, owner_user_id=None,
    ))
    await db_session.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/portal/access-requests", json={
                "resource_type": "dataset", "resource_id": ds_id,
            }, headers={"X-API-Key": user_key})
        assert resp.status_code == 400
        assert "已经有访问权限" in resp.json()["detail"]
    finally:
        await db_session.rollback()
        await db_session.execute(delete(KnowledgeAccessRequest).where(
            KnowledgeAccessRequest.dataset_id == ds_id))
        await db_session.execute(delete(KnowledgeBaseMetadata).where(
            KnowledgeBaseMetadata.ragflow_dataset_id == ds_id))
        await db_session.commit()
