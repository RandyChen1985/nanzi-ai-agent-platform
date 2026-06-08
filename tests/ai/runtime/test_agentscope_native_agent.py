from __future__ import annotations

import pytest
from pydantic import BaseModel


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_agentscope_agent_executes_runtime_tool_via_native_toolkit():
    from agentscope.agent import Agent
    from agentscope.agent import ReActConfig
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock, UserMsg
    from agentscope.model import ChatModelBase, ChatResponse

    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, build_toolkit

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            if not any(msg.has_content_blocks("tool_result") for msg in messages):
                assert tools
                assert tools[0]["function"]["name"] == "lookup_status"
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_native_1",
                            name="lookup_status",
                            input='{"name": "general"}',
                        )
                    ],
                    is_last=True,
                )

            tool_result = next(
                block
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            )
            return ChatResponse(
                content=[TextBlock(text=f"native final: {tool_result.output[0].text}")],
                is_last=True,
            )

    async def lookup_status(name: str) -> str:
        return f"{name}:ok"

    toolkit = build_toolkit(
        [
            RuntimeToolSpec(
                name="lookup_status",
                description="Lookup status",
                parameters_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                source_type="static",
                callable=lookup_status,
                permission_scope="read",
            )
        ]
    )
    agent = Agent(
        name="general",
        system_prompt="You are a general assistant.",
        model=FakeModel(
            credential=FakeCredential(),
            model="fake-native",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        toolkit=toolkit,
        react_config=ReActConfig(max_iters=3),
    )

    events = []
    async for event in agent.reply_stream(UserMsg("user", "check status")):
        events.append(event)

    assert [event.type for event in events].count("TOOL_RESULT_END") == 1
    text_events = [event for event in events if event.type == "TEXT_BLOCK_DELTA"]
    assert text_events[-1].delta == "native final: general:ok"
