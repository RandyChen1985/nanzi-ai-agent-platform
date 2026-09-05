from datetime import datetime, timezone

import pytest

from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceStatus, EvidenceType
from app.services.ai.runtime.agentscope.tool_result import (
    attach_tool_call_id_metadata,
    build_final_tool_result_context,
    build_tool_result_envelope,
    is_tool_execution_success,
    tool_call_id_from_metadata,
)


pytestmark = pytest.mark.no_infrastructure


def test_tool_result_envelope_carries_provenance_and_is_admitted_only_at_final_boundary():
    observed_at = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)
    source_as_of = datetime(2026, 9, 5, 8, 59, tzinfo=timezone.utc)
    envelope = build_tool_result_envelope(
        call_id="call-1",
        producer="mcp:get_tickets",
        result={
            "status": "success",
            "items": [{"train": "G1"}],
            "source_ref": "mcp://rail/tickets",
            "observed_at": observed_at.isoformat(),
            "data_as_of": source_as_of.isoformat(),
            "truncated": True,
        },
        evidence_policy="non_empty",
        result_state="success",
    )

    assert envelope.status is EvidenceStatus.SUCCESS_NON_EMPTY
    assert envelope.call_id == "call-1"
    assert envelope.producer == "mcp:get_tickets"
    assert envelope.source_ref == "mcp://rail/tickets"
    assert envelope.observed_at == observed_at
    assert envelope.source_as_of == source_as_of
    assert envelope.truncated is True
    assert envelope.evidence_eligible is True

    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    receipt = ledger.record_envelope(
        envelope,
        evidence_types={EvidenceType.EXTERNAL_TOOL},
        policy="non_empty",
    )
    assert receipt is not None
    assert receipt.call_id == "call-1"
    assert receipt.source_ref == "mcp://rail/tickets"
    assert receipt.observed_at == observed_at
    assert receipt.source_as_of == source_as_of
    assert receipt.truncated is True
    restored = EvidenceLedger.from_snapshot(
        ledger.to_snapshot(),
        user_id="u1",
        conversation_id="c1",
    )
    assert restored.receipts[0].truncated is True


def test_error_envelope_is_not_admitted_even_when_error_text_contains_business_facts():
    envelope = build_tool_result_envelope(
        call_id="call-error",
        producer="generic_api:orders",
        result={
            "status": "error",
            "message": "查询失败，但上次订单总数是 999",
            "items": [{"order_id": "should-not-be-evidence"}],
        },
        evidence_policy="non_empty",
        result_state="error",
    )

    assert envelope.status is EvidenceStatus.FAILED
    assert envelope.evidence_eligible is False

    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    assert (
        ledger.record_envelope(
            envelope,
            evidence_types={EvidenceType.INTERNAL_DATA},
            policy="non_empty",
        )
        is None
    )
    assert ledger.receipts == ()


def test_success_state_cannot_override_error_payload():
    envelope = build_tool_result_envelope(
        call_id="call-inconsistent",
        producer="mcp:orders",
        result={"status": "error", "message": "上游返回失败，但订单数为 100"},
        result_state="success",
    )

    assert envelope.status is EvidenceStatus.FAILED
    assert envelope.evidence_eligible is False


@pytest.mark.parametrize(
    "result",
    [
        None,
        "",
        "错误码定义：404 表示资源不存在。",
        "失败原因字段说明：这是接口文档正文。",
    ],
)
def test_empty_or_descriptive_text_is_not_execution_failure(result):
    assert is_tool_execution_success(result) is True


def test_explicit_error_payload_is_execution_failure():
    assert is_tool_execution_success({"success": False, "message": "查询失败"}) is False
    assert is_tool_execution_success("[Execution Error] upstream unavailable") is False


@pytest.mark.asyncio
async def test_runtime_action_with_empty_result_is_success_without_evidence():
    from app.services.ai.runtime.agentscope.tools import AgentScopeRuntimeTool, RuntimeToolSpec

    tool = AgentScopeRuntimeTool(
        RuntimeToolSpec(
            name="update_preferences",
            description="Update preferences",
            parameters_schema={"type": "object", "properties": {}},
            source_type="static",
            callable=lambda: None,
            permission_scope="write",
        )
    )

    result = await tool()

    assert str(getattr(result.state, "value", result.state)).lower() == "success"


@pytest.mark.asyncio
async def test_native_action_converts_empty_raw_result_to_success_chunk():
    from app.services.ai.runtime.agentscope.tools import AgentScopeNativeApprovalTool

    class NativeAction:
        name = "update_preferences"
        description = "Update preferences"
        input_schema = {"type": "object", "properties": {}}
        is_read_only = False

        async def __call__(self, **kwargs):
            return None

    result = await AgentScopeNativeApprovalTool(NativeAction(), permission_scope="write")()

    assert str(getattr(result.state, "value", result.state)).lower() == "success"
    assert result.content == []


@pytest.mark.asyncio
async def test_native_exception_chunk_keeps_internal_call_id_for_event_reconciliation():
    from app.services.ai.runtime.agentscope.tools import AgentScopeNativeApprovalTool

    class FailingNativeTool:
        name = "failing_lookup"
        description = "failing lookup"
        input_schema = {"type": "object", "properties": {}}
        is_read_only = True

        async def __call__(self, **kwargs):
            raise RuntimeError("upstream unavailable")

    result = await AgentScopeNativeApprovalTool(
        FailingNativeTool(),
        permission_scope="read",
    )()

    assert str(getattr(result.state, "value", result.state)).lower() == "error"
    assert tool_call_id_from_metadata(result)


@pytest.mark.asyncio
async def test_async_generator_error_chunk_is_not_admitted_as_evidence():
    from agentscope.message import TextBlock, ToolResultState
    from agentscope.tool import ToolChunk

    from app.core.context import AgentContext, set_agent_context
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    async def generator():
        yield ToolChunk(
            content=[TextBlock(text="上游失败")],
            state=ToolResultState.ERROR,
        )

    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    set_agent_context(
        AgentContext(
            agent_id="assistant",
            agent_name="assistant",
            grounding_evidence_ledger=ledger,
        )
    )
    try:
        spec = RuntimeToolSpec(
            name="streaming_lookup",
            description="streaming lookup",
            parameters_schema={"type": "object", "properties": {}},
            source_type="static",
            callable=generator,
            evidence_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
            evidence_policy="allow_empty_success",
        )
        await spec.invoke({})
    finally:
        set_agent_context(None)

    assert ledger.receipts == ()


def test_retry_keeps_only_the_final_successful_tool_receipt():
    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    failed = build_tool_result_envelope(
        call_id="call-retry-1",
        producer="search_knowledge_base",
        result="[Tool Error] knowledge service unavailable",
        evidence_policy="allow_empty_success",
        result_state="error",
    )
    succeeded = build_tool_result_envelope(
        call_id="call-retry-2",
        producer="search_knowledge_base",
        result='{"content":"权限申请流程","citations":[{"id":"1"}]}',
        evidence_policy="allow_empty_success",
        result_state="success",
    )

    assert ledger.record_envelope(
        failed,
        evidence_types={EvidenceType.INTERNAL_KNOWLEDGE},
        policy="allow_empty_success",
    ) is None
    assert ledger.record_envelope(
        succeeded,
        evidence_types={EvidenceType.INTERNAL_KNOWLEDGE},
        policy="allow_empty_success",
    ) is not None
    assert [receipt.call_id for receipt in ledger.receipts] == ["call-retry-2"]


def test_tool_chunk_metadata_reconciles_internal_and_agentscope_call_ids():
    from agentscope.message import TextBlock
    from agentscope.tool import ToolChunk

    chunk = ToolChunk(content=[TextBlock(text="ok")])
    attach_tool_call_id_metadata(chunk, "internal-call-1")
    assert tool_call_id_from_metadata(chunk) == "internal-call-1"

    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    envelope = build_tool_result_envelope(
        call_id="internal-call-1",
        producer="lookup",
        result={"items": [{"id": "A-1"}]},
        evidence_policy="non_empty",
        result_state="success",
    )
    assert ledger.record_envelope(
        envelope,
        evidence_types={EvidenceType.EXTERNAL_TOOL},
    ) is not None
    assert ledger.rebind_call_id("internal-call-1", "agentscope-call-1") is True
    assert ledger.receipts[0].call_id == "agentscope-call-1"


def test_truncated_receipt_can_support_a_row_but_not_a_complete_result_claim():
    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    ledger.record_success(
        call_id="truncated-1",
        producer="orders_query",
        evidence_types={EvidenceType.INTERNAL_DATA},
        result={"items": [{"order_id": "A-1", "amount": 100}]},
        truncated=True,
    )

    assert ledger.has_fresh_evidence(
        {EvidenceType.INTERNAL_DATA},
        allow_truncated=False,
    ) is False
    assert ledger.has_candidate_overlap(
        "订单 A-1 金额 100 元",
        {EvidenceType.INTERNAL_DATA},
        allow_truncated=True,
    ) is True


@pytest.mark.asyncio
async def test_runtime_tool_domain_error_is_final_error_and_never_enters_ledger():
    from app.core.context import AgentContext, set_agent_context
    from app.services.ai.runtime.agentscope.tools import AgentScopeRuntimeTool, RuntimeToolSpec

    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    set_agent_context(
        AgentContext(
            agent_id="assistant",
            agent_name="assistant",
            grounding_evidence_ledger=ledger,
        )
    )
    spec = RuntimeToolSpec(
        name="search_knowledge_base",
        description="search",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=lambda: {"status": "error", "message": "知识库失败，但数字 123 不是证据"},
        evidence_types=frozenset({EvidenceType.INTERNAL_KNOWLEDGE}),
        evidence_policy="allow_empty_success",
    )

    try:
        result = await AgentScopeRuntimeTool(spec)()
    finally:
        set_agent_context(None)

    assert str(getattr(result.state, "value", result.state)).lower() == "error"
    assert ledger.receipts == ()


def test_final_tool_context_excludes_process_and_failed_result_text():
    meta = {
        "tool_names": {"call-ok": "search_knowledge_base", "call-failed": "search_knowledge_base"},
        "tool_args_text": {"call-ok": '{"q":"权限"}', "call-failed": '{"q":"旧问题"}'},
        "tool_outputs": {
            "call-ok": "找到 2 条知识：权限申请流程",
            "call-failed": "工具调用失败：知识库暂不可用",
        },
        "tool_result_states": {"call-ok": "success", "call-failed": "error"},
        "tool_data": {"call-ok": [{"title": "权限申请流程"}]},
    }

    context = build_final_tool_result_context(meta)

    assert "权限申请流程" in context
    assert "旧问题" not in context
    assert "暂不可用" not in context
    assert "思考" not in context
    assert "执行日志" not in context
