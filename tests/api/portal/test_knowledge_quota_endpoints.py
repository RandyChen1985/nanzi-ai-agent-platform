"""配额在端点上的落点：建库、上传都要拦得住，且能读出用量。"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


async def _allow(*args, **kwargs):
    return None


def _request(method="POST"):
    return SimpleNamespace(method=method, client=SimpleNamespace(host="127.0.0.1"))


def _patch_base(monkeypatch):
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_access", _allow)

    async def fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)


@pytest.mark.asyncio
async def test_create_dataset_is_blocked_when_quota_exhausted(monkeypatch):
    _patch_base(monkeypatch)

    async def boom(db, user_id):
        raise ragflow.QuotaExceeded("你已拥有 50 个知识库，达到上限 50 个")

    monkeypatch.setattr(ragflow.KnowledgeQuotaService, "ensure_can_create_dataset", boom)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.create_ragflow_dataset(
            payload=ragflow.CreateDatasetRequest(name="新库"),
            request=_request(),
            user={"user_id": 7, "user_name": "alice", "role": "user"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 400
    assert "上限 50 个" in exc.value.detail


@pytest.mark.asyncio
async def test_upload_is_blocked_when_document_limit_reached(monkeypatch):
    _patch_base(monkeypatch)

    async def boom(client, dataset_id, incoming=1):
        raise ragflow.QuotaExceeded("该知识库已有 2000 个文档，上限 2000 个")

    monkeypatch.setattr(ragflow.KnowledgeQuotaService, "ensure_can_upload", boom)

    file = SimpleNamespace(filename="制度.pdf", size=1024, content_type="application/pdf")

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.upload_dataset_document(
            dataset_id="ds-1",
            request=_request(),
            file=file,
            user={"user_id": 7, "role": "user"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 400
    assert "上限 2000 个" in exc.value.detail


@pytest.mark.asyncio
async def test_quota_endpoint_reports_usage(monkeypatch):
    _patch_base(monkeypatch)

    async def fake_snapshot(client, dataset_id, db=None, user_id=None):
        return {
            "documents": {"used": 312, "limit": 2000},
            "max_datasets_per_user": 50,
            "owned_datasets": {"used": 3, "limit": 50},
        }

    monkeypatch.setattr(ragflow.KnowledgeQuotaService, "snapshot", fake_snapshot)

    res = await ragflow.get_dataset_quota(
        dataset_id="ds-1",
        user={"user_id": 7, "role": "user"},
        db=SimpleNamespace(),
    )

    assert res["code"] == 0
    assert res["data"]["documents"] == {"used": 312, "limit": 2000}
    assert res["data"]["owned_datasets"]["used"] == 3
