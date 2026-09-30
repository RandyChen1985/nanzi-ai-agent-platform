"""直调 generation span 的单元测试：零开销、属性映射、累积、收尾。

不构造真实 Langfuse client（会注册全局 TracerProvider），全部用假 tracer 注入。
"""

import types

import pytest

from app.services.ai.observability import generation_span, manager, settings, span_attributes

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _reset():
    manager.reset_for_tests()
    settings.reset_for_tests()
    yield
    manager.reset_for_tests()
    settings.reset_for_tests()
    generation_span.reset_for_tests()


class _RecordingSpan:
    def __init__(self, name, attributes):
        self.name = name
        self.attributes = dict(attributes or {})
        self.ended = 0
        self.context = types.SimpleNamespace(trace_id=0xAB * 16)

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def get_span_context(self):
        return self.context

    def end(self):
        self.ended += 1


class _RecordingTracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, attributes=None):
        span = _RecordingSpan(name, attributes)
        self.spans.append(span)
        return span


class _OccupiedSpan:
    """模拟“当前已有活跃 span”（即处于某个轮次内）。"""

    def get_span_context(self):
        return types.SimpleNamespace(is_valid=True)


def _enable_tracing(monkeypatch):
    monkeypatch.setattr(generation_span, "is_tracing_enabled", lambda: True)


async def test_no_span_when_tracing_disabled(monkeypatch):
    """追踪关闭时必须零 span 创建（冷启动零开销的依据）。"""
    tracer = _RecordingTracer()
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)
    monkeypatch.setattr(generation_span, "is_tracing_enabled", lambda: False)

    async with generation_span.traced_generation(
        model="gpt-x",
        streaming=False,
        source=None,
        input_messages=[{"role": "user", "content": "hi"}],
    ) as span:
        assert span.active is False
        span.record_output("unused")
        span.end(error=RuntimeError("ignored"))

    assert tracer.spans == []


def _start(
    monkeypatch,
    tracer,
    *,
    source=None,
    model="gpt-x",
    streaming=False,
    input_messages=None,
    has_parent=False,
):
    _enable_tracing(monkeypatch)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)
    monkeypatch.setattr(generation_span, "_current_parent_is_valid", lambda: has_parent)
    return generation_span.start_generation_span(
        model=model,
        streaming=streaming,
        source=source,
        input_messages=(
            input_messages
            if input_messages is not None
            else [{"role": "user", "content": "hi"}]
        ),
    )


def test_span_name_and_generation_attributes(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=True)
    handle.end()

    span = tracer.spans[0]
    assert span.name == generation_span.SPAN_NAME
    assert span.attributes[span_attributes.OBSERVATION_TYPE] == "generation"
    assert span.attributes[span_attributes.GEN_AI_OPERATION_NAME] == "chat"
    assert span.attributes[span_attributes.GEN_AI_REQUEST_MODEL] == "gpt-x"
    assert span.attributes[span_attributes.OBSERVATION_MODEL] == "gpt-x"
    assert span.attributes[generation_span.STREAMING_ATTRIBUTE] is True
    assert "hi" in span.attributes[span_attributes.GEN_AI_INPUT_MESSAGES]


def test_root_span_writes_trace_name_from_source(monkeypatch):
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, source="chatbi.sql", has_parent=False).end()
    assert tracer.spans[0].attributes[span_attributes.TRACE_NAME] == "chatbi.sql"


def test_root_span_without_source_uses_span_name(monkeypatch):
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, source=None, has_parent=False).end()
    assert tracer.spans[0].attributes[span_attributes.TRACE_NAME] == generation_span.SPAN_NAME


def test_child_span_never_writes_trace_name(monkeypatch):
    """轮次内的直调不能改写整条 trace 的名字。"""
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, source="chatbi.sql", has_parent=True).end()
    assert span_attributes.TRACE_NAME not in tracer.spans[0].attributes


def test_source_attribute_only_when_provided(monkeypatch):
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, source="  ").end()
    assert generation_span.SOURCE_ATTRIBUTE not in tracer.spans[0].attributes

    tracer2 = _RecordingTracer()
    _start(monkeypatch, tracer2, source="intent").end()
    assert tracer2.spans[0].attributes[generation_span.SOURCE_ATTRIBUTE] == "intent"


def test_string_input_is_wrapped_as_message(monkeypatch):
    """字符串入参必须被包成消息，否则 generation_attributes 会把它拆成字符列表。"""
    tracer = _RecordingTracer()
    _start(monkeypatch, tracer, input_messages="hello").end()
    raw = tracer.spans[0].attributes[span_attributes.GEN_AI_INPUT_MESSAGES]
    assert "hello" in raw
    assert '"h"' not in raw


class _Chunk:
    """模拟 AgentScope 的原始流式 chunk（带 is_last 终帧标记）。"""

    def __init__(self, text="", is_last=False, usage=None):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.is_last = is_last
        self.usage = usage


class _Message:
    """模拟 AIMessage（无 is_last，带 usage_metadata）。"""

    def __init__(self, text="", usage=None):
        self.content = text
        self.usage_metadata = usage


def test_stream_chunks_final_frame_overrides_increments(monkeypatch):
    """终帧 content 是完整正文，必须覆盖增量，否则文本重复。"""
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=True)

    handle.record_output(_Chunk("你好"))
    handle.record_output(_Chunk("，世界"))
    handle.record_output(_Chunk("你好，世界", is_last=True))
    handle.end()

    out = tracer.spans[0].attributes[span_attributes.GEN_AI_OUTPUT_MESSAGES]
    assert out.count("你好，世界") == 1
    assert "你好，世界你好，世界" not in out


def test_stream_usage_taken_from_final_frame(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=True)

    handle.record_output(_Chunk("hi"))
    handle.record_output(
        _Chunk("hi there", is_last=True, usage={"input_tokens": 11, "output_tokens": 4})
    )
    handle.end()

    attrs = tracer.spans[0].attributes
    assert attrs[span_attributes.GEN_AI_USAGE_INPUT_TOKENS] == 11
    assert attrs[span_attributes.GEN_AI_USAGE_OUTPUT_TOKENS] == 4
    assert "input" in attrs[span_attributes.OBSERVATION_USAGE_DETAILS]


def test_non_stream_message_output_and_usage(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=False)

    handle.record_output(_Message("答案", usage={"input_tokens": 3, "output_tokens": 2}))
    handle.end()

    attrs = tracer.spans[0].attributes
    assert "答案" in attrs[span_attributes.GEN_AI_OUTPUT_MESSAGES]
    assert attrs[span_attributes.GEN_AI_USAGE_INPUT_TOKENS] == 3


def test_first_chunk_records_ttft(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer, streaming=True)

    handle.record_output(_Chunk("a"))
    handle.record_output(_Chunk("ab", is_last=True))

    attrs = tracer.spans[0].attributes
    assert generation_span.FIRST_CHUNK_ATTRIBUTE in attrs
    assert attrs[generation_span.FIRST_CHUNK_ATTRIBUTE] >= 0


def test_end_records_error_type_and_is_idempotent(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer)

    handle.end(error=ValueError("boom"))
    handle.end()
    handle.end()

    span = tracer.spans[0]
    assert span.attributes[generation_span.ERROR_TYPE_ATTRIBUTE] == "ValueError"
    assert span.ended == 1


def test_record_status_writes_attribute(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer)
    handle.record_status("structured_output_error")
    handle.end()
    attrs = tracer.spans[0].attributes
    assert attrs[generation_span.STRUCTURED_STATUS_ATTRIBUTE] == "structured_output_error"


def test_span_json_is_truncated(monkeypatch):
    tracer = _RecordingTracer()
    handle = _start(monkeypatch, tracer)
    handle.record_output(_Message("x" * (generation_span.MAX_OUTPUT_CHARS + 500)))
    handle.end()
    out = tracer.spans[0].attributes[span_attributes.GEN_AI_OUTPUT_MESSAGES]
    assert len(out) <= generation_span.MAX_OUTPUT_CHARS + 200


async def test_generator_exit_does_not_record_error(monkeypatch):
    """调用方提前 break 属正常收尾，不该被记成错误。"""
    tracer = _RecordingTracer()
    _enable_tracing(monkeypatch)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)

    with pytest.raises(GeneratorExit):
        async with generation_span.traced_generation(
            model="m", streaming=True, source=None, input_messages=[]
        ):
            raise GeneratorExit

    span = tracer.spans[0]
    assert span.ended == 1
    assert generation_span.ERROR_TYPE_ATTRIBUTE not in span.attributes


async def test_exception_is_reraised_and_recorded(monkeypatch):
    tracer = _RecordingTracer()
    _enable_tracing(monkeypatch)
    monkeypatch.setattr(generation_span, "_get_tracer", lambda: tracer)

    with pytest.raises(ValueError):
        async with generation_span.traced_generation(
            model="m", streaming=False, source=None, input_messages=[]
        ):
            raise ValueError("kaboom")

    span = tracer.spans[0]
    assert span.ended == 1
    assert span.attributes[generation_span.ERROR_TYPE_ATTRIBUTE] == "ValueError"
