"""轮次根 span：把一轮对话（含路由、多智能体、子智能体、工具调用）收敛成一条 trace。

为什么需要它：AgentScope 的 ``TracingMiddleware`` 只覆盖「智能体 reply」这一段，
路由、直调模型、收尾这些环节要么没有 span，要么各自成为新的根。平台在流水线入口建一个
``chat.turn`` 根 span，并把它 attach 到 OTel 上下文，后续所有 span 才会挂在同一条 trace 下。

实现注意：``attach`` 之后要跨越大量 ``yield``，而异步生成器可能在不同任务里被关闭，
因此 ``detach`` 失败属于预期情况（与 AgentScope 自身处理一致），必须吞掉。
"""

import logging
from typing import Any, Dict, Optional

from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace

from app.services.ai.observability import manager, settings, span_attributes

logger = logging.getLogger(__name__)

TRACER_NAME = "nanzi.llm"
TURN_SPAN_NAME = "chat.turn"

_tracer = None


def _get_tracer():
    """获取平台自建 span 的 tracer。

    Langfuse 的默认导出过滤器只认自家 span、GenAI span 与已知插桩库，``agentscope``
    不在名单内，所以平台 span 必须统一用 ``nanzi.llm`` 这个 scope 名，
    并在 ``should_export_span`` 里显式放行。
    """
    global _tracer
    if _tracer is None:
        _tracer = otel_trace.get_tracer(TRACER_NAME)
    return _tracer


def _start_span(*, name: str, attributes: Dict[str, Any]):
    """创建 span（独立函数便于测试注入）。"""
    return _get_tracer().start_span(name=name, attributes=attributes)


def is_tracing_enabled() -> bool:
    """追踪是否真正可用：开关打开、连接参数完整，且 client 已建好。

    client 未建好意味着全局 provider 还是 ``ProxyTracerProvider``，此时创建 span 只会得到
    空实现，徒增开销，因此直接跳过。
    """
    try:
        return settings.is_tracing_ready() and manager.current_client() is not None
    except Exception:
        return False


class TurnSpan:
    """一次轮次根 span 的句柄；不可用时是安全空实现。"""

    __slots__ = ("_span", "_token", "_ended", "active")

    def __init__(self, *, span: Any = None, token: Any = None) -> None:
        self._span = span
        self._token = token
        self._ended = False
        self.active = span is not None

    def end(self, error: Optional[BaseException] = None) -> None:
        """结束 span 并 detach 上下文；幂等、绝不抛异常。"""
        if self._ended:
            return
        self._ended = True

        if self._span is None:
            return

        try:
            if error is not None:
                self._span.set_attribute("error.type", type(error).__name__)
        except Exception as exc:
            logger.debug("Langfuse 轮次 span 记录错误状态失败（忽略）: %s", exc)

        try:
            self._span.end()
        except Exception as exc:
            logger.debug("Langfuse 轮次 span 结束失败（忽略）: %s", exc)

        if self._token is not None:
            try:
                otel_context.detach(self._token)
            except Exception as exc:
                # 生成器在不同任务中被关闭时 detach 必然失败，属预期。
                logger.debug("Langfuse 轮次 span 上下文 detach 失败（忽略）: %s", exc)
        self._token = None


def start_turn_span(
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> TurnSpan:
    """创建并 attach 轮次根 span；追踪不可用或出错时返回非活跃句柄。"""
    if not is_tracing_enabled():
        return TurnSpan()

    try:
        snapshot = settings.get_snapshot()
        attributes = span_attributes.trace_attributes(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name=agent_name,
            trace_id=trace_id,
            extra=extra,
        )
        attributes.update(
            span_attributes.resource_attributes(
                environment=snapshot.environment, release=snapshot.release
            )
        )
        span = _start_span(name=TURN_SPAN_NAME, attributes=attributes)
        token = otel_context.attach(otel_trace.set_span_in_context(span))
    except Exception as exc:
        logger.warning("Langfuse 轮次根 span 创建失败，本轮不上报: %s", exc)
        return TurnSpan()

    return TurnSpan(span=span, token=token)
