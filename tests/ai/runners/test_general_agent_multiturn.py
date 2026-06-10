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
        return MagicMock(name="AgentInstance")

    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.get_local_workspace",
        AsyncMock(return_value=fake_workspace),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.build_toolkit",
        build_toolkit,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.assistant_agent_runner.Agent",
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
async def test_general_runner_second_turn_skips_repeat_read_with_restored_state():
    """恢复 AgentState 后，第二轮不应再次触发 Read 工具调用。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.state import AgentState

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
        user_info={"user_id": "u-multiturn"},
        conversation_id="c-multiturn",
    )

    with patch(
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
        if "content" in chunk and "type" not in chunk
    )
