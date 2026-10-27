from app.services.ai.user_question_history import (
    apply_question_status_to_timeline,
    persist_user_question_cancellation,
)


def _question_log(question_id: str, status: str = "pending") -> dict:
    return {
        "kind": "log",
        "id": f"user_question_{question_id}",
        "category": "user_question",
        "status": status,
        "user_question": {"question_id": question_id, "status": status},
    }


def test_marks_matching_question_log_and_snapshot():
    """取消/作答后日志项与卡片快照都要变成终态，否则刷新后仍显示进行中 + 可点。"""
    other = {"kind": "log", "id": "router-1", "category": "router", "status": "success"}
    target = _question_log("uq_1")
    timeline = [other, target]

    assert apply_question_status_to_timeline(timeline, "uq_1", "cancelled") is True

    assert target["status"] == "success"
    assert target["user_question"]["status"] == "cancelled"
    # 其它轮次不能被顺手改掉
    assert other == {"kind": "log", "id": "router-1", "category": "router", "status": "success"}


def test_marks_nested_question_and_keeps_other_questions_pending():
    """提问卡可能挂在 children 里；同一会话的另一个问题必须保持 pending。"""
    pending_other = _question_log("uq_other")
    nested = _question_log("uq_2")
    timeline = [
        {"kind": "log", "id": "parent", "category": "tool", "status": "pending", "children": [nested]},
        pending_other,
    ]

    assert apply_question_status_to_timeline(timeline, "uq_2", "submitted") is True

    assert nested["user_question"]["status"] == "submitted"
    assert pending_other["user_question"]["status"] == "pending"
    assert pending_other["status"] == "pending"


def test_returns_false_when_question_is_absent():
    timeline = [_question_log("uq_1")]
    assert apply_question_status_to_timeline(timeline, "uq_missing", "cancelled") is False
    assert timeline[0]["user_question"]["status"] == "pending"


def test_ignores_non_question_log_with_same_id():
    """只有 category=user_question 的日志才算命中，防止同 id 的其它日志被误标。"""
    timeline = [
        {
            "kind": "log",
            "id": "user_question_uq_1",
            "category": "tool",
            "status": "pending",
            "user_question": {"question_id": "uq_1", "status": "pending"},
        }
    ]
    assert apply_question_status_to_timeline(timeline, "uq_1", "cancelled") is False


def test_empty_inputs_are_noops():
    assert apply_question_status_to_timeline(None, "uq_1", "cancelled") is False
    assert apply_question_status_to_timeline([], "uq_1", "cancelled") is False
    assert apply_question_status_to_timeline([_question_log("uq_1")], "", "cancelled") is False


def test_history_writer_is_importable_and_async():
    """DB 写入入口必须存在且是协程（preflight 里 await 调用）。"""
    import inspect

    assert inspect.iscoroutinefunction(persist_user_question_cancellation)


# --- 取消轮补历史 ---------------------------------------------------------


import pytest
from sqlalchemy import BigInteger  # noqa: F401  （保持与模型一致的列类型导入习惯）

from app.models.audit import AgentExecutionHistory
import app.services.ai.user_question_history as history_module


class _FakeResult:
    def __init__(self, payload):
        self._payload = payload

    def scalar_one_or_none(self):
        return self._payload

    def scalars(self):
        return self

    def all(self):
        return self._payload


class _FakeSession:
    """按调用顺序返回「已有 trace 查询」与「最近轮次查询」的结果。"""

    def __init__(self, *, existing_id=None, recent_rows=None):
        self._existing_id = existing_id
        self._recent_rows = recent_rows or []
        self._calls = 0
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _stmt):
        self._calls += 1
        if self._calls == 1:
            return _FakeResult(self._existing_id)
        return _FakeResult(self._recent_rows)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _pending_row() -> AgentExecutionHistory:
    return AgentExecutionHistory(
        agent_id="agent-1",
        trace_id="trace-question",
        conversation_id="conv-1",
        user_id="1",
        username="admin",
        process_timeline=[_question_log("uq_1")],
    )


@pytest.mark.asyncio
async def test_cancellation_inserts_turn_and_marks_question(monkeypatch):
    """取消后要补一条 cancelled 轮次，同时把提问轮快照标成终态。"""
    row = _pending_row()
    session = _FakeSession(recent_rows=[row])
    monkeypatch.setattr(history_module, "AsyncSessionLocal", lambda: session)

    ok = await persist_user_question_cancellation(
        conversation_id="conv-1",
        question_id="uq_1",
        trace_id="trace-cancel",
        user_query="【用户回答】… cancelled: true",
    )

    assert ok is True
    assert session.committed is True
    assert len(session.added) == 1
    inserted = session.added[0]
    assert inserted.agent_id == "agent-1"  # 继承上一轮，满足非空约束
    assert inserted.trace_id == "trace-cancel"
    assert inserted.status == "cancelled"
    assert inserted.summary == history_module.CANCELLATION_MESSAGE
    assert inserted.conversation_id == "conv-1"
    # 快照里的卡片也要变成 cancelled，刷新后不再可点
    assert row.process_timeline[0]["user_question"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancellation_is_idempotent_by_trace_id(monkeypatch):
    """同一 trace 已经落过就不再插入（重试 / 并发）。"""
    session = _FakeSession(existing_id=123)
    monkeypatch.setattr(history_module, "AsyncSessionLocal", lambda: session)

    ok = await persist_user_question_cancellation(
        conversation_id="conv-1",
        question_id="uq_1",
        trace_id="trace-cancel",
        user_query="receipt",
    )

    assert ok is True
    assert session.added == []
    assert session.committed is False


@pytest.mark.asyncio
async def test_cancellation_falls_back_to_context_agent(monkeypatch):
    """会话还没有任何历史时，用请求上下文里的 agent_id 兜底。"""
    session = _FakeSession(recent_rows=[])
    monkeypatch.setattr(history_module, "AsyncSessionLocal", lambda: session)

    ok = await persist_user_question_cancellation(
        conversation_id="conv-1",
        question_id="uq_1",
        trace_id="trace-cancel",
        user_query="receipt",
        fallback_agent_id="agent-fallback",
        fallback_user_id="9",
    )

    assert ok is True
    assert session.added[0].agent_id == "agent-fallback"
    assert session.added[0].user_id == "9"


@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_duplicate_turn(monkeypatch):
    """连点「取消」每次都会生成新 trace，但同一 question_id 只允许一条取消轮。"""
    previous = AgentExecutionHistory(
        agent_id="agent-1",
        trace_id="trace-old-cancel",
        conversation_id="conv-1",
        status="cancelled",
        query="【用户回答】\nquestion_id: uq_1\ncancelled: true",
    )
    row = _pending_row()
    session = _FakeSession(recent_rows=[previous, row])
    monkeypatch.setattr(history_module, "AsyncSessionLocal", lambda: session)

    ok = await persist_user_question_cancellation(
        conversation_id="conv-1",
        question_id="uq_1",
        trace_id="trace-new-cancel",
        user_query="【用户回答】\nquestion_id: uq_1\ncancelled: true",
    )

    assert ok is True
    assert session.added == [], "同一问题的第二次取消不应再补一条历史轮次"
    assert row.process_timeline[0]["user_question"]["status"] == "cancelled"
