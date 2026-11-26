"""对话历史长文本落库保护。

背景：``ai_agent_execution_history`` 的 ``query`` / ``summary`` / ``reasoning_content``
长期是 MySQL ``TEXT``（65,535 字节），而 ``AuditManager.save_history`` 原样写入。
一旦思考内容或长回答超限，严格模式下整条 INSERT 失败，且异常被 ``except`` 吞掉，
表现为「Redis 里有这一轮、``/api/v1/chat/history`` 永远查不到」。

本文件锁定三条契约：
1. MySQL 上三列必须是 ``MEDIUMTEXT``（PG 保持 ``TEXT``）；
2. 落库前按 UTF-8 字节做保底截断并留下截断标记，任何内容都不再触发 1406；
3. 写入失败必须可识别、可重试，不能静默当成成功。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import mysql, postgresql

from app.models.audit import AgentExecutionHistory
from app.services.ai.audit import AuditManager
from app.services.ai.audit_payload import (
    FALLBACK_HISTORY_TEXT_BYTES,
    MAX_HISTORY_TEXT_BYTES,
    HISTORY_TRUNCATION_MARKER,
    truncate_text_bytes,
)

pytestmark = pytest.mark.no_infrastructure


class _FakeExecuteResult:
    def __init__(self, scalar_first=None, rows=None):
        self._scalar_first = scalar_first
        self._rows = list(rows or [])

    def scalars(self):
        first_item = self._scalar_first

        class _Scalars:
            def first(self):
                return first_item

        return _Scalars()

    def all(self):
        return list(self._rows)


class _FakeSession:
    """最小 AsyncSession 替身：覆盖 save_history / save_trace_logs 的查改与提交。"""

    def __init__(
        self,
        commit_error: BaseException | None = None,
        existing_history=None,
        existing_trace_rows=None,
    ):
        self.added: list = []
        self._commit_error = commit_error
        self._existing_history = existing_history
        self._existing_trace_rows = list(existing_trace_rows or [])
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objs):
        self.added.extend(objs)

    async def commit(self):
        self.commit_calls += 1
        if self._commit_error is not None:
            raise self._commit_error

    async def execute(self, *_args, **_kwargs):
        return _FakeExecuteResult(
            scalar_first=self._existing_history,
            rows=self._existing_trace_rows,
        )


class _SessionFactory:
    """按调用顺序发放预置 session，并记录总共被创建了几次。"""

    def __init__(self, sessions: list[_FakeSession]):
        self._sessions = list(sessions)
        self.created: list[_FakeSession] = []

    def __call__(self):
        session = self._sessions.pop(0)
        self.created.append(session)
        return session


def _save_kwargs(**overrides):
    kwargs = {
        "trace_id": "trace-history-text-1",
        "agent_id": "agent-1",
        "query": "请分析这份报告",
        "summary": "分析完成",
        "user_info": {"user_id": 7, "user_name": "tester"},
        "reasoning_content": "思考",
    }
    kwargs.update(overrides)
    return kwargs


def test_history_text_columns_are_mediumtext_on_mysql():
    """MySQL 的 TEXT 只有 65,535 字节，必须升级为 MEDIUMTEXT 才装得下长思考。"""
    for column_name in ("query", "summary", "reasoning_content"):
        column_type = AgentExecutionHistory.__table__.c[column_name].type
        assert (
            str(column_type.compile(dialect=mysql.dialect())) == "MEDIUMTEXT"
        ), f"{column_name} 在 MySQL 上必须编译为 MEDIUMTEXT"


def test_history_text_columns_stay_text_on_postgresql():
    """PG 的 TEXT 本身无长度上限，不要跟着改类型。"""
    for column_name in ("query", "summary", "reasoning_content"):
        column_type = AgentExecutionHistory.__table__.c[column_name].type
        assert str(column_type.compile(dialect=postgresql.dialect())) == "TEXT"


def test_truncate_text_bytes_keeps_utf8_boundary():
    text = "汉" * 1000  # 3000 字节
    truncated, original_bytes = truncate_text_bytes(text, max_bytes=300)

    assert original_bytes == 3000
    assert truncated.endswith(HISTORY_TRUNCATION_MARKER)
    assert len(truncated.encode("utf-8")) <= 300
    # 截断点落在多字节字符中间时不能产生乱码残字节
    truncated.encode("utf-8").decode("utf-8")


def test_truncate_text_bytes_returns_verbatim_when_within_budget():
    text = "短文本"
    truncated, original_bytes = truncate_text_bytes(text, max_bytes=1024)

    assert truncated == text
    assert original_bytes == len(text.encode("utf-8"))


def test_truncate_text_bytes_returns_none_for_empty_value():
    assert truncate_text_bytes(None, max_bytes=1024) == (None, 0)


@pytest.mark.asyncio
async def test_save_history_truncates_oversized_reasoning_before_insert():
    """超长推理不得原样落库，否则首轮 INSERT 就会被 1406 打回。"""
    oversized = "思" * (600 * 1024)  # 1.8MB，远超 1MiB 保底阈值
    session = _FakeSession()

    with patch(
        "app.services.ai.audit.AsyncSessionLocal", _SessionFactory([session])
    ):
        persisted = await AuditManager.save_history(
            **_save_kwargs(reasoning_content=oversized)
        )

    assert persisted is True
    saved = session.added[0]
    assert len(saved.reasoning_content.encode("utf-8")) <= MAX_HISTORY_TEXT_BYTES
    assert saved.reasoning_content.endswith(HISTORY_TRUNCATION_MARKER)


@pytest.mark.asyncio
async def test_save_history_keeps_normal_content_verbatim():
    """正常长度内容必须原样保存，截断只在超限时发生。"""
    session = _FakeSession()
    reasoning = "先看数据，再下结论。" * 100

    with patch(
        "app.services.ai.audit.AsyncSessionLocal", _SessionFactory([session])
    ):
        persisted = await AuditManager.save_history(
            **_save_kwargs(reasoning_content=reasoning, summary="结论：正常")
        )

    assert persisted is True
    saved = session.added[0]
    assert saved.reasoning_content == reasoning
    assert saved.summary == "结论：正常"


@pytest.mark.asyncio
async def test_save_history_retries_with_harder_truncation_on_data_too_long():
    """即便保底阈值仍被数据库拒绝，也必须降级重试一次而不是丢整轮历史。"""
    oversized = "思" * (600 * 1024)
    failing = _FakeSession(
        commit_error=RuntimeError(
            "(1406, \"Data too long for column 'reasoning_content' at row 1\")"
        )
    )
    retrying = _FakeSession()
    factory = _SessionFactory([failing, retrying])

    with patch("app.services.ai.audit.AsyncSessionLocal", factory):
        persisted = await AuditManager.save_history(
            **_save_kwargs(reasoning_content=oversized)
        )

    assert persisted is True
    assert len(factory.created) == 2, "首次长度失败后必须重试一次"
    saved = retrying.added[0]
    assert len(saved.reasoning_content.encode("utf-8")) <= FALLBACK_HISTORY_TEXT_BYTES


@pytest.mark.asyncio
async def test_save_history_retries_once_on_transient_error_and_reports_failure():
    """非长度类异常也要重试一次；两次都失败时必须返回 False，不能静默返回 None。"""
    first = _FakeSession(commit_error=RuntimeError("connection reset by peer"))
    second = _FakeSession(commit_error=RuntimeError("connection reset by peer"))
    factory = _SessionFactory([first, second])

    with patch("app.services.ai.audit.AsyncSessionLocal", factory):
        persisted = await AuditManager.save_history(**_save_kwargs())

    assert persisted is False
    assert len(factory.created) == 2


@pytest.mark.asyncio
async def test_save_history_upserts_existing_trace_and_preserves_query_when_blank():
    """同 trace_id 再次落库必须原地更新，且恢复轮缺 query 时不得抹空挂起轮原始提问。"""
    existing = AgentExecutionHistory(
        trace_id="trace-upsert-1",
        agent_id="agent-1",
        conversation_id="conv-1",
        user_id="7",
        username="tester",
        query="帮我执行部署检查",
        summary="",
        reasoning_content="审批前思考",
        process_timeline=[{"kind": "log", "id": "step_1", "status": "pending"}],
        status="awaiting_permission",
        execution_time_ms=1200.0,
        prompt_tokens=100,
        completion_tokens=40,
        total_tokens=140,
        has_data_output=0,
    )
    session = _FakeSession(existing_history=existing)

    with patch(
        "app.services.ai.audit.AsyncSessionLocal", _SessionFactory([session])
    ):
        persisted = await AuditManager.save_history(
            **_save_kwargs(
                trace_id="trace-upsert-1",
                query="",
                summary="检查已完成",
                reasoning_content="审批前思考\n\n审批后思考",
                process_timeline=[{"kind": "log", "id": "step_1", "status": "success"}],
                status="success",
                execution_time_ms=800.0,
                prompt_tokens=220,
                completion_tokens=90,
                total_tokens=310,
                accumulate_tokens=False,
                accumulate_duration=True,
            )
        )

    assert persisted is True
    assert session.added == [], "已存在同 trace_id 时应就地更新，不能重复 INSERT"
    assert existing.query == "帮我执行部署检查"
    assert existing.summary == "检查已完成"
    assert existing.status == "success"
    assert existing.execution_time_ms == 2000.0
    assert (existing.prompt_tokens, existing.completion_tokens, existing.total_tokens) == (
        220,
        90,
        310,
    )


@pytest.mark.asyncio
async def test_save_history_accumulates_tokens_and_duration_on_cross_process_resume():
    """跨进程恢复只掌握后半段 trace_buffer，必须把后半段 Token 与耗时累加到挂起轮。"""
    existing = AgentExecutionHistory(
        trace_id="trace-upsert-2",
        agent_id="agent-1",
        conversation_id="conv-1",
        user_id="7",
        query="跑一下巡检",
        summary="",
        status="awaiting_permission",
        execution_time_ms=1500.0,
        prompt_tokens=300,
        completion_tokens=80,
        total_tokens=380,
    )
    session = _FakeSession(existing_history=existing)

    with patch(
        "app.services.ai.audit.AsyncSessionLocal", _SessionFactory([session])
    ):
        persisted = await AuditManager.save_history(
            **_save_kwargs(
                trace_id="trace-upsert-2",
                summary="巡检全部通过",
                status="success",
                execution_time_ms=900.0,
                prompt_tokens=200,
                completion_tokens=50,
                total_tokens=250,
                accumulate_tokens=True,
                accumulate_duration=True,
            )
        )

    assert persisted is True
    assert existing.execution_time_ms == 2400.0
    assert (existing.prompt_tokens, existing.completion_tokens, existing.total_tokens) == (
        500,
        130,
        630,
    )


@pytest.mark.asyncio
async def test_save_history_recovers_from_concurrent_unique_conflict_without_truncating():
    """并发下首次 INSERT 撞唯一键（MySQL 1062 / PG 23505）时，必须重试转更新且不降级截断。"""
    existing = AgentExecutionHistory(
        trace_id="trace-race-1",
        agent_id="agent-1",
        conversation_id="conv-1",
        user_id="7",
        query="原始问题",
        summary="",
        status="awaiting_permission",
        execution_time_ms=500.0,
    )
    conflict_session = _FakeSession(
        commit_error=RuntimeError(
            "(1062, \"Duplicate entry 'trace-race-1' for key 'ix_trace_id'\")"
        )
    )
    update_session = _FakeSession(existing_history=existing)
    long_summary = "结" * 20_000  # 60KB > FALLBACK_HISTORY_TEXT_BYTES(32KB)，用于证明未误降级截断

    factory = _SessionFactory([conflict_session, update_session])
    with patch("app.services.ai.audit.AsyncSessionLocal", factory):
        persisted = await AuditManager.save_history(
            **_save_kwargs(
                trace_id="trace-race-1",
                summary=long_summary,
                status="success",
            )
        )

    assert persisted is True
    assert len(factory.created) == 2
    assert existing.status == "success"
    assert existing.summary == long_summary


@pytest.mark.asyncio
async def test_save_history_does_not_wipe_prior_content_on_empty_cancelled_resume():
    """恢复流强制取消（未回捞前序快照）写入空内容时，不得抹空挂起轮已存正文与时间线。"""
    prior_timeline = [{"kind": "log", "id": "prep_1", "status": "success"}]
    existing = AgentExecutionHistory(
        trace_id="trace-cancel-preserve-1",
        agent_id="agent-1",
        conversation_id="conv-1",
        user_id="7",
        query="帮我查一下",
        summary="前半段已输出内容",
        reasoning_content="前半段思考",
        process_timeline=prior_timeline,
        status="awaiting_permission",
        execution_time_ms=600.0,
    )
    session = _FakeSession(existing_history=existing)

    with patch("app.services.ai.audit.AsyncSessionLocal", _SessionFactory([session])):
        persisted = await AuditManager.save_history(
            **_save_kwargs(
                trace_id="trace-cancel-preserve-1",
                query="",
                summary="",
                reasoning_content=None,
                process_timeline=None,
                status="cancelled",
                execution_time_ms=120.0,
                accumulate_duration=True,
            )
        )

    assert persisted is True
    assert existing.status == "cancelled"
    assert existing.query == "帮我查一下"
    assert existing.summary == "前半段已输出内容"
    assert existing.reasoning_content == "前半段思考"
    assert existing.process_timeline == prior_timeline
    assert existing.execution_time_ms == 720.0


@pytest.mark.asyncio
async def test_save_trace_logs_dedups_and_offsets_cross_process_steps():
    """跨进程恢复写 trace：仅按非空 span_id 去重，span_id=None 的新步骤不被误删且步号严格顺延。"""
    from datetime import datetime
    from app.schemas.agent import AgentExecutionStep

    existing_rows = [
        (1, "tool_call", None),
        (2, "model_call", "span-1"),
    ]
    session = _FakeSession(existing_trace_rows=existing_rows)

    steps = [
        # 与挂起轮同 span_id：去重跳过
        AgentExecutionStep(
            step_number=1,
            event_type="model_call",
            span_id="span-1",
            timestamp=datetime.now(),
        ),
        # 跨进程重建 runner 从 step_number=1 重新编号且无 span_id：不得被误判重复而丢弃
        AgentExecutionStep(
            step_number=1,
            event_type="tool_call",
            span_id=None,
            timestamp=datetime.now(),
        ),
        # TraceSpanContext 默认 step_number=0 的新 span：应单调顺延不撞号
        AgentExecutionStep(
            step_number=0,
            event_type="agent_execution",
            span_id="span-3",
            timestamp=datetime.now(),
        ),
        AgentExecutionStep(
            step_number=2,
            event_type="model_call",
            span_id="span-4",
            timestamp=datetime.now(),
        ),
    ]

    with patch("app.services.ai.audit.AsyncSessionLocal", _SessionFactory([session])):
        await AuditManager.save_trace_logs(
            "trace-dedup-1",
            steps,
            dedup_existing=True,
            offset_step_number=True,
        )

    assert len(session.added) == 3
    assert [(row.step_number, row.event_type, row.span_id) for row in session.added] == [
        (3, "tool_call", None),
        (4, "agent_execution", "span-3"),
        (5, "model_call", "span-4"),
    ]


@pytest.mark.asyncio
async def test_log_transaction_propagates_history_persistence_failure():
    """落库结果必须向上返回，让 finalize 能标记本轮历史是否真的写进去了。"""
    broken_session = _FakeSession()

    with patch(
        "app.services.ai.audit.AsyncSessionLocal", _SessionFactory([broken_session])
    ), patch.object(
        AuditManager, "save_history", new=AsyncMock(return_value=False)
    ) as save_history:
        persisted = await AuditManager.log_transaction(
            trace_id="trace-history-text-2",
            agent_config=type(
                "AgentCfg",
                (),
                {"model_name": "m", "agent_version": "v1", "agent_id": "agent-1"},
            )(),
            user_query="问题",
            response_content="回答",
            user_info={"user_id": 7},
            status="success",
            duration=12.0,
            trace_buffer=[],
        )

    save_history.assert_awaited_once()
    assert persisted is False


class _FakePerformanceTracker:
    def __init__(self):
        self.marked: list[str] = []

    def mark(self, name: str) -> None:
        self.marked.append(name)

    def snapshot(self, **_kwargs):
        return {"stub": True}


@pytest.mark.asyncio
async def test_finalize_step_surfaces_history_persist_failure():
    """历史没写进去时，本轮不能安静地按「已完成」收尾。

    ``run_status`` 在此之前已经发出（persisting=True），因此失败必须由独立事件 +
    ``execution_performance.audit_persisted`` 标记出来，运维/前端才有据可查。
    """
    from app.services.ai.pipeline.context import PipelineContext
    from app.services.ai.pipeline.steps.finalize_step import FinalizeStep

    context = PipelineContext(
        messages=[{"role": "user", "content": "你好"}],
        user_info={"user_id": 123},
    )
    context.trace_id = "trace-history-text-3"
    context.user_query = "你好"
    context.full_response_content = "回答"
    context.performance_tracker = _FakePerformanceTracker()

    with patch(
        "app.services.ai.agent_service._should_persist_turn_history",
        return_value=True,
    ), patch(
        "app.services.ai.agent_service._persist_assistant_message_and_summary",
        new_callable=AsyncMock,
    ), patch(
        "app.services.ai.agent_service.AuditManager.log_transaction",
        new_callable=AsyncMock,
        return_value=False,
    ):
        chunks = [chunk async for chunk in FinalizeStep().run(context)]

    failure_events = [
        chunk for chunk in chunks if chunk.get("type") == "history_persist_failed"
    ]
    assert failure_events, "落库失败必须发出可识别事件"
    assert failure_events[0]["trace_id"] == "trace-history-text-3"
    assert (
        context.shared_state["execution_performance"]["audit_persisted"] is False
    )


@pytest.mark.asyncio
async def test_finalize_step_marks_audit_persisted_on_success():
    from app.services.ai.pipeline.context import PipelineContext
    from app.services.ai.pipeline.steps.finalize_step import FinalizeStep

    context = PipelineContext(
        messages=[{"role": "user", "content": "你好"}],
        user_info={"user_id": 123},
    )
    context.trace_id = "trace-history-text-4"
    context.user_query = "你好"
    context.full_response_content = "回答"
    context.performance_tracker = _FakePerformanceTracker()

    with patch(
        "app.services.ai.agent_service._should_persist_turn_history",
        return_value=True,
    ), patch(
        "app.services.ai.agent_service._persist_assistant_message_and_summary",
        new_callable=AsyncMock,
    ), patch(
        "app.services.ai.agent_service.AuditManager.log_transaction",
        new_callable=AsyncMock,
        return_value=True,
    ):
        chunks = [chunk async for chunk in FinalizeStep().run(context)]

    assert not [
        chunk for chunk in chunks if chunk.get("type") == "history_persist_failed"
    ]
    assert context.shared_state["execution_performance"]["audit_persisted"] is True
