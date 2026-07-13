import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.policy import FactRequirement
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec


pytestmark = pytest.mark.no_infrastructure


def _runner(*, request_source="unknown") -> AssistantAgentRunner:
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
        route_hints={
            "request_source": request_source,
            "request_capability": "answer",
        },
    )


def test_unknown_router_source_uses_semantic_fact_intent_as_evidence_contract():
    runner = _runner()
    runner.route_hints["semantic_intent"] = "DATA_QUERY"
    runner.route_hints["semantic_confidence"] = 0.9

    decision = runner._resolve_grounding_request_decision("分析业绩")

    assert decision.source.value == "internal_structured_data"
    assert decision.capability.value == "data_query"


def _fabricated_ranking() -> str:
    return (
        "好的，我来调用 ChatBI 按维度分析。\n"
        "| 排名 | 姓名 | 总订单数 | 总金额 |\n"
        "| --- | --- | --- | --- |\n"
        "| 1 | 王强 | 1,193 | ¥663.98万 |"
    )


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


def test_structured_grounding_retry_overrides_unknown_route_with_typed_requirement():
    runner = _runner(request_source="unknown")
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
    runner = _runner(request_source="internal_structured_data")
    runner.debug_options["grounding_action"] = {"type": "method"}
    ctx = runner._ensure_agent_context()

    requirement = runner._resolve_turn_grounding_requirement("只看分析方法", ctx)

    assert requirement.required is False
    assert requirement.scrutinize_unknown_output is True


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
