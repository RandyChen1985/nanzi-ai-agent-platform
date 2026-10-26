"""跨库全局审计：管理员视角的「某人某天动了哪些库」。

原来只能逐个库点开看操作日志，出了事没法倒查——尤其"某个人今天动了哪些库"
这种问题，要挨个库翻一遍。全局视图还要把 ``__global__`` 那类没落到具体库的
记录（同步、巡检）一并带出来，否则「点过同步但什么都没发生」查不到。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow

pytestmark = pytest.mark.no_infrastructure


class _Result:
    """count 查询走 .scalar()，明细查询走 .scalars().all()，两者共用一份假结果。"""

    def __init__(self, total, rows):
        self._total = total
        self._rows = rows

    def scalar(self):
        return self._total

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, total=0, rows=None):
        self.total = total
        self.rows = rows or []
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self.total, self.rows)


def _row(**kwargs):
    base = {
        "id": 1,
        "dataset_id": "ds-1",
        "action": "documents:delete",
        "user_id": 7,
        "user_name": "alice",
        "status_code": 200,
        "detail": {"file_name": "旧制度.pdf"},
        "client_ip": "10.0.0.1",
        "error_message": None,
        "created_at": datetime(2026, 10, 26, 10, 30, 0),
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class _PermService:
    def __init__(self, db, admin=True):
        self.admin = admin

    async def get_knowledge_base_access(self, user_id, user_name=None):
        return {"is_admin": self.admin, "accessible_ids": {"ds-1"}, "writable_ids": {"ds-1"}}


def _patch(monkeypatch, admin=True):
    monkeypatch.setattr(
        ragflow, "PermissionService", lambda db: _PermService(db, admin=admin)
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_read_global_audit(monkeypatch):
    _patch(monkeypatch, admin=False)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.list_global_audit_logs(
            user={"user_id": 9, "user_name": "bob", "role": "user"},
            db=_Session(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_gets_items_including_global_records(monkeypatch):
    _patch(monkeypatch)
    db = _Session(total=2, rows=[_row(), _row(id=2, dataset_id="__global__", action="datasets:sync")])

    res = await ragflow.list_global_audit_logs(
        user={"user_id": 1, "user_name": "root", "role": "admin"},
        db=db,
    )

    assert res["code"] == 0
    assert res["data"]["total"] == 2
    assert [item["dataset_id"] for item in res["data"]["items"]] == ["ds-1", "__global__"]
    assert res["data"]["items"][0]["created_at"] == "2026-10-26T10:30:00"


@pytest.mark.asyncio
async def test_action_filter_is_applied(monkeypatch):
    _patch(monkeypatch)
    db = _Session(total=0, rows=[])

    await ragflow.list_global_audit_logs(
        action="chunks:delete",
        dataset_id="ds-1",
        user_id=7,
        user={"user_id": 1, "role": "admin"},
        db=db,
    )

    sql = str(db.statements[0])
    assert "knowledge_audit" in sql.lower()
    # 三个过滤条件都应进 WHERE
    assert "action" in sql.lower()
    assert "dataset_id" in sql.lower()
    assert "user_id" in sql.lower()


@pytest.mark.asyncio
async def test_invalid_date_is_rejected(monkeypatch):
    _patch(monkeypatch)

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.list_global_audit_logs(
            start="2026年10月",
            user={"user_id": 1, "role": "admin"},
            db=_Session(),
        )

    assert exc.value.status_code == 400
    assert "时间格式" in exc.value.detail


@pytest.mark.asyncio
async def test_csv_export_has_bom_and_header(monkeypatch):
    _patch(monkeypatch)
    db = _Session(total=1, rows=[_row()])

    response = await ragflow.list_global_audit_logs(
        export="csv",
        user={"user_id": 1, "role": "admin"},
        db=db,
    )

    assert response.media_type == "text/csv"
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    body = b"".join(chunks)

    # BOM：否则 Excel 打开中文是乱码
    assert body.startswith(b"\xef\xbb\xbf")
    text = body.decode("utf-8-sig")
    assert "时间" in text
    assert "alice" in text
