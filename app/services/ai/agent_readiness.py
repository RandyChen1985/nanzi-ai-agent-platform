"""Readiness rules shared by publishing and sub-agent delegation."""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.services.ai.agent_types import AgentType, LOCKED_CAPABILITY_BY_TYPE


@dataclass(frozen=True)
class AgentReadiness:
    ready: bool
    missing: tuple[str, ...]


DATA_QUERY_TOOLS = frozenset({"get_dataset_schema", "execute_sql_query"})
KNOWLEDGE_BASE_TOOL = "search_knowledge_base"


def _enabled_tool_names(tools: Iterable[Any] | None) -> set[str]:
    names: set[str] = set()
    for entry in tools or []:
        if isinstance(entry, Mapping):
            if entry.get("enabled", True) is False:
                continue
            name = str(entry.get("name") or "").strip()
        else:
            name = str(entry or "").strip()
        if name:
            names.add(name)
    return names


def evaluate_agent_readiness(
    *,
    agent_type: AgentType | str,
    capabilities: Iterable[str] | None,
    engine_config: Mapping[str, Any] | None,
    tools: Iterable[Any] | None,
    has_published_version: bool,
) -> AgentReadiness:
    normalized_type = AgentType(agent_type)
    capability_set = {str(value).strip() for value in capabilities or []}
    config = engine_config or {}
    dataset_ids = [value for value in config.get("dataset_ids", []) or [] if value]
    tool_names = _enabled_tool_names(tools)
    missing: list[str] = []

    if not has_published_version:
        missing.append("published_version")

    locked_capability = LOCKED_CAPABILITY_BY_TYPE[normalized_type]
    if locked_capability not in capability_set:
        missing.append("primary_capability")

    if normalized_type is AgentType.CHATBI:
        if not (tool_names & DATA_QUERY_TOOLS):
            missing.append("data_query_tool")
    elif normalized_type is AgentType.KNOWLEDGE_BASE:
        if not dataset_ids:
            missing.append("knowledge_base_binding")
        if KNOWLEDGE_BASE_TOOL not in tool_names:
            missing.append("knowledge_base_tool")

    return AgentReadiness(ready=not missing, missing=tuple(missing))
