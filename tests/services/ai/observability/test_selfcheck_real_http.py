"""真实 HTTP 实测：不 mock 网络，用一个假 Langfuse 服务验证导出判定。

延续一期的验证习惯：单元测试全部 mock 网络，因此这里补一次真发请求的检查，
确认「成功 → 记为成功」「服务端 5xx → 记为失败并留下原因」这两条判定真的成立。
"""

import http.server
import socketserver
import threading

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.services.ai.observability import export_health, exporter, settings

pytestmark = pytest.mark.no_infrastructure


class _Handler(http.server.BaseHTTPRequestHandler):
    status_code = 200

    def do_POST(self):  # noqa: N802 - http.server 约定
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(_Handler.status_code)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):  # 静默，避免污染测试输出
        pass


@pytest.fixture
def fake_langfuse():
    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _real_spans():
    """用独立 provider 造出真实的 ReadableSpan（OTLP exporter 只接受真实 span）。

    刻意不调用 ``otel_trace.set_tracer_provider``：那会污染全局 provider，
    影响同进程的其它用例。
    """
    collector = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(collector))
    tracer = provider.get_tracer("nanzi.llm")
    with tracer.start_as_current_span("selfcheck-real-http"):
        pass
    spans = collector.get_finished_spans()
    assert spans, "未能生成用于导出的真实 span"
    return list(spans)


def _snapshot(host):
    return settings.LangfuseSnapshot(
        enabled=True,
        host=host,
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        timeout_seconds=5,
        status=settings.STATUS_ENABLED,
    )


def test_real_export_success_is_recorded(fake_langfuse):
    _Handler.status_code = 200
    export_health.reset_for_tests()

    span_exporter = exporter.build_span_exporter(_snapshot(fake_langfuse))
    try:
        span_exporter.export(_real_spans())
    finally:
        span_exporter.shutdown()

    snap = export_health.snapshot()
    assert snap.success_count == 1
    assert snap.failure_count == 0


def test_real_export_server_error_is_recorded(fake_langfuse):
    _Handler.status_code = 500
    export_health.reset_for_tests()

    span_exporter = exporter.build_span_exporter(_snapshot(fake_langfuse))
    try:
        span_exporter.export(_real_spans())
    finally:
        span_exporter.shutdown()

    snap = export_health.snapshot()
    assert snap.failure_count == 1, "服务端 5xx 必须记为失败"
    assert snap.last_error, "失败必须留下可读原因"
