"""走真实 OTel 管线的链路自检。

为什么坚持走 OTel 而不是 Langfuse 的原生上报 API：平台真正依赖的是
「全局 TracerProvider → should_export_span 白名单/采样 → LangfuseSpanProcessor → OTLP」
这条链路。原生 API 会绕过它，自检通过也无法证明业务链路可用。

判定分两层：
- 层 1（本模块）：flush 后对比导出健康度计数，判断导出是否被 Langfuse 接收；
- 层 2（``manager.query_trace_exists``）：稍后反查 trace 是否已可查询（摄取是异步的）。
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.services.ai.observability import export_health, manager, settings

logger = logging.getLogger(__name__)

# 自检 span 的标记属性：既是「绕过采样」的依据，也让它能在 Langfuse 里被一眼认出。
SELFCHECK_MARKER_ATTRIBUTE = "langfuse.trace.metadata.nanzi_selfcheck"
SELFCHECK_SPAN_NAME = "nanzi.selfcheck"
SELFCHECK_CHILD_SPAN_NAME = "nanzi.selfcheck.child"

TRACER_NAME = "nanzi.llm"


@dataclass(frozen=True)
class SelfCheckResult:
    """层 1 的自检结论。"""

    ok: bool
    trace_id: Optional[str]
    export_confirmed: bool
    message: str


def _emit_selfcheck_spans() -> Optional[str]:
    """发出自检 trace（根 + 一个子 span，顺带验证父子层级），返回 32 位 hex trace id。"""
    from opentelemetry import trace as otel_trace

    tracer = otel_trace.get_tracer(TRACER_NAME)
    attributes = {
        "langfuse.trace.name": SELFCHECK_SPAN_NAME,
        SELFCHECK_MARKER_ATTRIBUTE: True,
        "langfuse.trace.metadata.nanzi_selfcheck_at": datetime.now(timezone.utc).isoformat(),
        "langfuse.observation.type": "span",
    }

    with tracer.start_as_current_span(SELFCHECK_SPAN_NAME, attributes=attributes) as span:
        # 子 span 用来验证 OTel 上下文传递（一期未验证项之一）。
        with tracer.start_as_current_span(SELFCHECK_CHILD_SPAN_NAME) as child:
            child.set_attribute("langfuse.observation.type", "span")
        return manager._span_trace_id_hex(span)


async def run_selfcheck() -> SelfCheckResult:
    """执行一次链路自检，返回层 1 结论。

    要求追踪已启用（存在真实 client）：自检的意义就是验证**当前生效**的链路；
    「保存前验证」由「测试连接」承担。
    """
    if not settings.is_tracing_ready():
        return SelfCheckResult(False, None, False, "追踪未启用，请先保存并启用链路追踪")

    client = manager.current_client()
    if client is None:
        return SelfCheckResult(False, None, False, "进程内 client 尚未就绪，请稍后重试")

    before = export_health.snapshot()

    try:
        trace_id = _emit_selfcheck_spans()
    except Exception as exc:
        logger.warning("Langfuse 自检 span 构造失败: %s", exc)
        return SelfCheckResult(False, None, False, f"自检 trace 构造失败：{exc}")

    try:
        # flush 内部是同步的 force_flush，必须丢到线程里执行（勿在事件循环同步调用）。
        await asyncio.to_thread(client.flush)
    except Exception as exc:
        logger.warning("Langfuse 自检 flush 失败: %s", exc)
        return SelfCheckResult(False, trace_id, False, f"导出等待失败：{exc}")

    after = export_health.snapshot()
    if after.success_count > before.success_count:
        if trace_id is None:
            # 极少数情况：全局 TracerProvider 未生效，拿不到可反查的 trace id。
            # 层 1 仍然可信（导出确实发生），但要如实告知无法自动反查。
            return SelfCheckResult(
                True,
                None,
                True,
                "导出成功（未能取得 trace id，无法自动反查，请到 Langfuse 控制台确认）",
            )
        return SelfCheckResult(
            True, trace_id, True, "导出成功，Langfuse 已接收（正在等待其完成摄取）"
        )
    if after.failure_count > before.failure_count:
        return SelfCheckResult(
            False, trace_id, False, f"导出失败：{after.last_error or '未记录到具体原因'}"
        )
    return SelfCheckResult(
        False,
        trace_id,
        False,
        "未观察到导出：可能被采样丢弃、被白名单过滤，或导出队列异常",
    )
