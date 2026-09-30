"""``LangfuseContextMiddleware`` 单元测试。

中间件只做一件事：在 AgentScope 的 reply span 上补平台身份属性，保证 trace 能按
用户/会话/轮次聚合，并且**绝不改变事件流**。
"""

import pytest
from unittest.mock import MagicMock

from app.services.ai.observability.context_middleware import LangfuseContextMiddleware

pytestmark = pytest.mark.no_infrastructure


def _patch_current_span(monkeypatch, span):
    monkeypatch.setattr(
        "app.services.ai.observability.context_middleware.otel_trace.get_current_span",
        lambda: span,
    )


def _middleware(**overrides):
    kwargs = {
        "user_id": "u1",
        "conversation_id": "c1",
        "agent_name": "助手",
        "trace_id": "t1",
    }
    kwargs.update(overrides)
    return LangfuseContextMiddleware(**kwargs)


async def test_stamps_platform_identity_on_current_span(monkeypatch):
    span = MagicMock()
    _patch_current_span(monkeypatch, span)

    async def next_handler(**_kwargs):
        yield "chunk-1"
        yield "chunk-2"

    items = [
        item
        async for item in _middleware().on_reply(
            agent=MagicMock(), input_kwargs={}, next_handler=next_handler
        )
    ]

    assert items == ["chunk-1", "chunk-2"]
    written = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
    assert written["user.id"] == "u1"
    assert written["session.id"] == "c1"
    assert written["langfuse.trace.name"] == "助手 对话"
    assert written["langfuse.trace.metadata.trace_id"] == "t1"


async def test_passes_events_through_untouched(monkeypatch):
    _patch_current_span(monkeypatch, MagicMock())

    payload = {"type": "text", "content": "原样透传"}

    async def next_handler(**_kwargs):
        yield payload
        yield None

    items = [
        item
        async for item in _middleware().on_reply(
            agent=MagicMock(), input_kwargs={}, next_handler=next_handler
        )
    ]

    assert items == [payload, None]
    assert items[0] is payload


async def test_swallows_attribute_errors(monkeypatch):
    class Boom:
        def set_attribute(self, *_args, **_kwargs):
            raise RuntimeError("otel down")

    _patch_current_span(monkeypatch, Boom())

    async def next_handler(**_kwargs):
        yield "ok"

    items = [
        item
        async for item in _middleware().on_reply(
            agent=MagicMock(), input_kwargs={}, next_handler=next_handler
        )
    ]
    assert items == ["ok"]


async def test_swallows_span_lookup_errors(monkeypatch):
    def boom():
        raise RuntimeError("no span")

    monkeypatch.setattr(
        "app.services.ai.observability.context_middleware.otel_trace.get_current_span", boom
    )

    async def next_handler(**_kwargs):
        yield "ok"

    items = [
        item
        async for item in _middleware().on_reply(
            agent=MagicMock(), input_kwargs={}, next_handler=next_handler
        )
    ]
    assert items == ["ok"]


async def test_propagates_handler_exceptions(monkeypatch):
    _patch_current_span(monkeypatch, MagicMock())

    async def next_handler(**_kwargs):
        yield "first"
        raise ValueError("业务异常必须原样抛出")

    received = []
    with pytest.raises(ValueError, match="业务异常必须原样抛出"):
        async for item in _middleware().on_reply(
            agent=MagicMock(), input_kwargs={}, next_handler=next_handler
        ):
            received.append(item)

    assert received == ["first"]


async def test_skips_empty_identity(monkeypatch):
    span = MagicMock()
    _patch_current_span(monkeypatch, span)

    async def next_handler(**_kwargs):
        yield "ok"

    items = [
        item
        async for item in _middleware(user_id=None, conversation_id=None, agent_name=None).on_reply(
            agent=MagicMock(), input_kwargs={}, next_handler=next_handler
        )
    ]

    assert items == ["ok"]
    written = {call.args[0] for call in span.set_attribute.call_args_list}
    assert "user.id" not in written
    assert "session.id" not in written


def test_middleware_is_agentscope_compatible():
    """必须继承 AgentScope 的 MiddlewareBase 并实现 on_reply。

    回归背景：AgentScope 用 ``is_implemented()`` 反射筛选中间件，普通对象会在构建
    中间件链时抛 ``AttributeError: ... has no attribute 'is_implemented'``，
    直接打断整轮对话。
    """
    from agentscope.middleware import MiddlewareBase

    middleware = _middleware()
    assert isinstance(middleware, MiddlewareBase)
    assert middleware.is_implemented("on_reply") is True
    assert middleware.is_implemented("on_acting") is False
