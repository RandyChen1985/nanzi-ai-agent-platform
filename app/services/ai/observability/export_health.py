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
