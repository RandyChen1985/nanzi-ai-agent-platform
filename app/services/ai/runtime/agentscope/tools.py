from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import shlex
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from app.services.ai.grounding.models import EvidenceType
from app.services.ai.runtime.agentscope.errors import RuntimeToolError, RuntimeTimeoutError, ToolLoopFuseError
from app.services.ai.runtime.shell_deletion_policy import assess_shell_deletion
from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector
from app.services.ai.runtime.agentscope.tool_timeout import (
    DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT,
    effective_tool_timeout,
)
from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_context
from app.services.ai.runtime.agentscope.tool_result import (
    attach_tool_call_id_metadata,
    build_tool_result_envelope,
    is_tool_execution_success,
    is_tool_result_failure_state,
)

from app.services.ai.runtime.agentscope.tool_parallel import (
    is_tool_concurrency_safe,
    execute_with_concurrency_guard,
)


logger = logging.getLogger(__name__)

from app.services.ai.runtime.command_policy import (  # noqa: E402
    is_shell_like_tool_name,
    matches_forbidden_command,
    shell_command_tokens,
)


ToolSourceType = Literal["static", "generic_api", "mcp", "class", "system", "script_tool"]
RuntimePermissionScope = Literal["read", "write", "ask", "dangerous"]
RuntimeApprovalMode = Literal["ask", "allow", "deny"]

_TOOL_LOOP_MODEL_GUIDANCE = (
    "请停止继续调用任何工具，基于已经获得的结果直接回答用户；"
    "如果现有信息不足，请明确说明限制，不要再次尝试工具调用。"
)

VALID_RUNTIME_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def is_valid_runtime_tool_name(name: str | None) -> bool:
    """Return whether a tool name is accepted by OpenAI-compatible APIs."""
    return bool(VALID_RUNTIME_TOOL_NAME_RE.fullmatch(str(name or "")))


def _tool_loop_fuse_message(reason: str) -> str:
    return f"{reason} {_TOOL_LOOP_MODEL_GUIDANCE}".strip()


def _raise_tool_loop_fuse(reason: str) -> None:
    """熔断后抛出可穿透 AgentScope toolkit 软 ERROR 包装的异常。

    Toolkit 会把普通 Exception 收成 ToolChunk(ERROR) 继续 ReAct；
    DeveloperOrientedException 会向上抛出，便于 runner 硬停工具环并强制文本收敛。
    """
    message = _tool_loop_fuse_message(reason)
    try:
        from agentscope.exception import DeveloperOrientedException
    except Exception:
        raise ToolLoopFuseError(message) from None
    raise DeveloperOrientedException(message)


def _format_runtime_tool_result(result: Any) -> str:
    """将工具结果转换为模型可读文本，并限制进入上下文的长度。"""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
    return truncate_for_context(text)


def _new_tool_call_id(tool_name: str) -> str:
    """为一次运行时工具调用生成稳定的内部调用标识。"""
    return f"{tool_name}:{uuid.uuid4().hex}"


RuntimeToolAuditStatus = Literal["start", "success", "error"]
RuntimeEvidencePolicy = Literal["non_empty", "structured_success", "allow_empty_success"]


READ_ONLY_TOOL_NAMES = {
    "get_current_model",
    "session_status",
    "get_runtime_capabilities",
    "get_current_time",
    "resolve_relative_dates",
    "get_dataset_schema",
    "search_knowledge_base",
    "search_qa_examples",
    "memory_search",
    "fetch_user_long_term_memory",
    "get_myinfo",
    "list_accessible_datasets",
    "list_accessible_directories",
    "list_accessible_knowledge_bases",
    "list_available_agents",
    "request_user_confirmation",
    "ask_user_question",
    "get_my_tasks",
    "jira_search",
    "jira_get_projects",
    "read_file",
    "read_image",
    "search_text",
    "list_process",
    "list_available_skills",
    "read_skill_instruction",
    "directory_tree_navigator",
    "web_renderer_and_snapshot",
    "code_syntax_linter",
    "fetch_static_web_url",
    "web_search_baidu_http",
    "web_search_bing_http",
    "web_search_baidu",
    "system_http_request",
    "sub_agent_call",
    "sub_agent_batch_call",
    "todo_write",
    "browser_snapshot",
    "browser_scroll",
    "browser_wait_for",
    "browser_read_visible",
    "browser_tabs",
    "browser_switch_tab",
    "browser_hover",
    "browser_back",
    "browser_forward",
    "browser_reload",
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
    result_state: Any = None,
    call_id: str | None = None,
) -> Any:
    envelope = build_tool_result_envelope(
        call_id=call_id or f"{tool_name}:{time.time_ns()}",
        producer=tool_name,
        result=result,
        evidence_policy=evidence_policy,
        result_state=result_state,
    )
    if not evidence_types:
        return envelope
    from app.core.context import get_current_agent_context

    context = get_current_agent_context()
    ledger = getattr(context, "grounding_evidence_ledger", None)
    if ledger is not None:
        ledger.record_envelope(
            envelope,
            evidence_types=evidence_types,
            policy=evidence_policy,
        )
    return envelope


def _stream_final_result_state(chunks: list[Any]) -> str | None:
    """把流式 chunk 收敛成一个最终状态，拒绝中间错误冒充成功。"""

    states = [getattr(chunk, "state", None) for chunk in chunks]
    normalized_states = [
        str(getattr(state, "value", state) or "").strip().lower()
        for state in states
    ]
    if any(is_tool_result_failure_state(state) for state in normalized_states):
        return "error"
    if any(normalized_states):
        success_states = {"success", "succeeded", "finished", "completed"}
        return (
            "success"
            if all(state in success_states for state in normalized_states if state)
            else "unknown"
        )
    return None


def _redact_runtime_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """在审计边界统一脱敏，避免敏感浏览器输入进入事件或审计存储。"""
    if tool_name == "browser_upload":
        payload = dict(arguments)
        if "file_path" in payload:
            payload["file_path"] = "<redacted>"
        return payload
    if tool_name == "browser_set_cookies":
        from app.services.ai.browser.browser_policy import redact_browser_cookies

        payload = dict(arguments)
        payload["cookies"] = redact_browser_cookies(payload.get("cookies"))
        return payload
    if tool_name == "browser_execute_js":
        payload = dict(arguments)
        if "script" in payload:
            payload["script"] = f"<redacted script length={len(str(payload['script']))}>"
        return payload
    if tool_name == "browser_handle_dialog":
        payload = dict(arguments)
        if "prompt_text" in payload:
            payload["prompt_text"] = "<redacted>"
        return payload
    if tool_name != "browser_fill":
        return dict(arguments)
    from app.services.ai.browser.browser_policy import redact_browser_arguments

    # browser_fill 不把 sensitive 暴露给模型，审计边界按服务端策略强制脱敏。
    return redact_browser_arguments({**arguments, "sensitive": True})


async def _browser_permission_decision(
    tool_name: str,
    tool_input: dict[str, Any],
) -> Any:
    """将 BrowserSession 的 guarded/autopilot 策略接入 AgentScope 权限层。"""
    if tool_name not in {
        "browser_click",
        "browser_fill",
        "browser_press",
        "browser_select_option",
        "browser_drag",
        "browser_slider_drag",
        "browser_upload",
        "browser_download",
        "browser_execute_js",
        "browser_set_cookies",
        "browser_handle_dialog",
    }:
        return None

    from agentscope.permission import PermissionBehavior, PermissionDecision

    from app.core.context import get_current_agent_context

    context = get_current_agent_context()
    session_id = getattr(context, "browser_session_id", None) if context else None
    user_id = getattr(context, "user_id", None) if context else None
    if not session_id or user_id is None:
        # 让通用权限层处理缺少浏览器上下文的异常调用。
        return None

    from app.core.orm import AsyncSessionLocal
    from app.services.ai.browser.browser_policy import classify_browser_action
    from app.services.ai.browser.browser_runtime import browser_runtime
    from app.services.ai.browser.browser_session_service import BrowserAccessDenied, BrowserSessionService

    snapshot_id = str(tool_input.get("snapshot_id", ""))
    target_ref = str(tool_input.get("target_ref", ""))
    if tool_name in {"browser_drag", "browser_slider_drag"} and not target_ref:
        target_ref = str(tool_input.get("source_ref", ""))
    element = None
    try:
        if target_ref:
            snapshot = browser_runtime.cached_snapshot(str(session_id), snapshot_id)
            element = next((item for item in snapshot.elements if item.ref == target_ref), None)
    except Exception:
        element = None

    if tool_name == "browser_press" and target_ref and element is None:
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message="浏览器快照已过期，请重新获取页面快照后重试。",
            decision_reason="browser_target_not_in_snapshot",
            bypass_immune=True,
        )

    if tool_name in {
        "browser_click",
        "browser_fill",
        "browser_select_option",
        "browser_drag",
        "browser_slider_drag",
        "browser_upload",
        "browser_download",
    } and element is None:
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message="浏览器快照已过期，请重新获取页面快照后重试。",
            decision_reason="browser_target_not_in_snapshot",
            bypass_immune=True,
        )

    if tool_name == "browser_fill":
        if element is None:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="无法确认浏览器输入目标，请刷新页面快照后重试。",
                decision_reason="browser_target_not_in_snapshot",
                bypass_immune=True,
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="浏览器输入可自动执行。",
            decision_reason="browser_fill_target",
        )

    if tool_name == "browser_upload":
        action_class = "commit"
    elif tool_name in {"browser_execute_js", "browser_set_cookies"}:
        action_class = "commit"
    elif tool_name == "browser_handle_dialog":
        action_class = (
            "interact"
            if str(tool_input.get("action") or "accept").casefold() == "dismiss"
            else "commit"
        )
    elif tool_name == "browser_press":
        key = str(tool_input.get("key") or "").strip().casefold()
        if element is None:
            action_class = "commit" if key in {"enter", "numpadenter", "ctrl+enter", "meta+enter"} else "interact"
        elif key in {"enter", "numpadenter", "ctrl+enter", "meta+enter"}:
            target_class = classify_browser_action(role=element.role, name=element.name)
            role = str(element.role or "").casefold()
            if role == "searchbox" and target_class != "commit":
                action_class = "interact"
            elif role in {"textbox", "combobox"} and target_class != "commit":
                action_class = "commit"
            else:
                action_class = target_class
        else:
            action_class = "interact"
    else:
        action_class = classify_browser_action(role=element.role, name=element.name)
    async with AsyncSessionLocal() as db:
        try:
            session = await BrowserSessionService(db).get_owned_session(
                user_id=int(user_id), session_id=str(session_id)
            )
        except (BrowserAccessDenied, TypeError, ValueError):
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="浏览器会话不存在或无权访问。",
                decision_reason="browser_session_access_denied",
                bypass_immune=True,
            )

    if action_class == "commit" and session.approval_mode != "autopilot":
        decision_reason = (
            "guarded_browser_sensitive_tool"
            if tool_name in {"browser_execute_js", "browser_set_cookies"}
            else "guarded_browser_commit"
        )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="该浏览器动作可能提交、删除或产生外部副作用，需要用户确认。",
            decision_reason=decision_reason,
        )
    return PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message="浏览器交互动作可自动执行。",
        decision_reason="browser_interaction_allowed",
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


def _callable_is_async_generator(callable_obj: Callable[..., Any]) -> bool:
    return inspect.isasyncgenfunction(callable_obj) or inspect.isasyncgenfunction(
        getattr(callable_obj, "__call__", None)
    )


def _callable_is_coroutine(callable_obj: Callable[..., Any]) -> bool:
    return inspect.iscoroutinefunction(callable_obj) or inspect.iscoroutinefunction(
        getattr(callable_obj, "__call__", None)
    )


def _timeout_parameter_unit(
    tool_name: str,
    input_schema: Any,
) -> str | None:
    properties = input_schema.get("properties") if isinstance(input_schema, dict) else None
    timeout_schema = properties.get("timeout") if isinstance(properties, dict) else None
    if not isinstance(timeout_schema, dict):
        return None
    description = str(timeout_schema.get("description") or "").lower()
    if tool_name == "Bash" or "millisecond" in description or "毫秒" in description:
        return "milliseconds"
    return "seconds"


def _prepare_timeout_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    input_schema: Any,
    timeout_seconds: float | None,
) -> tuple[dict[str, Any], float]:
    effective = effective_tool_timeout(
        timeout_seconds or DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT
    )
    unit = _timeout_parameter_unit(tool_name, input_schema)
    if unit is None:
        return dict(arguments), effective

    adjusted = dict(arguments)
    multiplier = 1000.0 if unit == "milliseconds" else 1.0
    adjusted["timeout"] = max(1, int(round(effective * multiplier)))
    return adjusted, effective


async def _start_callable_with_timeout(
    callable_obj: Callable[..., Any],
    arguments: dict[str, Any],
    timeout_seconds: float,
) -> Any:
    """在统一 deadline 内**启动**一次工具调用，返回未消费的结果。

    与 ``_invoke_callable_with_timeout`` 的唯一区别：异步生成器不在这里拼成列表，
    而是原样返回，交给调用方逐片消费——进度片必须在工具执行过程中抵达前端，
    一旦在这里被消费掉就只剩最终结果了。迭代期间的 deadline 由消费方负责
    （``_stream_runtime_result`` 用 ``asyncio.timeout`` 覆盖）。
    """

    async def run() -> Any:
        if _callable_is_async_generator(callable_obj) or _callable_is_coroutine(callable_obj):
            result = callable_obj(**arguments)
        else:
            result = await asyncio.to_thread(callable_obj, **arguments)
        if inspect.isawaitable(result):
            result = await result
        return result

    return await asyncio.wait_for(run(), timeout=timeout_seconds)


def _is_tool_progress_item(item: Any) -> bool:
    """是否是「远端进度片」而不是工具结果。

    约定与 AgentScope 一致：进度片带 ``metadata["mcp_progress"]``，且不是最后一片。
    用 duck typing 判断，避免工具层强依赖 agent 的消息类型。
    """
    metadata = getattr(item, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    if getattr(item, "is_last", False):
        return False
    return metadata.get("mcp_progress") is not None


async def _close_async_generator(generator: Any) -> None:
    close = getattr(generator, "aclose", None)
    if close is None:
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.debug("Failed to close timed-out async generator", exc_info=True)


@dataclass(frozen=True)
class RuntimeToolSpec:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    source_type: ToolSourceType
    callable: Callable[..., Any]
    permission_scope: RuntimePermissionScope = "ask"
    display_name: str | None = None
    evidence_types: frozenset[EvidenceType] = frozenset()
    evidence_policy: RuntimeEvidencePolicy = "non_empty"
    evidence_inference_disabled: bool = False
    timeout_seconds: float | None = None
    audit_callback: Callable[[RuntimeToolAuditEvent], Any] | None = None
    native_tool: Any | None = None
    is_concurrency_safe: bool | None = None
    # 平台内部透传字段（不进入模型可见 schema）。脚本工具用它把配置带到
    # bind_configured_tools_to_workspace，由后者包上沙箱执行器。
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_read_only(self) -> bool:
        return self.permission_scope == "read"

    @property
    def concurrency_safe(self) -> bool:
        return is_tool_concurrency_safe(
            tool_name=self.name,
            permission_scope=self.permission_scope,
            approval_mode="allow",
            source_type=self.source_type,
            custom_flag=self.is_concurrency_safe,
            read_only_tool_names=READ_ONLY_TOOL_NAMES,
        )

    async def invoke(
        self,
        arguments: dict[str, Any] | None = None,
        *,
        call_id: str | None = None,
    ) -> Any:
        arguments = arguments or {}
        call_arguments, timeout_seconds = _prepare_timeout_arguments(
            self.name,
            arguments,
            self.parameters_schema,
            self.timeout_seconds,
        )
        audit_arguments = _redact_runtime_tool_arguments(self.name, arguments)
        start = time.perf_counter()
        await self._emit_audit(
            RuntimeToolAuditEvent(
                tool_name=self.name,
                status="start",
                source_type=self.source_type,
                permission_scope=self.permission_scope,
                arguments=audit_arguments,
            )
        )
        try:
            result = await _start_callable_with_timeout(
                self.callable,
                call_arguments,
                timeout_seconds,
            )
            if inspect.isasyncgen(result):
                # 流式工具（远端 MCP 会发 progress）：原样交给
                # ``AgentScopeRuntimeTool`` 逐片消费，证据与审计在
                # ``finish_streamed()`` 里按最终结果补记一次。
                return result
            result_ok, result_state = self._record_result_evidence(result, call_id)
            await self._emit_audit(
                RuntimeToolAuditEvent(
                    tool_name=self.name,
                    status="success" if result_ok else "error",
                    source_type=self.source_type,
                    permission_scope=self.permission_scope,
                    arguments=audit_arguments,
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                    result_preview=_preview_result(result),
                    error=None if result_ok else "工具明确返回了失败结果",
                )
            )
            return result
        except TimeoutError as exc:
            wrapped = RuntimeTimeoutError(
                f"Tool '{self.name}' timed out",
                cause=exc,
                details={"tool_name": self.name, "timeout_seconds": timeout_seconds},
            )
            _record_evidence_result(
                tool_name=self.name,
                evidence_types=self.evidence_types,
                evidence_policy=self.evidence_policy,
                result={"status": "error", "message": str(wrapped)},
                result_state="error",
                call_id=call_id,
            )
            await self._emit_error_audit(audit_arguments, start, wrapped)
            raise wrapped from exc
        except Exception as exc:
            wrapped = RuntimeToolError(
                f"Tool '{self.name}' failed: {exc}",
                cause=exc,
                details={"tool_name": self.name},
            )
            _record_evidence_result(
                tool_name=self.name,
                evidence_types=self.evidence_types,
                evidence_policy=self.evidence_policy,
                result={"status": "error", "message": str(wrapped)},
                result_state="error",
                call_id=call_id,
            )
            await self._emit_error_audit(audit_arguments, start, wrapped)
            raise wrapped from exc

    def _record_result_evidence(
        self,
        result: Any,
        call_id: str | None,
    ) -> tuple[bool, str | None]:
        """按最终业务结果记一次证据，返回 (是否成功, 结果状态)。"""
        result_state = (
            _stream_final_result_state(result)
            if isinstance(result, (list, tuple))
            else None
        )
        if result_state is None and not is_tool_execution_success(result):
            result_state = "error"
        _record_evidence_result(
            tool_name=self.name,
            evidence_types=self.evidence_types,
            evidence_policy=self.evidence_policy,
            result=result,
            result_state=result_state,
            call_id=call_id,
        )
        return is_tool_execution_success(result, result_state=result_state), result_state

    async def finish_streamed(
        self,
        result: Any,
        *,
        call_id: str | None,
        arguments: dict[str, Any] | None,
        start: float,
    ) -> bool:
        """流式工具结束后补记证据与审计，与 ``invoke`` 的成功分支同口径。"""
        result_ok, _ = self._record_result_evidence(result, call_id)
        await self._emit_audit(
            RuntimeToolAuditEvent(
                tool_name=self.name,
                status="success" if result_ok else "error",
                source_type=self.source_type,
                permission_scope=self.permission_scope,
                arguments=arguments or {},
                elapsed_ms=(time.perf_counter() - start) * 1000,
                result_preview=_preview_result(result),
                error=None if result_ok else "工具明确返回了失败结果",
            )
        )
        return result_ok

    async def emit_error_audit(
        self,
        arguments: dict[str, Any] | None,
        start: float,
        exc: Exception,
    ) -> None:
        """供流式路径复用：记录一次失败审计（参数在这里统一脱敏）。"""
        await self._emit_error_audit(
            _redact_runtime_tool_arguments(self.name, arguments or {}),
            start,
            exc,
        )

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


def apply_delegation_tool_filter(
    tools: list[RuntimeToolSpec],
    allowed_names: list[str] | None = None,
) -> list[RuntimeToolSpec]:
    """Apply the current sub-agent allowlist to visible and executable tools."""
    if allowed_names is None:
        from app.core.context import get_current_agent_context

        context = get_current_agent_context()
        allowed_names = getattr(context, "delegation_tool_filter", None)
    if allowed_names is None:
        return list(tools)
    allowed = {str(name) for name in allowed_names}
    return [tool for tool in tools if str(tool.name) in allowed]


def filter_valid_runtime_tool_specs(
    tools: list[RuntimeToolSpec] | tuple[RuntimeToolSpec, ...] | None,
) -> list[RuntimeToolSpec]:
    """Drop tool specs whose names would make an OpenAI-compatible request invalid."""
    valid: list[RuntimeToolSpec] = []
    for tool in tools or ():
        if is_valid_runtime_tool_name(tool.name):
            valid.append(tool)
        else:
            logger.warning(
                "Dropping runtime tool with invalid model name %r (source=%s)",
                tool.name,
                tool.source_type,
            )
    return valid


class AgentScopeRuntimeTool:
    is_external_tool = False
    is_state_injected = False
    is_mcp = False
    mcp_name = None

    @property
    def is_concurrency_safe(self) -> bool:
        return is_tool_concurrency_safe(
            tool_name=self.name,
            permission_scope=self.spec.permission_scope,
            approval_mode=self.approval_mode,
            source_type=self.spec.source_type,
            custom_flag=self.spec.is_concurrency_safe,
            read_only_tool_names=READ_ONLY_TOOL_NAMES,
        )

    def __init__(
        self,
        spec: RuntimeToolSpec,
        approval_mode: RuntimeApprovalMode | str | None = None,
        loop_detector: ToolLoopDetector | None = None,
        user_id: int | str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self.spec = spec
        self.name = spec.name
        self.display_name = spec.display_name or spec.name
        self.description = spec.description
        self.input_schema = spec.parameters_schema
        self.is_read_only = spec.is_read_only
        self.approval_mode = _normalize_runtime_approval_mode(approval_mode)
        self.loop_detector = loop_detector
        self.user_id = user_id
        self.conversation_id = conversation_id

    def _check_tool_loop(self, tool_input: dict[str, Any]) -> None:
        if not self.loop_detector:
            return
        verdict = self.loop_detector.record(self.name, tool_input)
        if verdict.fused:
            _raise_tool_loop_fuse(verdict.message)

    async def check_permissions(self, tool_input: dict[str, Any], context: Any) -> Any:
        try:
            from agentscope.permission import PermissionBehavior, PermissionDecision
        except Exception:
            return None

        deletion_decision = _shell_deletion_permission_decision(
            self.name,
            tool_input,
            cwd=os.getcwd(),
        )
        if deletion_decision and deletion_decision.behavior == PermissionBehavior.DENY:
            return deletion_decision

        forbidden_decision = await _enforce_tool_forbidden(self.name, getattr(self, "user_id", None))
        if forbidden_decision:
            return forbidden_decision

        blacklist_decision = await _enforce_command_blacklist(self.name, tool_input, getattr(self, "user_id", None))
        if blacklist_decision:
            return blacklist_decision

        if deletion_decision:
            return deletion_decision

        if self.name in {
            "browser_click",
            "browser_fill",
            "browser_press",
            "browser_select_option",
            "browser_drag",
            "browser_slider_drag",
            "browser_upload",
            "browser_download",
            "browser_execute_js",
            "browser_set_cookies",
            "browser_handle_dialog",
        }:
            if self.approval_mode == "deny":
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Tool '{self.name}' is denied by runtime approval mode.",
                    decision_reason=f"runtime approval mode: {self.approval_mode}",
                    bypass_immune=True,
                )
            browser_decision = await _browser_permission_decision(self.name, tool_input)
            if browser_decision is not None:
                return browser_decision

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
        grant_decision = await _session_grant_decision(
            tool_name=self.name,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
        )
        if grant_decision is not None:
            return grant_decision
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
        call_id = _new_tool_call_id(self.spec.name)
        try:
            async def _invoke():
                return await self.spec.invoke(kwargs, call_id=call_id)

            res = await execute_with_concurrency_guard(_invoke)
            if inspect.isasyncgen(res):
                # 远端 MCP 发了进度：把生成器交给 agent，由它逐片转成事件。
                return self._stream_runtime_result(
                    res,
                    call_id=call_id,
                    arguments=kwargs,
                )
            return ToolChunk(
                content=[TextBlock(text=_format_runtime_tool_result(res))],
                state=(
                    ToolResultState.SUCCESS
                    if is_tool_execution_success(res)
                    else ToolResultState.ERROR
                ),
                metadata={"nanzi_call_id": call_id},
            )
        except Exception as exc:
            if isinstance(exc, PermissionError):
                raise
            from app.services.ai.runtime.agentscope.errors import ToolLoopFuseError

            try:
                from agentscope.exception import DeveloperOrientedException

                if isinstance(exc, DeveloperOrientedException):
                    raise
            except ImportError:
                pass
            if isinstance(exc, (ToolLoopFuseError, asyncio.CancelledError)):
                raise
            logger.warning(
                "[RuntimeTool] Tool '%s' execution failed gracefully: %s",
                self.spec.name,
                exc,
            )
            return ToolChunk(
                content=[TextBlock(text=f"工具 [{self.spec.name}] 调用发生异常: {exc}")],
                state=ToolResultState.ERROR,
                metadata={"nanzi_call_id": call_id},
            )


    async def _stream_runtime_result(
        self,
        generator: Any,
        *,
        call_id: str,
        arguments: dict[str, Any],
    ) -> Any:
        """逐片透传进度片，最后一片是由业务结果包装出的 ``ToolChunk``。

        并发护栏只覆盖「启动」，与 ``AgentScopeNativeApprovalTool`` 同构；
        迭代期间用 ``asyncio.timeout`` 施加平台工具超时，避免长工具脱离约束。
        """
        from agentscope.message import TextBlock, ToolResultState
        from agentscope.tool import ToolChunk

        _, timeout_seconds = _prepare_timeout_arguments(
            self.spec.name,
            arguments,
            self.input_schema,
            self.spec.timeout_seconds,
        )
        start = time.perf_counter()
        final_result: Any = None
        has_result = False
        try:
            async with asyncio.timeout(timeout_seconds):
                async for item in generator:
                    if _is_tool_progress_item(item):
                        # 进度片原样上抛：agent 会把它转成 CustomEvent。
                        yield item
                        continue
                    final_result = item
                    has_result = True
        except asyncio.CancelledError:
            await _close_async_generator(generator)
            raise
        except Exception as exc:
            await _close_async_generator(generator)
            from app.services.ai.runtime.agentscope.errors import ToolLoopFuseError

            try:
                from agentscope.exception import DeveloperOrientedException

                if isinstance(exc, DeveloperOrientedException):
                    raise
            except ImportError:
                pass
            if isinstance(exc, (ToolLoopFuseError, PermissionError)):
                raise
            msg = (
                f"工具 [{self.spec.name}] 调用超时: {exc}"
                if isinstance(exc, TimeoutError)
                else f"工具 [{self.spec.name}] 调用发生异常: {exc}"
            )
            _record_evidence_result(
                tool_name=self.spec.name,
                evidence_types=self.spec.evidence_types,
                evidence_policy=self.spec.evidence_policy,
                result={"status": "error", "message": msg},
                result_state="error",
                call_id=call_id,
            )
            await self.spec.emit_error_audit(
                arguments,
                start,
                exc,
            )
            logger.warning(
                "[RuntimeTool] Streamed tool '%s' failed gracefully: %s",
                self.spec.name,
                exc,
            )
            yield ToolChunk(
                content=[TextBlock(text=msg)],
                state=ToolResultState.ERROR,
                is_last=True,
                metadata={"nanzi_call_id": call_id},
            )
            return

        result = attach_tool_call_id_metadata(
            final_result if has_result else None,
            call_id,
        )
        result_ok = await self.spec.finish_streamed(
            result,
            call_id=call_id,
            arguments=arguments,
            start=start,
        )
        yield ToolChunk(
            content=[
                TextBlock(text=_format_runtime_tool_result(result) if has_result else "")
            ],
            state=ToolResultState.SUCCESS if result_ok else ToolResultState.ERROR,
            is_last=True,
            metadata={"nanzi_call_id": call_id},
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
        conversation_id: str | None = None,
        evidence_types: frozenset[EvidenceType] = frozenset(),
        evidence_policy: str = "non_empty",
        timeout_seconds: float | None = None,
        source_type: ToolSourceType = "system",
        audit_callback: Callable[[RuntimeToolAuditEvent], Any] | None = None,
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
        self.conversation_id = conversation_id
        self.evidence_types = evidence_types
        self.evidence_policy = evidence_policy
        self.timeout_seconds = timeout_seconds or DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT
        self.source_type = source_type
        self.audit_callback = audit_callback

    def _check_tool_loop(self, tool_input: dict[str, Any]) -> None:
        if not self.loop_detector:
            return
        verdict = self.loop_detector.record(self.name, tool_input)
        if verdict.fused:
            _raise_tool_loop_fuse(verdict.message)

    @property
    def is_concurrency_safe(self) -> bool:
        native_flag = getattr(self.native_tool, "is_concurrency_safe", None)
        return is_tool_concurrency_safe(
            tool_name=self.name,
            permission_scope=self.permission_scope,
            approval_mode=self.approval_mode,
            source_type=self.source_type,
            custom_flag=native_flag,
            read_only_tool_names=READ_ONLY_TOOL_NAMES,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.native_tool, name)

    async def check_permissions(self, tool_input: dict[str, Any], context: Any) -> Any:
        try:
            from agentscope.permission import PermissionBehavior, PermissionDecision
        except Exception:
            return None

        deletion_decision = _shell_deletion_permission_decision(
            self.name,
            tool_input,
            cwd=getattr(self.native_tool, "_cwd", None) or os.getcwd(),
        )
        if deletion_decision and deletion_decision.behavior == PermissionBehavior.DENY:
            return deletion_decision

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

        if deletion_decision:
            return deletion_decision

        path_checker = getattr(self.native_tool, "check_path_access", None)
        if path_checker:
            try:
                path_result = path_checker(tool_input)
                if inspect.isawaitable(path_result):
                    await path_result
            except (PermissionError, ValueError) as exc:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=str(exc),
                    decision_reason="workspace_path_access_denied",
                    bypass_immune=True,
                )

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
                result = await result
            if result is not None:
                grant_decision = await _session_grant_override(
                    result,
                    context=context,
                    tool_name=self.name,
                    user_id=self.user_id,
                    conversation_id=self.conversation_id,
                )
                if grant_decision is not None:
                    return grant_decision
                return result
        grant_decision = await _session_grant_decision(
            tool_name=self.name,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
        )
        if grant_decision is not None:
            return grant_decision
        return PermissionDecision(
            behavior=(
                PermissionBehavior.ALLOW
                if self.permission_scope == "read"
                else PermissionBehavior.ASK
            ),
            message=(
                f"Tool '{self.name}' is read-only and can run automatically."
                if self.permission_scope == "read"
                else f"Tool '{self.name}' requires user confirmation before execution."
            ),
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
        call_id = _new_tool_call_id(self.name)
        from app.services.ai.runtime.agentscope.workspace import (
            WORKSPACE_BUILTIN_TOOL_NAMES,
            enhance_workspace_error_message,
        )

        required_argument = None
        if self.name in {"Read", "Write", "Edit"} and not str(kwargs.get("file_path") or "").strip():
            required_argument = "file_path"
        elif self.name in {"Glob", "Grep"} and not str(kwargs.get("pattern") or "").strip():
            required_argument = "pattern"
        if required_argument:
            from agentscope.message import TextBlock, ToolResultState
            from agentscope.tool import ToolChunk

            _record_evidence_result(
                tool_name=self.name,
                evidence_types=self.evidence_types,
                evidence_policy=self.evidence_policy,
                result={
                    "status": "error",
                    "message": f"缺少必填参数 {required_argument}",
                },
                result_state="error",
                call_id=call_id,
            )
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"{self.name} 调用失败：缺少必填参数 {required_argument}。"
                            + (
                                "请根据 paths.file_tools 传入目标文件路径。"
                                if required_argument == "file_path"
                                else "请传入具体的文件匹配模式或搜索表达式。"
                            )
                        )
                    )
                ],
                state=ToolResultState.ERROR,
                is_last=True,
                metadata={"nanzi_call_id": call_id},
            )

        is_file_tool = self.name in (WORKSPACE_BUILTIN_TOOL_NAMES | {"read_file", "write_file", "edit_file", "glob_files", "search_text"})
        call_arguments, timeout_seconds = _prepare_timeout_arguments(
            self.name,
            kwargs,
            self.input_schema,
            self.timeout_seconds,
        )
        audit_arguments = _redact_runtime_tool_arguments(self.name, kwargs)
        start = time.perf_counter()
        await self._emit_audit(
            RuntimeToolAuditEvent(
                tool_name=self.name,
                status="start",
                source_type=self.source_type,
                permission_scope=self.permission_scope,
                arguments=audit_arguments,
            )
        )
        try:
            async def invoke_native() -> Any:
                if _callable_is_async_generator(self.native_tool) or _callable_is_coroutine(self.native_tool):
                    result = self.native_tool(**call_arguments)
                else:
                    result = await asyncio.to_thread(self.native_tool, **call_arguments)
                if inspect.isawaitable(result):
                    result = await result
                return result

            async def _guarded_native_invoke():
                return await asyncio.wait_for(
                    invoke_native(),
                    timeout=timeout_seconds,
                )

            result = await execute_with_concurrency_guard(_guarded_native_invoke)
            if inspect.isasyncgen(result):
                return self._stream_native_result(
                    result,
                    audit_arguments=audit_arguments,
                    start=start,
                    timeout_seconds=timeout_seconds,
                    is_file_tool=is_file_tool,
                    call_id=call_id,
                )
        except Exception as exc:
            if isinstance(exc, PermissionError):
                _record_evidence_result(
                    tool_name=self.name,
                    evidence_types=self.evidence_types,
                    evidence_policy=self.evidence_policy,
                    result={"status": "denied", "message": str(exc)},
                    result_state="denied",
                    call_id=call_id,
                )
                raise
            from app.services.ai.runtime.agentscope.errors import ToolLoopFuseError

            try:
                from agentscope.exception import DeveloperOrientedException

                if isinstance(exc, DeveloperOrientedException):
                    raise
            except ImportError:
                pass
            if isinstance(exc, (ToolLoopFuseError, asyncio.CancelledError)):
                raise
            if isinstance(exc, TimeoutError):
                wrapped = RuntimeTimeoutError(
                    f"Tool '{self.name}' timed out",
                    cause=exc,
                    details={
                        "tool_name": self.name,
                        "timeout_seconds": timeout_seconds,
                    },
                )
                _record_evidence_result(
                    tool_name=self.name,
                    evidence_types=self.evidence_types,
                    evidence_policy=self.evidence_policy,
                    result={"status": "error", "message": str(wrapped)},
                    result_state="error",
                    call_id=call_id,
                )
                await self._emit_error_audit(audit_arguments, start, wrapped)
                return self._native_error_chunk(
                    f"工具 [{self.name}] 调用超时: {wrapped}",
                    call_id=call_id,
                )
            from agentscope.message import TextBlock, ToolResultState
            from agentscope.tool import ToolChunk

            msg = enhance_workspace_error_message(exc) if is_file_tool else str(exc)
            wrapped = RuntimeToolError(
                f"Tool '{self.name}' failed: {msg}",
                cause=exc,
                details={"tool_name": self.name},
            )
            _record_evidence_result(
                tool_name=self.name,
                evidence_types=self.evidence_types,
                evidence_policy=self.evidence_policy,
                result={"status": "error", "message": str(wrapped)},
                result_state="error",
                call_id=call_id,
            )
            await self._emit_error_audit(audit_arguments, start, wrapped)
            logger.warning(
                "[NativeTool] Tool '%s' execution failed gracefully: %s",
                self.name,
                msg,
            )
            return ToolChunk(
                content=[TextBlock(text=f"工具 [{self.name}] 调用发生异常: {msg}")],
                state=ToolResultState.ERROR,
                is_last=True,
                metadata={"nanzi_call_id": call_id},
            )

        if is_file_tool and isinstance(result, str):
            result = enhance_workspace_error_message(result)

        result = attach_tool_call_id_metadata(result, call_id)

        _record_evidence_result(
            tool_name=self.name,
            evidence_types=self.evidence_types,
            evidence_policy=self.evidence_policy,
            result=result,
            call_id=call_id,
        )
        result_ok = is_tool_execution_success(result)
        await self._emit_audit(
            RuntimeToolAuditEvent(
                tool_name=self.name,
                status="success" if result_ok else "error",
                source_type=self.source_type,
                permission_scope=self.permission_scope,
                arguments=audit_arguments,
                elapsed_ms=(time.perf_counter() - start) * 1000,
                result_preview=_preview_result(result),
                error=None if result_ok else "工具明确返回了失败结果",
            )
        )
        if not result_ok:
            return self._native_error_chunk(
                _format_runtime_tool_result(result),
                call_id=call_id,
            )
        if result is None:
            return self._native_empty_success_chunk(call_id=call_id)
        return result

    async def _stream_native_result(
        self,
        generator: Any,
        *,
        audit_arguments: dict[str, Any],
        start: float,
        timeout_seconds: float,
        is_file_tool: bool,
        call_id: str,
    ) -> Any:
        chunks: list[Any] = []
        try:
            async with asyncio.timeout(timeout_seconds):
                async for chunk in generator:
                    chunks.append(chunk)
                    yield attach_tool_call_id_metadata(chunk, call_id)
        except asyncio.CancelledError:
            await _close_async_generator(generator)
            raise
        except Exception as exc:
            await _close_async_generator(generator)
            if isinstance(exc, PermissionError):
                _record_evidence_result(
                    tool_name=self.name,
                    evidence_types=self.evidence_types,
                    evidence_policy=self.evidence_policy,
                    result={"status": "denied", "message": str(exc)},
                    result_state="denied",
                    call_id=call_id,
                )
                raise
            from app.services.ai.runtime.agentscope.errors import ToolLoopFuseError

            try:
                from agentscope.exception import DeveloperOrientedException

                if isinstance(exc, DeveloperOrientedException):
                    raise
            except ImportError:
                pass
            if isinstance(exc, ToolLoopFuseError):
                raise
            if isinstance(exc, TimeoutError):
                wrapped = RuntimeTimeoutError(
                    f"Tool '{self.name}' timed out",
                    cause=exc,
                    details={
                        "tool_name": self.name,
                        "timeout_seconds": timeout_seconds,
                    },
                )
                _record_evidence_result(
                    tool_name=self.name,
                    evidence_types=self.evidence_types,
                    evidence_policy=self.evidence_policy,
                    result={"status": "error", "message": str(wrapped)},
                    result_state="error",
                    call_id=call_id,
                )
                await self._emit_error_audit(audit_arguments, start, wrapped)
                yield self._native_error_chunk(
                    f"工具 [{self.name}] 调用超时: {wrapped}",
                    call_id=call_id,
                )
                return
            from app.services.ai.runtime.agentscope.workspace import enhance_workspace_error_message

            msg = enhance_workspace_error_message(exc) if is_file_tool else str(exc)
            wrapped = RuntimeToolError(
                f"Tool '{self.name}' failed: {msg}",
                cause=exc,
                details={"tool_name": self.name},
            )
            _record_evidence_result(
                tool_name=self.name,
                evidence_types=self.evidence_types,
                evidence_policy=self.evidence_policy,
                result={"status": "error", "message": str(wrapped)},
                result_state="error",
                call_id=call_id,
            )
            await self._emit_error_audit(audit_arguments, start, wrapped)
            logger.warning(
                "[NativeTool] Tool '%s' execution failed gracefully: %s",
                self.name,
                msg,
            )
            yield self._native_error_chunk(
                f"工具 [{self.name}] 调用发生异常: {msg}",
                call_id=call_id,
            )
            return

        _record_evidence_result(
            tool_name=self.name,
            evidence_types=self.evidence_types,
            evidence_policy=self.evidence_policy,
            result=chunks,
            result_state=_stream_final_result_state(chunks),
            call_id=call_id,
        )
        result_state = _stream_final_result_state(chunks)
        result_ok = is_tool_execution_success(chunks, result_state=result_state)
        await self._emit_audit(
            RuntimeToolAuditEvent(
                tool_name=self.name,
                status="success" if result_ok else "error",
                source_type=self.source_type,
                permission_scope=self.permission_scope,
                arguments=audit_arguments,
                elapsed_ms=(time.perf_counter() - start) * 1000,
                result_preview=_preview_result(chunks),
                error=None if result_ok else "流式工具明确返回了失败结果",
            )
        )

    @staticmethod
    def _native_error_chunk(message: str, *, call_id: str | None = None) -> Any:
        from agentscope.message import TextBlock, ToolResultState
        from agentscope.tool import ToolChunk

        return ToolChunk(
            content=[TextBlock(text=message)],
            state=ToolResultState.ERROR,
            is_last=True,
            metadata=(
                {"nanzi_call_id": call_id}
                if call_id
                else {}
            ),
        )

    @staticmethod
    def _native_empty_success_chunk(*, call_id: str | None = None) -> Any:
        """把无返回体的动作工具转换为合法的 AgentScope 空成功块。"""

        from agentscope.message import ToolResultState
        from agentscope.tool import ToolChunk

        return ToolChunk(
            content=[],
            state=ToolResultState.SUCCESS,
            is_last=True,
            metadata=(
                {"nanzi_call_id": call_id}
                if call_id
                else {}
            ),
        )

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
    conversation_id: str | None = None,
) -> Any:
    if spec.native_tool is not None:
        return AgentScopeNativeApprovalTool(
            spec.native_tool,
            approval_mode=approval_mode,
            permission_scope=spec.permission_scope,
            loop_detector=loop_detector,
            user_id=user_id,
            conversation_id=conversation_id,
            evidence_types=spec.evidence_types,
            evidence_policy=spec.evidence_policy,
            timeout_seconds=spec.timeout_seconds,
            source_type=spec.source_type,
            audit_callback=spec.audit_callback,
        )
    return AgentScopeRuntimeTool(
        spec,
        approval_mode=approval_mode,
        loop_detector=loop_detector,
        user_id=user_id,
        conversation_id=conversation_id,
    )


def runtime_tool_from_native(
    native_tool: Any,
    *,
    approval_mode: RuntimeApprovalMode | str | None = None,
    user_id: int | str | None = None,
    conversation_id: str | None = None,
    timeout_seconds: float | None = None,
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
        conversation_id=conversation_id,
        evidence_types=evidence_types,
        evidence_policy=NATIVE_TOOL_EVIDENCE_POLICIES.get(native_name, "non_empty"),
        timeout_seconds=timeout_seconds,
    )


def build_toolkit(
    tool_specs: list[RuntimeToolSpec],
    *,
    approval_mode: RuntimeApprovalMode | str | None = None,
    loop_detector: ToolLoopDetector | None = None,
    user_id: int | str | None = None,
    conversation_id: str | None = None,
):
    toolkit_cls = _load_agentscope_toolkit()
    tool_specs = filter_valid_runtime_tool_specs(tool_specs)
    tools = [
        runtime_tool_from_spec(
            spec,
            approval_mode=approval_mode,
            loop_detector=loop_detector,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        for spec in tool_specs
    ]
    return toolkit_cls(tools=tools)


def _schema_from_legacy_tool(tool: Any) -> dict[str, Any]:
    mcp_input_schema = getattr(tool, "mcp_input_schema", None)
    if isinstance(mcp_input_schema, dict):
        return mcp_input_schema
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
            if _callable_is_coroutine(tool) or _callable_is_async_generator(tool):
                result = tool(**kwargs)
            else:
                result = await asyncio.to_thread(tool, **kwargs)
            if inspect.isawaitable(result):
                return await result
            if inspect.isasyncgen(result):
                parts = []
                async for chunk in result:
                    parts.append(_tool_chunk_to_text(chunk))
                return "".join(parts)
            return result
        raise TypeError(f"Tool {getattr(tool, 'name', repr(tool))} is not callable")

    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    if not name:
        raise ValueError("Legacy tool is missing a name")
    tool_scope = getattr(tool, "permission_scope", None)
    if not tool_scope and getattr(tool, "is_read_only", False):
        tool_scope = "read"
    resolved_scope = permission_scope or tool_scope or infer_runtime_permission_scope(name, source_type)
    evidence_types = _normalize_evidence_types(
        getattr(tool, "evidence_types", None),
        tool_name=name,
    )
    evidence_policy = getattr(tool, "evidence_policy", "non_empty")

    return RuntimeToolSpec(
        name=name,
        display_name=getattr(tool, "display_name", None),
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
        is_concurrency_safe=getattr(tool, "is_concurrency_safe", None),
        metadata=dict(getattr(tool, "runtime_metadata", None) or {}),
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
        is_concurrency_safe=getattr(tool, "is_concurrency_safe", None),
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


def _shell_deletion_permission_decision(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    cwd: str,
) -> Any:
    # 与命令黑名单同一套识别：只认两个名字会让沙箱 MCP 的 shell
    # （mcp_sandbox_bash_<hash>）绕过删除保护
    if not is_shell_like_tool_name(tool_name):
        return None

    from agentscope.permission import PermissionBehavior, PermissionDecision

    assessment = assess_shell_deletion(
        str(tool_input.get("command", "")),
        cwd=cwd,
    )
    if assessment.action == "deny":
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message=f"安全策略拦截：{assessment.reason}",
            decision_reason="protected_shell_deletion",
            bypass_immune=True,
        )
    if assessment.action == "ask":
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"需要确认：{assessment.reason}",
            decision_reason="shell_deletion_requires_confirmation",
            bypass_immune=True,
        )
    return None


def resolve_tool_conversation_id(explicit: Any = None) -> str | None:
    """会话级授权使用的会话标识：显式传入优先，其次取当前 Agent 上下文。"""

    value = str(explicit or "").strip()
    if value:
        return value
    try:
        from app.core.context import get_current_agent_context

        context = get_current_agent_context()
    except Exception:
        return None
    return str(getattr(context, "conversation_id", "") or "").strip() or None


def _mark_session_grant_auto_approved(tool_name: str) -> None:
    """判定点记账：这一次调用确实是被会话授权放行的。

    工具结果事件靠这条队列决定「要不要打 auto_approved 标记」。刻意不在结果侧反查
    Redis：反查只能看出「本会话授权过这个工具名」，会把只读快通道自动放行的
    ``ls``、``approval_mode=allow`` 的工具、以及刚被 DENY 的调用一起误标成「自动执行」。
    """

    try:
        from app.core.context import get_current_agent_context

        context = get_current_agent_context()
    except Exception:
        return
    if context is None:
        return
    try:
        context.session_grant_auto_approved.append(str(tool_name))
    except Exception:
        return


def consume_session_grant_auto_approved(tool_name: str) -> bool:
    """结果侧出队：一次授权只对应一次调用（并行批次按工具名一一出队）。"""

    try:
        from app.core.context import get_current_agent_context

        context = get_current_agent_context()
    except Exception:
        return False
    pending = getattr(context, "session_grant_auto_approved", None) if context else None
    if not isinstance(pending, list):
        return False
    try:
        pending.remove(str(tool_name))
    except ValueError:
        return False
    return True


async def _session_grant_decision(
    *,
    tool_name: str,
    user_id: Any,
    conversation_id: Any,
) -> Any:
    """「本会话总是允许」把 ASK 降级为 ALLOW。

    只在工具自身 ``check_permissions`` 的 ASK 出口调用：禁用工具、命令黑名单、
    ``dangerous`` 作用域与删除类 ``bypass_immune`` 决策都在此之前返回，本函数
    因此无法覆盖任何 DENY。
    """

    resolved_conversation = resolve_tool_conversation_id(conversation_id)
    if not resolved_conversation:
        return None

    from agentscope.permission import PermissionBehavior, PermissionDecision

    from app.services.ai.runtime.agentscope.session_permission_grants import (
        session_permission_grants,
    )

    if not await session_permission_grants.is_granted(
        user_id,
        resolved_conversation,
        tool_name,
    ):
        return None
    _mark_session_grant_auto_approved(tool_name)
    return PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message=f"Tool '{tool_name}' is allowed for this session by user grant.",
        decision_reason=f"session_grant:{tool_name}",
    )


async def _session_grant_override(
    native_decision: Any,
    *,
    context: Any,
    tool_name: str,
    user_id: Any,
    conversation_id: Any,
) -> Any:
    """原生工具的决策是否可以被会话授权降级为 ALLOW。

    原生工具在「非只读、非危险」的普通调用上返回的是 ``PASSTHROUGH``（例如 ``Bash``
    执行 ``mkdir``、``Write`` 写工作区文件），而不是 ``ASK``：引擎 Step 4 只采纳
    ``ALLOW``/``DENY``，两种非终态都会继续落到 Step 6 的兜底 ``ASK``。所以这里必须
    同时覆盖 ``ASK`` 与 ``PASSTHROUGH``，否则授权在主路径（Bash）上完全不生效。

    两类例外不放行：
    - ``bypass_immune`` 的 safety ASK（如 ``rm -rf /``）：AgentScope 语义里连 allow
      规则都覆盖不了它；
    - ``DONT_ASK`` 模式（无人值守的定时任务/后台运行）：它把普通 ASK 与 PASSTHROUGH
      兜底成 DENY，降级成 ALLOW 等于放宽无人值守场景的安全边界。
    """

    try:
        from agentscope.permission import PermissionBehavior, PermissionMode
    except Exception:
        return None

    if getattr(native_decision, "behavior", None) not in (
        PermissionBehavior.ASK,
        PermissionBehavior.PASSTHROUGH,
    ):
        return None
    if getattr(native_decision, "bypass_immune", False):
        return None
    if getattr(context, "mode", None) == PermissionMode.DONT_ASK:
        return None
    return await _session_grant_decision(
        tool_name=tool_name,
        user_id=user_id,
        conversation_id=conversation_id,
    )


def supports_session_grant(tool: Any) -> bool:
    """该工具是否适合提供「本会话总是允许」选项（只读与危险工具不提供）。"""

    scope = getattr(tool, "permission_scope", None)
    if scope is None:
        spec = getattr(tool, "spec", None)
        scope = getattr(spec, "permission_scope", None)
    return str(scope or "ask") not in {"read", "dangerous"}


async def enforce_tool_forbidden(
    tool_name: str,
    explicit_user_id: int | str | None = None,
    explicit_agent_id: str | None = None,
) -> Any:
    """DENY when the user has the tool in ``forbidden_tools`` (alias-aware).

    Shared by tool ``check_permissions`` and ``ToolPermissionMiddleware``.

    作用域：有效禁用 = 全局禁用 ∪ **当前智能体**的智能体级禁用。智能体取自显式参数，
    否则取会话上下文（``AgentContext.agent_id``）——同一个用户在不同智能体里的边界不同，
    这层上下文必须由调用链带进来，不能在这里猜。
    """
    from app.core.context import get_current_agent_context
    agent_ctx = get_current_agent_context()
    user_id = explicit_user_id or (agent_ctx.user_id if agent_ctx else None)
    agent_id = explicit_agent_id or (
        getattr(agent_ctx, "agent_id", None) if agent_ctx else None
    )

    if explicit_user_id is None and agent_ctx and agent_ctx.is_admin:
        return None

    if not user_id:
        # 「查不到身份」此前是静默放行：与查询失败的 fail-closed 行为相反，又没有任何痕迹，
        # 线上无法区分「没禁」和「没身份所以没查」。至少留下可告警的日志。
        logger.warning(
            "[forbidden] No user identity when enforcing tool blacklist for tool=%s; allowing",
            tool_name,
        )

    if user_id:
        try:
            from app.core.orm import AsyncSessionLocal
            from app.services.permission_service import PermissionService
            from app.services.ai.tools.model_tool_name import to_runtime_tool_names
            from app.services.ai.tools.registry import AGENTSCOPE_BUILTIN_TOOL_ALIASES
            from agentscope.permission import PermissionBehavior, PermissionDecision

            async with AsyncSessionLocal() as session:
                perm_service = PermissionService(session)
                perms = await perm_service.get_user_permissions(int(user_id), agent_id)
                if "admin" in perms.roles:
                    return None

                forbidden = set(perms.permissions.forbidden_tools or [])
                if forbidden:
                    extended_forbidden = set()
                    for f in forbidden:
                        # 平台名与模型调用名（MCP 运行时名）两侧都要能命中：
                        # 目录里存 `jira_search`，而运行时工具名是 `mcp_jira_search_<hash>`。
                        extended_forbidden.update(to_runtime_tool_names(f))
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


# Backward-compatible alias for existing call sites / tests.
_enforce_tool_forbidden = enforce_tool_forbidden


async def _enforce_command_blacklist(tool_name: str, tool_input: dict[str, Any], explicit_user_id: int | str | None = None) -> Any:
    """命令禁用判定（工具链入口）。

    工具名判定走 ``is_shell_like_tool_name`` 而不是硬编码的 ``{exec_command, bash}``：
    沙箱容器里的 shell 是 MCP 工具 ``mcp_sandbox_bash_<hash>``（模型侧名字由
    ``build_model_tool_name`` 生成），只认两个名字会让那条路径完全不校验。

    查询与匹配委托 ``runtime.command_policy``，与代码执行服务、沙箱终端接口同源。
    """
    from app.services.ai.runtime.command_policy import (
        CommandPolicyUnavailable,
        command_forbidden_message,
        find_forbidden_command_rule,
        is_shell_like_tool_name,
    )

    if not is_shell_like_tool_name(tool_name):
        return None

    from app.core.context import get_current_agent_context
    agent_ctx = get_current_agent_context()
    user_id = explicit_user_id or (agent_ctx.user_id if agent_ctx else None)

    if explicit_user_id is None and agent_ctx and agent_ctx.is_admin:
        return None

    if not user_id:
        # 身份缺失是静默放行，必须留痕，否则无法区分「没禁」与「没查」
        logger.warning(
            "[forbidden] No user identity when enforcing command blacklist for tool=%s; allowing",
            tool_name,
        )
        return None

    from agentscope.permission import PermissionBehavior, PermissionDecision

    try:
        rule = await find_forbidden_command_rule(user_id, [tool_input.get("command", "")])
    except CommandPolicyUnavailable:
        # 与工具/命令侧既有约定一致：策略查不到就拒绝这一次调用
        return _permission_policy_unavailable_decision()
    except Exception:
        logger.exception("Failed to enforce forbidden commands for user %s", user_id)
        return _permission_policy_unavailable_decision()

    if not rule:
        return None
    return PermissionDecision(
        behavior=PermissionBehavior.DENY,
        message=command_forbidden_message(rule),
        decision_reason="hit_user_command_blacklist",
        bypass_immune=True,
    )


# 公开名：deny_override 需要在引擎只读快速通道之上再拦一次命令（见 agent_runtime）。
enforce_command_forbidden = _enforce_command_blacklist


def _permission_policy_unavailable_decision() -> Any:
    from agentscope.permission import PermissionBehavior, PermissionDecision

    return PermissionDecision(
        behavior=PermissionBehavior.DENY,
        message="安全策略暂时无法验证，工具调用已拒绝，请稍后重试。",
        decision_reason="user_permission_policy_unavailable",
        bypass_immune=True,
    )


# 匹配实现已收敛到 runtime.command_policy；这里 re-export 以保持既有导入路径可用
# （tests/ai/runtime/test_agentscope_tooling.py 直接导入 _matches_forbidden_command）。
_shell_command_tokens = shell_command_tokens
_matches_forbidden_command = matches_forbidden_command


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
