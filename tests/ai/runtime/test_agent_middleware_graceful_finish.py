"""``GracefulFinishMiddleware`` 的契约测试。

以 fake agent / fake handler 驱动，不依赖真实 LLM、Redis 或数据库。
"""

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.no_infrastructure


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMsg:
    def __init__(self, role: str, content) -> None:
        self.role = role
        self.content = content


class _FakeState:
    def __init__(self, cur_iter: int, context=None) -> None:
        self.cur_iter = cur_iter
        self.context = list(context or [])
        self.session_id = "sess-1"

    def append_context(self, _name, blocks) -> None:
        self.context.extend(blocks)


class _FakeReactConfig:
    def __init__(self, max_iters: int) -> None:
        self.max_iters = max_iters


class _FakeAgent:
    def __init__(self, *, cur_iter: int, max_iters: int, context=None) -> None:
        self.state = _FakeState(cur_iter, context)
        self.react_config = _FakeReactConfig(max_iters)
        self.name = "Friday"


def _patch_switch(monkeypatch, value):
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value=value),
    )


async def _collect(agen):
    return [item async for item in agen]


# --------------------------------------------------------------------------
# GracefulFinishMiddleware
# --------------------------------------------------------------------------


async def test_triggers_when_steps_exhausted_and_no_text_output(monkeypatch):
    from app.services.ai.runtime.agentscope.middleware import GracefulFinishMiddleware

    _patch_switch(monkeypatch, "true")
    middleware = GracefulFinishMiddleware()
    agent = _FakeAgent(cur_iter=19, max_iters=20)  # remaining=1 <= reserve(2)

    captured = {}

    async def next_handler(**kwargs):
        captured.update(kwargs)
        yield "event"

    out = await _collect(middleware.on_reasoning(agent, {}, next_handler))

    assert out == ["event"]
    assert captured["tool_choice"].mode == "none"
    hints = [
        blk for blk in agent.state.context
        if type(blk).__name__ == "HintBlock"
    ]
    assert len(hints) == 1
    assert "19/20" in hints[0].hint


async def test_skips_when_assistant_already_produced_text(monkeypatch):
    """产出检测生效：模型已在给答案时不打断。"""
    from app.services.ai.runtime.agentscope.middleware import GracefulFinishMiddleware

    _patch_switch(monkeypatch, "true")
    middleware = GracefulFinishMiddleware()
    agent = _FakeAgent(
        cur_iter=19,
        max_iters=20,
        context=[_FakeMsg("assistant", [_TextBlock("这是最终答复")])],
    )

    captured = {}

    async def next_handler(**kwargs):
        captured.update(kwargs)
        yield "event"

    await _collect(middleware.on_reasoning(agent, {}, next_handler))

    assert "tool_choice" not in captured
    assert not any(
        type(blk).__name__ == "HintBlock" for blk in agent.state.context
    )


async def test_skips_when_steps_remain(monkeypatch):
    from app.services.ai.runtime.agentscope.middleware import GracefulFinishMiddleware

    _patch_switch(monkeypatch, "true")
    middleware = GracefulFinishMiddleware()
    agent = _FakeAgent(cur_iter=5, max_iters=20)

    captured = {}

    async def next_handler(**kwargs):
        captured.update(kwargs)
        yield "event"

    await _collect(middleware.on_reasoning(agent, {}, next_handler))

    assert "tool_choice" not in captured


async def test_respects_disabled_switch(monkeypatch):
    from app.services.ai.runtime.agentscope.middleware import GracefulFinishMiddleware

    _patch_switch(monkeypatch, "false")
    middleware = GracefulFinishMiddleware()
    agent = _FakeAgent(cur_iter=19, max_iters=20)

    captured = {}

    async def next_handler(**kwargs):
        captured.update(kwargs)
        yield "event"

    await _collect(middleware.on_reasoning(agent, {}, next_handler))

    assert "tool_choice" not in captured
    assert middleware._enabled is False


async def test_switch_read_failure_defaults_to_enabled(monkeypatch):
    from app.services.ai.runtime.agentscope.middleware import GracefulFinishMiddleware

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(side_effect=RuntimeError("config down")),
    )
    middleware = GracefulFinishMiddleware()

    assert await middleware._is_enabled() is True


def test_reserve_steps_scales_with_budget():
    from app.services.ai.runtime.agentscope.middleware import GracefulFinishMiddleware

    assert GracefulFinishMiddleware._reserve_steps(5) == 1
    assert GracefulFinishMiddleware._reserve_steps(8) == 1
    assert GracefulFinishMiddleware._reserve_steps(9) == 2
    assert GracefulFinishMiddleware._reserve_steps(20) == 2


async def test_passes_through_when_iteration_info_missing(monkeypatch):
    """agent 结构不符合预期时不干预，也不抛异常。"""
    from app.services.ai.runtime.agentscope.middleware import GracefulFinishMiddleware

    _patch_switch(monkeypatch, "true")
    middleware = GracefulFinishMiddleware()

    class _Broken:
        name = "broken"

    async def next_handler(**kwargs):
        yield "event"

    out = await _collect(middleware.on_reasoning(_Broken(), {}, next_handler))
    assert out == ["event"]
