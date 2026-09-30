# Langfuse 链路自检与导出健康度 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在系统设置页提供「发送测试 trace」自检（走真实 OTel 管线），并对真实业务 span 的导出成败做持续观测。

**Architecture:** 新增三个单一职责模块——`export_health`（线程安全的进程内健康度）、`exporter`（包装 OTLP exporter 记录导出结果）、`selfcheck`（构造自检 trace 并判定层 1 结果）。自检 span 带专用标记以绕过采样；层 2（是否可在 Langfuse 查到）由前端轮询后端反查接口完成。顺带移除一期遗留的死配置 `trace_url_template`。

**Tech Stack:** Python 3.11 / FastAPI / OpenTelemetry SDK / langfuse==4.15.6 / pytest（`asyncio_mode=auto`）/ Vue 3 + TS + Tailwind。

**设计依据：** `docs/superpowers/specs/2026-09-30-langfuse-selfcheck-design.md`

**提交约定：** 计划中的 commit 步骤遵循本项目规范——**仅当用户明确要求时才执行提交**；否则改用 `git add` 暂存或直接汇报改动。

**关键常量（全文统一，勿改名）**

| 名称 | 值 |
|---|---|
| `export_health.ExportHealthSnapshot` | 健康度快照数据类 |
| `export_health.record_success()` / `record_failure(err)` / `snapshot()` / `reset_for_tests()` | 健康度读写 |
| `exporter.ObservableSpanExporter` | 包装型 exporter |
| `exporter.build_span_exporter(snapshot)` | 构造注入用 exporter |
| `exporter._otel_exporter_kwargs(snapshot)` | 复刻 SDK 的构造参数（供防漂移测试比对） |
| `selfcheck.SELFCHECK_MARKER_ATTRIBUTE` | `"langfuse.trace.metadata.nanzi_selfcheck"` |
| `selfcheck.SELFCHECK_SPAN_NAME` | `"nanzi.selfcheck"` |
| `selfcheck.run_selfcheck()` | 执行自检 |
| `selfcheck.SelfCheckResult` | 自检结果数据类 |
| `manager.query_trace_exists(trace_id)` | 层 2 反查 |
| `manager.make_should_export_span()` | 自检放行改造点 |

---

## Task 1: `export_health` 模块

**Files:**
- Create: `app/services/ai/observability/export_health.py`
- Test: `tests/services/ai/observability/test_export_health.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/services/ai/observability/test_export_health.py`：

```python
"""导出健康度：计数、连续失败、错误截断与并发安全。"""

import threading

from app.services.ai.observability import export_health


def setup_function():
    export_health.reset_for_tests()


def test_initial_snapshot_is_empty():
    snap = export_health.snapshot()
    assert snap.success_count == 0
    assert snap.failure_count == 0
    assert snap.consecutive_failures == 0
    assert snap.last_success_at is None
    assert snap.last_failure_at is None
    assert snap.last_error is None


def test_record_success_sets_timestamp_and_counts():
    export_health.record_success()
    snap = export_health.snapshot()
    assert snap.success_count == 1
    assert snap.last_success_at is not None
    assert snap.consecutive_failures == 0


def test_record_failure_accumulates_and_keeps_reason():
    export_health.record_failure(RuntimeError("boom"))
    export_health.record_failure(RuntimeError("again"))
    snap = export_health.snapshot()
    assert snap.failure_count == 2
    assert snap.consecutive_failures == 2
    assert snap.last_failure_at is not None
    assert "again" in snap.last_error


def test_success_resets_consecutive_failures_but_keeps_last_error():
    """成功后连续失败清零，但「最近一次失败原因」保留为历史记录。"""
    export_health.record_failure(RuntimeError("first"))
    export_health.record_success()
    snap = export_health.snapshot()
    assert snap.consecutive_failures == 0
    assert snap.success_count == 1
    assert snap.failure_count == 1
    assert "first" in snap.last_error


def test_error_message_is_truncated():
    export_health.record_failure(RuntimeError("x" * 5000))
    assert len(export_health.snapshot().last_error) <= export_health.MAX_ERROR_LENGTH


def test_record_never_raises_on_exotic_error():
    """record_* 跑在导出线程上，抛异常会干扰导出，因此必须自兜底。"""

    class _BadError(BaseException):
        def __str__(self):
            raise RuntimeError("无法转成字符串")

    export_health.record_failure(_BadError())  # 不得抛出


def test_concurrent_writes_do_not_lose_counts():
    def worker():
        for _ in range(200):
            export_health.record_success()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert export_health.snapshot().success_count == 800
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_export_health.py -q`
Expected: FAIL —— `ModuleNotFoundError: app.services.ai.observability.export_health`

- [ ] **Step 3: 实现**

创建 `app/services/ai/observability/export_health.py`：

```python
"""进程内的 Langfuse 导出健康度。

写入方是 OTel 的**导出线程**（每次 exporter.export 之后），读取方是**请求线程**
（状态卡片接口），因此状态由一个 ``threading.Lock`` 保护。

两条硬约束：

1. ``record_success`` / ``record_failure`` **绝不抛异常** —— 它们跑在导出线程上，
   抛出去会干扰 span 导出，把「可观测性」变成「故障源」。
2. ``last_error`` 是**最近一次失败的原因**，成功后不清空（它是历史记录）；当前是否健康
   应由 ``consecutive_failures == 0`` 判断。
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# 错误信息入库/上屏前的截断长度，避免把整段堆栈塞进接口响应。
MAX_ERROR_LENGTH = 500


@dataclass(frozen=True)
class ExportHealthSnapshot:
    """某一时刻的导出健康度。不可变，便于跨线程安全传递。"""

    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    last_error: Optional[str] = None
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0


_lock = threading.Lock()
_state = ExportHealthSnapshot()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _describe(error: BaseException) -> str:
    """把异常转成可读文本；异常本身 ``__str__`` 出错时退回类型名。"""
    try:
        text = f"{type(error).__name__}: {error}"
    except Exception:
        text = type(error).__name__
    return text[:MAX_ERROR_LENGTH]


def record_success() -> None:
    """记录一次成功导出（连续失败清零，保留历史错误）。"""
    global _state
    try:
        with _lock:
            _state = ExportHealthSnapshot(
                last_success_at=_now(),
                last_failure_at=_state.last_failure_at,
                last_error=_state.last_error,
                success_count=_state.success_count + 1,
                failure_count=_state.failure_count,
                consecutive_failures=0,
            )
    except Exception as exc:  # pragma: no cover - 记录失败不能反过来影响导出
        logger.warning("Langfuse 导出健康度记录成功时异常（忽略）: %s", exc)


def record_failure(error: BaseException) -> None:
    """记录一次失败导出（累加连续失败并保留原因）。"""
    global _state
    try:
        message = _describe(error)
        with _lock:
            _state = ExportHealthSnapshot(
                last_success_at=_state.last_success_at,
                last_failure_at=_now(),
                last_error=message,
                success_count=_state.success_count,
                failure_count=_state.failure_count + 1,
                consecutive_failures=_state.consecutive_failures + 1,
            )
    except Exception as exc:  # pragma: no cover - 同上
        logger.warning("Langfuse 导出健康度记录失败时异常（忽略）: %s", exc)


def snapshot() -> ExportHealthSnapshot:
    """读取当前健康度（线程安全）。"""
    with _lock:
        return _state


def reset_for_tests() -> None:
    """仅测试使用：清空健康度。"""
    global _state
    with _lock:
        _state = ExportHealthSnapshot()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_export_health.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add app/services/ai/observability/export_health.py tests/services/ai/observability/test_export_health.py
git commit -m "feat(ai): 新增 Langfuse 导出健康度状态模块"
```

---

## Task 2: 可观测 exporter 与防漂移测试

**Files:**
- Create: `app/services/ai/observability/exporter.py`
- Test: `tests/services/ai/observability/test_exporter.py`

**背景：** 我们要把自己的 exporter 通过 SDK 公开参数 `span_exporter` 传入，因此必须**复刻** SDK 内部的 `OTLPSpanExporter` 构造参数。SDK 一旦漂移而我们没跟上，可能静默破坏导出——所以本任务的防漂移测试是整份计划里最关键的一条测试。

- [ ] **Step 1: 写失败测试**

创建 `tests/services/ai/observability/test_exporter.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_exporter.py -q`
Expected: FAIL —— `ModuleNotFoundError: app.services.ai.observability.exporter`

- [ ] **Step 3: 实现**

创建 `app/services/ai/observability/exporter.py`：

```python
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

# 与 SDK 一致：这两个可由环境变量覆盖（保留是为了与 SDK 行为完全相同）。
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_exporter.py -q`
Expected: PASS（7 passed）

若 `test_otel_exporter_kwargs_match_sdk_defaults` 失败，说明本机 SDK 版本与计划编写时（4.15.6）不一致，**以该测试的实际捕获值为准修正 `_otel_exporter_kwargs`**，切勿放宽断言。

- [ ] **Step 5: 提交**

```bash
git add app/services/ai/observability/exporter.py tests/services/ai/observability/test_exporter.py
git commit -m "feat(ai): 注入可观测 OTLP exporter 并加 SDK 参数防漂移测试"
```

---

## Task 3: manager 接入 exporter 与自检放行

**Files:**
- Modify: `app/services/ai/observability/manager.py`（`_build_client`、`make_should_export_span`）
- Modify: `tests/services/ai/observability/test_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/services/ai/observability/test_manager.py` 末尾追加：

```python
# ------------------------------------------------- 导出 exporter 与自检放行


def test_build_client_passes_observable_span_exporter(monkeypatch):
    """client 必须注入可观测 exporter，否则导出健康度永远为空。"""
    captured = {}
    _install_fake_client(monkeypatch, captured)
    snapshot = settings.LangfuseSnapshot(
        enabled=True,
        host="http://lf:3000",
        public_key="pk-lf",
        secret_key="sk-lf",
        timeout_seconds=5,
        status=settings.STATUS_ENABLED,
    )

    from app.services.ai.observability import exporter

    manager._build_client(snapshot)

    assert "span_exporter" in captured["init_kwargs"]
    assert isinstance(captured["init_kwargs"]["span_exporter"], exporter.ObservableSpanExporter)


class _FakeSpan:
    def __init__(self, scope_name, attributes):
        self.instrumentation_scope = type("S", (), {"name": scope_name})()
        self.attributes = attributes


def test_should_export_span_allows_selfcheck_even_when_sample_rate_is_zero(monkeypatch):
    """自检必须绕过采样：否则 sample_rate=0 时自检会「假失败」。"""

    async def fake_load():
        return _row(sample_rate=0.0)

    monkeypatch.setattr(config_store, "load_config", fake_load)
    hook = manager.make_should_export_span()

    selfcheck_span = _FakeSpan(
        "nanzi.llm",
        {
            selfcheck_module.SELFCHECK_MARKER_ATTRIBUTE: True,
            "langfuse.trace.name": selfcheck_module.SELFCHECK_SPAN_NAME,
        },
    )
    ordinary_span = _FakeSpan("nanzi.llm", {"langfuse.trace.name": "chat.turn"})

    assert hook(selfcheck_span) is True
    assert hook(ordinary_span) is False
```

并在该文件顶部导入区补上：

```python
from app.services.ai.observability import selfcheck as selfcheck_module
```

> 注：`_row` 与 `_install_fake_client` 是该文件已有的测试辅助函数；`_install_fake_client` 记录的 `captured["init_kwargs"]` 正是传给 `Langfuse(...)` 的关键字参数。

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_manager.py -q -k "span_exporter or selfcheck"`
Expected: FAIL —— 自检放行断言失败 / `selfcheck` 模块尚不存在

- [ ] **Step 3: 实现**

先创建 `selfcheck` 模块的**常量与标记**（完整实现见 Task 4；此处先落标记常量以便 manager 引用）：

在 `app/services/ai/observability/selfcheck.py` 中写入：

```python
"""走真实 OTel 管线的链路自检（完整实现见后续任务）。"""

# 自检 span 的标记属性：既是「绕过采样」的依据，也让它能在 Langfuse 里被一眼认出。
SELFCHECK_MARKER_ATTRIBUTE = "langfuse.trace.metadata.nanzi_selfcheck"
SELFCHECK_SPAN_NAME = "nanzi.selfcheck"
```

修改 `manager.py` 的 `_build_client`：

```python
def _build_client(snapshot: "settings.LangfuseSnapshot") -> Any:
    from langfuse import Langfuse

    from app.services.ai.observability import exporter

    return Langfuse(
        host=snapshot.host,
        public_key=snapshot.public_key,
        secret_key=snapshot.secret_key,
        timeout=snapshot.timeout_seconds,
        environment=snapshot.environment,
        release=snapshot.release,
        should_export_span=make_should_export_span(),
        mask_otel_spans=make_mask_otel_spans(),
        # 注入自己的 exporter 以观测每次导出结果（参数与 SDK 默认一致）。
        span_exporter=exporter.build_span_exporter(snapshot),
    )
```

修改 `manager.py` 的 `make_should_export_span`，在白名单之后、采样之前插入自检放行：

```python
    def should_export_span(span: Any) -> bool:
        try:
            snapshot = settings.get_snapshot()
            if snapshot.status != settings.STATUS_ENABLED:
                return False
            attributes = _span_attributes(span)
            if not _is_allowed_scope(_span_scope_name(span), attributes):
                return False
            # 自检 trace 必须绕过采样，否则 sample_rate<1 时自检会假失败。
            from app.services.ai.observability import selfcheck

            if attributes.get(selfcheck.SELFCHECK_MARKER_ATTRIBUTE):
                return True
            return gate.is_sampled(_span_trace_id_hex(span), snapshot.sample_rate)
        except Exception as exc:  # 钩子抛异常会导致整批丢弃，必须自兜底
            logger.warning("Langfuse should_export_span 判定异常，已丢弃该 span: %s", exc)
            return False
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_manager.py -q`
Expected: PASS（全部通过，含既有用例）

- [ ] **Step 5: 提交**

```bash
git add app/services/ai/observability/manager.py app/services/ai/observability/selfcheck.py tests/services/ai/observability/test_manager.py
git commit -m "feat(ai): client 注入可观测 exporter，自检 span 绕过采样"
```

---

## Task 4: `run_selfcheck` 自检执行

**Files:**
- Modify: `app/services/ai/observability/selfcheck.py`（补全实现）
- Test: `tests/services/ai/observability/test_selfcheck.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/services/ai/observability/test_selfcheck.py`：

```python
"""自检：前置校验、span 结构、层 1 判定的三种分支，以及不阻塞事件循环。"""

import asyncio
import time
from datetime import datetime, timezone

import pytest

from app.services.ai.observability import export_health, manager, selfcheck, settings


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
            "trace_url_template": None,
        }

    monkeypatch.setattr("app.services.ai.observability.config_store.load_config", fake_load)
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
    await _enable(monkeypatch, outcome="success")
    result = await selfcheck.run_selfcheck()
    assert result.ok is True
    assert result.export_confirmed is True
    assert result.trace_id is not None
    assert len(result.trace_id) == 32
    int(result.trace_id, 16)  # 必须是 32 位十六进制


async def test_failure_reports_reason(monkeypatch):
    await _enable(monkeypatch, outcome="failure")
    result = await selfcheck.run_selfcheck()
    assert result.ok is False
    assert result.export_confirmed is False
    assert "连接被拒绝" in result.message
    assert result.trace_id is not None


async def test_no_export_observed_is_reported(monkeypatch):
    await _enable(monkeypatch, outcome="nothing")
    result = await selfcheck.run_selfcheck()
    assert result.ok is False
    assert "未观察到导出" in result.message


async def test_flush_does_not_block_event_loop(monkeypatch):
    """flush 是同步阻塞调用，必须丢到线程里执行。

    回归动机同 `90eb309a`：在事件循环线程里同步 flush 会把并发的对话一起卡住。
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_selfcheck.py -q`
Expected: FAIL —— `run_selfcheck` / `SelfCheckResult` 尚不存在

- [ ] **Step 3: 实现**

用以下内容替换 `app/services/ai/observability/selfcheck.py`：

```python
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

    要求追踪已启用（存在真实 client）：自检的意义就是验证**当前生效**的链路。
    "保存前验证"由「测试连接」承担。
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_selfcheck.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add app/services/ai/observability/selfcheck.py tests/services/ai/observability/test_selfcheck.py
git commit -m "feat(ai): 实现走真实 OTel 管线的链路自检"
```

---

## Task 5: `query_trace_exists` 层 2 反查

**Files:**
- Modify: `app/services/ai/observability/manager.py`（新增函数）
- Modify: `tests/services/ai/observability/test_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/services/ai/observability/test_manager.py` 末尾追加：

```python
# ------------------------------------------------- 层 2：反查 trace 是否可查询


class _FakeTraceApi:
    def __init__(self, outcome="found"):
        self.outcome = outcome
        self.calls = []

    async def get(self, trace_id):
        self.calls.append(trace_id)
        if self.outcome == "found":
            return {"id": trace_id}
        if self.outcome == "not_found":
            from langfuse.api import NotFoundError

            raise NotFoundError("not found")
        raise RuntimeError("鉴权失败")


class _FakeApiNamespace:
    def __init__(self, trace_api):
        self.trace = trace_api


class _FakeClientWithApi:
    def __init__(self, outcome="found"):
        self.trace_api = _FakeTraceApi(outcome)
        self.async_api = _FakeApiNamespace(self.trace_api)


async def test_query_trace_exists_found(monkeypatch):
    client = _FakeClientWithApi("found")
    monkeypatch.setattr(manager, "current_client", lambda: client)
    found, message = await manager.query_trace_exists("a" * 32)
    assert found is True
    assert client.trace_api.calls == ["a" * 32]
    assert "已到达" in message


async def test_query_trace_exists_not_found_yet(monkeypatch):
    client = _FakeClientWithApi("not_found")
    monkeypatch.setattr(manager, "current_client", lambda: client)
    found, message = await manager.query_trace_exists("b" * 32)
    assert found is False
    assert "尚未" in message


async def test_query_trace_exists_error_is_distinguished(monkeypatch):
    """查询失败必须与「未查到」区分，否则会一直傻等。"""
    client = _FakeClientWithApi("error")
    monkeypatch.setattr(manager, "current_client", lambda: client)
    found, message = await manager.query_trace_exists("c" * 32)
    assert found is False
    assert "查询失败" in message


async def test_query_trace_exists_without_client(monkeypatch):
    manager.reset_for_tests()
    found, message = await manager.query_trace_exists("d" * 32)
    assert found is False
    assert "未启用" in message
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_manager.py -q -k query_trace`
Expected: FAIL —— `AttributeError: module ... has no attribute 'query_trace_exists'`

- [ ] **Step 3: 实现**

在 `manager.py` 的「连通性探测」小节之后新增：

```python
# --------------------------------------------------------------------------- 层 2 反查


async def _fetch_trace(client: Any, trace_id: str) -> Any:
    """调用 Langfuse 查询 API；优先用异步变体，避免阻塞事件循环。"""
    async_api = getattr(client, "async_api", None)
    if async_api is not None:
        return await async_api.trace.get(trace_id)
    # 回退：SDK 未提供异步变体时，把同步调用丢到线程里。
    return await asyncio.to_thread(client.api.trace.get, trace_id)


async def query_trace_exists(trace_id: str) -> Tuple[bool, str]:
    """反查 trace 是否已在 Langfuse 可查询。

    摄取是异步的（SDK 文档：典型 15~30 秒，负载高时更久），因此「未查到」是正常的
    中间态，调用方应带截止时间轮询。**必须把「未查到」与「查询失败」区分开**，
    否则鉴权/网络问题会被误当成「再等等就好」。
    """
    client = current_client()
    if client is None:
        return (False, "追踪未启用或 client 尚未就绪")

    try:
        await _fetch_trace(client, trace_id)
    except Exception as exc:
        try:
            from langfuse.api import NotFoundError
        except Exception:  # pragma: no cover - SDK 异常类型不可用时退化为按名字判断
            NotFoundError = None  # type: ignore[assignment]

        if NotFoundError is not None and isinstance(exc, NotFoundError):
            return (False, "尚未查到，Langfuse 可能仍在处理（摄取有延迟）")
        logger.warning("Langfuse trace 反查失败: %s", exc)
        return (False, f"查询失败：{type(exc).__name__}: {exc}")

    return (True, "已到达 Langfuse，可以打开控制台查看")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_manager.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/ai/observability/manager.py tests/services/ai/observability/test_manager.py
git commit -m "feat(ai): 新增 Langfuse trace 反查，区分未查到与查询失败"
```

---

## Task 6: 接口（自检、反查、健康度）

**Files:**
- Modify: `app/api/portal/endpoints/langfuse.py`
- Test: `tests/api/portal/test_langfuse_selfcheck_api.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/api/portal/test_langfuse_selfcheck_api.py`（沿用同目录既有文件的风格：`AsyncClient` + `X-API-Key` 鉴权头、`@pytest.mark.asyncio`）：

```python
"""自检接口契约测试。

与 ``test_langfuse_config_api.py`` 相同的隔离策略：**绝不构造真实 Langfuse client**
（构造它会注册全局 TracerProvider，污染同进程后续用例），自检与反查全部打桩。
"""

import pytest
from httpx import AsyncClient

from app.services.ai.observability import config_store, export_health, manager, selfcheck, settings

SELFCHECK_URL = "/api/portal/system/langfuse/selfcheck"
CONFIG_URL = "/api/portal/system/langfuse"


@pytest.fixture(autouse=True)
def _hermetic_observability(monkeypatch):
    settings.reset_for_tests()
    manager.reset_for_tests()
    export_health.reset_for_tests()
    monkeypatch.setattr(manager, "_build_client", lambda _snapshot: object())
    yield
    settings.reset_for_tests()
    manager.reset_for_tests()
    export_health.reset_for_tests()


@pytest.mark.asyncio
async def test_selfcheck_requires_api_key(client: AsyncClient):
    response = await client.post(SELFCHECK_URL)
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_selfcheck_requires_dedicated_element_permission(
    client: AsyncClient, valid_api_key: str
):
    """自检会向外部系统发数据，必须有 element:system:langfuse_save。"""
    response = await client.post(SELFCHECK_URL, headers={"X-API-Key": valid_api_key})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_selfcheck_reports_disabled(client: AsyncClient, admin_api_key: str, monkeypatch):
    async def fake_run():
        return selfcheck.SelfCheckResult(False, None, False, "追踪未启用，请先保存并启用链路追踪")

    monkeypatch.setattr(selfcheck, "run_selfcheck", fake_run)
    response = await client.post(SELFCHECK_URL, headers={"X-API-Key": admin_api_key})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["trace_id"] is None
    assert "未启用" in body["message"]


@pytest.mark.asyncio
async def test_selfcheck_returns_trace_id(client: AsyncClient, admin_api_key: str, monkeypatch):
    async def fake_run():
        return selfcheck.SelfCheckResult(True, "a" * 32, True, "导出成功")

    monkeypatch.setattr(selfcheck, "run_selfcheck", fake_run)
    body = (
        await client.post(SELFCHECK_URL, headers={"X-API-Key": admin_api_key})
    ).json()
    assert body["ok"] is True
    assert body["export_confirmed"] is True
    assert body["trace_id"] == "a" * 32


@pytest.mark.asyncio
async def test_selfcheck_survives_internal_error(client: AsyncClient, admin_api_key: str,
                                                 monkeypatch):
    """自检内部异常也必须返回结构化结果，而不是 500。"""

    async def boom():
        raise RuntimeError("意外错误")

    monkeypatch.setattr(selfcheck, "run_selfcheck", boom)
    response = await client.post(SELFCHECK_URL, headers={"X-API-Key": admin_api_key})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "意外错误" in body["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["zz", "a" * 31, "a" * 33, "G" * 32])
async def test_reverse_lookup_rejects_malformed_trace_id(
    client: AsyncClient, admin_api_key: str, bad: str
):
    response = await client.get(
        f"{SELFCHECK_URL}/{bad}", headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reverse_lookup_found(client: AsyncClient, admin_api_key: str, monkeypatch):
    async def fake_query(trace_id):
        return (True, "已到达 Langfuse，可以打开控制台查看")

    monkeypatch.setattr(manager, "query_trace_exists", fake_query)
    body = (
        await client.get(f"{SELFCHECK_URL}/{'a' * 32}", headers={"X-API-Key": admin_api_key})
    ).json()
    assert body["found"] is True


@pytest.mark.asyncio
async def test_reverse_lookup_reports_pending(client: AsyncClient, admin_api_key: str,
                                              monkeypatch):
    async def fake_query(trace_id):
        return (False, "尚未查到，Langfuse 可能仍在处理（摄取有延迟）")

    monkeypatch.setattr(manager, "query_trace_exists", fake_query)
    body = (
        await client.get(f"{SELFCHECK_URL}/{'b' * 32}", headers={"X-API-Key": admin_api_key})
    ).json()
    assert body["found"] is False
    assert "尚未查到" in body["message"]


@pytest.mark.asyncio
async def test_get_config_includes_export_health(client: AsyncClient, admin_api_key: str,
                                                 monkeypatch):
    async def fake_load():
        return config_store.normalize_payload({})

    monkeypatch.setattr(config_store, "load_config", fake_load)
    export_health.record_failure(RuntimeError("boom"))
    export_health.record_success()

    health = (
        await client.get(CONFIG_URL, headers={"X-API-Key": admin_api_key})
    ).json()["export_health"]
    assert health["success_count"] == 1
    assert health["failure_count"] == 1
    assert health["consecutive_failures"] == 0  # 成功之后连续失败清零
    assert health["last_error"], "最近一次失败原因应保留为历史记录"
    assert health["last_success_at"] is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/api/portal/test_langfuse_selfcheck_api.py -q`
Expected: FAIL —— 404（路由不存在）

- [ ] **Step 3: 实现**

修改 `app/api/portal/endpoints/langfuse.py`：

顶部导入区追加：

```python
import re

from app.services.ai.observability import (
    config_store,
    export_health,
    manager,
    selfcheck,
    settings,
)

# Langfuse 的 trace id 是 32 位十六进制；用它做格式校验，避免任意字符串被拼进外部请求。
_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
```

在 `_runtime_status()` 之后新增健康度序列化：

```python
def _export_health_view() -> Dict[str, Any]:
    """导出健康度的公开视图（进程内状态，多实例各自独立）。"""
    snap = export_health.snapshot()
    return {
        "last_success_at": snap.last_success_at.isoformat() if snap.last_success_at else None,
        "last_failure_at": snap.last_failure_at.isoformat() if snap.last_failure_at else None,
        "last_error": snap.last_error,
        "success_count": snap.success_count,
        "failure_count": snap.failure_count,
        "consecutive_failures": snap.consecutive_failures,
    }
```

在 `_build_response()` 里追加一行（放在 `payload.update(_runtime_status())` 之后）：

```python
    payload["export_health"] = _export_health_view()
```

在文件末尾新增两个端点：

```python
@router.post("/selfcheck")
async def run_langfuse_selfcheck(
    user: Dict = Depends(require_permission(*WRITE_PERMISSION)),
):
    """发出一条走真实 OTel 管线的自检 trace，并返回层 1（导出是否被接收）结论。

    会等一次 ``flush``（成功时通常数百毫秒；导出失败时最坏一个 timeout），
    因此耗时可能达数秒——这是刻意的：只有等到导出结果才算真正验证。
    """
    del user
    try:
        result = await selfcheck.run_selfcheck()
    except Exception as exc:
        logger.warning("Langfuse 自检执行异常: %s", exc)
        return {
            "ok": False,
            "trace_id": None,
            "export_confirmed": False,
            "message": f"自检执行失败：{type(exc).__name__}: {exc}",
        }
    return {
        "ok": result.ok,
        "trace_id": result.trace_id,
        "export_confirmed": result.export_confirmed,
        "message": result.message,
    }


@router.get("/selfcheck/{trace_id}")
async def lookup_langfuse_selfcheck(
    trace_id: str,
    user: Dict = Depends(require_permission(*WRITE_PERMISSION)),
):
    """层 2：反查自检 trace 是否已在 Langfuse 可查询（摄取有延迟，需轮询）。"""
    del user
    if not _TRACE_ID_PATTERN.match(trace_id):
        raise HTTPException(status_code=400, detail="trace_id 必须是 32 位十六进制字符串")
    try:
        found, message = await manager.query_trace_exists(trace_id)
    except Exception as exc:
        logger.warning("Langfuse trace 反查异常: %s", exc)
        return {"found": False, "message": f"查询失败：{type(exc).__name__}: {exc}"}
    return {"found": found, "message": message}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/api/portal/test_langfuse_selfcheck_api.py tests/api/portal/test_langfuse_config_api.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/api/portal/endpoints/langfuse.py tests/api/portal/test_langfuse_selfcheck_api.py
git commit -m "feat(ai): 新增 Langfuse 自检与 trace 反查接口，配置接口返回导出健康度"
```

---

## Task 7: 前端自检按钮、轮询与健康度展示

**Files:**
- Modify: `frontend/src/components/system/LangfuseConfig.vue`
- Test: `tests/frontend/test_langfuse_selfcheck_contract.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/frontend/test_langfuse_selfcheck_contract.py`（沿用同目录既有契约测试的读取方式）：

```python
"""前端契约：自检按钮、层 2 轮询参数、卸载清理与健康度展示。"""

import pathlib
import re

COMPONENT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend/src/components/system/LangfuseConfig.vue"
)


def _source() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def test_selfcheck_button_is_gated_by_permission_and_switch():
    source = _source()
    assert "发送测试 trace" in source
    button = re.search(r"发送测试 trace[\s\S]{0,600}?</button>", source)
    assert button, "未找到自检按钮"
    # 按钮所在模板块必须同时受启用状态与权限约束
    region = source[max(0, button.start() - 800) : button.end()]
    assert "form.enabled" in region
    assert "canSave" in region


def test_polling_uses_deadline_and_cleans_up():
    source = _source()
    assert re.search(r"POLL_INTERVAL_MS\s*=\s*3_?000", source), "轮询间隔应为 3 秒"
    assert re.search(r"POLL_TIMEOUT_MS\s*=\s*60_?000", source), "轮询上限应为 60 秒"
    assert "onUnmounted" in source, "必须在卸载时清理轮询定时器"
    assert re.search(r"clearTimeout|clearInterval", source)


def test_export_health_section_is_rendered():
    source = _source()
    assert "导出健康度" in source
    assert "export_health" in source
    assert "进程内" in source, "健康度是进程内状态，界面必须标注"


def test_selfcheck_endpoints_are_called():
    source = _source()
    assert "`${ENDPOINT}/selfcheck`" in source, "层 1 应调用自检接口"
    assert "${ENDPOINT}/selfcheck/${traceId}" in source, "层 2 应调用反查接口"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/frontend/test_langfuse_selfcheck_contract.py --confcutdir=tests/frontend -q`
Expected: FAIL —— 未找到「发送测试 trace」等文案

- [ ] **Step 3: 实现**

修改 `frontend/src/components/system/LangfuseConfig.vue`：

(a) 导入区（第 8 行）改为：

```ts
import { computed, onMounted, onUnmounted, ref } from 'vue'
```

(b) `interface LangfuseConfigView` 内新增字段：

```ts
  export_health: ExportHealthView
```

并在接口定义上方新增：

```ts
interface ExportHealthView {
  last_success_at: string | null
  last_failure_at: string | null
  last_error: string | null
  success_count: number
  failure_count: number
  consecutive_failures: number
}
```

(c) 在 `const ENDPOINT = ...` 下方新增常量与状态：

```ts
const POLL_INTERVAL_MS = 3_000
const POLL_TIMEOUT_MS = 60_000

const selfChecking = ref(false)
const selfCheckResult = ref<{ ok: boolean; message: string } | null>(null)
const pollState = ref<'idle' | 'waiting' | 'found' | 'timeout' | 'error'>('idle')
let pollTimer: ReturnType<typeof setTimeout> | null = null
let pollDeadline = 0

const health = ref<ExportHealthView>({
  last_success_at: null,
  last_failure_at: null,
  last_error: null,
  success_count: 0,
  failure_count: 0,
  consecutive_failures: 0,
})

const healthTone = computed(() => {
  if (health.value.consecutive_failures > 0) return 'text-red-600'
  if (health.value.success_count > 0) return 'text-green-600'
  return 'text-gray-500'
})

const formatTime = (value: string | null) => {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

const stopPolling = () => {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}
```

(d) `applyView` 内新增（在 `secretKeyInput.value = ''` 之前）：

```ts
  health.value = data.export_health || health.value
```

(e) 在 `testConnection` 之后新增自检与轮询：

```ts
const pollSelfCheck = async (traceId: string) => {
  if (Date.now() > pollDeadline) {
    pollState.value = 'timeout'
    stopPolling()
    return
  }
  try {
    const response = await axios.get(`${ENDPOINT}/selfcheck/${traceId}`)
    const body = response.data as { found: boolean; message: string }
    if (body.found) {
      pollState.value = 'found'
      stopPolling()
      return
    }
    if (body.message?.includes('查询失败')) {
      pollState.value = 'error'
      selfCheckResult.value = { ok: false, message: body.message }
      stopPolling()
      return
    }
  } catch {
    // 单次轮询失败不终止整体等待，交给截止时间兜底
  }
  pollTimer = setTimeout(() => pollSelfCheck(traceId), POLL_INTERVAL_MS)
}

const sendSelfCheck = async () => {
  selfChecking.value = true
  selfCheckResult.value = null
  pollState.value = 'idle'
  stopPolling()
  try {
    const response = await axios.post(`${ENDPOINT}/selfcheck`)
    const body = response.data as { ok: boolean; trace_id: string | null; message: string }
    selfCheckResult.value = { ok: body.ok, message: body.message }
    showToast({ type: body.ok ? 'success' : 'error', message: body.message })
    if (body.trace_id) {
      pollState.value = 'waiting'
      pollDeadline = Date.now() + POLL_TIMEOUT_MS
      pollTimer = setTimeout(() => pollSelfCheck(body.trace_id as string), POLL_INTERVAL_MS)
    }
    await fetchConfig()
  } catch (error: any) {
    const message = error?.response?.data?.message || '自检请求失败'
    selfCheckResult.value = { ok: false, message }
    showToast({ type: 'error', message })
  } finally {
    selfChecking.value = false
  }
}

onUnmounted(stopPolling)
```

(f) 状态卡片内（`<p v-if="meta.status_detail" ...>` 之后）插入健康度区块：

```html
      <div class="mt-3 rounded-lg border border-gray-100 bg-gray-50 px-4 py-3 space-y-1">
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span class="font-medium text-gray-700">导出健康度</span>
          <span class="text-gray-400">（进程内状态，仅代表当前实例）</span>
          <span :class="healthTone">
            连续失败 {{ health.consecutive_failures }} 次
          </span>
          <span class="text-gray-500">成功 {{ health.success_count }} / 失败 {{ health.failure_count }}</span>
        </div>
        <p class="text-xs text-gray-500">最近成功导出：{{ formatTime(health.last_success_at) }}</p>
        <p v-if="health.last_error" class="text-xs text-red-600">
          最近一次导出错误：{{ health.last_error }}
        </p>
        <p v-if="health.success_count === 0 && health.failure_count === 0" class="text-xs text-gray-400">
          尚未观察到任何导出。
        </p>
      </div>
```

(g) 按钮区（`测试连接` 按钮之后）插入自检按钮与轮询提示：

```html
        <button
          v-if="form.enabled && canSave"
          type="button"
          class="px-4 py-2 border border-gray-300 text-sm rounded-md text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-50"
          :disabled="selfChecking"
          @click="sendSelfCheck"
        >
          {{ selfChecking ? '自检中...' : '发送测试 trace' }}
        </button>
```

并在其后的提示文案区追加：

```html
        <span v-if="pollState === 'waiting'" class="text-xs text-gray-400">
          已导出，正在等待 Langfuse 摄取…
        </span>
        <span v-else-if="pollState === 'found'" class="text-xs text-green-600">
          已到达 Langfuse，可打开控制台查看
        </span>
        <span v-else-if="pollState === 'timeout'" class="text-xs text-amber-600">
          已导出成功，但 Langfuse 尚未可查（摄取延迟），可稍后重新加载
        </span>
```

(h) 页面底部说明区追加一条：

```html
      <p>· 自检会向 Langfuse 发出一条名为 nanzi.selfcheck 的测试 trace，便于人工核对。</p>
```

- [ ] **Step 4: 跑测试与类型检查**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/frontend/ --confcutdir=tests/frontend -q`
Expected: PASS（含新契约测试）

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json 2>&1 | grep -cE "error TS"`
Expected: `55`（与改动前基线一致，不得新增）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/system/LangfuseConfig.vue tests/frontend/test_langfuse_selfcheck_contract.py
git commit -m "feat(ui): 链路追踪页新增自检按钮、摄取轮询与导出健康度"
```

---

## Task 8: 移除死配置 `trace_url_template`

**Files:**
- Modify: `app/services/ai/observability/config_store.py`
- Modify: `app/services/ai/observability/settings.py`
- Modify: `app/api/portal/endpoints/langfuse.py`
- Modify: `frontend/src/components/system/LangfuseConfig.vue`
- Modify: 受影响的既有测试

- [ ] **Step 1: 定位全部引用**

Run: `grep -rn "trace_url_template" app/ frontend/src/ tests/ db-prod/ db-prod-pg/ | grep -v node_modules`
Expected: 得到完整引用清单（`app/services/ai/observability/{config_store,settings,manager}.py`、`app/api/portal/endpoints/langfuse.py`、前端组件与若干测试）。**迁移 SQL 不在清理范围内**。

- [ ] **Step 2: 后端清理**

`config_store.py`：
- `DEFAULTS` 删除 `"trace_url_template": None,`
- `_STR_MAX_LENGTH` 删除 `"trace_url_template": 512,`
- `LANGFUSE_CONFIG_TABLE` 删除 `Column("trace_url_template", String(512)),`
- `_SELECT_COLUMNS` 与 `_SELECT_NAMES` 删除对应项

> 数据库列**保留**（历史迁移不可改，多一列无副作用）；SQLAlchemy 表定义不映射它即可，INSERT/SELECT 都不再涉及它。

`settings.py`：
- `LangfuseSnapshot` 删除 `trace_url_template: Optional[str] = None`
- `_build_snapshot` 删除 `trace_url_template=config.get("trace_url_template"),`

`app/api/portal/endpoints/langfuse.py`：
- `LangfuseConfigUpdateRequest` 删除 `trace_url_template: Any = None`

- [ ] **Step 3: 前端清理**

`LangfuseConfig.vue`：
- `LangfuseConfigView` 删除 `trace_url_template: string | null`
- `form` 删除 `trace_url_template: ''`
- `applyView` 删除 `trace_url_template: data.trace_url_template || '',`
- `buildPayload` 删除 `trace_url_template: form.value.trace_url_template.trim(),`
- 删除模板中「跳转链接模板」整个 `<div>`（含 label、input 与提示文案），并把其所在的 `grid-cols-1 md:grid-cols-2` 容器改为单列布局（保留其中的「发布版本」字段）

- [ ] **Step 4: 同步既有测试**

删除或改写所有引用该字段的断言：`tests/services/ai/observability/test_config_store*.py`、`tests/api/portal/test_langfuse_config_api.py`、`tests/frontend/test_langfuse_config_contract.py`、`tests/test_langfuse_migration_contract.py` 中与本字段相关的部分。

> 若某个测试断言的是「PUT 能保存自定义跳转模板」，直接删除该用例；若断言的是「GET 返回字段集合」，把该字段从期望集合里移除。

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/ tests/api/portal/test_langfuse_config_api.py tests/frontend/ --confcutdir=tests -q`
Expected: PASS

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json 2>&1 | grep -cE "error TS"`
Expected: `55`

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor(ai): 移除永不生效的 trace_url_template 死配置"
```

---

## Task 9: 真实 HTTP 实测与文档更新

**Files:**
- Create: `tests/services/ai/observability/test_selfcheck_real_http.py`
- Modify: `docs/superpowers/specs/2026-09-30-langfuse-llm-tracing-design.md`（跳转入口改为「已决定不做」）
- Modify: `docs/superpowers/specs/2026-09-30-langfuse-selfcheck-design.md`（状态改为「已实施」）
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 写真实 HTTP 测试**

创建 `tests/services/ai/observability/test_selfcheck_real_http.py`：

```python
"""真实 HTTP 实测：不 mock 网络，用一个假 Langfuse 服务验证导出判定。

延续一期的验证习惯：单元测试全 mock 网络，因此这里补一次真发请求的检查，
确认「成功 → ok」「服务端 5xx → 失败并记录原因」这两条判定真的成立。
"""

import http.server
import socketserver
import threading

import pytest

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
    yield f"http://127.0.0.1:{server.server_address[1]}", server
    server.shutdown()
    server.server_close()


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
    host, _server = fake_langfuse
    _Handler.status_code = 200
    export_health.reset_for_tests()

    span_exporter = exporter.build_span_exporter(_snapshot(host))
    span_exporter.export([])

    snap = export_health.snapshot()
    assert snap.success_count == 1
    assert snap.failure_count == 0


def test_real_export_server_error_is_recorded(fake_langfuse):
    host, _server = fake_langfuse
    _Handler.status_code = 500
    export_health.reset_for_tests()

    span_exporter = exporter.build_span_exporter(_snapshot(host))
    span_exporter.export([])

    snap = export_health.snapshot()
    assert snap.failure_count == 1
    assert snap.last_error
```

- [ ] **Step 2: 跑测试**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_selfcheck_real_http.py -q -p no:randomly`
Expected: PASS

若 500 用例未记为失败，检查 `OTLPSpanExporter` 是否会自行重试后抛出——必要时改用 `pytest.raises` 之外的显式断言记录实际行为，并以实际行为为准修正 `ObservableSpanExporter`。

- [ ] **Step 3: 文档更新**

- `docs/superpowers/specs/2026-09-30-langfuse-llm-tracing-design.md`：把「对话页跳转入口」相关的二期待办条目改为「**已决定不做**——Langfuse 仅管理员可登录，跳转对平台用户无意义」，并说明 `trace_url_template` 已移除。
- `docs/superpowers/specs/2026-09-30-langfuse-selfcheck-design.md`：状态改为「已实施」，并在末尾补一节「实施记录」，简述实际改动、测试数量与实测结论。
- `tests/CHECKLIST.md`：新增本次交付条目（功能点、测试数量、验证方式）。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "test(ai): 补真实 HTTP 实测并同步文档与交付清单"
```

---

## 完成标准

- [ ] `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/ tests/api/portal/ tests/frontend/ --confcutdir=tests -q` 全绿
- [ ] `vue-tsc --noEmit` 错误数不超过 55（基线）
- [ ] `grep -rn "trace_url_template" app/ frontend/src/` 无结果（迁移 SQL 除外）
- [ ] 在真实 Langfuse 实例上：点自检得到「导出成功」，随后层 2 轮询能变为「已到达」
- [ ] 跑一轮真实对话后，状态卡片的「最近成功导出」时间更新
