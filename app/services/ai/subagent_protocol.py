"""Typed result protocol for sub-agent delegation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SubAgentResultStatus(str, Enum):
    """Lifecycle status exposed by a delegated execution."""

    COMPLETED = "completed"
    EMPTY = "empty"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"
    DEPTH_EXCEEDED = "depth_exceeded"
    INVALID_OUTPUT = "invalid_output"
    # 子智能体已在委派中向用户发起提问并停下等回答。它不是「没产出」，
    # 也不是失败：父这一轮必须停止，等用户回答后再 resume 续聊。
    NEEDS_USER_INPUT = "needs_user_input"


class SubAgentStopReason(str, Enum):
    """Terminal reason for one delegated execution."""

    COMPLETED = "completed"
    EMPTY = "empty"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"
    DEPTH_EXCEEDED = "depth_exceeded"
    INVALID_OUTPUT = "invalid_output"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    NEEDS_USER_INPUT = "needs_user_input"


_STOP_REASON_BY_STATUS = {
    SubAgentResultStatus.COMPLETED: SubAgentStopReason.COMPLETED,
    SubAgentResultStatus.EMPTY: SubAgentStopReason.EMPTY,
    SubAgentResultStatus.TIMEOUT: SubAgentStopReason.TIMEOUT,
    SubAgentResultStatus.CANCELLED: SubAgentStopReason.CANCELLED,
    SubAgentResultStatus.PERMISSION_DENIED: SubAgentStopReason.PERMISSION_DENIED,
    SubAgentResultStatus.DEPTH_EXCEEDED: SubAgentStopReason.DEPTH_EXCEEDED,
    SubAgentResultStatus.INVALID_OUTPUT: SubAgentStopReason.INVALID_OUTPUT,
    SubAgentResultStatus.FAILED: SubAgentStopReason.FAILED,
    SubAgentResultStatus.INTERRUPTED: SubAgentStopReason.INTERRUPTED,
    SubAgentResultStatus.NEEDS_USER_INPUT: SubAgentStopReason.NEEDS_USER_INPUT,
}


@dataclass(frozen=True)
class SubAgentRequest:
    """Normalized request metadata used by the delegation boundary."""

    target_agent_name: str
    query: str
    caller_agent_id: str | None = None
    caller_agent_name: str | None = None
    delegation_depth: int = 0
    approval_mode: str = "ask"
    capability: str | None = None
    authority: str = "inherited_permission"
    timeout_seconds: float | None = None
    background: bool = False
    run_id: str | None = None
    parent_trace_id: str | None = None
    parent_conversation_id: str | None = None
    child_session_id: str | None = None
    max_depth: int | None = None
    tool_filter: list[str] | None = None
    output_schema: dict[str, Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Return safe request metadata without user credentials or raw context."""
        return {
            "target_agent_name": self.target_agent_name,
            "query_chars": len(self.query),
            "caller_agent_id": self.caller_agent_id,
            "caller_agent_name": self.caller_agent_name,
            "delegation_depth": self.delegation_depth,
            "approval_mode": self.approval_mode,
            "capability": self.capability,
            "authority": self.authority,
            "timeout_seconds": self.timeout_seconds,
            "background": self.background,
            "run_id": self.run_id,
            "parent_trace_id": self.parent_trace_id,
            "parent_conversation_id": self.parent_conversation_id,
            "child_session_id": self.child_session_id,
            "max_depth": self.max_depth,
            "tool_filter": (
                list(self.tool_filter)
                if self.tool_filter is not None
                else None
            ),
            "output_schema": _schema_metadata(self.output_schema),
        }


EMPTY_SUB_AGENT_RESULT_MESSAGE = (
    "子智能体已执行完成，但未产生可交付正文（可能仅有内部进度日志或工具中间结果）。"
    "请勿使用相同参数重复委派；请根据上述情况向用户说明，或建议其直接打开对应子智能体对话。"
)

NEEDS_USER_INPUT_SUB_AGENT_RESULT_MESSAGE = (
    "子智能体已向用户发起提问，正在等待用户回答。"
    "本轮请勿重复提问，也不要替用户作答；用户回答后，用 sub_agent_call(resume=True) "
    "让同一子智能体接着上次的进度继续。"
)


@dataclass(frozen=True)
class SubAgentResult:
    """JSON-safe internal result that keeps delegation state separate from text."""

    status: SubAgentResultStatus
    target_agent_id: str | None = None
    target_agent_name: str | None = None
    content: str = ""
    error_code: str | None = None
    interrupt_type: str | None = None
    truncated: bool = False
    capability: str | None = None
    evidence: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    run_id: str | None = None
    parent_trace_id: str | None = None
    parent_conversation_id: str | None = None
    child_trace_id: str | None = None
    child_session_id: str | None = None
    stop_reason: SubAgentStopReason | str | None = None
    structured: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.stop_reason is None:
            object.__setattr__(self, "stop_reason", _STOP_REASON_BY_STATUS[self.status])
        elif not isinstance(self.stop_reason, SubAgentStopReason):
            object.__setattr__(self, "stop_reason", SubAgentStopReason(str(self.stop_reason)))

    def to_tool_text(self) -> str:
        """Return the backward-compatible text presented to the calling model."""
        if self.content.strip():
            return self.content
        if self.structured is not None:
            return json.dumps(self.structured, ensure_ascii=False)
        if self.status is SubAgentResultStatus.NEEDS_USER_INPUT:
            # 兜底路径也点名子代理：父需要据此决定 resume 谁。
            if self.target_agent_name:
                return (
                    f"{NEEDS_USER_INPUT_SUB_AGENT_RESULT_MESSAGE}"
                    f"\n发起提问的子智能体：{self.target_agent_name}。"
                )
            return NEEDS_USER_INPUT_SUB_AGENT_RESULT_MESSAGE
        if self.status is SubAgentResultStatus.EMPTY:
            return EMPTY_SUB_AGENT_RESULT_MESSAGE
        return "错误：子智能体未产生可交付结果。"

    def to_metadata(self) -> dict[str, Any]:
        """Return non-sensitive metadata suitable for a trace or SSE meta event."""
        return {
            "status": self.status.value,
            "target_agent_id": self.target_agent_id,
            "target_agent_name": self.target_agent_name,
            "error_code": self.error_code,
            "interrupt_type": self.interrupt_type,
            "truncated": self.truncated,
            "capability": self.capability,
            "evidence_count": len(self.evidence),
            "artifact_count": len(self.artifacts),
            "content_chars": len(self.content),
            "run_id": self.run_id,
            "parent_trace_id": self.parent_trace_id,
            "parent_conversation_id": self.parent_conversation_id,
            "child_trace_id": self.child_trace_id,
            "child_session_id": self.child_session_id,
            "stop_reason": (
                self.stop_reason.value
                if isinstance(self.stop_reason, SubAgentStopReason)
                else self.stop_reason
            ),
            "structured": self.structured is not None,
        }


def _schema_metadata(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not schema:
        return None
    return {
        "type": schema.get("type"),
        "property_count": (
            len(schema.get("properties") or {})
            if isinstance(schema.get("properties"), dict)
            else 0
        ),
    }


_JSON_SCHEMA_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}


def _validate_schema_definition(schema: Any, *, root: bool = False) -> str | None:
    if not isinstance(schema, dict):
        return "schema must be an object"
    schema_type = schema.get("type")
    if schema_type not in _JSON_SCHEMA_TYPES:
        return f"unsupported schema type '{schema_type}'"
    if root and schema_type != "object":
        return "schema root must be an object"
    properties = schema.get("properties")
    if schema_type == "object" and properties is not None:
        if not isinstance(properties, dict):
            return "object properties must be an object"
        for child in properties.values():
            error = _validate_schema_definition(child)
            if error:
                return error
    items = schema.get("items")
    if schema_type == "array" and items is not None:
        error = _validate_schema_definition(items)
        if error:
            return error
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        return "required must be a list of strings"
    return None


def _validate_json_value(
    value: Any,
    schema: dict[str, Any],
    path: str = "value",
) -> str | None:
    schema_type = schema["type"]
    if schema_type == "null" and value is not None:
        return f"{path} must be null"
    if schema_type == "boolean" and not isinstance(value, bool):
        return f"{path} must be a boolean"
    if schema_type == "string" and not isinstance(value, str):
        return f"{path} must be a string"
    if schema_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return f"{path} must be an integer"
    if schema_type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return f"{path} must be a number"
    if schema_type == "array":
        if not isinstance(value, list):
            return f"{path} must be an array"
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                error = _validate_json_value(item, item_schema, f"{path}[{index}]")
                if error:
                    return error
    if schema_type == "object":
        if not isinstance(value, dict):
            return f"{path} must be an object"
        properties = schema.get("properties") or {}
        for required in schema.get("required", []):
            if required not in value:
                return f"missing required property '{required}'"
        if schema.get("additionalProperties") is False:
            unknown = next((key for key in value if key not in properties), None)
            if unknown is not None:
                return f"unexpected property '{unknown}'"
        for key, child_schema in properties.items():
            if key in value:
                error = _validate_json_value(value[key], child_schema, f"property '{key}'")
                if error:
                    return error
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        return f"{path} is not one of the allowed values"
    return None


def validate_structured_output(
    value: Any,
    schema: dict[str, Any],
) -> tuple[bool, str | None]:
    """Validate the supported JSON object subset for a delegated result."""
    definition_error = validate_structured_schema(schema)
    if definition_error:
        return False, definition_error
    value_error = _validate_json_value(value, schema)
    return value_error is None, value_error


def validate_structured_schema(schema: Any) -> str | None:
    """Validate a delegated output schema before starting child execution."""
    return _validate_schema_definition(schema, root=True)


__all__ = [
    "EMPTY_SUB_AGENT_RESULT_MESSAGE",
    "NEEDS_USER_INPUT_SUB_AGENT_RESULT_MESSAGE",
    "SubAgentRequest",
    "SubAgentResult",
    "SubAgentResultStatus",
    "SubAgentStopReason",
    "validate_structured_schema",
    "validate_structured_output",
]
