"""检索效果指标：零结果率、P95、搜不到的 query。

只统计命中数看不出问题——命中 1 条和零结果在平均值里差别不大，
但对使用者一个是"勉强能用"，一个是"答非所问"。
"""

from types import SimpleNamespace

import pytest

from app.api.portal.endpoints import ragflow
from app.services import knowledge_retrieval_log_service as mod
from app.services.knowledge_retrieval_log_service import KnowledgeRetrievalLogService

pytestmark = pytest.mark.no_infrastructure


class _Result:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows


class _Session:
    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0) if self._results else _Result()


@pytest.mark.asyncio
async def test_stats_computes_zero_result_rate_and_p95():
    db = _Session([
        _Result(scalar=200),          # total
        _Result(scalar=30),           # zero
        _Result(scalar=2.5),          # avg hits
        _Result(scalar=180.4),        # avg latency
        _Result(scalar=900),          # p95 latency
        _Result(rows=[("报销流程", 7), ("年假几天", 3)]),  # top zero queries
    ])

    stats = await KnowledgeRetrievalLogService.stats(db, days=7)

    assert stats["total_queries"] == 200
    assert stats["zero_result_count"] == 30
    assert stats["zero_result_rate"] == 0.15
    assert stats["avg_hits"] == 2.5
    assert stats["p95_latency_ms"] == 900
    assert stats["top_zero_queries"][0] == {"query": "报销流程", "count": 7}


@pytest.mark.asyncio
async def test_stats_handles_empty_window_without_dividing_by_zero():
    db = _Session([_Result(scalar=0), _Result(scalar=0), _Result(scalar=None), _Result(scalar=None), _Result(rows=[])])

    stats = await KnowledgeRetrievalLogService.stats(db, days=7)

    assert stats["total_queries"] == 0
    assert stats["zero_result_rate"] == 0.0
    assert stats["p95_latency_ms"] is None


@pytest.mark.asyncio
async def test_stats_clamps_days_to_a_sane_range():
    db = _Session([_Result(scalar=0), _Result(scalar=0), _Result(scalar=None), _Result(scalar=None), _Result(rows=[])])

    stats = await KnowledgeRetrievalLogService.stats(db, days=9999)

    assert stats["days"] == 90


@pytest.mark.asyncio
async def test_blank_query_is_not_logged(monkeypatch):
    called = {"session": False}

    class _Boom:
        def __call__(self):
            called["session"] = True
            raise AssertionError("空查询不该开数据库会话")

    monkeypatch.setattr(mod, "AsyncSessionLocal", _Boom())

    assert await KnowledgeRetrievalLogService.log(query="   ") is False
    assert called["session"] is False


@pytest.mark.asyncio
async def test_log_failure_never_raises(monkeypatch):
    class _Broken:
        def __call__(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(mod, "AsyncSessionLocal", _Broken())

    assert await KnowledgeRetrievalLogService.log(query="报销流程", hit_count=0) is False


class _PermService:
    def __init__(self, db, admin):
        self.admin = admin

    async def get_knowledge_base_access(self, user_id, user_name=None):
        return {"is_admin": self.admin}


@pytest.mark.asyncio
async def test_retrieval_stats_requires_admin(monkeypatch):
    monkeypatch.setattr(ragflow, "PermissionService", lambda db: _PermService(db, False))

    with pytest.raises(ragflow.HTTPException) as exc:
        await ragflow.get_retrieval_stats(
            user={"user_id": 5, "user_name": "bob"},
            db=SimpleNamespace(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_retrieval_stats_returns_service_payload(monkeypatch):
    monkeypatch.setattr(ragflow, "PermissionService", lambda db: _PermService(db, True))

    async def fake_stats(db, *, days=7, source=None):
        return {"days": days, "total_queries": 12, "zero_result_rate": 0.25}

    monkeypatch.setattr(ragflow.KnowledgeRetrievalLogService, "stats", fake_stats)

    res = await ragflow.get_retrieval_stats(
        days=3,
        user={"user_id": 1, "user_name": "root"},
        db=SimpleNamespace(),
    )

    assert res["data"]["days"] == 3
    assert res["data"]["zero_result_rate"] == 0.25
