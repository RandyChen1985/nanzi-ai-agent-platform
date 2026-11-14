"""会话 resource_scope.mcp_tools 与智能体版本 tools 的并集注入。"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from app.schemas.agent import SessionMcpToolConfig


def _tool_config_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return str(getattr(item, "name", "") or "").strip()


def _session_mcp_mounts(resource_scope: Optional[dict[str, Any]]) -> dict[str, str]:
    mounts: dict[str, str] = {}
    for item in (resource_scope or {}).get("mcp_tools") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            mounts.setdefault(name, str(item.get("id") or "").strip())
    return mounts


def extract_session_mcp_tool_names(resource_scope: Optional[dict[str, Any]]) -> List[str]:
    """从会话资源范围提取 MCP 工具名（保序去重）。"""
    return list(_session_mcp_mounts(resource_scope))


def merge_tools_with_session_mcp(
    configured_tools: Sequence[Any] | None,
    resource_scope: Optional[dict[str, Any]],
) -> List[Any]:
    """
    版本 tools ∪ 会话 mcp_tools（按工具名去重）。

    版本显式配置保留原形态。会话挂载的工具携带保存时的 ID，兼容只有名称的历史快照。
    """
    merged: List[Any] = list(configured_tools or [])
    seen = {_tool_config_name(item) for item in merged if _tool_config_name(item)}
    for name, tool_id in _session_mcp_mounts(resource_scope).items():
        if name in seen:
            continue
        merged.append(SessionMcpToolConfig(name=name, session_mcp_tool_id=tool_id) if tool_id else name)
        seen.add(name)
    return merged


def apply_session_mcp_tools_to_agent_config(
    agent_config: Any,
    resource_scope: Optional[dict[str, Any]],
) -> List[str]:
    """
    就地写回 agent_config.tools，返回本次新追加的 MCP 工具名。
    """
    before = {
        _tool_config_name(item)
        for item in (getattr(agent_config, "tools", None) or [])
        if _tool_config_name(item)
    }
    merged = merge_tools_with_session_mcp(
        getattr(agent_config, "tools", None),
        resource_scope,
    )
    agent_config.tools = merged
    after_added = [
        name
        for name in extract_session_mcp_tool_names(resource_scope)
        if name not in before
    ]
    return after_added
