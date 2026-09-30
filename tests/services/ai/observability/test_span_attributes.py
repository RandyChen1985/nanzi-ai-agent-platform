"""``span_attributes`` 单元测试：属性名与取值契约。

这里的属性名不是随意取的，必须与上游实现对齐（已逐条核对安装包源码）：
- AgentScope ``middleware/_tracing/_attributes.py`` 用的是
  ``opentelemetry.semconv._incubating.attributes.gen_ai_attributes``；
- Langfuse ``_client/attributes.py`` 用 ``user.id`` / ``session.id`` /
  ``langfuse.trace.name`` / ``langfuse.trace.metadata``；
- Langfuse 会把 metadata 字典**打平成** ``langfuse.trace.metadata.<key>``
  （``_flatten_and_serialize_metadata``），所以这里必须写带后缀的键。
"""

import pytest

from app.services.ai.observability import span_attributes as sa

pytestmark = pytest.mark.no_infrastructure


def test_trace_attributes_carry_platform_identity():
    attrs = sa.trace_attributes(
        user_id="u1",
        conversation_id="c1",
        agent_name="助手",
        trace_id="t1",
        extra={"agent_id": "a1"},
    )
    assert attrs["user.id"] == "u1"
    assert attrs["session.id"] == "c1"
    assert attrs["langfuse.trace.name"] == "助手 对话"
    assert attrs["langfuse.trace.metadata.trace_id"] == "t1"
    assert attrs["langfuse.trace.metadata.agent_id"] == "a1"


def test_trace_attributes_fall_back_to_default_name():
    attrs = sa.trace_attributes(
        user_id="u1", conversation_id="c1", agent_name=None, trace_id="t1", extra={}
    )
    assert attrs["langfuse.trace.name"] == "对话"


def test_trace_attributes_skip_empty_values():
    attrs = sa.trace_attributes(
        user_id=None, conversation_id="  ", agent_name=None, trace_id=None, extra={"agent_id": None}
    )
    assert "user.id" not in attrs
    assert "session.id" not in attrs
    assert "langfuse.trace.metadata.trace_id" not in attrs
    assert "langfuse.trace.metadata.agent_id" not in attrs


def test_trace_attributes_serialize_non_scalar_metadata():
    attrs = sa.trace_attributes(
        user_id="u1", conversation_id="c1", agent_name="a", trace_id="t1",
        extra={"flags": {"multi_agent": True}},
    )
    assert attrs["langfuse.trace.metadata.flags"] == '{"multi_agent": true}'


def test_content_attribute_names_are_complete():
    assert set(sa.CONTENT_ATTRIBUTES) == {
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "langfuse.observation.input",
        "langfuse.observation.output",
        "langfuse.trace.input",
        "langfuse.trace.output",
    }


def test_content_attribute_detection():
    assert sa.is_content_attribute("gen_ai.input.messages") is True
    assert sa.is_content_attribute("gen_ai.tool.call.arguments") is True
    assert sa.is_content_attribute("langfuse.observation.output") is True
    # 工具名与工具定义属于「结构」而非「内容」，脱敏时保留，便于排障。
    assert sa.is_content_attribute("gen_ai.tool.name") is False
    assert sa.is_content_attribute("gen_ai.tool.definitions") is False
    assert sa.is_content_attribute("gen_ai.usage.input_tokens") is False


def test_extract_usage_from_dict_and_object():
    assert sa.extract_usage({"input_tokens": 3, "output_tokens": 4}) == (3, 4)
    assert sa.extract_usage({"prompt_tokens": 5, "completion_tokens": 6}) == (5, 6)

    class Usage:  # noqa: N801 - 模拟 SDK 的 usage 对象
        input_tokens = 7
        output_tokens = 8

    assert sa.extract_usage(Usage()) == (7, 8)
    assert sa.extract_usage(None) == (None, None)
    assert sa.extract_usage({}) == (None, None)


def test_generation_attributes_use_genai_conventions():
    attrs = sa.generation_attributes(
        model="deepseek-chat",
        provider="deepseek",
        input_messages=[{"role": "user", "content": "你好"}],
        output_messages=[{"role": "assistant", "content": "在"}],
        usage=(3, 4),
    )
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.request.model"] == "deepseek-chat"
    assert attrs["gen_ai.provider.name"] == "deepseek"
    assert attrs["gen_ai.usage.input_tokens"] == 3
    assert attrs["gen_ai.usage.output_tokens"] == 4
    assert attrs["langfuse.observation.type"] == "generation"
    assert '"role"' in attrs["gen_ai.input.messages"]


def test_generation_attributes_omit_missing_pieces():
    attrs = sa.generation_attributes(model=None, provider=None, input_messages=None,
                                     output_messages=None, usage=(None, None))
    assert "gen_ai.request.model" not in attrs
    assert "gen_ai.usage.input_tokens" not in attrs
    assert "gen_ai.input.messages" not in attrs
    assert attrs["gen_ai.operation.name"] == "chat"


def test_serialize_messages_keeps_unicode_readable():
    text = sa.serialize_payload([{"role": "user", "content": "中文内容"}])
    assert "中文内容" in text
