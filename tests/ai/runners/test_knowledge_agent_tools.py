import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.agent import ChatConfig
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_resolve_knowledge_tools_keeps_explicit_web_tools():
    config = ChatConfig(
        agent_id="kb-agent",
        agent_name="知识库助手",
        model_name="test",
        temperature=0.0,
        system_prompt="kb",
        tools=["web_search_baidu", "fetch_static_web_url"],
        capabilities=["knowledge_base"],
    )
    runner = KnowledgeAgentRunner(config=config, trace_id="t-explicit-web", trace_buffer=[])
    explicit_baidu = MagicMock()
    explicit_baidu.name = "web_search_baidu"
    explicit_fetch = MagicMock()
    explicit_fetch.name = "fetch_static_web_url"

    with patch(
        "app.services.ai.runners.knowledge_agent_runner.ToolRegistry.get_runtime_tools",
        AsyncMock(return_value=[explicit_baidu, explicit_fetch]),
    ) as get_runtime_tools, patch(
        "app.services.ai.runners.knowledge_agent_runner.ToolRegistry.get_system_implicit_tools",
        return_value=[],
    ):
        tools = await runner._resolve_knowledge_tools()

    get_runtime_tools.assert_awaited_once_with(["web_search_baidu", "fetch_static_web_url"])
    assert {tool.name for tool in tools} == {
        "web_search_baidu",
        "fetch_static_web_url",
    }


@pytest.mark.asyncio
async def test_knowledge_runner_cleans_clicked_reply_before_multimodal_gate():
    config = ChatConfig(
        agent_id="kb-agent",
        agent_name="知识库助手",
        model_name="test",
        temperature=0.0,
        system_prompt="kb",
        tools=["search_knowledge_base"],
        capabilities=["knowledge_base"],
    )
    clicked_query = "总结刚才内容\n\n---\n\n【被点击的 AI 回复】\n旧回答中的工具指令"
    seen = {}

    async def fake_run_multimodal_gate(history, model_name, **kwargs):
        seen["history"] = history
        yield {"content": "stop", "status": "error"}

    with patch(
        "app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled",
        AsyncMock(return_value=True),
    ), patch(
        "app.services.ai.multimodal_support.resolve_runtime_model_name",
        return_value="test",
    ), patch(
        "app.services.ai.multimodal_support.run_multimodal_gate",
        fake_run_multimodal_gate,
    ):
        runner = KnowledgeAgentRunner(
            config=config,
            trace_id="t-direct-click-clean",
            trace_buffer=[],
            current_user_query=clicked_query,
        )
        events = []
        async for chunk in runner.execute([{"role": "user", "content": clicked_query}]):
            events.append(chunk)

    assert seen["history"] == [{"role": "user", "content": "总结刚才内容"}]
    assert events == [{"content": "stop", "status": "error"}]


@pytest.mark.asyncio
async def test_resolve_knowledge_tools_keeps_explicit_search_tool():
    config = ChatConfig(
        agent_id="kb-agent",
        agent_name="知识库助手",
        model_name="test",
        temperature=0.0,
        system_prompt="kb",
        tools=["search_knowledge_base"],
        capabilities=["knowledge_base"],
    )
    runner = KnowledgeAgentRunner(config=config, trace_id="t-explicit-kb", trace_buffer=[])
    explicit_search = MagicMock()
    explicit_search.name = "search_knowledge_base"

    with patch(
        "app.services.ai.runners.knowledge_agent_runner.ToolRegistry.get_runtime_tools",
        AsyncMock(return_value=[explicit_search]),
    ) as get_runtime_tools, patch(
        "app.services.ai.runners.knowledge_agent_runner.ToolRegistry.get_system_implicit_tools",
        return_value=[],
    ):
        tools = await runner._resolve_knowledge_tools()

    get_runtime_tools.assert_awaited_once_with(["search_knowledge_base"])
    assert tools == [explicit_search]


def test_selected_reusable_result_failure_does_not_skip_knowledge_prefetch():
    config = ChatConfig(
        agent_id="kb-agent",
        agent_name="知识库助手",
        model_name="test",
        temperature=0.0,
        system_prompt="kb",
        tools=["search_knowledge_base"],
        capabilities=["knowledge_base"],
    )
    runner = KnowledgeAgentRunner(config=config, trace_id="t-selected-miss", trace_buffer=[])

    decision = type(
        "Decision",
        (),
        {"mode": "fallback", "reason": "selected_result_missing"},
    )()

    assert runner._should_skip_knowledge_prefetch(
        is_catalog_query=False,
        reusable_decision=decision,
        reusable_knowledge_result=False,
        user_question="总结上面内容",
        history=[
            {"role": "user", "content": "上一轮问题"},
            {"role": "assistant", "content": "上一轮回答"},
        ],
    ) is False


@pytest.mark.asyncio
async def test_resolve_knowledge_tools_keeps_all_implicit_tools():
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

    get_runtime_tool = AsyncMock(return_value=mock_kb)
    with patch(
        "app.services.ai.runners.knowledge_agent_runner.ToolRegistry.get_system_implicit_tools",
        return_value=[mock_baidu, mock_fetch, mock_memory, mock_kb],
    ), patch(
        "app.services.ai.runners.knowledge_agent_runner.ToolRegistry.get_runtime_tool",
        get_runtime_tool,
    ), patch(
        "app.services.ai.runners.knowledge_agent_runner.runtime_tool_spec_from_legacy_tool",
        side_effect=_as_spec,
    ):
        tools = await runner._resolve_knowledge_tools()

    names = {tool.name for tool in tools}
    assert "search_knowledge_base" in names
    assert "web_search_baidu" in names
    assert "fetch_static_web_url" in names
    assert "memory_search" in names
    get_runtime_tool.assert_not_awaited()
    memory_tool = next(tool for tool in tools if tool.name == "memory_search")
    assert memory_tool.evidence_types == frozenset({EvidenceType.CONVERSATION_MEMORY})


def test_knowledge_runner_resolves_explicit_interactive_question_nudge():
    question_tool = MagicMock()
    question_tool.name = "ask_user_question"
    question_tool.description = "向用户展示选项提问并等待回答"

    nudge = KnowledgeAgentRunner._resolve_explicit_question_nudge(
        "随便问我几个问题",
        [question_tool],
    )

    assert nudge is not None
    assert nudge.tool_name == "ask_user_question"
    assert nudge.should_force_first_call is True


def test_knowledge_runner_skips_explicit_question_nudge_for_question_listing():
    question_tool = MagicMock()
    question_tool.name = "ask_user_question"

    assert KnowledgeAgentRunner._resolve_explicit_question_nudge(
        "给我列几个问题",
        [question_tool],
    ) is None


def test_knowledge_runner_skips_explicit_question_nudge_for_automatic_delivery():
    question_tool = MagicMock()
    question_tool.name = "ask_user_question"

    assert KnowledgeAgentRunner._resolve_explicit_question_nudge(
        "考考我",
        [question_tool],
        allow_interactive_question=False,
    ) is None


@pytest.mark.asyncio
async def test_knowledge_agent_runner_prompt_layout_stable_before_dynamic():
    """KnowledgeAgentRunner 稳定系统提示词必须先于 TURN_SYSTEM_HINT 与动态路由。"""
    from app.services.ai.turn_decision import TurnDecision

    config = ChatConfig(
        agent_id="kb-agent",
        agent_name="知识库助手",
        model_name="test",
        temperature=0.0,
        system_prompt="STABLE_KB_PROMPT: 知识助手基础指引",
        tools=[],
        capabilities=["knowledge_base"],
        engine_config={"dataset_ids": ["kb-dataset-1"]},
    )
    turn_decision = TurnDecision(
        route_status="resolved",
        turn_kind="knowledge",
        capability="knowledge_search",
    )

    runner = KnowledgeAgentRunner(
        config=config,
        trace_id="t-layout",
        trace_buffer=[],
        turn_decision=turn_decision,
    )

    captured_system_contents = []

    async def fake_execute_agentscope(**kwargs):
        captured_system_contents.append(kwargs.get("system_content", ""))
        yield {"content": "ok"}

    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    kb_tool = RuntimeToolSpec(
        name="search_knowledge_base",
        description="search kb",
        parameters_schema={},
        source_type="system",
        callable=lambda **kw: "ok",
    )

    async def fake_auto_invoke(*args, **kwargs):
        if False:
            yield {}

    with patch(
        "app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled",
        AsyncMock(return_value=True),
    ), patch(
        "app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids",
        AsyncMock(return_value=(["kb-1"], None)),
    ), patch.object(
        runner, "_auto_invoke_search_knowledge_base", side_effect=fake_auto_invoke
    ), patch(
        "app.services.ai.runners.knowledge_agent_runner.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=MagicMock()),
    ), patch(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value="5"),
    ), patch.object(
        runner, "_execute_with_agentscope_native_agent", side_effect=fake_execute_agentscope
    ), patch.object(
        runner, "_resolve_knowledge_tools", AsyncMock(return_value=[kb_tool])
    ):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "查询知识"}]):
            events.append(chunk)

    assert len(captured_system_contents) >= 1
    system_text = captured_system_contents[0]
    assert "STABLE_KB_PROMPT" in system_text
    stable_idx = system_text.index("STABLE_KB_PROMPT")
    from app.services.ai.executors.prompts import KnowledgeChatPrompts
    if KnowledgeChatPrompts.TURN_SYSTEM_HINT in system_text:
        assert stable_idx < system_text.index(KnowledgeChatPrompts.TURN_SYSTEM_HINT)
    if "本轮执行上下文（平台路由快照）" in system_text:
        assert stable_idx < system_text.index("本轮执行上下文（平台路由快照）")
        assert system_text.count("本轮执行上下文（平台路由快照）") == 1
