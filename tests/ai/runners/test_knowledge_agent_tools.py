import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.agent import ChatConfig
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.runners.knowledge_agent_runner import (
    KNOWLEDGE_EXCLUDED_IMPLICIT_TOOLS,
    KnowledgeAgentRunner,
)

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_resolve_knowledge_tools_excludes_external_web_search():
    config = ChatConfig(
        agent_id="kb-agent",
        agent_name="知识库助手",
        model_name="test",
        temperature=0.0,
        system_prompt="kb",
        tools=[],
        capabilities=["knowledge_base"],
    )
    runner = KnowledgeAgentRunner(config=config, trace_id="t1", trace_buffer=[])

    mock_baidu = MagicMock()
    mock_baidu.name = "web_search_baidu"
    mock_fetch = MagicMock()
    mock_fetch.name = "fetch_static_web_url"
    mock_memory = MagicMock()
    mock_memory.name = "memory_search"
    mock_kb = MagicMock()
    mock_kb.name = "search_knowledge_base"

    def _as_spec(tool, **kwargs):
        from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

        return runtime_tool_spec_from_legacy_tool(tool, source_type=kwargs.get("source_type", "system"))

    with patch(
        "app.services.ai.runners.knowledge_agent_runner.ToolRegistry.get_system_implicit_tools",
        return_value=[mock_baidu, mock_fetch, mock_memory],
    ), patch(
        "app.services.ai.runners.knowledge_agent_runner.ToolRegistry.get_runtime_tool",
        new_callable=AsyncMock,
        return_value=mock_kb,
    ), patch(
        "app.services.ai.runners.knowledge_agent_runner.runtime_tool_spec_from_legacy_tool",
        side_effect=_as_spec,
    ):
        tools = await runner._resolve_knowledge_tools()

    names = {tool.name for tool in tools}
    assert "search_knowledge_base" in names
    assert "memory_search" in names
    assert not names & KNOWLEDGE_EXCLUDED_IMPLICIT_TOOLS
    memory_tool = next(tool for tool in tools if tool.name == "memory_search")
    assert memory_tool.evidence_types == frozenset({EvidenceType.CONVERSATION_MEMORY})
