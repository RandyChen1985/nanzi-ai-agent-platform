"""可观测 exporter：结果记录、异常兜底，以及与 SDK 默认参数的防漂移比对。"""

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from app.services.ai.observability import export_health, exporter, settings


def setup_function():
    export_health.reset_for_tests()


class _FakeInner:
    """记录调用并按预设结果返回的内层 exporter。"""

    def __init__(self, result=SpanExportResult.SUCCESS, raises=None):
        self.result = result
        self.raises = raises
        self.export_calls = 0
        self.shutdown_calls = 0

    def export(self, spans):
        self.export_calls += 1
        if self.raises is not None:
            raise self.raises
        return self.result

    def shutdown(self):
        self.shutdown_calls += 1


def _snapshot(**overrides):
    base = dict(
        enabled=True,
        host="http://lf:3000",
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        timeout_seconds=7,
        status=settings.STATUS_ENABLED,
    )
    base.update(overrides)
    return settings.LangfuseSnapshot(**base)


def test_success_is_recorded():
    inner = _FakeInner(SpanExportResult.SUCCESS)
    wrapper = exporter.ObservableSpanExporter(inner)
    assert wrapper.export([]) == SpanExportResult.SUCCESS
    snap = export_health.snapshot()
    assert snap.success_count == 1
    assert snap.failure_count == 0


def test_failure_result_is_recorded():
    inner = _FakeInner(SpanExportResult.FAILURE)
    wrapper = exporter.ObservableSpanExporter(inner)
    assert wrapper.export([]) == SpanExportResult.FAILURE
    snap = export_health.snapshot()
    assert snap.failure_count == 1
    assert snap.consecutive_failures == 1
    assert "FAILURE" in snap.last_error


def test_inner_exception_is_swallowed_and_recorded():
    """内层抛异常时返回 FAILURE，绝不把异常抛给 OTel 导出线程。"""
    inner = _FakeInner(raises=RuntimeError("网络炸了"))
    wrapper = exporter.ObservableSpanExporter(inner)
    assert wrapper.export([]) == SpanExportResult.FAILURE
    assert "网络炸了" in export_health.snapshot().last_error


def test_shutdown_is_delegated_and_swallows_errors():
    inner = _FakeInner()
    wrapper = exporter.ObservableSpanExporter(inner)
    wrapper.shutdown()
    assert inner.shutdown_calls == 1

    class _BadShutdown(_FakeInner):
        def shutdown(self):
            raise RuntimeError("关不掉")

    exporter.ObservableSpanExporter(_BadShutdown()).shutdown()  # 不得抛出


def test_otel_exporter_kwargs_match_sdk_defaults(monkeypatch):
    """防漂移：我们从 SDK 源码复刻了 OTLP exporter 的构造参数。

    SDK 升级若改了 endpoint / headers / timeout 的算法，这个测试立刻变红——
    否则我们传入的 exporter 会与 SDK 默认行为不一致，可能静默破坏导出。
    """
    from langfuse._client import span_processor

    captured = {}

    class _CapturingExporter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def shutdown(self):  # BatchSpanProcessor 关闭时会调用
            pass

    monkeypatch.setattr(span_processor, "OTLPSpanExporter", _CapturingExporter)

    # 直接构造 processor（不构造 Langfuse client），避免污染全局 TracerProvider。
    processor = span_processor.LangfuseSpanProcessor(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="http://lf:3000",
        timeout=7,
    )
    try:
        ours = exporter._otel_exporter_kwargs(_snapshot())
        assert ours["endpoint"] == captured["endpoint"]
        assert ours["headers"] == captured["headers"]
        assert ours["timeout"] == captured["timeout"]
    finally:
        processor.shutdown()


def test_endpoint_uses_langfuse_otel_path():
    kwargs = exporter._otel_exporter_kwargs(_snapshot(host="https://us.cloud.langfuse.com/"))
    assert kwargs["endpoint"] == "https://us.cloud.langfuse.com/api/public/otel/v1/traces"
    assert kwargs["headers"]["Authorization"].startswith("Basic ")
    assert kwargs["headers"]["x-langfuse-public-key"] == "pk-lf-test"
    assert kwargs["timeout"] == 7


def test_build_span_exporter_wraps_inner():
    built = exporter.build_span_exporter(_snapshot())
    assert isinstance(built, exporter.ObservableSpanExporter)
