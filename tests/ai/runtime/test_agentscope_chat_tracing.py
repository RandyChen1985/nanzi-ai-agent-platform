"""直调链路埋点的行为对照测试：属性正确，且返回值/异常语义完全不变。"""

import types

import pytest

from app.core.llm.client import AgentScopeLLMHandle
from app.services.ai.observability import generation_span, span_attributes
from app.services.ai.runtime.agentscope import chat

pytestmark = pytest.mark.no_infrastructure


def _user_message(text="hi"):
    """构造平台运行时的用户消息（AgentScopeChatClient 的入参类型）。

    注意不能传 dict：to_agentscope_messages 需要 RuntimeMessage 对象。
    """
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
    )

    return RuntimeMessage(
        role="user",
        content=[RuntimeContentBlock(type="text", text=text)],
    )


def test_chat_client_from_handle_carries_model_name_and_source():
    handle = AgentScopeLLMHandle(
        native_model=object(),
        model_name="gpt-x",
        temperature=0.7,
        streaming=False,
        source="chatbi.sql",
    )
    client = chat.chat_client_from_handle(handle)
    assert client.model_name == "gpt-x"
    assert client.source == "chatbi.sql"


def test_bind_tools_keeps_source():
    """bind_tools 返回新实例；漏传 source 会让「绑定工具后的调用」丢标签。"""
    handle = AgentScopeLLMHandle(
        native_model=object(),
        model_name="gpt-x",
        temperature=0.7,
        streaming=False,
        source="executor.tool",
    )
    rebound = handle.bind_tools([])
    assert rebound.source == "executor.tool"


def test_chat_client_from_bare_native_model_has_no_metadata():
    class _Native:
        pass

    client = chat.chat_client_from_handle(_Native())
    assert client.model_name is None
    assert client.source is None


class _RecordingSpan:
    def __init__(self, name, attributes):
        self.name = name
        self.attributes = dict(attributes or {})
        self.ended = 0

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def get_span_context(self):
        return types.SimpleNamespace(is_valid=True, trace_id=0xAB * 16)

    def end(self):
        self.ended += 1


class _RecordingTracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, attributes=None):
        span = _RecordingSpan(name, attributes)
        self.spans.append(span)
        return span


@pytest.fixture
def recording_spans(monkeypatch):
    """让 generation_span 走假 tracer 并认为追踪可用。"""
    tracer = _RecordingTracer()
    monkeypatch.setattr(generation_span, "is_tracing_enabled", lambda: True)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)
    monkeypatch.setattr(generation_span, "_current_parent_is_valid", lambda: False)
    return tracer


class _TextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    """模拟 AgentScope 的 ChatResponse。"""

    def __init__(self, text, usage=None, is_last=True):
        self.content = [_TextBlock(text)]
        self.usage = usage
        self.is_last = is_last


class _PlainNative:
    """一次性返回响应（非流式）的模型。"""

    def __init__(self, text="你好"):
        self.text = text
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        return _Response(self.text)


class _BoomNative:
    def __call__(self, messages, **kwargs):
        raise RuntimeError("模型炸了")


async def test_generate_message_creates_span_and_keeps_result(recording_spans):
    client = chat.AgentScopeChatClient(
        _PlainNative("你好"), model_name="gpt-x", source="unit.test"
    )
    result = await client.generate_message([_user_message()])

    assert result.content == "你好"
    assert len(recording_spans.spans) == 1
    span = recording_spans.spans[0]
    assert span.name == generation_span.SPAN_NAME
    assert span.attributes[generation_span.SOURCE_ATTRIBUTE] == "unit.test"
    assert span.ended == 1
    assert "你好" in span.attributes[span_attributes.GEN_AI_OUTPUT_MESSAGES]


async def test_generate_message_reraises_and_records_error(recording_spans):
    client = chat.AgentScopeChatClient(_BoomNative(), model_name="gpt-x")

    with pytest.raises(RuntimeError, match="模型炸了"):
        await client.generate_message([_user_message()])

    span = recording_spans.spans[0]
    assert span.ended == 1
    assert span.attributes[generation_span.ERROR_TYPE_ATTRIBUTE] == "RuntimeError"


class _StructuredBoomNative:
    async def generate_structured_output(self, **kwargs):
        raise RuntimeError("structured failed")


class _StructuredOkNative:
    async def generate_structured_output(self, **kwargs):
        # generate_structured_dict 直接接受 dict 形态的 content
        return types.SimpleNamespace(content={"a": 1})


async def test_generate_structured_dict_keeps_fail_open(recording_spans):
    """埋点不得改变 fail-open：异常仍被吞掉并返回 None。"""
    client = chat.AgentScopeChatClient(_StructuredBoomNative(), model_name="gpt-x")

    result = await client.generate_structured_dict(
        [_user_message()], structured_model=object()
    )

    assert result is None
    span = recording_spans.spans[0]
    assert span.ended == 1
    # 失败由状态属性表达，而不是异常
    assert span.attributes[generation_span.STRUCTURED_STATUS_ATTRIBUTE] in {
        "error",
        "structured_output_error",
        "invalid",
    }
    assert generation_span.ERROR_TYPE_ATTRIBUTE not in span.attributes


async def test_generate_structured_dict_records_success_status(recording_spans):
    client = chat.AgentScopeChatClient(_StructuredOkNative(), model_name="gpt-x")

    result = await client.generate_structured_dict(
        [_user_message()], structured_model=object()
    )

    assert result == {"a": 1}
    span = recording_spans.spans[0]
    assert span.attributes[generation_span.STRUCTURED_STATUS_ATTRIBUTE] == "success"


class _StreamingTextNative:
    """流式返回：增量块 + 完整终帧。"""

    def __call__(self, messages, **kwargs):
        async def _gen():
            yield _Response("你", is_last=False)
            yield _Response("你好", is_last=True)

        return _gen()


async def test_generate_text_keeps_result_and_records_output(recording_spans):
    client = chat.AgentScopeChatClient(_StreamingTextNative(), model_name="gpt-x")

    text = await client.generate_text([_user_message()])

    assert text == "你好"
    span = recording_spans.spans[0]
    assert span.ended == 1
    assert "你好" in span.attributes[span_attributes.GEN_AI_OUTPUT_MESSAGES]


async def test_generate_text_reraises_error(recording_spans):
    client = chat.AgentScopeChatClient(_BoomNative(), model_name="gpt-x")

    with pytest.raises(RuntimeError):
        await client.generate_text([_user_message()])

    assert recording_spans.spans[0].ended == 1


async def test_stream_messages_records_original_chunks_without_duplication(recording_spans):
    client = chat.AgentScopeChatClient(_StreamingTextNative(), model_name="gpt-x")

    chunks = [m.content async for m in client.stream_messages([_user_message()])]

    assert chunks == ["你", "你好"]  # 对外 chunk 序列不变
    span = recording_spans.spans[0]
    assert span.ended == 1
    out = span.attributes[span_attributes.GEN_AI_OUTPUT_MESSAGES]
    assert out.count("你好") == 1, "终帧必须覆盖增量，不能重复拼接"
    assert generation_span.FIRST_CHUNK_ATTRIBUTE in span.attributes


async def test_stream_messages_closes_span_on_early_break(recording_spans):
    """调用方提前 break 时 span 必须收尾，且不记为错误。"""
    client = chat.AgentScopeChatClient(_StreamingTextNative(), model_name="gpt-x")

    stream = client.stream_messages([_user_message()])
    async for _ in stream:
        break
    await stream.aclose()  # 显式关闭，不依赖 GC 时机

    span = recording_spans.spans[0]
    assert span.ended == 1
    assert generation_span.ERROR_TYPE_ATTRIBUTE not in span.attributes


class _MidStreamBoomNative:
    def __call__(self, messages, **kwargs):
        async def _gen():
            yield _Response("a", is_last=False)
            raise RuntimeError("流中断了")

        return _gen()


async def test_stream_messages_reraises_midstream_error(recording_spans):
    client = chat.AgentScopeChatClient(_MidStreamBoomNative(), model_name="gpt-x")

    with pytest.raises(RuntimeError, match="流中断了"):
        async for _ in client.stream_messages([_user_message()]):
            pass

    span = recording_spans.spans[0]
    assert span.ended == 1
    assert span.attributes[generation_span.ERROR_TYPE_ATTRIBUTE] == "RuntimeError"


async def test_stream_messages_creates_no_span_when_unused(recording_spans):
    """生成器未被消费 = 调用没发生，不应产生 span。"""
    client = chat.AgentScopeChatClient(_StreamingTextNative(), model_name="gpt-x")
    client.stream_messages([_user_message()])
    assert recording_spans.spans == []


async def test_child_call_does_not_rewrite_trace_name(monkeypatch):
    """轮次内的直调必须挂进同一 trace，且不能改写轮次 trace 的名称。

    这里模拟「已有活跃父 span」：span 归属交给 OTel，我们只断言不写 trace name。
    """
    tracer = _RecordingTracer()
    monkeypatch.setattr(generation_span, "is_tracing_enabled", lambda: True)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)
    monkeypatch.setattr(generation_span, "_current_parent_is_valid", lambda: True)

    client = chat.AgentScopeChatClient(
        _PlainNative("ok"), model_name="gpt-x", source="executor.tool"
    )
    await client.generate_message([_user_message()])

    span = tracer.spans[0]
    assert span_attributes.TRACE_NAME not in span.attributes
    assert span.attributes[generation_span.SOURCE_ATTRIBUTE] == "executor.tool"


async def test_no_span_when_tracing_disabled(monkeypatch):
    """追踪关闭时方法行为与无埋点版本一致（以 generate_message 为代表）。"""
    from app.services.ai.observability import manager, settings

    manager.reset_for_tests()
    settings.reset_for_tests()

    client = chat.AgentScopeChatClient(_PlainNative("ok"), model_name="gpt-x")
    result = await client.generate_message([_user_message()])
    assert result.content == "ok"
