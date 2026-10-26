"""访问申请端点的边界：私库不露出、已有权限不必申请、只有归属人能审批。"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def execute(self, stmt):
        return _Result(self.rows)


class _PermService:
    def __init__(self, db, *, is_admin=False, accessible=None, writable=None):
        self.is_admin = is_admin
        self.accessible = accessible or set()
        self.writable = writable or set()

    async def get_knowledge_base_access(self, user_id, user_name=None):
        return {
            "is_admin": self.is_admin,
            "accessible_ids": self.accessible,
            "writable_ids": self.writable,
        }


def _meta(**kwargs):
    base = SimpleNamespace(
        ragflow_dataset_id="ds-1",
        name="制度库",
        description="公司制度",
        owner="财务部",
        visibility="public",
        status="active",
        owner_dept_id=3,
        updated_at=None,
    )
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


class _MetaService:
    def __init__(self, db):
        pass

    async def list_metadata(self, include_deleted=False):
        return [_meta(), _meta(ragflow_dataset_id="ds-2", name="私有库", visibility="private")]

    async def get_by_dataset_id(self, dataset_id):
        if dataset_id == "ds-1":
            return _meta()
        if dataset_id == "ds-2":
            return _meta(ragflow_dataset_id="ds-2", name="私有库", visibility="private")
        return None


async def _allow(*args, **kwargs):
    return None


def _patch(monkeypatch, perm: _PermService):
    monkeypatch.setattr(ragflow, "PermissionService", lambda db: perm)
    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", _MetaService)

    async def fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)


def _request():
    return SimpleNamespace(method="GET", client=SimpleNamespace(host="127.0.0.1"))


@pytest.mark.asyncio
async def test_discover_hides_private_and_already_accessible(monkeypatch):
    _patch(monkeypatch, _PermService(None, accessible={"ds-9"}))

    res = await ragflow.discover_datasets(
        user={"user_id": 9, "user_name": "bob"},
        db=_Session(),
    )

    names = [item["name"] for item in res["data"]["items"]]
    assert names == ["制度库"]


@pytest.mark.asyncio
async def test_discover_excludes_datasets_i_already_have(monkeypatch):
    _patch(monkeypatch, _PermService(None, accessible={"ds-1", "ds-9"}))

    res = await ragflow.discover_datasets(
        user={"user_id": 9, "user_name": "bob"},
        db=_Session(),
    )

    assert res["data"]["items"] == []


@pytest.mark.asyncio
async def test_cannot_request_private_dataset(monkeypatch):
    _patch(monkeypatch, _PermService(None))

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.create_access_request(
            dataset_id="ds-2",
            payload=ragflow.AccessRequestBody(reason="想看"),
            request=_request(),
            user={"user_id": 9, "user_name": "bob"},
            db=_Session(),
        )

    assert exc.value.status_code == 400
    assert "私有" in exc.value.detail


@pytest.mark.asyncio
async def test_cannot_request_what_i_already_have(monkeypatch):
    _patch(monkeypatch, _PermService(None, accessible={"ds-1"}))

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.create_access_request(
            dataset_id="ds-1",
            payload=ragflow.AccessRequestBody(),
            request=_request(),
            user={"user_id": 9, "user_name": "bob"},
            db=_Session(),
        )

    assert exc.value.status_code == 400
    assert "已经可以访问" in exc.value.detail


@pytest.mark.asyncio
async def test_reviewer_must_be_owner_or_admin(monkeypatch):
    _patch(monkeypatch, _PermService(None, writable={"ds-other"}))

    row = SimpleNamespace(
        id=5,
        dataset_id="ds-1",
        status="pending",
        applicant_user_id=9,
        applicant_user_name="bob",
    )

    class _Service:
        def __init__(self, db):
            pass

        async def get(self, request_id):
            return row

        async def decide(self, **kwargs):  # pragma: no cover - 不应走到
            raise AssertionError("越权审批不该进入 decide")

    monkeypatch.setattr(ragflow, "KnowledgeAccessRequestService", _Service)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.approve_access_request(
            request_id=5,
            payload=ragflow.AccessReviewBody(comment="同意"),
            request=_request(),
            user={"user_id": 1, "user_name": "alice"},
            db=_Session(),
        )

    assert exc.value.status_code == 403
