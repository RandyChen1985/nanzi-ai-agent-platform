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

    assert text == "partialfinal answer"


@pytest.mark.asyncio
async def test_generate_text_accumulates_all_stream_deltas():
    # 回归：旧逻辑只在遭遇 is_last 标记时整体覆盖、非首块增量会被丢弃。
    # 此处所有增量块均不携带 is_last（末端才作为哨兵），累积结果必须完整。
    from agentscope.message import TextBlock
    from agentscope.model import ChatResponse

    from app.services.ai.runtime.agentscope.chat import AgentScopeChatClient
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    async def fake_stream() -> AsyncIterator[ChatResponse]:
        for part in ("hello ", "world", "!"):
            yield ChatResponse(content=[TextBlock(text=part)], is_last=False)
        yield ChatResponse(content=[], is_last=True)

    class FakeNativeModel:
        async def __call__(self, messages, **kwargs):
            return fake_stream()

    client = AgentScopeChatClient(FakeNativeModel())

    text = await client.generate_text(
        [
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text="say hi")],
            )
        ]
    )

    assert text == "hello world!"


@pytest.mark.asyncio
async def test_generate_text_accumulates_deltas_after_is_last_sentinel():
    # is_last 仅是哨兵，正文增量依旧逐块累积，不能被末块覆盖而截断。
    from agentscope.message import TextBlock
    from agentscope.model import ChatResponse

    from app.services.ai.runtime.agentscope.chat import AgentScopeChatClient
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    async def fake_stream() -> AsyncIterator[ChatResponse]:
        yield ChatResponse(content=[TextBlock(text="first")], is_last=False)
        yield ChatResponse(content=[TextBlock(text=" -mid")], is_last=True)
        yield ChatResponse(content=[TextBlock(text=" -tail")], is_last=False)

    class FakeNativeModel:
        async def __call__(self, messages, **kwargs):
            return fake_stream()

    client = AgentScopeChatClient(FakeNativeModel())

    text = await client.generate_text(
        [
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text="x")],
            )
        ]
    )

    assert text == "first -mid -tail"


@pytest.mark.asyncio
async def test_chat_client_generate_structured_dict_fail_open_and_success():
    from pydantic import BaseModel, Field

    from app.services.ai.runtime.agentscope.chat import AgentScopeChatClient
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    class Payload(BaseModel):
        agent_name: str = Field(default="")
        confidence: float = Field(default=0.0)

    class BrokenNative:
        async def generate_structured_output(self, **kwargs):
            raise RuntimeError("boom")

    broken = AgentScopeChatClient(BrokenNative())
    assert (
        await broken.generate_structured_dict(
            [
                RuntimeMessage(
                    role="user",
                    content=[RuntimeContentBlock(type="text", text="x")],
                )
            ],
            Payload,
        )
        is None
    )

    class OkNative:
        async def generate_structured_output(self, **kwargs):
            assert "structured_model" in kwargs
            assert kwargs["messages"]
            return type("R", (), {"content": {"agent_name": "main", "confidence": 0.9}})()

    ok = AgentScopeChatClient(OkNative())
    payload = await ok.generate_structured_dict(
        [
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text="x")],
            )
        ],
        Payload,
    )
    assert payload == {"agent_name": "main", "confidence": 0.9}


@pytest.mark.asyncio
async def test_chat_client_records_structured_output_status_without_falling_back():
    from app.services.ai.runtime.agentscope.chat import AgentScopeChatClient

    class UnsupportedNative:
        pass

    unsupported = AgentScopeChatClient(UnsupportedNative())
    assert (
        await unsupported.generate_structured_dict([], object())
    ) is None
    assert unsupported.last_structured_output_status == "unsupported"

    class BrokenNative:
        async def generate_structured_output(self, **kwargs):
            raise RuntimeError("boom")

    broken = AgentScopeChatClient(BrokenNative())
    assert await broken.generate_structured_dict([], object()) is None
    assert broken.last_structured_output_status == "error"

    class InvalidNative:
        async def generate_structured_output(self, **kwargs):
            return type("R", (), {"content": "not-json"})()

    invalid = AgentScopeChatClient(InvalidNative())
    assert await invalid.generate_structured_dict([], object()) is None
    assert invalid.last_structured_output_status == "invalid"

    class JsonTextNative:
        async def generate_structured_output(self, **kwargs):
            return type(
                "R",
                (),
                {"content": '{"agent_name":"main","confidence":0.9}'},
            )()

    json_text = AgentScopeChatClient(JsonTextNative())
    assert await json_text.generate_structured_dict([], object()) == {
        "agent_name": "main",
        "confidence": 0.9,
    }
    assert json_text.last_structured_output_status == "success"
