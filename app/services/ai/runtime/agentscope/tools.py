from __future__ import annotations

import asyncio
import inspect
import logging
import os
import shlex
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

from app.services.ai.grounding.models import EvidenceType
from app.services.ai.runtime.agentscope.errors import RuntimeToolError, RuntimeTimeoutError, ToolLoopFuseError
from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector


logger = logging.getLogger(__name__)


ToolSourceType = Literal["static", "generic_api", "mcp", "class", "system"]
RuntimePermissionScope = Literal["read", "write", "ask", "dangerous"]
RuntimeApprovalMode = Literal["ask", "allow", "deny"]

_TOOL_LOOP_MODEL_GUIDANCE = (
    "请停止继续调用任何工具，基于已经获得的结果直接回答用户；"
    "如果现有信息不足，请明确说明限制，不要再次尝试工具调用。"
)


def _tool_loop_fuse_message(reason: str) -> str:
    return f"{reason} {_TOOL_LOOP_MODEL_GUIDANCE}".strip()


RuntimeToolAuditStatus = Literal["start", "success", "error"]
RuntimeEvidencePolicy = Literal["non_empty", "structured_success", "allow_empty_success"]


READ_ONLY_TOOL_NAMES = {
    "get_current_time",
    "resolve_relative_dates",
    "get_dataset_schema",
    "search_knowledge_base",
    "memory_search",
    "fetch_user_long_term_memory",
    "get_my_tasks",
    "jira_search",
    "jira_get_projects",
    "read_file",
    "search_text",
    "list_process",
    "list_available_skills",
    "read_skill_instruction",
    "directory_tree_navigator",
    "web_renderer_and_snapshot",
    "code_syntax_linter",
    "fetch_static_web_url",
    "web_search_baidu",
    "system_http_request",
    "sub_agent_call",
}
NATIVE_TOOL_EVIDENCE_TYPES = {
    "Bash": frozenset({EvidenceType.RUNTIME_STATE}),
    "Read": frozenset({EvidenceType.USER_FILE}),
    "Grep": frozenset({EvidenceType.USER_FILE}),
    "Glob": frozenset({EvidenceType.USER_FILE}),
}
NATIVE_TOOL_EVIDENCE_POLICIES = {
    "Read": "allow_empty_success",
    "Grep": "allow_empty_success",
    "Glob": "allow_empty_success",
}


def _record_evidence_result(
    *,
    tool_name: str,
    evidence_types: frozenset[EvidenceType],
    evidence_policy: str,
    result: Any,
) -> None:
    if not evidence_types:
        return
    from app.core.context import get_current_agent_context

    context = get_current_agent_context()
    ledger = getattr(context, "grounding_evidence_ledger", None)
    if ledger is not None:
        ledger.record_success(
            call_id=f"{tool_name}:{time.time_ns()}",
            producer=tool_name,
            evidence_types=evidence_types,
            result=result,
            policy=evidence_policy,
        )


@dataclass(frozen=True)
class RuntimeToolAuditEvent:
    tool_name: str
    status: RuntimeToolAuditStatus
    source_type: ToolSourceType
    permission_scope: RuntimePermissionScope
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
    permission_scope: RuntimePermissionScope = "ask"
    evidence_types: frozenset[EvidenceType] = frozenset()
    evidence_policy: RuntimeEvidencePolicy = "non_empty"
    evidence_inference_disabled: bool = False
    timeout_seconds: float | None = None
    audit_callback: Callable[[RuntimeToolAuditEvent], Any] | None = None
    native_tool: Any | None = None

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
            _record_evidence_result(
                tool_name=self.name,
                evidence_types=self.evidence_types,
                evidence_policy=self.evidence_policy,
                result=result,
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

    def __init__(
        self,
        spec: RuntimeToolSpec,
        approval_mode: RuntimeApprovalMode | str | None = None,
        loop_detector: ToolLoopDetector | None = None,
        user_id: int | str | None = None,
    ) -> None:
        self.spec = spec
        self.name = spec.name
        self.description = spec.description
        self.input_schema = spec.parameters_schema
        self.is_read_only = spec.is_read_only
        self.approval_mode = _normalize_runtime_approval_mode(approval_mode)
        self.loop_detector = loop_detector
        self.user_id = user_id

    def _check_tool_loop(self, tool_input: dict[str, Any]) -> None:
        if not self.loop_detector:
            return
        verdict = self.loop_detector.record(self.name, tool_input)
        if verdict.fused:
            raise ToolLoopFuseError(_tool_loop_fuse_message(verdict.message))

    async def check_permissions(self, tool_input: dict[str, Any], context: Any) -> Any:
        try:
            from agentscope.permission import PermissionBehavior, PermissionDecision
        except Exception:
            return None

        forbidden_decision = await _enforce_tool_forbidden(self.name, getattr(self, "user_id", None))
        if forbidden_decision:
            return forbidden_decision

        blacklist_decision = await _enforce_command_blacklist(self.name, tool_input, getattr(self, "user_id", None))
        if blacklist_decision:
            return blacklist_decision

        if self.spec.permission_scope == "read":
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Tool '{self.name}' is read-only and can run automatically.",
            )
        if self.spec.permission_scope == "dangerous":
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Tool '{self.name}' is marked dangerous and cannot run automatically.",
                decision_reason="dangerous runtime tool scope",
                bypass_immune=True,
            )
        if self.approval_mode == "allow":
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Tool '{self.name}' is allowed by runtime approval mode.",
                decision_reason=f"runtime approval mode: {self.approval_mode}",
            )
        if self.approval_mode == "deny":
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Tool '{self.name}' is denied by runtime approval mode.",
                decision_reason=f"runtime approval mode: {self.approval_mode}",
                bypass_immune=True,
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Tool '{self.name}' requires user confirmation before execution.",
            decision_reason=f"runtime tool scope: {self.spec.permission_scope}",
        )

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        return self.is_read_only

    def match_rule(self, rule_content: str | None, tool_input: dict[str, Any]) -> bool:
        return rule_content is None

    def generate_suggestions(self, tool_input: dict[str, Any]) -> list[Any]:
        return []

    async def __call__(self, **kwargs: Any) -> Any:
        from agentscope.message import TextBlock, ToolResultState
        from agentscope.tool import ToolChunk

        self._check_tool_loop(kwargs)
        return ToolChunk(
            content=[TextBlock(text=str(await self.spec.invoke(kwargs)))],
            state=ToolResultState.SUCCESS,
        )


class AgentScopeNativeApprovalTool:
    """Apply runtime approval mode to AgentScope native tools such as Bash."""

    def __init__(
        self,
        native_tool: Any,
        *,
        approval_mode: RuntimeApprovalMode | str | None = None,
        permission_scope: RuntimePermissionScope | None = None,
        loop_detector: ToolLoopDetector | None = None,
        user_id: int | str | None = None,
        evidence_types: frozenset[EvidenceType] = frozenset(),
        evidence_policy: str = "non_empty",
    ) -> None:
        self.native_tool = native_tool
        self.name = getattr(native_tool, "name", "")
        self.description = getattr(native_tool, "description", "")
        self.input_schema = getattr(native_tool, "input_schema", {"type": "object", "properties": {}})
        self.is_read_only = bool(getattr(native_tool, "is_read_only", False))
        self.approval_mode = _normalize_runtime_approval_mode(approval_mode)
        self.permission_scope = permission_scope or _infer_native_permission_scope(native_tool)
        self.loop_detector = loop_detector
        self.user_id = user_id
        self.evidence_types = evidence_types
        self.evidence_policy = evidence_policy

    def _check_tool_loop(self, tool_input: dict[str, Any]) -> None:
        if not self.loop_detector:
            return
        verdict = self.loop_detector.record(self.name, tool_input)
        if verdict.fused:
            raise ToolLoopFuseError(_tool_loop_fuse_message(verdict.message))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.native_tool, name)

    async def check_permissions(self, tool_input: dict[str, Any], context: Any) -> Any:
        try:
            from agentscope.permission import PermissionBehavior, PermissionDecision
        except Exception:
            return None

        forbidden_decision = await _enforce_tool_forbidden(self.name, self.user_id)
        if forbidden_decision:
            return forbidden_decision

        blacklist_decision = await _enforce_command_blacklist(
            self.name,
            tool_input,
            self.user_id,
        )
        if blacklist_decision:
            return blacklist_decision

        if self.permission_scope == "read":
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Tool '{self.name}' is read-only and can run automatically.",
            )
        if self.permission_scope == "dangerous":
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Tool '{self.name}' is marked dangerous and cannot run automatically.",
                decision_reason="dangerous runtime tool scope",
                bypass_immune=True,
            )
        if self.approval_mode == "allow":
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Tool '{self.name}' is allowed by runtime approval mode.",
                decision_reason=f"runtime approval mode: {self.approval_mode}",
            )
        if self.approval_mode == "deny":
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Tool '{self.name}' is denied by runtime approval mode.",
                decision_reason=f"runtime approval mode: {self.approval_mode}",
                bypass_immune=True,
            )
        native_check = getattr(self.native_tool, "check_permissions", None)
        if native_check:
            result = native_check(tool_input, context)
            if inspect.isawaitable(result):
                return await result
            return result
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Tool '{self.name}' requires user confirmation before execution.",
            decision_reason=f"runtime tool scope: {self.permission_scope}",
        )

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        native_check = getattr(self.native_tool, "check_read_only", None)
        if native_check:
            result = native_check(tool_input)
            if inspect.isawaitable(result):
                return bool(await result)
            return bool(result)
        return self.permission_scope == "read"

    def match_rule(self, rule_content: str | None, tool_input: dict[str, Any]) -> bool:
        native_match = getattr(self.native_tool, "match_rule", None)
        if native_match:
            return bool(native_match(rule_content, tool_input))
        return rule_content is None

    def generate_suggestions(self, tool_input: dict[str, Any]) -> list[Any]:
        native_generate = getattr(self.native_tool, "generate_suggestions", None)
        if native_generate:
            return native_generate(tool_input)
        return []

    async def __call__(self, **kwargs: Any) -> Any:
        self._check_tool_loop(kwargs)
        result = self.native_tool(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        _record_evidence_result(
            tool_name=self.name,
            evidence_types=self.evidence_types,
            evidence_policy=self.evidence_policy,
            result=result,
        )
        return result


def _load_agentscope_toolkit():
    from agentscope.tool import Toolkit

    return Toolkit


def _preview_result(result: Any, max_length: int = 500) -> str:
    text = str(result)
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _normalize_runtime_approval_mode(
    approval_mode: RuntimeApprovalMode | str | None,
) -> RuntimeApprovalMode:
    if approval_mode in {"allow", "deny", "ask"}:
        return approval_mode
    return "ask"


def _infer_native_permission_scope(native_tool: Any) -> RuntimePermissionScope:
    if bool(getattr(native_tool, "is_read_only", False)):
        return "read"
    name = str(getattr(native_tool, "name", "") or "")
    if name in {"Read", "Glob", "Grep"}:
        return "read"
    return "ask"


def runtime_tool_from_spec(
    spec: RuntimeToolSpec,
    *,
    approval_mode: RuntimeApprovalMode | str | None = None,
    loop_detector: ToolLoopDetector | None = None,
    user_id: int | str | None = None,
) -> Any:
    if spec.native_tool is not None:
        return AgentScopeNativeApprovalTool(
            spec.native_tool,
            approval_mode=approval_mode,
            permission_scope=spec.permission_scope,
            loop_detector=loop_detector,
            user_id=user_id,
            evidence_types=spec.evidence_types,
            evidence_policy=spec.evidence_policy,
        )
    return AgentScopeRuntimeTool(
        spec,
        approval_mode=approval_mode,
        loop_detector=loop_detector,
        user_id=user_id,
    )


def runtime_tool_from_native(
    native_tool: Any,
    *,
    approval_mode: RuntimeApprovalMode | str | None = None,
    user_id: int | str | None = None,
) -> Any:
    native_name = str(getattr(native_tool, "name", "") or "")
    evidence_types = NATIVE_TOOL_EVIDENCE_TYPES.get(
        native_name,
        frozenset(),
    )
    return AgentScopeNativeApprovalTool(
        native_tool,
        approval_mode=approval_mode,
        user_id=user_id,
        evidence_types=evidence_types,
        evidence_policy=NATIVE_TOOL_EVIDENCE_POLICIES.get(native_name, "non_empty"),
    )


def build_toolkit(
    tool_specs: list[RuntimeToolSpec],
    *,
    approval_mode: RuntimeApprovalMode | str | None = None,
    loop_detector: ToolLoopDetector | None = None,
    user_id: int | str | None = None,
):
    toolkit_cls = _load_agentscope_toolkit()
    tools = [
        runtime_tool_from_spec(spec, approval_mode=approval_mode, loop_detector=loop_detector, user_id=user_id)
        for spec in tool_specs
    ]
    return toolkit_cls(tools=tools)


def _schema_from_legacy_tool(tool: Any) -> dict[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        return args_schema.model_json_schema()
    input_schema = getattr(tool, "input_schema", None)
    if isinstance(input_schema, dict):
        return input_schema
    return {"type": "object", "properties": {}}


def _normalize_evidence_types(values: Any, *, tool_name: str) -> frozenset[EvidenceType]:
    normalized: set[EvidenceType] = set()
    for value in values or ():
        try:
            normalized.add(EvidenceType(value))
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring invalid evidence type %r declared by tool %s",
                value,
                tool_name,
            )
    return frozenset(normalized)


def runtime_tool_spec_from_legacy_tool(
    tool: Any,
    source_type: ToolSourceType,
    permission_scope: RuntimePermissionScope | None = None,
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
    resolved_scope = permission_scope or infer_runtime_permission_scope(name, source_type)
    evidence_types = _normalize_evidence_types(
        getattr(tool, "evidence_types", None),
        tool_name=name,
    )
    evidence_policy = getattr(tool, "evidence_policy", "non_empty")

    return RuntimeToolSpec(
        name=name,
        description=getattr(tool, "description", None) or getattr(tool, "__doc__", "") or "",
        parameters_schema=_schema_from_legacy_tool(tool),
        source_type=source_type,
        callable=_invoke,
        permission_scope=resolved_scope,
        evidence_types=evidence_types,
        evidence_policy=evidence_policy,
        evidence_inference_disabled=(
            getattr(tool, "evidence_inference_disabled", False) is True
        ),
    )


def runtime_tool_spec_from_native_agentscope_tool(
    tool: Any,
    *,
    source_type: ToolSourceType = "system",
    permission_scope: RuntimePermissionScope | None = None,
) -> RuntimeToolSpec:
    async def _invoke(**kwargs: Any) -> Any:
        result = tool(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        if inspect.isasyncgen(result):
            parts = []
            async for chunk in result:
                parts.append(_tool_chunk_to_text(chunk))
            return "".join(parts)
        return _tool_chunk_to_text(result)

    resolved_scope = permission_scope or ("read" if getattr(tool, "is_read_only", False) else "ask")
    evidence_types = _normalize_evidence_types(
        getattr(tool, "evidence_types", None),
        tool_name=str(getattr(tool, "name", "<unnamed>")),
    )
    return RuntimeToolSpec(
        name=getattr(tool, "name"),
        description=getattr(tool, "description", ""),
        parameters_schema=getattr(tool, "input_schema", {"type": "object", "properties": {}}),
        source_type=source_type,
        callable=_invoke,
        permission_scope=resolved_scope,
        evidence_types=evidence_types,
        evidence_policy=getattr(tool, "evidence_policy", "non_empty"),
        evidence_inference_disabled=(
            getattr(tool, "evidence_inference_disabled", False) is True
        ),
        native_tool=tool,
    )


def _tool_chunk_to_text(result: Any) -> str:
    content = getattr(result, "content", None)
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(str(text))
        if parts:
            return "".join(parts)
    return str(result)


def infer_runtime_permission_scope(
    tool_name: str,
    source_type: ToolSourceType,
) -> RuntimePermissionScope:
    if tool_name in READ_ONLY_TOOL_NAMES:
        return "read"
    if source_type in {"generic_api", "mcp"}:
        return "ask"
    return "ask"


async def _enforce_tool_forbidden(tool_name: str, explicit_user_id: int | str | None = None) -> Any:
    from app.core.context import get_current_agent_context
    agent_ctx = get_current_agent_context()
    user_id = explicit_user_id or (agent_ctx.user_id if agent_ctx else None)

    if explicit_user_id is None and agent_ctx and agent_ctx.is_admin:
        return None

    if user_id:
        try:
            from app.core.orm import AsyncSessionLocal
            from app.services.permission_service import PermissionService
            from app.services.ai.tools.registry import AGENTSCOPE_BUILTIN_TOOL_ALIASES
            from agentscope.permission import PermissionBehavior, PermissionDecision

            async with AsyncSessionLocal() as session:
                perm_service = PermissionService(session)
                perms = await perm_service.get_user_permissions(int(user_id))
                if "admin" in perms.roles:
                    return None

                forbidden = set(perms.permissions.forbidden_tools or [])
                if forbidden:
                    extended_forbidden = set()
                    for f in forbidden:
                        extended_forbidden.add(f)
                        if f in AGENTSCOPE_BUILTIN_TOOL_ALIASES:
                            extended_forbidden.add(AGENTSCOPE_BUILTIN_TOOL_ALIASES[f])
                        for k, v in AGENTSCOPE_BUILTIN_TOOL_ALIASES.items():
                            if v == f:
                                extended_forbidden.add(k)

                    if tool_name in extended_forbidden:
                        return PermissionDecision(
                            behavior=PermissionBehavior.DENY,
                            message=f"安全策略拦截：您的账号已被禁止使用 '{tool_name}' 工具。",
                            decision_reason="hit_user_forbidden_tool",
                            bypass_immune=True,
                        )
        except Exception as err:
            logger.exception("Failed to enforce forbidden tools for user %s", user_id)
            return _permission_policy_unavailable_decision()
    return None


async def _enforce_command_blacklist(tool_name: str, tool_input: dict[str, Any], explicit_user_id: int | str | None = None) -> Any:
    if tool_name.lower() not in {"exec_command", "bash"}:
        return None

    from app.core.context import get_current_agent_context
    agent_ctx = get_current_agent_context()
    user_id = explicit_user_id or (agent_ctx.user_id if agent_ctx else None)

    if explicit_user_id is None and agent_ctx and agent_ctx.is_admin:
        return None

    if user_id:
        try:
            from app.core.orm import AsyncSessionLocal
            from app.services.permission_service import PermissionService
            from agentscope.permission import PermissionBehavior, PermissionDecision

            async with AsyncSessionLocal() as session:
                perm_service = PermissionService(session)
                perms = await perm_service.get_user_permissions(int(user_id))
                if "admin" in perms.roles:
                    return None

                forbidden_cmds = [cmd.lower().strip() for cmd in (perms.permissions.forbidden_commands or []) if cmd.strip()]
                if forbidden_cmds:
                    command_str = str(tool_input.get("command", ""))
                    for w in forbidden_cmds:
                        if _matches_forbidden_command(command_str, w):
                            return PermissionDecision(
                                behavior=PermissionBehavior.DENY,
                                message=f"安全策略拦截：您的账号已被禁止在该智能体中执行包含 '{w}' 的命令。",
                                decision_reason="hit_user_command_blacklist",
                                bypass_immune=True,
                            )
        except Exception as err:
            logger.exception("Failed to enforce forbidden commands for user %s", user_id)
            return _permission_policy_unavailable_decision()
    return None


def _permission_policy_unavailable_decision() -> Any:
    from agentscope.permission import PermissionBehavior, PermissionDecision

    return PermissionDecision(
        behavior=PermissionBehavior.DENY,
        message="安全策略暂时无法验证，工具调用已拒绝，请稍后重试。",
        decision_reason="user_permission_policy_unavailable",
        bypass_immune=True,
    )


def _shell_command_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        raw_tokens = list(lexer)
    except ValueError:
        raw_tokens = command.split()
    return [
        os.path.basename(token).lower()
        for token in raw_tokens
        if token and not all(char in ";&|()" for char in token)
    ]


def _matches_forbidden_command(command: str, rule: str) -> bool:
    command_tokens = _shell_command_tokens(command)
    rule_tokens = _shell_command_tokens(rule)
    if not rule_tokens or len(rule_tokens) > len(command_tokens):
        return False
    window_size = len(rule_tokens)
    return any(
        command_tokens[index : index + window_size] == rule_tokens
        for index in range(len(command_tokens) - window_size + 1)
    )
