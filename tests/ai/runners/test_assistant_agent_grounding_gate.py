import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.grounding.models import EvidenceType, FactFreshness
from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.policy import FactRequirement
from app.services.ai.grounding.service import GroundingService
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
from app.services.ai.runners import assistant_agent_runner as assistant_runner_module
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.turn_decision import TurnDecision


pytestmark = pytest.mark.no_infrastructure


@asynccontextmanager
async def _unlocked_session(**_kwargs):
    yield


def _runner(*, request_source="unknown", debug_options=None) -> AssistantAgentRunner:
    return AssistantAgentRunner(
        config=ChatConfig(
            agent_id="sys-agent-chat",
            agent_name="main",
            model_name="test",
            temperature=0,
            system_prompt="test",
            tools=["sub_agent_call"],
        ),
        trace_id="grounding-test",
        trace_buffer=[],
        user_info={"user_id": "1", "role": "admin"},
        conversation_id="conv-1",
        turn_decision=TurnDecision(
            route_status="resolved",
            source=request_source,
            capability="answer",
        ),
        debug_options=debug_options,
    )


def test_unknown_router_source_uses_semantic_fact_intent_as_evidence_contract():
    runner = _runner()
    runner.turn_decision = runner.turn_decision.model_copy(update={
        "semantic_intent": "DATA_QUERY",
        "semantic_confidence": 0.9,
    })

    decision = runner._resolve_grounding_request_decision("分析业绩")

    assert decision.source.value == "internal_structured_data"
    assert decision.capability.value == "data_query"


def test_grounding_keeps_catalog_fallback_decision_instead_of_reinferring_knowledge():
    runner = _runner()
    runner.turn_decision = runner.turn_decision.model_copy(
        update={
            "source": "general",
            "semantic_intent": "KNOWLEDGE_BASE",
            "semantic_domain": "internal_docs",
            "knowledge_catalog_status": "available",
            "knowledge_fallback_allowed": True,
        }
    )

    decision = runner._resolve_grounding_request_decision(
        "查看春秋航空9C6475航班的准点率和退改签政策"
    )

    assert decision.source.value == "general"
    assert decision.should_delegate is False
    assert decision.knowledge_fallback_allowed is True


@pytest.mark.asyncio
async def test_knowledge_fallback_search_tool_is_wrapped_to_one_call_per_turn():
    runner = _runner()
    runner.turn_decision = runner.turn_decision.model_copy(
        update={"knowledge_fallback_allowed": True}
    )
    calls = []

    async def search(**kwargs):
        calls.append(kwargs)
        return '{"status":"empty","citations":[]}'

    tool = RuntimeToolSpec(
        name="search_knowledge_base",
        description="检索知识库",
        parameters_schema={"type": "object", "properties": {}},
        source_type="system",
        callable=search,
    )

    limited_tools = runner._apply_knowledge_fallback_budget([tool])
    first = await limited_tools[0].invoke({"query": "测试"})
    second = await limited_tools[0].invoke({"query": "再次测试"})

    assert len(calls) == 1
    assert first.startswith('{"status":"empty"')
    assert "本轮只允许调用一次" in second


def _fabricated_ranking() -> str:
    return (
        "好的，我来调用 ChatBI 按维度分析。\n"
        "| 排名 | 姓名 | 总订单数 | 总金额 |\n"
        "| --- | --- | --- | --- |\n"
        "| 1 | 王强 | 1,193 | ¥663.98万 |"
    )


def test_grounding_requires_explicit_boolean_true():
    assert _runner(debug_options=None)._grounding_enabled() is False
    assert _runner(debug_options={"grounding_enabled": False})._grounding_enabled() is False
    assert _runner(debug_options={"grounding_enabled": "true"})._grounding_enabled() is False
    assert _runner(debug_options={"grounding_enabled": True})._grounding_enabled() is True


def test_tool_preflight_marks_evidence_capable_nudge_as_current_turn_required():
    from app.services.ai.tool_nudge_policy import ToolNudge

    runner = _runner()
    tool = RuntimeToolSpec(
        name="mcp_get_tickets",
        description="查询票务结果",
        parameters_schema={"type": "object"},
        source_type="mcp",
        permission_scope="read",
        callable=lambda: "ok",
        evidence_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
    )
    nudge = ToolNudge(
        tool_name=tool.name,
        score=1.0,
        message="必须先调用工具",
        force_first_call=True,
    )

    metadata = runner._build_tool_preflight_evidence_metadata(nudge, [tool])

    assert metadata == {
        "current_turn_evidence_required": True,
        "required_evidence_types": ["external_tool"],
    }


@pytest.mark.asyncio
async def test_current_turn_evidence_gate_blocks_unverified_specific_answer():
    runner = _runner()

    async def fake_core(_history):
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["external_tool"],
        }
        yield {
            "type": "answer_delta",
            "content": "明天车次 G1 的票价是 100 元。",
            "phase": "synthesis",
        }

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "查询明天高铁票"}]
            )
        ]

    answer = "".join(
        str(event.get("content") or "")
        for event in events
        if event.get("type") == "answer_delta"
    )
    assert "G1" not in answer
    assert "100 元" not in answer
    assert "暂时无法可靠提供具体结论" in answer
    assert any(
        event.get("type") == "log"
        and event.get("grounding_blocked") is True
        for event in events
    )


@pytest.mark.asyncio
async def test_stream_with_retraction_emits_speculative_answer_then_retraction():
    runner = _runner(
        debug_options={
            "grounding_enabled": True,
            "grounding_block_mode": "stream_with_retraction",
        }
    )

    async def fake_core(_history):
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["external_tool"],
        }
        yield {
            "type": "answer_delta",
            "content": "北京今天有 38 度。",
            "phase": "synthesis",
        }

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "查询今天北京天气"}]
            )
        ]

    answer_index = next(
        index
        for index, event in enumerate(events)
        if event.get("type") == "answer_delta"
        and "北京今天有" in str(event.get("content") or "")
    )
    retraction_index = next(
        index
        for index, event in enumerate(events)
        if event.get("type") == "retraction"
    )
    assert answer_index < retraction_index
    assert "北京今天有" not in str(events[retraction_index].get("content") or "")


@pytest.mark.asyncio
async def test_multi_tool_evidence_contracts_are_checked_independently():
    runner = _runner()

    async def fake_core(_history):
        runner._evidence_ledger.record_success(
            call_id="weather-1",
            producer="weather_lookup",
            evidence_types={EvidenceType.EXTERNAL_TOOL},
            result={"city": "上海", "temperature": 26},
        )
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["external_tool"],
            "evidence_contracts": [
                {
                    "tool_name": "weather_lookup",
                    "required_evidence_types": ["external_tool"],
                    "freshness": "current_turn",
                },
                {
                    "tool_name": "train_lookup",
                    "required_evidence_types": ["external_tool"],
                    "freshness": "current_turn",
                },
            ],
        }
        yield {
            "type": "answer_delta",
            "content": "上海天气 26 度；北京到上海高铁票价 100 元。",
            "phase": "synthesis",
        }

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "查上海天气并查北京到上海高铁"}]
            )
        ]

    assert any(event.get("grounding_blocked") is True for event in events)


@pytest.mark.asyncio
async def test_multi_tool_evidence_contracts_release_each_correlated_result():
    runner = _runner()

    async def fake_core(_history):
        runner._evidence_ledger.record_success(
            call_id="weather-1",
            producer="weather_lookup",
            evidence_types={EvidenceType.EXTERNAL_TOOL},
            result={"city": "上海", "temperature": 26},
        )
        runner._evidence_ledger.record_success(
            call_id="train-1",
            producer="train_lookup",
            evidence_types={EvidenceType.EXTERNAL_TOOL},
            result="北京到上海高铁 G1 票价 100 元",
        )
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["external_tool"],
            "evidence_contracts": [
                {
                    "tool_name": "weather_lookup",
                    "required_evidence_types": ["external_tool"],
                    "freshness": "current_turn",
                },
                {
                    "tool_name": "train_lookup",
                    "required_evidence_types": ["external_tool"],
                    "freshness": "current_turn",
                },
            ],
        }
        yield {
            "type": "answer_delta",
            "content": "上海天气 26 度；北京到上海高铁 G1 票价 100 元。",
            "phase": "synthesis",
        }

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "查上海天气并查北京到上海高铁"}]
            )
        ]

    assert any(
        event.get("type") == "answer_delta"
        and "上海天气" in str(event.get("content") or "")
        for event in events
    )
    assert not any(event.get("grounding_blocked") is True for event in events)


def test_evidence_contract_freshness_is_normalized_and_invalid_values_fail_closed():
    runner = _runner()

    assert runner._resolve_contract_freshness("current_turn") is FactFreshness.DYNAMIC
    assert runner._resolve_contract_freshness("realtime") is FactFreshness.REALTIME
    assert runner._resolve_contract_freshness("not-a-freshness") is None


@pytest.mark.asyncio
async def test_current_turn_evidence_gate_releases_answer_with_matching_receipt():
    runner = _runner()

    async def fake_core(_history):
        runner._evidence_ledger.record_success(
            call_id="mcp-ticket-1",
            producer="mcp_get_tickets",
            evidence_types={EvidenceType.EXTERNAL_TOOL},
            result="车次 G1 票价 100 元",
        )
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["external_tool"],
        }
        yield {
            "type": "answer_delta",
            "content": "车次 G1 的票价是 100 元。",
            "phase": "synthesis",
        }

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "查询明天高铁票"}]
            )
        ]

    answer = "".join(
        str(event.get("content") or "")
        for event in events
        if event.get("type") == "answer_delta"
    )
    assert answer == "车次 G1 的票价是 100 元。"
    assert not any(event.get("grounding_blocked") is True for event in events)


@pytest.mark.asyncio
async def test_current_turn_evidence_gate_does_not_reuse_historical_receipt():
    runner = _runner()

    async def fake_core(_history):
        runner._evidence_ledger.record_success(
            call_id="historical-ticket-1",
            producer="previous-turn:mcp_get_tickets",
            evidence_types={EvidenceType.EXTERNAL_TOOL},
            result="车次 G1 票价 100 元",
            freshness=FactFreshness.REUSE_PREVIOUS,
        )
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["external_tool"],
        }
        yield {
            "type": "answer_delta",
            "content": "车次 G1 的票价是 100 元。",
            "phase": "synthesis",
        }

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "查询明天高铁票"}]
            )
        ]

    answer = "".join(
        str(event.get("content") or "")
        for event in events
        if event.get("type") == "answer_delta"
    )
    assert "G1" not in answer
    assert any(event.get("grounding_blocked") is True for event in events)


@pytest.mark.asyncio
async def test_preflight_failure_keeps_current_turn_evidence_gate_fail_closed():
    runner = _runner()
    tool = RuntimeToolSpec(
        name="mcp_get_tickets",
        description="查询高铁票车次和票价",
        parameters_schema={"type": "object"},
        source_type="mcp",
        permission_scope="read",
        callable=lambda: "ok",
        evidence_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
    )

    with patch.object(
        runner,
        "_resolve_runtime_tools_from_config",
        AsyncMock(return_value=[tool]),
    ), patch.object(
        runner,
        "_resolve_available_sub_agent_delegation_info",
        AsyncMock(return_value=(set(), {})),
    ), patch(
        "app.services.config_service.ConfigService.get",
        AsyncMock(side_effect=["5", "soft"]),
    ), patch(
        "app.services.ai.tool_nudge_policy.resolve_tool_nudge",
        side_effect=RuntimeError("preflight resolver failed"),
    ), patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=SimpleNamespace(native_model=None)),
    ), patch(
        "app.services.ai.session_tool_artifact.load_session_tool_artifact",
        AsyncMock(return_value=None),
    ):
        events = [
            event
            async for event in runner._execute_core(
                [{"role": "user", "content": "查询明天高铁票"}]
            )
        ]

    preflight = next(
        event
        for event in events
        if event.get("type") == "log" and event.get("category") == "tool_preflight"
    )
    assert preflight["current_turn_evidence_required"] is True
    assert preflight["force_first_call"] is True
    assert preflight["required_evidence_types"] == ["external_tool"]


@pytest.mark.asyncio
async def test_current_turn_evidence_gate_releases_explicit_no_result_answer():
    runner = _runner()

    async def fake_core(_history):
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["external_tool"],
        }
        yield {"content": "本轮没有获取到实时结果，暂时无法确认。"}

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "查询明天高铁票"}]
            )
        ]

    assert "本轮没有获取到实时结果" in "".join(
        str(event.get("content") or "") for event in events
    )
    assert not any(event.get("grounding_blocked") is True for event in events)


@pytest.mark.asyncio
async def test_current_turn_evidence_gate_waits_for_interrupted_tool_resume():
    runner = _runner()

    async def fake_core(_history):
        yield {
            "type": "log",
            "category": "tool_preflight",
            "current_turn_evidence_required": True,
            "required_evidence_types": ["external_tool"],
        }
        yield {
            "type": "external_execution_required",
            "status": "pending",
            "tool_call": {"name": "mcp_get_tickets"},
        }
        yield {
            "type": "answer_delta",
            "content": "车次 G1 的票价是 100 元。",
            "phase": "synthesis",
        }

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "查询明天高铁票"}]
            )
        ]

    assert not any(event.get("grounding_blocked") is True for event in events)
    assert not any(
        event.get("type") == "answer_delta"
        and "G1" in str(event.get("content") or "")
        for event in events
    )


@pytest.mark.asyncio
async def test_resumed_evidence_tool_answer_is_blocked_without_successful_receipt():
    runner = _runner()
    agent = SimpleNamespace(reply_stream=lambda _event: object(), state={})
    native_model = SimpleNamespace(model="test")
    state = {"user_query": "查询明天高铁票"}
    tool = RuntimeToolSpec(
        name="mcp_get_tickets",
        description="查询票务结果",
        parameters_schema={"type": "object"},
        source_type="mcp",
        permission_scope="read",
        callable=lambda: "ok",
        evidence_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
    )
    pending = SimpleNamespace(
        snapshot=SimpleNamespace(evidence_receipts=[]),
        tool_call=SimpleNamespace(name=tool.name),
    )

    async def fake_stream(**_kwargs):
        yield {"type": "answer_delta", "content": "车次 G1 的票价是 100 元。"}

    with patch.object(
        runner,
        "_create_tool_loop_detector",
        AsyncMock(return_value=None),
    ), patch.object(
        runner,
        "_resolve_pending_runtime",
        AsyncMock(return_value=(agent, [tool], native_model, state)),
    ), patch.object(
        runner,
        "_stream_agentscope_native_events",
        fake_stream,
    ), patch(
        "app.services.ai.runners.assistant_agent_runner.agent_state_store.save",
        AsyncMock(return_value=None),
    ), patch.object(
        assistant_runner_module.agentscope_session_lock,
        "hold",
        _unlocked_session,
    ):
        events = [
            event
            async for event in runner._resume_agentscope_native_stream(
                pending=pending,
                resume_event=object(),
            )
        ]

    answer = "".join(
        str(event.get("content") or "")
        for event in events
        if event.get("type") == "answer_delta"
    )
    assert "G1" not in answer
    assert any(event.get("grounding_blocked") is True for event in events)


@pytest.mark.asyncio
async def test_unknown_request_directly_passes_structured_output_without_tool_evidence():
    runner = _runner()

    async def fake_core(_history):
        yield {"content": _fabricated_ranking()}

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "按业务维度分析排行，看看最近谁最好"}]
            )
        ]

    content = "".join(str(event.get("content") or "") for event in events)
    assert "王强" in content
    assert not any(event.get("type") == "grounding_blocked" for event in events)


@pytest.mark.asyncio
async def test_unknown_date_answer_does_not_append_grounding_warning():
    runner = _runner(debug_options={"grounding_enabled": True})
    answer = "今天是 2026年7月30日，星期四。"

    async def fake_core(_history):
        yield {"content": answer}

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "今天日期"}]
            )
        ]

    assert answer == "".join(str(event.get("content") or "") for event in events)
    assert "风险提示" not in "".join(str(event.get("content") or "") for event in events)
    assert not any(
        event.get("type") == "log" and event.get("category") == "grounding"
        for event in events
    )


def test_structured_grounding_retry_overrides_unknown_route_with_typed_requirement():
    runner = _runner(
        request_source="unknown",
        debug_options={"grounding_enabled": True},
    )
    runner.debug_options["grounding_action"] = {
        "type": "retry",
        "required_evidence_types": ["public_web", "unsupported"],
    }
    ctx = runner._ensure_agent_context()

    requirement = runner._resolve_turn_grounding_requirement("重新核实", ctx)

    assert requirement.required is True
    assert requirement.accepted_types == frozenset({EvidenceType.PUBLIC_WEB})


def test_grounding_retry_selects_only_tool_with_matching_evidence_type():
    runner = _runner(request_source="unknown")
    tools = [
        SimpleNamespace(name="read_file", evidence_types={EvidenceType.USER_FILE}),
        SimpleNamespace(name="web_search", evidence_types={EvidenceType.PUBLIC_WEB}),
    ]

    selected = runner._select_grounding_retry_tool(
        tools,
        frozenset({EvidenceType.PUBLIC_WEB}),
    )

    assert selected.name == "web_search"
    assert runner._select_grounding_retry_tool(
        tools,
        frozenset({EvidenceType.RUNTIME_STATE}),
    ) is None


def test_structured_method_action_keeps_unknown_output_scrutiny_without_requiring_tool():
    runner = _runner(
        request_source="internal_structured_data",
        debug_options={"grounding_enabled": True},
    )
    runner.debug_options["grounding_action"] = {"type": "method"}
    ctx = runner._ensure_agent_context()

    requirement = runner._resolve_turn_grounding_requirement("只看分析方法", ctx)

    assert requirement.required is False
    assert requirement.scrutinize_unknown_output is True


def test_general_route_hint_is_upgraded_from_dynamic_public_user_intent():
    runner = _runner(request_source="general")
    ctx = runner._ensure_agent_context()

    requirement = runner._resolve_turn_grounding_requirement(
        "查询今天上海天气",
        ctx,
    )

    assert requirement.required is True
    assert requirement.accepted_types == frozenset({EvidenceType.PUBLIC_WEB})


@pytest.mark.asyncio
async def test_unknown_request_with_unrelated_tool_attempt_still_passes():
    runner = _runner()

    async def fake_core(_history):
        yield {"type": "log", "category": "tool", "title": "get_current_time"}
        yield {"content": _fabricated_ranking()}

    with patch.object(runner, "_execute_core", fake_core):
        events = [event async for event in runner.execute([{"role": "user", "content": "分析排行"}])]

    content = "".join(str(event.get("content") or "") for event in events)
    assert "王强" in content
    assert not any(event.get("type") == "grounding_blocked" for event in events)


@pytest.mark.asyncio
async def test_matching_server_receipt_allows_grounded_facts():
    runner = _runner(request_source="internal_structured_data")
    grounded = "根据查询结果，王强本月金额为 100 万元。"

    async def fake_core(_history):
        runner._evidence_ledger.record_success(
            call_id="call-1",
            producer="data-agent",
            evidence_types={EvidenceType.INTERNAL_DATA},
            result={"rows": [{"name": "王强", "amount": 100}]},
        )
        yield {"content": grounded}

    with patch.object(runner, "_execute_core", fake_core):
        events = [event async for event in runner.execute([{"role": "user", "content": "分析排行"}])]

    assert grounded == "".join(str(event.get("content") or "") for event in events)


@pytest.mark.asyncio
async def test_successful_read_only_mcp_receipt_does_not_append_risk_warning():
    runner = _runner()
    grounded = (
        "| 车次 | 出发时间 | 二等座 |\n"
        "| --- | --- | --- |\n"
        "| G1 | 06:30 | 661元 |"
    )

    async def fake_core(_history):
        runner._evidence_ledger.record_success(
            call_id="mcp-tickets-1",
            producer="mcp-api:get-tickets",
            evidence_types={EvidenceType.EXTERNAL_TOOL},
            result={"trains": [{"number": "G1", "departure": "06:30", "price": 661}]},
        )
        yield {"content": grounded}

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "查询明天北京到上海的火车票"}]
            )
        ]

    content = "".join(str(event.get("content") or "") for event in events)
    assert content == grounded
    assert "风险提示" not in content
    assert not any(event.get("type") == "grounding_blocked" for event in events)


@pytest.mark.asyncio
async def test_missing_evidence_preserves_answer_and_appends_risk_warning():
    runner = _runner(
        request_source="internal_structured_data",
        debug_options={"grounding_enabled": True},
    )
    answer = "当前销售额排名第一的是王强，金额为 663.98 万元。"

    async def fake_core(_history):
        yield {"content": answer}

    with patch.object(runner, "_execute_core", fake_core):
        events = [event async for event in runner.execute([{"role": "user", "content": "分析排行"}])]

    content = "".join(str(event.get("content") or "") for event in events)
    assert answer in content
    assert content.count("风险提示") == 1
    assert not any(event.get("type") == "grounding_blocked" for event in events)
    grounding_logs = [
        event
        for event in events
        if event.get("type") == "log" and event.get("category") == "grounding"
    ]
    assert len(grounding_logs) == 1
    decision_meta = grounding_logs[0]["grounding_decision"]
    assert decision_meta["decision_origin"] == "router"
    assert decision_meta["evidence_mode"] == "required"
    assert decision_meta["accepted_evidence_types"] == ["internal_data"]
    assert decision_meta["decision_conflicts"] == []


@pytest.mark.asyncio
async def test_grounding_is_disabled_by_default_and_streams_without_warning():
    runner = _runner(request_source="internal_structured_data")
    answer = "当前销售额排名第一的是王强，金额为 663.98 万元。"

    async def fake_core(_history):
        yield {"content": answer}

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "分析排行"}]
            )
        ]

    assert events == [{"content": answer}]
    assert "风险提示" not in "".join(str(event.get("content") or "") for event in events)


@pytest.mark.asyncio
async def test_internal_knowledge_compatibility_preserves_answer_with_source_notice():
    runner = _runner(
        request_source="internal_structured_data",
        debug_options={"grounding_enabled": True},
    )
    answer = "询价审批需要采购和主管共同确认。"

    async def fake_core(_history):
        runner._evidence_ledger.record_success(
            call_id="kb-1",
            producer="search_knowledge_base",
            evidence_types={EvidenceType.INTERNAL_KNOWLEDGE},
            result={"content": answer},
        )
        yield {"content": answer}

    with patch.object(runner, "_execute_core", fake_core):
        events = [event async for event in runner.execute([{"role": "user", "content": "说明询价审批规则"}])]

    content = "".join(str(event.get("content") or "") for event in events)
    assert answer in content
    assert content.count("信息来源提示") == 1
    assert "知识库或已授权文件" in content
    assert not any(event.get("type") == "grounding_blocked" for event in events)


@pytest.mark.asyncio
async def test_sub_agent_runtime_tool_receipt_allows_main_grounded_answer():
    from app.core.context import AgentContext, get_current_agent_context, set_agent_context

    runner = _runner()
    grounded = "根据实际查询结果，2026-07-10 新增用户 3 人。"
    sql_tool = RuntimeToolSpec(
        name="execute_sql_query",
        description="query data",
        parameters_schema={"type": "object"},
        source_type="static",
        callable=lambda: {"items": [["2026-07-10", 3]]},
        evidence_types=frozenset({EvidenceType.INTERNAL_DATA}),
    )

    async def fake_core(_history):
        main_ctx = get_current_agent_context()
        sub_ctx = AgentContext(
            agent_id="data-agent",
            agent_name="chat-bi",
            delegation_depth=1,
            grounding_evidence_ledger=main_ctx.grounding_evidence_ledger,
        )
        set_agent_context(sub_ctx)
        try:
            await sql_tool.invoke({})
        finally:
            set_agent_context(main_ctx)
        yield {"content": grounded}

    with patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "有多少用户，每天新增情况"}]
            )
        ]

    assert grounded == "".join(str(event.get("content") or "") for event in events)
    assert not any(event.get("type") == "grounding_blocked" for event in events)


@pytest.mark.asyncio
async def test_stable_general_answer_keeps_existing_behavior():
    runner = _runner(request_source="general")
    answer = "HTTP 404 表示请求的资源未找到。"

    async def fake_core(_history):
        yield {"content": answer}

    with patch.object(runner, "_execute_core", fake_core):
        events = [event async for event in runner.execute([{"role": "user", "content": "404 是什么"}])]

    assert answer == "".join(str(event.get("content") or "") for event in events)


def test_historical_attachment_authorization_does_not_require_file_evidence_for_current_turn():
    runner = _runner(request_source="general")
    ctx = runner._ensure_agent_context()
    ctx.authorized_attachment_paths = ["/tmp/old-report.xlsx"]
    ctx.current_turn_attachment_paths = []

    requirement = runner._resolve_turn_grounding_requirement("解释一下 HTTP 404", ctx)

    assert requirement.required is False
    assert EvidenceType.USER_FILE not in requirement.accepted_types


def test_current_turn_attachment_requires_file_evidence():
    runner = _runner(request_source="general")
    ctx = runner._ensure_agent_context()
    ctx.authorized_attachment_paths = ["/tmp/current-report.xlsx"]
    ctx.current_turn_attachment_paths = ["/tmp/current-report.xlsx"]

    requirement = runner._resolve_turn_grounding_requirement("分析这个附件", ctx)

    assert requirement.required is True
    assert requirement.accepted_types == frozenset({EvidenceType.USER_FILE})


def test_current_turn_attachment_adds_file_as_alternative_to_knowledge_evidence():
    runner = _runner(request_source="internal_docs")
    ctx = runner._ensure_agent_context()
    ctx.authorized_attachment_paths = ["/tmp/current-policy.docx"]
    ctx.current_turn_attachment_paths = ["/tmp/current-policy.docx"]

    requirement = runner._resolve_turn_grounding_requirement("总结这份询价制度", ctx)

    assert requirement.required is True
    assert requirement.accepted_types == frozenset(
        {EvidenceType.INTERNAL_KNOWLEDGE, EvidenceType.USER_FILE}
    )


def test_attachment_continuation_requires_file_evidence_without_repeating_file_word():
    runner = _runner(request_source="unknown")
    ctx = runner._ensure_agent_context()
    ctx.authorized_attachment_paths = ["/tmp/report.pdf"]
    ctx.current_turn_attachment_paths = []

    requirement = runner._resolve_turn_grounding_requirement("继续看第二页", ctx)

    assert requirement.required is True
    assert requirement.accepted_types == frozenset({EvidenceType.USER_FILE})


@pytest.mark.parametrize(
    ("query", "evidence_type"),
    [
        ("联网搜索一下今天的天气", EvidenceType.PUBLIC_WEB),
        ("看看当前服务器磁盘状态", EvidenceType.RUNTIME_STATE),
        ("从知识库查一下操作要求", EvidenceType.INTERNAL_KNOWLEDGE),
        ("我上次说过的偏好是什么", EvidenceType.CONVERSATION_MEMORY),
    ],
)
def test_unknown_requirement_refines_from_query_signal_and_matching_evidence(
    query,
    evidence_type,
):
    runner = _runner(request_source="unknown")
    ledger = EvidenceLedger(user_id="1", conversation_id="conv-1")
    ledger.record_success(
        call_id="call-1",
        producer="typed-tool",
        evidence_types={evidence_type},
        result={"success": True, "data": "verified"},
    )
    requirement = FactRequirement(False, frozenset(), scrutinize_unknown_output=True)

    refined = runner._refine_unknown_requirement_from_evidence(
        requirement,
        user_query=query,
        ledger=ledger,
    )

    assert refined.required is True
    assert refined.accepted_types == frozenset({evidence_type})


def test_only_clearly_non_factual_turns_skip_grounding_buffer():
    runner = _runner(request_source="general")
    general = FactRequirement(False, frozenset(), scrutinize_unknown_output=False)
    unknown = FactRequirement(False, frozenset(), scrutinize_unknown_output=True)
    factual = FactRequirement(True, frozenset({EvidenceType.PUBLIC_WEB}))

    assert runner._should_buffer_grounding_output(general, run_data_guard=False) is False
    assert runner._should_buffer_grounding_output(general, run_data_guard=True) is True
    assert runner._should_buffer_grounding_output(unknown, run_data_guard=False) is True
    assert runner._should_buffer_grounding_output(factual, run_data_guard=False) is True


@pytest.mark.asyncio
async def test_clearly_non_factual_turn_emits_content_before_generation_completes():
    runner = _runner(request_source="general")
    ctx = runner._ensure_agent_context()
    ctx.authorized_attachment_paths = []
    ctx.current_turn_attachment_paths = []
    release = asyncio.Event()

    async def fake_core(_history):
        yield {"content": "第一段"}
        await release.wait()
        yield {"content": "第二段"}

    with patch.object(runner, "_execute_core", fake_core):
        stream = runner.execute([{"role": "user", "content": "帮我润色这句话"}])
        first = await asyncio.wait_for(anext(stream), timeout=0.5)
        assert first == {"content": "第一段"}
        release.set()
        remaining = [event async for event in stream]

    assert remaining == [{"content": "第二段"}]


@pytest.mark.asyncio
async def test_general_route_dynamic_fact_streams_then_appends_warning():
    runner = _runner(
        request_source="general",
        debug_options={"grounding_enabled": True},
    )

    async def fake_core(_history):
        yield {"content": "当前美元汇率为 9.99。"}

    with patch.object(
        GroundingService,
        "audit",
        wraps=GroundingService.audit,
    ) as audit_mock, patch.object(runner, "_execute_core", fake_core):
        events = [
            event
            async for event in runner.execute(
                [{"role": "user", "content": "给我简单介绍一下汇率"}]
            )
        ]

    content = "".join(str(event.get("content") or "") for event in events)
    assert "当前美元汇率为 9.99。" in content
    assert content.count("风险提示") == 1
    assert audit_mock.call_count == 1


@pytest.mark.asyncio
async def test_resumed_stream_preserves_answer_and_appends_risk_warning():
    runner = _runner(
        request_source="internal_structured_data",
        debug_options={"grounding_enabled": True},
    )
    answer = "当前销售额为 663.98 万元。"
    agent = SimpleNamespace(reply_stream=lambda _event: object(), state={})
    native_model = SimpleNamespace(model="test")
    state = {"user_query": "查询当前销售额"}

    async def fake_stream(**_kwargs):
        yield {"content": answer}

    with patch.object(
        runner,
        "_create_tool_loop_detector",
        AsyncMock(return_value=None),
    ), patch.object(
        runner,
        "_resolve_pending_runtime",
        AsyncMock(return_value=(agent, [], native_model, state)),
    ), patch.object(
        runner,
        "_stream_agentscope_native_events",
        fake_stream,
    ), patch(
        "app.services.ai.runners.assistant_agent_runner.agent_state_store.save",
        AsyncMock(return_value=None),
    ), patch.object(
        assistant_runner_module.agentscope_session_lock,
        "hold",
        _unlocked_session,
    ):
        events = [
            event
            async for event in runner._resume_agentscope_native_stream(
                pending=SimpleNamespace(),
                resume_event=object(),
            )
        ]

    content = "".join(str(event.get("content") or "") for event in events)
    assert answer in content
    assert content.count("风险提示") == 1
    assert not any(event.get("type") == "grounding_blocked" for event in events)


@pytest.mark.asyncio
async def test_cross_process_resume_restores_mcp_evidence_without_warning():
    runner = _runner()
    answer = (
        "| 车次 | 出发时间 | 二等座 |\n"
        "| --- | --- | --- |\n"
        "| G1 | 06:30 | 661元 |"
    )
    source_ledger = EvidenceLedger(user_id="1", conversation_id="conv-1")
    source_ledger.record_success(
        call_id="mcp-tickets-1",
        producer="railway:get-tickets",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"trains": [{"number": "G1", "price": 661}]},
    )
    pending = SimpleNamespace(
        snapshot=SimpleNamespace(evidence_receipts=source_ledger.to_snapshot())
    )
    agent = SimpleNamespace(reply_stream=lambda _event: object(), state={})
    native_model = SimpleNamespace(model="test")
    state = {"user_query": "查询明天北京到上海的火车票"}

    async def fake_stream(**_kwargs):
        yield {"content": answer}

    with patch.object(
        runner,
        "_create_tool_loop_detector",
        AsyncMock(return_value=None),
    ), patch.object(
        runner,
        "_resolve_pending_runtime",
        AsyncMock(return_value=(agent, [], native_model, state)),
    ), patch.object(
        runner,
        "_stream_agentscope_native_events",
        fake_stream,
    ), patch(
        "app.services.ai.runners.assistant_agent_runner.agent_state_store.save",
        AsyncMock(return_value=None),
    ), patch.object(
        assistant_runner_module.agentscope_session_lock,
        "hold",
        _unlocked_session,
    ):
        events = [
            event
            async for event in runner._resume_agentscope_native_stream(
                pending=pending,
                resume_event=object(),
            )
        ]

    content = "".join(str(event.get("content") or "") for event in events)
    assert content == answer
    assert "风险提示" not in content


def test_successful_external_execution_result_records_tool_evidence():
    ledger = EvidenceLedger(user_id="1", conversation_id="conv-1")
    tool = RuntimeToolSpec(
        name="client_weather",
        description="client weather",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=lambda: None,
        evidence_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
        evidence_policy="allow_empty_success",
    )
    result = SimpleNamespace(
        id="call-weather",
        name="client_weather",
        output='{"city":"上海","temperature":28}',
        state=SimpleNamespace(value="success"),
    )

    AssistantAgentRunner._record_external_execution_evidence(
        ledger=ledger,
        tools=[tool],
        execution_results=[result],
    )

    assert ledger.has_valid_evidence({EvidenceType.EXTERNAL_TOOL})


def test_failed_external_execution_result_does_not_record_tool_evidence():
    ledger = EvidenceLedger(user_id="1", conversation_id="conv-1")
    tool = RuntimeToolSpec(
        name="client_weather",
        description="client weather",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=lambda: None,
        evidence_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
    )
    result = SimpleNamespace(
        id="call-weather",
        name="client_weather",
        output="permission denied",
        state=SimpleNamespace(value="error"),
    )

    AssistantAgentRunner._record_external_execution_evidence(
        ledger=ledger,
        tools=[tool],
        execution_results=[result],
    )

    assert not ledger.receipts
