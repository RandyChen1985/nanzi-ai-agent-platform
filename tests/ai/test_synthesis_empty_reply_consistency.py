"""回归测试：轨迹 SYNTHESIS 与落库 summary / 对话回复一致性。

覆盖 4 类真实缺陷与安全边界：
1. ChatBI native_turn：临时正文被 retraction 撤回后，轨迹不得残留被撤回的 SYNTHESIS；
   修复轮无正文时，有有效缓存 SQL 优先走缓存合成（不误判 error），无缓存时 emit_final_guard
   不得因 ready_to_answer=True 静默跳过；正常定稿时在 _finalize_content_and_persist 落一条完整 SYNTHESIS。
2. AssistantAgentRunner（strict_buffer）：
   - 主助手自身挂起（external_execution_required / 自身 permission_required）：严禁提前释放
     chunks_buffer，且同步清理未下发给用户的 0-token synthesis 轨迹（承载独立 token 的步骤保留计量但清空正文）。
   - 子代理旁路挂起（permission_required 带 subagent）：主助手生成的引导正文需继续走 grounding 审核
     并正常输出（不静默丢失），若被 grounding 降级替换则同步更新 synthesis 轨迹；主助手无正文时不误吐降级说明。
3. AgentScopeResumeHandler（resume.py）：
   - 仅当 execution_status == "success" 且合并前序后 merged_response_content 仍为空时触发空回复兜底，
     并在 run_status 之前下发正文；
   - rejected、二次挂起、以及前半段已有正文时严禁误触发兜底。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
from app.services.ai.runners.chatbi import native_turn as chatbi_native_turn
from app.services.ai.runners.chatbi import react_stream as chatbi_react_stream
from app.services.ai.runners.data_agent_runner import DataAgentRunner
from app.services.ai.runtime.agentscope import resume as resume_module
from app.services.ai.runtime.agentscope.compat import HumanMessage
from app.services.ai.runtime.agentscope.confirmations import pending_agentscope_confirmations
from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state
from app.services.ai.runtime.agentscope.resume import AgentScopeResumeHandler

pytestmark = pytest.mark.no_infrastructure


@dataclass
class _FakeTextDeltaEvent:
    type: str = "TEXT_BLOCK_DELTA"
    block_id: str = "blk_1"
    delta: str = ""


@dataclass
class _FakeSimpleEvent:
    type: str
    block_id: str = "blk_1"


def _make_config(agent_id: str, agent_name: str, display_name: str) -> ChatConfig:
    return ChatConfig(
        agent_id=agent_id,
        agent_name=agent_name,
        agent_display_name=display_name,
        agent_version="v1",
        model_name="mock-model",
        temperature=0.0,
        tools=[],
        system_prompt="test prompt",
    )


# ---------------------------------------------------------------------------
# 1. ChatBI: 延后写 SYNTHESIS + 撤回后优先缓存合成 + emit_final_guard 防静默跳过
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatbi_retract_before_interrupt_leaves_no_stale_synthesis_trace():
    """首轮临时正文被 retraction 撤回且修复轮挂起时，trace_buffer 不得残留被撤回的 SYNTHESIS。"""
    trace_buffer: List[AgentExecutionStep] = []
    runner = DataAgentRunner(
        config=_make_config("agent-bi", "chatbi", "数据智能体"),
        trace_id="trace-chatbi-retract-interrupt",
        trace_buffer=trace_buffer,
        user_info={"user_id": 1001, "user_name": "tester"},
        conversation_id="conv-chatbi-1",
    )
    native_model = SimpleNamespace(model="mock-model")
    call_count = 0

    class _FakeAgent:
        def __init__(self):
            self.model = native_model
            self.state = {}

        def reply_stream(self, _inputs):
            nonlocal call_count
            call_count += 1
            current = call_count

            async def _gen():
                if current == 1:
                    yield _FakeSimpleEvent(type="TEXT_BLOCK_START")
                    yield _FakeTextDeltaEvent(
                        delta="抱歉，这次没能取到数据，让我进一步按明细维度查询后再为您汇总。"
                    )
                    yield _FakeSimpleEvent(type="TEXT_BLOCK_END")

            return _gen()

    runner._ensure_schema_similarity_threshold = AsyncMock(return_value=None)
    runner._is_sql_plan_enabled = lambda: False
    runner._wrap_tools_with_schema_gate = lambda tools, state: tools
    runner._build_native_agent = AsyncMock(return_value=_FakeAgent())
    runner._resolve_initial_tool_choice = lambda state: None

    orig_stream = chatbi_react_stream.stream_agentscope_events
    stream_round = 0

    async def _patched_stream(**kwargs):
        nonlocal stream_round
        stream_round += 1
        st = kwargs["state"]
        if stream_round == 1:
            st.schema_completed = True
            st.sql_completed = True
            st.last_successful_sql_output = '{"rows": [{"a": 1}]}'
            st.last_successful_nonempty_sql_at = 1
            async for ch in orig_stream(runner, **kwargs):
                yield ch
            return
        yield {
            "type": "permission_required",
            "permission_request_id": "perm_chatbi_1",
            "tool_name": "execute_sql_query",
        }

    with (
        patch.object(runner, "_stream_agentscope_events", side_effect=_patched_stream),
        patch("app.services.ai.runners.chatbi.native_turn._persist_agent_state", new=AsyncMock()),
    ):
        chunks = [
            ch
            async for ch in chatbi_native_turn.run_native_agent_turn(
                runner,
                native_model=native_model,
                tools=[],
                tools_fingerprint="fp",
                model_name="mock-model",
                agent_name="chatbi",
                system_content="sys",
                max_steps=5,
                llm_handle=SimpleNamespace(model_name="mock-model"),
                runtime_messages=[HumanMessage(content="查询销售明细")],
                user_question="查询销售明细",
                turn_cls=SimpleNamespace(requires_fresh_data=True, requires_sql_query=True),
            )
        ]

    assert any(ch.get("type") == "retraction" and ch.get("content") == "" for ch in chunks)
    assert any(ch.get("type") == "permission_required" for ch in chunks)
    assert [s for s in trace_buffer if s.event_type == "synthesis"] == []


@pytest.mark.asyncio
async def test_chatbi_retract_with_cached_sql_prefers_cache_synthesis_over_error_guard():
    """撤回后修复轮未吐字但有有效缓存 SQL 时，优先走缓存合成（不误标为 error）。"""
    trace_buffer: List[AgentExecutionStep] = []
    runner = DataAgentRunner(
        config=_make_config("agent-bi", "chatbi", "数据智能体"),
        trace_id="trace-chatbi-cache-fallback",
        trace_buffer=trace_buffer,
        user_info={"user_id": 1001, "user_name": "tester"},
        conversation_id="conv-chatbi-2",
    )
    native_model = SimpleNamespace(model="mock-model")
    call_count = 0

    class _FakeAgent:
        def __init__(self):
            self.model = native_model
            self.state = {}

        def reply_stream(self, _inputs):
            nonlocal call_count
            call_count += 1
            current = call_count

            async def _gen():
                if current == 1:
                    yield _FakeSimpleEvent(type="TEXT_BLOCK_START")
                    yield _FakeTextDeltaEvent(
                        delta="当前粒度较粗，让我进一步按区域细分统计后再为您汇总。"
                    )
                    yield _FakeSimpleEvent(type="TEXT_BLOCK_END")
                else:
                    if False:
                        yield _FakeSimpleEvent(type="MODEL_CALL_END")

            return _gen()

    runner._ensure_schema_similarity_threshold = AsyncMock(return_value=None)
    runner._is_sql_plan_enabled = lambda: False
    runner._wrap_tools_with_schema_gate = lambda tools, state: tools
    runner._build_native_agent = AsyncMock(return_value=_FakeAgent())
    runner._resolve_initial_tool_choice = lambda state: None

    orig_stream = chatbi_react_stream.stream_agentscope_events
    stream_round = 0

    async def _patched_stream(**kwargs):
        nonlocal stream_round
        stream_round += 1
        st = kwargs["state"]
        if stream_round == 1:
            st.schema_completed = True
            st.sql_completed = True
            st.last_successful_sql_output = '{"rows": [{"region": "华东", "amount": 100}]}'
            st.last_successful_nonempty_sql_at = 1
        async for ch in orig_stream(runner, **kwargs):
            yield ch

    async def _fake_cache_synthesis(**_kwargs):
        runner._increment_step()
        trace_buffer.append(
            AgentExecutionStep(
                step_number=runner.step_counter,
                event_type="synthesis",
                agent_name="chatbi",
                tool_output={"content": "基于已查结果的汇总：华东 100。", "reused_sql_without_followup_content": True},
            )
        )
        yield {"content": "基于已查结果的汇总：华东 100。"}

    with (
        patch.object(runner, "_stream_agentscope_events", side_effect=_patched_stream),
        patch.object(runner, "_synthesize_from_cached_sql_result", side_effect=_fake_cache_synthesis),
        patch("app.services.ai.runners.chatbi.native_turn._persist_agent_state", new=AsyncMock()),
    ):
        chunks = [
            ch
            async for ch in chatbi_native_turn.run_native_agent_turn(
                runner,
                native_model=native_model,
                tools=[],
                tools_fingerprint="fp",
                model_name="mock-model",
                agent_name="chatbi",
                system_content="sys",
                max_steps=5,
                llm_handle=SimpleNamespace(model_name="mock-model"),
                runtime_messages=[HumanMessage(content="查询各区域销售额")],
                user_question="查询各区域销售额",
                turn_cls=SimpleNamespace(requires_fresh_data=True, requires_sql_query=True),
            )
        ]

    assert any(ch.get("content") == "基于已查结果的汇总：华东 100。" for ch in chunks)
    assert not any(ch.get("status") == "error" for ch in chunks)
    synth_steps = [s for s in trace_buffer if s.event_type == "synthesis"]
    assert len(synth_steps) == 1
    assert synth_steps[0].tool_output["content"] == "基于已查结果的汇总：华东 100。"


@pytest.mark.asyncio
async def test_chatbi_emit_final_guard_does_not_skip_when_provisional_retracted():
    """曾撤回临时正文且最终无正文时，即使 ready_to_answer=True，emit_final_guard 也不得静默返回。"""
    runner = DataAgentRunner(
        config=_make_config("agent-bi", "chatbi", "数据智能体"),
        trace_id="trace-chatbi-retracted-guard",
        trace_buffer=[],
    )
    state = chatbi_react_stream.DataRunState(
        requires_fresh_data=False,  # ready_to_answer == True
        full_content="",
        provisional_content_retracted=True,
    )
    assert state.ready_to_answer is True

    chunks = [ch async for ch in chatbi_react_stream.emit_final_guard(runner, state)]
    assert any(ch.get("content") and ch.get("status") == "error" for ch in chunks)


@pytest.mark.asyncio
async def test_chatbi_tool_loop_fuse_does_not_mask_with_cached_sql_fallback():
    """工具循环熔断时即使存在旧的缓存 SQL，也应交由 emit_final_guard 输出熔断提示而非静默复用旧结果。"""
    trace_buffer: List[AgentExecutionStep] = []
    runner = DataAgentRunner(
        config=_make_config("agent-bi", "chatbi", "数据智能体"),
        trace_id="trace-chatbi-loop-fuse-no-mask",
        trace_buffer=trace_buffer,
        user_info={"user_id": 1001, "user_name": "tester"},
        conversation_id="conv-chatbi-fuse",
    )
    native_model = SimpleNamespace(model="mock-model")

    class _FakeAgent:
        def __init__(self):
            self.model = native_model
            self.state = {}

        def reply_stream(self, _inputs):
            async def _gen():
                if False:
                    yield _FakeSimpleEvent(type="MODEL_CALL_END")

            return _gen()

    runner._ensure_schema_similarity_threshold = AsyncMock(return_value=None)
    runner._is_sql_plan_enabled = lambda: False
    runner._wrap_tools_with_schema_gate = lambda tools, state: tools
    runner._build_native_agent = AsyncMock(return_value=_FakeAgent())
    runner._resolve_initial_tool_choice = lambda state: None

    async def _patched_stream(**kwargs):
        st = kwargs["state"]
        st.schema_completed = True
        st.last_successful_sql_output = '{"rows": [{"old": 1}]}'
        st.tool_loop_fuse_triggered = True
        st.tool_loop_fuse_reason = "重复调用相同工具参数"
        st.repair_attempts["tool_loop_fuse"] = 99
        if False:
            yield {}

    cache_synth_mock = AsyncMock()
    with (
        patch.object(runner, "_stream_agentscope_events", side_effect=_patched_stream),
        patch.object(runner, "_synthesize_from_cached_sql_result", side_effect=cache_synth_mock),
        patch("app.services.ai.runners.chatbi.native_turn._persist_agent_state", new=AsyncMock()),
    ):
        chunks = [
            ch
            async for ch in chatbi_native_turn.run_native_agent_turn(
                runner,
                native_model=native_model,
                tools=[],
                tools_fingerprint="fp",
                model_name="mock-model",
                agent_name="chatbi",
                system_content="sys",
                max_steps=5,
                llm_handle=SimpleNamespace(model_name="mock-model"),
                runtime_messages=[HumanMessage(content="查询销售明细")],
                user_question="查询销售明细",
                turn_cls=SimpleNamespace(requires_fresh_data=True, requires_sql_query=True),
            )
        ]

    cache_synth_mock.assert_not_called()
    assert any("工具调用出现循环" in str(ch.get("content") or "") for ch in chunks)


@pytest.mark.asyncio
async def test_chatbi_normal_finalize_records_single_final_synthesis_trace():
    """正常定稿出口（_finalize_content_and_persist）应记录唯一一条与展示正文一致的 SYNTHESIS。"""
    trace_buffer: List[AgentExecutionStep] = []
    runner = DataAgentRunner(
        config=_make_config("agent-bi", "chatbi", "数据智能体"),
        trace_id="trace-chatbi-normal-finalize",
        trace_buffer=trace_buffer,
        user_info={"user_id": 1001, "user_name": "tester"},
        conversation_id="conv-chatbi-3",
    )
    native_model = SimpleNamespace(model="mock-model")

    class _FakeAgent:
        def __init__(self):
            self.model = native_model
            self.state = {}

        def reply_stream(self, _inputs):
            async def _gen():
                yield _FakeSimpleEvent(type="TEXT_BLOCK_START")
                yield _FakeTextDeltaEvent(delta="本月总销售额为 120 万元。")
                yield _FakeSimpleEvent(type="TEXT_BLOCK_END")

            return _gen()

    runner._ensure_schema_similarity_threshold = AsyncMock(return_value=None)
    runner._is_sql_plan_enabled = lambda: False
    runner._wrap_tools_with_schema_gate = lambda tools, state: tools
    runner._build_native_agent = AsyncMock(return_value=_FakeAgent())
    runner._resolve_initial_tool_choice = lambda state: None

    orig_stream = chatbi_react_stream.stream_agentscope_events

    async def _patched_stream(**kwargs):
        st = kwargs["state"]
        st.schema_completed = True
        st.sql_completed = True
        st.last_successful_sql_output = '{"rows": [{"total": 120}]}'
        st.last_successful_nonempty_sql_at = 1
        async for ch in orig_stream(runner, **kwargs):
            yield ch

    with (
        patch.object(runner, "_stream_agentscope_events", side_effect=_patched_stream),
        patch("app.services.ai.runners.chatbi.native_turn._persist_agent_state", new=AsyncMock()),
    ):
        chunks = [
            ch
            async for ch in chatbi_native_turn.run_native_agent_turn(
                runner,
                native_model=native_model,
                tools=[],
                tools_fingerprint="fp",
                model_name="mock-model",
                agent_name="chatbi",
                system_content="sys",
                max_steps=5,
                llm_handle=SimpleNamespace(model_name="mock-model"),
                runtime_messages=[HumanMessage(content="本月总销售额是多少")],
                user_question="本月总销售额是多少",
                turn_cls=SimpleNamespace(requires_fresh_data=True, requires_sql_query=True),
            )
        ]

    assert any(ch.get("content") == "本月总销售额为 120 万元。" for ch in chunks)
    synth_steps = [s for s in trace_buffer if s.event_type == "synthesis"]
    assert len(synth_steps) == 1
    assert synth_steps[0].tool_output == {"content": "本月总销售额为 120 万元。"}


# ---------------------------------------------------------------------------
# 2. AssistantAgentRunner: 自身挂起清理未下发 SYNTHESIS vs 子代理旁路挂起放行并走 grounding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assistant_self_interrupt_drops_buffer_and_prunes_unemitted_synthesis():
    """主助手自身挂起时：不释放缓冲正文，且清理未下发的 0-token synthesis 轨迹（保留含 token 计量）。"""
    trace_buffer: List[AgentExecutionStep] = []
    runner = AssistantAgentRunner(
        config=_make_config("agent-main", "main", "主助手"),
        trace_id="trace-self-interrupt",
        trace_buffer=trace_buffer,
        execution_options={"grounding_enabled": True},
        user_info={"user_id": 1001, "user_name": "tester"},
        conversation_id="conv-main-1",
    )

    async def _fake_core(_history):
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["external_tool"],
            "grounding_block_mode": "strict_buffer",
        }
        yield {
            "type": "external_execution_required",
            "status": "pending",
            "tool_call": {"name": "mcp_get_tickets"},
        }
        # 模拟内层写了一条 0-token synthesis 与一条带 token 的 synthesis_fallback
        trace_buffer.append(
            AgentExecutionStep(
                step_number=10,
                event_type="synthesis",
                agent_name="main",
                tool_output={"content": "未经核实的车次 G1 票价 100 元"},
            )
        )
        trace_buffer.append(
            AgentExecutionStep(
                step_number=11,
                event_type="synthesis",
                agent_name="main",
                tool_name="synthesis_fallback",
                tool_output={"content": "同样未下发的兜底正文"},
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            )
        )
        yield {
            "type": "answer_delta",
            "content": "未经核实的车次 G1 票价 100 元",
            "phase": "synthesis",
        }

    with patch.object(runner, "_execute_core", _fake_core):
        events = [ev async for ev in runner.execute([{"role": "user", "content": "查明天高铁票"}])]

    # 仍遵守反幻觉安全契约：绝不提前放出缓冲正文
    assert not any(ev.get("type") == "answer_delta" for ev in events)
    # 0-token synthesis 被移除；带 token 的 synthesis 保留计量但清除未下发正文
    synth_steps = [s for s in trace_buffer if s.event_type == "synthesis"]
    assert len(synth_steps) == 1
    assert synth_steps[0].total_tokens == 120
    assert synth_steps[0].tool_output == {"content": "", "suppressed_by_interrupt": True}


@pytest.mark.asyncio
async def test_assistant_subagent_handoff_runs_grounding_and_syncs_synthesis_trace():
    """子代理旁路审批时：主助手已生成正文不丢弃，且经 grounding 降级后同步更新 SYNTHESIS 轨迹。"""
    trace_buffer: List[AgentExecutionStep] = []
    runner = AssistantAgentRunner(
        config=_make_config("agent-main", "main", "主助手"),
        trace_id="trace-subagent-handoff",
        trace_buffer=trace_buffer,
        execution_options={"grounding_enabled": True},
        user_info={"user_id": 1001, "user_name": "tester"},
        conversation_id="conv-main-2",
    )
    runner.step_counter = 16

    async def _fake_core(_history):
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["internal_data"],
            "grounding_block_mode": "strict_buffer",
        }
        from app.core.context import get_current_agent_context

        ctx = get_current_agent_context()
        assert ctx is not None and ctx.event_queue is not None
        await ctx.event_queue.put(
            {
                "type": "permission_required",
                "permission_request_id": "perm_sub_01",
                "tool_name": "execute_sql_query",
                "source_agent_display_name": "数据智能体",
                "subagent": {"run_id": "subrun_1", "target_agent_name": "data-agent"},
            }
        )
        await asyncio.sleep(0)

        state = new_native_stream_state(candidate_answer_enabled=False)
        native_model = SimpleNamespace(model="mock-model")

        async def _fake_native_stream():
            yield _FakeSimpleEvent(type="MODEL_CALL_START")
            yield _FakeTextDeltaEvent(delta="本月销售额为 999 万元，请先授权子智能体进一步核验。")
            yield _FakeSimpleEvent(type="MODEL_CALL_END")

        async for chunk in runner._stream_agentscope_native_events(
            event_stream=_fake_native_stream(),
            agent=None,
            tools=[],
            native_model=native_model,
            state=state,
        ):
            yield chunk

    with patch.object(runner, "_execute_core", _fake_core):
        events = [ev async for ev in runner.execute([{"role": "user", "content": "查本月销售额"}])]

    # 1. 不再出现「正文完全蒸发」：必须有下发给用户的正文（经 grounding 门禁降级为安全说明）
    answer_chunks = [
        ev for ev in events
        if ev.get("type") in {"answer_delta", "process_narration_promote", "retraction"}
        and str(ev.get("content") or "").strip()
    ]
    assert answer_chunks, "子代理旁路中断时不应把主助手已产出正文静默丢弃"
    emitted_text = "".join(str(ev.get("content") or "") for ev in answer_chunks)
    # 2. 仍受 grounding 严格保护：未取证的“999 万元”被替换为安全说明
    assert "999 万元" not in emitted_text
    # 3. 轨迹中的 SYNTHESIS 已同步为最终展示给用户的正文
    synth_steps = [s for s in trace_buffer if s.event_type == "synthesis"]
    assert len(synth_steps) == 1
    assert synth_steps[0].tool_output["content"] == emitted_text


@pytest.mark.asyncio
async def test_assistant_subagent_handoff_without_reply_does_not_fabricate_grounding_guidance():
    """子代理旁路挂起且主助手自身未产出正文时，不应误触发 grounding 降级文案。"""
    trace_buffer: List[AgentExecutionStep] = []
    runner = AssistantAgentRunner(
        config=_make_config("agent-main", "main", "主助手"),
        trace_id="trace-subagent-empty",
        trace_buffer=trace_buffer,
        execution_options={"grounding_enabled": True},
        user_info={"user_id": 1001, "user_name": "tester"},
        conversation_id="conv-main-3",
    )

    async def _fake_core(_history):
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["internal_data"],
            "grounding_block_mode": "strict_buffer",
        }
        from app.core.context import get_current_agent_context

        ctx = get_current_agent_context()
        assert ctx is not None and ctx.event_queue is not None
        await ctx.event_queue.put(
            {
                "type": "permission_required",
                "permission_request_id": "perm_sub_02",
                "tool_name": "execute_sql_query",
                "source_agent_display_name": "数据智能体",
                "subagent": {"run_id": "subrun_2", "target_agent_name": "data-agent"},
            }
        )
        await asyncio.sleep(0)

    with patch.object(runner, "_execute_core", _fake_core):
        events = [ev async for ev in runner.execute([{"role": "user", "content": "查本月销售额"}])]

    assert not any(ev.get("grounding_downgraded") is True for ev in events)
    assert not any(ev.get("type") == "answer_delta" for ev in events)


# ---------------------------------------------------------------------------
# 3. resume.py: 恢复流空回复兜底边界校验
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_cm(*_args, **_kwargs):
    yield SimpleNamespace(cancelled=False)


async def _run_resume_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    confirmed: bool,
    resume_chunks: List[Dict[str, Any]],
    prior_merged_content: str,
    fallback_text: str | None = "抱歉，本次我没有生成有效回复。",
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    snapshot = SimpleNamespace(
        kind="permission",
        conversation_id="conv-resume-1",
        delegation=None,
        tool_call={"name": "bash", "input": "{}"},
    )
    pending = SimpleNamespace(
        trace_id="trace-resume-empty-1",
        user_id="1001",
        snapshot=snapshot,
        runner=SimpleNamespace(conversation_id="conv-resume-1"),
        tool_call=SimpleNamespace(id="tc-1"),
        state={"user_query": "执行检查"},
    )

    class _FakeRunner:
        conversation_id = "conv-resume-1"
        config = _make_config("agent-1", "assistant", "助手")
        trace_buffer: List[AgentExecutionStep] = []

        async def resume_agentscope_native_confirmation(self, *_a, **_kw):
            for item in resume_chunks:
                yield item

    captured_audit: Dict[str, Any] = {}

    async def _fake_log_transaction(
        trace_id, agent_config, user_query, response_content, user_info, status, duration, tb, **kwargs
    ):
        captured_audit.update(
            {"trace_id": trace_id, "summary": response_content, "status": status}
        )
        return True

    monkeypatch.setattr(resume_module.conversation_run_lane, "hold", _noop_cm)
    monkeypatch.setattr(resume_module, "track_conversation_run", _noop_cm)
    monkeypatch.setattr(
        pending_agentscope_confirmations, "peek_async", AsyncMock(return_value=pending)
    )
    monkeypatch.setattr(
        pending_agentscope_confirmations, "pop_async", AsyncMock(return_value=pending)
    )
    monkeypatch.setattr(
        AgentScopeResumeHandler,
        "build_agentscope_runner_from_pending",
        classmethod(lambda cls, p, **kw: _FakeRunner()),
    )
    monkeypatch.setattr(
        AgentScopeResumeHandler,
        "restore_runner_execution_context",
        classmethod(AsyncMock()),
    )
    monkeypatch.setattr(
        AgentScopeResumeHandler,
        "_merge_prior_turn_snapshot",
        classmethod(
            AsyncMock(return_value=([], None, prior_merged_content))
        ),
    )
    monkeypatch.setattr(
        "app.services.ai.agent_service._persist_assistant_message_and_summary",
        AsyncMock(),
    )
    monkeypatch.setattr(resume_module.AuditManager, "log_transaction", _fake_log_transaction)

    fake_service = SimpleNamespace(
        _quota_block_message=AsyncMock(return_value=None),
        _maybe_empty_response_fallback=AsyncMock(return_value=fallback_text),
    )
    chunks = [
        ch
        async for ch in AgentScopeResumeHandler.resume_permission_stream(
            fake_service,
            permission_request_id="req-empty-1",
            confirmed=confirmed,
            user_info={"user_id": "1001", "user_name": "tester"},
        )
    ]
    return chunks, captured_audit


@pytest.mark.asyncio
async def test_resume_applies_empty_fallback_when_success_and_merged_content_empty(
    monkeypatch: pytest.MonkeyPatch,
):
    """恢复轮 status=success 且合并后正文为空时，应在 run_status 前发出兜底文案并落库。"""
    chunks, audit = await _run_resume_case(
        monkeypatch,
        confirmed=True,
        resume_chunks=[],
        prior_merged_content="",
    )

    fallback_idx = next(
        i for i, c in enumerate(chunks) if c.get("content") == "抱歉，本次我没有生成有效回复。"
    )
    run_status_idx = next(i for i, c in enumerate(chunks) if c.get("type") == "run_status")
    assert fallback_idx < run_status_idx
    assert audit["status"] == "success"
    assert audit["summary"] == "抱歉，本次我没有生成有效回复。"


@pytest.mark.asyncio
async def test_resume_skips_empty_fallback_for_rejected_and_resuspended_and_nonempty_prior(
    monkeypatch: pytest.MonkeyPatch,
):
    """拒批、二次挂起、或挂起前已有正文时，严禁误触发空回复兜底。"""
    # 1) 用户拒绝授权 (status=rejected)
    chunks_rej, audit_rej = await _run_resume_case(
        monkeypatch,
        confirmed=False,
        resume_chunks=[],
        prior_merged_content="",
    )
    assert not any(c.get("content") == "抱歉，本次我没有生成有效回复。" for c in chunks_rej)
    assert audit_rej["status"] == "rejected"
    assert audit_rej["summary"] == ""

    # 2) 恢复流再次挂起 (status=awaiting_permission)
    chunks_again, audit_again = await _run_resume_case(
        monkeypatch,
        confirmed=True,
        resume_chunks=[{"type": "permission_required", "permission_request_id": "req-2"}],
        prior_merged_content="",
    )
    assert not any(c.get("content") == "抱歉，本次我没有生成有效回复。" for c in chunks_again)
    assert audit_again["status"] == "awaiting_permission"
    assert audit_again["summary"] == ""

    # 3) 挂起前半段已有正文（merged_response_content 非空）
    chunks_prior, audit_prior = await _run_resume_case(
        monkeypatch,
        confirmed=True,
        resume_chunks=[],
        prior_merged_content="前半段已经说明的结论。",
    )
    assert not any(c.get("content") == "抱歉，本次我没有生成有效回复。" for c in chunks_prior)
    assert audit_prior["status"] == "success"
    assert audit_prior["summary"] == "前半段已经说明的结论。"
