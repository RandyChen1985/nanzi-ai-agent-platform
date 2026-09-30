"""``build_runtime_middlewares`` 的 Langfuse 接线契约。

中间件链是洋葱模型，``middlewares[0]`` 最外层。追踪相关的两个中间件必须放在**最内层**
（列表末尾），这样它们看到的当前 span 才是 AgentScope ``TracingMiddleware`` 已经建立的
reply span，而不是被别的中间件包在外面。
"""

import pytest

from app.services.ai.runtime.agentscope import agent_runtime

pytestmark = pytest.mark.no_infrastructure


def _build(monkeypatch, **overrides):
    monkeypatch.setattr("app.core.context.get_current_agent_context", lambda: None)
    kwargs = {
        "user_id": "u1",
        "conversation_id": "c1",
        "agent_name": "助手",
        "trace_id": "t1",
    }
    kwargs.update(overrides)
    return agent_runtime.build_runtime_middlewares(**kwargs)


def test_langfuse_middlewares_are_appended_at_the_end(monkeypatch):
    middlewares = _build(monkeypatch)
    names = [type(item).__name__ for item in middlewares]
    assert names[-2:] == ["TracingMiddleware", "LangfuseContextMiddleware"]


def test_tracing_middleware_comes_after_stats_middleware(monkeypatch):
    middlewares = _build(monkeypatch)
    names = [type(item).__name__ for item in middlewares]
    assert "ModelCallStatsMiddleware" in names
    assert names.index("ModelCallStatsMiddleware") < names.index("TracingMiddleware")


def test_context_middleware_carries_platform_identity(monkeypatch):
    middlewares = _build(monkeypatch)
    context_middleware = middlewares[-1]
    assert context_middleware._user_id == "u1"
    assert context_middleware._conversation_id == "c1"
    assert context_middleware._trace_id == "t1"


def test_langfuse_wiring_is_present_without_conversation(monkeypatch):
    middlewares = _build(monkeypatch, conversation_id=None)
    names = [type(item).__name__ for item in middlewares]
    assert names[-2:] == ["TracingMiddleware", "LangfuseContextMiddleware"]
    assert "ModelCallStatsMiddleware" not in names
