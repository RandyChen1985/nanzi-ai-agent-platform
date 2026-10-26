"""文档重命名 / 启停。

RAGFlow 的 `PUT /documents/{id}` 支持部分更新（name / enabled / chunk_method /
parser_config）。这两个动作的价值在于：不必删文档就能把过期内容移出召回，
以及纠正传错的文件名 —— 而删了再传会丢掉切片与引用。
"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


def _fake_request(method: str = "PUT"):
    return SimpleNamespace(method=method, client=SimpleNamespace(host="127.0.0.1"))


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def update_document(self, dataset_id, document_id, **kwargs):
        _FakeClient.calls = {"dataset_id": dataset_id, "document_id": document_id, **kwargs}
        return {"id": document_id, "name": kwargs.get("name") or "old.pdf", "enabled": kwargs.get("enabled", True)}


async def _allow(*args, **kwargs):
    return None


def _patch(monkeypatch, audit: dict | None = None):
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "require_dataset_content_write_access", _allow)
    monkeypatch.setattr(ragflow, "RagFlowClient", _FakeClient)

    async def fake_audit(request, user, action, status_code, payload_, error_message=None, started_at=None):
        if audit is not None:
            audit.update(
                action=action,
                status_code=status_code,
                payload=payload_,
                error_message=error_message,
            )

    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)
    _FakeClient.calls = {}


@pytest.mark.asyncio
async def test_update_document_name_and_enabled(monkeypatch):
    audit: dict = {}
    _patch(monkeypatch, audit)

    res = await ragflow.update_dataset_document(
        dataset_id="ds-1",
        document_id="doc-1",
        payload=ragflow.UpdateDocumentRequest(name="  制度手册 v2.pdf  ", enabled=False),
        request=_fake_request(),
        user={"user_id": 1, "user_name": "alice", "role": "user"},
        db=SimpleNamespace(),
    )

    assert res["code"] == 0
    # 名称要 strip：带前后空格的名字在树里看起来像"没改"
    assert _FakeClient.calls["name"] == "制度手册 v2.pdf"
    assert _FakeClient.calls["enabled"] is False
    assert "chunk_method" not in _FakeClient.calls
    assert audit["action"] == "documents:update"
    assert audit["payload"]["document_id"] == "doc-1"


@pytest.mark.asyncio
async def test_update_document_requires_at_least_one_field(monkeypatch):
    _patch(monkeypatch)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.update_dataset_document(
            dataset_id="ds-1",
            document_id="doc-1",
            payload=ragflow.UpdateDocumentRequest(),
            request=_fake_request(),
            user={"user_id": 1, "role": "user"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 400
    assert "没有要更新的字段" in exc.value.detail


@pytest.mark.asyncio
async def test_update_document_rejects_blank_name(monkeypatch):
    _patch(monkeypatch)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.update_dataset_document(
            dataset_id="ds-1",
            document_id="doc-1",
            payload=ragflow.UpdateDocumentRequest(name="   "),
            request=_fake_request(),
            user={"user_id": 1, "role": "user"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 400
    assert "名称不能为空" in exc.value.detail


@pytest.mark.asyncio
async def test_update_document_uses_parse_permission_code(monkeypatch):
    """切片编辑与文档改名都属于"动内容"，复用 parse_document，不新增权限码。"""
    seen: list[str] = []

    async def fake_perm(user, db, code):
        seen.append(code)

    _patch(monkeypatch)
    monkeypatch.setattr(ragflow, "require_element_permission", fake_perm)

    await ragflow.update_dataset_document(
        dataset_id="ds-1",
        document_id="doc-1",
        payload=ragflow.UpdateDocumentRequest(enabled=True),
        request=_fake_request(),
        user={"user_id": 1, "role": "user"},
        db=SimpleNamespace(),
    )

    assert seen == ["element:knowledge:parse_document"]
