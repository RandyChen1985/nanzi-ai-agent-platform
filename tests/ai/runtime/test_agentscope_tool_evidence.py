import pytest

from app.core.context import AgentContext, set_agent_context
from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.runtime.agentscope.tools import (
    RuntimeToolSpec,
    runtime_tool_spec_from_legacy_tool,
    runtime_tool_spec_from_native_agentscope_tool,
)
from app.services.ai.tools.registry import ToolRegistry


pytestmark = pytest.mark.no_infrastructure


async def _noop(**_kwargs):
    return "ok"


def test_runtime_tool_spec_has_backward_compatible_evidence_metadata():
    spec = RuntimeToolSpec(
        name="plain-tool",
        description="plain",
        parameters_schema={"type": "object"},
        source_type="static",
        callable=_noop,
    )

    assert spec.evidence_types == frozenset()
    assert spec.evidence_policy == "non_empty"


def test_legacy_tool_evidence_metadata_is_preserved():
    class LegacyTool:
        name = "knowledge-tool"
        description = "search internal documents"
        evidence_types = {EvidenceType.INTERNAL_KNOWLEDGE}
        evidence_policy = "structured_success"

        async def ainvoke(self, _kwargs):
            return {"content": "result"}

    spec = runtime_tool_spec_from_legacy_tool(LegacyTool(), source_type="static")

    assert spec.evidence_types == frozenset({EvidenceType.INTERNAL_KNOWLEDGE})
    assert spec.evidence_policy == "structured_success"


async def test_registry_assigns_abstract_evidence_capabilities():
    knowledge = await ToolRegistry.get_runtime_tool("search_knowledge_base")
    web = await ToolRegistry.get_runtime_tool("web_search_baidu")
    user_file = await ToolRegistry.get_runtime_tool("read_file")
    runtime = await ToolRegistry.get_runtime_tool("list_process")
    memory = await ToolRegistry.get_runtime_tool("memory_search")

    assert knowledge.evidence_types == frozenset({EvidenceType.INTERNAL_KNOWLEDGE})
    assert web.evidence_types == frozenset({EvidenceType.PUBLIC_WEB})
    assert user_file.evidence_types == frozenset({EvidenceType.USER_FILE})
    assert runtime.evidence_types == frozenset({EvidenceType.RUNTIME_STATE})
    assert memory.evidence_types == frozenset({EvidenceType.CONVERSATION_MEMORY})
    assert knowledge.evidence_policy == "allow_empty_success"
    assert user_file.evidence_policy == "allow_empty_success"


async def test_registry_resolves_builtin_aliases_to_evidence_capabilities():
    bash_by_alias = await ToolRegistry.get_runtime_tool("bash")
    bash_native = await ToolRegistry.get_runtime_tool("Bash")
    grep_alias = await ToolRegistry.get_runtime_tool("grep")
    read_alias = await ToolRegistry.get_runtime_tool("read")

    assert bash_by_alias.evidence_types == frozenset({EvidenceType.RUNTIME_STATE})
    assert bash_native.evidence_types == frozenset({EvidenceType.RUNTIME_STATE})
    assert grep_alias.evidence_types == frozenset({EvidenceType.USER_FILE})
    assert read_alias.evidence_types == frozenset({EvidenceType.USER_FILE})
    assert grep_alias.evidence_policy == "allow_empty_success"
    assert read_alias.evidence_policy == "allow_empty_success"


def test_legacy_tool_ignores_invalid_evidence_type_and_keeps_valid_values(caplog):
    class LegacyTool:
        name = "third-party-tool"
        description = "dynamic tool"
        evidence_types = {EvidenceType.PUBLIC_WEB, "not-a-real-evidence-type"}

        async def ainvoke(self, _kwargs):
            return "ok"

    spec = runtime_tool_spec_from_legacy_tool(LegacyTool(), source_type="mcp")

    assert spec.evidence_types == frozenset({EvidenceType.PUBLIC_WEB})
    assert "not-a-real-evidence-type" in caplog.text


def test_native_tool_ignores_invalid_evidence_type(caplog):
    class NativeTool:
        name = "native-third-party-tool"
        description = "dynamic native tool"
        input_schema = {"type": "object", "properties": {}}
        evidence_types = {"unsupported"}

        async def __call__(self, **_kwargs):
            return "ok"

    spec = runtime_tool_spec_from_native_agentscope_tool(NativeTool())

    assert spec.evidence_types == frozenset()
    assert "unsupported" in caplog.text


@pytest.mark.asyncio
async def test_runtime_tool_records_typed_evidence_in_request_context_ledger():
    ledger = EvidenceLedger(user_id="1", conversation_id="conv-1")
    ctx = AgentContext(
        agent_id="data-agent",
        agent_name="chat-bi",
        grounding_evidence_ledger=ledger,
    )
    set_agent_context(ctx)
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="query data",
        parameters_schema={"type": "object"},
        source_type="static",
        callable=lambda: {"items": [["2026-07-10", 3]]},
        evidence_types=frozenset({EvidenceType.INTERNAL_DATA}),
    )

    try:
        result = await spec.invoke({})
    finally:
        set_agent_context(None)

    assert result == {"items": [["2026-07-10", 3]]}
    assert ledger.has_valid_evidence({EvidenceType.INTERNAL_DATA})
    assert ledger.receipts[0].producer == "execute_sql_query"


@pytest.mark.asyncio
async def test_runtime_tool_does_not_record_empty_result_as_evidence():
    ledger = EvidenceLedger(user_id="1", conversation_id="conv-1")
    ctx = AgentContext(
        agent_id="data-agent",
        agent_name="chat-bi",
        grounding_evidence_ledger=ledger,
    )
    set_agent_context(ctx)
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="query data",
        parameters_schema={"type": "object"},
        source_type="static",
        callable=lambda: {"items": []},
        evidence_types=frozenset({EvidenceType.INTERNAL_DATA}),
    )

    try:
        await spec.invoke({})
    finally:
        set_agent_context(None)

    assert ledger.receipts == ()


@pytest.mark.asyncio
async def test_runtime_tool_allow_empty_success_records_empty_query_result():
    ledger = EvidenceLedger(user_id="1", conversation_id="conv-1")
    ctx = AgentContext(
        agent_id="data-agent",
        agent_name="chat-bi",
        grounding_evidence_ledger=ledger,
    )
    set_agent_context(ctx)
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="query data",
        parameters_schema={"type": "object"},
        source_type="static",
        callable=lambda: {"success": True, "items": []},
        evidence_types=frozenset({EvidenceType.INTERNAL_DATA}),
        evidence_policy="allow_empty_success",
    )

    try:
        await spec.invoke({})
    finally:
        set_agent_context(None)

    assert ledger.has_valid_evidence({EvidenceType.INTERNAL_DATA})
