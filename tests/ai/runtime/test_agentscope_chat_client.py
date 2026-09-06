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
    # AgentScope 流式契约：增量块(is_last=False)逐块射出，末尾由基类 __call__ 的
    # 累积器补发一个携带完整正文的终帧(is_last=True)。终帧 content 是整段累积
    # 后的完整结果，而不是最后一块增量——不能把终帧再次拼到已累积文本之后。
    from agentscope.message import TextBlock
    from agentscope.model import ChatResponse

    from app.services.ai.runtime.agentscope.chat import AgentScopeChatClient
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    async def fake_stream() -> AsyncIterator[ChatResponse]:
        yield ChatResponse(content=[TextBlock(text="partial")], is_last=False)
        # 终帧按契约携带完整正文（= 全部增量拼合后的结果）
        yield ChatResponse(content=[TextBlock(text="partialfinal answer")], is_last=True)

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
async def test_generate_text_does_not_double_count_complete_terminal_frame():
    # 回归（P1）：旧逻辑在流式下无条件累加每个 chunk 的 content，导致“增量汇总 +
    # 完整终帧”被重复拼接（例如最终正文出现两遍）。契约下终帧 content 已是完整
    # 正文，必须用终帧覆盖累加的增量，而不是再次追加。
    from agentscope.message import TextBlock
    from agentscope.model import ChatResponse

    from app.services.ai.runtime.agentscope.chat import AgentScopeChatClient
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    async def fake_stream() -> AsyncIterator[ChatResponse]:
        yield ChatResponse(content=[TextBlock(text="今天天气")], is_last=False)
        yield ChatResponse(content=[TextBlock(text=" 不错，适合出门")], is_last=False)
        # 终帧 = 完整累积结果；若再逐块累加会被重复拼接两次
        yield ChatResponse(
            content=[TextBlock(text="今天天气 不错，适合出门")],
            is_last=True,
        )

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

    # 结果是完整终帧（恰好等于增量拼合），绝不能把终帧再追加到增量之后
    assert text == "今天天气 不错，适合出门"


@pytest.mark.asyncio
async def test_generate_message_does_not_double_count_complete_terminal_frame():
    # 回归（P1 扩展）：generate_message 走流式时同样必须区分增量块与完整终帧，
    # 否则会把完整终帧追加到已拼合的增量之后，造成重复。
    from agentscope.message import TextBlock
    from agentscope.model import ChatResponse

    from app.services.ai.runtime.agentscope.chat import AgentScopeChatClient
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    async def fake_stream() -> AsyncIterator[ChatResponse]:
        yield ChatResponse(content=[TextBlock(text="你好，")], is_last=False)
        yield ChatResponse(content=[TextBlock(text="世界")], is_last=False)
        yield ChatResponse(content=[TextBlock(text="你好，世界")], is_last=True)

    class FakeNativeModel:
        async def __call__(self, messages, tools=None, **kwargs):
            return fake_stream()

    client = AgentScopeChatClient(FakeNativeModel())

    msg = await client.generate_message(
        [
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text="打招呼")],
            )
        ]
    )

    assert msg.content == "你好，世界"


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
