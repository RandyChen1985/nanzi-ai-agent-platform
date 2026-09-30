"""``PipelineRunner`` 的轮次根 span 接线测试。

``run`` 被拆成「薄包装 + _run_steps」：包装层只负责建/收根 span，
真正的步骤编排逻辑（含异常降级、finalize 收拢、chunk 透传）全部保持在 ``_run_steps`` 里，
避免改动既有行为。
"""

import inspect
from types import SimpleNamespace

import pytest

from app.services.ai.pipeline import runner as runner_module
from app.services.ai.pipeline.runner import PipelineRunner

pytestmark = pytest.mark.no_infrastructure


class _FakeTurnSpan:
    def __init__(self, recorded):
        self._recorded = recorded
        self.active = True

    def end(self, error=None):
        self._recorded["ended"] = True
        self._recorded["error"] = error


@pytest.fixture
def recorded_span(monkeypatch):
    recorded = {}

    def fake_start_turn_span(**kwargs):
        recorded["kwargs"] = kwargs
        return _FakeTurnSpan(recorded)

    monkeypatch.setattr(
        "app.services.ai.observability.turn_span.start_turn_span", fake_start_turn_span
    )
    return recorded


def _context(**overrides):
    values = {
        "run_handle": None,
        "trace_id": "t1",
        "user_info": {"user_id": "u1"},
        "conversation_id": "c1",
        "agent_name": "助手",
        "agent_id": "a1",
        "execution_status": "success",
        "shared_state": {},
        # 既有错误降级路径会读这两个字段，夹具必须与真实 PipelineContext 对齐。
        "agent_config": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_run_steps_holds_the_orchestration_logic():
    source = inspect.getsource(runner_module.PipelineRunner)
    assert "async def _run_steps" in source
    assert "async for chunk in self._run_steps(context)" in source


def test_run_does_not_duplicate_step_orchestration():
    run_source = inspect.getsource(PipelineRunner.run)
    # 包装层不应重新实现步骤循环、异常降级等逻辑。
    assert "TERMINAL_OR_SHORT_CIRCUIT_STATUSES" not in run_source
    assert "FinalizeStep()" not in run_source


async def test_run_passes_platform_identity_to_turn_span(recorded_span):
    runner = PipelineRunner(steps=[])

    chunks = [chunk async for chunk in runner.run(_context())]

    assert chunks == []
    kwargs = recorded_span["kwargs"]
    assert kwargs["user_id"] == "u1"
    assert kwargs["conversation_id"] == "c1"
    assert kwargs["agent_name"] == "助手"
    assert kwargs["trace_id"] == "t1"
    assert kwargs["extra"] == {"agent_id": "a1"}
    assert recorded_span["ended"] is True


async def test_run_yields_chunks_untouched(recorded_span):
    class YieldingStep:
        async def run(self, context):  # noqa: ARG002 - 与真实步骤签名一致
            yield "chunk-1"
            yield {"type": "text", "content": "chunk-2"}

    runner = PipelineRunner(steps=[YieldingStep()])

    chunks = [chunk async for chunk in runner.run(_context())]

    assert chunks[0] == "chunk-1"
    assert chunks[1] == {"type": "text", "content": "chunk-2"}
    assert recorded_span["ended"] is True


async def test_run_ends_span_when_consumer_stops_early(recorded_span):
    class InfiniteStep:
        async def run(self, context):  # noqa: ARG002
            index = 0
            while True:
                index += 1
                yield f"chunk-{index}"

    runner = PipelineRunner(steps=[InfiniteStep()])

    stream = runner.run(_context())
    first = await stream.__anext__()
    assert first == "chunk-1"
    await stream.aclose()  # 客户端断开 → 生成器被关闭

    assert recorded_span["ended"] is True


async def test_run_ends_span_on_step_failure(recorded_span, monkeypatch):
    class FailingStep:
        async def run(self, context):  # noqa: ARG002
            raise RuntimeError("步骤炸了")
            yield  # pragma: no cover - 让函数成为异步生成器

    async def fake_enrich(chunk, **_kwargs):
        return chunk

    monkeypatch.setattr(
        "app.services.ai.agent_service._enrich_terminal_error_chunk", fake_enrich
    )

    runner = PipelineRunner(steps=[FailingStep()])

    chunks = [chunk async for chunk in runner.run(_context())]

    assert recorded_span["ended"] is True
    assert any(chunk.get("type") == "error" for chunk in chunks if isinstance(chunk, dict))


async def test_run_survives_turn_span_failure(monkeypatch):
    """建根 span 失败不能影响对话本身：事件流照常，不抛异常。"""

    def boom(**_kwargs):
        raise RuntimeError("span 建不出来")

    monkeypatch.setattr("app.services.ai.observability.turn_span.start_turn_span", boom)

    class YieldingStep:
        async def run(self, context):  # noqa: ARG002
            yield "chunk-1"

    runner = PipelineRunner(steps=[YieldingStep()])

    chunks = [chunk async for chunk in runner.run(_context())]

    assert chunks == ["chunk-1"]
