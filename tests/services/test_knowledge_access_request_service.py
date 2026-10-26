"""访问申请的核心规则：去重、不能自批、通过即授权。"""

from types import SimpleNamespace

import pytest

from app.services.knowledge_access_request_service import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    AccessRequestError,
    KnowledgeAccessRequestService,
)

pytestmark = pytest.mark.no_infrastructure


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _DB:
    def __init__(self, results=None, by_id=None):
        self.results = list(results or [])
        self.by_id = by_id or {}
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        return _Result(self.results.pop(0) if self.results else [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return None

    async def get(self, model, pk):
        return self.by_id.get(pk)


def _request(**kwargs):
    base = {
        "id": 1,
        "dataset_id": "ds-1",
        "dataset_name": "制度库",
        "applicant_user_id": 9,
        "applicant_user_name": "bob",
        "status": STATUS_PENDING,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_duplicate_pending_request_is_rejected():
    db = _DB(results=[[_request()]])

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).create(
            dataset_id="ds-1",
            dataset_name="制度库",
            user={"user_id": 9, "user_name": "bob"},
        )

    assert "等待审批" in str(exc.value)


@pytest.mark.asyncio
async def test_create_stores_snapshot_and_pending_status():
    db = _DB(results=[[]])

    row = await KnowledgeAccessRequestService(db).create(
        dataset_id="ds-1",
        dataset_name="制度库",
        user={"user_id": 9, "user_name": "bob"},
        reason="  季度对账要用  ",
    )

    assert row.status == STATUS_PENDING
    assert row.applicant_user_id == 9
    assert row.dataset_name == "制度库"
    assert row.reason == "季度对账要用"
    assert db.added and db.commits == 1


@pytest.mark.asyncio
async def test_approve_grants_resource_permission():
    row = _request()
    db = _DB(results=[[]], by_id={1: row})

    result = await KnowledgeAccessRequestService(db).decide(
        request_id=1,
        reviewer={"user_id": 1, "user_name": "alice"},
        approve=True,
        comment="同意",
    )

    assert result.status == STATUS_APPROVED
    assert result.reviewer_user_name == "alice"
    granted = [item for item in db.added if getattr(item, "resource_type", None) == "dataset"]
    assert len(granted) == 1
    assert granted[0].user_id == 9
    assert granted[0].resource_id == "ds-1"


@pytest.mark.asyncio
async def test_approve_reenables_existing_permission_instead_of_duplicating():
    row = _request()
    existing = SimpleNamespace(user_id=9, resource_type="dataset", resource_id="ds-1", enabled=False)
    db = _DB(results=[[existing]], by_id={1: row})

    await KnowledgeAccessRequestService(db).decide(
        request_id=1,
        reviewer={"user_id": 1, "user_name": "alice"},
        approve=True,
    )

    assert existing.enabled is True
    assert not [item for item in db.added if getattr(item, "resource_type", None) == "dataset"]


@pytest.mark.asyncio
async def test_reject_does_not_grant_anything():
    row = _request()
    db = _DB(results=[[]], by_id={1: row})

    result = await KnowledgeAccessRequestService(db).decide(
        request_id=1,
        reviewer={"user_id": 1, "user_name": "alice"},
        approve=False,
        comment="申请理由不足",
    )

    assert result.status == STATUS_REJECTED
    assert result.review_comment == "申请理由不足"
    assert db.added == []


@pytest.mark.asyncio
async def test_cannot_review_own_request():
    row = _request(applicant_user_id=5)
    db = _DB(results=[[]], by_id={1: row})

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).decide(
            request_id=1,
            reviewer={"user_id": 5},
            approve=True,
        )

    assert "不能审批自己" in str(exc.value)


@pytest.mark.asyncio
async def test_already_decided_request_is_rejected():
    row = _request(status=STATUS_APPROVED)
    db = _DB(by_id={1: row})

    with pytest.raises(AccessRequestError) as exc:
        await KnowledgeAccessRequestService(db).decide(
            request_id=1,
            reviewer={"user_id": 1},
            approve=False,
        )

    assert "已经处理过" in str(exc.value)


@pytest.mark.asyncio
async def test_cancel_only_by_applicant_and_only_when_pending():
    row = _request()
    db = _DB(by_id={1: row})
    service = KnowledgeAccessRequestService(db)

    with pytest.raises(AccessRequestError):
        await service.cancel(1, {"user_id": 999})

    cancelled = await service.cancel(1, {"user_id": 9})
    assert cancelled.status == STATUS_CANCELLED

    with pytest.raises(AccessRequestError):
        await service.cancel(1, {"user_id": 9})
