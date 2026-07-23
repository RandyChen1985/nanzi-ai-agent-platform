from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal


RuntimeErrorKind = Literal[
    "model",
    "tool",
    "permission",
    "timeout",
    "cancelled",
    "configuration",
    "unknown",
]


class AgentScopeRuntimeError(Exception):
    """Base error for the platform runtime adapter."""

    kind: RuntimeErrorKind = "unknown"

    def __init__(
        self,
        message: str,
        *,
        cause: BaseException | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.details = details or {}


class RuntimeModelError(AgentScopeRuntimeError):
    kind: RuntimeErrorKind = "model"


class RuntimeToolError(AgentScopeRuntimeError):
    kind: RuntimeErrorKind = "tool"


class ToolLoopFuseError(RuntimeToolError):
    """Raised when repeated identical tool calls exceed the fuse threshold."""


_TOOL_LOOP_FUSE_MARKERS = (
    "停止继续调用任何工具",
    "系统判断继续执行大概率只会消耗步数",
    "系统判断已陷入拉锯循环并中止",
    "系统中止以避免无意义空转",
    "工具调用总数已达",
)


def extract_tool_loop_fuse_message(exc: BaseException | None) -> str | None:
    """从异常链 / ExceptionGroup 中提取工具循环熔断信息（若有）。"""
    if exc is None:
        return None
    if isinstance(exc, ToolLoopFuseError):
        return str(exc)
    message = str(exc or "").strip()
    if message and any(marker in message for marker in _TOOL_LOOP_FUSE_MARKERS):
        return message
    exceptions = getattr(exc, "exceptions", None)
    if exceptions:
        for nested in exceptions:
            found = extract_tool_loop_fuse_message(nested)
            if found:
                return found
    for attr in ("__cause__", "__context__"):
        nested = getattr(exc, attr, None)
        if isinstance(nested, BaseException):
            found = extract_tool_loop_fuse_message(nested)
            if found:
                return found
    return None


class RuntimePermissionError(AgentScopeRuntimeError):
    kind: RuntimeErrorKind = "permission"


class RuntimeTimeoutError(AgentScopeRuntimeError):
    kind: RuntimeErrorKind = "timeout"


class RuntimeCancelledError(AgentScopeRuntimeError):
    kind: RuntimeErrorKind = "cancelled"


class RuntimeConfigurationError(AgentScopeRuntimeError):
    kind: RuntimeErrorKind = "configuration"


@dataclass(frozen=True)
class RuntimeErrorEnvelope:
    kind: RuntimeErrorKind
    message: str
    details: dict[str, Any]


def normalize_runtime_error(exc: BaseException) -> AgentScopeRuntimeError:
    if isinstance(exc, AgentScopeRuntimeError):
        return exc
    if isinstance(exc, asyncio.CancelledError):
        return RuntimeCancelledError("Runtime operation was cancelled", cause=exc)
    if isinstance(exc, TimeoutError):
        return RuntimeTimeoutError("Runtime operation timed out", cause=exc)
    return AgentScopeRuntimeError(str(exc) or exc.__class__.__name__, cause=exc)


def error_to_envelope(exc: BaseException) -> RuntimeErrorEnvelope:
    runtime_error = normalize_runtime_error(exc)
    return RuntimeErrorEnvelope(
        kind=runtime_error.kind,
        message=str(runtime_error),
        details=runtime_error.details,
    )
