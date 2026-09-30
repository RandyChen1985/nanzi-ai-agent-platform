"""轮次根 span 单元测试。

一轮对话（可能包含路由、多智能体、子智能体、多次工具调用）应当收敛成**一条 trace**，
因此在流水线入口建一个 ``chat.turn`` 根 span，并把平台身份写在它身上。

关键约束：
- 追踪不可用时必须是零开销直通（不建 span、不 attach 上下文）；
- ``end()`` 必须幂等，且 detach 失败不能影响业务（异步生成器可能在不同任务里被关闭）。
"""

import pytest
from types import SimpleNamespace

from app.services.ai.observability import turn_span

pytestmark = pytest.mark.no_infrastructure


class _FakeSpan:
    def __init__(self):
        self.attributes = {}
        self.ended = False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def end(self):
        self.ended = True


@pytest.fixture
def fake_runtime(monkeypatch):
    """把 span 创建与上下文 attach/detach 都换成可观测的假实现。"""
    state = {
        "span": _FakeSpan(),
        "created": [],
        "attached": [],
        "detached": [],
        "tokens": [],
    }

    monkeypatch.setattr(turn_span, "is_tracing_enabled", lambda: True)

    def fake_start_span(**kwargs):
        state["created"].append(kwargs)
        return state["span"]

    monkeypatch.setattr(turn_span, "_start_span", fake_start_span)

    def fake_attach(context):
        token = f"token-{len(state['attached'])}"
        state["attached"].append(context)
        state["tokens"].append(token)
        return token

    monkeypatch.setattr(turn_span.otel_context, "attach", fake_attach)
    monkeypatch.setattr(
        turn_span.otel_context, "detach", lambda token: state["detached"].append(token)
    )
    return state


def test_disabled_runtime_is_zero_cost(monkeypatch):
    monkeypatch.setattr(turn_span, "is_tracing_enabled", lambda: False)
    created = []
    monkeypatch.setattr(turn_span, "_start_span", lambda **kwargs: created.append(kwargs))
    monkeypatch.setattr(
        turn_span.otel_context, "attach", lambda ctx: pytest.fail("关闭态不应 attach 上下文")
    )

    span = turn_span.start_turn_span(user_id="u1", conversation_id="c1", agent_name="助手",
                                     trace_id="t1")

    assert span.active is False
    assert created == []
    span.end()  # 幂等且安全


def test_creates_root_span_with_platform_identity(fake_runtime):
    span = turn_span.start_turn_span(
        user_id="u1", conversation_id="c1", agent_name="助手", trace_id="t1",
        extra={"agent_id": "a1"},
    )

    assert span.active is True
    assert len(fake_runtime["created"]) == 1
    created = fake_runtime["created"][0]
    assert created["name"] == "chat.turn"
    assert created["attributes"]["user.id"] == "u1"
    assert created["attributes"]["session.id"] == "c1"
    assert created["attributes"]["langfuse.trace.metadata.trace_id"] == "t1"
    assert created["attributes"]["langfuse.trace.metadata.agent_id"] == "a1"


def test_span_is_attached_to_otel_context(fake_runtime):
    turn_span.start_turn_span(user_id="u1", conversation_id="c1", agent_name="助手",
                              trace_id="t1")
    assert len(fake_runtime["attached"]) == 1


def test_end_ends_span_and_detaches(fake_runtime):
    span = turn_span.start_turn_span(user_id="u1", conversation_id="c1", agent_name="助手",
                                     trace_id="t1")

    span.end()

    assert fake_runtime["span"].ended is True
    assert fake_runtime["detached"] == ["token-0"]


def test_end_is_idempotent(fake_runtime):
    span = turn_span.start_turn_span(user_id="u1", conversation_id="c1", agent_name="助手",
                                     trace_id="t1")

    span.end()
    span.end()
    span.end()

    assert len(fake_runtime["detached"]) == 1


def test_end_marks_error_status(fake_runtime):
    span = turn_span.start_turn_span(user_id="u1", conversation_id="c1", agent_name="助手",
                                     trace_id="t1")

    span.end(error=ValueError("流水线异常"))

    assert fake_runtime["span"].ended is True


def test_end_swallows_detach_errors(fake_runtime, monkeypatch):
    span = turn_span.start_turn_span(user_id="u1", conversation_id="c1", agent_name="助手",
                                     trace_id="t1")

    def boom(_token):
        raise ValueError("不同任务里无法 detach")

    monkeypatch.setattr(turn_span.otel_context, "detach", boom)

    span.end()  # 不抛异常
    assert fake_runtime["span"].ended is True


def test_start_failure_degrades_to_inactive(monkeypatch):
    monkeypatch.setattr(turn_span, "is_tracing_enabled", lambda: True)

    def boom(**_kwargs):
        raise RuntimeError("provider 未就绪")

    monkeypatch.setattr(turn_span, "_start_span", boom)

    span = turn_span.start_turn_span(user_id="u1", conversation_id="c1", agent_name="助手",
                                     trace_id="t1")

    assert span.active is False
    span.end()  # 不抛异常


def test_is_tracing_enabled_requires_client_and_snapshot(monkeypatch):
    from app.services.ai.observability import manager, settings as obs_settings

    assert turn_span.is_tracing_enabled() is False

    monkeypatch.setattr(obs_settings, "is_tracing_ready", lambda: True)
    monkeypatch.setattr(manager, "current_client", lambda: object())
    assert turn_span.is_tracing_enabled() is True

    monkeypatch.setattr(manager, "current_client", lambda: None)
    assert turn_span.is_tracing_enabled() is False
