from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from typing import Any

from app.services.ai.tools.registry import AGENTSCOPE_BUILTIN_TOOL_ALIASES

logger = logging.getLogger(__name__)

WORKSPACE_BUILTIN_TOOL_NAMES = frozenset(
    {"Bash", "Read", "Write", "Edit", "Glob", "Grep"}
)
WORKSPACE_REPLACED_PLATFORM_TOOL_NAMES = frozenset(
    {
        *WORKSPACE_BUILTIN_TOOL_NAMES,
        *AGENTSCOPE_BUILTIN_TOOL_ALIASES.keys(),
        "list_available_skills",
        "read_skill_instruction",
    }
)

_workspace_cache: dict[str, Any] = {}


def _clean_key_part(value: str | None, fallback_prefix: str) -> str:
    raw = value or f"{fallback_prefix}_{uuid.uuid4().hex[:12]}"
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return cleaned or f"{fallback_prefix}_{uuid.uuid4().hex[:12]}"


def build_workspace_key(trace_id: str | None, conversation_id: str | None = None) -> str:
    trace_part = _clean_key_part(trace_id, "trace")
    if not conversation_id:
        return trace_part
    return f"{trace_part}__{_clean_key_part(conversation_id, 'conversation')}"


def default_workspace_root() -> str:
    for candidate in ("/app/data/agent_workspaces", "data/agent_workspaces"):
        if candidate == "/app/data/agent_workspaces" and not os.path.exists("/app/data"):
            continue
        return os.path.abspath(candidate)
    return os.path.abspath("data/agent_workspaces")


def discover_platform_skill_paths() -> list[str]:
    """Collect platform skill directories that contain SKILL.md."""
    try:
        from app.core.config import settings

        skills_root = getattr(settings, "SKILLS_DIR", None)
    except Exception:
        return []
    if not skills_root or not os.path.isdir(skills_root):
        return []

    paths: list[str] = []
    for entry in sorted(os.listdir(skills_root)):
        skill_dir = os.path.join(skills_root, entry)
        if os.path.isdir(skill_dir) and os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
            paths.append(os.path.abspath(skill_dir))
    return paths


async def resolve_workspace_root() -> str:
    try:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get("agentscope_workspace_root")
        if raw:
            return os.path.abspath(str(raw))
    except Exception as exc:
        logger.warning("[workspace] Failed to load agentscope_workspace_root: %s", exc)
    root = default_workspace_root()
    os.makedirs(root, exist_ok=True)
    return root


def resolve_session_workdir(
    *,
    root: str,
    user_id: str | int | None,
    conversation_id: str,
) -> str:
    uid = _clean_key_part(str(user_id) if user_id is not None else None, "anonymous")
    cid = _clean_key_part(conversation_id, "conversation")
    return os.path.join(os.path.abspath(root), uid, cid)


async def get_local_workspace(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
) -> Any | None:
    """Return an initialized LocalWorkspace for the conversation."""
    if not conversation_id:
        return None

    root = await resolve_workspace_root()
    workdir = resolve_session_workdir(
        root=root,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    cached = _workspace_cache.get(workdir)
    if cached is not None:
        return cached

    try:
        from agentscope.workspace import LocalWorkspace
    except Exception as exc:
        logger.warning("[workspace] LocalWorkspace unavailable: %s", exc)
        return None

    workspace = LocalWorkspace(
        workdir=workdir,
        skill_paths=discover_platform_skill_paths(),
    )
    try:
        await workspace.initialize()
    except Exception as exc:
        logger.warning("[workspace] Failed to initialize LocalWorkspace workdir=%s: %s", workdir, exc)
        return None

    _workspace_cache[workdir] = workspace
    return workspace


async def get_local_workspace_offloader(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
) -> Any | None:
    """Backward-compatible alias for get_local_workspace."""
    return await get_local_workspace(
        user_id=user_id,
        conversation_id=conversation_id,
    )


async def delete_workspace_for_session(
    user_id: str | int | None,
    conversation_id: str | None,
) -> None:
    if not conversation_id:
        return
    root = await resolve_workspace_root()
    workdir = resolve_session_workdir(
        root=root,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    _workspace_cache.pop(workdir, None)
    if not os.path.isdir(workdir):
        return
    try:
        shutil.rmtree(workdir)
    except Exception as exc:
        logger.warning("[workspace] Failed to delete workdir=%s: %s", workdir, exc)


def clear_workspace_cache() -> None:
    _workspace_cache.clear()


def is_workspace_managed_tool_spec(spec: Any) -> bool:
    """Tools replaced by LocalWorkspace builtins or AgentScope skill viewer."""
    name = getattr(spec, "name", "")
    if name in WORKSPACE_REPLACED_PLATFORM_TOOL_NAMES:
        return True
    native_tool = getattr(spec, "native_tool", None)
    native_name = getattr(native_tool, "name", None) if native_tool is not None else None
    return native_name in WORKSPACE_BUILTIN_TOOL_NAMES


async def build_workspace_toolkit(
    workspace: Any,
    tool_specs: list[Any],
    *,
    approval_mode: str | None = None,
):
    """显式合并 LocalWorkspace 内置文件工具与平台工具（Runner 默认不再调用）。

    AgentScope LocalWorkspace 会通过 list_tools() 返回 Bash/Read/Write/Edit/Glob/Grep。
    平台 Runner 现已改为只挂载 agent 配置工具；如需 workspace 内置工具，请在 agent
    后端配置对应别名（如 grep、read_file、exec_command）。
    """
    from app.services.ai.runtime.agentscope.tools import (
        _load_agentscope_toolkit,
        runtime_tool_from_native,
        runtime_tool_from_spec,
    )

    toolkit_cls = _load_agentscope_toolkit()
    workspace_tools = await workspace.list_tools()
    workspace_names = {getattr(tool, "name", "") for tool in workspace_tools}
    if workspace_names != set(WORKSPACE_BUILTIN_TOOL_NAMES):
        logger.warning(
            "[workspace] Unexpected workspace tools: %s",
            sorted(workspace_names),
        )

    runtime_workspace_tools = [
        runtime_tool_from_native(tool, approval_mode=approval_mode)
        for tool in workspace_tools
    ]
    platform_tools = [
        runtime_tool_from_spec(
            spec,
            approval_mode=approval_mode,
        )
        for spec in tool_specs
        if not is_workspace_managed_tool_spec(spec)
    ]
    skills = await workspace.list_skills()
    mcps = await workspace.list_mcps()
    return toolkit_cls(
        tools=[*runtime_workspace_tools, *platform_tools],
        skills_or_loaders=skills,
        mcps=mcps,
    )
