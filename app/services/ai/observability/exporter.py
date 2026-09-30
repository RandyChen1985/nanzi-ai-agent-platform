"""把 Langfuse 的 OTLP 导出结果变成平台可观测的信号。

为什么需要它：Langfuse SDK 没有「导出成功/失败」回调，一期因此砍掉了
``langfuse_last_export``。但 SDK 允许通过公开参数 ``span_exporter`` 注入自定义 exporter，
而它的 ``export()`` 返回值正是每次导出的结果——这就是我们要的观测点。

注入的 exporter 会被 SDK 再包一层 ``LangfuseTransformingSpanExporter``，因此我们的
``export()`` 收到的是**已完成 Langfuse 转换、即将发出的 span**。

⚠️ 维护须知：``_otel_exporter_kwargs`` 复刻了 SDK 内部的构造逻辑。升级 ``langfuse``
前必须跑 ``test_exporter.py::test_otel_exporter_kwargs_match_sdk_defaults``。
"""

import base64
import logging
import os
from typing import Any, Dict

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from app.services.ai.observability import export_health

logger = logging.getLogger(__name__)

# 与 SDK 一致：导出路径可由环境变量覆盖（保留是为了与 SDK 行为完全相同）。
ENV_OTEL_TRACES_EXPORT_PATH = "LANGFUSE_OTEL_TRACES_EXPORT_PATH"
DEFAULT_OTEL_TRACES_EXPORT_PATH = "api/public/otel/v1/traces"


def _otel_exporter_kwargs(snapshot: Any) -> Dict[str, Any]:
    """复刻 ``LangfuseSpanProcessor`` 中 OTLP exporter 的构造参数。

    与 ``langfuse==4.15.6`` 的 ``_client/span_processor.py`` 逐行对齐：
    endpoint 用 host 拼接（可被环境变量覆盖），headers 含 Basic 鉴权与三个 SDK 标识头。
    """
    from langfuse._version import __version__ as langfuse_version

    basic_auth_header = "Basic " + base64.b64encode(
        f"{snapshot.public_key}:{snapshot.secret_key}".encode("utf-8")
    ).decode("ascii")

    headers = {
        "Authorization": basic_auth_header,
        "x-langfuse-sdk-name": "python",
        "x-langfuse-sdk-version": langfuse_version,
        "x-langfuse-public-key": snapshot.public_key,
    }

    base_url = str(snapshot.host).strip().rstrip("/")
    export_path = os.environ.get(ENV_OTEL_TRACES_EXPORT_PATH) or DEFAULT_OTEL_TRACES_EXPORT_PATH

    return {
        "endpoint": f"{base_url}/{export_path}",
        "headers": headers,
        "timeout": snapshot.timeout_seconds,
    }


class ObservableSpanExporter(SpanExporter):
    """包装内层 exporter，把每次导出结果写进 :mod:`export_health`。

    两条约束与健康度模块一致：本类跑在 OTel 导出线程上，**绝不能把异常抛出去**。
    """

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception as exc:
            logger.warning("Langfuse span 导出异常: %s", exc)
            export_health.record_failure(exc)
            return SpanExportResult.FAILURE

        if result == SpanExportResult.SUCCESS:
            export_health.record_success()
        else:
            export_health.record_failure(RuntimeError(f"导出返回 {result}"))
        return result

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception as exc:
            logger.warning("Langfuse span exporter 关闭异常（忽略）: %s", exc)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """透传 OTel 的 force_flush（内层未实现时视为成功）。"""
        inner_flush = getattr(self._inner, "force_flush", None)
        if inner_flush is None:
            return True
        try:
            return bool(inner_flush(timeout_millis))
        except Exception as exc:
            logger.warning("Langfuse span exporter force_flush 异常（忽略）: %s", exc)
            return False


def build_span_exporter(snapshot: Any) -> SpanExporter:
    """构造注入给 Langfuse SDK 的 exporter（与 SDK 默认参数一致 + 结果观测）。"""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    inner = OTLPSpanExporter(**_otel_exporter_kwargs(snapshot))
    return ObservableSpanExporter(inner)
