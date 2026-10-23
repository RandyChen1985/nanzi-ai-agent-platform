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


class _FakeSession:
    """最小 AsyncSession 替身：只覆盖 save_history 会用到的方法。"""

    def __init__(self, commit_error: BaseException | None = None):
        self.added: list = []
        self._commit_error = commit_error
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_calls += 1
        if self._commit_error is not None:
            raise self._commit_error

    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("fake session 不支持查询")


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
