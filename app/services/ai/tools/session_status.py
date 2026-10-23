"""Read-only runtime facts for the currently executing AI session."""

from __future__ import annotations

import inspect
import json
import os
import platform
import sys
from typing import Any

from app.core.context import get_current_agent_context, get_execution_option
from app.core.redis import get_redis
from app.services.ai.context_usage import estimate_context_usage, estimate_text_tokens
from app.services.ai.memory_service import memory_service
from app.services.ai.runtime.agentscope.middleware import STATS_KEY_SUFFIX
from app.services.ai.tools.tool_compat import tool


_SAFE_MODEL_FIELDS = (
    "configured_model",
    "effective_model_id",
    "source",
    "phase",
    "is_fallback",
    "resolution_status",
    "provider",
    "thinking_enable",
    "thinking_capable",
    "reasoning_effort",
    "thinking_only",
    "allow_disable_thinking",
    "supported_reasoning_efforts",
)


async def resolve_workspace_root() -> str:
    """Load the configured workspace root without creating an import cycle."""
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_root as resolver

    return await resolver(ensure_exists=False)


def resolve_workspace_user_key(*, user_id: Any, user_name: str | None = None) -> str:
    from app.services.ai.runtime.agentscope.workspace import resolve_workspace_user_key as resolver

    return resolver(user_id=user_id, user_name=user_name)


def resolve_session_workdir(
    *,
    root: str,
    user_id: Any,
    user_name: str | None,
    conversation_id: str,
) -> str:
    from app.services.ai.runtime.agentscope.workspace import resolve_session_workdir as resolver

    return resolver(
        root=root,
        user_id=user_id,
        user_name=user_name,
        conversation_id=conversation_id,
    )


def resolve_user_docs_dir(*, root: str, user_id: Any, user_name: str | None) -> str:
    from app.services.ai.runtime.agentscope.workspace import resolve_user_docs_dir as resolver

    return resolver(root=root, user_id=user_id, user_name=user_name)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _directory_state(
    path: str | None,
    *,
    scope: str | None = None,
) -> dict[str, Any] | None:
    if not path:
        return None

    normalized = os.path.abspath(path)
    exists = os.path.isdir(normalized)
    state: dict[str, Any] = {
        "path": normalized,
        "exists": exists,
        "writable": bool(exists and os.access(normalized, os.W_OK)),
    }
    if scope:
        state["scope"] = scope
    return state


def _user_summary(context: Any) -> dict[str, Any] | None:
    if context is None:
        return None

    dimensions = dict(getattr(context, "user_dimensions", {}) or {})
    return {
        "id": getattr(context, "user_id", None),
        "user_name": dimensions.get("user_name"),
        "real_name": dimensions.get("real_name"),
        "role": dimensions.get("role"),
        "dept_code": dimensions.get("dept_code"),
        "org_path": dimensions.get("org_path"),
        "is_admin": bool(getattr(context, "is_admin", False)),
    }


def _client_summary() -> dict[str, Any]:
    injected = get_execution_option("injected_context", {}) or {}
    if not isinstance(injected, dict):
        injected = {}
    device_type = injected.get("device_type")
    display_hint = injected.get("display_hint")
    return {
        "device_type": device_type,
        "display_hint": display_hint,
        "source": "client_reported" if device_type or display_hint else "unavailable",
    }


def _runtime_env_summary() -> dict[str, Any]:
    """只读的运行环境事实（后端进程所在的机器 / 容器环境）。

    全部字段来自 Python 运行时（``sys``/``platform``）与进程级缓存的环境探测，
    不启动任何子进程、不做耗时探测，也不公开用户身份等敏感信息。任何字段探测
    失败都返回 ``None`` 并交由调用方计入 ``limitations``，不抛错。
    """
    env_kind: str | None = None
    try:
        # 惰性导入规避 import 环；get_env() 进程级只探一次并缓存。
        from app.utils.env import get_env

        env_kind = get_env()
    except Exception:
        env_kind = None

    vi = sys.version_info
    return {
        "env_kind": env_kind,
        "python": {
            "version": platform.python_version(),
            "major": vi.major,
            "minor": vi.minor,
            "micro": vi.micro,
            "implementation": platform.python_implementation(),
            "interpreter": sys.executable,
        },
        "platform": {
            "os": sys.platform,
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
    }


def _model_summary(context: Any) -> dict[str, Any]:
    info = dict(getattr(context, "runtime_model_info", {}) or {}) if context else {}
    result = {field: info.get(field) for field in _SAFE_MODEL_FIELDS}
    result["context_window_tokens"] = info.get("context_size")
    result["max_output_tokens"] = info.get("max_output_tokens")
    return {
        "configured_model": result.pop("configured_model"),
        "effective_model_id": result.pop("effective_model_id"),
        "source": result.pop("source"),
        "phase": result.pop("phase"),
        "is_fallback": result.pop("is_fallback"),
        "resolution_status": result.pop("resolution_status"),
        "provider": result.pop("provider"),
        "thinking_enable": result.pop("thinking_enable"),
        "thinking_capable": result.pop("thinking_capable"),
        "reasoning_effort": result.pop("reasoning_effort"),
        "thinking_only": result.pop("thinking_only"),
        "allow_disable_thinking": result.pop("allow_disable_thinking"),
        "supported_reasoning_efforts": result.pop("supported_reasoning_efforts"),
        "context_window_tokens": result.pop("context_window_tokens"),
        "max_output_tokens": result.pop("max_output_tokens"),
    }


def _session_summary(context: Any) -> dict[str, Any]:
    if context is None:
        return {
            "conversation_id": None,
            "trace_id": None,
            "agent_id": None,
            "agent_name": None,
            "agent_type": None,
            "delegation_depth": None,
            "status": "unavailable",
            "phase": None,
        }

    trace_buffer = list(getattr(context, "trace_buffer", []) or [])
    last_step = trace_buffer[-1] if trace_buffer else None
    last_event_type = getattr(last_step, "event_type", None) if last_step else None
    phase = str(last_event_type or "tool_call")
    if phase not in {"model_call", "tool_call", "agent_execution", "synthesis"}:
        phase = "tool_call"

    return {
        "conversation_id": getattr(context, "conversation_id", None),
        "trace_id": getattr(context, "trace_id", None),
        "agent_id": getattr(context, "agent_id", None),
        "agent_name": getattr(context, "agent_name", None),
        "agent_type": getattr(context, "agent_type", None),
        "delegation_depth": getattr(context, "delegation_depth", 0),
        "status": "running",
        "phase": phase,
    }


async def _last_model_call_stats(context: Any) -> dict[str, Any]:
    result = {
        "last_input_tokens": None,
        "last_output_tokens": None,
        "last_cache_input_tokens": None,
        "last_measured_at": None,
    }
    if context is None or not getattr(context, "conversation_id", None):
        return result

    from app.services.ai.conversation_identity import MissingUserIdentityError, require_user_id

    try:
        uid = require_user_id(getattr(context, "user_id", None))
    except MissingUserIdentityError:
        return result
    key = f"{memory_service.KEY_PREFIX}:{uid}:{context.conversation_id}:{STATS_KEY_SUFFIX}"
    try:
        redis = await _maybe_await(get_redis())
        if not redis:
            return result
        rows = await _maybe_await(redis.lrange(key, -1, -1))
        if not rows:
            return result
        raw = rows[-1]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        record = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(record, dict):
            return result
        result.update(
            {
                "last_input_tokens": record.get("input_tokens"),
                "last_output_tokens": record.get("output_tokens"),
                "last_cache_input_tokens": record.get("cache_input_tokens"),
                "last_measured_at": record.get("timestamp"),
            }
        )
    except Exception:
        return result
    return result


async def _context_usage_estimate(context: Any) -> dict[str, Any]:
    """复用共享服务估算当前会话上下文，保持 session_status 原有空历史语义。"""
    if context is None:
        return await estimate_context_usage(user_id=None, conversation_id=None)
    from app.services.ai.conversation_identity import MissingUserIdentityError, require_user_id

    try:
        user_id = require_user_id(getattr(context, "user_id", None))
    except MissingUserIdentityError:
        return {
            "history_messages": 0,
            "estimated_tokens": 0,
            "token_budget": None,
            "status": "identity_unavailable",
        }
    return await estimate_context_usage(
        user_id=user_id,
        conversation_id=getattr(context, "conversation_id", None),
        runtime_model_info=getattr(context, "runtime_model_info", {}) or {},
    )


async def _workspace_summary(context: Any) -> tuple[dict[str, Any], list[str]]:
    empty = {
        "user_root": None,
        "session_workdir": None,
        "docs_dir": None,
        "uploads_dir": None,
        "sandbox_dir": None,
        "sandbox_policy": None,
    }
    limitations: list[str] = []
    if context is None or getattr(context, "user_id", None) is None:
        limitations.append("当前没有可用的认证用户，无法解析用户工作区路径")
        return empty, limitations

    try:
        from app.services.config_service import (
            ConfigService,
            resolve_effective_sandbox_policy,
        )

        sandbox_policy = resolve_effective_sandbox_policy(
            await ConfigService.get("sandbox_policy", "local"),
        )
        root = await _maybe_await(resolve_workspace_root())
        user_id = getattr(context, "user_id", None)
        dimensions = dict(getattr(context, "user_dimensions", {}) or {})
        user_name = dimensions.get("user_name")
        user_key = resolve_workspace_user_key(user_id=user_id, user_name=user_name)
        user_root = os.path.join(os.path.abspath(str(root)), user_key)
        conversation_id = getattr(context, "conversation_id", None)

        session_path = None
        if conversation_id:
            session_path = resolve_session_workdir(
                root=str(root),
                user_id=user_id,
                user_name=user_name,
                conversation_id=conversation_id,
            )
        else:
            limitations.append("当前会话没有 conversation_id，无法解析会话工作目录")

        docs_path = resolve_user_docs_dir(
            root=str(root),
            user_id=user_id,
            user_name=user_name,
        )
        return {
            "user_root": _directory_state(user_root),
            "session_workdir": _directory_state(session_path),
            "docs_dir": _directory_state(docs_path, scope="cross_session"),
            "uploads_dir": _directory_state(os.path.join(user_root, "uploads")),
            "sandbox_dir": _directory_state(os.path.join(user_root, "sandbox")),
            "sandbox_policy": sandbox_policy,
        }, limitations
    except Exception:
        limitations.append("工作区路径解析失败，路径信息不可用")
        return empty, limitations


def _attachments_summary(context: Any) -> dict[str, Any]:
    authorized = {
        str(path)
        for path in (getattr(context, "authorized_attachment_paths", []) or [])
        if str(path).strip()
    } if context else set()
    current_turn = {
        str(path)
        for path in (getattr(context, "current_turn_attachment_paths", []) or [])
        if str(path).strip()
    } if context else set()
    filenames = sorted({os.path.basename(os.path.normpath(path)) for path in authorized})
    return {
        "authorized_count": len(authorized),
        "current_turn_count": len(current_turn),
        "filenames": filenames,
    }


def _resource_summary(context: Any) -> dict[str, Any]:
    capabilities = list(getattr(context, "runtime_tool_capabilities", []) or []) if context else []
    mcp_tools = [
        {"name": item.get("name"), "source_type": item.get("source_type"), "status": "mounted"}
        for item in capabilities
        if isinstance(item, dict) and item.get("source_type") == "mcp" and item.get("name")
    ]
    if context is None:
        return {
            "dataset_ids": [],
            "knowledge_dataset_ids": [],
            "metadata_dataset_ids": [],
            "active_skills": [],
            "mcp_tools": [],
        }
    return {
        "dataset_ids": list(getattr(context, "dataset_ids", []) or []),
        "knowledge_dataset_ids": list(getattr(context, "knowledge_dataset_ids", []) or []),
        "metadata_dataset_ids": list(getattr(context, "metadata_dataset_ids", []) or []),
        "active_skills": list(getattr(context, "skills", []) or []),
        "mcp_tools": mcp_tools,
    }


def runtime_tool_capability_from_spec(spec: Any) -> dict[str, Any]:
    """Build a safe capability record from an already resolved runtime spec."""
    from app.services.ai.tool_policy import resolve_tool_metadata

    metadata = resolve_tool_metadata(spec)
    return {
        "name": str(getattr(spec, "name", "") or ""),
        "description": str(getattr(spec, "description", "") or ""),
        "source_type": str(getattr(spec, "source_type", "unknown") or "unknown"),
        "permission_scope": str(getattr(spec, "permission_scope", "unknown") or "unknown"),
        "is_read_only": bool(getattr(spec, "is_read_only", False)),
        "capability": metadata.capability,
        "source": metadata.source,
        "side_effect": metadata.side_effect,
        "confirmation": metadata.confirmation,
        "freshness": metadata.freshness,
        "idempotent": metadata.idempotent,
        "nudge_mode": metadata.nudge_mode,
    }


@tool
async def session_status() -> str:
    """读取当前 AI 会话的只读运行时信息。

    当不确定会话、设备、模型上下文窗口、工作区、文档目录、用户身份、最近
    Token 统计或运行环境（容器 / 宿主机、Python 与系统版本）时调用；这些信息
    不可用时返回 null 或限制说明，不要猜测。不接受参数，不修改任何会话状态。
    context_usage 包含 physical_window、history_budget、completion_reserve_tokens
    和 request_input_budget 等上下文预算字段（JSON keys: "physical_window",
    "history_budget", "completion_reserve_tokens", "request_input_budget"）。
    """
    context = get_current_agent_context()
    workspace, workspace_limitations = await _workspace_summary(context)
    usage = await _last_model_call_stats(context)
    estimate = await _context_usage_estimate(context)
    runtime_env = _runtime_env_summary()

    limitations = [
        "device_type 和 display_hint 来自客户端请求，仅作为客户端上报信息",
        "Token 使用量仅代表最近一次已完成模型调用的统计",
        "estimated_* 为全量历史基于 estimate_text_tokens 的粗略估算",
        "当前上下文 Token 和剩余容量没有可靠实时估算时返回 null",
        "MCP 工具列表仅代表本次运行时实际解析并挂载的工具",
        "runtime_env 描述的是后端进程所在机器/容器，可能与用户客户端环境不同",
        *workspace_limitations,
    ]
    if context is None:
        limitations.insert(0, "当前请求没有可用的 AgentContext，未对环境信息进行猜测")
    if runtime_env["env_kind"] is None:
        limitations.insert(0, "运行环境（容器/宿主机）探测失败，env_kind 不可用")

    payload = {
        "schema_version": 1,
        "scope": "current_session",
        "session": _session_summary(context),
        "client": _client_summary(),
        "model": _model_summary(context),
        "context_usage": {
            **usage,
            **estimate,
        },
        "user": _user_summary(context),
        "workspace": workspace,
        "runtime_env": runtime_env,
        "resources": _resource_summary(context),
        "attachments": _attachments_summary(context),
        "limitations": limitations,
    }
    return json.dumps(payload, ensure_ascii=False)


@tool
async def get_runtime_capabilities() -> str:
    """读取当前运行时实际挂载的系统、配置和 MCP 工具能力。"""
    context = get_current_agent_context()
    capabilities = list(getattr(context, "runtime_tool_capabilities", []) or []) if context else []
    payload = {
        "schema_version": 1,
        "scope": "current_runtime",
        "tools": capabilities,
        "limitations": [
            "这里只报告当前运行时已经解析并挂载的工具，不代表所有已注册工具",
            "工具能力描述不授予权限，实际调用仍受服务端权限、工具门禁、路径和数据授权约束",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
