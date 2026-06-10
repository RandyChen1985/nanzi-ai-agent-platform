from __future__ import annotations

from typing import AsyncIterator

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_runtime_messages_convert_to_agentscope_msgs():
    from app.services.ai.runtime.agentscope.chat import to_agentscope_messages
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    messages = to_agentscope_messages(
        [
            RuntimeMessage(
                role="system",
                content=[RuntimeContentBlock(type="text", text="system prompt")],
            ),
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text="hello")],
            ),
            RuntimeMessage(
                role="assistant",
                content=[RuntimeContentBlock(type="text", text="hi")],
            ),
            RuntimeMessage(
                role="tool",
                content=[RuntimeContentBlock(type="text", text="tool result")],
            ),
        ]
    )

    assert [msg.role for msg in messages] == ["system", "user", "assistant", "user"]
    assert messages[0].get_text_content() == "system prompt"
    assert messages[-1].get_text_content() == "Tool result from tool: tool result"


def test_runtime_tool_spec_converts_to_openai_tool_schema():
    from app.services.ai.runtime.agentscope.chat import legacy_tools_to_openai_schemas
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    spec = RuntimeToolSpec(
        name="runtime_lookup",
        description="Lookup runtime data",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        source_type="static",
        callable=lambda query: query,
    )

    schema = legacy_tools_to_openai_schemas([spec])[0]

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "runtime_lookup"
    assert schema["function"]["parameters"]["required"] == ["query"]


@pytest.mark.asyncio
async def test_chat_client_extracts_non_streaming_text():
    from agentscope.message import TextBlock
    from agentscope.model import ChatResponse

    from app.services.ai.runtime.agentscope.chat import AgentScopeChatClient
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    class FakeNativeModel:
        async def __call__(self, messages, **kwargs):
            self.messages = messages
            self.kwargs = kwargs
            return ChatResponse(
                content=[TextBlock(text="route-json")],
                is_last=True,
            )

    client = AgentScopeChatClient(FakeNativeModel())

    text = await client.generate_text(
        [
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text="route me")],
            )
        ]
    )

    assert text == "route-json"


@pytest.mark.asyncio
async def test_chat_client_collects_streaming_final_text():
    from agentscope.message import TextBlock
    from agentscope.model import ChatResponse

    from app.services.ai.runtime.agentscope.chat import AgentScopeChatClient
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    async def fake_stream() -> AsyncIterator[ChatResponse]:
        yield ChatResponse(content=[TextBlock(text="partial")], is_last=False)
        yield ChatResponse(content=[TextBlock(text="final answer")], is_last=True)

    class FakeNativeModel:
        async def __call__(self, messages, **kwargs):
            return fake_stream()

    client = AgentScopeChatClient(FakeNativeModel())

    text = await client.generate_text(
        [
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text="summarize")],
            )
        ]
    )

    assert text == "final answer"
