"""数据集审计写入：按数据集拆条、失败不抛。"""

import pytest
from sqlalchemy import delete, select

from app.models.metadata import MetadataAuditLog
from app.services.metadata_audit_service import MetadataAuditService


@pytest.mark.asyncio
async def test_log_writes_one_row_per_dataset(db_session):
    ok = await MetadataAuditService.log(
        metadata_dataset_ids=[9101, 9102, 9101],
        action="metadata:grant",
        user={"user_id": 1, "user_name": "admin"},
        detail={"applicant_user_id": 7},
        client_ip="10.0.0.1",
    )
    assert ok is True

    try:
        rows = (
            await db_session.execute(
                select(MetadataAuditLog)
                .where(MetadataAuditLog.metadata_dataset_id.in_([9101, 9102]))
                .order_by(MetadataAuditLog.metadata_dataset_id.asc())
            )
        ).scalars().all()
        # 同一批里的重复 ID 只留一条：查一个数据集时不该看到同一动作两遍
        assert [row.metadata_dataset_id for row in rows] == [9101, 9102]
        assert {row.action for row in rows} == {"metadata:grant"}
        assert rows[0].detail == {"applicant_user_id": 7}
        assert rows[0].client_ip == "10.0.0.1"
        assert rows[0].status_code == 200
    finally:
        await db_session.execute(
            delete(MetadataAuditLog).where(MetadataAuditLog.metadata_dataset_id.in_([9101, 9102]))
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_log_ignores_empty_and_non_numeric_ids():
    assert await MetadataAuditService.log(metadata_dataset_ids=[], action="metadata:grant") is False
    assert await MetadataAuditService.log(metadata_dataset_ids=["abc", None], action="metadata:grant") is False


@pytest.mark.asyncio
async def test_log_swallows_session_errors(monkeypatch):
    """审计是旁路：库挂了也只能记日志，不能把审批打成 5xx。"""

    class BoomSession:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "app.services.metadata_audit_service.AsyncSessionLocal", lambda: BoomSession()
    )
    ok = await MetadataAuditService.log(metadata_dataset_ids=[9103], action="metadata:grant")
    assert ok is False
