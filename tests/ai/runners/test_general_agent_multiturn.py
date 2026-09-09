import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def isolate_general_runtime(monkeypatch):
    async def _no_redis():
        return None

    @asynccontextmanager
    async def _noop_session_lock_hold(**kwargs):
        yield True

    monkeypatch.setattr("app.core.redis.get_redis", _no_redis)
    monkeypatch.setattr("app.services.config_service.get_redis", _no_redis)
    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.get_local_workspace",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.agentscope_session_lock.hold",
        _noop_session_lock_hold,
    )


@pytest.mark.asyncio
async def test_general_runner_uses_configured_tools_only_when_workspace_exists(monkeypatch):
    from unittest.mock import MagicMock

    from app.schemas.agent import ChatConfig
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    fake_workspace = MagicMock()
    fake_toolkit = MagicMock()
    build_toolkit = MagicMock(return_value=fake_toolkit)
    captured_agent_kwargs: dict = {}

    def fake_agent(**kwargs):
        captured_agent_kwargs.update(kwargs)
        inst = MagicMock()
        inst.name = "AgentInstance"
        return inst

    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.get_local_workspace",
        AsyncMock(return_value=fake_workspace),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.build_toolkit",
        build_toolkit,
    )
    monkeypatch.setattr(
        "agentscope.agent.Agent",
        fake_agent,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.load_context_config",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.build_model_config",
        AsyncMock(return_value=None),
    )

    config = ChatConfig(
        agent_id="general-agent-id",
        agent_name="GeneralAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a general agent.",
        tools=["search_knowledge_base"],
    )
    runner = AssistantAgentRunner(
        config=config,
        trace_id="trace-toolkit",
        trace_buffer=[],
        conversation_id="conv-1",
    )
    tools = [
        RuntimeToolSpec(
            name="search_knowledge_base",
            description="kb",
            parameters_schema={"type": "object", "properties": {}},
            source_type="static",
            callable=AsyncMock(return_value="ok"),
            permission_scope="read",
        )
    ]
    agent = await runner._build_native_agent(
        native_model=MagicMock(model="fake"),
        tools=tools,
        system_content="system",
        max_steps=3,
        primary_model_name="fake-model",
    )

    build_toolkit.assert_called_once()
    assert captured_agent_kwargs["toolkit"] is fake_toolkit
    assert captured_agent_kwargs["offloader"] is fake_workspace
    assert agent.name == "AgentInstance"


@pytest.mark.asyncio
async def test_general_turn_exposes_configured_knowledge_search_tool(monkeypatch):
    from app.schemas.agent import ChatConfig
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
    from app.services.ai.turn_decision import TurnDecision

    config = ChatConfig(
        agent_id="general-agent-id",
        agent_name="GeneralAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a general agent.",
        tools=["search_knowledge_base", "get_current_time"],
    )
    runner = AssistantAgentRunner(
        config=config,
        trace_id="trace-general-tool-gate",
        trace_buffer=[],
        conversation_id="conv-1",
        turn_decision=TurnDecision(
            route_status="resolved",
            turn_kind="general",
            source="general",
            capability="answer",
        ),
    )

    captured_tool_names = []

    async def fake_get_runtime_tools(tool_configs):
        captured_tool_names.extend(
            item if isinstance(item, str) else getattr(item, "name", "")
            for item in tool_configs
        )
        return [
            RuntimeToolSpec(
                name="get_current_time",
                description="current time",
                parameters_schema={"type": "object", "properties": {}},
                source_type="static",
                callable=AsyncMock(return_value="2026-07-29"),
                permission_scope="read",
            )
        ]

    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.ToolRegistry.get_runtime_tools",
        fake_get_runtime_tools,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.ToolRegistry.get_system_implicit_tools",
        lambda: [],
    )

    await runner._resolve_runtime_tools_from_config()

    assert captured_tool_names == ["search_knowledge_base", "get_current_time"]


@pytest.mark.asyncio
async def test_general_runner_second_turn_skips_repeat_read_with_restored_state():
    """恢复 AgentState 后，第二轮不应再次触发 Read 工具调用。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.state import AgentState

    from unittest.mock import MagicMock

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.agent_runtime import build_tools_fingerprint
    from app.services.ai.runtime.agentscope.state_store import RuntimeStateEnvelope, SCHEMA_VERSION
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
    from app.schemas.agent import ChatConfig

    config = ChatConfig(
        agent_id="general-agent-id",
        agent_name="GeneralAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a general agent.",
        tools=["Read"],
    )

    read_invocations: list[int] = []

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.formatter = type("FakeFormatter", (), {"supported_input_media_types": ["image", "text"]})()

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            tool_results = [
                block
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            ]
            read_invocations.append(len(tool_results))
            if not tool_results:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_read",
                            name="Read",
                            input='{"path": "/tmp/demo.txt"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(content=[TextBlock(text="second turn answer")], is_last=True)

    async def read_tool(path: str):
        return f"file:{path}"

    runtime_spec = RuntimeToolSpec(
        name="Read",
        description="Read file",
        parameters_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        source_type="native",
        callable=read_tool,
        permission_scope="read",
    )

    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-general",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-general",
        temperature=0.0,
        streaming=True,
    )

    restored_state = AgentState.model_validate(
        {
            "session_id": "session-multiturn",
            "reply_id": "reply-multiturn",
            "context": [
                {
                    "name": "user",
                    "role": "user",
                    "content": [{"type": "text", "text": "first question"}],
                },
                {
                    "name": "assistant",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_call",
                            "id": "call_read",
                            "name": "Read",
                            "input": '{"path": "/tmp/demo.txt"}',
                        }
                    ],
                },
                {
                    "name": "assistant",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_result",
                            "id": "call_read",
                            "name": "Read",
                            "output": "file:/tmp/demo.txt",
                        }
                    ],
                },
                {
                    "name": "assistant",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "first answer"}],
                },
            ],
        }
    )

    fingerprint = build_tools_fingerprint(config, [runtime_spec])
    saved_envelope = RuntimeStateEnvelope(
        schema_version=SCHEMA_VERSION,
        agent_name=config.agent_name,
        agent_version=config.agent_version,
        tools_fingerprint=fingerprint,
        model_name="fake-native-general",
        updated_at="2026-06-09T00:00:00Z",
        state=restored_state.model_dump(mode="json"),
    )

    runner = AssistantAgentRunner(
        config=config,
        trace_id="trace-multiturn",
        trace_buffer=[],
        user_info={"user_id": "10001"},
        conversation_id="c-multiturn",
    )

    fake_workspace = MagicMock()
    with patch(
        "app.services.ai.runners.assistant_agent_runner.get_local_workspace",
        AsyncMock(return_value=fake_workspace),
    ), patch(
        "app.services.ai.runners.assistant_agent_runner.bind_configured_tools_to_workspace",
        AsyncMock(return_value=[runtime_spec]),
    ), patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=handle),
    ), patch(
        "app.services.ai.tools.registry.ToolRegistry.get_runtime_tools",
        AsyncMock(return_value=[runtime_spec]),
    ), patch(
        "app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools",
        return_value=[],
    ), patch(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value="5"),
    ), patch(
        "app.services.ai.runners.assistant_agent_runner.agent_state_store.load",
        AsyncMock(return_value=saved_envelope),
    ), patch(
        "app.services.ai.runners.assistant_agent_runner.agent_state_store.save",
        AsyncMock(return_value=None),
    ):
        turn2_events = []
        async for chunk in runner.execute([
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow up"},
        ]):
            turn2_events.append(chunk)

    assert read_invocations == [1]
    assert any(
        chunk.get("content") == "second turn answer"
        for chunk in turn2_events
        if "content" in chunk
    )


@pytest.mark.asyncio
async def test_assistant_agent_runner_prompt_layout_stable_before_dynamic_and_no_duplicate_route():
    """AssistantAgentRunner 稳定系统提示词必须先于动态路由和安全模式，且路由提示不重复。"""
    from unittest.mock import MagicMock
    from app.schemas.agent import ChatConfig
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.turn_decision import TurnDecision

    config = ChatConfig(
        agent_id="test-agent",
        agent_name="test_bot",
        system_prompt="STABLE_BOT_PROMPT: You are a helpful assistant.",
        engine_type="LOCAL",
        model_name="test-model",
        temperature=0.7,
        tools=[],
    )
    turn_decision = TurnDecision(
        route_status="resolved",
        turn_kind="chat",
        capability="chat",
        turn_labels=["chat"],
        user_action_type="chat",
    )

    captured_system_messages = []

    class FakeSimpleLLM:
        async def astream(self, messages):
            for m in messages:
                if getattr(m, "content", None) and "STABLE_BOT_PROMPT" in str(m.content):
                    captured_system_messages.append(str(m.content))
            chunk = MagicMock()
            chunk.content = "simple reply"
            yield chunk

    runner = AssistantAgentRunner(
        config=config,
        trace_id="trace-layout",
        trace_buffer=[],
        turn_decision=turn_decision,
        debug_options={
            "grounding_enabled": True,
            "grounding_action": {"type": "method"},
        },
    )

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=FakeSimpleLLM()),
    ), patch.object(runner, "_resolve_runtime_tools_from_config", AsyncMock(return_value=[])):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "hello"}]):
            events.append(chunk)

    assert len(captured_system_messages) >= 1
    system_text = captured_system_messages[0]
    stable_idx = system_text.index("STABLE_BOT_PROMPT")
    # 动态安全规则和路由决策必须存在，且稳定提示词必须在动态内容之前
    assert "【安全回答模式】" in system_text
    assert stable_idx < system_text.index("【安全回答模式】")
    assert "【本轮执行决策（仅供参考）】" in system_text
    assert stable_idx < system_text.index("【本轮执行决策（仅供参考）】")
    # 路由决策快照只出现一次
    assert system_text.count("【本轮执行决策（仅供参考）】") == 1
