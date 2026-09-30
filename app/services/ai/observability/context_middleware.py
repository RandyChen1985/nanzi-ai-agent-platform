"""给 AgentScope 的 reply span 补平台身份属性。

AgentScope 的 ``TracingMiddleware`` 已经产出了完整的 ``invoke_agent`` / ``chat`` /
``execute_tool`` 层级，但它只在 span 上写 AgentScope 自己的属性（``gen_ai.*`` 与
``agentscope.*``）。平台需要的是「这条 trace 属于哪个用户 / 哪个会话 / 哪一轮」，
因此这里在 reply span 上补 ``user.id`` / ``session.id`` / ``langfuse.trace.name``。

属性全部包在 try/except 里：追踪是旁路能力，任何失败都不允许影响对话。
"""

import logging
from typing import Any, AsyncGenerator, Callable, Optional

from agentscope.middleware import MiddlewareBase
from opentelemetry import trace as otel_trace

from app.services.ai.observability import span_attributes

logger = logging.getLogger(__name__)


class LangfuseContextMiddleware(MiddlewareBase):
    """把平台身份写进当前 span（不包裹 try/finally，避免改动事件流语义）。

    必须继承 ``MiddlewareBase``：AgentScope 通过 ``is_implemented()`` 反射判断每个
    中间件实现了哪些钩子，普通对象会在构建中间件链时直接抛 AttributeError。
    """

    def __init__(
        self,
        *,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._agent_name = agent_name
        self._trace_id = trace_id

    def _current_attributes(self) -> dict:
        return span_attributes.trace_attributes(
            user_id=self._user_id,
            conversation_id=self._conversation_id,
            agent_name=self._agent_name,
            trace_id=self._trace_id,
        )

    def _stamp_span(self) -> None:
        try:
            span = otel_trace.get_current_span()
        except Exception as exc:
            logger.debug("Langfuse 身份属性写入跳过：无法获取当前 span（%s）", exc)
            return

        attributes = self._current_attributes()
        for key, value in attributes.items():
            try:
                span.set_attribute(key, value)
            except Exception as exc:
                logger.debug("Langfuse 属性 %s 写入失败（忽略）: %s", key, exc)
                return

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        self._stamp_span()
        async for item in next_handler(**input_kwargs):
            yield item
