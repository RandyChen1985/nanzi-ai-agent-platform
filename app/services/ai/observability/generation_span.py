"""直调链路的 generation span：把一次模型调用变成 Langfuse 可看的观测。

埋点位置是 ``AgentScopeChatClient`` 的四个方法——它是平台所有直调（含经
``AgentScopeLLMHandle`` 的与直接使用 chat client 的）的唯一出口。

与 ``turn_span`` 的关键差异：本模块只发 generation 级 span，**不 attach 到 OTel 上下文**
（单层 span 没有子节点，还能省掉跨 ``yield`` 的 detach 坑）。
"""

import contextlib
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from opentelemetry import trace as otel_trace

from app.services.ai.observability import manager, settings, span_attributes

logger = logging.getLogger(__name__)

TRACER_NAME = "nanzi.llm"
SPAN_NAME = "nanzi.llm.call"

SOURCE_ATTRIBUTE = "nanzi.llm.source"
STREAMING_ATTRIBUTE = "nanzi.llm.streaming"
FIRST_CHUNK_ATTRIBUTE = "nanzi.llm.first_chunk_ms"
STRUCTURED_STATUS_ATTRIBUTE = "nanzi.llm.structured_status"
ERROR_TYPE_ATTRIBUTE = "error.type"

# 输出文本入库前的截断长度，避免把超长响应塞进 span 属性。
MAX_OUTPUT_CHARS = 20000

_tracer = None


def _get_tracer():
    """平台自建 span 的 tracer；scope 必须是白名单里的 ``nanzi.llm``。"""
    global _tracer
    if _tracer is None:
        _tracer = otel_trace.get_tracer(TRACER_NAME)
    return _tracer


def is_tracing_enabled() -> bool:
    """追踪是否真正可用（开关 + 连接参数 + client 已建好），与 turn_span 同口径。"""
    try:
        return settings.is_tracing_ready() and manager.current_client() is not None
    except Exception:
        return False


def reset_for_tests() -> None:
    """仅测试使用：丢弃缓存的 tracer，避免上一用例的假 tracer 泄漏。"""
    global _tracer
    _tracer = None


class GenerationSpan:
    """一次模型调用的 span 句柄；追踪不可用时是安全空实现。"""

    __slots__ = (
        "_span",
        "_ended",
        "_started",
        "_chunk_count",
        "_text_parts",
        "_final_text",
        "_usage",
    )

    def __init__(self, *, span: Any = None) -> None:
        self._span = span
        self._ended = False
        self._started = time.perf_counter()
        self._chunk_count = 0
        self._text_parts: List[str] = []
        self._final_text: Optional[str] = None
        self._usage: Tuple[Optional[int], Optional[int]] = (None, None)

    @property
    def active(self) -> bool:
        return self._span is not None

    def record_output(self, output: Any) -> None:
        """记录一次输出：非流式传最终结果，流式传单个 chunk。

        流式下终帧（``is_last=True``）的 content 是整段完整正文，必须**覆盖**已累积的
        增量，否则会得到「增量 + 完整正文」的重复内容。
        """
        if self._span is None or self._ended:
            return
        try:
            if self._chunk_count == 0:
                self._mark_first_chunk()
            self._chunk_count += 1

            text, is_last = _output_text_and_is_last(output)
            if is_last:
                if text:
                    self._final_text = text
            elif text:
                self._text_parts.append(text)

            tokens = _output_usage(output)
            if tokens != (None, None):
                self._usage = tokens
        except Exception as exc:
            logger.debug("Langfuse generation 输出记录失败（忽略）: %s", exc)

    def record_status(self, status: str) -> None:
        """记录业务侧状态（如结构化输出的成功/失败分类）。"""
        if self._span is None or self._ended:
            return
        try:
            self._span.set_attribute(STRUCTURED_STATUS_ATTRIBUTE, str(status))
        except Exception as exc:
            logger.debug("Langfuse generation 状态记录失败（忽略）: %s", exc)

    def _mark_first_chunk(self) -> None:
        try:
            elapsed_ms = (time.perf_counter() - self._started) * 1000
            self._span.set_attribute(FIRST_CHUNK_ATTRIBUTE, round(elapsed_ms, 2))
        except Exception as exc:
            logger.debug("Langfuse TTFT 记录失败（忽略）: %s", exc)

    def _resolved_text(self) -> str:
        if self._final_text is not None:
            return self._final_text
        return "".join(self._text_parts)

    def end(self, error: Optional[BaseException] = None) -> None:
        """结束 span；幂等、绝不抛异常。"""
        if self._ended:
            return
        self._ended = True
        if self._span is None:
            return

        if error is not None:
            try:
                self._span.set_attribute(ERROR_TYPE_ATTRIBUTE, type(error).__name__)
            except Exception as exc:
                logger.debug("Langfuse 错误状态记录失败（忽略）: %s", exc)

        try:
            text = self._resolved_text()
            attributes = span_attributes.generation_attributes(
                output_messages=(
                    [{"role": "assistant", "content": text[:MAX_OUTPUT_CHARS]}]
                    if text
                    else None
                ),
                usage=self._usage,
            )
            for key, value in attributes.items():
                self._span.set_attribute(key, value)
        except Exception as exc:
            logger.debug("Langfuse 输出属性写入失败（忽略）: %s", exc)

        try:
            self._span.end()
        except Exception as exc:
            logger.debug("Langfuse generation span 结束失败（忽略）: %s", exc)


def _current_parent_is_valid() -> bool:
    """当前是否已处于某个 trace 内（即有没有活跃父 span）。"""
    try:
        parent = otel_trace.get_current_span()
        return bool(parent.get_span_context().is_valid)
    except Exception:
        return False


def _normalize_messages(messages: Any) -> List[Any]:
    """把入参规范成消息列表。

    字符串必须包成一条消息：``generation_attributes`` 内部做 ``list(input_messages)``，
    直接传字符串会被拆成一个个字符。
    """
    if messages is None:
        return []
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    if isinstance(messages, (list, tuple)):
        return list(messages)
    return [messages]


def _output_text_and_is_last(output: Any) -> Tuple[str, bool]:
    """从一次输出里取 (文本, 是否终帧)，兼容 str / AIMessage / 原始 chunk。"""
    if isinstance(output, str):
        return output, False

    is_last = bool(getattr(output, "is_last", False))
    content = getattr(output, "content", None)

    if content is None and isinstance(output, dict):
        content = output.get("content")
        is_last = is_last or bool(output.get("is_last", False))

    if isinstance(content, str):
        return content, is_last

    # 原始 chunk 的 content 是 block 列表（AgentScope 形态）。
    parts: List[str] = []
    if isinstance(content, (list, tuple)):
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts), is_last


def _output_usage(output: Any) -> Tuple[Optional[int], Optional[int]]:
    """从输出里取 (input_tokens, output_tokens)，兼容 usage_metadata / usage。"""
    for attr in ("usage_metadata", "usage"):
        usage = getattr(output, attr, None)
        if usage is None and isinstance(output, dict):
            usage = output.get(attr)
        tokens = span_attributes.extract_usage(usage)
        if tokens != (None, None):
            return tokens
    return (None, None)


def _identity_attributes() -> Dict[str, Any]:
    """从平台上下文取 user / session；取不到就返回空（不影响调用）。"""
    try:
        from app.core.context import get_current_agent_context

        ctx = get_current_agent_context()
    except Exception:
        return {}
    if ctx is None:
        return {}
    try:
        return span_attributes.identity_attributes(
            user_id=getattr(ctx, "user_id", None),
            conversation_id=getattr(ctx, "conversation_id", None),
        )
    except Exception:
        return {}


def start_generation_span(
    *,
    model: Optional[str],
    streaming: bool,
    source: Optional[str],
    input_messages: Any,
) -> GenerationSpan:
    """创建 generation span；不可用或出错时返回非活跃句柄。"""
    if not is_tracing_enabled():
        return GenerationSpan()

    try:
        clean_source = str(source).strip() if source else ""

        attributes = span_attributes.generation_attributes(
            model=model,
            input_messages=_normalize_messages(input_messages),
        )
        attributes[STREAMING_ATTRIBUTE] = bool(streaming)
        if clean_source:
            attributes[SOURCE_ATTRIBUTE] = clean_source
        attributes.update(_identity_attributes())

        # trace name 只能由「自建 trace」的 span 写：轮次内的直调写它会改写整条 trace 的名字。
        if not _current_parent_is_valid():
            attributes[span_attributes.TRACE_NAME] = clean_source or SPAN_NAME

        span = _get_tracer().start_span(name=SPAN_NAME, attributes=attributes)
    except Exception as exc:
        logger.warning("Langfuse 直调 span 创建失败，本次不上报: %s", exc)
        return GenerationSpan()

    return GenerationSpan(span=span)


@contextlib.asynccontextmanager
async def traced_generation(
    *,
    model: Optional[str],
    streaming: bool,
    source: Optional[str],
    input_messages: Any,
) -> AsyncIterator[GenerationSpan]:
    """包裹一次模型调用，保证三条路径都收尾 span。

    ``GeneratorExit`` 是调用方提前 ``break`` / ``aclose()`` 的正常结果，不算错误。
    """
    span = start_generation_span(
        model=model, streaming=streaming, source=source, input_messages=input_messages
    )
    try:
        yield span
    except GeneratorExit:
        span.end()
        raise
    except BaseException as exc:
        span.end(error=exc)
        raise
    else:
        span.end()
