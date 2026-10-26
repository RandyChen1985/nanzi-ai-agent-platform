"""知识库业务审计：写入拆条、失败不拖累主流程、查询按级别裁剪。

背景：审计以前只写进通用访问日志，`dataset_id` 埋在 JSON 文本里，
按库查询等于全表 LIKE；而访问日志本身会被定期清理，业务审计不能跟着没。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

import app.services.knowledge_audit_service as audit_module
from app.api.portal.endpoints import ragflow
from app.services.knowledge_audit_service import KnowledgeAuditService

pytestmark = pytest.mark.no_infrastructure


class _FakeSession:
    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def add_all(self, objs):
        self._sink.extend(objs)

    async def commit(self):
        self._sink.append("committed")


@pytest.mark.asyncio
async def test_log_writes_one_row_per_dataset(monkeypatch):
    sink = []
    monkeypatch.setattr(audit_module, "AsyncSessionLocal", lambda: _FakeSession(sink))

    ok = await KnowledgeAuditService.log(
        dataset_ids=["ds-a", "ds-b", "ds-a", "  "],
        action="documents:upload",
        user={"user_id": 7, "user_name": "chenxiaolong"},
        detail={"file_name": "预算.xlsx"},
        client_ip="10.0.0.1",
    )

    assert ok is True
    rows = [item for item in sink if item != "committed"]
    # 去重 + 去空：同一个库只留一条
    assert sorted(row.dataset_id for row in rows) == ["ds-a", "ds-b"]
    assert rows[0].action == "documents:upload"
    assert rows[0].user_id == 7
    assert rows[0].detail == {"file_name": "预算.xlsx"}


@pytest.mark.asyncio
async def test_log_is_noop_without_dataset_ids(monkeypatch):
    sink = []
    monkeypatch.setattr(audit_module, "AsyncSessionLocal", lambda: _FakeSession(sink))

    ok = await KnowledgeAuditService.log(dataset_ids=[], action="datasets:create")

    assert ok is False
    assert sink == []


@pytest.mark.asyncio
async def test_log_swallows_database_errors(monkeypatch):
    """审计是旁路：写不进去也绝不能让知识库操作失败。"""

    class _BoomSession:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(audit_module, "AsyncSessionLocal", lambda: _BoomSession())

    ok = await KnowledgeAuditService.log(
        dataset_ids=["ds-a"], action="documents:upload"
    )
    assert ok is False


# ---------------------------------------------------------------- 查询分级


class _FakeResult:
    def __init__(self, scalar_value=None, rows=None):
        self._scalar = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows
        self.calls = 0

    async def execute(self, stmt):
        self.calls += 1
        if self.calls == 1:
            return _FakeResult(scalar_value=len(self._rows))
        return _FakeResult(rows=self._rows)


def _row(action="documents:upload"):
    return SimpleNamespace(
        id=1,
        action=action,
        user_id=7,
        user_name="chenxiaolong",
        status_code=200,
        detail={"file_name": "预算.xlsx"},
        client_ip="10.0.0.1",
        error_message=None,
        created_at=datetime(2026, 10, 26, 14, 2, 0),
    )


def _patch_access(monkeypatch, *, is_admin=False, writable=()):
    class _FakePermissionService:
        def __init__(self, db):
            pass

        async def get_knowledge_base_access(self, user_id, user_name=None):
            return {
                "is_admin": is_admin,
                "accessible_ids": {"ds-1"},
                "writable_ids": set(writable),
                "content_writable_ids": set(writable),
            }

    monkeypatch.setattr(ragflow, "PermissionService", _FakePermissionService)

    async def _fake_require_read(user, db, ids):
        return None

    monkeypatch.setattr(ragflow, "require_dataset_access", _fake_require_read)


@pytest.mark.asyncio
async def test_manager_sees_all_actions(monkeypatch):
    _patch_access(monkeypatch, writable={"ds-1"})
    res = await ragflow.list_dataset_audit_logs(
        dataset_id="ds-1",
        page=1,
        size=20,
        action=None,
        user={"user_id": 1, "user_name": "chenxiaolong"},
        db=_FakeDB([_row("datasets:grant")]),
    )
    assert res["data"]["can_manage"] is True
    assert res["data"]["items"][0]["action"] == "datasets:grant"


@pytest.mark.asyncio
async def test_team_member_gets_content_only_flag(monkeypatch):
    _patch_access(monkeypatch, writable=())
    res = await ragflow.list_dataset_audit_logs(
        dataset_id="ds-1",
        page=1,
        size=20,
        action=None,
        user={"user_id": 2, "user_name": "dev"},
        db=_FakeDB([_row()]),
    )
    assert res["data"]["can_manage"] is False


@pytest.mark.asyncio
async def test_non_manager_cannot_probe_sensitive_action(monkeypatch):
    """部门成员显式请求「授权变更」不应看到记录，也不应报错暴露存在性。"""
    _patch_access(monkeypatch, writable=())
    res = await ragflow.list_dataset_audit_logs(
        dataset_id="ds-1",
        page=1,
        size=20,
        action="datasets:grant",
        user={"user_id": 2, "user_name": "dev"},
        db=_FakeDB([_row("datasets:grant")]),
    )
    assert res["data"]["items"] == []
    assert res["data"]["total"] == 0


def _fake_request(method: str = "POST"):
    return SimpleNamespace(
        method=method,
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(trace_id="test-trace"),
    )


@pytest.mark.asyncio
async def test_sync_writes_audit_for_touched_datasets(monkeypatch):
    """同步必须落审计：以前 payload 没有 dataset_ids，log() 直接返回 False，
    于是「点过同步」在事后完全无痕。"""
    captured = {}

    class FakeMetaService:
        def __init__(self, db):
            pass

        async def sync_from_ragflow_datasets(self, datasets, user_name=None):
            return {
                "created": 1,
                "updated": 1,
                "skipped": 0,
                "dataset_ids": ["ds-new", "ds-old"],
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def list_datasets(self, **kwargs):
            return [{"id": "ds-new", "name": "新库"}]

    async def fake_record(request, user, action, status_code, payload, error_message=None, started_at=None):
        captured["action"] = action
        captured["dataset_ids"] = payload.get("dataset_ids")
        captured["status_code"] = status_code

    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetaService)
    monkeypatch.setattr(ragflow, "RagFlowClient", FakeClient)
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_record)

    await ragflow.sync_ragflow_datasets(
        request=_fake_request(),
        user={"role": "admin", "user_id": "1", "user_name": "admin"},
        db=SimpleNamespace(),
    )

    assert captured["action"] == "datasets:sync"
    assert captured["dataset_ids"] == ["ds-new", "ds-old"]


@pytest.mark.asyncio
async def test_audit_records_upstream_status_code(monkeypatch):
    """RAGFlow 业务错误（status_code=502）不该被一律记成 500。"""
    captured = {}
    from app.core.errors import RagFlowServiceError

    class FakeMetaService:
        def __init__(self, db):
            pass

        async def find_active_by_name(self, name):
            return None

    class BoomClient:
        def __init__(self, **kwargs):
            pass

        async def create_dataset(self, **kwargs):
            raise RagFlowServiceError("embedding 模型不可用")

    async def fake_record(request, user, action, status_code, payload, error_message=None, started_at=None):
        captured["status_code"] = status_code
        captured["error_message"] = error_message

    async def _allow(*args, **kwargs):
        return None

    monkeypatch.setattr(ragflow, "KnowledgeBaseMetadataService", FakeMetaService)
    monkeypatch.setattr(ragflow, "RagFlowClient", BoomClient)
    monkeypatch.setattr(ragflow, "require_element_permission", _allow)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_record)

    with pytest.raises(RagFlowServiceError):
        await ragflow.create_ragflow_dataset(
            payload=ragflow.CreateDatasetRequest(name="新库"),
            request=_fake_request(),
            user={"role": "user", "user_id": "1", "user_name": "alice"},
            db=SimpleNamespace(),
        )

    assert captured["status_code"] == 502
    assert "embedding" in captured["error_message"]
