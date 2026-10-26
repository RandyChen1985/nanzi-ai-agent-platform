"""切片的增删改。

召回不准时的最后手段：直接改某条切片的内容、临时停用、或删掉噪音切片。
权限复用 `element:knowledge:parse_document`（都是"动内容"），不新增权限码。
"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


def _fake_request(method: str = "POST"):
    return SimpleNamespace(method=method, client=SimpleNamespace(host="127.0.0.1"))


class _FakeClient:
    calls: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def create_chunk(self, dataset_id, document_id, content, **kwargs):
        _FakeClient.calls = {"op": "create", "dataset_id": dataset_id, "document_id": document_id, "content": content, **kwargs}
        return {"id": "chunk-new", "content": content}

    async def update_chunk(self, dataset_id, document_id, chunk_id, **kwargs):
        _FakeClient.calls = {"op": "update", "dataset_id": dataset_id, "document_id": document_id, "chunk_id": chunk_id, **kwargs}
        return {"id": chunk_id, **{k: v for k, v in kwargs.items() if v is not None}}

    async def delete_chunk(self, dataset_id, document_id, chunk_id):
        _FakeClient.calls = {"op": "delete", "dataset_id": dataset_id, "document_id": document_id, "chunk_id": chunk_id}


async def _allow(*args, **kwargs):
    return None


def _patch(monkeypatch, audit: dict | None = None):
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)
    monkeypatch.setattr(ragflow, "RagFlowClient", _FakeClient)
    _FakeClient.calls = {}

    async def fake_audit(request, user, action, status_code, payload_, error_message=None, started_at=None):
        if audit is not None:
            audit.update(action=action, status_code=status_code, payload=payload_, error_message=error_message)

    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)


@pytest.mark.asyncio
async def test_create_chunk_records_audit_with_content_preview(monkeypatch):
    audit: dict = {}
    _patch(monkeypatch, audit)

    res = await ragflow.create_document_chunk(
        dataset_id="ds-1",
        document_id="doc-1",
        payload=ragflow.CreateChunkRequest(content="  " + "长" * 200 + "  "),
        request=_fake_request(),
        user={"user_id": 1, "user_name": "alice", "role": "user"},
        db=SimpleNamespace(),
    )

    assert res["code"] == 0
    assert _FakeClient.calls["content"] == "长" * 200
    assert audit["action"] == "chunks:create"
    # 审计里只留前 120 字，避免日志表被整段正文撑爆
    assert len(audit["payload"]["content_preview"]) == 120


@pytest.mark.asyncio
async def test_create_chunk_rejects_blank_content(monkeypatch):
    _patch(monkeypatch)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.create_document_chunk(
            dataset_id="ds-1",
            document_id="doc-1",
            payload=ragflow.CreateChunkRequest(content="   "),
            request=_fake_request(),
            user={"user_id": 1, "role": "user"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 400
    assert "切片内容不能为空" in exc.value.detail


@pytest.mark.asyncio
async def test_update_chunk_content_and_available(monkeypatch):
    audit: dict = {}
    _patch(monkeypatch, audit)

    res = await ragflow.update_document_chunk(
        dataset_id="ds-1",
        document_id="doc-1",
        chunk_id="chunk-1",
        payload=ragflow.UpdateChunkRequest(content="改写后的内容", available=False),
        request=_fake_request("PUT"),
        user={"user_id": 1, "role": "user"},
        db=SimpleNamespace(),
    )

    assert res["code"] == 0
    assert _FakeClient.calls["content"] == "改写后的内容"
    assert _FakeClient.calls["available"] is False
    assert audit["action"] == "chunks:update"
    assert audit["payload"]["chunk_id"] == "chunk-1"


@pytest.mark.asyncio
async def test_update_chunk_requires_at_least_one_field(monkeypatch):
    _patch(monkeypatch)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.update_document_chunk(
            dataset_id="ds-1",
            document_id="doc-1",
            chunk_id="chunk-1",
            payload=ragflow.UpdateChunkRequest(),
            request=_fake_request("PUT"),
            user={"user_id": 1, "role": "user"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 400
    assert "没有要更新的字段" in exc.value.detail


@pytest.mark.asyncio
async def test_delete_chunk_records_audit(monkeypatch):
    audit: dict = {}
    _patch(monkeypatch, audit)

    res = await ragflow.delete_document_chunk(
        dataset_id="ds-1",
        document_id="doc-1",
        chunk_id="chunk-1",
        request=_fake_request("DELETE"),
        user={"user_id": 1, "role": "user"},
        db=SimpleNamespace(),
    )

    assert res["code"] == 0
    assert _FakeClient.calls["op"] == "delete"
    assert audit["action"] == "chunks:delete"
    assert audit["payload"]["chunk_id"] == "chunk-1"


@pytest.mark.asyncio
async def test_chunk_endpoints_reuse_parse_permission(monkeypatch):
    seen: list[str] = []

    async def fake_perm(user, db, code):
        seen.append(code)

    _patch(monkeypatch)
    monkeypatch.setattr(ragflow, "require_element_permission", fake_perm)

    await ragflow.create_document_chunk(
        dataset_id="ds-1",
        document_id="doc-1",
        payload=ragflow.CreateChunkRequest(content="x"),
        request=_fake_request(),
        user={"user_id": 1, "role": "user"},
        db=SimpleNamespace(),
    )

    assert seen == ["element:knowledge:parse_document"]
