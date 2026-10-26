"""归属人的授权行不可撤销。

归属人的可读可写来自 ``owner_user_id``，与授权表里那条记录无关。
删掉它只会让名单看起来「撤销成功」，实际仍可访问 —— 一次假操作。
"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


def _fake_request():
    return SimpleNamespace(method="DELETE", client=SimpleNamespace(host="127.0.0.1"))


async def _allow(*args, **kwargs):
    return None


async def _noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_cannot_revoke_owner_permission(monkeypatch):
    class FakeMetaService:
        def __init__(self, db):
            pass

        async def get_by_dataset_id(self, dataset_id):
            return SimpleNamespace(owner_user_id=42, status="active")

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetaService)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.delete_dataset_permission(
            dataset_id="ds-1",
            payload=ragflow.DeleteDatasetPermissionRequest(target_type="user", target_id=42),
            request=_fake_request(),
            db=SimpleNamespace(),
            user={"role": "admin", "user_id": "1", "user_name": "admin"},
        )

    assert exc.value.status_code == 400
    assert "归属人" in exc.value.detail


@pytest.mark.asyncio
async def test_can_still_revoke_non_owner_user(monkeypatch):
    """非归属人的授权仍然要能撤销，别把整条路径堵死。"""
    deleted = {}

    class FakeMetaService:
        def __init__(self, db):
            pass

        async def get_by_dataset_id(self, dataset_id):
            return SimpleNamespace(owner_user_id=42, status="active")

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class FakeDb:
        async def execute(self, stmt):
            return FakeResult()

        async def flush(self):
            deleted["flushed"] = True

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetaService)
    monkeypatch.setattr(ragflow, "require_dataset_write_access", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", _noop)

    result = await ragflow.delete_dataset_permission(
        dataset_id="ds-1",
        payload=ragflow.DeleteDatasetPermissionRequest(target_type="user", target_id=7),
        request=_fake_request(),
        db=FakeDb(),
        user={"role": "admin", "user_id": "1", "user_name": "admin"},
    )

    assert result["code"] == 0
