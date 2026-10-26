"""同名上传的「替换」流程。

顺序必须是：上传新文件 → 改名回旧名 → 删旧文档 → 触发解析。
反过来做（先删旧再上传）一旦上传失败，用户就没文档了；
而"删旧失败"必须回滚掉新文件，否则库里同时躺着两个。

解析失败是唯一不回滚的情况：旧文档已经删了，再删新的等于两个都没，
只能让用户手动重试解析。
"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


class _FakeUpload:
    def __init__(self, filename: str, payload: bytes = b"content"):
        self.filename = filename
        self.content_type = "application/pdf"
        self.size = len(payload)
        self._payload = payload
        self._read = False

    async def read(self, size: int = -1):
        if self._read:
            return b""
        self._read = True
        return self._payload


def _fake_request():
    return SimpleNamespace(method="POST", client=SimpleNamespace(host="127.0.0.1"))


class _FakeClient:
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def list_documents(self, dataset_id, page_size=100, **kwargs):
        _FakeClient.calls.append(("list", dataset_id))
        return list(_FakeClient.existing)

    async def upload_document(self, dataset_id, file_name, blob, content_type="x"):
        _FakeClient.calls.append(("upload", file_name))
        if _FakeClient.fail_on == "upload":
            raise RuntimeError("upload failed")
        return {"id": "new-doc"}

    async def update_document(self, dataset_id, document_id, **kwargs):
        _FakeClient.calls.append(("update", document_id, kwargs))
        if _FakeClient.fail_on == "update":
            raise RuntimeError("update failed")
        return {"id": document_id}

    async def delete_documents(self, dataset_id, document_ids):
        _FakeClient.calls.append(("delete", tuple(document_ids)))
        if _FakeClient.fail_on == "delete":
            raise RuntimeError("delete failed")

    async def parse_documents(self, dataset_id, document_ids):
        _FakeClient.calls.append(("parse", tuple(document_ids)))
        if _FakeClient.fail_on == "parse":
            raise RuntimeError("parse failed")


async def _allow(*args, **kwargs):
    return None


def _patch(monkeypatch, audit: dict | None = None):
    _FakeClient.calls = []
    _FakeClient.existing = [{"id": "old-doc", "name": "制度手册.pdf"}]
    _FakeClient.fail_on = None

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


@pytest.mark.asyncio
async def test_replace_uploads_then_deletes_old(monkeypatch):
    audit: dict = {}
    _patch(monkeypatch, audit)

    res = await ragflow.replace_dataset_document(
        dataset_id="ds-1",
        request=_fake_request(),
        file=_FakeUpload("制度手册.pdf"),
        old_document_id="old-doc",
        user={"user_id": 1, "user_name": "alice", "role": "user"},
        db=SimpleNamespace(),
    )

    assert res["code"] == 0
    assert res["data"]["id"] == "new-doc"
    kinds = [call[0] for call in _FakeClient.calls]
    # 旧文档必须在新文档就绪之后才删
    assert kinds == ["list", "upload", "update", "delete", "parse"]
    # 新文件可能被 RAGFlow 加上 "(1)" 后缀，改名回旧名
    assert _FakeClient.calls[2][2]["name"] == "制度手册.pdf"
    assert audit["action"] == "documents:replace"
    assert audit["payload"]["old_document_id"] == "old-doc"
    assert audit["payload"]["new_document_id"] == "new-doc"


@pytest.mark.asyncio
async def test_replace_rejects_unknown_old_document(monkeypatch):
    _patch(monkeypatch)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.replace_dataset_document(
            dataset_id="ds-1",
            request=_fake_request(),
            file=_FakeUpload("制度手册.pdf"),
            old_document_id="doc-from-another-dataset",
            user={"user_id": 1, "role": "user"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 404
    # 不能借"替换"删别的库（或已不存在）的文档
    assert [call[0] for call in _FakeClient.calls] == ["list"]


@pytest.mark.asyncio
async def test_replace_rolls_back_new_document_when_delete_fails(monkeypatch):
    _patch(monkeypatch)
    _FakeClient.fail_on = "delete"

    with pytest.raises(RuntimeError):
        await ragflow.replace_dataset_document(
            dataset_id="ds-1",
            request=_fake_request(),
            file=_FakeUpload("制度手册.pdf"),
            old_document_id="old-doc",
            user={"user_id": 1, "role": "user"},
            db=SimpleNamespace(),
        )

    deletes = [call for call in _FakeClient.calls if call[0] == "delete"]
    # 第一次是删旧失败，第二次是回滚删新
    assert deletes == [("delete", ("old-doc",)), ("delete", ("new-doc",))]


@pytest.mark.asyncio
async def test_replace_does_not_roll_back_when_parse_fails(monkeypatch):
    """旧文档已删、新文档已就位：这时再删新的等于两个都没。"""
    _patch(monkeypatch)
    _FakeClient.fail_on = "parse"

    res = await ragflow.replace_dataset_document(
        dataset_id="ds-1",
        request=_fake_request(),
        file=_FakeUpload("制度手册.pdf"),
        old_document_id="old-doc",
        user={"user_id": 1, "role": "user"},
        db=SimpleNamespace(),
    )

    assert res["code"] == 0
    deletes = [call for call in _FakeClient.calls if call[0] == "delete"]
    assert deletes == [("delete", ("old-doc",))]
