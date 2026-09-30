"""自检：前置校验、span 结构、层 1 判定的三种分支，以及不阻塞事件循环。"""

import asyncio
import contextlib
import time
import types

from opentelemetry import trace as otel_trace

from app.services.ai.observability import (
    config_store,
    export_health,
    manager,
    selfcheck,
    settings,
)


def setup_function():
    export_health.reset_for_tests()
    manager.reset_for_tests()
    settings.reset_for_tests()


class _FakeClient:
    """可编程的假 client：flush 时模拟一次导出结果。"""

    def __init__(self, outcome="success", delay=0.0):
        self.outcome = outcome
        self.delay = delay
        self.flush_calls = 0

    def flush(self):
        self.flush_calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.outcome == "success":
            export_health.record_success()
        elif self.outcome == "failure":
            export_health.record_failure(RuntimeError("连接被拒绝"))
        # outcome == "nothing" 时不记录任何结果


async def _enable(monkeypatch, outcome="success", delay=0.0):
    async def fake_load():
        return {
            "enabled": True,
            "host": "http://lf:3000",
            "public_key": "pk-lf",
            "secret_key": "sk-lf",
            "sample_rate": 1.0,
            "capture_content": True,
            "environment": None,
            "release": None,
            "timeout_seconds": 5,
        }

    monkeypatch.setattr(config_store, "load_config", fake_load)
    await settings.refresh_snapshot()
    client = _FakeClient(outcome=outcome, delay=delay)
    monkeypatch.setattr(manager, "current_client", lambda: client)
    return client


async def test_rejects_when_tracing_disabled():
    result = await selfcheck.run_selfcheck()
    assert result.ok is False
    assert result.trace_id is None
    assert "未启用" in result.message


async def test_success_reports_confirmed_export(monkeypatch):
    """层 1 结论与 trace id 提取分离验证。

    测试环境没有真实全局 TracerProvider（拿到的是 NonRecordingSpan，trace_id=0），
    因此这里只断言层 1 结论；trace id 的生成与取值由下面的假 tracer 用例覆盖。
    """
    await _enable(monkeypatch, outcome="success")
    result = await selfcheck.run_selfcheck()
    assert result.ok is True
    assert result.export_confirmed is True
    if result.trace_id is not None:
        assert len(result.trace_id) == 32
        int(result.trace_id, 16)


async def test_failure_reports_reason(monkeypatch):
    await _enable(monkeypatch, outcome="failure")
    result = await selfcheck.run_selfcheck()
    assert result.ok is False
    assert result.export_confirmed is False
    assert "连接被拒绝" in result.message


async def test_no_export_observed_is_reported(monkeypatch):
    await _enable(monkeypatch, outcome="nothing")
    result = await selfcheck.run_selfcheck()
    assert result.ok is False
    assert "未观察到导出" in result.message


async def test_flush_does_not_block_event_loop(monkeypatch):
    """flush 是同步阻塞调用，必须丢到线程里执行。

    回归动机同 ``90eb309a``：在事件循环线程里同步 flush 会把并发的对话一起卡住。
    """
    await _enable(monkeypatch, outcome="success", delay=1.0)

    async def ticker():
        await asyncio.sleep(0.05)
        return time.perf_counter()

    start = time.perf_counter()
    task = asyncio.create_task(ticker())
    result = await selfcheck.run_selfcheck()
    ticked_at = await task
    elapsed = time.perf_counter() - start

    assert result.ok is True
    assert ticked_at - start < 0.5, "自检期间事件循环被阻塞了"
    assert elapsed >= 1.0  # 自检本身仍等待了慢 flush


def test_emit_selfcheck_spans_sets_marker_and_extracts_trace_id(monkeypatch):
    """自检 span 必须带标记属性（绕过采样 + 可在 Langfuse 辨识），并正确取出 trace id。

    用假 tracer 注入已知 trace id，避免依赖（且污染）全局 TracerProvider。
    """
    recorded_names = []
    recorded_attributes = {}

    class _FakeSpan:
        def set_attribute(self, key, value):
            recorded_attributes[key] = value

        def get_span_context(self):
            return types.SimpleNamespace(trace_id=int("abcd" * 8, 16))

    class _FakeTracer:
        def start_as_current_span(self, name, attributes=None):
            recorded_names.append(name)
            recorded_attributes.update(attributes or {})
            return contextlib.nullcontext(_FakeSpan())

    monkeypatch.setattr(otel_trace, "get_tracer", lambda _name: _FakeTracer())

    trace_id = selfcheck._emit_selfcheck_spans()

    assert recorded_names == [
        selfcheck.SELFCHECK_SPAN_NAME,
        selfcheck.SELFCHECK_CHILD_SPAN_NAME,
    ], "应发出根 span 与一个子 span（用于验证上下文传递）"
    assert recorded_attributes[selfcheck.SELFCHECK_MARKER_ATTRIBUTE] is True
    assert recorded_attributes["langfuse.trace.name"] == selfcheck.SELFCHECK_SPAN_NAME
    assert trace_id == "abcd" * 8
