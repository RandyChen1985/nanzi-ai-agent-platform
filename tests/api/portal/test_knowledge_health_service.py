"""健康巡检：解析状态归类、治理摘要、管理员门槛。"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow
from app.services.knowledge_health_service import summarize_documents, summarize_snapshots

pytestmark = pytest.mark.no_infrastructure


def test_summarize_documents_maps_ragflow_run_states():
    stats = summarize_documents([
        {"run": 1, "chunk_count": 10},
        {"run": "DONE", "chunk_count": 5},
        {"run": 2},
        {"run": "RUNNING"},
        {"run": 3},
        {"run": 0},
        {},
    ])

    assert stats["documents"] == 7
    assert stats["parsed"] == 2
    assert stats["parsing"] == 2
    assert stats["failed"] == 1
    # 未知状态算「尚未解析」而不是失败：当失败会让治理视图长期挂着假告警
    assert stats["unparsed"] == 2
    assert stats["chunks"] == 15


def test_summarize_snapshots_counts_actionable_buckets():
    rows = [
        SimpleNamespace(dataset_id="a", name="失联库", is_missing=True, documents=0, failed=0, stale_days=1),
        SimpleNamespace(dataset_id="b", name="空库", is_missing=False, documents=0, failed=0, stale_days=10),
        SimpleNamespace(dataset_id="c", name="失败库", is_missing=False, documents=8, failed=3, stale_days=5),
        SimpleNamespace(dataset_id="d", name="老库", is_missing=False, documents=4, failed=0, stale_days=120),
    ]

    summary = summarize_snapshots(rows)

    assert summary["total"] == 4
    assert summary["missing"] == 1
    assert summary["empty"] == 1
    assert summary["failing"] == 1
    assert summary["stale"] == 1
    assert summary["missing_items"][0]["name"] == "失联库"


class _PermService:
    def __init__(self, db, admin):
        self.admin = admin

    async def get_knowledge_base_access(self, user_id, user_name=None):
        return {"is_admin": self.admin}


@pytest.mark.asyncio
async def test_health_endpoints_require_admin(monkeypatch):
    monkeypatch.setattr(ragflow, "PermissionService", lambda db: _PermService(db, False))

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.get_knowledge_health(
            user={"user_id": 5, "user_name": "bob"}, db=SimpleNamespace()
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_health_scan_writes_audit_and_returns_summary(monkeypatch):
    monkeypatch.setattr(ragflow, "PermissionService", lambda db: _PermService(db, True))

    async def fake_scan(db, concurrency=4):
        return {"date": "2026-10-26", "scanned": 3, "total": 3, "missing": 1, "empty": 0, "failing": 1, "stale": 0}

    captured = {}

    async def fake_audit(request, user, action, status_code, payload, error_message=None, started_at=None):
        captured["action"] = action
        captured["payload"] = payload

    monkeypatch.setattr(ragflow.KnowledgeHealthService, "scan", fake_scan)
    monkeypatch.setattr(ragflow, "record_knowledge_audit", fake_audit)

    res = await ragflow.scan_knowledge_health(
        request=SimpleNamespace(method="POST", client=SimpleNamespace(host="127.0.0.1")),
        user={"user_id": 1, "user_name": "root"},
        db=SimpleNamespace(),
    )

    assert res["data"]["missing"] == 1
    assert captured["action"] == "datasets:health_scan"
    assert captured["payload"]["summary"]["failing"] == 1
