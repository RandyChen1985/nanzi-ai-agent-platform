"""AI 聊天全链路关键 Bug 修复回归测试（无基础设施依赖）。"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.chat import (
    TruncateHistoryRequest,
    _audit_query_candidates,
    truncate_history_endpoint,
)
from app.schemas.agent import ChatConfig
from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner
from app.services.ai.runtime.agentscope import resume as resume_module
from app.services.ai.runtime.agentscope.confirmations import (
    PendingAgentScopeConfirmation,
    pending_agentscope_confirmations,
)
from app.services.ai.runtime.agentscope.pending_store import (
    PendingAgentScopeSnapshot,
    PendingAgentScopeStore,
)
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.agent_service import AgentService

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _make_snapshot(
    *,
    request_id: str = "req-reg-1",
    kind: str = "permission",
    user_id: str = "u1",
    conversation_id: str = "conv-1",
) -> PendingAgentScopeSnapshot:
    return PendingAgentScopeSnapshot(
        request_id=request_id,
        kind=kind,
        user_id=user_id,
        conversation_id=conversation_id,
        trace_id="trace-reg-1",
        reply_id="reply-reg-1",
        agent_name="GeneralAgent",
        tool_call={"id": "call_1", "name": "bash", "input": "{}"},
        agent_state={},
        stream_state={},
        runner_context={"runner_type": "general", "config": {}},
    )


def _kb_config() -> ChatConfig:
    return ChatConfig(
        agent_id="sys-agent-kb",
        agent_name="knowledge-base",
        agent_version=None,
        model_name="qwen",
        temperature=0.1,
        system_prompt="You are a knowledge assistant.",
        tools=["search_knowledge_base"],
    )


def _search_tool() -> RuntimeToolSpec:
    async def _fake(**kwargs):
        return "{}"

    return RuntimeToolSpec(
        name="search_knowledge_base",
        description="Search KB",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=_fake,
        permission_scope="read",
    )


def test_audit_query_candidates_includes_mention_and_stripped_text():
    candidates = _audit_query_candidates("@数据分析助手 帮我查一下本月销量")
    assert "@数据分析助手 帮我查一下本月销量" in candidates
    assert "帮我查一下本月销量" in candidates
    assert _audit_query_candidates("普通问题") == ["普通问题"]


@pytest.mark.asyncio
async def test_pending_store_clear_conversation_isolates_user_and_conversation():
    store = PendingAgentScopeStore()
    with patch("app.core.redis.get_redis", AsyncMock(return_value=None)):
        await store.register(_make_snapshot(request_id="r-u1-c1", user_id="u1", conversation_id="c1"))
        await store.register(_make_snapshot(request_id="r-u1-c2", user_id="u1", conversation_id="c2"))
        await store.register(_make_snapshot(request_id="r-u2-c1", user_id="u2", conversation_id="c1"))

        await store.clear_conversation("u1", "c1")

        assert await store.peek("r-u1-c1", user_id="u1") is None
        assert await store.peek("r-u1-c2", user_id="u1") is not None
        assert await store.peek("r-u2-c1", user_id="u2") is not None


@pytest.mark.asyncio
async def test_resume_skips_side_effects_when_consumed_before_lock(monkeypatch):
    """拿锁前 snapshot 在，拿锁后已被其他并发请求消费：不得触发 on_lane_acquired/授权/回执。"""
    events: list[str] = []
    snap = PendingAgentScopeConfirmation.from_snapshot(_make_snapshot(kind="permission"))

    peek_calls = 0

    async def _peek(*args, **kwargs):
        nonlocal peek_calls
        peek_calls += 1
        return snap if peek_calls == 1 else None

    @contextlib.asynccontextmanager
    async def _fake_hold(*args, **kwargs):
        events.append("hold_enter")
        yield
        events.append("hold_exit")

    @contextlib.asynccontextmanager
    async def _fake_track(*args, **kwargs):
        events.append("track_enter")
        yield None
        events.append("track_exit")

    async def _on_lane_acquired():
        events.append("on_lane_acquired")

    grant_mock = AsyncMock(return_value={"granted": True})
    monkeypatch.setattr(pending_agentscope_confirmations, "peek_async", _peek)
    monkeypatch.setattr(resume_module.conversation_run_lane, "hold", _fake_hold)
    monkeypatch.setattr(resume_module, "track_conversation_run", _fake_track)
    monkeypatch.setattr(resume_module.AgentScopeResumeHandler, "_grant_session_permission", grant_mock)

    chunks = [
        chunk
        async for chunk in AgentService().resume_agentscope_permission_stream(
            permission_request_id="req-reg-1",
            confirmed=True,
            grant_scope="session",
            user_info={"user_id": "u1"},
            on_lane_acquired=_on_lane_acquired,
        )
    ]

    assert "on_lane_acquired" not in events
    grant_mock.assert_not_awaited()
    assert not any(c.get("type") == "permission_result" for c in chunks)
    assert any(c.get("type") == "error" for c in chunks)


@pytest.mark.asyncio
async def test_resume_holds_lane_around_track_and_finalizes_inside_lock(monkeypatch):
    """恢复流必须先拿锁再注册运行句柄，并把授权、消费、落库与审计全部收在锁内。"""
    events: list[str] = []
    snap = PendingAgentScopeConfirmation.from_snapshot(_make_snapshot(kind="permission"))

    class _FakeRunner:
        config = SimpleNamespace(model_name="m1", agent_name="GeneralAgent")
        conversation_id = "conv-1"
        trace_buffer = []

        async def resume_agentscope_native_confirmation(self, pending, *, confirmed=True):
            events.append("stream_chunk")
            yield {"type": "answer_delta", "content": "ok"}

    @contextlib.asynccontextmanager
    async def _fake_hold(*args, **kwargs):
        events.append("hold_enter")
        yield
        events.append("hold_exit")

    @contextlib.asynccontextmanager
    async def _fake_track(*args, **kwargs):
        events.append("track_enter")
        yield SimpleNamespace(cancelled=False)
        events.append("track_exit")

    async def _on_lane_acquired():
        events.append("on_lane_acquired")

    async def _grant(*args, **kwargs):
        events.append("grant_session")
        return {"granted": True, "tool_name": "bash", "conversation_id": "conv-1"}

    async def _peek(*args, **kwargs):
        return snap

    async def _pop(*args, **kwargs):
        events.append("pop")
        return snap

    async def _persist(**kwargs):
        events.append("persist")

    async def _audit(*args, **kwargs):
        events.append("audit")
        return True

    monkeypatch.setattr(resume_module.conversation_run_lane, "hold", _fake_hold)
    monkeypatch.setattr(resume_module, "track_conversation_run", _fake_track)
    monkeypatch.setattr(pending_agentscope_confirmations, "peek_async", _peek)
    monkeypatch.setattr(pending_agentscope_confirmations, "pop_async", _pop)
    monkeypatch.setattr(resume_module.AgentScopeResumeHandler, "_grant_session_permission", classmethod(_grant))
    monkeypatch.setattr(
        resume_module.AgentScopeResumeHandler,
        "build_agentscope_runner_from_pending",
        classmethod(lambda cls, pending, **kwargs: _FakeRunner()),
    )
    monkeypatch.setattr(
        resume_module.AgentScopeResumeHandler,
        "restore_runner_execution_context",
        classmethod(AsyncMock()),
    )
    monkeypatch.setattr("app.services.ai.agent_service._should_persist_turn_history", lambda *a, **k: True)
    monkeypatch.setattr("app.services.ai.agent_service._persist_assistant_message_and_summary", _persist)
    monkeypatch.setattr(resume_module.AuditManager, "log_transaction", _audit)

    chunks = [
        chunk
        async for chunk in AgentService().resume_agentscope_permission_stream(
            permission_request_id="req-reg-1",
            confirmed=True,
            grant_scope="session",
            user_info={"user_id": "u1"},
            on_lane_acquired=_on_lane_acquired,
        )
    ]

    assert any(c.get("type") == "permission_result" for c in chunks)
    expected_order = [
        "hold_enter",
        "track_enter",
        "on_lane_acquired",
        "grant_session",
        "stream_chunk",
        "pop",
        "persist",
        "audit",
        "track_exit",
        "hold_exit",
    ]
    indices = [events.index(step) for step in expected_order]
    assert indices == sorted(indices), f"Unexpected order: {events}"


@pytest.mark.asyncio
async def test_knowledge_runner_interrupt_bypasses_reflection_and_isolates_thinking():
    """KnowledgeAgentRunner：thinking/interrupt 实时透传且不被缓冲误改写，触发中断后立即停止不评估不保存 State。"""
    runner = KnowledgeAgentRunner(
        config=_kb_config(),
        trace_id="test-kb-int",
        trace_buffer=[],
        user_info={"role": "admin", "user_id": "1"},
        execution_options={"grounding_enabled": True, "hallucination_check": True},
    )
    runner._rag_empty = False
    flushed_calls: list[str] = []

    async def mock_execute_agentscope(*args, **kwargs):
        assert runner._defer_native_state_save is True
        runner._pending_native_state_save = {"attempt": "interrupted"}
        yield {"type": "reasoning_content", "content": "思考过程不应被当成正文"}
        yield {"type": "answer_delta", "content": "正在分析"}
        yield {
            "type": "user_question",
            "question_id": "q-1",
            "title": "请选择范围",
            "questions": [],
        }

    async def mock_auto_prefetch(*args, **kwargs):
        yield {"__knowledge_output__": json.dumps({"content": "参考资料", "citations": []})}

    async def fake_flush():
        flushed_calls.append("flush")

    evaluate = AsyncMock(return_value={"is_hallucinated": True, "reason": "should not run"})
    with patch.object(runner, "_execute_with_agentscope_native_agent", mock_execute_agentscope), \
         patch.object(runner, "_resolve_knowledge_tools", AsyncMock(return_value=[_search_tool()])), \
         patch.object(runner, "_auto_invoke_search_knowledge_base", mock_auto_prefetch), \
         patch.object(runner, "_flush_pending_native_state_save", fake_flush), \
         patch("app.services.ai.hallucination_evaluator.HallucinationEvaluator.evaluate", evaluate), \
         patch("app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled", AsyncMock(return_value=True)), \
         patch("app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids", AsyncMock(return_value=(["ds-1"], None))), \
         patch("app.services.ai.runners.knowledge_agent_runner.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=SimpleNamespace(native_model=object()))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = [
            event
            async for event in runner._execute_raw([{"role": "user", "content": "问题"}])
        ]

    evaluate.assert_not_awaited()
    assert flushed_calls == []
    assert runner._defer_native_state_save is False
    types = [e.get("type") for e in events]
    assert "reasoning_content" in types
    assert "user_question" in types
    assert types.index("answer_delta") < types.index("user_question")


@pytest.mark.asyncio
async def test_knowledge_runner_flushes_state_only_for_accepted_retry():
    """第一轮幻觉重写时不落地中间脏 State，仅最终轮采纳后 flush 一次。"""
    runner = KnowledgeAgentRunner(
        config=_kb_config(),
        trace_id="test-kb-retry",
        trace_buffer=[],
        user_info={"role": "admin", "user_id": "1"},
        execution_options={"grounding_enabled": True, "hallucination_check": True},
    )
    runner._rag_empty = False
    attempt = 0
    saved_payloads: list[dict] = []

    async def mock_execute_agentscope(*args, **kwargs):
        nonlocal attempt
        attempt += 1
        runner._pending_native_state_save = {"attempt": attempt}
        if attempt == 1:
            yield {"type": "answer_delta", "content": "无依据的幻觉回答"}
        else:
            yield {"type": "answer_delta", "content": "依据事实文献回答。[ID:1]"}

    async def mock_auto_prefetch(*args, **kwargs):
        yield {
            "__knowledge_output__": json.dumps(
                {"content": "事实文献内容", "citations": [{"id": 1, "content": "事实文献内容"}]}
            )
        }

    async def fake_flush():
        if runner._pending_native_state_save is not None:
            saved_payloads.append(runner._pending_native_state_save)
            runner._pending_native_state_save = None

    evaluate = AsyncMock(
        side_effect=[
            {"is_hallucinated": True, "reason": "缺少依据"},
            {"is_hallucinated": False, "reason": "ok"},
        ]
    )
    with patch.object(runner, "_execute_with_agentscope_native_agent", mock_execute_agentscope), \
         patch.object(runner, "_resolve_knowledge_tools", AsyncMock(return_value=[_search_tool()])), \
         patch.object(runner, "_auto_invoke_search_knowledge_base", mock_auto_prefetch), \
         patch.object(runner, "_flush_pending_native_state_save", fake_flush), \
         patch("app.services.ai.hallucination_evaluator.HallucinationEvaluator.evaluate", evaluate), \
         patch("app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled", AsyncMock(return_value=True)), \
         patch("app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids", AsyncMock(return_value=(["ds-1"], None))), \
         patch("app.services.ai.runners.knowledge_agent_runner.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=SimpleNamespace(native_model=object()))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = [
            event
            async for event in runner._execute_raw([{"role": "user", "content": "问题"}])
        ]

    assert attempt == 2
    assert saved_payloads == [{"attempt": 2}]
    full_text = "".join(str(e.get("content") or "") for e in events if e.get("type") == "answer_delta")
    assert "依据事实文献回答" in full_text
    assert "无依据的幻觉回答" not in full_text


@pytest.mark.asyncio
async def test_truncate_history_rejects_locked_conversation():
    fake_user = {"user_id": 1, "user_name": "test", "role": "user"}
    with patch(
        "app.services.ai.runtime.session_run_lane.conversation_run_lane.is_locked",
        AsyncMock(return_value=True),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await truncate_history_endpoint(
                TruncateHistoryRequest(conversation_id="conv-locked", keep_count=2),
                request=MagicMock(),
                user_info=fake_user,
                db=MagicMock(),
            )
    assert exc_info.value.status_code == 409


def test_frontend_stream_bugfix_contracts():
    embed_src = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    assert "let receivedDuplicateRequest = false;" in embed_src
    assert "if (!receivedDuplicateRequest) {" in embed_src
    assert embed_src.index("if (!receivedDuplicateRequest) {") < embed_src.index("if (conversationId.value === streamConversationId) {")
    assert "signal: resumeAbort.signal" in embed_src

    debug_src = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")
    stream_loop = debug_src[debug_src.index("const sendMessageInternal = async"):debug_src.index("const submitPendingExternalExecution")]
    assert stream_loop.index('else if (data.type === "error" || data.status === "error")') < stream_loop.index('else if (data.type === "answer_delta" || data.content)')
    assert "signal: resumeAbort.signal" in debug_src
