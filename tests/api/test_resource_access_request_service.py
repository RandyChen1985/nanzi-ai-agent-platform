"""统一资源访问申请服务：申请、审批、授权、越权与状态保护。"""

import uuid

import pytest
from sqlalchemy import select

from app.models.knowledge import KnowledgeAccessRequest, KnowledgeBaseMetadata
from app.models.metadata import MetaDataset
from app.models.permission import ResourcePermission
from app.services.resource_access_request_service import (
    AccessRequestError,
    ResourceAccessRequestService,
)


@pytest.fixture
async def dataset(db_session):
    row = MetaDataset(
        name=f"rs_test_{uuid.uuid4().hex[:8]}",
        display_name="测试数据集",
        description="用于资源广场测试",
        status=1,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


async def _cleanup(db_session, dataset_id: int, request_ids: list[int]) -> None:
    from sqlalchemy import delete

    if request_ids:
        await db_session.execute(
            delete(KnowledgeAccessRequest).where(KnowledgeAccessRequest.id.in_(request_ids))
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
async def test_create_metadata_request_snapshots_display_name(db_session, dataset):
    service = ResourceAccessRequestService(db_session)
    row = await service.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=None,
        user={"user_id": 9001, "user_name": "applicant_a"},
        reason="做经营分析",
    )
    try:
        assert row.status == "pending"
        assert row.resource_type == "metadata"
        # 前端只传 ID，名称必须由服务端按资源回填，否则审批列表显示裸 ID
        assert row.dataset_name == "测试数据集"
    finally:
        await _cleanup(db_session, dataset.id, [row.id])


@pytest.mark.asyncio
async def test_duplicate_pending_request_is_rejected(db_session, dataset):
    service = ResourceAccessRequestService(db_session)
    applicant = {"user_id": 9002, "user_name": "applicant_b"}
    row = await service.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=None,
        user=applicant,
    )
    try:
        with pytest.raises(AccessRequestError, match="等待审批"):
            await service.create(
                resource_type="metadata",
                resource_id=str(dataset.id),
                resource_name=None,
                user=applicant,
            )
    finally:
        await _cleanup(db_session, dataset.id, [row.id])


@pytest.mark.asyncio
async def test_approve_grants_metadata_permission(db_session, dataset):
    service = ResourceAccessRequestService(db_session)
    row = await service.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=None,
        user={"user_id": 9003, "user_name": "applicant_c"},
    )
    try:
        decided = await service.decide(
            request_id=row.id,
            reviewer={"user_id": 1, "user_name": "admin", "role": "admin"},
            approve=True,
            comment="同意",
        )
        assert decided.status == "approved"

        perm = (
            await db_session.execute(
                select(ResourcePermission).where(
                    ResourcePermission.resource_type == "metadata",
                    ResourcePermission.resource_id == str(dataset.id),
                    ResourcePermission.user_id == 9003,
                )
            )
        ).scalars().first()
        assert perm is not None and perm.enabled is True
    finally:
        await _cleanup(db_session, dataset.id, [row.id])


@pytest.mark.asyncio
async def test_reject_does_not_grant(db_session, dataset):
    service = ResourceAccessRequestService(db_session)
    row = await service.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=None,
        user={"user_id": 9004, "user_name": "applicant_d"},
    )
    try:
        decided = await service.decide(
            request_id=row.id,
            reviewer={"user_id": 1, "user_name": "admin", "role": "admin"},
            approve=False,
            comment="数据敏感",
        )
        assert decided.status == "rejected"
        perm = (
            await db_session.execute(
                select(ResourcePermission).where(
                    ResourcePermission.resource_type == "metadata",
                    ResourcePermission.resource_id == str(dataset.id),
                )
            )
        ).scalars().first()
        assert perm is None
    finally:
        await _cleanup(db_session, dataset.id, [row.id])


@pytest.mark.asyncio
async def test_only_admin_can_decide(db_session, dataset):
    service = ResourceAccessRequestService(db_session)
    row = await service.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=None,
        user={"user_id": 9005, "user_name": "applicant_e"},
    )
    try:
        with pytest.raises(AccessRequestError, match="管理员"):
            await service.decide(
                request_id=row.id,
                reviewer={"user_id": 9006, "user_name": "someone", "role": "user"},
                approve=True,
            )
    finally:
        await _cleanup(db_session, dataset.id, [row.id])


@pytest.mark.asyncio
async def test_cannot_decide_own_request(db_session, dataset):
    service = ResourceAccessRequestService(db_session)
    row = await service.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=None,
        user={"user_id": 9007, "user_name": "applicant_f"},
    )
    try:
        with pytest.raises(AccessRequestError, match="自己"):
            await service.decide(
                request_id=row.id,
                reviewer={"user_id": 9007, "user_name": "applicant_f", "role": "admin"},
                approve=True,
            )
    finally:
        await _cleanup(db_session, dataset.id, [row.id])


@pytest.mark.asyncio
async def test_cancel_only_by_owner_and_only_pending(db_session, dataset):
    service = ResourceAccessRequestService(db_session)
    row = await service.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=None,
        user={"user_id": 9008, "user_name": "applicant_g"},
    )
    try:
        with pytest.raises(AccessRequestError, match="自己"):
            await service.cancel(row.id, {"user_id": 9009, "user_name": "other"})

        cancelled = await service.cancel(row.id, {"user_id": 9008, "user_name": "applicant_g"})
        assert cancelled.status == "cancelled"

        with pytest.raises(AccessRequestError, match="处理过"):
            await service.cancel(row.id, {"user_id": 9008, "user_name": "applicant_g"})
    finally:
        await _cleanup(db_session, dataset.id, [row.id])


@pytest.mark.asyncio
async def test_disabled_dataset_cannot_be_approved(db_session, dataset):
    service = ResourceAccessRequestService(db_session)
    row = await service.create(
        resource_type="metadata",
        resource_id=str(dataset.id),
        resource_name=None,
        user={"user_id": 9010, "user_name": "applicant_h"},
    )
    try:
        dataset.status = 0
        await db_session.commit()

        with pytest.raises(AccessRequestError, match="停用"):
            await service.decide(
                request_id=row.id,
                reviewer={"user_id": 1, "user_name": "admin", "role": "admin"},
                approve=True,
            )
    finally:
        await _cleanup(db_session, dataset.id, [row.id])


@pytest.mark.asyncio
async def test_private_knowledge_base_is_not_requestable(db_session):
    kb_id = f"kb_{uuid.uuid4().hex[:10]}"
    db_session.add(
        KnowledgeBaseMetadata(
            ragflow_dataset_id=kb_id,
            name=f"私有库_{kb_id}",
            visibility="private",
            status="active",
        )
    )
    await db_session.commit()

    service = ResourceAccessRequestService(db_session)
    try:
        with pytest.raises(AccessRequestError, match="私有"):
            await service.create(
                resource_type="dataset",
                resource_id=kb_id,
                resource_name=None,
                user={"user_id": 9011, "user_name": "applicant_i"},
            )
    finally:
        from sqlalchemy import delete

        await db_session.execute(
            delete(KnowledgeBaseMetadata).where(KnowledgeBaseMetadata.ragflow_dataset_id == kb_id)
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_unknown_resource_type_is_rejected(db_session):
    service = ResourceAccessRequestService(db_session)
    with pytest.raises(AccessRequestError, match="不支持"):
        await service.create(
            resource_type="agent",
            resource_id="x",
            resource_name=None,
            user={"user_id": 9012, "user_name": "applicant_j"},
        )
