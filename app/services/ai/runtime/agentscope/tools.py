from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

from app.services.ai.runtime.agentscope.errors import RuntimeToolError, RuntimeTimeoutError


ToolSourceType = Literal["static", "generic_api", "mcp", "class", "system"]
RuntimeToolAuditStatus = Literal["start", "success", "error"]


@dataclass(frozen=True)
class RuntimeToolAuditEvent:
    tool_name: str
    status: RuntimeToolAuditStatus
    source_type: ToolSourceType
    permission_scope: str
    arguments: dict[str, Any]
    elapsed_ms: float | None = None
    result_preview: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class RuntimeToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    source_type: ToolSourceType
    callable: Callable[..., Any]
    permission_scope: str = "write"
    timeout_seconds: float | None = None
    audit_callback: Callable[[RuntimeToolAuditEvent], Any] | None = None

    @property
    def is_read_only(self) -> bool:
        return self.permission_scope == "read"

    async def invoke(self, arguments: dict[str, Any] | None = None) -> Any:
        arguments = arguments or {}
        start = time.perf_counter()
        await self._emit_audit(
            RuntimeToolAuditEvent(
                tool_name=self.name,
                status="start",
                source_type=self.source_type,
                permission_scope=self.permission_scope,
                arguments=arguments,
            )
        )
        try:
            result = self.callable(**arguments)
            if inspect.isawaitable(result):
                if self.timeout_seconds:
                    result = await asyncio.wait_for(result, timeout=self.timeout_seconds)
                else:
                    result = await result
            await self._emit_audit(
                RuntimeToolAuditEvent(
                    tool_name=self.name,
                    status="success",
                    source_type=self.source_type,
                    permission_scope=self.permission_scope,
                    arguments=arguments,
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                    result_preview=_preview_result(result),
                )
            )
            return result
        except TimeoutError as exc:
            wrapped = RuntimeTimeoutError(
                f"Tool '{self.name}' timed out",
                cause=exc,
                details={"tool_name": self.name, "timeout_seconds": self.timeout_seconds},
            )
            await self._emit_error_audit(arguments, start, wrapped)
            raise wrapped from exc
        except Exception as exc:
            wrapped = RuntimeToolError(
                f"Tool '{self.name}' failed: {exc}",
                cause=exc,
                details={"tool_name": self.name},
            )
            await self._emit_error_audit(arguments, start, wrapped)
            raise wrapped from exc

    async def _emit_error_audit(
        self,
        arguments: dict[str, Any],
        start: float,
        exc: Exception,
    ) -> None:
        await self._emit_audit(
            RuntimeToolAuditEvent(
                tool_name=self.name,
                status="error",
                source_type=self.source_type,
                permission_scope=self.permission_scope,
                arguments=arguments,
                elapsed_ms=(time.perf_counter() - start) * 1000,
                error=str(exc),
            )
        )

    async def _emit_audit(self, event: RuntimeToolAuditEvent) -> None:
        if not self.audit_callback:
            return
        result = self.audit_callback(event)
        if inspect.isawaitable(result):
            await result


class AgentScopeRuntimeTool:
    is_concurrency_safe = False
    is_external_tool = False
    is_state_injected = False
    is_mcp = False
    mcp_name = None

    def __init__(self, spec: RuntimeToolSpec) -> None:
        self.spec = spec
        self.name = spec.name
        self.description = spec.description
        self.input_schema = spec.parameters_schema
        self.is_read_only = spec.is_read_only

    async def check_permissions(self, tool_input: dict[str, Any], context: Any) -> Any:
        try:
            from agentscope.permission import PermissionBehavior, PermissionDecision
        except Exception:
            return None
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="runtime permission checks are handled by platform middleware",
        )

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        return self.is_read_only

    def match_rule(self, rule_content: str | None, tool_input: dict[str, Any]) -> bool:
        return rule_content is None

    async def __call__(self, **kwargs: Any) -> Any:
        from agentscope.message import TextBlock, ToolResultState
        from agentscope.tool import ToolChunk

        return ToolChunk(
            content=[TextBlock(text=str(await self.spec.invoke(kwargs)))],
            state=ToolResultState.SUCCESS,
        )


def _load_agentscope_toolkit():
    from agentscope.tool import Toolkit

    return Toolkit


def _preview_result(result: Any, max_length: int = 500) -> str:
    text = str(result)
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def build_toolkit(tool_specs: list[RuntimeToolSpec]):
    toolkit_cls = _load_agentscope_toolkit()
    return toolkit_cls(tools=[AgentScopeRuntimeTool(spec) for spec in tool_specs])


def _schema_from_legacy_tool(tool: Any) -> dict[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        return args_schema.model_json_schema()
    input_schema = getattr(tool, "input_schema", None)
    if isinstance(input_schema, dict):
        return input_schema
    return {"type": "object", "properties": {}}


def runtime_tool_spec_from_legacy_tool(
    tool: Any,
    source_type: ToolSourceType,
    permission_scope: str = "write",
) -> RuntimeToolSpec:
    async def _invoke(**kwargs: Any) -> Any:
        if hasattr(tool, "ainvoke"):
            return await tool.ainvoke(kwargs)
        if hasattr(tool, "arun"):
            return await tool.arun(**kwargs)
        if callable(tool):
            result = tool(**kwargs)
            if inspect.isawaitable(result):
                return await result
            return result
        raise TypeError(f"Tool {getattr(tool, 'name', repr(tool))} is not callable")

    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    if not name:
        raise ValueError("Legacy tool is missing a name")

    return RuntimeToolSpec(
        name=name,
        description=getattr(tool, "description", None) or getattr(tool, "__doc__", "") or "",
        parameters_schema=_schema_from_legacy_tool(tool),
        source_type=source_type,
        callable=_invoke,
        permission_scope=permission_scope,
    )
