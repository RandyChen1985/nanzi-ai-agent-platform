"""Span 属性构造与内容属性清单。

属性名与上游严格对齐（已核对安装包源码）：
- AgentScope tracing 用 OpenTelemetry 的 ``gen_ai_attributes`` 语义约定；
- Langfuse 用 ``user.id`` / ``session.id`` / ``langfuse.trace.name``，
  并把 metadata 字典**打平**成 ``langfuse.trace.metadata.<key>``
  （见 ``langfuse/_client/attributes.py::_flatten_and_serialize_metadata``）。
"""

import json
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

# 平台身份属性：Langfuse 从根 span 上读取这些属性推导 trace 维度。
TRACE_USER_ID = "user.id"
TRACE_SESSION_ID = "session.id"
TRACE_NAME = "langfuse.trace.name"
TRACE_METADATA_PREFIX = "langfuse.trace.metadata"

# GenAI 语义约定属性。
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"
GEN_AI_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_RESULT = "gen_ai.tool.call.result"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_DEFINITIONS = "gen_ai.tool.definitions"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# Langfuse 观测维度属性。
OBSERVATION_TYPE = "langfuse.observation.type"
OBSERVATION_MODEL = "langfuse.observation.model.name"
OBSERVATION_USAGE_DETAILS = "langfuse.observation.usage_details"
OBSERVATION_INPUT = "langfuse.observation.input"
OBSERVATION_OUTPUT = "langfuse.observation.output"
TRACE_INPUT = "langfuse.trace.input"
TRACE_OUTPUT = "langfuse.trace.output"
ENVIRONMENT = "langfuse.environment"
RELEASE = "langfuse.release"

# 承载用户内容/工具参数的属性：关闭 capture_content 时按此清单删除。
# 刻意不包含 gen_ai.tool.name / gen_ai.tool.definitions —— 工具名与工具体系结构
# 属于排障必需的「结构信息」，不应随内容开关一起消失。
CONTENT_ATTRIBUTES: Tuple[str, ...] = (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_RESULT,
    OBSERVATION_INPUT,
    OBSERVATION_OUTPUT,
    TRACE_INPUT,
    TRACE_OUTPUT,
)

_CONTENT_ATTRIBUTE_SET = frozenset(CONTENT_ATTRIBUTES)


def is_content_attribute(key: str) -> bool:
    """判断属性是否承载用户内容（脱敏时需要删除）。"""
    return key in _CONTENT_ATTRIBUTE_SET


def serialize_payload(payload: Any) -> str:
    """把消息/参数序列化成可放进 OTel 属性的字符串，保留中文可读性。"""
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(payload)


def _metadata_attributes(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """按 Langfuse 的打平规则生成 metadata 属性。"""
    attributes: Dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if value is None or value == "":
            continue
        flat_key = f"{TRACE_METADATA_PREFIX}.{key}"
        # Langfuse 对 str/int 直传、其余类型自行序列化，这里保持一致以免双重转义。
        attributes[flat_key] = value if isinstance(value, (str, int)) else serialize_payload(value)
    return attributes


def trace_attributes(
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造 trace 维度属性（写在一轮对话的根 span 上）。"""
    attributes: Dict[str, Any] = {}

    if user_id and str(user_id).strip():
        attributes[TRACE_USER_ID] = str(user_id).strip()
    if conversation_id and str(conversation_id).strip():
        attributes[TRACE_SESSION_ID] = str(conversation_id).strip()

    name = (agent_name or "").strip()
    attributes[TRACE_NAME] = f"{name} 对话" if name else "对话"

    metadata: Dict[str, Any] = {}
    if trace_id and str(trace_id).strip():
        metadata["trace_id"] = str(trace_id).strip()
    for key, value in (extra or {}).items():
        if value is None or value == "":
            continue
        metadata[key] = value
    attributes.update(_metadata_attributes(metadata))

    return attributes


def extract_usage(usage: Any) -> Tuple[Optional[int], Optional[int]]:
    """从多种形态的 usage 对象里取 (input_tokens, output_tokens)。

    兼容 dict / 对象，以及 ``input_tokens`` 与 ``prompt_tokens`` 两套命名。
    """
    if usage is None:
        return (None, None)

    def _pick(*names: str) -> Optional[int]:
        for name in names:
            if isinstance(usage, dict):
                if name in usage and usage[name] is not None:
                    try:
                        return int(usage[name])
                    except (TypeError, ValueError):
                        continue
            else:
                value = getattr(usage, name, None)
                if value is not None:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        continue
        return None

    return (_pick("input_tokens", "prompt_tokens"), _pick("output_tokens", "completion_tokens"))


def generation_attributes(
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    input_messages: Optional[Iterable[Any]] = None,
    output_messages: Optional[Sequence[Any]] = None,
    usage: Tuple[Optional[int], Optional[int]] = (None, None),
) -> Dict[str, Any]:
    """构造一次模型调用的 generation 属性。"""
    attributes: Dict[str, Any] = {
        GEN_AI_OPERATION_NAME: "chat",
        OBSERVATION_TYPE: "generation",
    }

    if model:
        attributes[GEN_AI_REQUEST_MODEL] = str(model)
        attributes[OBSERVATION_MODEL] = str(model)
    if provider:
        attributes[GEN_AI_PROVIDER_NAME] = str(provider)

    if input_messages is not None:
        attributes[GEN_AI_INPUT_MESSAGES] = serialize_payload(list(input_messages))
    if output_messages is not None:
        attributes[GEN_AI_OUTPUT_MESSAGES] = serialize_payload(list(output_messages))

    input_tokens, output_tokens = usage
    if input_tokens is not None:
        attributes[GEN_AI_USAGE_INPUT_TOKENS] = input_tokens
    if output_tokens is not None:
        attributes[GEN_AI_USAGE_OUTPUT_TOKENS] = output_tokens
    if input_tokens is not None or output_tokens is not None:
        attributes[OBSERVATION_USAGE_DETAILS] = serialize_payload(
            {
                key: value
                for key, value in (
                    ("input", input_tokens),
                    ("output", output_tokens),
                )
                if value is not None
            }
        )

    return attributes


def resource_attributes(
    *, environment: Optional[str] = None, release: Optional[str] = None
) -> Dict[str, Any]:
    """构造 Langfuse 的部署维度属性，留空则不写。"""
    attributes: Dict[str, Any] = {}
    if environment:
        attributes[ENVIRONMENT] = str(environment).strip()
    if release:
        attributes[RELEASE] = str(release).strip()
    return attributes
