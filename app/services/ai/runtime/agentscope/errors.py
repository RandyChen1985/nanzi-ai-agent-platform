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
