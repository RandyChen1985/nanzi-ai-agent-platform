import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_chat_completion_stream_wires_real_six_step_pipeline_and_tracker(monkeypatch):
    """生产入口必须构造真实六步骤并注入性能追踪器。"""
    import app.services.ai.agent_service as agent_service_module
    import app.services.ai.pipeline as pipeline_module
    from app.services.ai.agent_service import AgentService
    from app.services.ai.runtime.execution_observability import ExecutionPerformanceTracker

    captured = {}

    class CapturingRunner:
        def __init__(self, steps):
            captured["steps"] = [type(step).__name__ for step in steps]

        async def run(self, context):
            captured["context"] = context
            yield {"type": "run_status", "status": "success"}

    class DummyLane:
        async def is_locked(self, **kwargs):
            return False

        @asynccontextmanager
        async def hold(self, **kwargs):
            yield

    @asynccontextmanager
    async def tracked_run(*args, **kwargs):
        yield SimpleNamespace(cancelled=False)

    async def no_quota_events(self, context):
        if False:
            yield {}

    monkeypatch.setattr(pipeline_module, "PipelineRunner", CapturingRunner)
    monkeypatch.setattr(agent_service_module, "conversation_run_lane", DummyLane())
    monkeypatch.setattr(agent_service_module, "track_conversation_run", tracked_run)
    monkeypatch.setattr(
        "app.services.ai.pipeline.steps.preflight_step.PreflightStep.check_quota_and_queue",
        no_quota_events,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tool_timeout.load_agent_max_toolcall_timeout",
        AsyncMock(return_value=30.0),
    )

    events = [
        event
        async for event in AgentService().chat_completion_stream(
            [{"role": "user", "content": "你好"}],
            user_info={"user_id": "42"},
        )
    ]

    assert events == [{"type": "run_status", "status": "success"}]
    assert captured["steps"] == [
        "PreflightStep",
        "ContextStep",
        "RouteStep",
        "AssembleStep",
        "ExecutionStep",
        "FinalizeStep",
    ]
    assert isinstance(captured["context"].performance_tracker, ExecutionPerformanceTracker)
    assert captured["context"].shared_state["performance_tracker"] is captured["context"].performance_tracker


def test_execution_performance_tracker_records_stage_and_ttft():
    from app.services.ai.runtime.execution_observability import (
        ExecutionPerformanceTracker,
    )

    ticks = iter([10.0, 10.1, 10.25, 10.5, 10.6])
    tracker = ExecutionPerformanceTracker(clock=lambda: next(ticks))

    tracker.mark("route_resolution")
    tracker.observe_chunk({"type": "log", "content": "not an answer"})
    tracker.observe_chunk({"type": "answer_delta", "content": "第一段"})
    tracker.mark("executor_finish")

    result = tracker.snapshot(
        trace_buffer=[
            SimpleNamespace(event_type="thought"),
            SimpleNamespace(event_type="model_call"),
            SimpleNamespace(event_type="tool_call"),
        ],
        status="success",
    )

    assert result["stages_ms"] == {
        "route_resolution": 100.0,
        "executor_finish": 500.0,
    }
    assert result["ttft_ms"] == 250.0
    assert result["model_call_count"] == 2
    assert result["tool_call_count"] == 1
    assert result["status"] == "success"
    assert "第一段" not in result


def test_execution_performance_tracker_ignores_control_events_for_ttft():
    from app.services.ai.runtime.execution_observability import (
        ExecutionPerformanceTracker,
    )

    ticks = iter([2.0, 2.4, 2.4])
    tracker = ExecutionPerformanceTracker(clock=lambda: next(ticks))

    tracker.observe_chunk({"type": "reasoning_content", "content": "内部推理"})
    tracker.observe_chunk({"type": "log", "content": "正在处理"})
    tracker.observe_chunk({"content": "最终回答"})

    result = tracker.snapshot(trace_buffer=[], status="success")

    assert result["ttft_ms"] == 400.0
    assert result["total_elapsed_ms"] == 400.0


def test_default_main_execution_log_uses_delegation_language():
    from app.schemas.agent import ChatConfig
    from app.services.ai.agent_service import _build_turn_execution_log
    from app.services.ai.turn_decision import TurnDecision

    config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="main",
        model_name="configured-model",
        temperature=0.0,
        system_prompt="test",
        tools=[],
        capabilities=["general_chat"],
    )
    decision = TurnDecision.for_default_main_delegation(config)

    event = _build_turn_execution_log(
        decision,
        turn_display_label="通用助手",
        execution_time_ms=0.0,
    )

    assert event["title"] == "进入主专家自动委派"
    assert "主专家将直接回答或按任务需要自动委派其他智能体" in event["details"]
    assert "意图识别" not in event["title"]
    assert "意图识别" not in event["details"]


@pytest.mark.asyncio
async def test_agent_service_publishes_execution_performance_snapshot(monkeypatch):
    from app.schemas.agent import ChatConfig
    from app.services.ai.agent_service import AgentService
    from app.services.ai.config import RuntimeModelInfo

    config = ChatConfig(
        agent_id="agent-1",
        agent_name="main",
        model_name="configured-model",
        temperature=0.0,
        system_prompt="test",
        tools=[],
        capabilities=["general_chat"],
    )
    runtime_info = RuntimeModelInfo(
        configured_model="configured-model",
        effective_model_id="effective-model",
        source="test",
    )

    async def route_result():
        return config, None, 1.0, None

    route_task = asyncio.create_task(route_result())
    service = AgentService()

    async def fail_fixed_polling(*_args, **_kwargs):
        raise AssertionError("route resolution should not use fixed-interval polling")

    monkeypatch.setattr(asyncio, "wait_for", fail_fixed_polling)
    monkeypatch.setattr(
        service,
        "_start_route_resolution",
        lambda **_kwargs: route_task,
    )
    monkeypatch.setattr(
        "app.services.ai.session_mcp_tools.apply_session_mcp_tools_to_agent_config",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "_resolve_runtime_model_info_safe",
        AsyncMock(return_value=runtime_info),
    )
    monkeypatch.setattr(
        "app.services.ai.agent_service.looks_like_current_model_query",
        lambda _query: True,
    )
    audit = AsyncMock()
    monkeypatch.setattr(
        "app.services.ai.agent_service.AuditManager.log_transaction",
        audit,
    )
    persist = AsyncMock()
    monkeypatch.setattr(
        "app.services.ai.agent_service.memory_service.add_message",
        persist,
    )

    shared_state = {}
    events = [
        event
        async for event in service.chat_completion_stream(
            messages=[{"role": "user", "content": "当前模型是什么"}],
            conversation_id="conv-observe",
            user_info={"user_id": "42"},
            enable_multi_agent=False,
            shared_state=shared_state,
        )
    ]

    snapshot = shared_state["execution_performance"]
    assert events[-1]["content"]
    assert not any(event.get("type") == "error" for event in events)
    assert persist.await_count == 2
    assert "route_resolution" in snapshot["stages_ms"]
    assert "runtime_model_metadata" in snapshot["stages_ms"]
    assert snapshot["ttft_ms"] is not None
    assert snapshot["model_call_count"] == 0
    assert snapshot["tool_call_count"] == 0
    assert snapshot["audit_completed"] is True
    assert "content" not in snapshot


@pytest.mark.asyncio
async def test_default_main_decision_does_not_enter_router_trace(monkeypatch):
    from app.schemas.agent import ChatConfig
    from app.services.ai.agent_service import AgentService
    from app.services.ai.turn_decision import TurnDecision

    config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="main",
        model_name="configured-model",
        temperature=0.0,
        system_prompt="test",
        tools=[],
        capabilities=["general_chat"],
    )
    decision = TurnDecision.for_default_main_delegation(config)
    resolve = AsyncMock(return_value=(config, decision))
    monkeypatch.setattr(
        "app.services.ai.agent_service.AgentContextManager.resolve_agent_config",
        resolve,
    )

    trace_buffer = []
    resolved = await AgentService()._resolve_and_verify_agent(
        messages=[{"role": "user", "content": "你好"}],
        agent_id=None,
        agent_name=None,
        version_id=None,
        enable_multi_agent=True,
        user_info=None,
        trace_buffer=trace_buffer,
        user_query="你好",
        route_progress=AsyncMock(),
    )

    assert resolved[:2] == (config, decision)
    assert trace_buffer == []


@pytest.mark.asyncio
async def test_default_main_decision_does_not_emit_router_log(monkeypatch):
    from app.schemas.agent import ChatConfig
    from app.services.ai.agent_service import AgentService
    from app.services.ai.config import RuntimeModelInfo
    from app.services.ai.turn_decision import TurnDecision

    config = ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="main",
        model_name="configured-model",
        temperature=0.0,
        system_prompt="test",
        tools=[],
        capabilities=["general_chat"],
    )
    runtime_info = RuntimeModelInfo(
        configured_model="configured-model",
        effective_model_id="effective-model",
        source="test",
    )
    decision = TurnDecision.for_default_main_delegation(config)

    async def route_result():
        return config, decision, 1.0, None

    service = AgentService()
    route_task = asyncio.create_task(route_result())
    monkeypatch.setattr(service, "_start_route_resolution", lambda **_kwargs: route_task)
    monkeypatch.setattr(
        "app.services.ai.session_mcp_tools.apply_session_mcp_tools_to_agent_config",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "_resolve_runtime_model_info_safe",
        AsyncMock(return_value=runtime_info),
    )
    monkeypatch.setattr(
        "app.services.ai.agent_service.looks_like_current_model_query",
        lambda _query: True,
    )
    monkeypatch.setattr(
        "app.services.ai.agent_service.AuditManager.log_transaction",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.ai.agent_service.memory_service.add_message",
        AsyncMock(),
    )

    events = [
        event
        async for event in service.chat_completion_stream(
            messages=[{"role": "user", "content": "当前模型是什么"}],
            conversation_id="conv-default-main",
            user_info={"user_id": "42"},
            enable_multi_agent=True,
        )
    ]

    assert not any(event.get("type") == "router_log" for event in events)
    assert any(event.get("type") == "meta" for event in events)
